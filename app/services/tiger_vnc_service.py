# app/services/tiger_vnc_service.py

import shutil
import subprocess
import os
from pathlib import Path
from flask import current_app
import pwd


class TigerVNCService:
    """
    封装 TigerVNC 服务管理功能。
    """

    # 配置文件模板
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

    def _run_command(self, command, as_user=None, input_data=None, expect_binary=False):
        """
        统一的子进程执行函数。
        【修改】新增 expect_binary 参数。
        - expect_binary=True: 返回 stdout 的原始字节 (bytes)，不进行解码。输入也必须处理好编码。
        - expect_binary=False: 返回解码后的字符串 (str)。
        """
        if as_user:
            command = ['sudo', '-u', as_user] + command

        try:
            kwargs = {
                'capture_output': True,
                'check': 'status' not in command,
            }

            if expect_binary:
                # 二进制模式：不设置 text=True
                if isinstance(input_data, str):
                    kwargs['input'] = input_data.encode('utf-8')
                else:
                    kwargs['input'] = input_data
            else:
                # 文本模式
                kwargs['text'] = True
                kwargs['encoding'] = 'utf-8'
                kwargs['input'] = input_data

            process = subprocess.run(command, **kwargs)

            if process.returncode != 0 and 'status' not in command:
                # 尝试解码错误信息以便记录
                err_msg = process.stderr if process.stderr else process.stdout
                if isinstance(err_msg, bytes):
                    err_msg = err_msg.decode('utf-8', errors='replace')
                raise subprocess.CalledProcessError(process.returncode, command, stderr=err_msg)

            if expect_binary:
                return True, process.stdout  # 返回 bytes
            else:
                output = process.stdout.strip() if process.stdout else ""
                return True, output

        except FileNotFoundError as e:
            return False, f"命令未找到: {e.filename}。"
        except subprocess.CalledProcessError as e:
            return False, f"命令执行失败: {e.stderr}"
        except Exception as e:
            return False, f"执行命令时发生未知错误: {e}"

    def _get_or_assign_vnc_port(self, username: str):
        """获取或为用户分配一个新的 VNC 端口。"""
        # ... (逻辑保持不变，省略以节省空间，功能无变化) ...
        vnc_users_file = Path(current_app.config.get('VNC_USERS_FILE', '/etc/tigervnc/vncserver.users'))
        users_map = {}
        max_port = 0
        try:
            vnc_users_file.parent.mkdir(parents=True, exist_ok=True)
            vnc_users_file.touch(exist_ok=True)
            with open(vnc_users_file, 'r') as f:
                for line in f:
                    if line.strip() and not line.startswith('#'):
                        display, user = line.strip().split('=', 1)
                        port_num = int(display.replace(':', ''))
                        users_map[user] = port_num
                        if port_num > max_port: max_port = port_num
            if username in users_map: return str(users_map[username]), None
            new_port = max_port + 1
            with open(vnc_users_file, 'a') as f:
                f.write(f":{new_port}={username}\n")
            return str(new_port), None
        except Exception as e:
            return None, f"分配端口失败: {e}"

    def _get_vnc_port(self, username: str):
        """从配置文件获取端口"""
        vnc_users_file = current_app.config['VNC_USERS_FILE']
        try:
            with open(vnc_users_file, 'r') as f:
                for line in f:
                    if line.strip() and not line.startswith('#'):
                        display, user = line.strip().split('=', 1)
                        if user == username: return display.replace(':', ''), None
            return None, f"未找到用户配置"
        except Exception:
            return None, "读取配置失败"

    def get_status(self, username: str):
        """查询状态"""
        port, error = self._get_vnc_port(username)
        if error:
            return True, {"username": username, "status": "uninitialized", "port": 0}
        success, output = self._run_command(['systemctl', 'status', f'tigervncserver@:{port}.service'])
        status = "active" if "Active: active (running)" in output else "inactive"
        return True, {"username": username, "status": status, "port": int(port) + 5900}

    def initialize(self, username: str, vnc_password: str):
        """
        初始化 VNC。
        """
        try:
            user_info = pwd.getpwnam(username)
            home_dir = Path(user_info.pw_dir)
            vnc_dir = home_dir / ".vnc"

            port, error = self._get_or_assign_vnc_port(username)
            if error:
                return False, error

            os.makedirs(vnc_dir, exist_ok=True, mode=0o700)
            shutil.chown(vnc_dir, user=user_info.pw_uid, group=user_info.pw_gid)

            # 写入密码
            passwd_file = vnc_dir / "passwd"
            # expect_binary=True 返回字节流
            success, encrypted_bytes = self._run_command(
                ['vncpasswd', '-f'], as_user=username, input_data=vnc_password, expect_binary=True
            )
            if not success:
                return False, f"生成密码失败: {encrypted_bytes}"

            # 使用 'wb' 模式写入二进制文件
            with open(passwd_file, 'wb') as f:
                f.write(encrypted_bytes)

            os.chmod(passwd_file, 0o600)
            shutil.chown(passwd_file, user=user_info.pw_uid, group=user_info.pw_gid)

            # 写入配置
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

            return True, f"VNC 初始化成功，端口 :{port}。"
        except Exception as e:
            return False, f"初始化失败: {e}"

    def reset_password(self, username: str, new_password: str):
        """
        重置密码。
        【关键修复】同 initialize，使用二进制处理。
        """
        try:
            user_info = pwd.getpwnam(username)
            vnc_dir = Path(user_info.pw_dir) / ".vnc"
            passwd_file = vnc_dir / "passwd"

            if not vnc_dir.exists(): return False, "未初始化"

            success, encrypted_bytes = self._run_command(
                ['vncpasswd', '-f'], as_user=username, input_data=new_password, expect_binary=True
            )
            if not success: return False, "生成密码失败"

            with open(passwd_file, 'wb') as f:
                f.write(encrypted_bytes)

            os.chmod(passwd_file, 0o600)
            shutil.chown(passwd_file, user=user_info.pw_uid, group=user_info.pw_gid)

            return True, "VNC 密码重置成功"
        except Exception as e:
            return False, f"重置密码失败: {e}"

    # ... start, stop, restart 等方法逻辑简单，保持原样即可 ...
    def start(self, username: str):
        return self._control(username, 'start')

    def stop(self, username: str):
        return self._control(username, 'stop')

    def restart(self, username: str):
        return self._control(username, 'restart')

    def _control(self, username, action):
        port, err = self._get_vnc_port(username)
        if err:
            return False, err
        succ, msg = self._run_command(['systemctl', action, f'tigervncserver@:{port}.service'])
        return succ, msg


tiger_vnc_service = TigerVNCService()
