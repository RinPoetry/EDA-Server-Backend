-- app/services/schema.sql

-- 删除已存在的表，确保一个干净的初始化环境
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS user_fingerprints;
DROP TABLE IF EXISTS user_active_tokens; -- 【新增】删除 user_active_tokens 表
DROP TABLE IF EXISTS pending_actions;
DROP TABLE IF EXISTS operation_logs;
DROP TABLE IF EXISTS async_tasks;
DROP TABLE IF EXISTS bookings;


-- 用户表
CREATE TABLE users
(
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    email              TEXT UNIQUE NOT NULL,
    password_hash      TEXT        NOT NULL,
    name               TEXT        NOT NULL,                -- 新增：真实姓名，必填
    server_username    TEXT UNIQUE NOT NULL,
    role               TEXT        NOT NULL DEFAULT 'user', -- 'user' or 'admin'
    is_active          INTEGER     NOT NULL DEFAULT 1,      -- 1 for active, 0 for inactive
    last_email_sent_at DATETIME,                            -- 用于邮件发送频率控制
    created_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 用户设备指纹表
CREATE TABLE user_fingerprints
(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER  NOT NULL,
    fingerprint  TEXT     NOT NULL,
    description  TEXT, -- e.g., "Chrome on Windows 10"
    last_used_at DATETIME,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (user_id, fingerprint)
);

-- 【新增】用户有效Token表，用于实现Token吊销
CREATE TABLE user_active_tokens
(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER  NOT NULL,
    jti         TEXT UNIQUE NOT NULL, -- JWT ID，Token的唯一标识符
    fingerprint TEXT,                 -- 关联的设备指纹
    ip_address  TEXT,                 -- 登录时的IP地址
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- 待处理操作表 (用于2FA、密码重置等)
CREATE TABLE pending_actions
(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER     NOT NULL,
    token       TEXT UNIQUE NOT NULL,
    action_type TEXT        NOT NULL, -- e.g., '2FA_LOGIN', 'CHANGE_PASSWORD', 'INVITE_USER', 'RESET_VNC_PASSWORD', 'GENERATE_SSH_KEY', 'CHANGE_LINUX_PASSWORD' (rename/merge from existing)
    payload     TEXT,                 -- 存储操作所需数据的JSON字符串 (如新密码哈希, 新设备指纹, SSH公钥)
    expires_at  DATETIME    NOT NULL,
    created_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- 用户操作日志表
CREATE TABLE operation_logs
(
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER  NOT NULL,
    username       TEXT     NOT NULL, -- 冗余存储，方便查询
    endpoint       TEXT     NOT NULL,
    method         TEXT     NOT NULL,
    params         TEXT,              -- 存储 JSON 格式的请求参数
    result_code    INTEGER  NOT NULL,
    result_message TEXT,
    ip_address     TEXT,
    timestamp      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- 异步任务表
CREATE TABLE async_tasks
(
    id            TEXT PRIMARY KEY,                    -- UUID
    user_id       INTEGER  NOT NULL,                   -- 发起任务的用户
    task_type     TEXT     NOT NULL,                   -- e.g., 'CREATE_USER', 'RESET_VNC_PASSWORD'
    status        TEXT     NOT NULL DEFAULT 'pending', -- 'pending', 'running', 'completed', 'failed'
    payload       TEXT,                                -- 任务输入参数 (JSON)
    result        TEXT,                                -- 任务成功结果 (JSON)
    error_message TEXT,                                -- 任务失败信息
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- 新增：资源预约表
CREATE TABLE bookings
(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER  NOT NULL,
    start_time  DATETIME NOT NULL,
    end_time    DATETIME NOT NULL,
    cpu_cores   INTEGER  NOT NULL, -- CPU核心数
    ram_gb      INTEGER  NOT NULL, -- 内存大小 (GB)
    gpu_ram_gb  INTEGER  NOT NULL, -- 显存大小 (GB)
    description TEXT,              -- 预约描述
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);