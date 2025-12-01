# app/services/email_service.py

import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr
from email.header import Header
from flask import current_app


class EmailService:
    """
    封装邮件发送服务的类。
    支持发送纯文本邮件、HTML邮件以及带附件的邮件。
    已修复中文发件人名称和主题的乱码问题。
    """

    def send_email(self, to, subject, body, attachments=None, is_html=False):
        """
        发送邮件的核心方法。
        """
        config = current_app.config
        sender_name, sender_email = config['MAIL_DEFAULT_SENDER']
        username = config['MAIL_USERNAME']
        password = config['MAIL_PASSWORD']
        server_host = config['MAIL_SERVER']
        server_port = config['MAIL_PORT']
        use_ssl = config['MAIL_USE_SSL']

        # 健壮性检查
        if not all([username, password, server_host]):
            error_msg = "邮件服务配置不完整 (MAIL_USERNAME, MAIL_PASSWORD, MAIL_SERVER 必须设置)。"
            current_app.logger.error(error_msg)
            return False, error_msg

        # 构造邮件对象
        msg = MIMEMultipart()

        # 【关键修复】使用 formataddr 处理带中文的发件人格式: "姓名 <email>"
        # formataddr 第一个参数是 (显示名称, 邮箱地址)，它会自动进行 RFC2047 编码
        msg['From'] = formataddr((sender_name, sender_email))

        if isinstance(to, list):
            msg['To'] = ", ".join(to)
        else:
            msg['To'] = to

        # 【关键修复】使用 Header 处理带中文的主题
        msg['Subject'] = Header(subject, 'utf-8')

        # 邮件正文
        body_type = 'html' if is_html else 'plain'
        msg.attach(MIMEText(body, body_type, 'utf-8'))

        # 处理附件
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
                # 处理附件名中文编码
                part.add_header(
                    'Content-Disposition',
                    'attachment',
                    filename=filename  # 现代客户端通常能处理UTF-8，若兼容性要求高需额外处理
                )
                msg.attach(part)

        # 发送邮件
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


email_service = EmailService()
