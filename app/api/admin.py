# app/api/admin.py (修改后)

from flask import Blueprint, request, g, current_app
from app.utils.decorators import admin_required, log_api_call
from app.utils.response_utils import success, error, RetCode
from app.services.sqlite_database_service import db_service
from app.services.linux_user_service import linux_user_service
from app.services.tiger_vnc_service import tiger_vnc_service
from app.services.email_service import email_service
import uuid
import secrets
import string
from threading import Thread

admin_bp = Blueprint('admin', __name__)


def _generate_password(length=12):
    """生成一个指定长度的随机密码，包含大写字母、小写字母和数字。"""
    alphabet = string.ascii_letters + string.digits
    while True:
        password = ''.join(secrets.choice(alphabet) for i in range(length))
        if (any(c.islower() for c in password)
                and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)):
            return password


def _create_and_run_task(task_type, payload, task_function):
    """
    辅助函数：创建并启动一个通用后台任务。
    """
    task_id = str(uuid.uuid4())
    db_service.create_task(task_id, g.current_user['id'], task_type, payload)

    def task_runner(app, task_id, payload_data):
        with app.app_context():
            db_service.update_task_status(task_id, status='running')
            try:
                result = task_function(payload_data)
                db_service.update_task_status(task_id, status='completed', result=result)
            except Exception as e:
                app.logger.error(f"Task {task_id} failed: {e}", exc_info=True)
                db_service.update_task_status(task_id, status='failed', error_message=str(e))

    app = current_app._get_current_object()
    thread = Thread(target=task_runner, args=(app, task_id, payload))
    thread.daemon = True
    thread.start()

    return success({"task_id": task_id}, msg=f"{task_type} 任务已提交，请稍后查询任务状态。"), 202


