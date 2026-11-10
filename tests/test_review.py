"""信号复盘测试：入场时点口径、方向化收益、衰减曲线与有效期判定。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.db import Database
from backend.engine.review import review_signals


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "review.db")


def _nav(code: str, dates: list[str], values: list[float], db: Database) -> None:
    db.upsert_nav([(code, d, v, v) for d, v in zip(dates, values)])


def _signal(db: Database, code: str, day: str, action: str, score: float = 0.5) -> None:
    db.save_signal({
        "trade_date": day, "fund_code": code, "action": action,
        "target_position": 0.5, "composite_score": score,
        "score_technical": 0.1, "score_valuation": 0.1,
        "score_sentiment": 0.1, "score_risk": 0.1,
        "reasons_json": "[]", "risk_checks_json": "[]",
    })


def _days(start: str, n: int) -> list[str]:
    return pd.bdate_range(start, periods=n).strftime("%Y-%m-%d").tolist()


# ---------- 入场时点与前向收益口径 ----------

def test_buy_forward_return_uses_next_day_entry(tmp_path):
    """T 日信号 → T+1 净值入场，N=1 收益 = nav[T+2]/nav[T+1] - 1。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _nav("110022", d, [1.0, 2.0, 4.0, 4.0], db)
    _signal(db, "110022", d[0], "BUY")
    r = review_signals(db, horizons=(1, 3))
    ev = r.details[0]
    assert ev.entry_date == d[1]              # 次一交易日入场
    assert ev.returns_pct[1] == 100.0         # 4.0/2.0 - 1
    assert ev.returns_pct[3] is None          # 只有 4 条净值，entry+3 越界 → pending


def test_sell_direction_inverts_return(tmp_path):
    db = _db(tmp_path)
    d = _days("2026-01-01", 4)
    _nav("110022", d, [1.0, 2.0, 4.0, 4.0], db)
    _signal(db, "110022", d[0], "SELL")
    r = review_signals(db, horizons=(1,))
    assert r.details[0].returns_pct[1] == -100.0  # 下跌对 SELL 是正向贡献


def test_latest_signal_is_pending_not_evaluated(tmp_path):
    """信号日晚于最后一条净值（当日收盘刚出的信号）→ pending，不进统计。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 3)
    _nav("110022", d, [1.0, 1.0, 1.0], db)
    _signal(db, "110022", d[-1], "BUY")
    r = review_signals(db, horizons=(1,))
    assert r.pending == 1
    assert r.evaluated == 0
    assert r.details == []


def test_hold_counted_but_not_evaluated(tmp_path):
    db = _db(tmp_path)
    d = _days("2026-01-01", 5)
    _nav("110022", d, [1.0] * 5, db)
    _signal(db, "110022", d[0], "HOLD")
    _signal(db, "110022", d[1], "BUY")
    r = review_signals(db, horizons=(1,))
    assert r.total_signals == 2
    assert r.actionable == 1
    assert r.evaluated == 1


# ---------- 聚合统计 ----------

def test_win_rate_and_avg_over_multiple_signals(tmp_path):
    """3 条 BUY：N=1 方向化收益 +10% / -5% / +3% → 胜率 2/3。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 9)
    # 三个独立信号块：信号日、入场日、N=1 出场日
    nav = [1.0, 1.00, 1.10,   # 信号1: entry 1.00 → 1.10 = +10%
           1.0, 1.00, 0.95,   # 信号2: -5%
           1.0, 1.00, 1.03]   # 信号3: +3%
    _nav("110022", d, nav, db)
    for i in (0, 3, 6):
        _signal(db, "110022", d[i], "BUY")
    r = review_signals(db, horizons=(1,))
    h = r.decay_curve[0]
    assert h.samples == 3
    assert abs(h.win_rate - 2 / 3) < 1e-9
    assert abs(h.avg_return_pct - (10 - 5 + 3) / 3) < 1e-6


def test_by_action_groups_buy_and_sell(tmp_path):
    db = _db(tmp_path)
    d = _days("2026-01-01", 6)
    _nav("110022", d, [1.0, 1.0, 1.10, 1.0, 1.0, 0.90], db)
    _signal(db, "110022", d[0], "BUY")    # +10%
    _signal(db, "110022", d[3], "SELL")   # 跌 10% → SELL 方向化 +10%
    r = review_signals(db, horizons=(1,))
    assert set(r.by_action) == {"BUY", "SELL"}
    assert r.by_action["BUY"][0].avg_return_pct == pytest.approx(10.0)
    assert r.by_action["SELL"][0].avg_return_pct == pytest.approx(10.0)


