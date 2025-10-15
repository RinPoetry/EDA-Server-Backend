# app/services/tiger_vnc_service.py (修复后)

import shutil
import subprocess
import os
from pathlib import Path
from flask import current_app
import pwd


class TigerVNCService:
    """
    封装 TigerVNC 服务管理功能。
    所有方法返回 (success: bool, result_or_error: any)。
    """
    # 配置文件模板 (保持不变)
    VNC_CONFIG_TEMPLATE = """# 由 TigerVNCService 自动生成
session=gnome
geometry=1920x1080
securitytypes=VncAuth,TLSVnc
"""

    VNC_XSTARTUP_TEMPLATE = """#!/bin/sh
# 由 TigerVNCService 自动生成
export XKL_XMODMAP_DISABLE=1
export XDG_CURRENT_DESKTOP="ubuntu:GNOME"
export GNOME_SHELL_SESSION_MODE="ubuntu"
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS

[ -x /etc/vnc/xstartup ] && exec /etc/vnc/xstartup
[ -r $HOME/.Xresources ] && xrdb $HOME/.Xresources

XAUTHORITY=$HOME/.Xauthority
export XAUTHORITY

xsetroot -solid grey
# vncconfig -iconic &

exec gnome-session
"""

    # 【核心修复】修改 _run_command 以支持二进制输入/输出
    def _run_command(self, command, as_user=None, input_data=None, expect_binary=False):
        """
        统一的子进程执行函数。
        【修改】新增 expect_binary 参数。
        如果为 True, 则不进行文本解码，直接处理原始字节。
        """
        if as_user:
            command = ['sudo', '-u', as_user] + command

        try:
            # 准备传递给 subprocess.run 的参数
            kwargs = {
                'capture_output': True,
                'check': 'status' not in command,  # status 命令不检查退出码
            }

            if expect_binary:
                # 二进制模式：不设置 text 和 encoding，输入数据需要编码
                if isinstance(input_data, str):
                    kwargs['input'] = input_data.encode('utf-8')
            else:
                # 文本模式：设置 text 和 encoding
                kwargs['text'] = True
                kwargs['encoding'] = 'utf-8'
                kwargs['input'] = input_data

            process = subprocess.run(command, **kwargs)

            if process.returncode != 0 and 'status' not in command:
                raise subprocess.CalledProcessError(process.returncode, command, output=process.stdout,
                                                    stderr=process.stderr)

            if expect_binary:
                # 返回原始字节
                return True, process.stdout
            else:
                # 返回解码后的文本
                output = process.stdout.strip() if process.stdout else process.stderr.strip()
                return True, output

        except FileNotFoundError as e:
            return False, f"命令未找到: {e.filename}。"
        except subprocess.CalledProcessError as e:
            error_message = e.stderr if isinstance(e.stderr, str) else e.stderr.decode('utf-8', errors='ignore')
            output_message = e.stdout if isinstance(e.stdout, str) else e.stdout.decode('utf-8', errors='ignore')
            full_error = error_message.strip() if error_message.strip() else output_message.strip()
            return False, f"命令 '{' '.join(command)}' 执行失败: {full_error}"
        except Exception as e:
            return False, f"执行命令时发生未知错误: {e}"

    def _get_or_assign_vnc_port(self, username: str):
        """
        获取或为用户分配一个新的 VNC 端口。
        如果 /etc/tigervnc/vncserver.users 不存在，则创建它。
        :return: (str, str) -> (端口号, 错误信息或 None)。
        """
        vnc_users_file = Path(current_app.config.get('VNC_USERS_FILE', '/etc/tigervnc/vncserver.users'))
        users_map = {}
        max_port = 0

        try:
            vnc_users_file.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return None, f"权限不足，无法创建目录 {vnc_users_file.parent}。"
        except Exception as e:
            return None, f"创建目录时发生错误: {e}"

        try:
            vnc_users_file.touch(exist_ok=True)
        except PermissionError:
            return None, f"权限不足，无法创建文件 {vnc_users_file}。"
        except Exception as e:
            return None, f"创建文件时发生错误: {e}"

        try:
            with open(vnc_users_file, 'r') as f:
                for line in f:
                    if line.strip() and not line.startswith('#'):
                        display, user = line.strip().split('=', 1)
                        port_num = int(display.replace(':', ''))
                        users_map[user] = port_num
                        if port_num > max_port:
                            max_port = port_num

            if username in users_map:
                return str(users_map[username]), None

            new_port = max_port + 1
            with open(vnc_users_file, 'a') as f:
                f.write(f":{new_port}={username}\n")

            return str(new_port), None

        except PermissionError:
            return None, f"读写 VNC 配置文件 {vnc_users_file} 时权限不足。请确保应用有权修改此文件。"
        except Exception as e:
            return None, f"处理 VNC 配置文件时出错: {e}"

    def _get_vnc_port(self, username: str):
        """
        从 VNC 配置文件中获取用户的端口号。
        :return: (str, str) -> (端口号, 错误信息或 None)。
        """
        vnc_users_file = current_app.config['VNC_USERS_FILE']
        try:
            with open(vnc_users_file, 'r') as f:
                for line in f:
                    if line.strip() and not line.startswith('#'):
                        display, user = line.strip().split('=', 1)
                        if user == username:
                            return display.replace(':', ''), None
            return None, f"在 {vnc_users_file} 中未找到用户 '{username}' 的配置。"
        except FileNotFoundError:
            return None, f"VNC 配置文件 {vnc_users_file} 未找到。"
        except Exception as e:
            return None, f"读取 VNC 配置文件时出错: {e}"

    def get_status(self, username: str):
        """
        查询指定用户的 VNC 服务状态。
        """
        port, error = self._get_vnc_port(username)
        if error:
            # 如果用户还没有端口，说明VNC未初始化，这是正常状态
            return True, {"username": username, "address": "", "port": 0, "status": "uninitialized", "details": error}

        success, output = self._run_command(['systemctl', 'status', f'tigervncserver@:{port}.service'])

        if "Active: active (running)" in output:
            status = "active"
        elif "Active: inactive (dead)" in output:
            status = "inactive"
        else:
            status = "unknown"
        server_address = current_app.config.get('SERVER_ADDRESS', 'IP')
        return True, {"username": username, "address": server_address, "port": int(port) + 5900, "status": status,
                      "details": output}

    def initialize(self, username: str, vnc_password: str):
        """
        为用户初始化 VNC 配置（分配端口、设置密码、创建配置文件）。
        【修改】调用 _run_command 时使用 expect_binary=True 并以二进制模式写入文件。
        """
        try:
            user_info = pwd.getpwnam(username)
            home_dir = Path(user_info.pw_dir)
            vnc_dir = home_dir / ".vnc"

            port, error = self._get_or_assign_vnc_port(username)
            if error:
                return False, f"无法为用户 '{username}' 分配 VNC 端口: {error}"

            os.makedirs(vnc_dir, exist_ok=True, mode=0o700)
            shutil.chown(vnc_dir, user=user_info.pw_uid, group=user_info.pw_gid)

            # 4. 【核心修复】设置 VNC 密码
            passwd_file = vnc_dir / "passwd"
            cmd = ['vncpasswd', '-f']
            # 以二进制模式执行命令，获取加密后的字节数据
            success, encrypted_password_bytes = self._run_command(
                cmd, as_user=username, input_data=vnc_password, expect_binary=True
            )
            if not success:
                return False, f"设置 VNC 密码失败: {encrypted_password_bytes}"

            # 以二进制写入模式('wb')打开文件，并写入字节数据
            with open(passwd_file, 'wb') as f:
                f.write(encrypted_password_bytes)

            os.chmod(passwd_file, 0o600)
            shutil.chown(passwd_file, user=user_info.pw_uid, group=user_info.pw_gid)

            config_file = vnc_dir / "config"
            with open(config_file, 'w') as f:
                f.write(self.VNC_CONFIG_TEMPLATE)
            os.chmod(config_file, 0o644)
            shutil.chown(config_file, user=user_info.pw_uid, group=user_info.pw_gid)

            xstartup_file = vnc_dir / "xstartup"
            with open(xstartup_file, 'w') as f:
                f.write(self.VNC_XSTARTUP_TEMPLATE)
            os.chmod(xstartup_file, 0o755)
            shutil.chown(xstartup_file, user=user_info.pw_uid, group=user_info.pw_gid)

            return True, f"用户 '{username}' 的 VNC 已成功初始化。分配的端口号是 :{port}。"

        except KeyError:
            return False, f"系统用户 '{username}' 不存在。"
        except Exception as e:
            return False, f"初始化 VNC 配置时发生未知错误: {e}"

    def _control_service(self, username: str, action: str):
        """
        内部函数，用于启动、关闭、重启 VNC 服务。
        """
        port, error = self._get_vnc_port(username)
        if error:
            return False, error

        if action in ['enable', 'disable']:
            success, msg = self._run_command(['systemctl', action, f'tigervncserver@:{port}.service'])
        else:
            success, msg = self._run_command(['systemctl', action, f'tigervncserver@:{port}.service'])

        if success:
            if action == 'status':
                return True, msg
            return True, f"VNC 服务 for '{username}' on port {port} has been {action}d."
        return False, msg

    def start(self, username: str):
        return self._control_service(username, 'start')

    def stop(self, username: str):
        return self._control_service(username, 'stop')

    def restart(self, username: str):
        return self._control_service(username, 'restart')

    def enable(self, username: str):
        return self._control_service(username, 'enable')

    def disable(self, username: str):
        return self._control_service(username, 'disable')

    def reset_password(self, username: str, new_password: str):
        """
        重设用户的 VNC 密码。
        【修改】与 initialize 方法保持一致，使用二进制模式处理密码。
        """
        try:
            user_info = pwd.getpwnam(username)
            home_dir = Path(user_info.pw_dir)
            vnc_dir = home_dir / ".vnc"
            passwd_file = vnc_dir / "passwd"

            if not vnc_dir.exists() or not (home_dir / ".vnc/config").exists():
                return False, f"用户 '{username}' 尚未初始化, 请先执行初始化。"

            cmd = ['vncpasswd', '-f']
            # 【核心修复】以二进制模式执行命令
            success, encrypted_password_bytes = self._run_command(
                cmd, as_user=username, input_data=new_password, expect_binary=True
            )
            if not success:
                return False, f"重设 VNC 密码失败: {encrypted_password_bytes}"

            # 【核心修复】以二进制模式写入文件
            with open(passwd_file, 'wb') as f:
                f.write(encrypted_password_bytes)

            os.chmod(passwd_file, 0o600)
            shutil.chown(passwd_file, user=user_info.pw_uid, group=user_info.pw_gid)

            return True, f"用户 '{username}' 的 VNC 密码已成功重置。"

        except KeyError:
            return False, f"系统用户 '{username}' 不存在。"
        except Exception as e:
            return False, f"重设密码时发生错误: {e}"


# 单例
tiger_vnc_service = TigerVNCService()
