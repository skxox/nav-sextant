<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '@/api/client'
import { useChart } from '@/composables/useChart'
import { useLatestRun } from '@/composables/useLatestRun'
import type { KlineData } from '@/api/types'
import type { EChartsOption } from 'echarts'
import {
  UP_COLOR, DOWN_COLOR, MA5_COLOR, MA20_COLOR, MA60_COLOR,
  axisLabelStyle, axisLineStyle, splitLineStyle, baseTooltip, ma,
} from '@/utils/chartTheme'

const props = defineProps<{ indexCode: string; days?: number }>()

const data = ref<KlineData | null>(null)
const option = ref<EChartsOption | null>(null)
const latest = useLatestRun()

watch(
  () => [props.indexCode, props.days] as const,
  async ([code, days]) => {
    if (!code) return
    let payload: KlineData
    try {
      const res = await latest(() => api.kline(code, days ?? 250))
      if (res.stale) return
      payload = res.value
    } catch {
      return
    }
    data.value = payload
    const rows = payload.rows
    if (!rows.length) { option.value = null; return }


    const dates = rows.map((r) => r.trade_date)
    const closes = rows.map((r) => r.close ?? 0)
    const candles = rows.map((r) => [r.open, r.close, r.low, r.high])
    const volumes = rows.map((r) => ({
      value: r.volume,
      itemStyle: { color: (r.close ?? 0) >= (r.open ?? 0) ? UP_COLOR : DOWN_COLOR, opacity: 0.6 },
    }))
    const maSeries = (window: number, color: string) => ({
      name: `MA${window}`,
      type: 'line' as const,
      data: ma(closes, window),
      showSymbol: false,
      lineStyle: { color, width: 1 },
      emphasis: { disabled: true },
    })

    option.value = {
      animation: false,
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      tooltip: baseTooltip({
        formatter: (params: unknown) => {
          const arr = params as { axisValue: string; seriesName: string; value: number | number[] }[]
          const d = arr[0]?.axisValue ?? ''
          const k = arr.find((p) => p.seriesName === 'K线')
          if (k && Array.isArray(k.value)) {
            const [o, c, l, h] = k.value
            const color = c >= o ? UP_COLOR : DOWN_COLOR
            const vol = arr.find((p) => p.seriesName === '成交量')
            const maLines = arr.filter((p) => p.seriesName.startsWith('MA'))
              .map((p) => `<br/>${p.seriesName} ${Array.isArray(p.value) ? '' : Number(p.value).toFixed(2)}`)
            return `${d}<br/><span style="color:${color}">开 ${o?.toFixed(3)} · 收 ${c?.toFixed(3)}<br/>低 ${l?.toFixed(3)} · 高 ${h?.toFixed(3)}</span>${maLines.join('')}<br/>成交量 ${vol && !Array.isArray(vol.value) ? Number(vol.value).toLocaleString() : '—'}`
          }
          return arr.map((p) => `${p.seriesName} ${Array.isArray(p.value) ? '' : Number(p.value).toFixed(2)}`).join('<br/>')
        },
      }),
      legend: {
        top: 0, right: 8, itemWidth: 14, itemHeight: 8,
        textStyle: { color: 'rgba(255,255,255,0.6)', fontSize: 10 },
        data: ['MA5', 'MA20', 'MA60'],
      },
      grid: [
        { left: 60, right: 16, top: 26, height: '56%' },
        { left: 60, right: 16, top: '72%', height: '14%' },
      ],
      xAxis: [
        { type: 'category', data: dates, boundaryGap: true, axisLine: axisLineStyle, axisLabel: axisLabelStyle,
          axisTick: { show: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' },
        { type: 'category', gridIndex: 1, data: dates, axisLabel: { show: false }, axisLine: { show: false },
          axisTick: { show: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax' },
      ],
      yAxis: [
        { type: 'value', scale: true, axisLabel: axisLabelStyle, splitLine: splitLineStyle,
          axisLine: { show: false }, axisTick: { show: false } },
        { type: 'value', gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false },
          axisLine: { show: false }, axisTick: { show: false } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1] },
        { type: 'slider', xAxisIndex: [0, 1], height: 16, bottom: 6,
          borderColor: 'rgba(255,255,255,0.08)', backgroundColor: 'rgba(255,255,255,0.03)',
          fillerColor: 'rgba(77,124,254,0.15)', handleStyle: { color: '#4d7cfe' },
          textStyle: { color: 'rgba(255,255,255,0.4)', fontSize: 9 } },
      ],
      series: [
        {
          name: 'K线',
          type: 'candlestick',
          data: candles,
          itemStyle: {
            color: UP_COLOR, color0: DOWN_COLOR,
            borderColor: UP_COLOR, borderColor0: DOWN_COLOR,
          },
        },
        maSeries(5, MA5_COLOR),
        maSeries(20, MA20_COLOR),
        maSeries(60, MA60_COLOR),
        { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes, barWidth: '60%' },
      ],
    }
  },
  { immediate: true },
)

const { el } = useChart(option)
</script>

<template>
  <div v-if="option" ref="el" class="chart-box" />
  <el-empty v-else description="K线数据加载中或数据源不可用" :image-size="60" />
</template>