def test_by_fund_breakdown_and_filter(tmp_path):
    db = _db(tmp_path)
    d = _days("2026-01-01", 6)
    _nav("110022", d, [1.0, 1.0, 1.10, 1.0, 1.0, 1.0], db)
    _nav("161725", d, [1.0, 1.0, 1.0, 1.0, 1.0, 0.90], db)
    _signal(db, "110022", d[0], "BUY")
    _signal(db, "161725", d[3], "SELL")
    r_all = review_signals(db, horizons=(1,), fund_names={"110022": "消费", "161725": "白酒"})
    assert {f["fund_code"] for f in r_all.by_fund} == {"110022", "161725"}
    assert next(f for f in r_all.by_fund if f["fund_code"] == "161725")["fund_name"] == "白酒"

    r_one = review_signals(db, fund_code="110022", horizons=(1,))
    assert r_one.total_signals == 1
    assert len(r_one.by_fund) == 1 and r_one.by_fund[0]["fund_code"] == "110022"


def test_fund_without_nav_rows_pending(tmp_path):
    """有信号但净值表空（数据缺失）→ pending，不抛异常。"""
    db = _db(tmp_path)
    _signal(db, "110022", "2026-01-05", "BUY")
    r = review_signals(db, horizons=(1,))
    assert r.pending == 1 and r.evaluated == 0


# ---------- 衰减曲线与有效期 ----------

def test_best_horizon_picks_peak_of_decay_curve(tmp_path):
    """8 条同构信号：N=1 +1%、N=3 +9%、N=5 +4% → 有效期 = 3 日。"""
    db = _db(tmp_path)
    n_blocks = 8
    d = _days("2026-01-01", n_blocks * 8)
    block = [1.0,   # 信号日
             1.0,   # 入场
             1.01,  # N=1 → +1%
             1.03,
             1.09,  # N=3 → +9%
             1.09,
             1.04,  # N=5 → +4%
             1.04]  # 缓冲（下一块的信号日）
    _nav("110022", d, block * n_blocks, db)
    for i in range(n_blocks):
        _signal(db, "110022", d[i * 8], "BUY")
    r = review_signals(db, horizons=(1, 3, 5))
    assert [h.samples for h in r.decay_curve] == [8, 8, 8]
    assert [h.avg_return_pct for h in r.decay_curve] == pytest.approx([1.0, 9.0, 4.0])
    assert r.best_horizon == 3
    assert "有效期约 3 日" in r.verdict


def test_no_positive_edge_means_no_best_horizon(tmp_path):
    """全部方向化收益为负 → best_horizon=None，结论提示无信息量。"""
    db = _db(tmp_path)
    n = 10
    d = _days("2026-01-01", n * 3)
    _nav("110022", d, [1.0, 1.0, 0.9] * n, db)  # 每块 -10%
    for i in range(n):
        _signal(db, "110022", d[i * 3], "BUY")
    r = review_signals(db, horizons=(1,))
    assert r.best_horizon is None
    assert "无信息量" in r.verdict


def test_insufficient_samples_verdict(tmp_path):
    db = _db(tmp_path)
    d = _days("2026-01-01", 3)
    _nav("110022", d, [1.0, 1.0, 1.1], db)
    _signal(db, "110022", d[0], "BUY")
    r = review_signals(db, horizons=(1,))
    assert r.best_horizon is None
    assert "样本不足" in r.verdict


# ---------- 边界 ----------

def test_empty_db_returns_clean_report(tmp_path):
    r = review_signals(_db(tmp_path), horizons=(1, 5))
    assert r.total_signals == 0
    assert r.decay_curve[0].samples == 0
    assert r.by_action == {} and r.by_fund == []
    assert "样本不足" in r.verdict


def test_t_stat_none_for_few_samples(tmp_path):
    """n < 3 时不算 t 值（自由度不足）。"""
    db = _db(tmp_path)
    d = _days("2026-01-01", 6)
    _nav("110022", d, [1.0, 1.0, 1.1, 1.0, 1.0, 1.2], db)
    _signal(db, "110022", d[0], "BUY")
    _signal(db, "110022", d[3], "BUY")
    r = review_signals(db, horizons=(1,))
    assert r.decay_curve[0].samples == 2
    assert r.decay_curve[0].t_stat is None
