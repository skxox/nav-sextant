<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '@/api/client'
import { useChart } from '@/composables/useChart'
import { useLatestRun } from '@/composables/useLatestRun'
import { usePanelStore } from '@/stores/panel'
import type { IndexHistoryData, FundCode } from '@/api/types'
import type { EChartsOption } from 'echarts'
import {
  UP_COLOR, DOWN_COLOR, axisLabelStyle, axisLineStyle, splitLineStyle, baseTooltip, dataZoomCfg,
} from '@/utils/chartTheme'

const props = defineProps<{ fundCode: FundCode; days?: number }>()

const store = usePanelStore()
const data = ref<IndexHistoryData | null>(null)
const option = ref<EChartsOption | null>(null)
const latest = useLatestRun()

const indexName = computed(() => {
  const fund = store.funds.find((f) => f.code === props.fundCode)
  return data.value?.name ?? fund?.benchmark_index ?? ''
})

// 区间涨跌幅
const rangeChange = computed(() => {
  const rows = data.value?.rows ?? []
  if (rows.length < 2) return null
  const first = rows[0].close, last = rows[rows.length - 1].close
  return first ? (last - first) / first * 100 : null
})

watch(
  () => [props.fundCode, props.days] as const,
  async ([code, days]) => {
    if (!code) return
    const fund = store.funds.find((f) => f.code === code)
    let payload: IndexHistoryData
    try {
      const res = await latest(() => api.indexHistory(fund?.benchmark_index ?? '000300', days ?? 250))
      if (res.stale) return
      payload = res.value
    } catch {
      return
    }
    data.value = payload
    const rows = payload.rows
    if (!rows.length) { option.value = null; return }
    const closes = rows.map((r) => r.close)
    const up = rangeChange.value != null && rangeChange.value >= 0

    option.value = {
      tooltip: baseTooltip({
        formatter: (params: unknown) => {
          const arr = params as { axisValue: string; value: number }[]
          return `${arr[0]?.axisValue}<br/>${indexName.value} ${Number(arr[0]?.value).toFixed(2)}`
        },
      }),
      grid: { left: 60, right: 16, top: 34, bottom: 56 },
      xAxis: { type: 'category', data: rows.map((r) => r.trade_date), boundaryGap: false,
               axisLine: axisLineStyle, axisLabel: axisLabelStyle, axisTick: { show: false } },
      yAxis: { type: 'value', scale: true, axisLabel: axisLabelStyle,
               splitLine: splitLineStyle, axisLine: { show: false }, axisTick: { show: false } },
      dataZoom: dataZoomCfg(),
      series: [{
        name: indexName.value,
        type: 'line',
        data: closes,
        showSymbol: false,
        lineStyle: { color: up ? UP_COLOR : DOWN_COLOR, width: 1.8 },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: up ? 'rgba(239,83,80,0.22)' : 'rgba(38,166,154,0.22)' },
              { offset: 1, color: 'rgba(0,0,0,0)' },
            ],
          },
        },
      }],
      title: {
        top: 0, left: 8,
        textStyle: { color: 'rgba(255,255,255,0.7)', fontSize: 11, fontWeight: 400 },
        text: `${indexName.value} 区间 ${rangeChange.value != null ? (rangeChange.value >= 0 ? '+' : '') + rangeChange.value.toFixed(2) + '%' : '—'}`,
      },
    }
  },
  { immediate: true },
)

const { el } = useChart(option)
</script>

<template>
  <div v-if="option" ref="el" class="chart-box" />
  <el-empty description="暂无板块指数数据：运行一次流水线后会自动采集" :image-size="60" />
</template>
