# app/api/user_management.py

from flask import Blueprint, request, g, current_app, render_template
from app.services.linux_user_service import linux_user_service
from app.services.sqlite_database_service import db_service
from app.services.email_service import email_service
from app.services.password_policy_service import password_policy_service
from app.services.confirmation_service import confirmation_service
from app.utils.decorators import token_required, log_api_call
from app.utils.response_utils import success, error, RetCode
import json

user_management_bp = Blueprint('user_management', __name__)


@user_management_bp.route('/check_availability', methods=['GET'])
def check_user_availability():
    username = request.args.get('username')
    if not username: return error(RetCode.MISSING_PARAMS)
    ok, res = linux_user_service.check_user_exists(username)
    return success(res) if ok else error(RetCode.COMMAND_EXECUTION_FAILED, msg=res)


@user_management_bp.route('/change_password', methods=['POST'])
@token_required
@log_api_call
def change_password():
    """修改 Linux 密码 (需确认)"""
    data = request.get_json()
    new_password = data.get('new_password')
    if not new_password: return error(RetCode.MISSING_PARAMS)

    is_valid, msg = password_policy_service.validate(new_password, g.current_user['server_username'])
    if not is_valid: return error(RetCode.PASSWORD_POLICY_VIOLATION, msg=msg)

    return confirmation_service.send_confirmation_email(
        user=g.current_user,
        action_type='CHANGE_PASSWORD',
        payload={'new_password': new_password},
        subject="安全操作确认：修改Linux账户密码",
        action_name="修改Linux密码"
    )


@user_management_bp.route('/ssh_keys', methods=['GET'])
@token_required
def get_ssh_keys():
    """
    [新增] 获取当前用户的 SSH 公钥列表。
    """
    username = g.current_user['server_username']
    ok, keys = linux_user_service.list_ssh_keys(username)
    if ok:
        return success(keys)
    else:
        return error(RetCode.INTERNAL_ERROR, msg=keys)


@user_management_bp.route('/update_ssh_keys', methods=['POST'])
@token_required
@log_api_call
def update_ssh_keys():
    """
    [新增] 全量更新 SSH 公钥列表 (需确认)。
    前端提交完整的 key 列表，后端校验后重写文件。
    """
    data = request.get_json()
    keys_data = data.get('keys')

    if not isinstance(keys_data, list):
        return error(RetCode.INVALID_PARAMS, msg="必须提交公钥列表")

    return confirmation_service.send_confirmation_email(
        user=g.current_user,
        action_type='UPDATE_SSH_KEYS',
        payload={'keys_data': keys_data},
        subject="安全操作确认：更新SSH公钥列表",
        action_name="更新SSH公钥"
    )


@user_management_bp.route('/change_ssh_key', methods=['POST'])
@token_required
@log_api_call
def change_ssh_key():
    """
    修改 SSH 公钥 (单条覆盖，旧接口，保留兼容性)
    """
    data = request.get_json()
    public_key = data.get('public_key')
    if not public_key:
        return error(RetCode.MISSING_PARAMS)

    return confirmation_service.send_confirmation_email(
        user=g.current_user,
        action_type='CHANGE_SSH_KEY',
        payload={'public_key': public_key},
        subject="安全操作确认：修改SSH公钥",
        action_name="修改SSH公钥"
    )


@user_management_bp.route('/generate_ssh_key', methods=['POST'])
@token_required
@log_api_call
def generate_ssh_key():
    """生成新 SSH 密钥对 (需确认)"""
    return confirmation_service.send_confirmation_email(
        user=g.current_user,
        action_type='GENERATE_SSH_KEY',
        payload={},
        subject="安全操作确认：生成新的SSH密钥对",
        action_name="生成SSH密钥对"
    )


@user_management_bp.route('/confirm_action', methods=['POST'])
def confirm_user_action():
    """统一确认接口"""
    data = request.get_json()
    token = data.get('token')
    if not token:
        return error(RetCode.MISSING_PARAMS)

    action = db_service.get_and_consume_pending_action(token)
    if not action:
        return error(RetCode.ACTION_TOKEN_INVALID_OR_EXPIRED)

    user = db_service.get_user_by_id(action['user_id'])
    if not user:
        return error(RetCode.USER_NOT_FOUND)

    payload = json.loads(action['payload'])
    username = user['server_username']
    atype = action['action_type']

    if atype == 'CHANGE_PASSWORD':
        ok, msg = linux_user_service.change_password(username, payload['new_password'])
        if not ok:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)
        return success(msg="Linux密码修改成功")

    elif atype == 'CHANGE_SSH_KEY':
        # 旧的单条覆盖逻辑
        ok, msg = linux_user_service.change_ssh_key(username, payload['public_key'])
        if not ok:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)
        return success(msg="SSH公钥更新成功")

    elif atype == 'UPDATE_SSH_KEYS':
        # [新增] 全量更新列表逻辑
        ok, msg = linux_user_service.update_authorized_keys(username, payload['keys_data'])
        if not ok:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)
        return success(msg="SSH公钥列表已成功同步")

    elif atype == 'GENERATE_SSH_KEY':
        # 生成逻辑
        ok, msg, private_key = linux_user_service.generate_ssh_key_pair(username)
        if not ok:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)

        html_body = render_template(
            'emails/ssh_key_notice.html',
            server_name=current_app.config['SERVER_NAME'],
            sender_name=current_app.config['MAIL_SENDER_NAME'],
            name=user['name'],
            private_key=private_key
        )

        ok, email_msg = email_service.send_email(
            to=user['email'],
            subject=f"[{current_app.config['SERVER_NAME']}] 您的新 SSH 私钥",
            body=html_body,
            is_html=True
        )

        if not ok:
            current_app.logger.critical(f"Key generated but email failed for {user['email']}")
            return error(RetCode.INTERNAL_ERROR, msg="密钥生成成功但发送邮件失败")

        return success(msg="新密钥对已生成，私钥已发送至您的邮箱")

    return error(RetCode.INVALID_PARAMS, msg="未知操作")
