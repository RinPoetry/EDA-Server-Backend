#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Flask 应用启动脚本

功能：
1. 检查当前用户是否为 Root 用户。
2. 如果不是 Root 用户，则自动尝试使用 'sudo' 命令提权并重新运行此脚本。
   这会提示用户输入密码以获取管理员权限。
3. 如果已经是 Root 用户，则直接启动 Flask Web 应用。

注意：本脚本必须以 Root 权限运行，因为应用可能需要绑定到低端口（<1024）
或访问受保护的系统资源。
"""

# 导入必要的系统模块
import os
import sys

# 从 app 包中导入应用创建函数
from app import create_app


def main():
    """
    应用程序的主执行函数。
    此函数包含了需要 Root 权限才能执行的核心逻辑。
    """
    # 打印状态信息，告知用户应用正在以 Root 权限启动
    print("✅ 权限检查通过，以 Root 身份启动应用...")

    # 使用在 app/__init__.py 中定义的工厂函数创建 Flask app 实例
    app = create_app()

    # 启动 Flask 开发服务器
    # host='0.0.0.0' 表示监听所有网络接口，允许局域网内的其他设备访问
    # port=19132 是指定的监听端口
    # debug=True 开启调试模式，这在开发阶段非常有用，但在生产环境中应关闭
    print(f"🚀 服务正在启动，请通过 http://0.0.0.0:19233 访问")
    app.run(host='0.0.0.0', port=19233, debug=True)


# 这是 Python 脚本的入口点
if __name__ == '__main__':

    # --- 权限检查与提权核心逻辑 ---

    # 1. 检查当前用户的用户ID (UID)。在 Linux/Unix 系统中，Root 用户的 UID 为 0。
    #    os.getuid() 返回当前进程的有效用户 ID。
    if os.getuid() != 0:
        # 2. 如果 UID 不是 0，说明当前用户不是 Root。
        #    打印提示信息，告知用户需要 Root 权限，并即将尝试提权。
        print("❌ 权限不足。此脚本需要 Root 权限才能运行。")
        print("正在尝试使用 'sudo' 重新启动...")

        # 3. 构造提权后要执行的新命令。
        #    - 'sudo': 用于提权的命令。
        #    - sys.executable: 当前 Python 解释器的完整路径 (例如 /usr/bin/python3)。
        #      这确保了即使在虚拟环境中也能使用正确的解释器。
        #    - sys.argv: 一个包含所有原始命令行参数的列表，
        #      例如 ['run.py', 'arg1', 'arg2']。
        #      这样可以保证重新执行时所有参数都得到保留。
        #
        #    我们将这些部分拼接成一个新的命令列表: ['sudo', '/usr/bin/python3', 'run.py', 'arg1', 'arg2']
        command_to_rerun = ['sudo', sys.executable] + sys.argv

        # 4. 使用 os.execvp() 执行新命令。
        #    这个函数会用新程序完全替换掉当前的 Python 进程。
        #    - 第一个参数 'sudo' 是要执行的程序名。
        #    - 第二个参数是完整的命令列表。
        #    如果 'sudo' 成功执行（用户输入了正确的密码），新的 root 权限脚本会运行，
        #    当前的非 root 脚本会直接终止。如果用户取消或密码错误，整个过程会失败并退出。
        try:
            os.execvp(command_to_rerun[0], command_to_rerun)
        except OSError as e:
            # 如果 'sudo' 命令不存在或执行失败，捕获异常并打印错误信息
            print(f"!! 提权失败: {e}", file=sys.stderr)
            sys.exit(1)  # 以错误码 1 退出

    else:
        # 5. 如果 os.getuid() 等于 0，说明已经是 Root 用户。
        #    直接调用主函数，启动应用。
        main()
