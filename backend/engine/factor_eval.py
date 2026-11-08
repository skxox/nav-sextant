"""因子有效性检验（IC 分析）。

量化因子研究标准流程：对每个因子，在历史逐时点计算因子值，与未来 N 日收益率
做秩相关（RankIC / Spearman），并给出显著性检验与分层收益：

- RankIC：因子值与未来收益的 Spearman 秩相关（单资产时序版：滚动窗口内计算）
- ICIR：IC 均值 / IC 标准差（因子稳定性）
- t 检验：H0 = IC 均值为 0；|t| > 2 视为统计显著
- 五分位多空价差：按因子值分组，Q5(高分位) − Q1(低分位) 的平均未来收益差
  （方向：动量因子预期为正；估值分因子已取"越低估分越高"，预期为正）

结论解读：|t| ≥ 2 且方向正确 → 因子有效；0 < |t| < 2 → 方向正确但不显著；
方向相反 → 因子在本标的上失效，应降低对应权重。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from ..agents.base import AnalysisContext  # noqa: F401  （保持与流水线一致的上下文引用）
from ..factors.momentum import compute_momentum
from ..factors.valuation import compute_valuation


@dataclass
class FactorICReport:
    factor: str
    label: str
    samples: int            # 参与检验的 (因子值, 未来收益) 样本对数
    rank_ic: float          # 全样本 Spearman 秩相关
    ic_tstat: float         # IC 显著性 t 值
    ic_mean: float          # 滚动窗口 IC 均值
    ic_std: float
    icir: float
    ic_win_rate: float      # 滚动 IC > 0 比例
    q5_q1_spread_pct: float  # 五分位多空价差（%）
    forward_days: int
    verdict: str            # 显著有效 / 方向正确不显著 / 方向相反 / 样本不足

    def to_dict(self) -> dict:
        return asdict(self)


def _verdict(t: float, ic: float, n: int, min_samples: int = 20) -> str:
    if n < min_samples:
        return "样本不足"
    if abs(t) >= 2.0:
        return "显著有效" if ic > 0 else "显著反向"
    if ic > 0:
        return "方向正确但不显著"
    return "方向相反"


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman 秩相关（秩变换 + Pearson），避免引入 scipy。"""
    if len(x) < 3 or np.std(x) < 1e-9 or np.std(y) < 1e-9:
        return 0.0
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    return float(np.corrcoef(rx, ry)[0, 1])


def _spearman_with_t(factor: np.ndarray, forward: np.ndarray) -> tuple[float, float]:
    rho = _spearman(factor, forward)
    n = len(factor)
    if n < 3 or abs(rho) >= 1.0:
        return rho, 0.0 if abs(rho) < 1.0 else float("inf")
    t = rho / np.sqrt((1 - rho ** 2) / (n - 2))
    return rho, float(t)


def _rolling_ic(factor: pd.Series, forward: pd.Series, window: int = 30) -> pd.Series:
    """滚动窗口 Spearman IC 序列。"""
    ics: list[float] = []
    idx: list = []
    f_arr, r_arr = factor.to_numpy(), forward.to_numpy()
    for i in range(window, len(f_arr) + 1):
        rho = _spearman(f_arr[i - window:i], r_arr[i - window:i])
        if not np.isnan(rho):
            ics.append(float(rho))
            idx.append(i - 1)
    return pd.Series(ics, index=idx) if ics else pd.Series(dtype=float)


def _quantile_spread(factor: np.ndarray, forward: np.ndarray, q: int = 5) -> float:
    """按因子值分 q 组，最高组与最低组的平均未来收益差（%）。"""
    if len(factor) < q * 4:
        return 0.0
    ranks = pd.Series(factor).rank(method="first")
    groups = pd.qcut(ranks, q, labels=False)
    df = pd.DataFrame({"g": groups, "r": forward}).groupby("g")["r"].mean()
    return round(float(df.max() - df.min()) * 100, 3)


def evaluate_factor(factor_by_date: dict[str, float],
                    nav_lookup: pd.Series,
                    label: str, factor_key: str,
                    forward_days: int = 5,
                    step: int = 5) -> FactorICReport:
    """通用因子检验：factor_by_date[t] 因子值，nav_lookup 为净值序列（按日期索引）。"""
    dates = sorted(set(factor_by_date) & set(nav_lookup.index))
    pairs_f, pairs_r = [], []
    for i, d in enumerate(dates):
        # 未来收益：d 之后的第 forward_days 个交易日
        pos = nav_lookup.index.get_loc(d)
        if pos + forward_days >= len(nav_lookup):
            break
        f = factor_by_date[d]
        if f is None or (isinstance(f, float) and np.isnan(f)):
            continue
        r = float(nav_lookup.iloc[pos + forward_days] / nav_lookup.iloc[pos] - 1)
        pairs_f.append(float(f))
        pairs_r.append(r)

    f_arr, r_arr = np.array(pairs_f), np.array(pairs_r)
    if len(f_arr) < 10:
        return FactorICReport(factor=factor_key, label=label, samples=len(f_arr),
                              rank_ic=0.0, ic_tstat=0.0, ic_mean=0.0, ic_std=0.0,
                              icir=0.0, ic_win_rate=0.0, q5_q1_spread_pct=0.0,
                              forward_days=forward_days, verdict="样本不足")

    rho, t = _spearman_with_t(f_arr, r_arr)
    rolling = _rolling_ic(pd.Series(f_arr), pd.Series(r_arr))
    ic_mean = float(rolling.mean()) if len(rolling) else rho
    ic_std = float(rolling.std()) if len(rolling) > 1 else 0.0
    icir = round(ic_mean / ic_std, 3) if ic_std > 1e-9 else 0.0
    win = float((rolling > 0).mean()) if len(rolling) else 0.0

    return FactorICReport(
        factor=factor_key, label=label, samples=len(f_arr),
        rank_ic=round(rho, 4), ic_tstat=round(t, 3),
        ic_mean=round(ic_mean, 4), ic_std=round(ic_std, 4), icir=icir,
        ic_win_rate=round(win, 3),
        q5_q1_spread_pct=_quantile_spread(f_arr, r_arr),
        forward_days=forward_days,
        verdict=_verdict(t, rho, len(f_arr)),
    )


