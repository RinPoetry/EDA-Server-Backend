# app/api/auth.py (修改后)

from flask import Blueprint, request, g, current_app
from werkzeug.security import check_password_hash
from app.services.sqlite_database_service import db_service
from app.services.email_service import email_service
from app.utils.response_utils import success, error, RetCode
from app.utils.decorators import token_required, log_api_call
from app.utils.token_utils import generate_jwt
from datetime import datetime, timezone, timedelta
import json
import uuid  # 【新增】导入uuid模块
# 【新增】导入 HTML 邮件模板
from .admin import HTML_EMAIL_TEMPLATE

auth_bp = Blueprint('auth', __name__)


def _create_full_user_response(user_record):
    """辅助函数，根据数据库记录创建一个完整的前端用户对象。"""
    if not user_record:
        return None
    return {
        "id": str(user_record['id']),
        "email": user_record['email'],
        "name": user_record['name'],
        "role": user_record['role'],
        "server_username": user_record['server_username']
    }


@auth_bp.route('/login', methods=['POST'])
# @log_api_call
def login():
    """
    用户登录接口。
    如果设备指纹是新的，会发送2FA邮件。
    【修改】登录成功后，生成 JTI 存入数据库，并签发包含 JTI 的 Token。
    """
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password') or not data.get('fingerprint'):
        return error(RetCode.MISSING_PARAMS, msg="缺少邮箱、密码或设备指纹。")

    email = data['email']
    password = data['password']
    fingerprint = data['fingerprint']
    ip_address = request.remote_addr

    user = db_service.get_user_by_email(email)
    if not user or not check_password_hash(user['password_hash'], password):
        return error(RetCode.INVALID_CREDENTIALS)

    if db_service.check_fingerprint(user['id'], fingerprint):
        # --- 【核心修改】开始：签发可吊销的Token ---
        jti = uuid.uuid4().hex
        db_service.add_active_token(user['id'], jti, fingerprint, ip_address)
        token = generate_jwt(str(user['id']), user['role'], jti)
        # --- 【核心修改】结束 ---
        return success({
            "token": token,
            "user": _create_full_user_response(user)
        }, msg="登录成功")
    else:
        # 2FA 逻辑保持不变
        interval = timedelta(seconds=current_app.config['MAIL_SEND_INTERVAL_SECONDS'])
        if user['last_email_sent_at'] and datetime.fromisoformat(user['last_email_sent_at']) + interval > datetime.now(
                timezone.utc):
            return error(RetCode.EMAIL_RATE_LIMIT_EXCEEDED)

        payload = {'fingerprint': fingerprint}
        action_token = db_service.create_pending_action(user['id'], '2FA_LOGIN', payload)

        confirm_url = current_app.config['TWO_FACTOR_AUTH_URL_TEMPLATE'].format(
            token=action_token,
            action='login'
        )

        config = current_app.config
        server_name = config['SERVER_NAME']
        sender_name = config['MAIL_SENDER_NAME']
        expire_minutes = config['PENDING_ACTION_TOKEN_EXPIRES_IN'].total_seconds() / 60

        subject = f"[{server_name}] 安全提醒：新设备登录验证"
        body_content = f"""
            <h2>安全验证请求</h2>
            <p>您好 {user['name']},</p>
            <p>我们检测到一次从新设备或浏览器登录您账户的尝试。为了保护您的账户安全，需要您进行验证。</p>

            <h3>登录详情</h3>
            <ul>
                <li><strong>操作类型:</strong> 新设备登录</li>
                <li><strong>操作时间:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</li>
                <li><strong>IP 地址:</strong> {ip_address}</li>
            </ul>

            <p>如果您确认这是您本人的操作，请点击下方的按钮以授权此设备：</p>
            <a href="{confirm_url}" class="button">授权新设备登录</a>
            <p style="font-size: 12px; color: #888;">如果按钮无法点击，请复制并打开以下链接：<br>{confirm_url}</p>

            <p>如果您没有进行此操作，这可能是一次未经授权的访问尝试。请立即登录面板修改您的密码，并检查账户活动。</p>
            <p><strong>此验证链接将在 {expire_minutes:.0f} 分钟后失效。</strong></p>
        """

        html_body = HTML_EMAIL_TEMPLATE.format(server_name=server_name, sender_name=sender_name,
                                               body_content=body_content)
        email_sent, msg = email_service.send_email(to=user['email'], subject=subject, body=html_body, is_html=True)

        if not email_sent:
            return error(RetCode.INTERNAL_ERROR, msg=f"发送验证邮件失败: {msg}")

        db_service.update_last_email_time(user['id'])
        return error(RetCode.NEW_DEVICE_NEEDS_VERIFICATION, msg="检测到新设备，验证邮件已发送至您的邮箱，请查收。")


