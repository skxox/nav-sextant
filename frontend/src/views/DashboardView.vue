<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { usePanelStore } from '@/stores/panel'
import { useChart } from '@/composables/useChart'
import type { Overview, LedgerResponse, RuntimeStatus } from '@/api/types'
import type { EChartsOption } from 'echarts'

const router = useRouter()
const store = usePanelStore()

const overview = ref<Overview | null>(null)
const runtime = ref<RuntimeStatus | null>(null)
const ledger = ref<LedgerResponse>({ rows: [], summary: { market_value: 0, daily_pnl: 0, pnl: 0, cost_total: 0, pnl_pct: null } })
const quickCode = ref('')

const stats = computed(() => overview.value?.stats)

const donutOption = computed<EChartsOption | null>(() => {
  const rows = ledger.value.rows.filter((r) => (r.market_value ?? 0) > 0)
  if (!rows.length) return null
  return {
    tooltip: { trigger: 'item', formatter: '{b}<br/>市值 ¥{c}（{d}%）' },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['46%', '70%'],
      center: ['50%', '44%'],
      itemStyle: { borderColor: 'var(--panel)' },
      label: { formatter: '{d}%', fontSize: 11 },
      data: rows.map((r) => ({ name: r.fund_name || r.fund_code, value: Number(r.market_value) })),
    }],
  }
})

const RUN_STATUS_LABEL: Record<string, { label: string; ok: boolean }> = {
  ok: { label: '成功', ok: true },
  partial: { label: '部分失败', ok: false },
  failed: { label: '失败', ok: false },
  skipped_non_trading_day: { label: '非交易日跳过', ok: true },
  already_done: { label: '当日已完成', ok: true },
}

const healthRows = computed(() => {
  const s = stats.value
  if (!s) return []
  const rt = runtime.value
  const last = rt?.last_run
  const lastLabel = last
    ? `${last.finished_at.slice(5, 16)} · ${RUN_STATUS_LABEL[last.status]?.label ?? last.status}`
    : '从未运行'
  const backup = rt?.latest_backup
  return [
    { name: '净值数据最新日期', value: s.nav_latest ?? '—', ok: !!s.nav_latest },
    { name: '新闻数据最新时间', value: s.news_latest ?? '—', ok: !!s.news_latest },
    { name: '每日定时调度', value: s.scheduler_enabled ? `已启用 · ${rt?.scheduler.run_at ?? ''}` : '手动触发', ok: s.scheduler_enabled },
    { name: '下次自动运行', value: rt?.next_run ? rt.next_run.slice(5, 16) : '—', ok: !!rt?.next_run },
    { name: '最近流水线运行', value: lastLabel, ok: !!last && (RUN_STATUS_LABEL[last.status]?.ok ?? false) },
    { name: '数据备份', value: backup ? `${backup.date} · ${(backup.size_bytes / 1024).toFixed(0)}KB` : '无备份', ok: !!backup },
    { name: '信号告警推送', value: s.notifications_enabled ? '已启用' : '未配置', ok: s.notifications_enabled },
  ]
})

const idxClass = (v: number): string => (v >= 0 ? 'text-pos' : 'text-neg')

const { el } = useChart(donutOption)

async function quickRun(): Promise<void> {
  if (quickCode.value) store.selectedFund = quickCode.value
  router.push('/signals')
}

onMounted(async () => {
  api.overview().then((o) => { overview.value = o }).catch(() => {})
  api.runtime().then((r) => { runtime.value = r }).catch(() => {})
  api.ledger().then((l) => { ledger.value = { rows: l.rows, summary: l.summary } }).catch(() => {})
  if (!store.signals.length) void store.loadAll()
})
</script>

