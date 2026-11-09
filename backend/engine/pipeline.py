"""每日分析流水线（扇出/合并模式）。

流程：交易日校验 → 数据刷新 → 逐基金「并行 Agent 分析 → 置信度加权合并
→ Bull/Bear 辩论与风控审核 → 硬风控前置 → 入库」→ 告警触达。

- Agent 层并发执行（ThreadPoolExecutor）：消息面 LLM 调用与其他量化 Agent 并行
- LLM 失败 fail-closed 回退；辩论层只能让信号更保守
- 单只基金失败不影响其他基金；信号按交易日幂等（重复运行不产生重复记录）
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd

from ..agents.base import AgentOutput, AnalysisContext
from ..agents.risk_agent import RiskAgent
from ..agents.sentiment import SentimentAgent
from ..agents.technical import TechnicalAgent
from ..agents.valuation_agent import ValuationAgent
from ..collectors.fund_data import fetch_fund_holdings, fetch_fund_nav
from ..collectors.market_data import fetch_index_ohlcv, fetch_index_valuation, fetch_market_news
from ..collectors.trading_calendar import is_trading_day, last_trading_day
from ..config import AppConfig, FundSpec
from ..contracts import SignalModel, RiskCheckModel, AgentOutputModel, DebateResultModel
from ..db import Database
from ..factors.momentum import compute_momentum
from ..factors.nav_estimate import estimate_nav
from ..factors.valuation import compute_valuation
from ..llm.client import LLMClient, LLMError
from ..notify import Notifier
from .debate import run_debate
from .orchestrator import synthesize
from .portfolio import portfolio_drawdown
from .risk_control import check

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        llm = None
        if config.llm.api_key_resolved:
            try:
                llm = LLMClient(config.llm)
            except LLMError as e:
                logger.warning("LLM 初始化失败，消息面 Agent 将回退: %s", e)
        self.llm = llm
        self.agents = [
            TechnicalAgent(),
            ValuationAgent(),
            SentimentAgent(llm=llm, fallback_enabled=config.llm.fallback_enabled,
                           samples=config.llm.sentiment_samples),
            RiskAgent(),
        ]
        self.notifier = Notifier(config.notifications)

    # ---------- 数据入库 ----------
    def refresh_data(self) -> dict[str, pd.DataFrame]:
        nav_frames: dict[str, pd.DataFrame] = {}
        for fund in self.config.funds:
            nav_df = fetch_fund_nav(fund.code)
            nav_frames[fund.code] = nav_df
            if not nav_df.empty:
                self.db.upsert_nav([
                    (fund.code, r.trade_date, float(r.nav) if pd.notna(r.nav) else None,
                     float(r.accumulated_nav) if pd.notna(r.accumulated_nav) else None)
                    for r in nav_df.itertuples()
                ])
            holdings = fetch_fund_holdings(fund.code)
            if not holdings.empty:
                self.db.upsert_holdings([
                    (fund.code, r.report_date, r.stock_code, r.stock_name, float(r.weight))
                    for r in holdings.itertuples()
                ])

        # 基准指数 + 估值专用指数（行业估值切换）都要采集
        for idx_code in ({f.benchmark_index for f in self.config.funds}
                         | {f.effective_valuation_index() for f in self.config.funds}):
            ohlcv_df = fetch_index_ohlcv(idx_code)
            if not ohlcv_df.empty:
                self.db.upsert_index_ohlcv([
                    (idx_code, r.trade_date,
                     float(r.open) if pd.notna(r.open) else None,
                     float(r.high) if pd.notna(r.high) else None,
                     float(r.low) if pd.notna(r.low) else None,
                     float(r.close) if pd.notna(r.close) else None,
                     float(r.volume) if pd.notna(r.volume) else None)
                    for r in ohlcv_df.itertuples()
                ])
            val_df = fetch_index_valuation(idx_code)
            if not val_df.empty:
                self.db.upsert_index([
                    (idx_code, r.trade_date, None,
                     float(r.pe) if pd.notna(r.pe) else None,
                     float(r.pb) if pd.notna(r.pb) else None,
                     float(r.pe_percentile) if pd.notna(getattr(r, "pe_percentile", None)) else None,
                     float(r.pb_percentile) if pd.notna(getattr(r, "pb_percentile", None)) else None)
                    for r in val_df.itertuples()
                ])

        news_df = fetch_market_news("股市")
        if not news_df.empty:
            self.db.insert_news([
                (r.published, r.title, r.content, r.source) for r in news_df.itertuples()
            ])
        return nav_frames

    # ---------- Agent 并行扇出 ----------
    def _run_agents(self, ctx: AnalysisContext) -> dict[str, AgentOutput]:
        outputs: dict[str, AgentOutput] = {}
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent") as pool:
            futures = {pool.submit(agent.analyze, ctx): agent for agent in self.agents}
            for fut in as_completed(futures):
                agent = futures[fut]
                try:
                    outputs[agent.name] = fut.result()
                except Exception as e:  # noqa: BLE001
                    logger.error("%s Agent 异常: %s", agent.name, e)
                    outputs[agent.name] = AgentOutput(
                        agent=agent.name, score=0.0, direction="HOLD", confidence=0.05,
                        summary=f"Agent 异常: {e}", fallback_used=True)
        return outputs

    def _build_context(self, fund: FundSpec, news: list[dict]) -> AnalysisContext:
        nav_rows = self.db.get_nav_history(fund.code)
        nav_history = pd.DataFrame([{"trade_date": r["trade_date"], "nav": r["nav"]} for r in nav_rows])
        holdings_rows = self.db.query(
            "SELECT * FROM fund_holdings WHERE fund_code=? ORDER BY report_date DESC LIMIT 10", (fund.code,))
        holdings = pd.DataFrame([dict(r) for r in holdings_rows])
        index_close = pd.DataFrame([
            {"trade_date": r["trade_date"], "close": r["close"]}
            for r in self.db.query(
                "SELECT trade_date, close FROM index_daily WHERE index_code=? AND close IS NOT NULL ORDER BY trade_date",
                (fund.benchmark_index,))
        ])
        index_val = pd.DataFrame([
            {"trade_date": r["trade_date"], "pe": r["pe"], "pb": r["pb"], "close": r["close"],
             "pe_percentile": r["pe_percentile"], "pb_percentile": r["pb_percentile"]}
            for r in self.db.query(
                "SELECT trade_date, pe, pb, close, pe_percentile, pb_percentile "
                "FROM index_daily WHERE index_code=? ORDER BY trade_date",
                (fund.effective_valuation_index(),))
        ])
        momentum = compute_momentum(nav_history, tuple(self.config.settings.momentum_windows),
                                    self.config.settings.momentum_windows_weights)
        valuation = compute_valuation(index_val, self.config.settings.valuation_percentile_window)
        nav_est = estimate_nav(nav_history, holdings, index_close)
        return AnalysisContext(
            fund_code=fund.code, fund_name=fund.name, nav_history=nav_history,
            momentum=momentum, valuation=valuation, news=news,
            index_change_pct=(nav_est.estimated_change_pct if nav_est else None),
        ), nav_history, nav_est

    # ---------- 单基金分析 ----------
    def analyze_fund(self, fund: FundSpec, trade_date: str, news: list[dict]) -> SignalModel:
        ctx, nav_history, nav_est = self._build_context(fund, news)
        outputs = self._run_agents(ctx)

        signal = synthesize(fund.code, fund.name, trade_date, outputs, self.config.settings)

        # Bull/Bear 辩论 + 风控审核层（非 HOLD 信号触发）
        if self.llm is not None and self.config.llm.debate_enabled and signal.action != "HOLD":
            debate = run_debate(self.llm, self.config.llm, signal, self.config.debate)
            signal.action = debate.final_action
            if not debate.agree:
                signal.target_position = 0.0
                signal.reasons.append(f"[debate] 风控审核否决：{debate.judge_reasoning}")
        else:
            from .debate import DebateResult
            debate = DebateResult(triggered=False, final_action=signal.action)

        # 硬风控前置（数据缺失时 BUY fail-closed）
        pos_rows = self.db.query("SELECT position FROM fund_position WHERE fund_code=?", (fund.code,))
        current_position = float(pos_rows[0]["position"]) if pos_rows else None
        portfolio_dd = portfolio_drawdown(self.db, nav_history) if current_position is not None else None
        risk_result = check(signal, self.config.settings, current_position, portfolio_dd)
        if not risk_result.passed:
            signal.target_position = 0.0

        # 入库
        for out in outputs.values():
            self.db.save_agent_log(out.to_log_record(trade_date, fund.code))
        self.db.save_agent_log({
            "trade_date": trade_date, "fund_code": fund.code, "agent": "debate",
            "score": 0.0, "direction": signal.action, "confidence": debate.confidence,
            "summary": (f"辩论{'维持' if debate.agree else '否决'}信号 → {signal.action}"
                        f"（{debate.rounds_used} 轮）；"
                        f"{debate.judge_reasoning or debate.unavailable_reason}"),
            "fallback_used": int(bool(debate.unavailable_reason) and debate.unavailable_reason != "debate_disabled"),
            "detail_json": json.dumps({
                "bull": debate.bull_points, "bear": debate.bear_points,
                "rounds": debate.rounds, "rounds_used": debate.rounds_used,
                "triggered": debate.triggered, "agree": debate.agree,
                "final_action": debate.final_action, "confidence": debate.confidence,
                "judge_reasoning": debate.judge_reasoning,
                "unavailable_reason": debate.unavailable_reason,
            }, ensure_ascii=False),
            "data_quality": 1.0,
        })
        self.db.save_signal({
            "trade_date": trade_date, "fund_code": fund.code, "action": signal.action,
            "target_position": signal.target_position, "composite_score": signal.composite_score,
            "score_technical": outputs["technical"].score, "score_valuation": outputs["valuation"].score,
            "score_sentiment": outputs["sentiment"].score, "score_risk": outputs["risk"].score,
            "reasons_json": json.dumps(signal.reasons, ensure_ascii=False),
            "risk_checks_json": json.dumps(risk_result.checks, ensure_ascii=False),
        })

        return SignalModel(
            trade_date=trade_date, fund_code=fund.code, fund_name=fund.name,
            action=signal.action, target_position=signal.target_position,
            composite_score=signal.composite_score,
            risk_passed=risk_result.passed, risk_summary=risk_result.summary,
            risk_checks=[RiskCheckModel(**c) for c in risk_result.checks],
            reasons=signal.reasons,
            agents={k: AgentOutputModel(**v.to_dict()) for k, v in outputs.items()},
            debate=DebateResultModel(
                triggered=debate.triggered, agree=debate.agree,
                final_action=debate.final_action, confidence=debate.confidence,
                bull_points=debate.bull_points, bear_points=debate.bear_points,
                rounds=debate.rounds, rounds_used=debate.rounds_used,
                judge_reasoning=debate.judge_reasoning,
                unavailable_reason=debate.unavailable_reason,
            ),
            nav_estimate=nav_est.__dict__ if nav_est else None,
        )

    # ---------- 主入口 ----------
    def run(self, force: bool = False) -> list[SignalModel]:
        today = date.today()
        if not is_trading_day(today):
            logger.info("%s 非交易日，跳过流水线", today)
            return []
        trade_date = last_trading_day(today).strftime("%Y-%m-%d")

        if not force:
            existing = self.db.query("SELECT COUNT(*) AS n FROM signals WHERE trade_date=?", (trade_date,))
            if existing[0]["n"] >= len(self.config.funds):
                logger.info("%s 信号已存在，跳过（force=True 可强制重跑）", trade_date)
                return self._existing_signals(trade_date)

        logger.info("流水线开始：%s（LLM=%s, model=%s）", trade_date,
                    "启用" if self.llm else "未配置", self.config.llm.model_name or self.config.llm.model)
        self.refresh_data()
        news = [dict(r) for r in self.db.query(
            "SELECT published, title, content, source FROM news_items ORDER BY published DESC LIMIT 20")]

        results: list[SignalModel] = []
        for fund in self.config.funds:
            try:
                results.append(self.analyze_fund(fund, trade_date, news))
            except Exception as e:  # noqa: BLE001
                logger.exception("基金 %s 分析失败: %s", fund.code, e)
        self.notifier.send([s for s in results if s.action in ("BUY", "SELL") or not s.risk_passed])
        logger.info("流水线完成：%d 只基金", len(results))
        return results

    def _existing_signals(self, trade_date: str) -> list[SignalModel]:
        """已运行过的交易日：从库中重建信号视图（幂等）。"""
        rows = self.db.query("SELECT * FROM signals WHERE trade_date=?", (trade_date,))
        names = {f.code: f.name for f in self.config.funds}
        return rebuild_signal_views(self.db, rows, names)


def rebuild_signal_views(db: Database, rows: list, fund_names: dict[str, str] | None = None
                         ) -> list[SignalModel]:
    """signals 表行 → 完整 SignalModel（agents/debate 从 agent_logs 联查重建）。

    /api/pipeline/run 幂等路径与 /api/signals/latest 共用：页面刷新后
    逐轮辩论与 Agent 评分与实时运行结果完全一致，而不是空壳。
    """
    fund_names = fund_names or {}
    out: list[SignalModel] = []
    for r in rows:
        code = r["fund_code"]
        logs = db.query("SELECT * FROM agent_logs WHERE trade_date=? AND fund_code=?",
                        (r["trade_date"], code))
        agents: dict[str, AgentOutputModel] = {}
        debate = DebateResultModel()
        for log in logs:
            detail = json.loads(log["detail_json"] or "{}")
            if log["agent"] == "debate":
                debate = _rebuild_debate(log, detail)
            else:
                agents[log["agent"]] = AgentOutputModel(
                    agent=log["agent"], score=log["score"] or 0.0,
                    direction=log["direction"] or "HOLD",
                    confidence=log["confidence"] if log["confidence"] is not None else 0.5,
                    summary=log["summary"] or "",
                    fallback_used=bool(log["fallback_used"]),
                    data_quality=log["data_quality"] if log["data_quality"] is not None else 1.0,
                    detail=detail if isinstance(detail, dict) else {},
                )
        checks = [RiskCheckModel(**c) for c in json.loads(r["risk_checks_json"] or "[]")]
        out.append(SignalModel(
            trade_date=r["trade_date"], fund_code=code,
            fund_name=fund_names.get(code, code),
            action=r["action"], target_position=r["target_position"] or 0.0,
            composite_score=r["composite_score"] or 0.0,
            risk_passed=all(c.passed for c in checks),
            risk_summary="（历史信号）",
            risk_checks=checks,
            reasons=json.loads(r["reasons_json"] or "[]"),
            agents=agents, debate=debate,
        ))
    return out


def _rebuild_debate(log, detail: dict) -> DebateResultModel:
    """debate 日志行 → DebateResultModel。新版 detail_json 含全量字段；
    旧版（本改动前写入）从 summary 模板反解。"""
    summary = log["summary"] or ""
    if "agree" in detail:  # 新格式：全量字段已入库
        agree = bool(detail["agree"])
        judge = detail.get("judge_reasoning", "")
    else:  # 旧行：summary = "辩论维持/否决信号 → X（N 轮）；{judge}"
        agree = "否决" not in summary
        judge = summary.split("）；", 1)[1] if "）；" in summary else ""
    return DebateResultModel(
        triggered=bool(detail.get("triggered")),
        agree=agree,
        final_action=detail.get("final_action") or log["direction"] or "HOLD",
        confidence=detail.get("confidence", log["confidence"] if log["confidence"] is not None else 0.5),
        bull_points=detail.get("bull") or [], bear_points=detail.get("bear") or [],
        rounds=detail.get("rounds") or [], rounds_used=detail.get("rounds_used") or 0,
        judge_reasoning=judge,
        unavailable_reason=detail.get("unavailable_reason", ""),
    )
