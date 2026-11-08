<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { AGENT_LABELS } from '@/stores/panel'
import type { AgentLog, TradeDate } from '@/api/types'

const logs = ref<AgentLog[]>([])
const tradeDate = ref<TradeDate | ''
>( '')
const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  try {
    logs.value = await api.agentLogs(tradeDate.value || undefined)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="card-title">
        Agent 决策日志
        <el-date-picker v-model="tradeDate" type="date" value-format="YYYY-MM-DD"
                        placeholder="按日期筛选" clearable size="small"
                        style="width: 150px" @change="load" />
        <el-button size="small" style="margin-left: auto" @click="load">刷新</el-button>
      </div>
    </template>
    <el-table v-loading="loading" :data="logs" size="small" max-height="640">
      <el-table-column prop="trade_date" label="日期" width="100" />
      <el-table-column prop="fund_code" label="基金" width="90" />
      <el-table-column label="Agent" width="90">
        <template #default="{ row }">
          {{ AGENT_LABELS[row.agent as keyof typeof AGENT_LABELS] ?? row.agent }}
        </template>
      </el-table-column>
      <el-table-column prop="score" label="评分" width="80" align="right">
        <template #default="{ row }">
          <span :class="row.score >= 0 ? 'text-pos' : 'text-neg'">{{ row.score.toFixed(2) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="direction" label="方向" width="70" />
      <el-table-column prop="confidence" label="置信度" width="80" align="right">
        <template #default="{ row }">{{ row.confidence.toFixed(2) }}</template>
      </el-table-column>
      <el-table-column label="回退" width="70">
        <template #default="{ row }">
          <el-tag v-if="row.fallback_used" type="warning" size="small" effect="plain">回退</el-tag>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="summary" label="分析摘要" min-width="280" show-overflow-tooltip />
    </el-table>
  </el-card>
</template>
