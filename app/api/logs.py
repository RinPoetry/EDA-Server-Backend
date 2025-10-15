# app/api/logs.py

from flask import Blueprint, request, g
from app.utils.decorators import token_required, admin_required, log_api_call
from app.utils.response_utils import success
from app.services.sqlite_database_service import db_service

logs_bp = Blueprint('logs', __name__)


@logs_bp.route('/my_logs', methods=['GET'])
@token_required
# @log_api_call
def get_my_logs():
    """ 获取当前用户的操作日志 (带分页和总数) """
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)  # 保持与前端默认值一致

    # 修改服务层方法以同时返回日志和总数
    logs, total = db_service.get_user_logs_paginated(g.current_user['id'], page, per_page)
    logs_data = [dict(log) for log in logs]

    # 返回一个包含数据列表和总条数的对象
    return success({"data": logs_data, "total": total})


@logs_bp.route('/all_logs', methods=['GET'])
@admin_required
# @log_api_call
def get_all_logs():
    """ (管理员) 获取所有用户的操作日志 (带分页和总数) """
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)

    # 修改服务层方法以同时返回日志和总数
    logs, total = db_service.get_all_logs_paginated(page, per_page)
    logs_data = [dict(log) for log in logs]

    # 返回一个包含数据列表和总条数的对象
    return success({"data": logs_data, "total": total})
