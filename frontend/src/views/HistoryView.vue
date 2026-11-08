<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { FundInfo, RiskCheck, SignalHistoryItem } from '@/api/types'

interface HistoryRow extends SignalHistoryItem {
  reasons: string[]
  risk_checks: RiskCheck[]
  risk_passed: boolean
}

const funds = ref<FundInfo[]>([])
const fundCode = ref<string>('')
const rows = ref<SignalHistoryItem[]>([])
const loading = ref(false)

const tableData = computed<HistoryRow[]>(() =>
  rows.value.map((r) => ({
    ...r,
    reasons: JSON.parse(r.reasons_json || '[]') as string[],
    risk_checks: JSON.parse(r.risk_checks_json || '[]') as RiskCheck[],
    risk_passed: (JSON.parse(r.risk_checks_json || '[]') as RiskCheck[]).every((c) => c.passed),
  })),
)

watch(fundCode, load, { immediate: true })
api.listFunds().then((f) => {
  funds.value = f
  if (!fundCode.value) fundCode.value = f[0]?.code ?? ''
})

async function load(): Promise<void> {
  if (!fundCode.value) return
  loading.value = true
  try {
    rows.value = await api.signalHistory(fundCode.value, 90)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <div class="card-title">
        信号历史
        <el-select v-model="fundCode" style="width: 280px" size="small">
          <el-option v-for="f in funds" :key="f.code" :value="f.code" :label="`${f.name} (${f.code})`" />
        </el-select>
      </div>
    </template>
    <el-table :data="tableData" size="small" stripe>
      <el-table-column prop="trade_date" label="日期" width="110" />
      <el-table-column label="信号" width="80">
        <template #default="{ row }">
          <el-tag :type="row.action === 'BUY' ? 'danger' : row.action === 'SELL' ? 'success' : 'warning'"
                  effect="dark" size="small">{{ row.action }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="综合分" width="90">
        <template #default="{ row }">
          <span :class="row.composite_score >= 0 ? 'text-pos' : 'text-neg'">{{ row.composite_score.toFixed(3) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="建议仓位" width="90">
        <template #default="{ row }">{{ (row.target_position * 100).toFixed(1) }}%</template>
      </el-table-column>
      <el-table-column label="技术/估值/消息/风险" width="170">
        <template #default="{ row }">
          {{ row.score_technical.toFixed(2) }} / {{ row.score_valuation.toFixed(2) }} /
          {{ row.score_sentiment.toFixed(2) }} / {{ row.score_risk.toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column label="风控" width="80">
        <template #default="{ row }">
          <span :class="row.risk_passed ? 'ok-text' : 'no-text'">{{ row.risk_passed ? '通过' : '拦截' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="决策依据" min-width="280">
        <template #default="{ row }">
          <div class="reasons">{{ row.reasons.join('；') }}</div>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<style scoped>
.ok-text { color: var(--sell); font-weight: 600; }
.no-text { color: var(--buy); font-weight: 600; }
.reasons { font-size: 12px; color: var(--muted); line-height: 1.6; }
</style>
