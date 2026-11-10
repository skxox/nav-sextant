"""信号复盘：历史 BUY/SELL 信号的事后 N 日收益验证 + 信号衰减曲线。

回答 ROADMAP A 组首问：这套系统到底准不准。

口径说明（与人工执行流程对齐，避免前视偏差）：
- 信号在 T 日收盘后生成，人 T+1 日执行，场外基金按 T+1 日净值确认申赎
  → 入场净值 = 信号日之后第 1 个交易日净值；N 日收益 = nav[入场+N] / nav[入场] - 1
- 方向化收益：BUY 记 +ret，SELL 记 -ret —— 群体均值 > 0 即信号有信息量
- 命中：单条信号方向化收益 > 0；胜率 = 命中数 / 有效样本数
- 衰减曲线：各 N 的方向化平均收益走势，峰值 N 即信号有效期
  （N 过小则噪声主导、N 过大则信息耗散，中间应有峰）
- HOLD 不参与评估（无方向），仅计入总信号数
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..db import Database

DEFAULT_HORIZONS = (1, 3, 5, 10, 20)
MIN_SAMPLES = 8  # 低于此样本数的 horizon 不参与「有效期」判定


@dataclass
class HorizonStat:
    """单个持有期 N 的复盘统计（方向化口径）。"""
    horizon: int
    samples: int                 # 有效样本数
    win_rate: float | None       # 0~1，样本不足为 None
    avg_return_pct: float | None # 方向化平均收益 %
    t_stat: float | None         # H0: 方向化收益均值=0 的 t 值

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon, "samples": self.samples,
            "win_rate": round(self.win_rate, 3) if self.win_rate is not None else None,
            "avg_return_pct": round(self.avg_return_pct, 2) if self.avg_return_pct is not None else None,
            "t_stat": round(self.t_stat, 2) if self.t_stat is not None else None,
        }


def _stats(directional_returns: list[float]) -> HorizonStat | None:
    """一组方向化收益（小数口径）→ 统计量。"""
    n = len(directional_returns)
    if n == 0:
        return None
    arr = np.array(directional_returns)
    win_rate = float((arr > 0).mean())
    avg = float(arr.mean())
    t = None
    if n >= 3 and arr.std(ddof=1) > 1e-12:
        t = float(avg / (arr.std(ddof=1) / np.sqrt(n)))
    return HorizonStat(horizon=0, samples=n, win_rate=win_rate,
                       avg_return_pct=avg * 100, t_stat=t)


@dataclass
class EvaluatedSignal:
    """单条已评估信号：各 N 的方向化收益（%）。"""
    trade_date: str
    fund_code: str
    action: str
    composite_score: float | None
    entry_date: str | None       # 入场日（信号日次一交易日）；前向净值不足为 None
    returns_pct: dict[int, float | None] = field(default_factory=dict)  # N → 方向化收益%

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date, "fund_code": self.fund_code,
            "action": self.action,
            "composite_score": self.composite_score,
            "entry_date": self.entry_date,
            "returns_pct": {str(k): (round(v, 2) if v is not None else None)
                            for k, v in self.returns_pct.items()},
        }


@dataclass
class SignalReviewReport:
    horizons: list[int]
    total_signals: int           # 全部信号（含 HOLD）
    actionable: int              # BUY+SELL 数
    evaluated: int               # 至少一个 horizon 有前向数据的 BUY/SELL 数
    pending: int                 # 信号日次一交易日净值尚不可得的（当日/最新信号）
    decay_curve: list[HorizonStat]           # 全体信号方向化收益随 N 衰减
    by_action: dict[str, list[HorizonStat]]  # BUY / SELL 分组
    by_fund: list[dict]                      # 每基金统计
    best_horizon: int | None     # 信号有效期（方向化平均收益峰值的 N）
    verdict: str                 # 一句话结论
    details: list[EvaluatedSignal]           # 已评估信号明细（新→旧）

    def to_dict(self) -> dict:
        return {
            "horizons": self.horizons,
            "total_signals": self.total_signals,
            "actionable": self.actionable,
            "evaluated": self.evaluated,
            "pending": self.pending,
            "decay_curve": [h.to_dict() for h in self.decay_curve],
            "by_action": {a: [h.to_dict() for h in hs] for a, hs in self.by_action.items()},
            "by_fund": self.by_fund,
            "best_horizon": self.best_horizon,
            "verdict": self.verdict,
            "details": [d.to_dict() for d in self.details],
        }


def _verdict(curve: list[HorizonStat], best: int | None) -> str:
    if not curve or max(h.samples for h in curve) < MIN_SAMPLES:
        return f"样本不足（有效评估需 ≥{MIN_SAMPLES} 条，积累信号后重试）"
    if best is not None:
        stat = next(h for h in curve if h.horizon == best)
        return (f"信号有效期约 {best} 日：{best} 日方向化收益 "
                f"+{stat.avg_return_pct:.2f}%，命中率 {stat.win_rate * 100:.1f}%")
    return "信号暂无信息量：各期方向化收益均 ≤ 0，考虑关闭或重校准"


def _load_navs(db: Database, fund_code: str | None = None) -> dict[str, list[tuple[str, float]]]:
    """各基金净值序列（按日期升序，已剔 NULL）。"""
    nav_by_fund: dict[str, list[tuple[str, float]]] = {}
    nav_where, nav_params = "", ()
    if fund_code:
        nav_where = " AND fund_code=?"
        nav_params = (fund_code,)
    for r in db.query(
            f"SELECT fund_code, trade_date, nav FROM fund_nav WHERE nav IS NOT NULL{nav_where} ORDER BY trade_date",
            nav_params):
        nav_by_fund.setdefault(r["fund_code"], []).append((r["trade_date"], float(r["nav"])))
    return nav_by_fund


def _forward_returns(navs: list[tuple[str, float]], signal_date: str, action: str,
                     horizons: tuple[int, ...]) -> tuple[str | None, dict[int, float | None]]:
    """单条方向判断的前向收益（复盘统一口径）。

    入场 = 信号日之后第 1 个交易日净值（人 T+1 执行、按 T+1 净值确认）；
    N 日收益 = nav[入场+N]/nav[入场] - 1，BUY 记正、SELL 记负。
    返回 (entry_date, {N: 方向化收益%})；净值未到账时 entry_date 为 None。
    """
    entry_idx = next((i for i, (d, _) in enumerate(navs) if d > signal_date), None)
    if entry_idx is None:
        return None, {n: None for n in horizons}
    sign = 1.0 if action == "BUY" else -1.0
    out: dict[int, float | None] = {}
    for n in horizons:
        j = entry_idx + n
        out[n] = sign * (navs[j][1] / navs[entry_idx][1] - 1) * 100 if j < len(navs) else None
    return navs[entry_idx][0], out


def _evaluate(db: Database, fund_code: str | None,
              horizons: tuple[int, ...]) -> tuple[list[EvaluatedSignal], dict[str, list[tuple[str, float]]],
                                                   int, int, int]:
    """加载信号并按复盘口径评估前向收益。

    返回 (已评估信号, 各基金净值序列, 总信号数, BUY/SELL 数, pending 数)。
    """
    where, params = "", ()
    if fund_code:
        where = " WHERE fund_code=?"
        params = (fund_code,)
    signal_rows = db.query(
        f"SELECT trade_date, fund_code, action, composite_score FROM signals{where} ORDER BY trade_date",
        params)
    nav_by_fund = _load_navs(db, fund_code)

    evaluated_signals: list[EvaluatedSignal] = []
    total = actionable = pending = 0
    for s in signal_rows:
        total += 1
        if s["action"] not in ("BUY", "SELL"):
            continue
        actionable += 1
        navs = nav_by_fund.get(s["fund_code"], [])
        entry_date, rets = _forward_returns(navs, s["trade_date"], s["action"], horizons)
        if entry_date is None:
            pending += 1
            continue
        evaluated_signals.append(EvaluatedSignal(
            trade_date=s["trade_date"], fund_code=s["fund_code"], action=s["action"],
            composite_score=float(s["composite_score"]) if s["composite_score"] is not None else None,
            entry_date=entry_date, returns_pct=rets,
        ))
    return evaluated_signals, nav_by_fund, total, actionable, pending


def review_signals(db: Database,
                   fund_code: str | None = None,
                   horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                   detail_limit: int = 200,
                   fund_names: dict[str, str] | None = None) -> SignalReviewReport:
    """复盘全部（或单只基金）历史信号。

    - fund_code: None = 全部基金合并统计
    - details 只保留最近 detail_limit 条已评估信号（API 载荷控制）
    """
    horizons = tuple(sorted(set(h for h in horizons if h >= 1)))
    names = fund_names or {}

    evaluated_signals, _navs, total, actionable, pending = _evaluate(db, fund_code, horizons)

    # ---- 聚合：全体 / 分 action / 分基金 ----
    def _curve(sigs: list[EvaluatedSignal]) -> list[HorizonStat]:
        out = []
        for n in horizons:
            rets = [ev.returns_pct[n] / 100 for ev in sigs
                    if ev.returns_pct.get(n) is not None]
            st = _stats(rets)
            if st:
                st.horizon = n
                out.append(st)
            else:
                out.append(HorizonStat(horizon=n, samples=0,
                                       win_rate=None, avg_return_pct=None, t_stat=None))
        return out

    decay_curve = _curve(evaluated_signals)
    by_action = {
        a: _curve([ev for ev in evaluated_signals if ev.action == a])
        for a in ("BUY", "SELL")
        if any(ev.action == a for ev in evaluated_signals)
    }

    by_fund: list[dict] = []
    for code in sorted({ev.fund_code for ev in evaluated_signals}):
        fund_sigs = [ev for ev in evaluated_signals if ev.fund_code == code]
        by_fund.append({
            "fund_code": code,
            "fund_name": names.get(code, code),
            "evaluated": len(fund_sigs),
            "horizons": [h.to_dict() for h in _curve(fund_sigs)],
        })

    # ---- 有效期：方向化平均收益的峰值 N（样本充足者中取正值最大者）----
    best: int | None = None
    qualified = [h for h in decay_curve if h.samples >= MIN_SAMPLES
                 and h.avg_return_pct is not None and h.avg_return_pct > 0]
    if qualified:
        best = max(qualified, key=lambda h: h.avg_return_pct).horizon

    return SignalReviewReport(
        horizons=list(horizons),
        total_signals=total,
        actionable=actionable,
        evaluated=len(evaluated_signals),
        pending=pending,
        decay_curve=decay_curve,
        by_action=by_action,
        by_fund=by_fund,
        best_horizon=best,
        verdict=_verdict(decay_curve, best),
        details=list(reversed(evaluated_signals))[:detail_limit],
    )


# ---------- 信号打乱对照（shuffle test）----------
# 最狠也最诚实的自检：若真实信号的方向化收益与「随机挑日期、同样方向组合」
# 的对照无显著差异 → 系统不含择时信息。思想源自 llm-quant 的 fraud detector。

@dataclass
class ShuffleHorizonResult:
    horizon: int
    samples: int                # 参与打乱的有效信号数
    real_avg_pct: float | None  # 真实信号方向化平均收益 %
    null_mean_pct: float | None # 随机对照均值 %
    null_std_pct: float | None
    z_score: float | None       # (真实 - 随机均值) / 随机标准差
    p_value: float | None       # 单侧 P(随机对照 ≥ 真实)，越小越显著
    verdict: str

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon, "samples": self.samples,
            "real_avg_pct": _r2(self.real_avg_pct), "null_mean_pct": _r2(self.null_mean_pct),
            "null_std_pct": _r2(self.null_std_pct),
            "z_score": _r2(self.z_score),
            "p_value": round(self.p_value, 4) if self.p_value is not None else None,
            "verdict": self.verdict,
        }


def _r2(v: float | None) -> float | None:
    if v is None:
        return None
    if v == float("inf"):
        return 99.99   # JSON 不支持 Infinity，钳制为极显著标记值
    if v == float("-inf"):
        return -99.99
    return round(v, 2)


@dataclass
class ShuffleTestReport:
    n_trials: int
    seed: int
    horizons: list[ShuffleHorizonResult]
    verdict: str                 # 总结论

    def to_dict(self) -> dict:
        return {
            "n_trials": self.n_trials, "seed": self.seed,
            "horizons": [h.to_dict() for h in self.horizons],
            "verdict": self.verdict,
        }


def shuffle_test(db: Database,
                 fund_code: str | None = None,
                 horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                 n_trials: int = 300,
                 seed: int = 42) -> ShuffleTestReport:
    """打乱对照检验：真实信号 vs 随机择时对照。

    对照构造（保持与真实信号同分布的可比性）：
    - 方向组合不变（BUY/SELL 条数与真实一致）
    - 每条信号在其所属基金的净值序列上随机抽一个合法入场日
      （entry ∈ [1, len-N-1]，与真实信号同款 T+1 入场约束）
    - 每次打乱计算全体方向化平均收益 → 零分布
    - p = P(随机 ≥ 真实)（单侧：我们只关心「是否比随机强」）

    多 horizon 同时检验存在多重比较膨胀，结论作探索性诊断用。
    固定 seed 保证同数据同结果（可复现）。
    """
    horizons = tuple(sorted(set(h for h in horizons if h >= 1)))
    evaluated_signals, nav_by_fund, *_ = _evaluate(db, fund_code, horizons)

    results: list[ShuffleHorizonResult] = []
    for n in horizons:
        sigs = [ev for ev in evaluated_signals if ev.returns_pct.get(n) is not None]
        if len(sigs) < MIN_SAMPLES:
            results.append(ShuffleHorizonResult(n, len(sigs), None, None, None, None, None, "样本不足"))
            continue

        real_avg = float(np.mean([ev.returns_pct[n] for ev in sigs]))

        # ---- 零分布：n_trials 次随机重排入场日（分基金抽，向量化）----
        rng = np.random.default_rng(seed + n)
        trial_sum = np.zeros(n_trials)
        funds = sorted({ev.fund_code for ev in sigs})
        for f in funds:
            group = [ev for ev in sigs if ev.fund_code == f]
            navs = nav_by_fund[f]
            # 该基金在 horizon n 下所有合法入场日的 N 日收益池
            ret_pool = np.array([navs[i + n][1] / navs[i][1] - 1
                                 for i in range(1, len(navs) - n)])
            signs = np.array([1.0 if ev.action == "BUY" else -1.0 for ev in group])
            idx = rng.integers(0, len(ret_pool), size=(n_trials, len(group)))
            trial_sum += (ret_pool[idx] * signs).sum(axis=1)
        trial_means = trial_sum / len(sigs) * 100

        null_mean = float(trial_means.mean())
        null_std = float(trial_means.std(ddof=1)) if n_trials > 1 else 0.0
        p = float((trial_means >= real_avg).mean())
        z = (real_avg - null_mean) / null_std if null_std > 1e-12 else None

        if p <= 0.05 and (z or 0) > 0:
            v = "显著优于随机"
        elif p >= 0.95 and (z or 0) < 0:
            v = "显著差于随机"
        else:
            v = "与随机无异"
        results.append(ShuffleHorizonResult(n, len(sigs), real_avg, null_mean, null_std,
                                            z, p, v))

    winners = [h for h in results if h.verdict == "显著优于随机"]
    if winners:
        verdict = (f"打乱对照：{len(winners)}/{len(results)} 个持有期显著优于随机"
                   f"（{('、'.join(str(h.horizon) + ' 日' for h in winners))}）——信号含择时信息")
    elif any(h.samples >= MIN_SAMPLES for h in results):
        verdict = "打乱对照：与随机无显著差异——信号不含择时信息，警惕系统无信息量"
    else:
        verdict = f"样本不足（有效评估需 ≥{MIN_SAMPLES} 条，积累信号后重试）"

    return ShuffleTestReport(n_trials=n_trials, seed=seed, horizons=results, verdict=verdict)


# ---------- 分 Agent 记分卡 ----------
# 四路分析 Agent（技术/估值/消息/风险）各自的历史命中率——找出谁在拖后腿。
# debate Agent 的角色是"否决"而非"定向"，不参与定向记分。

AGENT_LABELS = {"technical": "技术面", "valuation": "估值面", "sentiment": "消息面", "risk": "风险面"}


@dataclass
class AgentStat:
    agent: str
    label: str
    samples: int                 # 参与评估的方向判断数（BUY/SELL 且前向净值可用）
    hit_rate: float | None       # 方向命中率 0~1
    avg_return_pct: float | None # 方向化平均收益 %
    t_stat: float | None
    avg_confidence: float | None # 该 Agent 自报平均置信度
    verdict: str

    def to_dict(self) -> dict:
        return {
            "agent": self.agent, "label": self.label, "samples": self.samples,
            "hit_rate": round(self.hit_rate, 3) if self.hit_rate is not None else None,
            "avg_return_pct": _r2(self.avg_return_pct),
            "t_stat": _r2(self.t_stat),
            "avg_confidence": round(self.avg_confidence, 3) if self.avg_confidence is not None else None,
            "verdict": self.verdict,
        }


@dataclass
class AgentScorecard:
    horizon: int                 # 评估使用的持有期
    horizon_source: str          # auto（取信号有效期）/ manual
    agents: list[AgentStat]
    composite: AgentStat | None  # 最终合成信号作为基准
    verdict: str

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon, "horizon_source": self.horizon_source,
            "agents": [a.to_dict() for a in self.agents],
            "composite": self.composite.to_dict() if self.composite else None,
            "verdict": self.verdict,
        }


def _agent_verdict(samples: int, avg_pct: float | None, t: float | None) -> str:
    if samples < MIN_SAMPLES:
        return "样本不足"
    assert avg_pct is not None
    if (t or 0) >= 2.0:
        return "显著正向"
    if avg_pct > 0:
        return "方向正确不显著"
    return "方向相反（拖后腿）" if (t or 0) > -2.0 else "显著反向"


def _agent_stat(label: str, agent_key: str, rets: list[float], confs: list[float]) -> AgentStat:
    arr = np.array(rets)
    n = len(arr)
    hit = float((arr > 0).mean())
    avg = float(arr.mean())
    t = None
    if n >= 3:
        std = arr.std(ddof=1)
        if std > 1e-12:
            t = float(avg / (std / np.sqrt(n)))
        elif abs(avg) > 1e-12:
            # 零方差且非零均值：全对/全错的完美单调样本 → 极显著（保留符号）
            t = float("inf") if avg > 0 else float("-inf")
    return AgentStat(agent=agent_key, label=label, samples=n, hit_rate=hit,
                     avg_return_pct=avg, t_stat=t,
                     avg_confidence=float(np.mean(confs)) if confs else None,
                     verdict=_agent_verdict(n, avg, t))


def agent_scorecard(db: Database,
                    fund_code: str | None = None,
                    horizon: int | None = None) -> AgentScorecard:
    """四路 Agent + 合成信号的方向记分卡（复盘统一口径）。

    - horizon: None = 自动取信号复盘的有效期（best_horizon），无有效期时回落 5 日
    - 每条 agent_logs 的 BUY/SELL 方向判断，按 T+1 入场、N 日方向化收益评估
    - HOLD 判断不参与定向记分（与信号复盘一致：无方向无对错）
    """
    # ---- 自动选持有期：优先信号有效期 ----
    horizon_source = "manual"
    if horizon is None:
        horizon = review_signals(db, fund_code).best_horizon or 5
        horizon_source = "auto"
    horizons = (int(horizon),)

    nav_by_fund = _load_navs(db, fund_code)
    where, params = "", ()
    if fund_code:
        where = " WHERE fund_code=?"
        params = (fund_code,)
    log_rows = db.query(
        f"SELECT trade_date, fund_code, agent, direction, confidence FROM agent_logs"
        f"{where} ORDER BY trade_date")

    # agent → (方向化收益列表, 置信度列表)
    buckets: dict[str, tuple[list[float], list[float]]] = {
        a: ([], []) for a in AGENT_LABELS}
    for r in log_rows:
        if r["agent"] not in buckets or r["direction"] not in ("BUY", "SELL"):
            continue
        _, rets = _forward_returns(nav_by_fund.get(r["fund_code"], []),
                                   r["trade_date"], r["direction"], horizons)
        if rets[horizons[0]] is None:
            continue
        buckets[r["agent"]][0].append(rets[horizons[0]])
        conf = float(r["confidence"]) if r["confidence"] is not None else None
        if conf is not None:
            buckets[r["agent"]][1].append(conf)

    agents = [_agent_stat(label, key, *buckets[key]) for key, label in AGENT_LABELS.items()]

    # ---- 合成信号基准（同口径、同持有期）----
    evaluated, _, _, _, _ = _evaluate(db, fund_code, horizons)
    composite = None
    rets_c = [ev.returns_pct[horizons[0]] for ev in evaluated if ev.returns_pct.get(horizons[0]) is not None]
    if rets_c:
        composite = _agent_stat("合成信号", "composite", rets_c, [])

    useful = [a for a in agents if a.samples >= MIN_SAMPLES]
    if not useful and not (composite and composite.samples >= MIN_SAMPLES):
        verdict = f"样本不足（各路 Agent 有效评估需 ≥{MIN_SAMPLES} 条）"
    else:
        ranked = sorted(useful, key=lambda a: a.avg_return_pct or 0, reverse=True)
        parts = []
        if ranked:
            best, worst = ranked[0], ranked[-1]
            parts.append(f"贡献最大：{best.label}（{best.avg_return_pct:+.2f}%）")
            if len(ranked) > 1 and (worst.avg_return_pct or 0) < 0:
                parts.append(f"拖后腿：{worst.label}（{worst.avg_return_pct:+.2f}%）——考虑降权")
        verdict = "；".join(parts) or "各路 Agent 暂无有效样本"

    return AgentScorecard(horizon=horizons[0], horizon_source=horizon_source,
                          agents=agents, composite=composite, verdict=verdict)
