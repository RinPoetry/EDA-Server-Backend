# app/utils/token_utils.py

import jwt
from datetime import datetime, timezone
from flask import current_app
import uuid  # 【新增】导入uuid模块


def generate_jwt(user_id, user_role, jti):
    """
    为指定用户生成 JWT。
    【修改】新增 jti 参数，并将其加入 payload。
    :param user_id: 用户ID
    :param user_role: 用户角色 ('user' or 'admin')
    :param jti: JWT ID, 用于Token吊销的唯一标识
    :return: JWT 字符串
    """
    secret_key = current_app.config['SECRET_KEY']
    algorithm = current_app.config['JWT_ALGORITHM']
    expiration = datetime.now(timezone.utc) + current_app.config['JWT_EXPIRATION_DELTA']

    payload = {
        'sub': user_id,
        'role': user_role,
        'jti': jti,  # 【新增】将 jti 加入 payload
        'iat': datetime.now(timezone.utc),
        'exp': expiration
    }
    return jwt.encode(payload, secret_key, algorithm=algorithm)


def decode_jwt(token):
    """
    解码并验证 JWT。
    :param token: JWT 字符串
    :return: payload 字典，如果无效则返回 None
    """
    secret_key = current_app.config['SECRET_KEY']
    algorithm = current_app.config['JWT_ALGORITHM']

    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
