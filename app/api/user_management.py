# app/api/user_management.py (修改后)

from flask import Blueprint, request, g, current_app
from app.services.linux_user_service import linux_user_service
from app.services.sqlite_database_service import db_service
from app.services.email_service import email_service
from app.services.password_policy_service import password_policy_service
from app.utils.decorators import token_required, log_api_call
from app.utils.response_utils import success, error, RetCode
from datetime import datetime, timezone, timedelta
import json
# 【新增】导入 HTML 邮件模板
from .admin import HTML_EMAIL_TEMPLATE

user_management_bp = Blueprint('user_management', __name__)

# 【新增】通用操作确认邮件正文模板
ACTION_BODY_TEMPLATE = """
<h2>安全操作确认</h2>
<p>您好 {name},</p>
<p>我们收到一个请求，要求对您的账户执行敏感操作。为保护您的账户安全，需要您进行确认。</p>

<h3>操作详情</h3>
<ul>
    <li><strong>操作类型:</strong> {action_name}</li>
    <li><strong>操作时间:</strong> {timestamp}</li>
    <li><strong>IP 地址:</strong> {ip_address}</li>
</ul>

<p>如果您确认这是您本人的操作，请点击下方的按钮以完成操作：</p>
<a href="{confirm_url}" class="button">确认执行操作</a>
<p style="font-size: 12px; color: #888;">如果按钮无法点击，请复制并打开以下链接：<br>{confirm_url}</p>

<p>如果您没有进行此操作，请立即登录面板修改您的密码，并检查账户活动。</p>
<p><strong>此确认链接将在 {expire_minutes} 分钟后失效。</strong></p>
"""


@user_management_bp.route('/check_availability', methods=['GET'])
def check_user_availability():
    """ 检查Linux用户名是否可用 """
    username = request.args.get('username')
    if not username:
        return error(RetCode.MISSING_PARAMS, msg="缺少 'username' 参数。")

    is_success, result = linux_user_service.check_user_exists(username)
    if not is_success:
        return error(RetCode.COMMAND_EXECUTION_FAILED, msg=result)

    return success(result)


def _initiate_action(user, action_type, payload, subject_template, action_name):
    """
    统一处理需要2FA确认的操作的辅助函数。
    【修改】使用美化后的 HTML 邮件模板。
    """
    interval = timedelta(seconds=current_app.config['MAIL_SEND_INTERVAL_SECONDS'])
    last_sent_at = user.get('last_email_sent_at')
    if last_sent_at and datetime.fromisoformat(last_sent_at) + interval > datetime.now(timezone.utc):
        return error(RetCode.EMAIL_RATE_LIMIT_EXCEEDED)

    action_token = db_service.create_pending_action(user['id'], action_type, payload)

    ip_address = request.remote_addr
    config = current_app.config
    confirm_url = config['TWO_FACTOR_AUTH_URL_TEMPLATE'].format(
        token=action_token,
        action=action_type.lower()
    )

    subject = subject_template.format(server_name=config['SERVER_NAME'])
    body_content = ACTION_BODY_TEMPLATE.format(
        name=user['name'],
        confirm_url=confirm_url,
        action_name=action_name,
        ip_address=ip_address,
        timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        expire_minutes=f"{config['PENDING_ACTION_TOKEN_EXPIRES_IN'].total_seconds() / 60:.0f}"
    )

    html_body = HTML_EMAIL_TEMPLATE.format(
        server_name=config['SERVER_NAME'],
        sender_name=config['MAIL_SENDER_NAME'],
        body_content=body_content
    )

    email_sent, msg = email_service.send_email(to=user['email'], subject=subject, body=html_body, is_html=True)
    if not email_sent:
        return error(RetCode.INTERNAL_ERROR, msg=f"发送确认邮件失败: {msg}")

    db_service.update_last_email_time(user['id'])
    return success(msg="确认邮件已发送，请检查您的邮箱并根据指示操作。")


@user_management_bp.route('/change_password', methods=['POST'])
@token_required
@log_api_call
def change_password():
    """ 发起修改Linux密码的请求 (需要2FA) """
    data = request.get_json()
    new_password = data.get('new_password')
    if not new_password:
        return error(RetCode.MISSING_PARAMS, msg="缺少新密码。")

    is_valid, msg = password_policy_service.validate(new_password, g.current_user['server_username'])
    if not is_valid:
        return error(RetCode.PASSWORD_POLICY_VIOLATION, msg=msg)

    payload = {'new_password': new_password}
    subject_template = "[{server_name}] 安全操作确认：修改Linux账户密码"
    return _initiate_action(g.current_user, 'CHANGE_PASSWORD', payload, subject_template, "修改Linux密码")


