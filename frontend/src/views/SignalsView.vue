<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { usePanelStore } from '@/stores/panel'
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
}
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
                        @select="selectFund" />
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
  </div>
</template>

<style scoped lang="scss">
.signals { display: grid; grid-template-columns: minmax(0, 1fr) 420px; gap: 16px; }
@media (max-width: 1020px) { .signals { grid-template-columns: 1fr; } }
.left-col, .right-col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.signal-list { display: flex; flex-direction: column; gap: 12px; }
</style>
