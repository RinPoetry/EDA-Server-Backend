# app/services/email_service.py (修改后)

import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from flask import current_app
# 【新增】导入 formataddr 用于正确格式化带中文的发件人，以及 Header 用于主题
from email.utils import formataddr
from email.header import Header


class EmailService:
    """
    封装邮件发送服务的类。
    支持发送纯文本邮件、HTML邮件以及带附件的邮件。
    """

    def send_email(self, to, subject, body, attachments=None, is_html=False):
        """
        发送邮件的核心方法。
        【修改】
        - 使用 formataddr 规范化 From 头部，解决中文名编码问题。
        - 使用 Header 规范化 Subject 头部，解决中文主题编码问题。
        - 增加配置完整性检查。
        """
        config = current_app.config
        sender_name, sender_email = config['MAIL_DEFAULT_SENDER']
        username = config['MAIL_USERNAME']
        password = config['MAIL_PASSWORD']
        server_host = config['MAIL_SERVER']
        server_port = config['MAIL_PORT']
        use_ssl = config['MAIL_USE_SSL']

        # 【新增】健壮性检查：在尝试发送前，确保关键配置已设置
        if not all([username, password, server_host]):
            error_msg = "邮件服务配置不完整 (MAIL_USERNAME, MAIL_PASSWORD, MAIL_SERVER 必须在环境变量中设置)。"
            current_app.logger.error(error_msg)
            return False, error_msg

        # 构造邮件对象
        msg = MIMEMultipart()

        # 【核心修复】使用 formataddr 格式化 From 头部
        # 这会自动处理 sender_name 中的中文字符编码，使其符合 RFC 标准
        msg['From'] = formataddr((sender_name, sender_email))

        # 处理多个收件人
        if isinstance(to, list):
            msg['To'] = ", ".join(to)
        else:
            msg['To'] = to

        # 【核心修复】确保邮件主题中的非ASCII字符也被正确编码
        msg['Subject'] = Header(subject, 'utf-8')

        # 邮件正文
        body_type = 'html' if is_html else 'plain'
        msg.attach(MIMEText(body, body_type, 'utf-8'))

        # 处理附件 (逻辑保持不变)
        if attachments:
            for file_path in attachments:
                if not os.path.exists(file_path):
                    current_app.logger.warning(f"附件未找到: {file_path}, 已跳过。")
                    continue
                with open(file_path, 'rb') as attachment:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment.read())
                encoders.encode_base64(part)
                filename = os.path.basename(file_path)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename="{filename}"'
                )
                msg.attach(part)

        # 发送邮件 (逻辑保持不变)
        try:
            if use_ssl:
                with smtplib.SMTP_SSL(server_host, server_port) as server:
                    server.login(username, password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(server_host, server_port) as server:
                    server.starttls()
                    server.login(username, password)
                    server.send_message(msg)
            return True, "邮件发送成功。"
        except smtplib.SMTPAuthenticationError:
            return False, "SMTP认证失败。请检查您的邮箱用户名或密码配置。"
        except Exception as e:
            current_app.logger.error(f"邮件服务异常: {e}", exc_info=True)
            return False, f"发送邮件时发生错误: {e}"


# 创建一个邮件服务的单例
email_service = EmailService()
