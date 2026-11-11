/** 后端 API 类型定义 —— 与 backend/contracts.py 中的 pydantic 模型保持一致 */

export type FundCode = string
export type TradeDate = string // YYYY-MM-DD
export type AgentName = 'technical' | 'valuation' | 'sentiment' | 'risk'
export type SignalAction = 'BUY' | 'SELL' | 'HOLD'

export interface FundInfo {
  code: FundCode
  name: string
  benchmark_index: string
  valuation_index: string
  sector: string
}

export interface AgentOutput {
  agent: AgentName
  score: number // -1 ~ 1
  direction: SignalAction
  confidence: number // 0 ~ 1
  summary: string
  fallback_used: boolean
  data_quality: number // 0 ~ 1 输入数据充分度
  detail: Record<string, unknown>
}

export interface RiskCheck {
  name: string
  passed: boolean
  detail: string
}

export interface DebateRound {
  round: number
  bull_points: string[]
  bear_points: string[]
  bull_confidence: number
  bear_confidence: number
}

export interface DebateResult {
  triggered: boolean
  agree: boolean
  final_action: SignalAction
  confidence: number
  bull_points: string[]
  bear_points: string[]
  rounds: DebateRound[]
  rounds_used: number
  judge_reasoning: string
  unavailable_reason: string
}

export interface NavEstimate {
  fund_code: FundCode
  latest_official_date: string | null
  latest_official_nav: number | null
  estimated_change_pct: number | null
  estimated_nav: number | null
}

export interface Signal {
  trade_date: TradeDate
  fund_code: FundCode
  fund_name: string
  action: SignalAction
  target_position: number
  composite_score: number
  risk_passed: boolean
  risk_summary: string
  risk_checks: RiskCheck[]
  reasons: string[]
  agents: Record<AgentName, AgentOutput>
  debate: DebateResult
  nav_estimate: NavEstimate | null
}

export interface SignalHistoryItem {
  id: number
  trade_date: TradeDate
  fund_code: FundCode
  action: SignalAction
  target_position: number
  composite_score: number
  score_technical: number
  score_valuation: number
  score_sentiment: number
  score_risk: number
  reasons_json: string
  risk_checks_json: string
}

export interface NavPoint {
  trade_date: TradeDate
  nav: number | null
  accumulated_nav: number | null
}

export interface AgentLog {
  id: number
  trade_date: TradeDate
  fund_code: FundCode
  agent: AgentName
  score: number
  direction: SignalAction
  confidence: number
  summary: string
  fallback_used: number
  detail_json: string
}

export interface FundPosition {
  fund_code: FundCode
  position: number
  shares: number
  cost: number
}

export interface MetaInfo {
  version: string
  llm: { model: string; model_name: string; configured: boolean; debate_enabled: boolean }
  scheduler: { enabled: boolean; run_at: string }
  notifications_enabled: boolean
}

export interface IntradayEstimate {
  fund_code: FundCode
  fund_name: string
  index_code: string
  index_change_pct: number | null
  estimated_change_pct: number | null
  estimated_nav: number | null
  latest_nav: number | null
  latest_nav_date: string | null
  as_of: string
}

export interface HoldingItem {
  report_date: string
  stock_code: string
  stock_name: string | null
  weight: number
}

export interface LedgerRow {
  fund_code: FundCode
  fund_name: string
  sector: string
  position: number
  shares: number
  cost: number
  latest_nav: number | null
  latest_nav_date: string | null
  prev_nav: number | null
  daily_change_pct: number | null
  daily_pnl: number | null
  daily_estimated: boolean
  market_value: number | null
  cost_total: number | null
  pnl: number | null
  pnl_pct: number | null
}

export interface LedgerSummary {
  market_value: number
  daily_pnl: number
  pnl: number
  cost_total: number
  pnl_pct: number | null
}

export interface LedgerResponse {
  rows: LedgerRow[]
  summary: LedgerSummary
}

export interface KlinePoint {
  trade_date: TradeDate
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
}

export interface KlineData {
  code: string
  name: string
  rows: KlinePoint[]
}

export interface IndexHistoryData {
  code: string
  name: string
  rows: { trade_date: TradeDate; close: number }[]
}

export interface PerformanceData {
  fund_code: FundCode
  fund_name: string
  benchmark: { code: string; name: string }
  rows: { trade_date: TradeDate; fund: number; benchmark: number }[]
}

export interface ProfitPoint {
  trade_date: TradeDate
  market_value: number
  profit: number | null
  profit_rate: number | null
}

export interface ProfitData {
  rows: ProfitPoint[]
  cost_total: number
}

export interface FactorICReport {
  factor: string
  label: string
  samples: number
  rank_ic: number
  ic_tstat: number
  ic_mean: number
  ic_std: number
  icir: number
  ic_win_rate: number
  q5_q1_spread_pct: number
  forward_days: number
  verdict: string
}

export interface FactorEvalResult {
  fund_code: FundCode
  fund_name: string
  valuation_index: string
  forward_days: number
  reports: FactorICReport[]
}

// ---------- 执行记录（决策日志）----------

export type ExecAction = 'BUY' | 'SELL' | 'SKIP'
export type ExecStatus = 'confirmed' | 'pending' | 'skipped'

export interface ExecutionRecord {
  id: number
  fund_code: FundCode
  fund_name: string
  signal_date: TradeDate | null
  exec_date: TradeDate
  action: ExecAction
  amount: number | null
  nav: number | null
  nav_source: 'manual' | 'nav' | null
  shares: number | null
  status: ExecStatus
  note: string
  signal_action: SignalAction | null
  signal_target: number | null
  signal_score: number | null
  created_at: string
}

export interface ExecutionStats {
  actionable: number
  executed: number
  skipped: number
  unhandled: number
  exec_rate: number | null
}

