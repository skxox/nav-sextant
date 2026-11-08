<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { IntradayEstimate } from '@/api/types'

const estimates = ref<IntradayEstimate[]>([])
const loading = ref(false)

async function refresh(): Promise<void> {
  loading.value = true
  try {
    const funds = await api.listFunds()
    estimates.value = await Promise.all(funds.map((f) => api.intradayEstimate(f.code)))
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
defineExpose({ refresh })
</script>

<template>
  <div v-loading="loading">
    <div v-for="e in estimates" :key="e.fund_code" class="est-row">
      <span class="name">{{ e.fund_name }}</span>
      <span v-if="e.estimated_change_pct !== null" :class="e.estimated_change_pct >= 0 ? 'text-pos' : 'text-neg'">
        {{ e.estimated_change_pct >= 0 ? '+' : '' }}{{ e.estimated_change_pct }}%
      </span>
      <span v-else class="text-muted">行情不可用</span>
      <span class="text-muted">估 {{ e.estimated_nav ?? '-' }}（官方 {{ e.latest_nav ?? '-' }}）</span>
    </div>
    <div class="text-muted" style="font-size: 11px; margin-top: 6px">
      持仓穿透估算 · 基准指数实时行情 {{ estimates[0]?.as_of.slice(11) ?? '' }}
    </div>
  </div>
</template>

<style scoped>
.est-row { display: flex; align-items: center; gap: 10px; padding: 5px 0; font-size: 13px; }
.est-row .name { flex: 1; }
</style>
