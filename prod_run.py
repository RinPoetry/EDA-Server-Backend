#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
生产环境启动脚本
- 使用 Waitress WSGI 服务器
- 启动前进行环境自检，确保所有依赖的系统命令都存在
"""
import os
import sys
import shutil  # 导入 shutil 模块
from app import create_app
from waitress import serve


def check_system_dependencies():
    """
    检查所有后端服务依赖的外部系统命令是否存在。
    """
    print("🔎 正在进行环境自检，检查系统命令依赖...")

    required_commands = [
        # Linux User Management
        'id', 'useradd', 'chpasswd', 'userdel', 'ssh-keygen',
        # System Info
        'lscpu', 'dmidecode', 'lsblk', 'smartctl',
        # VNC Management
        'sudo', 'systemctl', 'vncpasswd'
    ]

    missing_commands = []
    for cmd in required_commands:
        if shutil.which(cmd) is None:
            missing_commands.append(cmd)

    if missing_commands:
        print("❌ 环境自检失败！缺少以下必要的系统命令:", file=sys.stderr)
        for cmd in missing_commands:
            print(f"  - {cmd}", file=sys.stderr)
        print("\n请根据您的操作系统 (如 Debian/Ubuntu/CentOS) 安装这些缺失的软件包。", file=sys.stderr)
        print("例如，在 Debian/Ubuntu 上，您可能需要运行:", file=sys.stderr)
        print(
            "sudo apt-get update && sudo apt-get install -y util-linux passwd coreutils openssh-client cpu-checker dmidecode smartmontools sudo systemd tigervnc-standalone-server",
            file=sys.stderr)
        return False

    print("✅ 环境自检通过，所有依赖的系统命令均已找到。")
    return True


def main():
    # 1. 权限检查
    if os.getuid() != 0:
        print("❌ 错误: 本程序必须以 root 权限运行。请使用 'sudo ./start.sh' 启动。", file=sys.stderr)
        sys.exit(1)

    # 2. 依赖检查
    if not check_system_dependencies():
        sys.exit(1)

    print("🚀 正在以生产模式启动后端服务...")
    app = create_app()

    print("✅ 后端服务正在监听 http://127.0.0.1:19132")
    serve(app, host='127.0.0.1', port=19132, threads=4)


if __name__ == '__main__':
    main()
