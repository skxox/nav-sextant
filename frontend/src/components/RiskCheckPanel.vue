<script setup lang="ts">
import type { RiskCheck } from '@/api/types'

defineProps<{ checks: RiskCheck[] }>()
</script>

<template>
  <el-table :data="checks" size="small" :show-header="false">
    <el-table-column width="40">
      <template #default="{ row }">
        <span :class="row.passed ? 'ok' : 'no'">{{ row.passed ? '✓' : '✗' }}</span>
      </template>
    </el-table-column>
    <el-table-column width="110" prop="name" />
    <el-table-column prop="detail">
      <template #default="{ row }">
        <span class="text-muted" style="font-size: 12px">{{ row.detail }}</span>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.ok { color: var(--sell); font-weight: 700; }
.no { color: var(--buy); font-weight: 700; }
</style>
