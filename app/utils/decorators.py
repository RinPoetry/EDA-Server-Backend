# app/utils/decorators.py

from functools import wraps
from flask import request, g
from .response_utils import error, RetCode
from .token_utils import decode_jwt
from app.services.sqlite_database_service import db_service
import json


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

        # 确保 g.current_user 存在
        if not hasattr(g, 'current_user'):
            return response

        try:
            # --- 修改开始 ---
            # 直接从 Response 对象中获取状态码和数据
            # Flask 会确保视图函数的返回值最终被包装成一个 Response 对象
            status_code = response.status_code
            response_data = response.get_json()  # 使用 get_json() 更安全，如果响应不是JSON，会返回None

            # 如果响应不是JSON格式，response.get_json() 会返回 None，需要处理这种情况
            if response_data is None:
                # 记录一个默认值或尝试从原始数据解析
                result_code = None
                result_message = 'Response is not in JSON format'
            else:
                result_code = response_data.get('code')
                result_message = response_data.get('msg')
            # --- 修改结束 ---

            # 准备日志参数
            params = {}
            if request.method == 'GET':
                params = request.args.to_dict()
            elif request.is_json:
                # 过滤掉敏感信息
                params = request.get_json()
                if 'password' in params: params['password'] = '******'
                if 'new_password' in params: params['new_password'] = '******'

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
            # 日志记录失败不应影响主流程
            print(f"Log记录失败: {e}")  # 打印更详细的错误信息
            pass

        return response

    return decorated_function
