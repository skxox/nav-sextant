"""末端信号守卫测试：抖动抑制（whipsaw）+ 最小交易幅度（min_trade）。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.config import Settings
from backend.db import Database
from backend.engine.guards import min_trade_guard, whipsaw_guard


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "whipsaw.db")


def _days(start: str, n: int) -> list[str]:
    return pd.bdate_range(start, periods=n).strftime("%Y-%m-%d").tolist()


def _seed_signal(db: Database, code: str, day: str, action: str,
                 target: float = 0.3) -> None:
    db.save_signal({
        "trade_date": day, "fund_code": code, "action": action,
        "target_position": target, "composite_score": 0.4,
        "score_technical": 0.1, "score_valuation": 0.1,
        "score_sentiment": 0.1, "score_risk": 0.1,
        "reasons_json": "[]", "risk_checks_json": "[]",
    })


def _seed_nav(db: Database, code: str, days: list[str]) -> None:
    db.upsert_nav([(code, d, 1.0, 1.0) for d in days])


# ---------- 压制 ----------

def test_reverse_within_cooldown_suppressed_to_hold(tmp_path):
    """昨日 BUY、今日 SELL（间隔 1 交易日）→ 压制 HOLD 并维持原仓位。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 5)
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[1], "BUY", target=0.3)
    action, pos, reasons = whipsaw_guard(db, d[2], "110022", "SELL", 0.0, [], 3)
    assert action == "HOLD"
    assert pos == 0.3                     # 维持上一信号目标仓位
    assert any(r.startswith("[whipsaw]") and "1 交易日" in r for r in reasons)


def test_reverse_after_cooldown_allowed(tmp_path):
    """间隔 4 个交易日 > 冷却 3 日 → 放行。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 8)
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[0], "BUY")       # 周一
    # d[4] = 周五：间隔 = 周二三四 + 周五 = 4 交易日
    action, pos, _ = whipsaw_guard(db, d[4], "110022", "SELL", 0.0, [], 3)
    assert action == "SELL" and pos == 0.0


def test_gap_counts_trading_days_via_nav(tmp_path):
    """间隔按净值序列交易日计数：周五 BUY → 次周二 SELL 间隔 2（周一、周二）。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 8)  # 01-01(四) 01-02(五) 01-05(一) 01-06(二)...
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[1], "SELL")      # 周五
    action, _, reasons = whipsaw_guard(db, d[3], "110022", "BUY", 0.3, [], 3)
    assert action == "HOLD"
    assert any("2 交易日" in r for r in reasons)


# ---------- 放行 ----------

def test_same_direction_passes(tmp_path):
    """同向信号（连续 BUY）不构成翻烙饼。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[0], "BUY")
    action, pos, reasons = whipsaw_guard(db, d[1], "110022", "BUY", 0.5, [], 3)
    assert (action, pos, reasons) == ("BUY", 0.5, [])


def test_hold_input_passes_through(tmp_path):
    """HOLD 输入直通（无方向无翻烙饼）。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[0], "BUY")
    assert whipsaw_guard(db, d[1], "110022", "HOLD", 0.3, [], 3) == ("HOLD", 0.3, [])


def test_no_history_passes_through(tmp_path):
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    assert whipsaw_guard(db, d[1], "110022", "SELL", 0.0, [], 3) == ("SELL", 0.0, [])


def test_zero_cooldown_disables_guard(tmp_path):
    """cooldown=0 → 功能关闭，直通。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[0], "BUY")
    assert whipsaw_guard(db, d[1], "110022", "SELL", 0.0, [], 0) == ("SELL", 0.0, [])


def test_stale_reverse_beyond_cooldown_allowed(tmp_path):
    """中间隔了 HOLD 的旧 BUY：只要距其超过冷却期，反向放行。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 10)
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[0], "BUY")       # 上上周 BUY
    _seed_signal(db, "110022", d[3], "HOLD")      # 中间 HOLD 不重置基准
    action, _, _ = whipsaw_guard(db, d[8], "110022", "SELL", 0.0, [], 3)
    assert action == "SELL"


# ---------- 其他 ----------

def test_gap_falls_back_to_calendar_days_without_nav(tmp_path):
    """净值序列缺失时退化为自然日差。"""
    db = _db(tmp_path)
    _seed_signal(db, "110022", "2026-01-05", "BUY")
    # 无 nav 行 → 2026-01-05 → 2026-01-07 自然日差 2 ≤ 3 → 压制
    action, _, _ = whipsaw_guard(db, "2026-01-07", "110022", "SELL", 0.0, [], 3)
    assert action == "HOLD"


