"""IC 调权 A/B 对照验证（ROADMAP A 组：支撑默认开关决策）。

思路：离线走查重放（walk-forward replay）——技术/估值/风险三路确定性
Agent 只依赖库内净值与指数序列，可对任意历史日复算（生产同款因子函数与
Agent），消息面需 LLM 不可离线复现，从两臂中同时剔除（公平对比）。

对每个信号日 T：
- 用 ≤T 的数据重算三路 Agent 输出（与当日实盘完全同构，无未来数据）
- IC 调权臂：用 ≤T 数据算因子 IC 检验 → ic_weight_adjustments → synthesize
- 对照臂：同一组 Agent 输出、同一合成配置，仅不传 ic_adjustments
- 两臂信号分别按复盘统一口径评估（T 信号 → T+1 净值入场 → N 日方向化收益）

结论解读：Δ = IC 臂均值 − 对照臂均值，|Δ| < 0.3pp 视为无显著差异；
样本 < MIN_SAMPLES 不下结论（宁缺毋滥，与复盘模块同口径）。

性能：IC 检验每 ic_step 个信号日重算一次（月度刷新，贴近实际运营节奏），
其余信号日沿用上次调整——IC 调权本就不是日频重估的机制。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from ..agents.base import AnalysisContext
from ..agents.risk_agent import RiskAgent
from ..agents.technical import TechnicalAgent
from ..agents.valuation_agent import ValuationAgent
from ..config import FundSpec, Settings
from ..db import Database
from ..factors.momentum import compute_momentum
from ..factors.valuation import compute_valuation
from .factor_eval import evaluate_all_factors, ic_weight_adjustments
from .orchestrator import synthesize
from .review import MIN_SAMPLES, _forward_returns, _load_navs, _stats

DETERMINISTIC_AGENTS = (TechnicalAgent(), ValuationAgent(), RiskAgent())
VERDICT_EPS = 0.3   # pp，两臂均值差小于此视为无显著差异
POOLED_MIN = 16     # 合并结论门槛：逐基金 MIN_SAMPLES，合并翻倍（单基金样本太薄）


@dataclass
class ArmStat:
    """单臂（开/关 IC）的信号质量统计。"""
    label: str                    # 开启 / 关闭
    actionable: int               # BUY+SELL 信号数
    evaluated: int                # 有前向收益的可评估数
    win_rate: float | None
    avg_return_pct: float | None
    t_stat: float | None

    def to_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) and v is not None else v)
                for k, v in asdict(self).items()}


@dataclass
class FundABResult:
    fund_code: str
    fund_name: str
    signal_days: int              # 参与重放的信号日数
    agreement_rate: float | None  # 两臂同日同动作比例
    churn_off: int                # 对照臂相邻信号日动作变化次数
    churn_on: int                 # IC 臂相邻信号日动作变化次数
    off: ArmStat | None
    on: ArmStat | None
    delta_avg_pct: float | None   # IC 臂 − 对照臂（方向化平均收益）
    ic_active_days: int           # IC 臂实际带调整的信号日数（其余=与对照相同）
    verdict: str

    def to_dict(self) -> dict:
        return {
            "fund_code": self.fund_code, "fund_name": self.fund_name,
            "signal_days": self.signal_days,
            "agreement_rate": round(self.agreement_rate, 3) if self.agreement_rate is not None else None,
            "churn_off": self.churn_off, "churn_on": self.churn_on,
            "off": self.off.to_dict() if self.off else None,
            "on": self.on.to_dict() if self.on else None,
            "delta_avg_pct": round(self.delta_avg_pct, 2) if self.delta_avg_pct is not None else None,
            "ic_active_days": self.ic_active_days,
            "verdict": self.verdict,
        }


@dataclass
class ICABReport:
    horizon: int
    horizon_source: str           # auto / manual
    step: int
    funds: list[FundABResult] = field(default_factory=list)
    pooled_off: ArmStat | None = None
    pooled_on: ArmStat | None = None
    verdict: str = ""

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon, "horizon_source": self.horizon_source,
            "step": self.step,
            "funds": [f.to_dict() for f in self.funds],
            "pooled_off": self.pooled_off.to_dict() if self.pooled_off else None,
            "pooled_on": self.pooled_on.to_dict() if self.pooled_on else None,
            "verdict": self.verdict,
        }


def _arm_stats(label: str, signals: list[tuple[str, str]],
               navs: list[tuple[str, float]], horizon: int) -> ArmStat:
    """一臂信号 → 统计（复盘统一口径）。"""
    rets = []
    for date, action in signals:
        if action not in ("BUY", "SELL"):
            continue
        _, fwd = _forward_returns(navs, date, action, (horizon,))
        if fwd[horizon] is not None:
            rets.append(fwd[horizon] / 100)
    stat = _stats(rets)
    return ArmStat(
        label=label,
        actionable=sum(1 for _, a in signals if a in ("BUY", "SELL")),
        evaluated=stat.samples if stat else 0,
        win_rate=stat.win_rate if stat else None,
        avg_return_pct=stat.avg_return_pct if stat else None,
        t_stat=stat.t_stat if stat else None,
    )


def _churn(signals: list[tuple[str, str]]) -> int:
    """相邻信号日动作变化次数（换手噪声代理）。"""
    acts = [a for _, a in signals]
    return sum(1 for a, b in zip(acts, acts[1:]) if a != b)


def _verdict(off: ArmStat | None, on: ArmStat | None,
             min_samples: int = MIN_SAMPLES) -> tuple[str, float | None]:
    if not off or not on or min(off.evaluated, on.evaluated) < min_samples:
        return (f"样本不足（两臂各需 ≥{min_samples} 条可评估信号，"
                f"当前 对照{off.evaluated if off else 0}/IC{on.evaluated if on else 0} 条）"), None
    delta = on.avg_return_pct - off.avg_return_pct
    if abs(delta) < VERDICT_EPS:
        return (f"无显著差异（Δ={delta:+.2f}pp < {VERDICT_EPS}pp），维持现状即可"), delta
    better = "IC 调权更优" if delta > 0 else "关闭更优"
    return (f"{better}：IC 臂方向化均值 {on.avg_return_pct:+.2f}% vs "
            f"对照 {off.avg_return_pct:+.2f}%（Δ={delta:+.2f}pp）"), delta


def _index_valuation(db: Database, index_code: str) -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_date": r["trade_date"], "pe": r["pe"], "pb": r["pb"], "close": r["close"],
         "pe_percentile": r["pe_percentile"], "pb_percentile": r["pb_percentile"]}
        for r in db.query(
            "SELECT trade_date, pe, pb, close, pe_percentile, pb_percentile FROM index_daily "
            "WHERE index_code=? ORDER BY trade_date", (index_code,))
    ])


def ic_ab_test(db: Database, funds: list[FundSpec], settings: Settings,
               fund_code: str | None = None, horizon: int | None = None,
               step: int = 5, ic_step: int = 4) -> ICABReport:
    """走查 A/B：返回逐基金与合并的两臂对照。horizon 缺省取复盘有效期（无则 5 日）。"""
    horizon_source = "manual"
    if horizon is None:
        horizon_source = "auto"
        horizon = 5
        try:
            from .review import review_signals
            rep = review_signals(db, fund_code)
            if rep.best_horizon:
                horizon = rep.best_horizon
        except Exception:  # noqa: BLE001
            pass

    nav_map = _load_navs(db, fund_code)
    report = ICABReport(horizon=horizon, horizon_source=horizon_source, step=step)

    pooled_off_rets: list[float] = []
    pooled_on_rets: list[float] = []
    pooled_off_actionable = pooled_on_actionable = 0

    for fund in funds:
        if fund_code and fund.code != fund_code:
            continue
        navs = nav_map.get(fund.code, [])
        nav_df = pd.DataFrame(navs, columns=["trade_date", "nav"])
        warmup = max(max(settings.momentum_windows) + 1, 90)
        if len(navs) < warmup + 2:  # 至少 1 个信号日 + 前向空间
            report.funds.append(FundABResult(
                fund.code, fund.name, 0, None, 0, 0, None, None, None, 0,
                f"净值历史不足（{len(navs)} 日 < {warmup + 2}），无法重放"))
            continue

        index_val = _index_valuation(db, fund.effective_valuation_index())
        dates = [d for d, _ in navs]
        signal_idx = list(range(warmup, len(navs) - 1, step))  # 留 1 日入场空间

        ic_adjustments: dict = {}
        ic_active = 0
        off_signals: list[tuple[str, str]] = []
        on_signals: list[tuple[str, str]] = []

        for k, i in enumerate(signal_idx):
            date = dates[i]
            nav_t = nav_df.iloc[: i + 1]
            val_t = index_val[index_val["trade_date"] <= date]
            ctx = AnalysisContext(
                fund_code=fund.code, fund_name=fund.name, nav_history=nav_t,
                momentum=compute_momentum(nav_t, tuple(settings.momentum_windows),
                                          tuple(settings.momentum_windows_weights)),
                valuation=compute_valuation(val_t, settings.valuation_percentile_window),
                news=[], index_change_pct=None, index_valuation_df=val_t,
            )
            outputs = {a.name: a.analyze(ctx) for a in DETERMINISTIC_AGENTS}

            # IC 调整每 ic_step 个信号日重估（≤T 数据，无未来信息）
            if k % ic_step == 0:
                reports = evaluate_all_factors(
                    nav_t, val_t, tuple(settings.momentum_windows),
                    tuple(settings.momentum_windows_weights),
                    settings.valuation_percentile_window)
                ic_adjustments = {a: adj.to_dict() for a, adj in
                                  ic_weight_adjustments(reports, settings.ic_min_samples).items()}
            if ic_adjustments:
                ic_active += 1

            off_signals.append((date, synthesize(
                fund.code, fund.name, date, outputs, settings).action))
            on_signals.append((date, synthesize(
                fund.code, fund.name, date, outputs, settings,
                ic_adjustments=ic_adjustments).action))

        off = _arm_stats("关闭", off_signals, navs, horizon)
        on = _arm_stats("开启", on_signals, navs, horizon)
        verdict, delta = _verdict(off, on)
        agree = (sum(1 for (_, a), (_, b) in zip(off_signals, on_signals) if a == b)
                 / len(off_signals)) if off_signals else None
        report.funds.append(FundABResult(
            fund.code, fund.name, len(signal_idx), agree,
            _churn(off_signals), _churn(on_signals), off, on, delta, ic_active, verdict))

        for date, action in off_signals:
            if action in ("BUY", "SELL"):
                pooled_off_actionable += 1
                _, fwd = _forward_returns(navs, date, action, (horizon,))
                if fwd[horizon] is not None:
                    pooled_off_rets.append(fwd[horizon] / 100)
        for date, action in on_signals:
            if action in ("BUY", "SELL"):
                pooled_on_actionable += 1
                _, fwd = _forward_returns(navs, date, action, (horizon,))
                if fwd[horizon] is not None:
                    pooled_on_rets.append(fwd[horizon] / 100)

    if report.funds:
        s_off, s_on = _stats(pooled_off_rets), _stats(pooled_on_rets)
        report.pooled_off = ArmStat("关闭", pooled_off_actionable, s_off.samples if s_off else 0,
                                    s_off.win_rate if s_off else None,
                                    s_off.avg_return_pct if s_off else None,
                                    s_off.t_stat if s_off else None) if s_off else None
        report.pooled_on = ArmStat("开启", pooled_on_actionable, s_on.samples if s_on else 0,
                                   s_on.win_rate if s_on else None,
                                   s_on.avg_return_pct if s_on else None,
                                   s_on.t_stat if s_on else None) if s_on else None
        report.verdict, _ = _verdict(report.pooled_off, report.pooled_on, min_samples=POOLED_MIN)
    else:
        report.verdict = "无基金可重放"
    return report
