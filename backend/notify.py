"""告警触达：邮件（SMTP）+ 飞书/企业微信 Webhook。

触发条件由 pipeline 决定（BUY/SELL 信号或风控拦截）；本模块只负责投递，
任何渠道失败只记日志，不影响主流程。

推送 actionable 化（ROADMAP E 组）：消息直接携带操作参数——方向、目标
仓位、相对当前仓位的变动与金额估算、一句话理由、次日 15:00 截单提醒，
尾部提示回系统记录执行（衔接决策日志），做到「收到即可照做」。
"""
from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass, field
from email.header import Header
from email.mime.text import MIMEText

import requests

from .config import NotificationSettings
from .contracts import SignalModel

logger = logging.getLogger(__name__)

AGENT_LABELS = {"technical": "技术面", "valuation": "估值面",
                "sentiment": "消息面", "risk": "风险面"}


@dataclass
class ActionContext:
    """操作参数的现态来源：fund_position 持仓 + 组合市值（pipeline 查库填充）。"""
    positions: dict[str, float | None] = field(default_factory=dict)  # fund_code -> 当前仓位（占组合）
    portfolio_value: float | None = None                              # 组合总市值（元）


def _fmt_money(v: float) -> str:
    if v >= 1e4:
        return f"¥{v / 1e4:.1f}万"
    return f"¥{round(v / 100) * 100:,.0f}"


def _one_line(text: str, limit: int = 40) -> str:
    """取第一句（按。；; 截断）并限长。"""
    for sep in ("。", "；", ";"):
        if sep in text:
            text = text.split(sep, 1)[0]
    return text if len(text) <= limit else text[:limit] + "…"


def _top_reason(s: SignalModel) -> str:
    """与信号方向最一致的 Agent 的摘要作为一句话理由（BUY 取最高分、SELL 取最低分）。"""
    if not s.agents:
        return ""
    ranked = sorted(s.agents.values(), key=lambda a: a.score,
                    reverse=s.action != "SELL")
    top = ranked[0]
    return f"{AGENT_LABELS.get(top.agent, top.agent)}：{_one_line(top.summary)}"


def _action_line(s: SignalModel, ctx: ActionContext | None) -> str:
    """操作参数行：目标仓位（当前仓位，变动 ≈ 金额）。"""
    pos = ctx.positions.get(s.fund_code) if ctx else None
    target = s.target_position
    if not s.risk_passed:
        return f"  不执行：{_one_line(s.risk_summary, 30)}"
    if pos is None:
        return f"  调仓至 {target:.0%}（当前仓位未录入）"
    delta = target - pos
    money = (f" ≈ {_fmt_money(abs(delta) * ctx.portfolio_value)}"
             if ctx and ctx.portfolio_value and abs(delta) > 1e-9 else "")
    verb = "清仓" if target <= 1e-9 else "减仓至" if delta < 0 else "加仓至"
    return f"  {verb} {target:.0%}（当前 {pos:.0%}，变动 {delta:+.0%}{money}）"


def format_signal_text(signals: list[SignalModel], ctx: ActionContext | None = None) -> str:
    """把当日信号渲染为 actionable 文本（飞书/邮件通用）。"""
    if not signals:
        return "nav-sextant：今日无信号"
    trade_date = signals[0].trade_date
    lines = [f"nav-sextant 每日信号 {trade_date}",
             "操作指引：次日交易日 15:00 前下单，按下单日净值成交", ""]
    # 可执行的排前面，风控拦截的垫底
    ordered = sorted(signals, key=lambda s: s.action in ("BUY", "SELL") and s.risk_passed,
                     reverse=True)
    for s in ordered:
        verb = {"BUY": "买入", "SELL": "卖出"}.get(s.action, s.action)
        if not s.risk_passed:
            verb = "拦截"
        lines.append(f"▶ {verb} {s.fund_name}（{s.fund_code}）")
        lines.append(_action_line(s, ctx))
        reason = _top_reason(s)
        if reason:
            lines.append(f"  理由：{reason}")
        lines.append("")
    lines.append("执行后在 Web「每日信号」页记录执行/跳过，形成决策日志")
    return "\n".join(lines)


def _subject(signals: list[SignalModel]) -> str:
    trade_date = signals[0].trade_date if signals else ""
    n_buy = sum(1 for s in signals if s.action == "BUY" and s.risk_passed)
    n_sell = sum(1 for s in signals if s.action == "SELL" and s.risk_passed)
    n_block = sum(1 for s in signals if not s.risk_passed)
    parts = [f"{n} {label}" for n, label in ((n_buy, "买"), (n_sell, "卖"), (n_block, "拦截")) if n]
    return f"nav-sextant 信号 {trade_date}（{' / '.join(parts) or '无操作'}）"


class Notifier:
    def __init__(self, settings: NotificationSettings):
        self.settings = settings

    @property
    def active(self) -> bool:
        s = self.settings
        return s.enabled and bool(
            s.feishu_webhook or s.wecom_webhook or (s.email_to and s.smtp_host))

    def send(self, signals: list[SignalModel], ctx: ActionContext | None = None) -> None:
        if not self.active:
            return
        text = format_signal_text(signals, ctx)
        subject = _subject(signals)
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
                self._send_email(text, subject)
            except Exception as e:  # noqa: BLE001
                logger.warning("邮件推送失败: %s", e)

    def _send_email(self, text: str, subject: str) -> None:
        s = self.settings
        password = os.environ.get(s.smtp_password_env, "")
        if not password:
            raise RuntimeError(f"未设置环境变量 {s.smtp_password_env}")
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
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