def test_gap_counts_today_when_nav_not_yet_published(tmp_path):
    """常态路径：今日净值未入库（T+1 发布），今日仍计 1 个交易日。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 5)
    _seed_nav(db, "110022", d[:4])                 # 净值只到 d[3]，今日 d[4] 未到
    _seed_signal(db, "110022", d[3], "BUY")
    action, _, reasons = whipsaw_guard(db, d[4], "110022", "SELL", 0.0, [], 3)
    assert action == "HOLD"
    assert any("1 交易日" in r for r in reasons)


def test_settings_default_cooldown():
    """默认 3 个交易日冷却，可在设置页调整（0-30）。"""
    s = Settings()
    assert s.whipsaw_cooldown_days == 3


def test_guard_isolated_per_fund(tmp_path):
    """冷却基准按基金隔离：A 基金昨日 BUY 不影响 B 基金今日 SELL。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_nav(db, "161725", d)
    _seed_signal(db, "110022", d[0], "BUY")
    assert whipsaw_guard(db, d[1], "161725", "SELL", 0.0, [], 3) == ("SELL", 0.0, [])


# ---------- 管道集成：guard 在入库前生效 ----------

def test_pipeline_end_to_end_suppresses_whipsaw(tmp_path, app_config):
    """离线全链路：桩 Agent 强制输出 SELL，但距昨日 BUY 1 个交易日内 → 入库为 HOLD。

    无 LLM 配置时消息面走回退路径，analyze_fund 不触网。
    """
    from backend.agents.base import AgentOutput, BaseAgent
    from backend.db import Database
    from backend.engine.pipeline import Pipeline

    class StubAgent(BaseAgent):
        def __init__(self, name, output):
            self.name = name
            self._out = output

        def analyze(self, ctx):
            return self._out

    app_config.llm.debate_enabled = False  # 离线：禁辩论层
    db = Database(app_config.db_path)
    days = _days("2026-06-01", 10)
    db.upsert_nav([("110022", d, 1.0 + i * 1e-4, 1.0 + i * 1e-4) for i, d in enumerate(days)])
    _seed_signal(db, "110022", days[-2], "BUY", target=0.3)  # 昨日 BUY
    _seed_position(db, "110022", 0.0)  # 实际空仓：min_trade 直通（|0.3-0|=0.3），聚焦 whipsaw

    def out(name, score, direction, conf):
        return AgentOutput(agent=name, score=score, direction=direction,
                           confidence=conf, summary="桩输出")

    pipe = Pipeline(app_config, db)
    pipe.agents = [
        StubAgent("technical", out("technical", -0.9, "SELL", 0.9)),
        StubAgent("valuation", out("valuation", 0.0, "HOLD", 0.1)),
        StubAgent("sentiment", out("sentiment", 0.0, "HOLD", 0.1)),
        StubAgent("risk", out("risk", 0.0, "HOLD", 0.1)),
    ]
    sig = pipe.analyze_fund(app_config.funds[0], trade_date=days[-1], news=[])

    assert sig.composite_score < app_config.settings.thresholds.sell  # 原始合成为 SELL
    assert sig.action == "HOLD"                                       # 冷却压制
    assert abs(sig.target_position - 0.3) < 1e-9                      # 维持原仓位
    assert any(r.startswith("[whipsaw]") for r in sig.reasons)
    saved = db.query('SELECT action, target_position FROM signals WHERE trade_date=?', (days[-1],))
    assert saved[0]["action"] == "HOLD"
    assert abs(float(saved[0]["target_position"]) - 0.3) < 1e-9


# ---------- 最小交易幅度 ----------

def _seed_position(db: Database, code: str, position: float) -> None:
    db.execute("INSERT OR REPLACE INTO fund_position (fund_code, position) VALUES (?,?)",
               (code, position))


def test_small_change_suppressed(tmp_path):
    """BUY 28% → 30%（变动 2% < 5%）→ HOLD 并维持实际持仓仓位。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_position(db, "110022", 0.28)
    action, pos, reasons = min_trade_guard(db, d[1], "110022", "BUY", 0.30, [], 0.05)
    assert action == "HOLD"
    assert pos == 0.28
    assert any(r.startswith("[min_trade]") and "2.0%" in r for r in reasons)


def test_large_change_passes(tmp_path):
    """BUY 20% → 30%（变动 10% ≥ 5%）→ 放行。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_position(db, "110022", 0.20)
    action, pos, reasons = min_trade_guard(db, d[1], "110022", "BUY", 0.30, [], 0.05)
    assert (action, pos, reasons) == ("BUY", 0.30, [])


def test_sell_small_reduction_suppressed(tmp_path):
    """SELL 30% → 27%（减仓 3% < 5%）同样压制——赎回费更贵。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_position(db, "110022", 0.30)
    action, pos, _ = min_trade_guard(db, d[1], "110022", "SELL", 0.27, [], 0.05)
    assert action == "HOLD" and pos == 0.30


def test_boundary_equal_to_threshold_passes(tmp_path):
    """变动恰等于阈值（5%）→ 放行（≥ 不含等于之外的压制）。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_position(db, "110022", 0.25)
    action, _, _ = min_trade_guard(db, d[1], "110022", "BUY", 0.30, [], 0.05)
    assert action == "BUY"


