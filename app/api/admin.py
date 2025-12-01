# app/api/admin.py

from flask import Blueprint, request, g, current_app, render_template
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
    """生成复杂随机密码"""
    alphabet = string.ascii_letters + string.digits
    while True:
        password = ''.join(secrets.choice(alphabet) for i in range(length))
        if (any(c.islower() for c in password) and any(c.isupper() for c in password) and any(
                c.isdigit() for c in password)):
            return password


def _create_and_run_task(task_type, payload, task_function):
    """创建并运行后台任务"""
    task_id = str(uuid.uuid4())
    db_service.create_task(task_id, g.current_user['id'], task_type, payload)

    def task_runner(app, task_id, payload_data):
        # 任务运行在独立线程，必须创建应用上下文才能使用 render_template 和 config
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
    return success({"task_id": task_id}, msg=f"{task_type} 任务已提交"), 202


@admin_bp.route('/invite_new_user', methods=['POST'])
@admin_required
@log_api_call
def invite_new_user():
    """邀请新用户"""
    data = request.get_json()
    email = data.get('email')
    name = data.get('name')
    server_username = data.get('server_username')
    home_volume = data.get('home_volume')

    if not all([email, name, server_username, home_volume]):
        return error(RetCode.MISSING_PARAMS)

    # ... (原有校验逻辑保持不变) ...

    payload = data.copy()
    payload['inviter_name'] = g.current_user['name']

    def task_function(p):
        panel_pwd = _generate_password()
        linux_pwd = _generate_password()
        vnc_pwd = _generate_password()

        # 创建系统用户和初始化VNC
        ok, msg = linux_user_service.add_user(p['server_username'], linux_pwd, p['home_volume'])
        if not ok: raise Exception(msg)

        ok, msg = tiger_vnc_service.initialize(p['server_username'], vnc_pwd)
        if not ok: raise Exception(msg)

        db_service.create_user(p['email'], panel_pwd, p['name'], p['server_username'])

        # 获取 VNC 地址
        vnc_port, _ = tiger_vnc_service._get_vnc_port(p['server_username'])
        vnc_address = f"{current_app.config['SERVER_ADDRESS']}:{5900 + int(vnc_port)}" if vnc_port else "N/A"

        # 【核心修改】使用模板渲染邮件
        html_body = render_template(
            'emails/invite_user.html',
            server_name=current_app.config['SERVER_NAME'],
            sender_name=current_app.config['MAIL_SENDER_NAME'],
            title=f"欢迎加入 {current_app.config['SERVER_NAME']}",
            name=p['name'],
            inviter_name=p['inviter_name'],
            email=p['email'],
            panel_url=current_app.config['FRONTEND_BASE_URL'],
            server_address=current_app.config['SERVER_ADDRESS'],

            is_new_user=True,
            panel_password=panel_pwd,
            linux_username=p['server_username'],
            linux_password=linux_pwd,
            vnc_password=vnc_pwd,
            vnc_address=vnc_address
        )

        ok, msg = email_service.send_email(
            to=p['email'],
            subject=f"欢迎加入 {current_app.config['SERVER_NAME']}",
            body=html_body,
            is_html=True
        )
        if not ok: raise Exception(msg)

        return {'message': '用户创建成功，邮件已发送'}

    return _create_and_run_task('CREATE_NEW_USER', payload, task_function)


@admin_bp.route('/invite_existing_user', methods=['POST'])
@admin_required
@log_api_call
def invite_existing_user():
    """邀请现有 Linux 用户"""
    data = request.get_json()
    # ... (参数获取和校验逻辑保持不变) ...

    payload = data.copy()
    payload['inviter_name'] = g.current_user['name']

    def task_function(p):
        panel_pwd = _generate_password()
        db_service.create_user(p['email'], panel_pwd, p['name'], p['server_username'], role=p.get('role', 'user'))

        # 【核心修改】使用模板渲染邮件
        html_body = render_template(
            'emails/invite_user.html',
            server_name=current_app.config['SERVER_NAME'],
            sender_name=current_app.config['MAIL_SENDER_NAME'],
            title=f"您已被邀请加入 {current_app.config['SERVER_NAME']}",
            name=p['name'],
            inviter_name=p['inviter_name'],
            email=p['email'],
            panel_url=current_app.config['FRONTEND_BASE_URL'],

            is_new_user=False,
            panel_password=panel_pwd,
            linux_username=p['server_username']
        )

        ok, msg = email_service.send_email(
            to=p['email'],
            subject=f"您已被邀请加入 {current_app.config['SERVER_NAME']}",
            body=html_body,
            is_html=True
        )
        if not ok: raise Exception(msg)

        return {'message': '邀请发送成功'}

    return _create_and_run_task('INVITE_EXISTING_USER', payload, task_function)
