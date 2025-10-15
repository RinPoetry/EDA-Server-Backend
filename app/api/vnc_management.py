# app/api/vnc_management.py (修改后)

from flask import Blueprint, request, g, current_app
from app.services.tiger_vnc_service import tiger_vnc_service
from app.services.password_policy_service import password_policy_service
from app.services.sqlite_database_service import db_service
from app.utils.decorators import token_required, log_api_call
from app.utils.response_utils import success, error, RetCode
# 【修改】导入完整的辅助函数
from app.api.user_management import _initiate_action
import json

vnc_management_bp = Blueprint('vnc_management', __name__)


@vnc_management_bp.route('/status', methods=['GET'])
@token_required
# @log_api_call
def get_vnc_status():
    """ 查询当前用户的VNC状态 """
    username = g.current_user['server_username']
    is_success, result = tiger_vnc_service.get_status(username)
    if not is_success:
        return error(RetCode.COMMAND_EXECUTION_FAILED, msg=result)
    return success(result)


@vnc_management_bp.route('/initialize', methods=['POST'])
@token_required
@log_api_call
def initialize_vnc():
    """
    为当前用户发起初始化或重置VNC环境的请求 (需要2FA)。
    【核心修改】
    此操作现在需要通过邮件进行二次确认，以提高安全性。
    """
    data = request.get_json()
    password = data.get('password')
    if not password:
        return error(RetCode.MISSING_PARAMS, msg="缺少VNC密码。")

    is_valid, msg = password_policy_service.validate(password, g.current_user['server_username'])
    if not is_valid:
        return error(RetCode.PASSWORD_POLICY_VIOLATION, msg=msg)

    # 准备发送到待办事项数据库中的数据
    payload = {'new_password': password}
    # 邮件主题模板
    subject_template = "[{server_name}] 安全操作确认：初始化/重置VNC环境"
    # 调用统一的2FA邮件发送辅助函数
    return _initiate_action(g.current_user, 'INITIALIZE_VNC', payload, subject_template, "初始化/重置VNC环境")


def _control_vnc(action_func):
    """ VNC启停重启的通用逻辑 """
    username = g.current_user['server_username']
    is_success, result = action_func(username)
    if not is_success:
        return error(RetCode.COMMAND_EXECUTION_FAILED, msg=result)
    return success(msg=f"VNC服务操作成功: {result}")


@vnc_management_bp.route('/start', methods=['POST'])
@token_required
@log_api_call
def start_vnc():
    """ 启动VNC服务 """
    return _control_vnc(tiger_vnc_service.start)


@vnc_management_bp.route('/stop', methods=['POST'])
@token_required
@log_api_call
def stop_vnc():
    """ 关闭VNC服务 """
    return _control_vnc(tiger_vnc_service.stop)


@vnc_management_bp.route('/restart', methods=['POST'])
@token_required
@log_api_call
def restart_vnc():
    """ 重启VNC服务 """
    return _control_vnc(tiger_vnc_service.restart)


@vnc_management_bp.route('/reset_password', methods=['POST'])
@token_required
@log_api_call
def reset_vnc_password():
    """ 发起重设VNC密码的请求 (需要2FA) """
    data = request.get_json()
    new_password = data.get('new_password')
    if not new_password:
        return error(RetCode.MISSING_PARAMS, msg="缺少新VNC密码。")

    is_valid, msg = password_policy_service.validate(new_password, g.current_user['server_username'])
    if not is_valid:
        return error(RetCode.PASSWORD_POLICY_VIOLATION, msg=msg)

    payload = {'new_password': new_password}
    subject_template = "[{server_name}] 安全操作确认：重设VNC密码"
    return _initiate_action(g.current_user, 'RESET_VNC_PASSWORD', payload, subject_template, "重设VNC密码")


@vnc_management_bp.route('/confirm_vnc_action', methods=['POST'])
def confirm_vnc_action():
    """
    统一确认VNC相关的操作 (重置密码、初始化/重置环境等)。
    【核心修改】
    此接口合并了原有的 confirm_reset_vnc_password 功能，并增加了处理初始化操作的逻辑。
    """
    data = request.get_json()
    token = data.get('token')
    if not token:
        return error(RetCode.MISSING_PARAMS)

    action = db_service.get_and_consume_pending_action(token)
    if not action or action['action_type'] not in ['RESET_VNC_PASSWORD', 'INITIALIZE_VNC']:
        return error(RetCode.ACTION_TOKEN_INVALID_OR_EXPIRED)

    user = db_service.get_user_by_id(action['user_id'])
    if not user:
        return error(RetCode.USER_NOT_FOUND)

    payload = json.loads(action['payload'])
    new_password = payload['new_password']
    username = user['server_username']

    # 根据操作类型调用不同的服务层函数
    if action['action_type'] == 'RESET_VNC_PASSWORD':
        is_success, msg = tiger_vnc_service.reset_password(username, new_password)
        if not is_success:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)
        return success(msg="VNC密码已成功重置。请重启您的VNC服务以使新密码生效。")

    elif action['action_type'] == 'INITIALIZE_VNC':
        is_success, msg = tiger_vnc_service.initialize(username, new_password)
        if not is_success:
            return error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)
        return success(msg="VNC环境已成功初始化/重置。")

    return error(RetCode.INVALID_PARAMS, msg="未知的VNC操作类型。")
