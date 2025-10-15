# app/utils/response_utils.py

from flask import jsonify


class RetCode:
    """ API 响应状态码 """
    # 通用成功
    SUCCESS = 0

    # 认证/授权错误 (-1 to -99)
    AUTH_REQUIRED = -1
    AUTH_INVALID_TOKEN = -2
    AUTH_PERMISSION_DENIED = -3

    # 请求错误 (1 to 99)
    BAD_REQUEST = 1
    MISSING_PARAMS = 2
    INVALID_PARAMS = 3

    # 业务逻辑错误 (100+)
    USER_NOT_FOUND = 100
    USER_ALREADY_EXISTS = 101
    INVALID_CREDENTIALS = 102
    NEW_DEVICE_NEEDS_VERIFICATION = 103
    ACTION_TOKEN_INVALID_OR_EXPIRED = 104
    PASSWORD_POLICY_VIOLATION = 105
    EMAIL_RATE_LIMIT_EXCEEDED = 106

    # 系统/服务错误 (500+)
    INTERNAL_ERROR = 500
    SERVICE_UNAVAILABLE = 501
    COMMAND_EXECUTION_FAILED = 502


# 错误码对应的中文消息
MSG_MAP = {
    RetCode.SUCCESS: "成功",
    RetCode.AUTH_REQUIRED: "需要认证",
    RetCode.AUTH_INVALID_TOKEN: "无效的认证令牌",
    RetCode.AUTH_PERMISSION_DENIED: "权限不足",
    RetCode.BAD_REQUEST: "无效的请求",
    RetCode.MISSING_PARAMS: "缺少必要的参数",
    RetCode.INVALID_PARAMS: "参数格式或内容无效",
    RetCode.USER_NOT_FOUND: "用户不存在",
    RetCode.USER_ALREADY_EXISTS: "用户已存在",
    RetCode.INVALID_CREDENTIALS: "邮箱或密码错误",
    RetCode.NEW_DEVICE_NEEDS_VERIFICATION: "新设备需要邮件验证",
    RetCode.ACTION_TOKEN_INVALID_OR_EXPIRED: "操作令牌无效或已过期",
    RetCode.PASSWORD_POLICY_VIOLATION: "密码不符合安全策略",
    RetCode.EMAIL_RATE_LIMIT_EXCEEDED: "邮件发送过于频繁，请稍后再试",
    RetCode.INTERNAL_ERROR: "服务器内部错误",
    RetCode.SERVICE_UNAVAILABLE: "服务暂不可用",
    RetCode.COMMAND_EXECUTION_FAILED: "后台命令执行失败",
}


def success(data=None, msg=None):
    """
    构造成功的API响应。
    :param data: 业务数据
    :param msg: 自定义成功消息
    :return: Flask Response
    """
    return jsonify({
        "code": RetCode.SUCCESS,
        "msg": msg or MSG_MAP[RetCode.SUCCESS],
        "data": data or {}
    })


def error(code, msg=None, data=None):
    """
    构造失败的API响应。
    :param code: 错误码 (来自 RetCode)
    :param msg: 自定义错误消息，会覆盖默认消息
    :param data: 额外的错误数据
    :return: Flask Response
    """
    http_status = 400
    if code < 0:
        http_status = 401
    elif code >= 500:
        http_status = 500

    response_data = {
        "code": code,
        "msg": msg or MSG_MAP.get(code, "未知错误"),
        "data": data or {}
    }
    return jsonify(response_data), http_status
