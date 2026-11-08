<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import { usePanelStore } from '@/stores/panel'
import { useChart } from '@/composables/useChart'
import NavChart from '@/components/NavChart.vue'
import KlineChart from '@/components/KlineChart.vue'
import PerformanceChart from '@/components/PerformanceChart.vue'
import ProfitChart from '@/components/ProfitChart.vue'
import SectorChart from '@/components/SectorChart.vue'
import PositionManager from '@/components/PositionManager.vue'
import LedgerPanel from '@/components/LedgerPanel.vue'
import type { FundCode, HoldingItem, PenetrateInfo } from '@/api/types'
import type { EChartsOption } from 'echarts'

type ChartTab = 'nav' | 'kline' | 'performance' | 'profit' | 'sector'

const store = usePanelStore()
const penetrate = ref<PenetrateInfo | null>(null)
const holdings = ref<HoldingItem[]>([])
const loadingPen = ref(false)

const chartTab = ref<ChartTab>('nav')
const range = ref(250)
const profitVersion = ref(0)
const ranges = [
  { label: '1月', days: 22 },
  { label: '3月', days: 66 },
  { label: '6月', days: 130 },
  { label: '1年', days: 250 },
  { label: '全部', days: 900 },
]

const tabMeta: Record<ChartTab, { label: string; hint: string }> = {
  nav: { label: '净值走势', hint: '单位净值与均线，含历史买卖信号' },
  kline: { label: 'K线', hint: '关联基准指数日K（开高低收 + 均线 + 成交量）' },
  performance: { label: '业绩对比', hint: '基金与基准指数起点归一对比（超额收益一目了然）' },
  profit: { label: '我的收益', hint: '全部持仓的累计收益与收益率曲线' },
  sector: { label: '关联板块', hint: '关联板块（基准）指数走势' },
}

const benchmarkIndex = computed(() =>
  store.funds.find((f) => f.code === store.selectedFund)?.benchmark_index ?? '000300')

async function loadPenetrate(code: FundCode): Promise<void> {
  if (!code) return
  loadingPen.value = true
  try {
    penetrate.value = await api.penetrate(code)
    holdings.value = penetrate.value.holdings
  } finally {
    loadingPen.value = false
  }
}

// 前十大重仓权重条形图
const weightOption = computed<EChartsOption | null>(() => {
  const rows = [...holdings.value].sort((a, b) => a.weight - b.weight)
  if (!rows.length) return null
  return {
    tooltip: { trigger: 'axis', formatter: (p: unknown) => {
      const arr = p as { name: string; value: number }[]
      return `${arr[0]?.name}<br/>占净值 ${Number(arr[0]?.value).toFixed(2)}%`
    } },
    grid: { left: 90, right: 40, top: 8, bottom: 24 },
    xAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
    yAxis: { type: 'category', data: rows.map((r) => r.stock_name || r.stock_code),
             axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar',
      data: rows.map((r) => r.weight),
      barWidth: 12,
      itemStyle: { color: '#4d7cfe', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', fontSize: 10, formatter: '{c}%' },
    }],
  }
})

const { el } = useChart(weightOption)

const peTrend = computed(() => {
  const p = penetrate.value?.index.pe_percentile
  if (p == null) return { text: '—', cls: 'text-muted' }
  if (p <= 25) return { text: '低估', cls: 'text-pos' }
  if (p >= 75) return { text: '高估', cls: 'text-neg' }
  return { text: '中性', cls: 'text-muted' }
})

function onLedgerChanged(): void {
  void store.loadAll()
  // 持仓变动后让收益曲线重新拉数（用 key 触发重挂载，避免依赖 tab 抖动）
  profitVersion.value += 1
}

watch(() => store.selectedFund, (c) => { if (c) void loadPenetrate(c) })

onMounted(() => {
  if (!store.funds.length) {
    void store.loadAll().then(() => { if (store.selectedFund) void loadPenetrate(store.selectedFund) })
  } else if (store.selectedFund) {
    void loadPenetrate(store.selectedFund)
  }
})
</script>

