import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api/client'
import type { AgentLog, FundInfo, MetaInfo, Signal, FundCode, AgentName } from '@/api/types'

export const AGENT_LABELS: Record<AgentName, string> = {
  technical: '技术面',
  valuation: '估值面',
  sentiment: '消息面',
  risk: '风险面',
}

export const usePanelStore = defineStore('panel', () => {
  const funds = ref<FundInfo[]>([])
  const signals = ref<Signal[]>([])
  const agentLogs = ref<AgentLog[]>([])
  const positions = ref<Record<FundCode, number>>({})
  const selectedFund = ref<FundCode | null>(null)
  const loading = ref(false)
  const pipelineRunning = ref(false)
  const meta = ref<MetaInfo | null>(null)

  const modelName = computed(() => meta.value?.llm.model_name ?? '未配置')

  const selectedSignal = computed(() =>
    signals.value.find((s) => s.fund_code === selectedFund.value) ?? null,
  )

  function agentsOf(code: FundCode): AgentLog[] {
    return agentLogs.value.filter((a) => a.fund_code === code)
  }

  async function loadAll(): Promise<void> {
    loading.value = true
    try {
      const [f, s, logs, pos] = await Promise.all([
        api.listFunds(),
        api.latestSignals(),
        api.agentLogs(),
        api.positions(),
      ])
      funds.value = f
      signals.value = s
      agentLogs.value = logs
      const map: Record<FundCode, number> = {}
      for (const p of pos) map[p.fund_code] = p.position
      positions.value = map
      if (!selectedFund.value) {
        selectedFund.value = s[0]?.fund_code ?? f[0]?.code ?? null
      }
    } finally {
      loading.value = false
    }
    api.meta().then((m) => { meta.value = m }).catch(() => {})
  }

  async function runPipeline(): Promise<{ ok: number; failed: number }> {
    pipelineRunning.value = true
    try {
      const result = await api.runPipeline()
      await loadAll()
      const ok = result.results.filter((r) => !('error' in r)).length
      return { ok, failed: result.results.length - ok }
    } finally {
      pipelineRunning.value = false
    }
  }

  async function savePosition(code: FundCode, position: number): Promise<void> {
    await api.savePosition(code, position)
    positions.value = { ...positions.value, [code]: position }
  }

  async function loadMetaSafe(): Promise<void> {
    try { meta.value = await api.meta() } catch { /* 后端不可用时静默 */ }
  }

  return {
    funds, signals, agentLogs, positions, selectedFund, loading, pipelineRunning, meta, modelName,
    selectedSignal, agentsOf, loadAll, loadMetaSafe, runPipeline, savePosition,
  }
})
