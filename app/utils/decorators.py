# app/utils/decorators.py

from functools import wraps
from flask import request, g, current_app
from .response_utils import error, RetCode
from .token_utils import decode_jwt
from app.services.sqlite_database_service import db_service
import json

# 定义敏感字段集合，便于统一管理，防止隐私泄露，后续记得检查字段
SENSITIVE_KEYS = {
    'password', 'new_password', 'old_password',
    'token', 'fingerprint', 'private_key', 'public_key',
    'secret', 'access_token', 'keys'
}


def _filter_sensitive_data(data):
    """
    【新增】递归过滤敏感数据的辅助函数。
    可以处理嵌套的字典或列表，将敏感字段的值替换为 '******'。
    """
    if isinstance(data, dict):
        data_copy = data.copy()  # 创建副本，以免修改原始数据
        for key, value in data_copy.items():
            if key in SENSITIVE_KEYS:
                data_copy[key] = '******'
            else:
                data_copy[key] = _filter_sensitive_data(value)
        return data_copy
    elif isinstance(data, list):
        return [_filter_sensitive_data(item) for item in data]
    else:
        return data


def token_required(f):
    """
    验证JWT Token的装饰器。
    【修改】增加了对 JTI 的数据库校验，以实现Token吊销。
    成功时，将用户信息和Token payload存入 flask.g。
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token or not token.startswith('Bearer '):
            return error(RetCode.AUTH_REQUIRED, msg="请求头中缺少 'Bearer' Token。")

        token = token.split(' ')[1]
        payload = decode_jwt(token)
        if not payload:
            return error(RetCode.AUTH_INVALID_TOKEN, msg="Token 无效或已过期。")

        user = db_service.get_user_by_id(payload['sub'])
        if not user or not user['is_active']:
            return error(RetCode.AUTH_INVALID_TOKEN, msg="用户不存在或已被禁用。")

        # --- 【核心修改】开始：校验 Token 是否已被吊销 ---
        jti = payload.get('jti')
        if not jti:
            return error(RetCode.AUTH_INVALID_TOKEN, msg="Token 格式错误，缺少唯一标识。")

        if not db_service.is_token_active(user['id'], jti):
            return error(RetCode.AUTH_INVALID_TOKEN, msg="您的会话已失效或已在其他地方登出，请重新登录。")
        # --- 【核心修改】结束 ---

        # 将当前用户和 token payload 存储在全局对象 g 中，方便后续 API 使用
        g.current_user = dict(user)
        g.token_payload = payload  # 【新增】存储payload，便于登出时获取jti

        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    """
    检查用户是否为管理员角色的装饰器。
    必须在 @token_required 之后使用。
    """

    @wraps(f)
    @token_required
    def decorated_function(*args, **kwargs):
        if not g.current_user or g.current_user.get('role') != 'admin':
            return error(RetCode.AUTH_PERMISSION_DENIED, msg="此操作需要管理员权限。")
        return f(*args, **kwargs)

    return decorated_function


def log_api_call(f):
    """
    记录API调用的装饰器。
    必须在 @token_required 之后使用，因为它依赖 g.current_user。
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 执行视图函数
        response = f(*args, **kwargs)

        # 确保 g.current_user 存在，如果未登录（如login接口）则无法记录user_id，这里选择跳过
        if not hasattr(g, 'current_user'):
            return response

        try:
            # --- 修改开始 ---
            # 1. 安全地获取响应数据，防止 response.get_json() 返回 None 导致 AttributeError
            status_code = response.status_code
            response_data = response.get_json()

            if response_data and isinstance(response_data, dict):
                result_code = response_data.get('code')
                result_message = response_data.get('msg')
            else:
                # 处理非 JSON 响应或 get_json 失败的情况
                result_code = status_code
                result_message = "Non-JSON response or empty body"
            # --- 修改结束 ---

            # 准备日志参数
            params = {}
            if request.method == 'GET':
                params = request.args.to_dict()
            elif request.is_json:
                # --- 修改开始 ---
                # 2. 使用新的通用过滤函数处理参数，彻底防止隐私泄露
                raw_params = request.get_json()
                params = _filter_sensitive_data(raw_params)
                # --- 修改结束 ---

            # 写入日志
            db_service.log_operation(
                user_id=g.current_user['id'],
                username=g.current_user['email'],
                endpoint=request.path,
                method=request.method,
                params=json.dumps(params, ensure_ascii=False),
                result_code=result_code,
                result_message=result_message,
                ip_address=request.remote_addr
            )
        except Exception as e:
            # --- 修改开始 ---
            # 3. 使用 logger 记录错误而不是 print，防止静默失败难以排查
            # exc_info=True 会打印完整的堆栈信息
            current_app.logger.error(f"API Log Recording Failed: {str(e)}", exc_info=True)
            # --- 修改结束 ---
            # 日志记录失败不应影响主流程，依然返回原 response

        return response

    return decorated_function
