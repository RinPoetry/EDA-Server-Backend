# app/services/sqlite_database_service.py (修改后)

import sqlite3
from flask import current_app, g
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import json
from datetime import datetime, timezone


class SQLiteDatabaseService:
    """
    封装所有与 SQLite 数据库交互的服务类。
    """

    def get_db(self):
        """ 获取当前请求的数据库连接。 """
        if 'db' not in g:
            g.db = sqlite3.connect(
                current_app.config['DATABASE_PATH'],
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
            )
            g.db.row_factory = sqlite3.Row
        return g.db

    def close_db(self, e=None):
        """ 关闭数据库连接。 """
        db = g.pop('db', None)
        if db is not None:
            db.close()

    def init_db(self):
        """ 初始化数据库，创建所有需要的表。 """
        db = self.get_db()
        schema_path = 'services/schema.sql'
        with current_app.open_resource(schema_path) as f:
            db.executescript(f.read().decode('utf8'))
        print("数据库已初始化。")

    # --- 用户管理 (User Management) ---

    def create_user(self, email, password, name, server_username, role='user', is_active=1):
        """ 创建新用户。新增 name 参数。 """
        db = self.get_db()
        try:
            cursor = db.execute(
                "INSERT INTO users (email, password_hash, name, server_username, role, is_active) VALUES (?, ?, ?, ?, ?, ?)",
                (email, generate_password_hash(password), name, server_username, role, is_active),
            )
            db.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            db.rollback()
            return None

    def get_user_by_email(self, email):
        """ 根据邮箱查询用户。 """
        return self.get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    def get_user_by_id(self, user_id):
        """ 根据用户 ID 查询用户。 """
        return self.get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def get_user_by_server_username(self, server_username):
        """ 根据Linux用户名查询用户。 """
        return self.get_db().execute("SELECT * FROM users WHERE server_username = ?", (server_username,)).fetchone()

    def update_password(self, user_id, new_password):
        """ 更新用户密码。 """
        db = self.get_db()
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user_id))
        db.commit()

    def update_last_email_time(self, user_id):
        """ 更新用户最后一次发送邮件的时间戳。 """
        db = self.get_db()
        db.execute("UPDATE users SET last_email_sent_at = ? WHERE id = ?", (datetime.now(timezone.utc), user_id))
        db.commit()

    # --- 指纹管理 (Fingerprint Management) ---

    def add_fingerprint(self, user_id, fingerprint, description=""):
        """ 为用户添加新设备指纹。 """
        db = self.get_db()
        db.execute(
            "INSERT INTO user_fingerprints (user_id, fingerprint, description, last_used_at) VALUES (?, ?, ?, ?)",
            (user_id, fingerprint, description, datetime.now(timezone.utc))
        )
        db.commit()

    def check_fingerprint(self, user_id, fingerprint):
        """ 检查指纹是否存在并更新使用时间。 """
        db = self.get_db()
        fp = db.execute(
            "SELECT id FROM user_fingerprints WHERE user_id = ? AND fingerprint = ?",
            (user_id, fingerprint)
        ).fetchone()
        if fp:
            db.execute(
                "UPDATE user_fingerprints SET last_used_at = ? WHERE id = ?",
                (datetime.now(timezone.utc), fp['id'])
            )
            db.commit()
        return fp is not None

    def delete_all_fingerprints_for_user(self, user_id):
        """
        删除指定用户的所有设备指纹记录。
        这会强制该用户在所有设备上重新进行2FA验证。
        """
        db = self.get_db()
        db.execute("DELETE FROM user_fingerprints WHERE user_id = ?", (user_id,))
        db.commit()

    # --- 【新增】Token 吊销管理 (Token Revocation Management) ---

    def add_active_token(self, user_id, jti, fingerprint, ip_address, max_sessions=5):
        """
        添加一个新的有效Token记录，并处理会话数量限制。
        """
        db = self.get_db()
        cursor = db.cursor()
        try:
            # 统计当前用户的有效Token数量
            cursor.execute("SELECT COUNT(id) FROM user_active_tokens WHERE user_id = ?", (user_id,))
            count = cursor.fetchone()[0]

            # 如果达到或超过上限，删除最早的一个
            if count >= max_sessions:
                cursor.execute(
                    "DELETE FROM user_active_tokens WHERE id IN (SELECT id FROM user_active_tokens WHERE user_id = ? ORDER BY created_at ASC LIMIT 1)",
                    (user_id,)
                )

            # 插入新的Token记录
            cursor.execute(
                "INSERT INTO user_active_tokens (user_id, jti, fingerprint, ip_address) VALUES (?, ?, ?, ?)",
                (user_id, jti, fingerprint, ip_address)
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    def is_token_active(self, user_id, jti):
        """ 检查Token是否在有效列表中。"""
        db = self.get_db()
        result = db.execute(
            "SELECT id FROM user_active_tokens WHERE user_id = ? AND jti = ?",
            (user_id, jti)
        ).fetchone()
        return result is not None

    def revoke_token(self, jti):
        """ 根据JTI吊销一个Token。"""
        db = self.get_db()
        db.execute("DELETE FROM user_active_tokens WHERE jti = ?", (jti,))
        db.commit()

    def revoke_all_tokens_for_user(self, user_id):
        """ 吊销一个用户的所有Token。"""
        db = self.get_db()
        db.execute("DELETE FROM user_active_tokens WHERE user_id = ?", (user_id,))
        db.commit()

    # --- 待办操作管理 (Pending Action Management) ---
    def create_pending_action(self, user_id, action_type, payload):
        """ 创建一个待处理操作并返回其Token。 """
        db = self.get_db()
        token = secrets.token_urlsafe(128)
        expires_at = datetime.now(timezone.utc) + current_app.config['PENDING_ACTION_TOKEN_EXPIRES_IN']

        db.execute(
            "INSERT INTO pending_actions (user_id, token, action_type, payload, expires_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, token, action_type, json.dumps(payload), expires_at)
        )
        db.commit()
        return token

    def get_and_consume_pending_action(self, token):
        """ 获取并删除一个有效的待处理操作。 """
        db = self.get_db()
        action = db.execute(
            "SELECT * FROM pending_actions WHERE token = ? AND expires_at > ?",
            (token, datetime.now(timezone.utc))
        ).fetchone()

        if action:
            db.execute("DELETE FROM pending_actions WHERE id = ?", (action['id'],))
            db.commit()
            return dict(action)  # 返回字典副本
        return None

    # --- 操作日志 (Operation Log) ---

    def log_operation(self, **kwargs):
        """ 记录一条操作日志。 """
        db = self.get_db()
        db.execute(
            """INSERT INTO operation_logs (user_id, username, endpoint, method, params, result_code, result_message, ip_address)
               VALUES (:user_id, :username, :endpoint, :method, :params, :result_code, :result_message, :ip_address)""",
            kwargs
        )
        db.commit()

    def get_user_logs_paginated(self, user_id, page=1, per_page=10):
        """
        分页获取单个用户的操作日志,并返回日志列表和总记录数。
        :return: (logs, total)
        """
        db = self.get_db()
        offset = (page - 1) * per_page
        total_query = "SELECT COUNT(id) FROM operation_logs WHERE user_id = ?"
        total = db.execute(total_query, (user_id,)).fetchone()[0]
        logs_query = "SELECT * FROM operation_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        logs = db.execute(logs_query, (user_id, per_page, offset)).fetchall()
        return logs, total

    def get_all_logs_paginated(self, page=1, per_page=10):
        """
        (管理员) 分页获取所有操作日志,并返回日志列表和总记录数。
        :return: (logs, total)
        """
        db = self.get_db()
        offset = (page - 1) * per_page
        total_query = "SELECT COUNT(id) FROM operation_logs"
        total = db.execute(total_query).fetchone()[0]
        logs_query = "SELECT * FROM operation_logs ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        logs = db.execute(logs_query, (per_page, offset)).fetchall()
        return logs, total

    # --- 异步任务 (Async Task) ---

    def create_task(self, task_id, user_id, task_type, payload):
        """ 创建一个异步任务。 """
        db = self.get_db()
        db.execute(
            "INSERT INTO async_tasks (id, user_id, task_type, payload) VALUES (?, ?, ?, ?)",
            (task_id, user_id, task_type, json.dumps(payload))
        )
        db.commit()

    def update_task_status(self, task_id, status, result=None, error_message=None):
        """ 更新任务状态。 """
        db = self.get_db()
        now = datetime.now(timezone.utc)
        db.execute(
            """UPDATE async_tasks SET status = ?, result = ?, error_message = ?, updated_at = ?
               WHERE id = ?""",
            (status, json.dumps(result) if result else None, error_message, now, task_id)
        )
        db.commit()

    def get_task(self, task_id):
        """ 获取任务信息。 """
        return self.get_db().execute("SELECT * FROM async_tasks WHERE id = ?", (task_id,)).fetchone()

    # --- 预约功能 (Booking Management) ---
    def create_booking(self, user_id: int, start_time: datetime, end_time: datetime,
                       cpu_cores: int, ram_gb: int, gpu_ram_gb: int, description: str):
        """ 创建一个新的资源预约。 """
        db = self.get_db()
        cursor = db.execute(
            """INSERT INTO bookings (user_id, start_time, end_time, cpu_cores, ram_gb, gpu_ram_gb, description)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, start_time, end_time, cpu_cores, ram_gb, gpu_ram_gb, description)
        )
        db.commit()
        return cursor.lastrowid

    def get_booking_by_id(self, booking_id: int):
        """ 根据ID获取单个预约。 """
        return self.get_db().execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()

    def get_all_bookings(self):
        """ 获取所有预约 (管理员权限)。包括用户信息。 """
        # JOIN users 表以获取用户名
        query = """
            SELECT b.*, u.email as user_email, u.name as user_name, u.server_username as user_server_username
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            ORDER BY b.start_time DESC
        """
        return self.get_db().execute(query).fetchall()

    def get_user_bookings(self, user_id: int):
        """ 获取指定用户的所有预约。包括用户信息。 """
        query = """
            SELECT b.*, u.email as user_email, u.name as user_name, u.server_username as user_server_username
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.user_id = ?
            ORDER BY b.start_time DESC
        """
        return self.get_db().execute(query, (user_id,)).fetchall()

    def update_booking(self, booking_id: int, start_time: datetime, end_time: datetime,
                       cpu_cores: int, ram_gb: int, gpu_ram_gb: int, description: str):
        """ 更新现有预约。 """
        db = self.get_db()
        db.execute(
            """UPDATE bookings SET start_time = ?, end_time = ?, cpu_cores = ?, ram_gb = ?, gpu_ram_gb = ?, description = ?, updated_at = ?
               WHERE id = ?""",
            (start_time, end_time, cpu_cores, ram_gb, gpu_ram_gb, description, datetime.now(timezone.utc), booking_id)
        )
        db.commit()

    def delete_booking(self, booking_id: int):
        """ 删除预约。 """
        db = self.get_db()
        cursor = db.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        db.commit()
        return cursor.rowcount > 0  # 返回是否成功删除


# 创建一个数据库服务的单例
db_service = SQLiteDatabaseService()