<template>
  <!-- 全宽：金融图表工作台 -->
  <el-card shadow="never" class="chart-card">
    <template #header>
      <div class="chart-head">
        <el-radio-group v-model="chartTab" size="small">
          <el-radio-button v-for="(m, k) in tabMeta" :key="k" :value="k">{{ m.label }}</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="range" size="small" class="ranges">
          <el-radio-button v-for="r in ranges" :key="r.days" :value="r.days">{{ r.label }}</el-radio-button>
        </el-radio-group>
      </div>
      <div class="chart-hint text-muted">
        {{ tabMeta[chartTab].hint }}
        <template v-if="chartTab === 'kline' || chartTab === 'sector' || chartTab === 'performance'">
          · 基准 <b>{{ benchmarkIndex }}</b>
        </template>
      </div>
    </template>
    <div class="chart-stage">
      <NavChart v-if="chartTab === 'nav' && store.selectedFund" :fund-code="store.selectedFund" :days="range" />
      <KlineChart v-else-if="chartTab === 'kline'" :index-code="benchmarkIndex" :days="range" />
      <PerformanceChart v-else-if="chartTab === 'performance' && store.selectedFund" :fund-code="store.selectedFund" :days="range" />
      <ProfitChart v-else-if="chartTab === 'profit'" :key="profitVersion" :days="range" />
      <SectorChart v-else-if="chartTab === 'sector' && store.selectedFund" :fund-code="store.selectedFund" :days="range" />
      <el-empty v-else description="请先在设置中添加基金" :image-size="60" />
    </div>
  </el-card>

  <div class="holdings">
    <div class="col">
      <el-card shadow="never">
        <template #header>
          <div class="card-title">
            基金穿透
            <template v-if="penetrate">
              <el-tag size="small" effect="plain">{{ penetrate.index.name }}（{{ penetrate.index.code }}）</el-tag>
              <span class="text-muted" style="font-size: 12px">
                <template v-if="penetrate.index.price_proxy">价格 {{ penetrate.index.pe?.toFixed(2) ?? '—' }} · 分位(代理)</template>
                <template v-else>PE {{ penetrate.index.pe?.toFixed(2) ?? '—' }} · 分位<el-tooltip v-if="penetrate.index.percentile_source === 'external'" content="蛋卷近10年分位快照（自有PE历史累积中）" placement="top"><el-tag size="small" type="info" effect="plain" style="margin-left: 4px">外部分位</el-tag></el-tooltip></template>
                <b :class="peTrend.cls">{{ penetrate.index.pe_percentile?.toFixed(1) ?? '—' }}%（{{ peTrend.text }}）</b>
              </span>
              <span v-if="penetrate.estimate.estimated_change_pct != null" class="text-muted" style="font-size: 12px">
                盘中估算
                <b :class="penetrate.estimate.estimated_change_pct >= 0 ? 'text-pos' : 'text-neg'">
                  {{ penetrate.estimate.estimated_change_pct >= 0 ? '+' : '' }}{{ penetrate.estimate.estimated_change_pct.toFixed(2) }}%
                </b>
              </span>
            </template>
          </div>
        </template>
        <el-table v-loading="loadingPen" :data="holdings" size="small" max-height="300">
          <el-table-column prop="stock_code" label="代码" width="100" />
          <el-table-column prop="stock_name" label="重仓股" min-width="140" />
          <el-table-column prop="weight" label="占净值比" width="110" align="right">
            <template #default="{ row }">{{ row.weight.toFixed(2) }}%</template>
          </el-table-column>
        </el-table>
        <div v-if="holdings[0]" class="text-muted" style="font-size: 12px; margin-top: 6px">
          {{ holdings[0].report_date }} 期前十大重仓 · 合计占比
          {{ holdings.reduce((s, h) => s + h.weight, 0).toFixed(1) }}%
        </div>
      </el-card>

      <el-card shadow="never">
        <template #header><div class="card-title">重仓股权重分布</div></template>
        <div v-if="weightOption" ref="el" class="chart-box-sm" />
        <el-empty v-else description="暂无持仓数据：运行一次流水线后会自动采集基金季报持仓" :image-size="60" />
      </el-card>
    </div>

    <div class="col">
      <el-card shadow="never">
        <template #header><div class="card-title">持仓对账（份额 × 最新净值 → 市值与盈亏）</div></template>
        <LedgerPanel />
      </el-card>

      <el-card shadow="never">
        <template #header><div class="card-title">持仓维护（风控现金缓冲依据）</div></template>
        <PositionManager @changed="onLedgerChanged" />
      </el-card>
    </div>
  </div>
</template>

<style scoped lang="scss">
.chart-card { margin-bottom: 16px; }
.chart-head {
  display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;
  .ranges { margin-left: auto; }
}
.chart-hint { font-size: 11px; margin-top: 6px; }
.chart-stage {
  height: 420px;
  // 图表铺满舞台高度（K 线的双 grid 需要更多纵向空间）
  :deep(.chart-box) { height: 100%; }
}
.holdings { display: grid; grid-template-columns: minmax(0, 1fr) 480px; gap: 16px; }
@media (max-width: 1020px) { .holdings { grid-template-columns: 1fr; } }
.col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
</style>