def evaluate_all_factors(nav_history: pd.DataFrame,
                         index_valuation: pd.DataFrame,
                         momentum_windows: tuple = (5, 20, 60),
                         momentum_weights: tuple = (0.2, 0.3, 0.5),
                         valuation_window: int = 750,
                         forward_days: int = 5) -> list[FactorICReport]:
    """对动量、估值（负分位方向）、波动率（反向）三个因子族做历史 IC 检验。"""
    reports: list[FactorICReport] = []
    if nav_history is None or len(nav_history) < 90:
        return reports

    nav = nav_history.dropna().sort_values("trade_date")
    nav_series = nav.set_index("trade_date")["nav"].astype(float)

    # ---- 动量因子：逐时点计算（复用生产同款因子函数，保证检验即上线行为） ----
    momentum_factor: dict[str, float] = {}
    risk_factor: dict[str, float] = {}
    nav_arr = nav["nav"].astype(float).to_numpy()
    dates_arr = nav["trade_date"].to_numpy()
    min_len = max(max(momentum_windows) + 1, 61)
    for i in range(min_len, len(nav_arr), 5):
        sub = pd.DataFrame({"trade_date": dates_arr[: i + 1], "nav": nav_arr[: i + 1]})
        mf = compute_momentum(sub, momentum_windows, momentum_weights)
        momentum_factor[dates_arr[i]] = mf.score
        rets = pd.Series(nav_arr[: i + 1]).pct_change().dropna()
        vol = float(rets.tail(60).std() * np.sqrt(252)) if len(rets) >= 40 else None
        if vol is not None:
            risk_factor[dates_arr[i]] = -vol  # 波动率反向：低波动 → 高分

    reports.append(evaluate_factor(momentum_factor, nav_series,
                                   "技术面·波动调整动量", "momentum", forward_days))
    reports.append(evaluate_factor(risk_factor, nav_series,
                                   "风险面·低波动偏好", "risk_lowvol", forward_days))

    # ---- 估值因子：用基准/行业指数 PE 分位 vs 指数未来收益 ----
    if index_valuation is not None and not index_valuation.empty:
        pe_df = index_valuation.dropna(subset=["pe"]).sort_values("trade_date")
        if len(pe_df) < 120 and "close" in index_valuation.columns:
            # 行业指数 PE 历史不足（中证官网仅近月）→ 价格分位代理（与生产 compute_valuation 一致）
            close_df = index_valuation.dropna(subset=["close"]).sort_values("trade_date").tail(760)
            if len(close_df) >= 240:
                close_arr = close_df["close"].astype(float).to_numpy()
                close_dates = close_df["trade_date"].to_numpy()
                val_factor: dict[str, float] = {}
                w = min(750, len(close_arr) - 1)
                for i in range(60, len(close_arr), 5):
                    hist = close_arr[max(0, i - w): i + 1]
                    pct = float((hist < close_arr[i]).mean() * 100)
                    val_factor[close_dates[i]] = (50 - pct) / 50  # 与估值分同向：低价格分位 → 高分
                idx_series = close_df.set_index("trade_date")["close"].astype(float)
                reports.append(evaluate_factor(val_factor, idx_series,
                                               "估值面·价格分位代理", "valuation_proxy", forward_days))
        elif len(pe_df) >= 240:
            pe_arr = pe_df["pe"].astype(float).to_numpy()
            pe_dates = pe_df["trade_date"].to_numpy()
            val_factor = {}
            for i in range(240, len(pe_arr), 5):
                sub = pd.DataFrame({"trade_date": pe_dates[: i + 1], "pe": pe_arr[: i + 1],
                                    "pb": np.where(np.isnan(pe_arr[: i + 1]), np.nan, np.nan)})
                vf = compute_valuation(sub, window=valuation_window)
                val_factor[pe_dates[i]] = vf.score
            # 指数未来收益代理（用 PE 序列自身的 close 不一定有 → 用同表 close 若存在）
            if "close" in pe_df.columns and pe_df["close"].notna().any():
                idx_series = pe_df.set_index("trade_date")["close"].astype(float).dropna()
                reports.append(evaluate_factor(val_factor, idx_series,
                                               "估值面·综合估值分", "valuation", forward_days))
    return reports
