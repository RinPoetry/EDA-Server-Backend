# app/api/tasks.py

from flask import Blueprint, g
from app.utils.decorators import token_required, log_api_call
from app.utils.response_utils import success, error, RetCode
from app.services.sqlite_database_service import db_service
import json

tasks_bp = Blueprint('tasks', __name__)


@tasks_bp.route('/<string:task_id>', methods=['GET'])
@token_required
@log_api_call
def get_task_status(task_id):
    """
    查询异步任务的状态。
    """
    task = db_service.get_task(task_id)

    if not task:
        return error(RetCode.BAD_REQUEST, msg="任务不存在。")

    # 安全检查：普通用户只能查询自己的任务
    if g.current_user['role'] != 'admin' and task['user_id'] != g.current_user['id']:
        return error(RetCode.AUTH_PERMISSION_DENIED, msg="无权查询此任务。")

    # 准备返回数据
    task_data = {
        "task_id": task['id'],
        "task_type": task['task_type'],
        "status": task['status'],
        "result": json.loads(task['result']) if task['result'] else None,
        "error_message": task['error_message'],
        "created_at": task['created_at'],
        "updated_at": task['updated_at']
    }

    return success(task_data, msg="获取任务状态成功。")
