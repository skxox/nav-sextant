<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '@/api/client'
import { useChart } from '@/composables/useChart'
import { useLatestRun } from '@/composables/useLatestRun'
import type { NavPoint, FundCode } from '@/api/types'
import type { EChartsOption } from 'echarts'
import {
  ACCENT, MA5_COLOR, MA20_COLOR, MA60_COLOR, UP_COLOR, DOWN_COLOR,
  axisLabelStyle, axisLineStyle, splitLineStyle, baseTooltip, dataZoomCfg, ma,
} from '@/utils/chartTheme'

const props = defineProps<{ fundCode: FundCode; days?: number }>()

const data = ref<NavPoint[]>([])
const option = ref<EChartsOption | null>(null)
const latest = useLatestRun()

watch(
  () => [props.fundCode, props.days] as const,
  async ([code, days]) => {
    if (!code) return
    let payload: { nav: NavPoint[]; history: Awaited<ReturnType<typeof api.signalHistory>> }
    try {
      const res = await latest(() => Promise.all([
        api.navHistory(code, days ?? 250),
        api.signalHistory(code, 120),
      ]).then(([nav, history]) => ({ nav, history })))
      if (res.stale) return
      payload = res.value
    } catch {
      return
    }
    const { nav, history } = payload
    data.value = nav
    if (!nav.length) { option.value = null; return }


    const dates = nav.map((r) => r.trade_date)
    const navs = nav.map((r) => Number(r.nav))

    // 历史 BUY/SELL 信号 → markPoint 标注在净值曲线上
    const markData = history
      .filter((s) => s.action !== 'HOLD')
      .flatMap((s) => {
        const navAt: number | null | undefined = nav.find((n) => n.trade_date <= s.trade_date)?.nav
        if (navAt == null) return []
        return [{
          name: s.action as string,
          coord: [s.trade_date, navAt] as [string, number],
          value: s.action,
          symbol: s.action === 'BUY' ? 'arrow' : 'triangle',
          symbolSize: 12,
          symbolRotate: s.action === 'BUY' ? 0 : 180,
          itemStyle: { color: s.action === 'BUY' ? UP_COLOR : DOWN_COLOR },
          label: {
            show: true, position: s.action === 'BUY' ? ('bottom' as const) : ('top' as const),
            fontSize: 10, color: s.action === 'BUY' ? UP_COLOR : DOWN_COLOR,
            formatter: s.action,
          },
        }]
      })

    option.value = {
      animation: false,
      tooltip: baseTooltip({
        formatter: (params: unknown) => {
          const arr = params as { axisValue: string; seriesName: string; value: number | null }[]
          const i = dates.indexOf(arr[0]?.axisValue ?? '')
          const lines = arr
            .filter((p) => p.value != null)
            .map((p) => `${p.seriesName} <b>${Number(p.value).toFixed(4)}</b>`)
          const chg = i > 0 && navs[i - 1] ? ((navs[i] - navs[i - 1]) / navs[i - 1]) * 100 : null
          if (chg != null) {
            lines.push(`日涨跌 <b style="color:${chg >= 0 ? UP_COLOR : DOWN_COLOR}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</b>`)
          }
          return `${arr[0]?.axisValue}<br/>${lines.join('<br/>')}`
        },
      }),
      legend: {
        top: 0, right: 8, itemWidth: 14, itemHeight: 8,
        textStyle: { color: 'rgba(255,255,255,0.6)', fontSize: 10 },
        data: ['单位净值', 'MA5', 'MA20', 'MA60'],
      },
      grid: { left: 60, right: 16, top: 30, bottom: 56 },
      xAxis: { type: 'category', data: dates, boundaryGap: false,
               axisLine: axisLineStyle, axisLabel: axisLabelStyle, axisTick: { show: false } },
      yAxis: { type: 'value', scale: true, axisLabel: axisLabelStyle,
               splitLine: splitLineStyle, axisLine: { show: false }, axisTick: { show: false } },
      dataZoom: dataZoomCfg(),
      series: [
        {
          name: '单位净值',
          type: 'line',
          data: navs,
          showSymbol: false,
          lineStyle: { color: ACCENT, width: 1.8 },
          areaStyle: {
            color: {
              type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(77,124,254,0.20)' },
                { offset: 1, color: 'rgba(77,124,254,0)' },
              ],
            },
          },
          markPoint: markData.length ? { symbolSize: 12, data: markData, animation: false } : undefined,
        },
        { name: 'MA5', type: 'line', data: ma(navs, 5), showSymbol: false,
          lineStyle: { color: MA5_COLOR, width: 1 }, emphasis: { disabled: true } },
        { name: 'MA20', type: 'line', data: ma(navs, 20), showSymbol: false,
          lineStyle: { color: MA20_COLOR, width: 1 }, emphasis: { disabled: true } },
        { name: 'MA60', type: 'line', data: ma(navs, 60), showSymbol: false,
          lineStyle: { color: MA60_COLOR, width: 1 }, emphasis: { disabled: true } },
      ],
    }
  },
  { immediate: true },
)

const { el } = useChart(option)
</script>

<template>
  <div v-if="option" ref="el" class="chart-box" />
  <el-empty v-else description="暂无净值数据" :image-size="60" />
</template>
