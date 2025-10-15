# app/api/bookings.py

from flask import Blueprint, request, g, current_app
from app.utils.decorators import token_required, admin_required, log_api_call
from app.utils.response_utils import success, error, RetCode
from app.services.sqlite_database_service import db_service
from datetime import datetime, timezone

# import json # json 模块在此文件中没有直接使用，可以移除或保留。

bookings_bp = Blueprint('bookings', __name__)


# Helper function to convert DB Row to dict, and handle datetime objects
def _booking_row_to_dict(row):
    """
    辅助函数：将数据库查询结果行转换为字典，并处理 datetime 对象，同时整合用户信息。
    - 将 datetime 对象转换为 ISO 格式字符串。
    - 处理从数据库中读取时，datetime 字段可能以字符串形式存在的情况。
    - 将后端字段名映射为前端期望的驼峰命名。
    """
    if not row:
        return None

    booking = dict(row)

    # 统一处理 datetime 字段的转换
    # 确保无论是 datetime 对象还是 ISO 格式字符串，都能被正确转换为 ISO 格式字符串。
    datetime_fields = ['start_time', 'end_time', 'created_at', 'updated_at']
    for field in datetime_fields:
        dt_value = booking.get(field)
        if dt_value:
            if isinstance(dt_value, datetime):
                # 如果已经是 datetime 对象，直接转换为 ISO 格式字符串
                booking[field] = dt_value.isoformat()
            elif isinstance(dt_value, str):
                try:
                    # 如果是字符串，尝试解析为 datetime 对象，然后转换为 ISO 格式字符串
                    # datetime.fromisoformat() 可以处理 "YYYY-MM-DD HH:MM:SS" 格式
                    dt_obj = datetime.fromisoformat(dt_value)
                    booking[field] = dt_obj.isoformat()
                except ValueError:
                    # 如果解析失败，可能是无效的日期时间字符串。
                    # 记录警告并保留原始字符串，或者根据需求进行更严格的处理（如返回 None 或报错）
                    current_app.logger.warning(
                        f"Could not parse datetime string '{dt_value}' for field '{field}'. Keeping as is."
                    )
            # 如果 dt_value 既不是 datetime 也不是 str (如 None), 则不做处理
        else:
            # 确保 None 值被保留，而不是尝试对其调用方法
            booking[field] = None

    # 将后端字段名映射为前端期望的驼峰命名或修改后的命名
    booking['startTime'] = booking.pop('start_time')
    booking['endTime'] = booking.pop('end_time')
    booking['cpuUsage'] = booking.pop('cpu_cores')
    booking['RAM'] = booking.pop('ram_gb')
    booking['GPU_RAM'] = booking.pop('gpu_ram_gb')  # 保持与前端一致的 'GPU_RAM' 命名
    booking['createdAt'] = booking.pop('created_at')
    booking['updatedAt'] = booking.pop('updated_at')

    # 如果是从 JOIN 查询结果中获取，则包含用户信息
    if 'user_email' in booking:
        booking['user_info'] = {
            'id': booking['user_id'],
            'email': booking.pop('user_email'),
            'name': booking.pop('user_name'),
            'server_username': booking.pop('user_server_username')
        }
    return booking


@bookings_bp.route('/', methods=['POST'])
@token_required
@log_api_call
def create_booking():
    """ 创建新的资源预约。 """
    data = request.get_json()
    user_id = g.current_user['id']

    try:
        # 从前端接收的参数名进行转换
        start_time = datetime.fromisoformat(data.get('startTime'))
        end_time = datetime.fromisoformat(data.get('endTime'))
        cpu_cores = int(data.get('cpuUsage'))
        ram_gb = int(data.get('RAM'))
        gpu_ram_gb = int(data.get('GPU_RAM'))  # 确保这里使用 'GPU_RAM'
        description = data.get('description', '')
    except (TypeError, ValueError):
        return error(RetCode.INVALID_PARAMS, msg="时间戳、核心数、内存或显存格式无效。")

    if not (start_time and end_time and cpu_cores is not None and ram_gb is not None and gpu_ram_gb is not None):
        return error(RetCode.MISSING_PARAMS, msg="缺少必要的预约参数。")

    if start_time >= end_time:
        return error(RetCode.INVALID_PARAMS, msg="开始时间必须早于结束时间。")
    if cpu_cores <= 0 or ram_gb <= 0 or gpu_ram_gb < 0:  # GPU RAM can be 0, but not negative
        return error(RetCode.INVALID_PARAMS, msg="CPU核心数和内存必须大于0，显存不能为负。")

    # TODO: Add logic to check for resource availability conflict (optional for this initial request)

    booking_id = db_service.create_booking(user_id, start_time, end_time, cpu_cores, ram_gb, gpu_ram_gb, description)
    if booking_id:
        return success({"id": booking_id}, msg="预约创建成功。"), 201
    return error(RetCode.INTERNAL_ERROR, msg="预约创建失败。")


