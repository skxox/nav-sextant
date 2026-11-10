<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api/client'
import type { ExecutionStats, ExecutionRecord, FundInfo, RiskCheck, SignalHistoryItem } from '@/api/types'

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

// ---------- 决策日志（执行记录）----------
const execRows = ref<ExecutionRecord[]>([])
const execStats = ref<ExecutionStats | null>(null)
const execLoading = ref(false)

async function loadExecutions(): Promise<void> {
  execLoading.value = true
  try {
    const r = await api.listExecutions(undefined, 100)
    execRows.value = r.rows
    execStats.value = r.stats
  } finally {
    execLoading.value = false
  }
}
loadExecutions()

async function removeExec(row: ExecutionRecord): Promise<void> {
  await api.deleteExecution(row.id)
  ElMessage.success('已删除')
  await loadExecutions()
}

const ACTION_TAG: Record<string, { label: string; type: 'danger' | 'success' | 'info' }> = {
  BUY: { label: '买入', type: 'danger' },
  SELL: { label: '卖出', type: 'success' },
  SKIP: { label: '跳过', type: 'info' },
}
</script>

<template>
  <div class="history-page">
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

    <el-card shadow="never" v-loading="execLoading">
      <template #header>
        <div class="card-title">
          决策日志
          <span v-if="execStats" class="text-muted" style="font-weight: 400; font-size: 12px">
            可执行信号 {{ execStats.actionable }} · 已执行 {{ execStats.executed }} · 跳过 {{ execStats.skipped }}
            · 待处理 {{ execStats.unhandled }}
            <template v-if="execStats.exec_rate !== null"> · 执行率 {{ (execStats.exec_rate * 100).toFixed(0) }}%</template>
          </span>
        </div>
      </template>
      <el-empty v-if="!execRows.length" description="暂无执行记录——在「每日信号」页对 BUY/SELL 信号点击「记录执行」或「跳过」" :image-size="60" />
      <el-table v-else :data="execRows" size="small" stripe>
        <el-table-column prop="exec_date" label="执行日" width="100" />
        <el-table-column label="基金" min-width="150">
          <template #default="{ row }">{{ row.fund_name }}<span class="code text-muted"> {{ row.fund_code }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="70">
          <template #default="{ row }">
            <el-tag :type="ACTION_TAG[row.action]?.type" effect="dark" size="small">{{ ACTION_TAG[row.action]?.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="对应信号" width="140">
          <template #default="{ row }">
            <span v-if="row.signal_date">{{ row.signal_date }} · {{ row.signal_action }}</span>
            <span v-else class="text-muted">人工操作</span>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="110" align="right">
          <template #default="{ row }">
            <span v-if="row.amount">¥{{ row.amount.toLocaleString() }}</span>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="成交净值" width="90" align="right">
          <template #default="{ row }">
            <span v-if="row.nav">{{ row.nav.toFixed(4) }}{{ row.nav_source === 'manual' ? '*' : '' }}</span>
            <span v-else-if="row.action !== 'SKIP'" class="text-muted">待确认</span>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="份额" width="100" align="right">
          <template #default="{ row }">
            <span v-if="row.shares">{{ row.shares.toLocaleString() }}</span>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
        <el-table-column width="60">
          <template #default="{ row }">
            <el-popconfirm title="删除这条执行记录？" @confirm="removeExec(row)">
              <template #reference><el-button link type="danger" size="small">删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.history-page { display: flex; flex-direction: column; gap: 16px; }
.ok-text { color: var(--sell); font-weight: 600; }
.no-text { color: var(--buy); font-weight: 600; }
.reasons { font-size: 12px; color: var(--muted); line-height: 1.6; }
.code { font-size: 11px; }
</style>
