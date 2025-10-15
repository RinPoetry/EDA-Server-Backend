# app/api/server_info.py (修改后)

from flask import Blueprint, current_app
from app.utils.response_utils import success

server_info_bp = Blueprint('server_info', __name__)


@server_info_bp.route('/panel_info', methods=['GET'])
def get_panel_info():
    """
    获取服务器面板的基本公开信息 (无需鉴权)。
    【修改】新增返回 user_home_volumes，供管理员邀请用户时选择。
    """
    config = current_app.config
    info = {
        "server_name": config.get('SERVER_NAME'),
        "server_address": config.get('SERVER_ADDRESS'),
        "update_intervals": config.get('UPDATE_INTERVALS'),
        # 【新增】返回可用的用户主目录卷列表
        "user_home_volumes": config.get('USER_HOME_VOLUMES', [])
    }
    return success(info, msg="获取服务器面板信息成功")
