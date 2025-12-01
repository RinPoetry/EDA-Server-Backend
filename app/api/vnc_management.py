# app/api/vnc_management.py

from flask import Blueprint, request, g, current_app
from app.services.tiger_vnc_service import tiger_vnc_service
from app.services.password_policy_service import password_policy_service
from app.services.sqlite_database_service import db_service
from app.services.confirmation_service import confirmation_service
from app.utils.decorators import token_required, log_api_call
from app.utils.response_utils import success, error, RetCode
import json

vnc_management_bp = Blueprint('vnc_management', __name__)


@vnc_management_bp.route('/status', methods=['GET'])
@token_required
def get_vnc_status():
    ok, res = tiger_vnc_service.get_status(g.current_user['server_username'])
    return success(res) if ok else error(RetCode.COMMAND_EXECUTION_FAILED, msg=res)


@vnc_management_bp.route('/initialize', methods=['POST'])
@token_required
@log_api_call
def initialize_vnc():
    """初始化/重置 VNC 环境 (需确认)"""
    data = request.get_json()
    password = data.get('password')
    if not password: return error(RetCode.MISSING_PARAMS)

    is_valid, msg = password_policy_service.validate(password, g.current_user['server_username'])
    if not is_valid: return error(RetCode.PASSWORD_POLICY_VIOLATION, msg=msg)

    return confirmation_service.send_confirmation_email(
        user=g.current_user,
        action_type='INITIALIZE_VNC',
        payload={'new_password': password},
        subject="安全操作确认：初始化/重置VNC环境",
        action_name="初始化/重置VNC环境"
    )


def _control_vnc(action_func):
    ok, res = action_func(g.current_user['server_username'])
    return success(msg=f"操作成功: {res}") if ok else error(RetCode.COMMAND_EXECUTION_FAILED, msg=res)


@vnc_management_bp.route('/start', methods=['POST'])
@token_required
@log_api_call
def start_vnc():
    return _control_vnc(tiger_vnc_service.start)


@vnc_management_bp.route('/stop', methods=['POST'])
@token_required
@log_api_call
def stop_vnc():
    return _control_vnc(tiger_vnc_service.stop)


@vnc_management_bp.route('/restart', methods=['POST'])
@token_required
@log_api_call
def restart_vnc():
    return _control_vnc(tiger_vnc_service.restart)


@vnc_management_bp.route('/reset_password', methods=['POST'])
@token_required
@log_api_call
def reset_vnc_password():
    """重置 VNC 密码 (需确认)"""
    data = request.get_json()
    new_password = data.get('new_password')
    if not new_password:
        return error(RetCode.MISSING_PARAMS)

    is_valid, msg = password_policy_service.validate(new_password, g.current_user['server_username'])
    if not is_valid:
        return error(RetCode.PASSWORD_POLICY_VIOLATION, msg=msg)

    return confirmation_service.send_confirmation_email(
        user=g.current_user,
        action_type='RESET_VNC_PASSWORD',
        payload={'new_password': new_password},
        subject="安全操作确认：重设VNC密码",
        action_name="重设VNC密码"
    )


@vnc_management_bp.route('/confirm_vnc_action', methods=['POST'])
def confirm_vnc_action():
    """VNC 动作确认接口"""
    data = request.get_json()
    token = data.get('token')
    if not token:
        return error(RetCode.MISSING_PARAMS)

    action = db_service.get_and_consume_pending_action(token)
    if not action or action['action_type'] not in ['RESET_VNC_PASSWORD', 'INITIALIZE_VNC']:
        return error(RetCode.ACTION_TOKEN_INVALID_OR_EXPIRED)

    user = db_service.get_user_by_id(action['user_id'])
    payload = json.loads(action['payload'])
    username = user['server_username']
    pwd = payload['new_password']

    if action['action_type'] == 'RESET_VNC_PASSWORD':
        ok, msg = tiger_vnc_service.reset_password(username, pwd)
        return success(msg="VNC密码已重置，请重启服务") if ok else error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)

    elif action['action_type'] == 'INITIALIZE_VNC':
        ok, msg = tiger_vnc_service.initialize(username, pwd)
        return success(msg="VNC环境已初始化") if ok else error(RetCode.COMMAND_EXECUTION_FAILED, msg=msg)

    return error(RetCode.INVALID_PARAMS)
