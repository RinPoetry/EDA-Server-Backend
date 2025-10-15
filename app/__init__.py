# app/__init__.py (修改后)

import os
import sys

import click
import atexit
from flask import Flask
from .config import Config
from werkzeug.security import generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix


def create_app():
    """
    应用工厂函数 (Application Factory)。
    此函数现在包含了自动化的首次运行初始化逻辑。
    """
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    # 反向代理配置，拿到真实IP
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1
    )

    if getattr(sys, 'frozen', False):
        # 如果 'frozen' 属性为 True，说明程序被 PyInstaller 等工具打包了
        # 我们希望 instance 文件夹和数据库位于可执行文件的旁边
        basedir = os.path.dirname(sys.executable)
        instance_path = os.path.join(basedir, 'instance')
    else:
        # 否则，是正常的开发环境，使用 Flask 默认的 instance_path
        instance_path = app.instance_path

    # 确保这个持久化的 instance 文件夹存在
    try:
        os.makedirs(instance_path)
    except OSError:
        pass
    # 将配置中的数据库路径更新为绝对路径，确保后续操作的准确性
    db_filename = os.path.basename(app.config['DATABASE_PATH'])
    absolute_db_path = os.path.join(instance_path, db_filename)
    app.config['DATABASE_PATH'] = absolute_db_path

    # --- 数据库服务初始化 ---
    from .services.sqlite_database_service import db_service

    # --- 【核心修改】自动化首次运行初始化 ---
    # 通过检查数据库文件是否存在来判断是否为首次运行。
    # 这段逻辑取代了原有的 'init-db' 和 'init-admin' 命令行命令。
    if not os.path.exists(app.config['DATABASE_PATH']):
        print("--> 检测到首次运行：数据库文件不存在。")
        print("--> 正在执行自动化首次设置...")

        # 数据库操作必须在应用上下文中进行
        with app.app_context():
            # 1. 初始化数据库表结构
            # db_service.init_db() 会使用 app.open_resource() 读取 schema.sql,
            # 这能正确处理被 PyInstaller 打包到可执行文件内的 .sql 文件。
            print("    - 步骤 1/2: 正在根据 schema.sql 初始化数据库...")
            db_service.init_db()
            print("      ...数据库初始化成功。")

            # 2. 创建初始管理员账户
            print("    - 步骤 2/2: 正在创建初始管理员账户...")
            email = app.config.get('INIT_ADMIN_EMAIL')
            password = app.config.get('INIT_ADMIN_PASSWORD')
            name = app.config.get('INIT_ADMIN_NAME')
            server_username = app.config.get('INIT_ADMIN_SERVER_USERNAME')

            if not all([email, password, name, server_username]):
                # 如果环境变量不完整，只打印警告，不中断应用启动
                print("      ...警告：未能创建管理员。请在 .env 文件或环境变量中完整设置 "
                      "INIT_ADMIN_EMAIL, INIT_ADMIN_PASSWORD, INIT_ADMIN_NAME, 和 INIT_ADMIN_SERVER_USERNAME。")
            else:
                # 检查用户是否已存在（虽然在首次初始化时不太可能，但这是个好习惯）
                if db_service.get_user_by_email(email):
                    print(f"      ...用户 {email} 已存在，跳过创建。")
                else:
                    db_service.create_user(email, password, name, server_username, role='admin')
                    print(f"      ...管理员用户 '{email}' 创建成功。")

        print("--> 自动化首次设置完成。")

    # --- 数据库连接管理 ---
    # 注册一个函数，在应用上下文销毁时关闭数据库连接
    app.teardown_appcontext(db_service.close_db)

    # 【核心修改】以下两个命令行命令已被上面的自动化逻辑取代，故予以删除。
    # @app.cli.command('init-db') ...
    # @app.cli.command('init-admin') ...

    # --- 启动常驻服务 ---
    from .services.system_info_service import system_info_service
    # system_info_service 启动也需要应用上下文
    with app.app_context():
        system_info_service.start(app)
    # 注册一个函数，在程序退出时停止服务
    atexit.register(system_info_service.stop)

    # --- 注册蓝图 (Blueprints) ---
    api_prefix = '/eda-server-api/v1'

    from .api.auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix=f'{api_prefix}/auth')

    from .api.monitor import monitor_bp
    app.register_blueprint(monitor_bp, url_prefix=f'{api_prefix}/monitor')

    from .api.server_info import server_info_bp
    app.register_blueprint(server_info_bp, url_prefix=f'{api_prefix}/server')

    from .api.tasks import tasks_bp
    app.register_blueprint(tasks_bp, url_prefix=f'{api_prefix}/tasks')

    # 新增的蓝图
    from .api.user_management import user_management_bp
    app.register_blueprint(user_management_bp, url_prefix=f'{api_prefix}/user')

    from .api.vnc_management import vnc_management_bp
    app.register_blueprint(vnc_management_bp, url_prefix=f'{api_prefix}/vnc')

    from .api.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix=f'{api_prefix}/admin')

    from .api.logs import logs_bp
    app.register_blueprint(logs_bp, url_prefix=f'{api_prefix}/logs')

    # 【新增】注册预约蓝图
    from .api.bookings import bookings_bp
    app.register_blueprint(bookings_bp, url_prefix=f'{api_prefix}/bookings')

    # --- 注册全局错误处理器 ---
    from .utils.response_utils import error, RetCode
    @app.errorhandler(404)
    def not_found(e):
        return error(RetCode.BAD_REQUEST, msg="请求的资源不存在。")

    @app.errorhandler(500)
    def internal_server_error(e):
        return error(RetCode.INTERNAL_ERROR, msg="服务器发生内部错误。")

    @app.errorhandler(Exception)
    def handle_exception(e):
        # 对于所有未捕获的异常，返回一个通用的500错误
        # 在生产环境中，这里应该记录详细的异常信息
        app.logger.error(f"Unhandled exception: {e}", exc_info=True)
        return error(RetCode.INTERNAL_ERROR, msg="服务器发生未知错误。")

    return app
