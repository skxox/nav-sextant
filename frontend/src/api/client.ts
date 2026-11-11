import axios from 'axios'
import { ElMessage } from 'element-plus'
import type {
  AgentLog,
  BacktestMetrics,
  FundInfo,
  FundPosition,
  HoldingItem,
  ImportResult,
  IntradayEstimate,
  LedgerResponse,
  KlineData,
  IndexHistoryData,
  PerformanceData,
  ProfitData,
  FactorEvalResult,
  MetaInfo,
  NavPoint,
  Overview,
  PenetrateInfo,
  PipelineRunResult,
  RuntimeStatus,
  SettingsBundle,
  SettingsSection,
  Signal,
  SignalHistoryItem,
  SignalReviewReport,
  ShuffleTestReport,
  AgentScorecard,
  ICABReport,
  FundCode,
  TradeDate,
  ExecutionsResponse,
  ExecutionRecord,
  ExecutionCreateBody,
} from './types'

const http = axios.create({ baseURL: '/api', timeout: 300_000 })

http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '请求失败，请检查后端服务')
    return Promise.reject(error)
  },
)

export const api = {
  async meta(): Promise<MetaInfo> {
    return (await http.get<MetaInfo>('/meta')).data
  },
  async listFunds(): Promise<FundInfo[]> {
    return (await http.get<FundInfo[]>('/funds')).data
  },
  async latestSignals(): Promise<Signal[]> {
    return (await http.get<Signal[]>('/signals/latest')).data
  },
  async signalHistory(code: FundCode, limit = 60): Promise<SignalHistoryItem[]> {
    return (await http.get<SignalHistoryItem[]>(`/signals/history/${code}`, { params: { limit } })).data
  },
  async navHistory(code: FundCode, limit = 250): Promise<NavPoint[]> {
    return (await http.get<NavPoint[]>(`/nav/${code}`, { params: { limit } })).data
  },
  async agentLogs(tradeDate?: TradeDate): Promise<AgentLog[]> {
    return (await http.get<AgentLog[]>('/agent-logs', { params: tradeDate ? { trade_date: tradeDate } : {} })).data
  },
  async positions(): Promise<FundPosition[]> {
    return (await http.get<FundPosition[]>('/positions')).data
  },
  async savePosition(code: FundCode, position: number, shares = 0, cost = 0): Promise<void> {
    await http.post('/position', { fund_code: code, position, shares, cost })
  },
  async ledger(): Promise<LedgerResponse> {
    return (await http.get<LedgerResponse>('/positions/ledger')).data
  },
  async holdings(code: FundCode): Promise<HoldingItem[]> {
    return (await http.get<HoldingItem[]>(`/holdings/${code}`)).data
  },
  async intradayEstimate(code: FundCode): Promise<IntradayEstimate> {
    return (await http.get<IntradayEstimate>(`/estimate/${code}`)).data
  },
  async runPipeline(): Promise<PipelineRunResult> {
    return (await http.post<PipelineRunResult>('/pipeline/run')).data
  },
  async runBacktest(code: FundCode): Promise<BacktestMetrics> {
    return (await http.post<BacktestMetrics>(`/backtest/${code}`)).data
  },
  async overview(): Promise<Overview> {
    return (await http.get<Overview>('/overview')).data
  },
  async runtime(): Promise<RuntimeStatus> {
    return (await http.get<RuntimeStatus>('/runtime')).data
  },
  async importText(text: string): Promise<ImportResult> {
    return (await http.post<ImportResult>('/import/text', { text })).data
  },
  async importImage(file: File): Promise<ImportResult> {
    const form = new FormData()
    form.append('file', file)
    return (await http.post<ImportResult>('/import/image', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })).data
  },
  async getSettings(): Promise<SettingsBundle> {
    return (await http.get<SettingsBundle>('/settings')).data
  },
  async updateSettings(section: SettingsSection, patch: Record<string, unknown>): Promise<void> {
    await http.put(`/settings/${section}`, { [section]: patch })
  },
  async resetSettings(section: SettingsSection): Promise<void> {
    await http.delete(`/settings/${section}`)
  },
  async addFund(body: { code: string; name: string; benchmark_index: string; sector?: string; valuation_index?: string }): Promise<void> {
    await http.post('/funds', { sector: '', valuation_index: '', ...body })
  },
  async deleteFund(code: FundCode): Promise<void> {
    await http.delete(`/funds/${code}`)
  },
  async deletePosition(code: FundCode): Promise<void> {
    await http.delete(`/position/${code}`)
  },
  async penetrate(code: FundCode): Promise<PenetrateInfo> {
    return (await http.get<PenetrateInfo>(`/penetrate/${code}`)).data
  },
  async kline(code: string, days = 250): Promise<KlineData> {
    return (await http.get<KlineData>(`/index/kline/${code}`, { params: { days } })).data
  },
  async indexHistory(code: string, days = 250): Promise<IndexHistoryData> {
    return (await http.get<IndexHistoryData>(`/index/history/${code}`, { params: { days } })).data
  },
  async performance(code: FundCode, days = 250): Promise<PerformanceData> {
    return (await http.get<PerformanceData>(`/fund/performance/${code}`, { params: { days } })).data
  },
  async portfolioProfit(days = 250): Promise<ProfitData> {
    return (await http.get<ProfitData>('/portfolio/profit', { params: { days } })).data
  },
  async factorsEval(code: FundCode, forwardDays = 5): Promise<FactorEvalResult> {
    return (await http.get<FactorEvalResult>(`/factors/eval/${code}`, { params: { forward_days: forwardDays } })).data
  },
  async signalReview(code?: FundCode): Promise<SignalReviewReport> {
    return (await http.get<SignalReviewReport>('/review/signals', { params: code ? { fund_code: code } : {} })).data
  },
  async shuffleTest(code?: FundCode, nTrials = 300): Promise<ShuffleTestReport> {
    return (await http.get<ShuffleTestReport>('/review/shuffle', {
      params: { ...(code ? { fund_code: code } : {}), n_trials: nTrials },
    })).data
  },
  async agentScorecard(code?: FundCode): Promise<AgentScorecard> {
    return (await http.get<AgentScorecard>('/review/agents', {
      params: code ? { fund_code: code } : {},
    })).data
  },
  async icABTest(code?: FundCode): Promise<ICABReport> {
    return (await http.get<ICABReport>('/review/ic', {
      params: code ? { fund_code: code } : {},
    })).data
  },
  async listExecutions(code?: FundCode, limit = 100): Promise<ExecutionsResponse> {
    return (await http.get<ExecutionsResponse>('/executions', {
      params: { ...(code ? { fund_code: code } : {}), limit },
    })).data
  },
  async recordExecution(body: ExecutionCreateBody): Promise<ExecutionRecord> {
    return (await http.post<ExecutionRecord>('/executions', body)).data
  },
  async deleteExecution(id: number): Promise<void> {
    await http.delete(`/executions/${id}`)
  },
}