def test_zero_threshold_disables(tmp_path):
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_position(db, "110022", 0.30)
    assert min_trade_guard(db, d[1], "110022", "BUY", 0.31, [], 0) == ("BUY", 0.31, [])


def test_hold_passes_through(tmp_path):
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    _seed_position(db, "110022", 0.30)
    assert min_trade_guard(db, d[1], "110022", "HOLD", 0.30, [], 0.05) == ("HOLD", 0.30, [])


def test_falls_back_to_last_signal_position(tmp_path):
    """无实际持仓记录时，用上次信号目标仓位作基准。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 6)
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[0], "BUY", target=0.30)   # 上次信号 30%
    # 今日 HOLD→BUY 31%：变动 1% < 5% → 压制，维持 30%
    action, pos, reasons = min_trade_guard(db, d[3], "110022", "BUY", 0.31, [], 0.05)
    assert action == "HOLD" and pos == 0.30
    assert any("上次信号仓位" in r for r in reasons)


def test_no_basis_passes_through(tmp_path):
    """既无持仓记录也无历史信号 → 无基准，不压制。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _seed_nav(db, "110022", d)
    assert min_trade_guard(db, d[1], "110022", "BUY", 0.02, [], 0.05) == ("BUY", 0.02, [])


def test_position_takes_precedence_over_signal(tmp_path):
    """实际持仓与上次信号仓位并存时，以实际持仓为准。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 6)
    _seed_nav(db, "110022", d)
    _seed_signal(db, "110022", d[0], "BUY", target=0.30)
    _seed_position(db, "110022", 0.10)                     # 实际只买到 10%
    # BUY 目标 12%：对实际持仓变动 2% → 压制；对信号仓位则是放大变动
    action, pos, _ = min_trade_guard(db, d[3], "110022", "BUY", 0.12, [], 0.05)
    assert action == "HOLD" and pos == 0.10


def test_settings_default_min_trade_change():
    assert Settings().min_trade_change == 0.05


# ---------- 管道集成：min_trade 在入库前生效 ----------

def _stub_pipeline(app_config, db):
    from backend.agents.base import AgentOutput, BaseAgent
    from backend.engine.pipeline import Pipeline

    class StubAgent(BaseAgent):
        def __init__(self, name, output):
            self.name = name
            self._out = output

        def analyze(self, ctx):
            return self._out

    def out(name, score, direction, conf):
        return AgentOutput(agent=name, score=score, direction=direction,
                           confidence=conf, summary="桩输出")

    pipe = Pipeline(app_config, db)
    pipe.agents = [
        StubAgent("technical", out("technical", 0.9, "BUY", 0.9)),
        StubAgent("valuation", out("valuation", 0.0, "HOLD", 0.1)),
        StubAgent("sentiment", out("sentiment", 0.0, "HOLD", 0.1)),
        StubAgent("risk", out("risk", 0.0, "HOLD", 0.1)),
    ]
    return pipe


def test_pipeline_suppresses_small_trade(tmp_path, app_config):
    """端到端：BUY 目标 30%，实际持仓 29%（变动 1%）→ 入库 HOLD 维持 29%。"""
    from backend.db import Database

    db = Database(app_config.db_path)
    days = _days("2026-06-01", 10)
    db.upsert_nav([("110022", d, 1.0 + i * 1e-4, 1.0 + i * 1e-4) for i, d in enumerate(days)])
    _seed_position(db, "110022", 0.29)

    pipe = _stub_pipeline(app_config, db)
    sig = pipe.analyze_fund(app_config.funds[0], trade_date=days[-1], news=[])

    assert sig.composite_score >= app_config.settings.thresholds.buy  # 原始为 BUY
    assert sig.action == "HOLD"
    assert abs(sig.target_position - 0.29) < 1e-9
    assert any(r.startswith("[min_trade]") for r in sig.reasons)


def test_risk_veto_not_undone_by_min_trade(tmp_path, app_config):
    """风控否决（现金缓冲拦截）的信号不走 min_trade：target 保持 0 而非弹回仓位。"""
    from backend.db import Database

    db = Database(app_config.db_path)
    days = _days("2026-06-01", 10)
    db.upsert_nav([("110022", d, 1.0 + i * 1e-4, 1.0 + i * 1e-4) for i, d in enumerate(days)])
    _seed_position(db, "110022", 0.95)  # 现金缓冲 0.10 → 0.95 触发拦截

    pipe = _stub_pipeline(app_config, db)
    sig = pipe.analyze_fund(app_config.funds[0], trade_date=days[-1], news=[])

    assert not sig.risk_passed                    # 风控拦截生效
    assert sig.target_position == 0.0             # min_trade 未把仓位弹回 0.95
    assert not any(r.startswith("[min_trade]") for r in sig.reasons)
