<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { LedgerResponse, LedgerRow } from '@/api/types'

const rows = ref<LedgerRow[]>([])
const summary = ref<LedgerResponse['summary'] | null>(null)
const loading = ref(false)

async function refresh(): Promise<void> {
  loading.value = true
  try {
    const resp = await api.ledger()
    rows.value = resp.rows
    summary.value = resp.summary
  } finally { loading.value = false }
}
onMounted(refresh)
defineExpose({ refresh })

const pct = (v: number | null | undefined): string =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
const money = (v: number | null | undefined): string =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const signed = (v: number): string => (v >= 0 ? 'text-pos' : 'text-neg')

const totalDailyPct = computed<number | null>(() => {
  if (!summary.value || !summary.value.market_value) return null
  return (summary.value.daily_pnl / (summary.value.market_value - summary.value.daily_pnl)) * 100
})
</script>

<template>
  <div v-if="summary" class="summary-bar">
    <div class="sum-item">
      <span class="sum-label">总市值</span>
      <span class="sum-value">¥{{ summary.market_value.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
    </div>
    <div class="sum-item">
      <span class="sum-label">当日收益</span>
      <span class="sum-value" :class="signed(summary.daily_pnl)">
        {{ money(summary.daily_pnl) }}
        <i v-if="totalDailyPct != null" style="font-style: normal; font-size: 11px">（{{ pct(totalDailyPct) }}）</i>
      </span>
    </div>
    <div class="sum-item">
      <span class="sum-label">持有收益</span>
      <span class="sum-value" :class="signed(summary.pnl)">
        {{ money(summary.pnl) }}
        <i v-if="summary.pnl_pct != null" style="font-style: normal; font-size: 11px">（{{ pct(summary.pnl_pct) }}）</i>
      </span>
    </div>
  </div>

  <el-table :data="rows" size="small" v-loading="loading">
    <el-table-column label="基金" min-width="150" fixed>
      <template #default="{ row }">
        <div class="fund-cell">
          <span class="fund-name">{{ row.fund_name }}</span>
          <span class="fund-code">{{ row.fund_code }}</span>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="关联板块" width="90">
      <template #default="{ row }">
        <el-tag v-if="row.sector" size="small" effect="dark" round>{{ row.sector }}</el-tag>
        <span v-else class="text-muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="最新净值" width="100" align="right">
      <template #default="{ row }">
        <div>{{ row.latest_nav ?? '—' }}</div>
        <div class="sub text-muted">{{ row.latest_nav_date ?? '' }}</div>
      </template>
    </el-table-column>
    <el-table-column label="当日涨幅" width="100" align="right">
      <template #default="{ row }">
        <span :class="row.daily_change_pct == null ? 'text-muted' : signed(row.daily_change_pct)">
          {{ pct(row.daily_change_pct) }}
          <el-tooltip v-if="row.daily_estimated" content="官方净值未更新，按指数实时行情估算" placement="top">
            <i class="est-flag">估</i>
          </el-tooltip>
        </span>
      </template>
    </el-table-column>
    <el-table-column label="当日收益" width="90" align="right">
      <template #default="{ row }">
        <span v-if="row.daily_pnl != null" :class="signed(row.daily_pnl)">¥{{ money(row.daily_pnl) }}</span>
        <span v-else class="text-muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="持有收益" width="120" align="right">
      <template #default="{ row }">
        <span v-if="row.pnl != null" :class="signed(row.pnl)">
          ¥{{ money(row.pnl) }}（{{ pct(row.pnl_pct) }}）
        </span>
        <span v-else class="text-muted">未录份额</span>
      </template>
    </el-table-column>
    <el-table-column label="份额" width="80" align="right">
      <template #default="{ row }">{{ row.shares > 0 ? row.shares.toLocaleString('zh-CN') : '—' }}</template>
    </el-table-column>
    <el-table-column label="成本价" width="80" align="right">
      <template #default="{ row }">{{ row.cost > 0 ? row.cost : '—' }}</template>
    </el-table-column>
    <el-table-column label="市值" width="100" align="right">
      <template #default="{ row }">
        <span v-if="row.market_value != null">¥{{ row.market_value.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
        <span v-else class="text-muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="仓位" width="70" align="right">
      <template #default="{ row }">{{ (row.position * 100).toFixed(0) }}%</template>
    </el-table-column>
  </el-table>
</template>

<style scoped lang="scss">
.summary-bar {
  display: flex;
  gap: 28px;
  padding: 10px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
  .sum-item { display: flex; flex-direction: column; gap: 2px; }
  .sum-label { font-size: 11px; color: var(--muted); }
  .sum-value { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
}
.fund-cell { display: flex; flex-direction: column; line-height: 1.3; }
.fund-name { font-size: 13px; font-weight: 500; }
.fund-code { font-size: 11px; color: var(--muted); }
.sub { font-size: 10px; }
.est-flag {
  font-style: normal;
  font-size: 10px;
  padding: 0 3px;
  margin-left: 2px;
  border: 1px solid var(--muted);
  border-radius: 3px;
  color: var(--muted);
}
</style>
