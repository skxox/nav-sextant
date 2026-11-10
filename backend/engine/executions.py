"""信号执行记录（决策日志，ROADMAP A 组）。

「自动决策、人工执行」——信号发出后由人决定是否下单。本模块记录人对
每条可执行信号的实际处置与成交要素，形成决策日志：

- 成交净值不在下单时写死：查询时按 exec_date 从 fund_nav 回查。场外
  基金 T 日下单按 T 日净值成交，而净值 T+1 晚间才公布——记录时成交价
  必然未知；净值入库后记录自动转「已确认」，期间为「待确认」。
- SKIP = 决定不执行：跳过也是决策，同样入册（note 记原因）。
- signal_date 为空的记录 = 非信号驱动的人工操作（自主加/减仓）。
- 不回写 fund_position：份额/成本精确核算属「完整账户记账」项，本模块
  只做日志；shares = amount/nav 为不含手续费的近似值。
"""
from __future__ import annotations

from ..db import Database

EXEC_ACTIONS = ("BUY", "SELL", "SKIP")


class ExecutionError(ValueError):
    """记录不合法（动作、日期、关联信号等），API 层转 400。"""


def record_execution(db: Database, fund_code: str, exec_date: str, action: str,
                     signal_date: str | None = None, amount: float | None = None,
                     nav: float | None = None, note: str = "") -> dict:
    """写入一条执行记录；同一 (fund_code, signal_date) 再次记录为改写。

    校验：action ∈ BUY/SELL/SKIP；日期为合法 ISO 格式；SKIP 必须关联信号
    且不带金额；关联的 signal_date 必须真实存在；金额 > 0。
    """
    from datetime import date as _date

    if action not in EXEC_ACTIONS:
        raise ExecutionError(f"action 必须是 {'/'.join(EXEC_ACTIONS)}，收到 {action!r}")
    for label, d in (("exec_date", exec_date), ("signal_date", signal_date)):
        if d is not None:
            try:
                _date.fromisoformat(d)
            except ValueError as e:
                raise ExecutionError(f"{label} 不是合法日期（YYYY-MM-DD）：{d!r}") from e
    if action == "SKIP":
        if not signal_date:
            raise ExecutionError("跳过记录必须关联某条信号（signal_date）")
        amount = nav = None  # 未执行何来金额与净值
    if amount is not None and amount <= 0:
        raise ExecutionError("金额必须大于 0")
    if nav is not None and nav <= 0:
        raise ExecutionError("成交净值必须大于 0")

    if signal_date:
        hit = db.query("SELECT 1 FROM signals WHERE fund_code=? AND trade_date=?",
                       (fund_code, signal_date))
        if not hit:
            raise ExecutionError(f"基金 {fund_code} 在 {signal_date} 无信号记录，无法关联")

    cur = db.execute(
        """INSERT OR REPLACE INTO executions
           (fund_code, signal_date, exec_date, action, amount, nav, note)
           VALUES (?,?,?,?,?,?,?)""",
        (fund_code, signal_date, exec_date, action, amount, nav, note))
    row = db.query(
        """SELECT e.*, s.action AS signal_action, s.target_position AS signal_target,
                  s.composite_score AS signal_score
           FROM executions e LEFT JOIN signals s
             ON s.fund_code = e.fund_code AND s.trade_date = e.signal_date
           WHERE e.id=?""",
        (cur.lastrowid,))[0]
    return _resolve(db, row)


def list_executions(db: Database, fund_code: str | None = None, limit: int = 100,
                    fund_names: dict[str, str] | None = None) -> list[dict]:
    """执行记录倒序列表，逐条解析净值/份额/状态。"""
    sql = """SELECT e.*, s.action AS signal_action, s.target_position AS signal_target,
                    s.composite_score AS signal_score
             FROM executions e LEFT JOIN signals s
               ON s.fund_code = e.fund_code AND s.trade_date = e.signal_date"""
    params: list = []
    if fund_code:
        sql += " WHERE e.fund_code=?"
        params.append(fund_code)
    sql += " ORDER BY e.exec_date DESC, e.id DESC LIMIT ?"
    params.append(min(max(limit, 1), 500))
    names = fund_names or {}
    return [_resolve(db, r, names.get(r["fund_code"], r["fund_code"])) for r in db.query(sql, params)]


def execution_stats(db: Database) -> dict:
    """决策执行统计：可执行信号（非 HOLD）中已执行/跳过/未处理的计数与执行率。"""
    rows = db.query(
        """SELECT e.action AS exec_action FROM signals s
           LEFT JOIN executions e ON e.fund_code = s.fund_code AND e.signal_date = s.trade_date
           WHERE s.action != 'HOLD'""")
    actionable = len(rows)
    executed = sum(1 for r in rows if r["exec_action"] in ("BUY", "SELL"))
    skipped = sum(1 for r in rows if r["exec_action"] == "SKIP")
    return {
        "actionable": actionable,
        "executed": executed,
        "skipped": skipped,
        "unhandled": actionable - executed - skipped,
        "exec_rate": round(executed / actionable, 4) if actionable else None,
    }


def _resolve(db: Database, row, fund_name: str | None = None) -> dict:
    """补齐净值来源、份额与状态：nav 列优先（人工确认），否则查 exec_date 官方净值。"""
    out = dict(row)
    if fund_name is not None:
        out["fund_name"] = fund_name
    action = out["action"]
    if action == "SKIP":
        out.update(nav_source=None, shares=None, status="skipped")
        return out
    nav, source = out["nav"], "manual"
    if nav is None:
        hit = db.query("SELECT nav FROM fund_nav WHERE fund_code=? AND trade_date=?",
                       (out["fund_code"], out["exec_date"]))
        if hit and hit[0]["nav"] is not None:
            nav, source = float(hit[0]["nav"]), "nav"
    out["nav"] = float(nav) if nav is not None else None
    out["nav_source"] = source if nav is not None else None
    amount = out["amount"]
    out["shares"] = round(amount / nav, 2) if (amount is not None and nav) else None
    out["status"] = "confirmed" if nav is not None else "pending"
    return out
