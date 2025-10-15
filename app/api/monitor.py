# app/api/monitor.py (修改后)

from flask import Blueprint, g
from app.utils.decorators import token_required, log_api_call
from app.utils.response_utils import success
from app.services.system_info_service import system_info_service

monitor_bp = Blueprint('monitor', __name__)


@monitor_bp.route('/hardware_info', methods=['GET'])
@token_required
# @log_api_call
def get_hardware_info():
    """ 获取设备硬件信息 """
    specs = system_info_service.get_hardware_info()
    return success(specs)


@monitor_bp.route('/realtime_status', methods=['GET'])
@token_required
# @log_api_call
def get_realtime_status():
    """ 获取实时状态信息历史记录 """
    history = system_info_service.get_realtime_history()
    return success(history)


@monitor_bp.route('/disk_usage', methods=['GET'])
@token_required
# @log_api_call
def get_disk_usage():
    """
    【修改】获取所有用户磁盘空间信息。
    根据需求，此接口现在对所有已登录用户（包括普通成员）返回完整的磁盘占用数据。
    """
    # 【核心修改】移除了原有的 g.current_user['role'] != 'admin' 的判断和过滤逻辑。
    # 现在无论用户角色如何，都直接返回由 system_info_service 收集到的完整数据。
    usage_data = system_info_service.get_disk_usage()
    return success(usage_data)


@monitor_bp.route('/smart_info', methods=['GET'])
@token_required
# @log_api_call
def get_smart_info():
    """ 获取S.M.A.R.T信息 """
    # 此接口通常需要较高权限，可在装饰器中加入角色检查
    smart_data = system_info_service.get_smart_info()
    return success(smart_data)
