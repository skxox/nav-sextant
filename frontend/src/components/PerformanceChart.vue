<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '@/api/client'
import { useChart } from '@/composables/useChart'
import { useLatestRun } from '@/composables/useLatestRun'
import type { PerformanceData, FundCode } from '@/api/types'
import type { EChartsOption } from 'echarts'
import {
  ACCENT, axisLabelStyle, axisLineStyle, splitLineStyle, baseTooltip, dataZoomCfg,
} from '@/utils/chartTheme'

const props = defineProps<{ fundCode: FundCode; days?: number }>()

const data = ref<PerformanceData | null>(null)
const option = ref<EChartsOption | null>(null)
const latest = useLatestRun()

watch(
  () => [props.fundCode, props.days] as const,
  async ([code, days]) => {
    if (!code) return
    let payload: PerformanceData
    try {
      const res = await latest(() => api.performance(code, days ?? 250))
      if (res.stale) return
      payload = res.value
    } catch {
      return
    }
    data.value = payload
    const rows = payload?.rows ?? []
    if (!rows.length) { option.value = null; return }

    const lastFund = rows[rows.length - 1].fund
    const lastBench = rows[rows.length - 1].benchmark
    option.value = {
      // 区间末值说明（整块赋值，避免就地修改导致响应式监听失效）
      title: {
        top: 0, left: 8,
        textStyle: { color: 'rgba(255,255,255,0.7)', fontSize: 11, fontWeight: 400 },
        text: `区间基金 ${(lastFund - 100).toFixed(2)}% vs 基准 ${(lastBench - 100).toFixed(2)}% · 超额 ${(lastFund - lastBench).toFixed(2)}%`,
      },
      tooltip: baseTooltip({
        valueFormatter: (v: number) => v.toFixed(2),
      }),
      legend: {
        top: 0, right: 8, itemWidth: 14, itemHeight: 8,
        textStyle: { color: 'rgba(255,255,255,0.6)', fontSize: 10 },
      },
      grid: { left: 56, right: 16, top: 30, bottom: 56 },
      xAxis: { type: 'category', data: rows.map((r) => r.trade_date),
               boundaryGap: false, axisLine: axisLineStyle, axisLabel: axisLabelStyle,
               axisTick: { show: false } },
      yAxis: { type: 'value', scale: true, axisLabel: { ...axisLabelStyle, formatter: '{value}' },
               splitLine: splitLineStyle, axisLine: { show: false }, axisTick: { show: false } },
      dataZoom: dataZoomCfg(),
      series: [
        {
          name: data.value!.fund_name,
          type: 'line',
          data: rows.map((r) => r.fund),
          showSymbol: false,
          lineStyle: { color: ACCENT, width: 1.8 },
          areaStyle: { color: 'rgba(77,124,254,0.10)' },
          markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: 'rgba(255,255,255,0.2)', type: 'dashed' },
            label: { formatter: '基准起点', fontSize: 9, color: 'rgba(255,255,255,0.4)' },
            data: [{ yAxis: 100 }],
          },
        },
        {
          name: `${data.value!.benchmark.name}（基准）`,
          type: 'line',
          data: rows.map((r) => r.benchmark),
          showSymbol: false,
          lineStyle: { color: 'rgba(255,255,255,0.45)', width: 1.2, type: 'dashed' },
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
  <el-empty v-else description="暂无对比数据：需同时有基金净值与基准指数历史" :image-size="60" />
</template>
