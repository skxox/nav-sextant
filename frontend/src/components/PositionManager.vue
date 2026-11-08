<script setup lang="ts">
import { onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api/client'
import { usePanelStore } from '@/stores/panel'

interface Draft { position: number; shares: number; cost: number }

const emit = defineEmits<{ changed: [] }>()
const store = usePanelStore()
const drafts = reactive<Record<string, Draft>>({})

onMounted(() => {
  for (const f of store.funds) {
    const saved = store.positions[f.code] ?? 0
    drafts[f.code] = { position: saved, shares: 0, cost: 0 }
  }
})

async function save(code: string): Promise<void> {
  const d = drafts[code]
  if (!d) return
  if (d.position < 0 || d.position > 1) { ElMessage.error('仓位需在 0 ~ 1 之间'); return }
  await api.savePosition(code, d.position, d.shares, d.cost)
  await store.savePosition(code, d.position)
  ElMessage.success('已保存')
  emit('changed')
}

async function removePosition(code: string): Promise<void> {
  await ElMessageBox.confirm(`确定删除基金 ${code} 的持仓记录？`, '删除持仓', { type: 'warning' })
  await api.deletePosition(code)
  ElMessage.success('已删除')
  if (drafts[code]) { drafts[code]!.position = 0; drafts[code]!.shares = 0; drafts[code]!.cost = 0 }
  emit('changed')
}
</script>

<template>
  <div v-for="f in store.funds" :key="f.code" class="pos-block">
    <div class="pos-head">{{ f.name }}</div>
    <div class="pos-row">
      <span class="pos-label">仓位比例</span>
      <el-input-number v-if="drafts[f.code]" v-model="drafts[f.code]!.position"
                       :min="0" :max="1" :step="0.05" :precision="2" size="small" controls-position="right" />
      <el-button size="small" type="primary" plain @click="save(f.code)">保存</el-button>
    </div>
    <div class="pos-row">
      <span class="pos-label">份额 / 成本</span>
      <el-input-number v-if="drafts[f.code]" v-model="drafts[f.code]!.shares"
                       :min="0" :step="100" size="small" controls-position="right" />
      <el-input-number v-if="drafts[f.code]" v-model="drafts[f.code]!.cost"
                       :min="0" :step="0.01" :precision="4" size="small" controls-position="right" />
      <el-button size="small" type="danger" text @click="removePosition(f.code)">删除</el-button>
    </div>
  </div>
  <div class="text-muted" style="font-size: 12px; margin-top: 6px">
    仓位比例供风控检查（现金缓冲）；份额与单位成本用于持仓对账与盈亏计算。
  </div>
</template>

<style scoped>
.pos-block { border-bottom: 1px dashed var(--border); padding-bottom: 10px; margin-bottom: 10px; }
.pos-head { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.pos-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.pos-label { width: 72px; font-size: 12px; color: var(--muted); flex-shrink: 0; }
</style>
