# app/services/confirmation_service.py

from flask import current_app, render_template, request
from datetime import datetime, timezone, timedelta
from app.services.sqlite_database_service import db_service
from app.services.email_service import email_service
from app.utils.response_utils import success, error, RetCode


class ConfirmationService:
    """
    统一处理需要二次确认（2FA/邮件验证）的敏感操作服务。
    封装了频率限制、Token生成、邮件渲染和发送逻辑。
    """

    def send_confirmation_email(self, user, action_type, payload, subject,
                                action_name, intro_text=None, warning_text=None, button_text="确认操作"):
        """
        发送通用的操作确认邮件。

        :param user: 用户字典对象 (需包含 id, name, email, last_email_sent_at)
        :param action_type: 操作类型字符串 (如 'CHANGE_PASSWORD', '2FA_LOGIN')
        :param payload: 需要保存到 PendingAction 的数据字典
        :param subject: 邮件主题
        :param action_name: 显示在邮件中的操作名称
        :param intro_text: (可选) 自定义介绍文字，默认为标准安全提醒
        :param warning_text: (可选) 自定义警告文字
        :param button_text: (可选) 按钮文字
        """
        # 1. 检查发送频率
        interval = timedelta(seconds=current_app.config['MAIL_SEND_INTERVAL_SECONDS'])
        last_sent_at = user['last_email_sent_at']
        if last_sent_at and datetime.fromisoformat(last_sent_at) + interval > datetime.now(timezone.utc):
            return error(RetCode.EMAIL_RATE_LIMIT_EXCEEDED)

        # 2. 创建待办事项 (Pending Action)
        action_token = db_service.create_pending_action(user['id'], action_type, payload)

        # 3. 构建确认链接
        # 前端路由通常期望: /confirm-action?token=xyz&action=type
        frontend_action_param = action_type.lower()
        if action_type == '2FA_LOGIN':
            frontend_action_param = 'login'  # 特殊处理登录的action参数以匹配前端

        confirm_url = current_app.config['TWO_FACTOR_AUTH_URL_TEMPLATE'].format(
            token=action_token,
            action=frontend_action_param
        )

        # 4. 准备模板变量
        config = current_app.config
        expire_minutes = config['PENDING_ACTION_TOKEN_EXPIRES_IN'].total_seconds() / 60
        ip_address = request.remote_addr

        # 默认文案
        if not intro_text:
            intro_text = "我们收到一个请求，要求对您的账户执行敏感操作。为保护您的账户安全，需要您进行确认。"
        if not warning_text:
            warning_text = "如果您没有进行此操作，请立即登录面板修改您的密码，并检查账户活动。"

        # 5. 渲染邮件 HTML
        html_body = render_template(
            'emails/action_confirmation.html',
            server_name=config['SERVER_NAME'],
            sender_name=config['MAIL_SENDER_NAME'],
            name=user['name'],
            title=subject,
            intro_text=intro_text,
            action_name=action_name,
            timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            ip_address=ip_address,
            confirm_url=confirm_url,
            button_text=button_text,
            warning_text=warning_text,
            expire_minutes=f"{expire_minutes:.0f}"
        )

        # 6. 发送邮件
        # 注意：email_service 现在会自动处理 Header 编码
        email_sent, msg = email_service.send_email(
            to=user['email'],
            subject=f"[{config['SERVER_NAME']}] {subject}",
            body=html_body,
            is_html=True
        )

        if not email_sent:
            return error(RetCode.INTERNAL_ERROR, msg=f"发送确认邮件失败: {msg}")

        # 7. 更新发送时间戳
        db_service.update_last_email_time(user['id'])

        return success(msg="验证邮件已发送，请检查您的邮箱并完成操作。")


# 单例
confirmation_service = ConfirmationService()
