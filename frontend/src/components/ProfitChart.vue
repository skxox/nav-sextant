<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '@/api/client'
import { useChart } from '@/composables/useChart'
import { useLatestRun } from '@/composables/useLatestRun'
import type { ProfitData } from '@/api/types'
import type { EChartsOption } from 'echarts'
import {
  UP_COLOR, DOWN_COLOR, axisLabelStyle, axisLineStyle, splitLineStyle, baseTooltip, dataZoomCfg,
} from '@/utils/chartTheme'

const props = defineProps<{ days?: number }>()

const data = ref<ProfitData | null>(null)
const option = ref<EChartsOption | null>(null)
const latest = useLatestRun()

watch(
  () => props.days,
  async (days) => {
    let payload: ProfitData
    try {
      const res = await latest(() => api.portfolioProfit(days ?? 250))
      if (res.stale) return
      payload = res.value
    } catch {
      return
    }
    data.value = payload
    const rows = payload?.rows ?? []
    if (!rows.length) { option.value = null; return }

    const dates = rows.map((r) => r.trade_date)
    const profits = rows.map((r) => r.profit ?? 0)
    const rates = rows.map((r) => r.profit_rate)
    // 分段着色：收益为红、亏损为绿（分块 area）
    const positive = profits.map((v) => (v >= 0 ? v : null))
    const negative = profits.map((v) => (v < 0 ? v : null))

    option.value = {
      tooltip: baseTooltip({
        formatter: (params: unknown) => {
          const arr = params as { axisValue: string; seriesName: string; value: number | null }[]
          const i = dates.indexOf(arr[0]?.axisValue ?? '')
          const p = profits[i]
          const r = rates?.[i]
          const mv = rows[i]?.market_value
          const cls = p >= 0 ? UP_COLOR : DOWN_COLOR
          return `${arr[0]?.axisValue}<br/>` +
            `<span style="color:${cls}">累计收益 ¥${p.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}` +
            `${r != null ? `（${r >= 0 ? '+' : ''}${r.toFixed(2)}%）` : ''}</span><br/>` +
            `持仓市值 ¥${mv?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) ?? '—'}`
        },
      }),
      grid: { left: 64, right: 56, top: 30, bottom: 56 },
      xAxis: { type: 'category', data: dates, boundaryGap: false,
               axisLine: axisLineStyle, axisLabel: axisLabelStyle, axisTick: { show: false } },
      yAxis: [
        { type: 'value', scale: true, name: '收益(¥)', nameTextStyle: { color: 'rgba(255,255,255,0.4)', fontSize: 10 },
          axisLabel: axisLabelStyle, splitLine: splitLineStyle, axisLine: { show: false }, axisTick: { show: false } },
        { type: 'value', scale: true, position: 'right', name: '收益率', nameTextStyle: { color: 'rgba(255,255,255,0.4)', fontSize: 10 },
          axisLabel: { ...axisLabelStyle, formatter: '{value}%' }, splitLine: { show: false },
          axisLine: { show: false }, axisTick: { show: false } },
      ],
      dataZoom: dataZoomCfg(),
      series: [
        {
          name: '累计收益',
          type: 'line',
          data: profits,
          showSymbol: false,
          lineStyle: { color: UP_COLOR, width: 1.8 },
          markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: 'rgba(255,255,255,0.25)', type: 'dashed' },
            label: { show: false },
            data: [{ yAxis: 0 }],
          },
        },
        { name: '盈', type: 'line', data: positive, showSymbol: false, stack: 'p',
          lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(239,83,80,0.18)' }, silent: true },
        { name: '亏', type: 'line', data: negative, showSymbol: false, stack: 'p',
          lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(38,166,154,0.18)' }, silent: true },
        {
          name: '收益率',
          type: 'line',
          yAxisIndex: 1,
          data: rates,
          showSymbol: false,
          lineStyle: { color: 'rgba(255,255,255,0.35)', width: 1, type: 'dashed' },
        },
      ],
    }
  },
  { immediate: true },
)

const { el } = useChart(option)
</script>

<template>
  <div v-if="option" ref="el" class="chart-box" />
  <el-empty v-else description="暂无收益数据：需在持仓中录入份额与成本" :image-size="60" />
</template>