<template>
  <div class="dashboard" v-loading="!overview">
    <!-- 指数概览 -->
    <div class="index-row">
      <div v-for="idx in overview?.indices ?? []" :key="idx.code" class="index-card">
        <div class="idx-name">{{ idx.name }}</div>
        <div class="idx-close" :class="idxClass(idx.change_pct)">{{ idx.close.toFixed(2) }}</div>
        <div class="idx-chg" :class="idxClass(idx.change_pct)">
          {{ idx.change_pct >= 0 ? '+' : '' }}{{ idx.change_pct.toFixed(2) }}%
          {{ idx.change_pct >= 0 ? '↑' : '↓' }}
        </div>
      </div>
      <div v-if="overview && !overview.indices.length" class="index-card">
        <div class="idx-name">指数行情</div>
        <div class="idx-close text-muted" style="font-size: 16px">数据源暂不可用</div>
      </div>
    </div>

    <!-- 统计卡 -->
    <div class="stat-row">
      <div class="stat-card">
        <div class="stat-label">自选基金</div>
        <div class="stat-value" style="color: var(--accent)">{{ stats?.funds ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">持仓基金</div>
        <div class="stat-value" style="color: var(--warn)">{{ stats?.holding_funds ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">今日操作信号</div>
        <div class="stat-value" style="color: var(--buy)">{{ stats?.today_signals ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">今日 Agent 调用</div>
        <div class="stat-value">{{ stats?.today_logs ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">AI 供应商</div>
        <div class="stat-value" style="color: var(--accent); font-size: 15px; line-height: 1.5">
          {{ store.modelName }}
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">数据状态</div>
        <div class="stat-value" style="color: var(--sell)">
          {{ stats?.nav_latest ? '在线' : '离线' }}
        </div>
      </div>
    </div>

    <!-- 图表区 -->
    <div class="chart-row">
      <el-card shadow="never" class="grow">
        <template #header><div class="card-title">持仓分布（按市值）</div></template>
        <div v-if="donutOption" ref="el" class="chart-box" />
        <el-empty v-else description="暂无对账数据：请先在「持仓管理」录入份额与成本" :image-size="70" />
      </el-card>

      <el-card shadow="never" class="grow">
        <template #header><div class="card-title">最新信号摘要</div></template>
        <div v-if="store.signals.length" class="sig-list">
          <div v-for="s in store.signals" :key="s.fund_code" class="sig-row" @click="router.push('/signals')">
            <span class="sig-name">{{ s.fund_name }}</span>
            <el-tag :type="s.action === 'BUY' ? 'danger' : s.action === 'SELL' ? 'success' : 'warning'"
                    size="small" effect="dark">
              {{ { BUY: '买入', SELL: '卖出', HOLD: '持有' }[s.action] }}
            </el-tag>
            <span :class="s.composite_score >= 0 ? 'text-pos' : 'text-neg'" style="margin-left: auto">
              {{ s.composite_score.toFixed(3) }}
            </span>
          </div>
        </div>
        <el-empty v-else description="暂无信号" :image-size="70" />
      </el-card>
    </div>

    <!-- 底部工具区 -->
    <div class="bottom-row">
      <el-card shadow="never" class="grow">
        <template #header><div class="card-title">快速分析</div></template>
        <div class="quick">
          <el-select v-model="quickCode" placeholder="选择基金" style="width: 260px">
            <el-option v-for="f in store.funds" :key="f.code" :value="f.code"
                       :label="`${f.name} (${f.code})`" />
          </el-select>
          <el-button type="primary" @click="quickRun">前往信号分析</el-button>
        </div>
      </el-card>

      <el-card shadow="never" class="grow">
        <template #header><div class="card-title">运行健康</div></template>
        <div class="health">
          <div v-for="h in healthRows" :key="h.name" class="health-row">
            <span class="text-muted">{{ h.name }}</span>
            <span :class="h.ok ? '' : 'text-muted'" style="font-weight: 600">{{ h.value }}</span>
            <el-tag :type="h.ok ? 'success' : 'info'" size="small" effect="plain">
              {{ h.ok ? '正常' : '未就绪' }}
            </el-tag>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped lang="scss">
.dashboard { display: flex; flex-direction: column; gap: 16px; }
.index-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.stat-row {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 16px;
  @media (max-width: 1100px) { grid-template-columns: repeat(3, 1fr); }
}
.chart-row, .bottom-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grow { min-width: 0; }

.sig-list { display: flex; flex-direction: column; }
.sig-row {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 4px; font-size: 13px; cursor: pointer;
  border-bottom: 1px solid var(--border);
  &:last-child { border-bottom: none; }
  &:hover { background: var(--panel-2); }
  .sig-name { font-weight: 600; }
}
.quick { display: flex; gap: 12px; align-items: center; }
.health { display: flex; flex-direction: column; gap: 4px; }
.health-row {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 4px; font-size: 13px;
  border-bottom: 1px dashed var(--border);
  &:last-child { border-bottom: none; }
  span:first-child { flex: 1; }
}
</style>