@bookings_bp.route('/', methods=['GET'])
@token_required
# @log_api_call
def get_bookings():
    """
    获取所有或当前用户的预约。
    管理员可获取所有，普通用户只能获取自己的。
    """
    if g.current_user['role'] == 'admin':
        bookings_rows = db_service.get_all_bookings()
    else:
        bookings_rows = db_service.get_user_bookings(g.current_user['id'])

    bookings = [_booking_row_to_dict(row) for row in bookings_rows]
    return success(bookings, msg="获取预约列表成功。")


@bookings_bp.route('/<int:booking_id>', methods=['GET'])
@token_required
# @log_api_call
def get_single_booking(booking_id):
    """ 获取单个预约。 """
    booking_row = db_service.get_booking_by_id(booking_id)
    if not booking_row:
        return error(RetCode.BAD_REQUEST, msg="预约不存在。")

    # 权限检查
    if g.current_user['role'] != 'admin' and booking_row['user_id'] != g.current_user['id']:
        return error(RetCode.AUTH_PERMISSION_DENIED, msg="无权查看此预约。")

    return success(_booking_row_to_dict(booking_row), msg="获取预约详情成功。")


@bookings_bp.route('/<int:booking_id>', methods=['PUT'])
@token_required
@log_api_call
def update_booking(booking_id):
    """ 更新预约。 """
    booking_row = db_service.get_booking_by_id(booking_id)
    if not booking_row:
        return error(RetCode.BAD_REQUEST, msg="预约不存在。")

    # 权限检查
    if g.current_user['role'] != 'admin' and booking_row['user_id'] != g.current_user['id']:
        return error(RetCode.AUTH_PERMISSION_DENIED, msg="无权修改此预约。")

    data = request.get_json()
    try:
        start_time = datetime.fromisoformat(data.get('startTime'))
        end_time = datetime.fromisoformat(data.get('endTime'))
        cpu_cores = int(data.get('cpuUsage'))
        ram_gb = int(data.get('RAM'))
        gpu_ram_gb = int(data.get('GPU_RAM'))
        description = data.get('description', '')
    except (TypeError, ValueError):
        return error(RetCode.INVALID_PARAMS, msg="时间戳、核心数、内存或显存格式无效。")

    if not (start_time and end_time and cpu_cores is not None and ram_gb is not None and gpu_ram_gb is not None):
        return error(RetCode.MISSING_PARAMS, msg="缺少必要的预约参数。")

    if start_time >= end_time:
        return error(RetCode.INVALID_PARAMS, msg="开始时间必须早于结束时间。")
    if cpu_cores <= 0 or ram_gb <= 0 or gpu_ram_gb < 0:
        return error(RetCode.INVALID_PARAMS, msg="CPU核心数和内存必须大于0，显存不能为负。")

    # TODO: Add logic to check for resource availability conflict (optional)

    db_service.update_booking(booking_id, start_time, end_time, cpu_cores, ram_gb, gpu_ram_gb, description)
    return success(msg="预约更新成功。")


@bookings_bp.route('/<int:booking_id>', methods=['DELETE'])
@token_required
@log_api_call
def delete_booking(booking_id):
    """ 删除预约。 """
    booking_row = db_service.get_booking_by_id(booking_id)
    if not booking_row:
        return error(RetCode.BAD_REQUEST, msg="预约不存在。")

    # 权限检查
    if g.current_user['role'] != 'admin' and booking_row['user_id'] != g.current_user['id']:
        return error(RetCode.AUTH_PERMISSION_DENIED, msg="无权删除此预约。")

    if db_service.delete_booking(booking_id):
        return success(msg="预约删除成功。")
    return error(RetCode.INTERNAL_ERROR, msg="预约删除失败。")
