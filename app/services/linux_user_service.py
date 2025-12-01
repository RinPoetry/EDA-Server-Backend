# app/services/linux_user_service.py (修改后)
import pwd
import subprocess
import os
import shutil
import re
import tempfile
from pathlib import Path
from flask import current_app
from datetime import datetime


class LinuxUserService:
    """
    封装 Linux 系统用户管理的类。
    所有方法都返回一个元组 (success: bool, result_or_error: any)。
    此类中的操作通常需要 root 权限。
    """

    def _run_command(self, command, input_data=None):
        """
        一个统一的子进程执行函数，使用参数列表防止命令注入。
        :param command: 命令列表 (e.g., ['useradd', '-m', 'testuser'])
        :param input_data: (可选) 用于管道输入的字符串
        :return: (bool, str) -> (成功与否, stdout 或 stderr)
        """
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                input=input_data,
                encoding='utf-8'
            )
            return True, process.stdout.strip()
        except FileNotFoundError as e:
            return False, f"命令未找到: {e.filename}。请确保相关工具已安装。"
        except subprocess.CalledProcessError as e:
            error_message = e.stderr.strip() if e.stderr else e.stdout.strip()
            return False, f"命令 '{' '.join(command)}' 执行失败: {error_message}"
        except Exception as e:
            return False, f"执行命令时发生未知错误: {e}"

    def check_user_exists(self, username: str):
        """
        检查指定用户是否存在，以及其家目录是否已被使用。
        :param username: 要检查的用户名。
        :return: (bool, dict/str) 成功时返回包含存在状态的字典，失败时返回错误信息。
        """
        # 1. 检查系统用户是否存在
        check_user_cmd = ['id', username]
        user_exists_process = subprocess.run(check_user_cmd, capture_output=True)
        system_user_exists = user_exists_process.returncode == 0

        # 2. 检查家目录是否被使用 (无论是真实目录还是符号链接)
        home_path = Path(f"/home/{username}")
        home_dir_used = home_path.exists() or home_path.is_symlink()

        result = {
            "username": username,
            "system_user_exists": system_user_exists,
            "home_dir_used": home_dir_used,
            "is_available": not system_user_exists and not home_dir_used
        }
        return True, result

    def add_user(self, username: str, password: str, home_volume: str):
        """
        新增一个 Linux 用户。
        """
        success, check_result = self.check_user_exists(username)
        if not success or not check_result.get("is_available"):
            return False, f"用户 '{username}' 无法创建，可能已存在或其家目录被占用。"

        real_home_path = Path(home_volume) / username
        link_path = Path(f"/home/{username}/storage")

        try:
            success, msg = self._run_command(['useradd', '-m', '-s', '/bin/bash', username])
            if not success: raise RuntimeError(f"创建用户失败: {msg}")

            success, msg = self._run_command(['chpasswd'], input_data=f"{username}:{password}")
            if not success: raise RuntimeError(f"设置密码失败: {msg}")

            os.makedirs(real_home_path, exist_ok=True)
            shutil.chown(real_home_path, user=username, group=username)
            os.symlink(real_home_path, link_path)
            user_info = pwd.getpwnam(username)
            uid, gid = user_info.pw_uid, user_info.pw_gid
            os.lchown(link_path, uid, gid)

            return True, f"用户 '{username}' 创建成功。数据目录位于 {real_home_path}。"

        except Exception as e:
            self._run_command(['userdel', '-r', username])
            return False, f"创建用户 '{username}' 过程中发生错误并已回滚: {e}"

    def change_password(self, username: str, new_password: str):
        """
        修改指定用户的密码。
        """
        return self._run_command(['chpasswd'], input_data=f"{username}:{new_password}")

    # =========================================================================
    # SSH 密钥列表管理 (新增/修改)
    # =========================================================================

    def list_ssh_keys(self, username: str):
        """
        [新增] 解析用户的 authorized_keys 文件，返回结构化列表。
        支持识别 [options] key-type base64-encoded-key [comment] 格式。
        """
        try:
            user_info = pwd.getpwnam(username)
            ssh_dir = Path(user_info.pw_dir) / ".ssh"
            authorized_keys_file = ssh_dir / "authorized_keys"

            if not authorized_keys_file.exists():
                return True, []

            keys_list = []
            # 常见的SSH密钥类型正则
            key_type_pattern = r'(ssh-rsa|ssh-dss|ssh-ed25519|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)'

            with open(authorized_keys_file, 'r', encoding='utf-8') as f:
                for idx, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue

                    # 1. 检查是否被注释
                    is_commented = line.startswith('#')
                    content = line.lstrip('#').strip()

                    # 2. 尝试解析
                    # 逻辑：查找第一个出现的 key_type，它之前的是 options，它之后的是 key body 和 comment
                    match = re.search(
                        fr'(^|\s)(?P<type>{key_type_pattern})\s+(?P<key>[A-Za-z0-9+/=]+)(?:\s+(?P<comment>.*))?$',
                        content)

                    key_data = {
                        "id": idx,  # 临时ID，用于前端列表key
                        "is_commented": is_commented,
                        "options": "",
                        "key_type": "unknown",
                        "key_body": "",
                        "comment": "",
                        "original_line": line
                    }

                    if match:
                        key_data["key_type"] = match.group('type')
                        key_data["key_body"] = match.group('key')
                        key_data["comment"] = match.group('comment') or ""

                        # 提取 options (匹配开始位置之前的所有内容)
                        start_index = match.start()
                        if start_index > 0:
                            key_data["options"] = content[:start_index].strip()
                    else:
                        # 无法解析的行（可能是纯注释或其他格式），保留在 comment 或 original_line 中
                        # 为了不丢失数据，我们把整行作为 comment 显示，类型标记为 invalid
                        key_data["key_type"] = "raw_text"
                        key_data["comment"] = content

                    keys_list.append(key_data)

            return True, keys_list

        except KeyError:
            return False, f"用户 '{username}' 不存在。"
        except Exception as e:
            return False, f"读取 SSH 密钥失败: {e}"

    def update_authorized_keys(self, username: str, keys_data: list):
        """
        [新增] 接收结构化的密钥列表，全量重写 authorized_keys 文件。
        这是原子操作的安全替代方案，防止命令注入。
        """
        try:
            user_info = pwd.getpwnam(username)
            home_dir = Path(user_info.pw_dir)
            uid, gid = user_info.pw_uid, user_info.pw_gid

            ssh_dir = home_dir / ".ssh"
            authorized_keys_file = ssh_dir / "authorized_keys"

            # 确保目录存在
            os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
            shutil.chown(ssh_dir, user=uid, group=gid)

            # 构建新文件内容
            new_lines = []
            for item in keys_data:
                # 简单防注入：只允许特定字符进入关键字段，或者直接通过拼接防止 shell 解析
                # 这里我们直接拼接字符串写入文件，不经过 shell 执行，所以是安全的。

                # 如果是无法解析的原始文本，直接写回（带注释状态）
                if item.get("key_type") == "raw_text":
                    line = item.get("original_line", "").lstrip('#').strip()
                else:
                    parts = []
                    if item.get("options"):
                        parts.append(item["options"].strip())
                    parts.append(item["key_type"].strip())
                    parts.append(item["key_body"].strip())
                    if item.get("comment"):
                        parts.append(item["comment"].strip())
                    line = " ".join(parts)

                if item.get("is_commented"):
                    line = f"# {line}"

                new_lines.append(line)

            # 写入临时文件然后移动（模拟原子写）
            with tempfile.NamedTemporaryFile('w', delete=False, dir=ssh_dir, encoding='utf-8') as tf:
                tf.write("\n".join(new_lines) + "\n")
                temp_path = tf.name

            # 设置权限
            os.chmod(temp_path, 0o600)
            shutil.chown(temp_path, user=uid, group=gid)

            # 覆盖原文件
            shutil.move(temp_path, authorized_keys_file)

            return True, "SSH 密钥列表已更新。"

        except Exception as e:
            return False, f"更新 authorized_keys 失败: {e}"

    def change_ssh_key(self, username: str, public_key: str):
        """
        修改或添加用户的 SSH 公钥。
        [注意] 此方法目前是单条覆盖逻辑，若使用新的 update_authorized_keys 则此方法可逐渐废弃。
        """
        # 兼容旧接口
        public_key = public_key.strip()
        if not re.match(r'^(ssh-rsa|ecdsa-sha2-nistp\d+|ssh-ed25519) AAAA[0-9A-Za-z+/]+[=]{0,3}(\s+.+)?$', public_key):
            return False, "无效的 SSH public key 格式。"
        try:
            user_info = pwd.getpwnam(username)
            home_dir = Path(user_info.pw_dir)
            uid, gid = user_info.pw_uid, user_info.pw_gid

            ssh_dir = home_dir / ".ssh"
            authorized_keys_file = ssh_dir / "authorized_keys"

            os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
            shutil.chown(ssh_dir, user=uid, group=gid)

            with open(authorized_keys_file, 'w') as f:
                f.write(public_key + '\n')
            os.chmod(authorized_keys_file, 0o600)
            shutil.chown(authorized_keys_file, user=uid, group=gid)

            return True, f"用户 '{username}' 的 SSH 公钥已更新。"
        except KeyError:
            return False, f"用户 '{username}' 不存在。"
        except Exception as e:
            return False, f"更新 SSH 公key时出错: {e}"

    def generate_ssh_key_pair(self, username: str):
        """
        在服务器上为用户生成一个新的 SSH 密钥对 (ed25519)。
        [注意] 此方法通过 append ('a') 模式写入，与新的列表管理方式兼容。
        """
        try:
            user_info = pwd.getpwnam(username)
            home_dir = Path(user_info.pw_dir)
            uid, gid = user_info.pw_uid, user_info.pw_gid

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                key_path = tmp_path / "id_ed25519"

                # 1. 生成密钥对，不设置密码(-N '')，并添加注释
                keygen_cmd = ['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', str(key_path), '-C',
                              f"{username}@panel-generated"]
                success, msg = self._run_command(keygen_cmd)
                if not success:
                    return False, f"生成SSH密钥失败: {msg}", None

                public_key = key_path.with_suffix('.pub').read_text()
                private_key = key_path.read_text()

                # 2. 准备 .ssh 目录并确保其所有权正确
                ssh_dir = home_dir / ".ssh"
                os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
                shutil.chown(ssh_dir, user=uid, group=gid)

                # 3. 将新公钥【附加】到 authorized_keys，而不是覆盖
                authorized_keys_file = ssh_dir / "authorized_keys"
                with open(authorized_keys_file, 'a') as f:
                    # 在公钥前加一个换行符和注释，以防前一个密钥没有换行
                    f.write(f"\n# Generated by panel on {datetime.now().isoformat()}\n")
                    f.write(public_key)

                # 4. 确保 authorized_keys 文件的权限和所有权正确
                os.chmod(authorized_keys_file, 0o600)
                shutil.chown(authorized_keys_file, user=uid, group=gid)

                # 5. 返回私钥，让上层函数处理邮件发送
                return True, "密钥对已成功生成，公钥已配置。", private_key

        except KeyError:
            return False, f"Linux 用户 '{username}' 不存在。", None
        except Exception as e:
            return False, f"生成密钥对时发生内部错误: {e}", None


# 单例
linux_user_service = LinuxUserService()
