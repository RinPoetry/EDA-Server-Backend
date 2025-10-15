# app/config.py (修改后)

import os
from dotenv import load_dotenv
from datetime import timedelta

# 在开发环境中，可以从项目根目录的 .env 文件加载环境变量
# 在生产环境中，应直接设置系统环境变量
load_dotenv()


class Config:
    """
    应用配置基类。
    所有配置项都优先从环境变量读取，并提供合理的默认值。
    敏感信息（如 SECRET_KEY, MAIL_PASSWORD）不应硬编码默认值，必须通过环境变量设置。
    """
    # [安全] 必须通过环境变量设置。用于 session、CSRF 保护等。
    SECRET_KEY = os.environ.get('SECRET_KEY', 'a-very-secret-key-for-dev')

    # [数据库] 定义数据库文件所在的目录
    DATABASE_PATH = os.path.join('instance', 'eda_server.db')

    # ==========================================================
    # ===== 服务器面板配置 (Server Panel Config) =====
    # ==========================================================
    # 【修改】服务器名称和地址，均可通过环境变量配置
    SERVER_NAME = os.environ.get('SERVER_NAME', 'EDA组服务器')
    SERVER_ADDRESS = os.environ.get('SERVER_ADDRESS', '10.161.90.114')
    # 信息更新时间间隔 (秒)
    UPDATE_INTERVALS = {
        "realtime_status": int(os.environ.get('UPDATE_INTERVAL_REALTIME', 5)),
        "disk_usage": int(os.environ.get('UPDATE_INTERVAL_DISK', 3600 * 24)),
        "smart_info": int(os.environ.get('UPDATE_INTERVAL_SMART', 3600 * 24)),
    }

    # ==========================================================
    # ===== 认证与安全配置 (Auth & Security Config) =====
    # ==========================================================
    # JWT 配置
    JWT_ALGORITHM = 'HS256'
    JWT_EXPIRATION_DELTA = timedelta(days=int(os.environ.get('JWT_EXPIRATION_DAYS', 7)))

    # 2FA (双因素认证) 待办操作Token的有效时间
    PENDING_ACTION_TOKEN_EXPIRES_IN = timedelta(minutes=int(os.environ.get('PENDING_ACTION_TOKEN_MINUTES', 30)))

    # 前端基础URL，用于构建面向用户的链接（如邮件中的确认链接）
    FRONTEND_BASE_URL = os.environ.get('FRONTEND_BASE_URL', 'http://localhost:5173')

    # 2FA 确认链接模板
    TWO_FACTOR_AUTH_URL_TEMPLATE = f"{FRONTEND_BASE_URL}/confirm-action?token={{token}}&action={{action}}"

    # ==========================================================
    # ===== 邮件服务配置 (Mail Service Config) =====
    # ==========================================================
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    # [安全] 必须通过环境变量设置
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.exmail.qq.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 465))
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'True').lower() in ['true', '1', 't']
    # 发件人显示名称 (邮件中的称呼)
    MAIL_SENDER_NAME = os.environ.get('MAIL_SENDER_NAME', 'EDA服务器管理员')
    MAIL_DEFAULT_SENDER = (MAIL_SENDER_NAME, MAIL_USERNAME)
    # 给同一位用户发送邮件的最小间隔 (秒)
    MAIL_SEND_INTERVAL_SECONDS = int(os.environ.get('MAIL_SEND_INTERVAL_SECONDS', 30))

    # ==========================================================
    # ===== 初始管理员账户 (Initial Admin User) =====
    # ==========================================================
    # 首次运行时，通过 `flask init-admin` 命令创建
    INIT_ADMIN_EMAIL = os.environ.get('INIT_ADMIN_EMAIL')
    INIT_ADMIN_PASSWORD = os.environ.get('INIT_ADMIN_PASSWORD')
    INIT_ADMIN_NAME = os.environ.get('INIT_ADMIN_NAME', '管理员')
    INIT_ADMIN_SERVER_USERNAME = os.environ.get('INIT_ADMIN_SERVER_USERNAME')

    # ==========================================================
    # ===== Linux 用户服务配置 =====
    # ==========================================================
    # 【修改】新用户家目录卷列表，可通过环境变量以逗号分隔的字符串形式提供
    USER_HOME_VOLUMES_STR = os.environ.get('USER_HOME_VOLUMES', '/volumes/home,/mnt/home')
    USER_HOME_VOLUMES = [path.strip() for path in USER_HOME_VOLUMES_STR.split(',')]
    # VNC 用户和端口的配置文件路径
    VNC_USERS_FILE = os.environ.get('VNC_USERS_FILE', "/etc/tigervnc/vncserver.users")

    # ==========================================================
    # ===== 系统信息服务配置 =====
    # ==========================================================
    # 【修改】需要每日统计磁盘空间占用的路径列表，可通过环境变量以逗号分隔的字符串形式提供
    DISK_USAGE_PATHS_STR = os.environ.get('DISK_USAGE_PATHS', '/home,/mnt/home,/volumes/home')
    DISK_USAGE_PATHS = [path.strip() for path in DISK_USAGE_PATHS_STR.split(',')]
    # 实时信息收集的时间间隔（秒）
    REALTIME_COLLECTION_INTERVAL = UPDATE_INTERVALS['realtime_status']
    # 实时信息在内存中保留的时间（分钟）
    REALTIME_RETENTION_MINUTES = int(os.environ.get('REALTIME_RETENTION_MINUTES', 30))
