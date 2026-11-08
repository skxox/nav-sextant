<script setup lang="ts">
import { ref, watch } from 'vue'
import { useChart } from '@/composables/useChart'
import type { AgentLog } from '@/api/types'
import { AGENT_LABELS } from '@/stores/panel'

const props = defineProps<{ agents: AgentLog[] }>()

const option = ref<import('echarts').EChartsOption | null>(null)

watch(
  () => props.agents,
  (agents) => {
    if (!agents.length) { option.value = null; return }
    option.value = {
      radar: {
        indicator: agents.map((a) => ({ name: AGENT_LABELS[a.agent], max: 1, min: -1 })),
        radius: '68%',
        axisName: { fontSize: 11, color: '#8b93a5' },
        splitLine: { lineStyle: { color: '#232b3a' } },
        splitArea: { areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.04)'] } },
        axisLine: { lineStyle: { color: '#232b3a' } },
      },
      series: [{
        type: 'radar',
        data: [{
          value: agents.map((a) => a.score),
          name: 'Agent 评分',
          areaStyle: { color: 'rgba(77,124,254,0.22)' },
          lineStyle: { color: '#4d7cfe' },
          itemStyle: { color: '#4d7cfe' },
        }],
      }],
    }
  },
  { immediate: true, deep: true },
)

const { el } = useChart(option)
</script>

<template>
  <div ref="el" class="chart-box" />
</template>
