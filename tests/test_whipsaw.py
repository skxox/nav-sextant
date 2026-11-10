"""信号抖动抑制测试：冷却期内反向压制、间隔口径与各类放行情形。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.config import Settings
from backend.db import Database
from backend.engine.whipsaw import whipsaw_guard


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

    db = Database(app_config.db_path)
    days = _days("2026-06-01", 10)
    db.upsert_nav([("110022", d, 1.0 + i * 1e-4, 1.0 + i * 1e-4) for i, d in enumerate(days)])
    _seed_signal(db, "110022", days[-2], "BUY", target=0.3)  # 昨日 BUY

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
