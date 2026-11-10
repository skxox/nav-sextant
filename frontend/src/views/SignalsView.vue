<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { usePanelStore } from '@/stores/panel'
import { api } from '@/api/client'
import type { ExecutionRecord, Signal } from '@/api/types'
import AgentRadar from '@/components/AgentRadar.vue'
import NavChart from '@/components/NavChart.vue'
import RiskCheckPanel from '@/components/RiskCheckPanel.vue'
import SignalCard from '@/components/SignalCard.vue'
import IntradayPanel from '@/components/IntradayPanel.vue'

const store = usePanelStore()

function selectFund(code: string): void {
  store.selectedFund = code
}

async function onRun(): Promise<void> {
  const r = await store.runPipeline()
  if (r.failed > 0) ElMessage.warning(`分析完成：${r.ok} 只成功，${r.failed} 只失败`)
  else ElMessage.success(`分析完成：${r.ok} 只基金`)
  await loadExecutions()  // 重跑可能改写今日信号，执行状态随之刷新
}

// ---------- 执行记录（决策日志）----------
const execMap = ref<Map<string, ExecutionRecord>>(new Map())
const execOf = (code: string, date: string): ExecutionRecord | undefined =>
  execMap.value.get(`${code}|${date}`)

async function loadExecutions(): Promise<void> {
  const r = await api.listExecutions()
  execMap.value = new Map(
    r.rows.filter((x) => x.signal_date).map((x) => [`${x.fund_code}|${x.signal_date}`, x]))
}

const dialog = reactive({
  visible: false, saving: false,
  mode: 'exec' as 'exec' | 'skip',
  signal: null as Signal | null,
  execDate: '', amount: undefined as number | undefined, note: '',
})

function openRecord(code: string, mode: 'exec' | 'skip'): void {
  const signal = store.signals.find((s) => s.fund_code === code)
  if (!signal) return
  const existing = execOf(code, signal.trade_date)
  dialog.signal = signal
  dialog.mode = mode
  dialog.execDate = existing?.exec_date ?? new Date().toISOString().slice(0, 10)
  dialog.amount = existing?.amount ?? undefined
  dialog.note = existing?.note ?? ''
  dialog.visible = true
}

const dialogTitle = computed(() =>
  `${dialog.mode === 'skip' ? '跳过' : '记录执行'} · ${dialog.signal?.fund_name ?? ''} ${dialog.signal?.action}`)

async function saveExec(): Promise<void> {
  if (!dialog.signal) return
  dialog.saving = true
  try {
    await api.recordExecution({
      fund_code: dialog.signal.fund_code,
      signal_date: dialog.signal.trade_date,
      exec_date: dialog.execDate,
      action: dialog.mode === 'skip' ? 'SKIP' : dialog.signal.action as 'BUY' | 'SELL',
      amount: dialog.mode === 'skip' ? undefined : dialog.amount,
      note: dialog.note,
    })
    ElMessage.success(dialog.mode === 'skip' ? '已记录跳过' : '已记录执行')
    dialog.visible = false
    await loadExecutions()
  } finally {
    dialog.saving = false
  }
}

onMounted(loadExecutions)
</script>

<template>
  <div v-loading="store.loading">
    <el-empty v-if="!store.signals.length" description="暂无信号，点击右上角「运行流水线」生成">
      <el-button type="primary" :loading="store.pipelineRunning" @click="onRun">运行分析流水线</el-button>
    </el-empty>

    <div v-else class="signals">
      <div class="left-col">
        <el-card shadow="never">
          <template #header>
            <div class="card-title">
              每日信号
              <span class="text-muted" style="font-weight: 400">{{ store.signals[0]?.trade_date }}</span>
            </div>
          </template>
          <div class="signal-list">
            <SignalCard v-for="s in store.signals" :key="s.fund_code"
                        :signal="s" :active="s.fund_code === store.selectedFund"
                        :exec="execOf(s.fund_code, s.trade_date)"
                        @select="selectFund" @record="openRecord" />
          </div>
        </el-card>
      </div>

      <div class="right-col">
        <el-card shadow="never">
          <template #header><div class="card-title">盘中实时估算</div></template>
          <IntradayPanel />
        </el-card>

        <el-card v-if="store.selectedFund" shadow="never">
          <template #header>
            <div class="card-title">{{ store.selectedSignal?.fund_name }} · 净值走势（含买卖点）</div>
          </template>
          <NavChart :fund-code="store.selectedFund" />
        </el-card>

        <el-card v-if="store.agentsOf(store.selectedFund ?? '').length" shadow="never">
          <template #header><div class="card-title">Agent 评分雷达</div></template>
          <AgentRadar :agents="store.agentsOf(store.selectedFund ?? '')" />
        </el-card>

        <el-card v-if="store.selectedSignal" shadow="never">
          <template #header><div class="card-title">风控检查 · {{ store.selectedSignal.risk_summary }}</div></template>
          <RiskCheckPanel :checks="store.selectedSignal.risk_checks" />
        </el-card>
      </div>
    </div>

    <el-dialog v-model="dialog.visible" :title="dialogTitle" width="420px">
      <el-form label-width="80px" v-if="dialog.signal">
        <el-form-item label="信号">{{ dialog.signal.trade_date }} · {{ dialog.signal.action }}（建议仓位 {{ (dialog.signal.target_position * 100).toFixed(0) }}%）</el-form-item>
        <template v-if="dialog.mode === 'exec'">
          <el-form-item label="执行日期">
            <el-date-picker v-model="dialog.execDate" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-form-item label="金额（元）">
            <el-input-number v-model="dialog.amount" :min="0" :step="1000" style="width: 100%" placeholder="选填" />
          </el-form-item>
        </template>
        <el-form-item :label="dialog.mode === 'skip' ? '原因' : '备注'">
          <el-input v-model="dialog.note" :placeholder="dialog.mode === 'skip' ? '为何不执行这条信号' : '选填'" />
        </el-form-item>
        <el-form-item v-if="dialog.mode === 'exec'">
          <span class="text-muted" style="font-size: 12px; line-height: 1.6">
            成交净值按执行日官方净值自动回查（T+1 晚间公布后自动确认），无需手填
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="saveExec">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.signals { display: grid; grid-template-columns: minmax(0, 1fr) 420px; gap: 16px; }
@media (max-width: 1020px) { .signals { grid-template-columns: 1fr; } }
.left-col, .right-col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.signal-list { display: flex; flex-direction: column; gap: 12px; }
</style>