# --- HTML 邮件模板 ---
HTML_EMAIL_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: 'Helvetica Neue', Helvetica, Arial, 'Microsoft Yahei', sans-serif; margin: 0; padding: 20px; background-color: #f4f7f6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); overflow: hidden; }}
        .header {{ background-color: #003a8c; color: #ffffff; padding: 20px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .content {{ padding: 30px; line-height: 1.6; }}
        .content h2 {{ color: #003a8c; }}
        .credentials {{ background-color: #f9f9f9; border: 1px solid #e0e0e0; padding: 15px; border-radius: 5px; margin: 20px 0; font-family: 'Courier New', Courier, monospace; }}
        .credentials pre {{ margin: 0; padding: 5px 0; white-space: pre-wrap; word-break: break-all; }}
        .credentials strong {{ color: #d63384; }}
        .button {{ display: inline-block; background-color: #0056b3; color: #ffffff; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-top: 15px; }}
        .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #888; background-color: #f1f1f1; }}
        .footer p {{ margin: 5px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header"><h1>{server_name}</h1></div>
        <div class="content">
            {body_content}
        </div>
        <div class="footer">
            <p><strong>重要提示：</strong>为确保链接有效，请在校园网环境下访问管理面板及相关链接。</p>
            <p>这是一封系统自动发送的邮件，请勿直接回复。</p>
            <p>由 {sender_name} 管理</p>
        </div>
    </div>
</body>
</html>
"""


@admin_bp.route('/invite_new_user', methods=['POST'])
@admin_required
@log_api_call
def invite_new_user():
    """
    (管理员) 邀请新用户并创建全新账户。
    """
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    server_username = data.get('server_username')
    home_volume = data.get('home_volume')

    if not all([email, name, server_username, home_volume]):
        return error(RetCode.MISSING_PARAMS, msg="缺少email, name, server_username或home_volume参数。")

    allowed_volumes = current_app.config['USER_HOME_VOLUMES']
    if home_volume not in allowed_volumes:
        return error(RetCode.INVALID_PARAMS, msg=f"无效的 home_volume 参数。必须是 {', '.join(allowed_volumes)} 之一。")

    if db_service.get_user_by_email(email) or db_service.get_user_by_server_username(server_username):
        return error(RetCode.USER_ALREADY_EXISTS, msg="邮箱或服务器用户名已存在。")

    success_check, check_result = linux_user_service.check_user_exists(server_username)
    if not success_check or not check_result.get('is_available'):
        return error(RetCode.USER_ALREADY_EXISTS, msg=f"Linux 用户 '{server_username}' 已存在或其家目录被占用。")

    # 【修改】获取邀请人姓名，并加入payload
    inviter_name = g.current_user['name']
    payload = data.copy()
    payload['inviter_name'] = inviter_name

    def task_function(p):
        panel_password = _generate_password()
        linux_password = _generate_password()
        vnc_password = _generate_password()

        success_linux, msg_linux = linux_user_service.add_user(
            p['server_username'], linux_password, p['home_volume']
        )
        if not success_linux: raise Exception(f"创建Linux用户失败: {msg_linux}")

        success_vnc, msg_vnc = tiger_vnc_service.initialize(p['server_username'], vnc_password)
        if not success_vnc: raise Exception(f"初始化VNC失败: {msg_vnc}")

        db_service.create_user(p['email'], panel_password, p['name'], p['server_username'])

        config = current_app.config
        server_name = config['SERVER_NAME']
        server_address = config['SERVER_ADDRESS']
        panel_url = config['FRONTEND_BASE_URL']
        sender_name = config['MAIL_SENDER_NAME']

        vnc_display_port, _ = tiger_vnc_service._get_vnc_port(p['server_username'])
        vnc_address = f"{server_address}:{5900 + int(vnc_display_port)}" if vnc_display_port else "N/A"

        # 【修改】邮件正文中加入邀请人信息
        body_content = f"""
            <h2>欢迎加入 {server_name}！</h2>
            <p>您好 {p['name']},</p>
            <p><strong>{p['inviter_name']}</strong> 邀请您加入 {server_name}！您的账户已创建成功。请妥善保管以下账户信息，不同服务的密码是独立的。我们强烈建议您首次登录后立即修改所有初始密码。</p>

            <h3>重要地址</h3>
            <ul>
                <li>服务器地址 (SSH): <strong>{server_address}</strong></li>
                <li>Web 管理面板: <a href="{panel_url}">{panel_url}</a></li>
                <li>VNC 远程桌面: <strong>{vnc_address}</strong></li>
            </ul>

            <h3>您的账户信息</h3>
            <div class="credentials">
                <pre><strong>Web 管理面板</strong>
  - 登录邮箱: {p['email']}
  - 初始密码: {panel_password}</pre>
                <hr>
                <pre><strong>Linux / SSH</strong>
  - 用户名: {p['server_username']}
  - 初始密码: {linux_password}</pre>
                <hr>
                <pre><strong>VNC 远程桌面</strong>
  - 初始密码: {vnc_password}</pre>
            </div>

            <p>如果您在登录或使用过程中遇到任何问题，请随时联系管理员。</p>
            <p>祝您使用愉快！</p>
        """
        html_body = HTML_EMAIL_TEMPLATE.format(server_name=server_name, sender_name=sender_name,
                                               body_content=body_content)
        email_sent, msg_email = email_service.send_email(to=p['email'], subject=f"欢迎加入 {server_name}",
                                                         body=html_body, is_html=True)
        if not email_sent: raise Exception(f"发送邀请邮件失败: {msg_email}")

        return {
            'message': '用户创建成功并已发送邮件。',
            'credentials': {
                'panel_email': p['email'],
                'panel_password': panel_password,
                'linux_username': p['server_username'],
                'linux_password': linux_password,
                'vnc_password': vnc_password,
                'vnc_address': vnc_address
            }
        }

    return _create_and_run_task('CREATE_NEW_USER', payload, task_function)


@admin_bp.route('/invite_existing_user', methods=['POST'])
@admin_required
@log_api_call
def invite_existing_user():
    """
    (管理员) 邀请已存在的 Linux 用户加入面板。
    """
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    server_username = data.get('server_username')
    role = data.get('role', 'user')

    if not all([email, name, server_username]):
        return error(RetCode.MISSING_PARAMS, msg="缺少email, name或server_username参数。")
    if server_username == 'root':
        return error(RetCode.INVALID_PARAMS, msg="出于安全考虑，禁止邀请 root 用户。")
    if role not in ['user', 'admin']:
        return error(RetCode.INVALID_PARAMS, msg="无效的角色，必须是 'user' 或 'admin'。")

    if db_service.get_user_by_email(email) or db_service.get_user_by_server_username(server_username):
        return error(RetCode.USER_ALREADY_EXISTS, msg="该邮箱或服务器用户名已在面板中注册。")

    success_check, check_result = linux_user_service.check_user_exists(server_username)
    if not success_check or not check_result.get('system_user_exists'):
        return error(RetCode.USER_NOT_FOUND, msg=f"Linux 系统中不存在用户 '{server_username}'。")

    # 【修改】获取邀请人姓名，并加入payload
    inviter_name = g.current_user['name']
    payload = data.copy()
    payload['inviter_name'] = inviter_name

    def task_function(p):
        panel_password = _generate_password()

        db_service.create_user(p['email'], panel_password, p['name'], p['server_username'], role=p.get('role', 'user'))

        config = current_app.config
        server_name = config['SERVER_NAME']
        panel_url = config['FRONTEND_BASE_URL']
        sender_name = config['MAIL_SENDER_NAME']

        # 【修改】邮件正文中加入邀请人信息
        body_content = f"""
            <h2>您已被邀请加入 {server_name}</h2>
            <p>您好 {p['name']},</p>
            <p>您已被管理员 <strong>{p['inviter_name']}</strong> 邀请加入 {server_name} 的 Web 管理面板。您的 Linux 系统账户 <strong>({p['server_username']})</strong> 已被关联。</p>
            <p>您可以使用以下凭据登录管理面板，我们强烈建议您首次登录后立即修改初始密码。</p>

            <div class="credentials">
                <pre><strong>Web 管理面板</strong>
  - 登录邮箱: {p['email']}
  - 初始密码: {panel_password}</pre>
  - 面板地址: <a href="{panel_url}">{panel_url}</a>
            </div>

            <a href="{panel_url}" class="button">点击登录面板</a>

            <p>请注意：此邀请仅用于面板登录，您的 Linux 系统密码和 VNC 密码保持不变。</p>
        """
        html_body = HTML_EMAIL_TEMPLATE.format(server_name=server_name, sender_name=sender_name,
                                               body_content=body_content)
        email_sent, msg_email = email_service.send_email(to=p['email'], subject=f"您已被邀请加入 {server_name}",
                                                         body=html_body, is_html=True)
        if not email_sent: raise Exception(f"发送邀请邮件失败: {msg_email}")

        return {'message': '用户邀请成功，邮件已发送。'}

    return _create_and_run_task('INVITE_EXISTING_USER', payload, task_function)