export interface ExecutionsResponse {
  rows: ExecutionRecord[]
  stats: ExecutionStats
}

export interface ExecutionCreateBody {
  fund_code: FundCode
  exec_date: TradeDate
  action: ExecAction
  signal_date?: TradeDate
  amount?: number
  note?: string
}

// ---------- 信号复盘 ----------

export interface ReviewHorizonStat {
  horizon: number
  samples: number
  win_rate: number | null
  avg_return_pct: number | null
  t_stat: number | null
}

export interface ReviewFundStat {
  fund_code: FundCode
  fund_name: string
  evaluated: number
  horizons: ReviewHorizonStat[]
}

export interface ReviewDetailItem {
  trade_date: TradeDate
  fund_code: FundCode
  action: 'BUY' | 'SELL'
  composite_score: number | null
  entry_date: TradeDate | null
  returns_pct: Record<string, number | null>
}

export interface SignalReviewReport {
  horizons: number[]
  total_signals: number
  actionable: number
  evaluated: number
  pending: number
  decay_curve: ReviewHorizonStat[]
  by_action: Record<string, ReviewHorizonStat[]>
  by_fund: ReviewFundStat[]
  best_horizon: number | null
  verdict: string
  details: ReviewDetailItem[]
}

// ---------- 打乱对照（shuffle test）----------

export interface ShuffleHorizonResult {
  horizon: number
  samples: number
  real_avg_pct: number | null
  null_mean_pct: number | null
  null_std_pct: number | null
  z_score: number | null
  p_value: number | null
  verdict: string
}

export interface ShuffleTestReport {
  n_trials: number
  seed: number
  horizons: ShuffleHorizonResult[]
  verdict: string
}

// ---------- 分 Agent 记分卡 ----------

export interface AgentStat {
  agent: string
  label: string
  samples: number
  hit_rate: number | null
  avg_return_pct: number | null
  t_stat: number | null
  avg_confidence: number | null
  verdict: string
}

export interface AgentScorecard {
  horizon: number
  horizon_source: 'auto' | 'manual'
  agents: AgentStat[]
  composite: AgentStat | null
  verdict: string
}

export interface BacktestMetrics {
  fund_code: FundCode
  fund_name: string
  start_date: TradeDate
  end_date: TradeDate
  trading_days: number
  total_return_pct: number
  annualized_return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  win_rate_pct: number
  trade_count: number
  strategy_curve: { trade_date: TradeDate; nav: number; benchmark: number }[]
}

export interface PipelineRunResult {
  trade_date: TradeDate | null
  results: (Signal | { fund_code: FundCode; fund_name: string; error: string })[]
}

export interface MarketIndex {
  code: string
  name: string
  close: number
  change_pct: number
}

export interface OverviewStats {
  funds: number
  holding_funds: number
  today_signals: number
  today_logs: number
  nav_latest: string | null
  news_latest: string | null
  notifications_enabled: boolean
  scheduler_enabled: boolean
}

// ---------- 运行保障 ----------

export interface PipelineRunInfo {
  id: number
  run_date: string
  trade_date: string | null
  status: string
  ok_count: number
  fail_count: number
  detail: string
  finished_at: string
}

export interface RuntimeStatus {
  scheduler: { enabled: boolean; run_at: string; weekdays_only: boolean }
  next_run: string | null
  notifications_active: boolean
  last_run: PipelineRunInfo | null
  latest_backup: { file: string; size_bytes: number; date: string } | null
}

export interface Overview {
  indices: MarketIndex[]
  stats: OverviewStats
}

export interface ImportRow {
  fund_code: FundCode
  position: number
  shares: number
  cost: number
}

export interface ImportResult {
  imported: ImportRow[]
  skipped: string[]
}

export interface LLMSettings {
  base_url: string
  api_key_env: string
  api_key: string
  model: string
  model_name: string
  vision_model: string
  timeout_seconds: number
  fallback_enabled: boolean
  debate_enabled: boolean
  sentiment_samples: number
  cache_ttl_minutes: number
}

export interface StrategySettings {
  weights: { technical: number; valuation: number; sentiment: number; risk: number }
  thresholds: { buy: number; sell: number }
  momentum_windows: number[]
  momentum_windows_weights: number[]
  valuation_percentile_window: number
  risk: { max_single_fund_position: number; max_drawdown: number; cash_buffer: number }
  ic_weighting_enabled?: boolean
  ic_min_samples?: number
  whipsaw_cooldown_days?: number
  min_trade_change?: number
}

export interface SchedulerSettings {
  enabled: boolean
  run_at: string
  weekdays_only: boolean
  backup_keep_days?: number
}

export interface NotificationSettings {
  enabled: boolean
  feishu_webhook: string
  wecom_webhook: string
  email_to: string[]
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_password_env: string
  email_from: string
}

export interface DebateSettings {
  rounds: number
  consensus_early_stop: boolean
}

export interface BacktestSettings {
  buy_cost: number
  sell_cost: number
  risk_free_rate: number
}

export type SettingsSection =
  | 'llm' | 'settings' | 'scheduler' | 'notifications' | 'debate' | 'backtest'

export interface SettingsBundle {
  llm: LLMSettings
  settings: StrategySettings
  scheduler: SchedulerSettings
  notifications: NotificationSettings
  debate: DebateSettings
  backtest: BacktestSettings
  funds: FundInfo[]
  overridden: SettingsSection[]
}

export interface PenetrateInfo {
  fund: FundInfo
  index: {
    code: string
    name: string
    pe: number | null
    pe_percentile: number | null
    price_proxy?: boolean
    percentile_source?: 'history' | 'external' | 'proxy' | 'none'
  }
  holdings: HoldingItem[]
  estimate: IntradayEstimate
}
