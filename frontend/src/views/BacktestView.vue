<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '@/api/client'
import { useChart } from '@/composables/useChart'
import type { FundInfo, BacktestMetrics, FundCode, FactorEvalResult } from '@/api/types'
import type { EChartsOption } from 'echarts'

const funds = ref<FundInfo[]>([])
const fundCode = ref<FundCode>('')
const metrics = ref<BacktestMetrics | null>(null)
const running = ref(false)
const option = ref<EChartsOption | null>(null)

// ---- 因子检验 ----
const factorEval = ref<FactorEvalResult | null>(null)
const factorRunning = ref(false)
const factorForward = ref(5)

api.listFunds().then((f) => {
  funds.value = f
  if (!fundCode.value) fundCode.value = f[0]?.code ?? ''
})

watch(fundCode, () => { metrics.value = null; factorEval.value = null })

async function run(): Promise<void> {
  if (!fundCode.value) return
  running.value = true
  try {
    metrics.value = await api.runBacktest(fundCode.value)
    const curve = metrics.value.strategy_curve
    option.value = {
      tooltip: { trigger: 'axis' },
      legend: { data: ['策略净值', '买入持有'], textStyle: { fontSize: 11 } },
      grid: { left: 56, right: 16, top: 36, bottom: 28 },
      xAxis: { type: 'category', data: curve.map((p) => p.trade_date) },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 11 } },
      dataZoom: [{ type: 'inside' }],
      series: [
        { name: '策略净值', type: 'line', data: curve.map((p) => p.nav), showSymbol: false,
          lineStyle: { color: '#c93030', width: 1.6 } },
        { name: '买入持有', type: 'line', data: curve.map((p) => p.benchmark), showSymbol: false,
          lineStyle: { color: '#8a919f', width: 1.2, type: 'dashed' } },
      ],
    }
  } catch {
    /* 拦截器已提示 */
  } finally {
    running.value = false
  }
}

async function runFactorEval(): Promise<void> {
  if (!fundCode.value) return
  factorRunning.value = true
  try { factorEval.value = await api.factorsEval(fundCode.value, factorForward.value) }
  catch { /* 拦截器已提示 */ }
  finally { factorRunning.value = false }
}

function verdictTag(v: string): 'success' | 'warning' | 'danger' | 'info' {
  if (v === '显著有效') return 'success'
  if (v === '显著反向') return 'danger'
  if (v.startsWith('方向正确')) return 'warning'
  return 'info'
}

const { el } = useChart(option)
</script>

<template>
  <div class="backtest">
    <el-card shadow="never">
      <template #header><div class="card-title">策略回测 · 阈值策略历史验证（含申赎成本）</div></template>
      <div class="controls">
        <el-select v-model="fundCode" style="width: 280px">
          <el-option v-for="f in funds" :key="f.code" :value="f.code" :label="`${f.name} (${f.code})`" />
        </el-select>
        <el-button type="primary" :loading="running" @click="run">运行回测</el-button>
      </div>
      <div v-if="metrics" class="metrics">
        <el-descriptions :column="4" border size="small">
          <el-descriptions-item label="区间">{{ metrics.start_date }} ~ {{ metrics.end_date }}</el-descriptions-item>
          <el-descriptions-item label="交易日">{{ metrics.trading_days }}</el-descriptions-item>
          <el-descriptions-item label="累计收益">
            <span :class="metrics.total_return_pct >= 0 ? 'text-pos' : 'text-neg'">{{ metrics.total_return_pct.toFixed(2) }}%</span>
          </el-descriptions-item>
          <el-descriptions-item label="年化收益">
            <span :class="metrics.annualized_return_pct >= 0 ? 'text-pos' : 'text-neg'">{{ metrics.annualized_return_pct.toFixed(2) }}%</span>
          </el-descriptions-item>
          <el-descriptions-item label="最大回撤">
            <span class="text-neg">{{ metrics.max_drawdown_pct.toFixed(2) }}%</span>
          </el-descriptions-item>
          <el-descriptions-item label="夏普比率">{{ metrics.sharpe_ratio.toFixed(2) }}</el-descriptions-item>
          <el-descriptions-item label="胜率">{{ metrics.win_rate_pct.toFixed(1) }}%</el-descriptions-item>
          <el-descriptions-item label="交易次数">{{ metrics.trade_count }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <el-empty v-else description="选择基金后点击「运行回测」" :image-size="60" />
    </el-card>

    <el-card v-if="metrics" shadow="never">
      <template #header><div class="card-title">策略净值 vs 买入持有</div></template>
      <div ref="el" class="chart-box" />
    </el-card>

    <!-- 因子有效性检验 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-title">
          因子有效性检验（RankIC · t 检验 · ICIR · 分层价差）
          <span class="text-muted" style="font-size: 11px; margin-left: 8px">
            检验生产同款因子函数对基金未来 {{ factorForward }} 日收益的预测力
          </span>
        </div>
      </template>
      <div class="controls">
        <el-select v-model="factorForward" style="width: 150px">
          <el-option :value="1" label="未来 1 日" />
          <el-option :value="5" label="未来 5 日" />
          <el-option :value="10" label="未来 10 日" />
          <el-option :value="20" label="未来 20 日" />
        </el-select>
        <el-button type="primary" plain :loading="factorRunning" @click="runFactorEval">运行因子检验</el-button>
      </div>
      <template v-if="factorEval">
        <el-table :data="factorEval.reports" size="small">
          <el-table-column prop="label" label="因子" min-width="150" />
          <el-table-column label="样本" width="60" align="right">
            <template #default="{ row }">{{ row.samples }}</template>
          </el-table-column>
          <el-table-column label="RankIC" width="90" align="right">
            <template #default="{ row }">
              <span :class="row.rank_ic > 0 ? 'text-pos' : 'text-neg'">{{ row.rank_ic.toFixed(4) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="t 值" width="80" align="right">
            <template #default="{ row }">{{ row.ic_tstat.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="ICIR" width="80" align="right">
            <template #default="{ row }">{{ row.icir.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="IC 胜率" width="85" align="right">
            <template #default="{ row }">{{ (row.ic_win_rate * 100).toFixed(0) }}%</template>
          </el-table-column>
          <el-table-column label="五分位价差" width="95" align="right">
            <template #default="{ row }">{{ row.q5_q1_spread_pct.toFixed(2) }}%</template>
          </el-table-column>
          <el-table-column label="结论" width="130">
            <template #default="{ row }">
              <el-tag size="small" :type="verdictTag(row.verdict)" effect="plain">{{ row.verdict }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <div class="text-muted" style="font-size: 11px; margin-top: 8px">
          解读：|t| ≥ 2 且 RankIC > 0 → 因子对基金有显著正向预测力，可保留权重；
          「显著反向」说明该维度在此基金上反着用（应降权或反转）；
          「方向正确但不显著」说明单因子噪声大，依赖合成层的多因子互补。
          估值因子使用指数
          <b>{{ factorEval.valuation_index }}</b>
          的历史序列对照指数收益。
        </div>
      </template>
      <el-empty v-else description="选择基金后点击「运行因子检验」" :image-size="60" />
    </el-card>
  </div>
</template>

<style scoped>
.backtest { display: flex; flex-direction: column; gap: 16px; }
.controls { display: flex; gap: 12px; margin-bottom: 16px; }
</style>