@user_management_bp.route('/change_ssh_key', methods=['POST'])
@token_required
@log_api_call
def change_ssh_key():
    """ 发起修改SSH公钥的请求 (需要2FA) """
    data = request.get_json()
    public_key = data.get('public_key')
    if not public_key:
        return error(RetCode.MISSING_PARAMS, msg="缺少SSH公钥。")

    payload = {'public_key': public_key}
    subject_template = "[{server_name}] 安全操作确认：修改SSH公钥"
    return _initiate_action(g.current_user, 'CHANGE_SSH_KEY', payload, subject_template, "修改SSH公钥")


@user_management_bp.route('/generate_ssh_key', methods=['POST'])
@token_required
@log_api_call
def generate_ssh_key():
    """ 发起在服务器上生成新 SSH 密钥对的请求 (需要2FA) """
    payload = {}
    subject_template = "[{server_name}] 安全操作确认：生成新的SSH密钥对"
    return _initiate_action(g.current_user, 'GENERATE_SSH_KEY', payload, subject_template, "生成新的SSH密钥对")


@user_management_bp.route('/confirm_action', methods=['POST'])
def confirm_user_action():
    """ 统一确认用户管理相关的操作(由邮件链接触发，无需token) """
    data = request.get_json()
    token = data.get('token')
    if not token:
        return error(RetCode.MISSING_PARAMS, msg="缺少操作令牌。")

    action = db_service.get_and_consume_pending_action(token)
    if not action:
        return error(RetCode.ACTION_TOKEN_INVALID_OR_EXPIRED)

    user = db_service.get_user_by_id(action['user_id'])
    if not user:
        return error(RetCode.USER_NOT_FOUND)

    payload = json.loads(action['payload'])
    username = user['server_username']
    action_type = action['action_type']

    if action_type == 'CHANGE_PASSWORD':
        is_success, msg = linux_user_service.change_password(username, payload['new_password'])
        if not is_success:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)
        return success(msg="Linux密码修改成功。")

    elif action_type == 'CHANGE_SSH_KEY':
        is_success, msg = linux_user_service.change_ssh_key(username, payload['public_key'])
        if not is_success:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)
        return success(msg="SSH公钥更新成功。")

    elif action_type == 'GENERATE_SSH_KEY':
        is_success, msg, private_key = linux_user_service.generate_ssh_key_pair(username)
        if not is_success:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)

        config = current_app.config
        server_name = config['SERVER_NAME']
        sender_name = config['MAIL_SENDER_NAME']

        email_subject = f"[{server_name}] 您的新 SSH 私钥"
        email_body_content = f"""
            <h2>您的新 SSH 私钥</h2>
            <p>您好 {user['name']},</p>
            <p>您请求的新 SSH 密钥对已成功生成。以下是您的 <strong>私钥</strong>，请立即将其保存到您本地电脑的 <code>~/.ssh/</code> 目录下（例如命名为 <code>id_ed25519_server</code>），并设置文件权限为 600。</p>
            <div class="credentials">
                <pre>{private_key}</pre>
            </div>
            <p style="color: red;"><strong>【安全警告】</strong>: 这是唯一一次显示您的私钥。请妥善保管，切勿泄露给任何人。建议您在保存后立即从邮件中删除此内容。</p>
        """
        html_body = HTML_EMAIL_TEMPLATE.format(server_name=server_name, sender_name=sender_name,
                                               body_content=email_body_content)
        email_sent, email_msg = email_service.send_email(to=user['email'], subject=email_subject, body=html_body,
                                                         is_html=True)

        if not email_sent:
            current_app.logger.critical(
                f"CRITICAL: Failed to email private key to {user['email']}. Key was already changed. Error: {email_msg}")
            return error(RetCode.INTERNAL_ERROR, msg=f"密钥生成成功，但发送私钥邮件失败。请立即联系管理员。")

        return success(msg="新 SSH 密钥对已生成，公钥已配置，私钥已通过邮件发送给您。")

    return error(RetCode.INVALID_PARAMS, msg="未知的操作类型。")
