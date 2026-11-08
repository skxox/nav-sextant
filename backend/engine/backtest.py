"""向量回测引擎：阈值策略在历史上的可验证性。

设计要点：
- 信号在 T 日收盘后生成，作用于 T+1 日收益，避免前视偏差
- 申赎成本按仓位变动比例计提
- 消息面 Agent 无历史数据，回测时剔除 sentiment 并将其权重重新归一
- 输出指标：累计/年化收益、最大回撤、夏普、日胜率、交易次数
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import AppConfig, FundSpec
from ..db import Database


@dataclass
class BacktestResult:
    fund_code: str
    fund_name: str
    start_date: str
    end_date: str
    trading_days: int
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    trade_count: int
    curve: list[dict] = field(default_factory=list)  # trade_date, nav, benchmark

    def to_dict(self) -> dict:
        return {
            "fund_code": self.fund_code, "fund_name": self.fund_name,
            "start_date": self.start_date, "end_date": self.end_date,
            "trading_days": self.trading_days,
            "total_return_pct": round(self.total_return_pct, 2),
            "annualized_return_pct": round(self.annualized_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "win_rate_pct": round(self.win_rate_pct, 1),
            "trade_count": self.trade_count,
            "strategy_curve": self.curve,
        }


def _momentum_series(nav: pd.Series, windows: list[int], weights: list[float]) -> pd.Series:
    """多窗口动量加权，tanh 归一化到 -1~1。与实盘因子保持同一定义。"""
    w = np.array(weights, dtype=float)
    w = w / w.sum()
    composite = pd.Series(0.0, index=nav.index)
    for window, weight in zip(windows, w):
        ret = nav / nav.shift(window) - 1
        composite = composite + weight * ret
    return np.tanh(composite * 100 / 5.0)


def _valuation_series(pe: pd.Series, window: int) -> pd.Series:
    """PE 历史分位 → -1~1（分位越低分越高）。"""
    pct = pe.rolling(window, min_periods=max(60, window // 5)).apply(
        lambda x: (x < x.iloc[-1]).mean() * 100, raw=False)
    return (50 - pct) / 50


def _risk_score_series(nav: pd.Series) -> pd.Series:
    """与 RiskAgent 同构的向量化风险分：波动率 + 回撤 + 急跌惩罚。"""
    ret = nav.pct_change()
    vol = ret.rolling(60, min_periods=20).std() * np.sqrt(252)
    vol_score = ((0.35 - vol) / 0.25).clip(-1, 1)

    window = 120
    peak = nav.rolling(window, min_periods=20).max()
    max_dd = ((nav - peak) / peak).clip(-1, 0)
    dd_score = (-max_dd / 0.30).clip(-1, 1) * 0.5

    recent = nav / nav.shift(5) - 1
    crash = (recent / -0.10).clip(0, 1) * -0.5

    score = vol_score * 0.4 + dd_score * 0.3 + crash * 0.3
    return score.clip(-1, 1)


def run_backtest(db: Database, fund: FundSpec, config: AppConfig,
                 max_days: int = 750) -> BacktestResult:
    s = config.settings
    bt = config.backtest

    nav_rows = db.get_nav_history(fund.code, limit=max_days + max(s.momentum_windows) + 10)
    if len(nav_rows) < max(s.momentum_windows) + 30:
        raise ValueError(f"基金 {fund.code} 净值历史不足（{len(nav_rows)} 条），无法回测")
    df = pd.DataFrame([{"trade_date": r["trade_date"], "nav": r["nav"]} for r in nav_rows])
    df = df.sort_values("trade_date").dropna().set_index("trade_date")
    nav = df["nav"].astype(float)

    # 因子合成（剔除 sentiment 后重新归一）
    weights = {k: v for k, v in s.weights.model_dump().items() if k != "sentiment"}
    total_w = sum(weights.values())
    mom = _momentum_series(nav, s.momentum_windows, s.momentum_windows_weights)

    val_rows = db.query(
        "SELECT trade_date, pe FROM index_daily WHERE index_code=? AND pe IS NOT NULL ORDER BY trade_date",
        (fund.benchmark_index,))
    if val_rows:
        pe = pd.DataFrame([dict(r) for r in val_rows]).set_index("trade_date")["pe"].astype(float)
        val = _valuation_series(pe, s.valuation_percentile_window).reindex(nav.index).ffill()
    else:
        val = pd.Series(0.0, index=nav.index)

    risk = _risk_score_series(nav)

    score = (weights["technical"] / total_w * mom
             + weights["valuation"] / total_w * val
             + weights["risk"] / total_w * risk)

    # 仓位：信号作用于 T+1，仓位变化按成本计提
    target = pd.Series(0.0, index=nav.index)
    target[score >= s.thresholds.buy] = (s.max_single_fund_position * score * 2).clip(upper=s.max_single_fund_position)
    target[score <= s.thresholds.sell] = 0.0
    # HOLD 保持原仓位：分段构造
    hold_mask = (score < s.thresholds.buy) & (score > s.thresholds.sell)
    held = target.copy()
    held[hold_mask] = np.nan
    held = held.ffill().fillna(0.0)

    daily_ret = nav.pct_change().fillna(0.0)
    strategy_ret = held.shift(1).fillna(0.0) * daily_ret  # T 日信号作用于 T+1 收益
    turnover = held.diff().abs().fillna(0.0)
    cost = turnover * np.where(held.diff().fillna(0.0) > 0, bt.buy_cost, bt.sell_cost)
    net_ret = strategy_ret - cost.shift(1).fillna(0.0)

    curve_nav = (1 + net_ret).cumprod()
    benchmark_nav = nav / nav.iloc[0]

    # 指标
    total_ret = float(curve_nav.iloc[-1] - 1)
    n_days = len(curve_nav)
    annualized = float((1 + total_ret) ** (252 / max(n_days, 1)) - 1)
    peak = curve_nav.cummax()
    max_dd = float(((curve_nav - peak) / peak).min())
    daily_std = float(net_ret.std())
    sharpe = float((net_ret.mean() - bt.risk_free_rate / 252) / daily_std * np.sqrt(252)) if daily_std > 0 else 0.0
    active = held.shift(1).fillna(0.0) > 0
    win_rate = float((net_ret[active] > 0).mean() * 100) if active.any() else 0.0
    trade_count = int((turnover > 1e-6).sum())

    return BacktestResult(
        fund_code=fund.code, fund_name=fund.name,
        start_date=str(df.index[0]), end_date=str(df.index[-1]),
        trading_days=n_days,
        total_return_pct=total_ret * 100,
        annualized_return_pct=annualized * 100,
        max_drawdown_pct=max_dd * 100,
        sharpe_ratio=sharpe,
        win_rate_pct=win_rate,
        trade_count=trade_count,
        curve=[{"trade_date": str(d), "nav": round(float(cv), 4), "benchmark": round(float(bv), 4)}
               for d, cv, bv in zip(curve_nav.index, curve_nav, benchmark_nav)],
    )
