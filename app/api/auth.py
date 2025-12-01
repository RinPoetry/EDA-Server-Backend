# app/api/auth.py

from flask import Blueprint, request, g, current_app
from werkzeug.security import check_password_hash
from app.services.sqlite_database_service import db_service
from app.services.confirmation_service import confirmation_service
from app.services.password_policy_service import password_policy_service
from app.utils.response_utils import success, error, RetCode
from app.utils.decorators import token_required, log_api_call
from app.utils.token_utils import generate_jwt
import json
import uuid

auth_bp = Blueprint('auth', __name__)


# 辅助函数
def _create_full_user_response(user_record):
    if not user_record: return None
    return {
        "id": str(user_record['id']), "email": user_record['email'],
        "name": user_record['name'], "role": user_record['role'],
        "server_username": user_record['server_username']
    }


@auth_bp.route('/login', methods=['POST'])
def login():
    """登录接口"""
    data = request.get_json()
    if not data or not all(k in data for k in ('email', 'password', 'fingerprint')):
        return error(RetCode.MISSING_PARAMS)

    email = data['email']
    password = data['password']
    fingerprint = data['fingerprint']
    ip_address = request.remote_addr

    # 1. 验证用户是否存在及密码是否正确
    user = db_service.get_user_by_email(email)
    if not user or not check_password_hash(user['password_hash'], password):
        return error(RetCode.INVALID_CREDENTIALS)

    # 2. 检查设备指纹是否已受信任
    is_trusted_device = db_service.check_fingerprint(user['id'], fingerprint)

    # 3. 获取全局2FA配置 (默认为False)
    enable_2fa = current_app.config.get('ENABLE_LOGIN_2FA', False)

    # 4. 登录逻辑判断
    # 满足以下任一条件即可直接登录：
    # A. 设备是受信任的 (is_trusted_device 为 True)
    # B. 全局2FA开关关闭 (enable_2fa 为 False)
    if is_trusted_device or not enable_2fa:

        # 如果是因为2FA关闭而进入此分支，且设备尚未受信任，则自动添加指纹
        if not is_trusted_device:
            db_service.add_fingerprint(user['id'], fingerprint, "自动授权设备 (2FA关闭)")

        # --- 执行登录成功逻辑 ---
        jti = uuid.uuid4().hex
        db_service.add_active_token(user['id'], jti, fingerprint, ip_address)
        token = generate_jwt(str(user['id']), user['role'], jti)
        return success({"token": token, "user": _create_full_user_response(user)})

    else:
        # 5. 2FA 流程 (设备不受信任 且 2FA开启)
        # 使用 ConfirmationService 发送 2FA 邮件
        payload = {'fingerprint': fingerprint}
        result = confirmation_service.send_confirmation_email(
            user=user,
            action_type='2FA_LOGIN',
            payload=payload,
            subject="安全提醒：新设备登录验证",
            action_name="新设备登录授权",
            intro_text="我们检测到一次从新设备或浏览器登录您账户的尝试。为了保护您的账户安全，需要您进行验证。",
            button_text="授权新设备登录"
        )

        if isinstance(result, tuple):
            return result

        return error(RetCode.NEW_DEVICE_NEEDS_VERIFICATION, msg="检测到新设备，验证邮件已发送至您的邮箱，请查收。")


@auth_bp.route('/confirm_2fa', methods=['POST'])
def confirm_2fa():
    """确认 2FA"""
    data = request.get_json()
    token = data.get('token')
    if not token: return error(RetCode.MISSING_PARAMS)

    action = db_service.get_and_consume_pending_action(token)
    if not action or action['action_type'] != '2FA_LOGIN':
        return error(RetCode.ACTION_TOKEN_INVALID_OR_EXPIRED)

    user = db_service.get_user_by_id(action['user_id'])
    payload = json.loads(action['payload'])

    # 记录指纹并登录
    db_service.add_fingerprint(user['id'], payload['fingerprint'], "新授权设备")
    jti = uuid.uuid4().hex
    db_service.add_active_token(user['id'], jti, payload['fingerprint'], request.remote_addr)
    jwt_token = generate_jwt(str(user['id']), user['role'], jti)

    return success({
        "token": jwt_token,
        "user": _create_full_user_response(user)
    }, msg="授权成功，已自动登录。")


