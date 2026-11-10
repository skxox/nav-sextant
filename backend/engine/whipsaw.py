"""信号抖动抑制（whipsaw guard）：跨日翻烙饼防护。

规则（冷却期模式，ROADMAP A 组）：反向信号在上次反向信号的 N 个交易日内
被压制为 HOLD，并维持上一信号的目标仓位——阈值附近来回穿越是合成信号的
固有噪声，冷却期以牺牲反应速度换申赎磨损下降。

考察印证：llm-quant 亦未解决，实盘正被 whipsaw 拖累（回测 Sharpe 2.2 vs
实盘落后基准 6%）；场外基金 T+1 申赎费率高的场景下磨损更重。

不受影响的情形：同向信号、HOLD、距上次反向信号超过 N 个交易日、
该基金无历史非 HOLD 信号、cooldown_days = 0（功能关闭）。

间隔 = 上次信号日与今日之间的交易日数（不含上次信号日、含今日）：按该
基金净值序列计数；今日净值尚未入库（T+1 发布的常态）时补 1；该基金在
上次信号日后完全没有净值数据时退化为自然日数。
"""
from __future__ import annotations

from datetime import date

from ..db import Database


def whipsaw_guard(db: Database, trade_date: str, fund_code: str,
                  action: str, target_position: float, reasons: list[str],
                  cooldown_days: int) -> tuple[str, float, list[str]]:
    """末端信号整形：冷却期内反向 → HOLD + 维持原仓位。

    返回 (action, target_position, reasons)；reasons 追加 [whipsaw] 标记
    供信号历史与告警追溯。
    """
    if cooldown_days <= 0 or action not in ("BUY", "SELL"):
        return action, target_position, reasons

    last = db.query(
        """SELECT trade_date, action, target_position FROM signals
           WHERE fund_code=? AND action != 'HOLD' AND trade_date < ?
           ORDER BY trade_date DESC LIMIT 1""",
        (fund_code, trade_date))
    if not last:
        return action, target_position, reasons
    last = last[0]
    if last["action"] == action:  # 同向信号不构成翻烙饼
        return action, target_position, reasons

    # 交易日间隔 = (上次信号日, 今日] 内的净值天数；今日净值未入库时补 1
    row = db.query(
        """SELECT COUNT(*) AS n, MAX(trade_date) AS mx FROM fund_nav
           WHERE fund_code=? AND trade_date > ? AND trade_date <= ?""",
        (fund_code, last["trade_date"], trade_date))[0]
    if row["mx"] == trade_date:
        gap = row["n"]           # 今日净值已入库（如重跑历史日）
    elif row["mx"] is not None:
        gap = row["n"] + 1       # 今日净值未到（T+1 发布常态）：今日仍计 1 日
    else:  # 上次信号日后无任何净值数据：退化为自然日差
        gap = (date.fromisoformat(trade_date) - date.fromisoformat(last["trade_date"])).days

    if gap > cooldown_days:
        return action, target_position, reasons

    prev_pos = float(last["target_position"] or 0.0)
    reason = (f"[whipsaw] 反向信号冷却中：距上次 {last['action']}"
              f"（{last['trade_date']}，{gap} 交易日）≤ {cooldown_days} 日，"
              f"维持仓位 {prev_pos:.0%}")
    return "HOLD", prev_pos, list(reasons) + [reason]