@auth_bp.route('/confirm_2fa', methods=['POST'])
# @log_api_call
def confirm_2fa():
    """
    2FA确认接口，处理来自邮件链接的请求。
    【修改】授权成功后，生成 JTI 存入数据库，并签发包含 JTI 的 Token。
    """
    data = request.get_json()
    token = data.get('token')
    if not token:
        return error(RetCode.MISSING_PARAMS, msg="缺少操作令牌。")

    action = db_service.get_and_consume_pending_action(token)
    if not action or action['action_type'] != '2FA_LOGIN':
        return error(RetCode.ACTION_TOKEN_INVALID_OR_EXPIRED)

    user = db_service.get_user_by_id(action['user_id'])
    if not user:
        return error(RetCode.USER_NOT_FOUND)

    payload = json.loads(action['payload'])
    fingerprint = payload.get('fingerprint')
    ip_address = request.remote_addr  # 获取IP地址

    db_service.add_fingerprint(user['id'], fingerprint, "新授权设备")

    # --- 【核心修改】开始：签发可吊销的Token ---
    jti = uuid.uuid4().hex
    db_service.add_active_token(user['id'], jti, fingerprint, ip_address)
    jwt_token = generate_jwt(str(user['id']), user['role'], jti)
    # --- 【核心修改】结束 ---

    return success({
        "token": jwt_token,
        "user": _create_full_user_response(user)
    }, msg="设备授权成功，已为您自动登录。")


@auth_bp.route('/status', methods=['GET'])
@token_required
# @log_api_call
def status():
    """ 检查当前Token的登录状态 """
    return success({
        "status": "logged_in",
        "user": _create_full_user_response(g.current_user)
    }, msg="用户已登录")


@auth_bp.route('/logout', methods=['POST'])
@token_required
@log_api_call
def logout():
    """
    用户登出接口。
    【修改】实现后端Token吊销。
    """
    try:
        jti = g.token_payload.get('jti')
        if jti:
            db_service.revoke_token(jti)
        return success(msg="登出成功。")
    except Exception as e:
        current_app.logger.error(f"Error during logout for user {g.current_user['id']}: {e}")
        return error(RetCode.INTERNAL_ERROR, msg="登出时发生服务器内部错误。")


@auth_bp.route('/logout_all', methods=['POST'])
@token_required
@log_api_call
def logout_all_devices():
    """
    登出所有设备。
    【修改】除了删除指纹，还吊销该用户的所有Token。
    """
    user_id = g.current_user['id']
    try:
        # 删除所有指纹，强制所有设备重新2FA
        db_service.delete_all_fingerprints_for_user(user_id)
        # 【新增】吊销该用户的所有Token
        db_service.revoke_all_tokens_for_user(user_id)
        return success(msg="已成功请求登出所有设备。下次登录需要邮件验证。")
    except Exception as e:
        current_app.logger.error(f"Error logging out all devices for user {user_id}: {e}")
        return error(RetCode.INTERNAL_ERROR, msg="登出所有设备时发生服务器内部错误。")