@auth_bp.route('/status', methods=['GET'])
@token_required
def status():
    return success({"status": "logged_in", "user": _create_full_user_response(g.current_user)})


@auth_bp.route('/logout', methods=['POST'])
@token_required
@log_api_call
def logout():
    db_service.revoke_token(g.token_payload.get('jti'))
    return success(msg="登出成功")


@auth_bp.route('/logout_all', methods=['POST'])
@token_required
@log_api_call
def logout_all_devices():
    db_service.delete_all_fingerprints_for_user(g.current_user['id'])
    db_service.revoke_all_tokens_for_user(g.current_user['id'])
    return success(msg="已登出所有设备")


@auth_bp.route('/change_panel_password', methods=['POST'])
@token_required
@log_api_call
def change_panel_password():
    """
    请求修改面板登录密码。
    需要验证旧密码，并校验新密码复杂度，通过后发送确认邮件。
    """
    data = request.get_json()
    old_password = data.get('old_password')
    new_password = data.get('new_password')

    if not old_password or not new_password:
        return error(RetCode.MISSING_PARAMS)

    user = g.current_user

    # 1. 验证旧密码是否正确
    if not check_password_hash(user['password_hash'], old_password):
        return error(RetCode.INVALID_CREDENTIALS, msg="当前密码错误，请重新输入。")

    # 2. 校验新密码是否符合安全策略
    # 面板账户没有 strict 的 username 包含限制，但为了安全一致性，我们传入 email 前缀作为参考
    username_for_check = user['email'].split('@')[0]
    is_valid, msg = password_policy_service.validate(new_password, username_for_check)
    if not is_valid:
        return error(RetCode.PASSWORD_POLICY_VIOLATION, msg=msg)

    # 3. 发送确认邮件
    # 注意：我们将新密码暂存在 payload 中。confirmation_service 会将其存入 pending_actions 表。
    # 令牌有效期较短，风险可控。
    return confirmation_service.send_confirmation_email(
        user=user,
        action_type='CHANGE_PANEL_PASSWORD',
        payload={'new_password': new_password},
        subject="安全操作确认：修改登录密码",
        action_name="修改Web面板登录密码",
        warning_text="修改密码后，所有已登录的设备（包括当前设备）将被强制登出，您需要使用新密码重新登录。",
        button_text="确认修改密码"
    )


@auth_bp.route('/confirm_change_password', methods=['POST'])
def confirm_change_password():
    """
    确认修改面板登录密码。
    验证 Token，更新数据库密码，并吊销所有现有 Token。
    """
    data = request.get_json()
    token = data.get('token')
    if not token:
        return error(RetCode.MISSING_PARAMS)

    # 1. 验证并获取待处理的操作
    action = db_service.get_and_consume_pending_action(token)
    if not action or action['action_type'] != 'CHANGE_PANEL_PASSWORD':
        return error(RetCode.ACTION_TOKEN_INVALID_OR_EXPIRED)

    user = db_service.get_user_by_id(action['user_id'])
    if not user:
        return error(RetCode.USER_NOT_FOUND)

    payload = json.loads(action['payload'])
    new_password = payload.get('new_password')

    if not new_password:
        return error(RetCode.INVALID_PARAMS, msg="操作数据已损坏，请重新发起请求。")

    # 2. 更新数据库中的密码
    # update_password 方法内部会处理哈希生成
    db_service.update_password(user['id'], new_password)

    # 3. 安全措施：修改密码后，吊销该用户的所有活跃 Token (强制下线)
    db_service.revoke_all_tokens_for_user(user['id'])

    return success(msg="登录密码修改成功！所有设备已登出，请使用新密码重新登录。")
