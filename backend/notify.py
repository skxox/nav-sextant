"""告警触达：邮件（SMTP）+ 飞书/企业微信 Webhook。

触发条件由 pipeline 决定（BUY/SELL 信号或风控拦截）；本模块只负责投递，
任何渠道失败只记日志，不影响主流程。
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText

import requests

from .config import NotificationSettings
from .contracts import SignalModel

logger = logging.getLogger(__name__)


def format_signal_text(signals: list[SignalModel]) -> str:
    """把当日信号渲染为文本（飞书/邮件通用）。"""
    if not signals:
        return "nav-sextant：今日无信号"
    lines = ["nav-sextant 每日信号"]
    for s in signals:
        risk = "通过" if s.risk_passed else f"拦截（{s.risk_summary}）"
        debate = ""
        if s.debate.triggered:
            debate = f"，辩论层{'维持' if s.debate.agree else '否决→HOLD'}"
        lines.append(
            f"· {s.fund_name}（{s.fund_code}）：{s.action}，"
            f"综合分 {s.composite_score:+.3f}，建议仓位 {s.target_position:.0%}，"
            f"风控 {risk}{debate}"
        )
    return "\n".join(lines)


class Notifier:
    def __init__(self, settings: NotificationSettings):
        self.settings = settings

    @property
    def active(self) -> bool:
        s = self.settings
        return s.enabled and bool(
            s.feishu_webhook or s.wecom_webhook or (s.email_to and s.smtp_host))

    def send(self, signals: list[SignalModel]) -> None:
        if not self.active:
            return
        text = format_signal_text(signals)
        if self.settings.feishu_webhook:
            try:
                requests.post(self.settings.feishu_webhook,
                              json={"msg_type": "text", "content": {"text": text}}, timeout=10).raise_for_status()
            except Exception as e:  # noqa: BLE001
                logger.warning("飞书推送失败: %s", e)
        if self.settings.wecom_webhook:
            try:
                requests.post(self.settings.wecom_webhook,
                              json={"msgtype": "text", "text": {"content": text}}, timeout=10).raise_for_status()
            except Exception as e:  # noqa: BLE001
                logger.warning("企业微信推送失败: %s", e)
        if self.settings.email_to and self.settings.smtp_host:
            try:
                self._send_email(text)
            except Exception as e:  # noqa: BLE001
                logger.warning("邮件推送失败: %s", e)

    def _send_email(self, text: str) -> None:
        s = self.settings
        password = os.environ.get(s.smtp_password_env, "")
        if not password:
            raise RuntimeError(f"未设置环境变量 {s.smtp_password_env}")
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = Header("nav-sextant 每日信号", "utf-8")
        msg["From"] = s.email_from or s.smtp_user
        msg["To"] = ", ".join(s.email_to)
        if s.smtp_port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
        try:
            server.login(s.smtp_user, password)
            server.sendmail(msg["From"], s.email_to, msg.as_string())
        finally:
            server.quit()
