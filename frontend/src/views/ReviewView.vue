<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { FundInfo, SignalReviewReport, ReviewHorizonStat, ShuffleTestReport, AgentScorecard, AgentStat, ICABReport } from '@/api/types'
import { useChart } from '@/composables/useChart'
import { ACCENT, UP_COLOR, DOWN_COLOR, axisLabelStyle, axisLineStyle, splitLineStyle } from '@/utils/chartTheme'
import type { EChartsOption } from 'echarts'

const funds = ref<FundInfo[]>([])
const fundCode = ref<string>('') // 空 = 全部基金合并
const report = ref<SignalReviewReport | null>(null)
const loading = ref(false)

const horizons = computed(() => report.value?.horizons ?? [])

// ---- 衰减曲线：柱 = 方向化平均收益，线 = 命中率 ----
const option = ref<EChartsOption | null>(null)
const { el: chartEl } = useChart(option)

function applyChart(r: SignalReviewReport): void {
  if (!r.decay_curve.length || r.evaluated === 0) {
    option.value = null
    return
  }
  option.value = {
    grid: { left: 48, right: 48, top: 36, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (ps: any) =>
        (Array.isArray(ps) ? ps : [ps])
          .map((p: { seriesName: string; value: number; marker: string }) =>
            `${p.marker} ${p.seriesName}：${p.seriesName === '命中率' ? (p.value * 100).toFixed(1) + '%' : Number(p.value).toFixed(2) + '%'}`)
          .join('<br/>'),
    },
    legend: {
      data: ['方向化平均收益', '命中率'],
      top: 0, right: 0,
      textStyle: { fontSize: 11, color: 'rgba(255,255,255,0.55)' },
      itemWidth: 14, itemHeight: 9,
    },
    xAxis: {
      type: 'category',
      data: r.decay_curve.map((h) => `${h.horizon} 日`),
      axisLabel: { ...axisLabelStyle, interval: 0 },
      axisLine: axisLineStyle,
    },
    yAxis: [
      {
        type: 'value',
        name: '收益 %',
        nameTextStyle: { fontSize: 10, color: 'rgba(255,255,255,0.45)' },
        axisLabel: axisLabelStyle, splitLine: splitLineStyle,
      },
      {
        type: 'value',
        name: '命中率',
        min: 0, max: 1,
        nameTextStyle: { fontSize: 10, color: 'rgba(255,255,255,0.45)' },
        axisLabel: { ...axisLabelStyle, formatter: (v: number) => `${Math.round(v * 100)}%` },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '方向化平均收益',
        type: 'bar',
        barWidth: 26,
        data: r.decay_curve.map((h) => h.avg_return_pct ?? 0),
        itemStyle: {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          color: (p: any) => (Number(p.value) >= 0 ? UP_COLOR : DOWN_COLOR),
          borderRadius: [3, 3, 0, 0],
        },
      },
      {
        name: '命中率',
        type: 'line',
        yAxisIndex: 1,
        data: r.decay_curve.map((h) => h.win_rate ?? 0),
        symbol: 'circle', symbolSize: 7,
        lineStyle: { width: 2, color: ACCENT },
        itemStyle: { color: ACCENT },
      },
    ],
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const r = await api.signalReview(fundCode.value || undefined)
    report.value = r
    applyChart(r)
    void loadScorecard()
  } finally {
    loading.value = false
  }
}

watch(fundCode, load)
onMounted(() => {
  api.listFunds().then((f) => { funds.value = f })
  void load()
})

// ---- 分方向统计表（BUY / SELL × 各持有期）----
interface ActionRow {
  action: string
  cells: (ReviewHorizonStat | null)[]
}
const actionRows = computed<ActionRow[]>(() => {
  const r = report.value
  if (!r) return []
  return ['BUY', 'SELL']
    .filter((a) => r.by_action[a])
    .map((a) => ({
      action: a,
      cells: horizons.value.map((n) => r.by_action[a]?.find((h) => h.horizon === n) ?? null),
    }))
})

function fmtPct(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}
function fmtRate(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`
}

// ---- 明细表 ----
const detailRows = computed(() => (report.value?.details ?? []).slice(0, 100))
const fundNameMap = computed(() => Object.fromEntries(funds.value.map((f) => [f.code, f.name])))

// ---- 打乱对照 ----
const shuffle = ref<ShuffleTestReport | null>(null)
const shuffling = ref(false)

async function runShuffle(): Promise<void> {
  shuffling.value = true
  try {
    shuffle.value = await api.shuffleTest(fundCode.value || undefined)
  } finally {
    shuffling.value = false
  }
}

watch(fundCode, () => { shuffle.value = null })

function shuffleTagType(v: string): 'success' | 'danger' | 'info' {
  return v === '显著优于随机' ? 'success' : v === '显著差于随机' ? 'danger' : 'info'
}

// ---- 分 Agent 记分卡 ----
const scorecard = ref<AgentScorecard | null>(null)

async function loadScorecard(): Promise<void> {
  try {
    scorecard.value = await api.agentScorecard(fundCode.value || undefined)
  } catch { /* 记分卡失败不阻塞复盘主体 */ }
}

function scorecardTagType(v: string): 'success' | 'danger' | 'warning' | 'info' {
  if (v === '显著正向') return 'success'
  if (v.includes('反向')) return 'danger'
  if (v === '方向正确不显著') return 'warning'
  return 'info'
}

const scorecardRows = computed<(AgentStat & { isComposite?: boolean })[]>(() => {
  const sc = scorecard.value
  if (!sc) return []
  const rows = sc.agents.filter((a) => a.samples > 0)
  return sc.composite ? [...rows, { ...sc.composite, isComposite: true }] : rows
})

function scorecardRowClass({ row }: { row: AgentStat & { isComposite?: boolean } }): string {
  return row.isComposite ? 'composite-row' : ''
}

// ---- IC 调权 A/B 对照 ----
const icab = ref<ICABReport | null>(null)
const icabRunning = ref(false)

async function runICAB(): Promise<void> {
  icabRunning.value = true
  try {
    icab.value = await api.icABTest(fundCode.value || undefined)
  } finally {
    icabRunning.value = false
  }
}

watch(fundCode, () => { icab.value = null })

function icVerdictType(v: string): 'success' | 'danger' | 'info' {
  if (v.includes('IC 调权更优')) return 'success'
  if (v.includes('关闭更优')) return 'danger'
  return 'info'
}
</script>

<template>
  <div class="review-page" v-loading="loading">
    <!-- 结论卡 -->
    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card-title">
          信号复盘
          <el-select v-model="fundCode" style="width: 220px" size="small" placeholder="全部基金">
            <el-option label="全部基金（合并）" value="" />
            <el-option v-for="f in funds" :key="f.code" :value="f.code" :label="`${f.name} (${f.code})`" />
          </el-select>
        </div>
      </template>

      <el-empty v-if="report && report.total_signals === 0" description="尚无历史信号——运行流水线积累数据后，这里会回答「这套系统到底准不准」" />

      <template v-else-if="report">
        <div class="verdict" :class="{ good: report.best_horizon !== null }">{{ report.verdict }}</div>
        <div class="stat-row">
          <div class="stat">
            <div class="stat-num">{{ report.total_signals }}</div>
            <div class="stat-label">历史信号</div>
          </div>
          <div class="stat">
            <div class="stat-num">{{ report.actionable }}</div>
            <div class="stat-label">BUY / SELL</div>
          </div>
          <div class="stat">
            <div class="stat-num">{{ report.evaluated }}</div>
            <div class="stat-label">已可评估</div>
          </div>
          <div class="stat">
            <div class="stat-num">{{ report.pending }}</div>
            <div class="stat-label">待净值到账</div>
          </div>
          <div class="stat">
            <div class="stat-num">{{ report.best_horizon !== null ? report.best_horizon + ' 日' : '—' }}</div>
            <div class="stat-label">信号有效期</div>
          </div>
        </div>
        <div class="hint">
          口径：T 日收盘出信号 → 按 T+1 净值入场 → 统计 N 日方向化收益（BUY 涨 / SELL 跌为命中）。
          均值 &gt; 0 即信号有信息量，峰值 N 即有效期。
        </div>
      </template>
    </el-card>

    <template v-if="report && report.evaluated > 0">
      <!-- 衰减曲线 -->
      <el-card shadow="never" class="mb">
        <template #header><div class="card-title">信号衰减曲线（持有 N 日的方向化收益与命中率）</div></template>
        <div ref="chartEl" class="chart" />
      </el-card>

      <!-- 分方向统计 -->
      <el-card shadow="never" class="mb">
        <template #header><div class="card-title">分方向统计</div></template>
        <el-table :data="actionRows" size="small" stripe>
          <el-table-column label="方向" width="70">
            <template #default="{ row }">
              <el-tag :type="row.action === 'BUY' ? 'danger' : 'success'" effect="dark" size="small">
                {{ row.action }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-for="(n, i) in horizons" :key="n" :label="`${n} 日`" align="center">
            <template #default="{ row }">
              <span v-if="row.cells[i] && row.cells[i]!.samples > 0">
                <span :class="(row.cells[i]!.avg_return_pct ?? 0) >= 0 ? 'text-pos' : 'text-neg'">
                  {{ fmtPct(row.cells[i]!.avg_return_pct) }}
                </span>
                <span class="sub">命中 {{ fmtRate(row.cells[i]!.win_rate) }} · n={{ row.cells[i]!.samples }}</span>
              </span>
              <span v-else class="sub">—</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 分基金统计 -->
      <el-card v-if="report.by_fund.length > 1" shadow="never" class="mb">
        <template #header><div class="card-title">分基金统计</div></template>
        <el-table :data="report.by_fund" size="small" stripe>
          <el-table-column label="基金" min-width="160">
            <template #default="{ row }">
              {{ row.fund_name }} <span class="sub">({{ row.fund_code }})</span>
            </template>
          </el-table-column>
          <el-table-column prop="evaluated" label="可评估" width="80" align="center" />
          <el-table-column v-for="(n, i) in horizons" :key="n" :label="`${n} 日`" align="center">
            <template #default="{ row }">
              <span v-if="row.horizons[i] && row.horizons[i]!.samples > 0">
                <span :class="(row.horizons[i]!.avg_return_pct ?? 0) >= 0 ? 'text-pos' : 'text-neg'">
                  {{ fmtPct(row.horizons[i]!.avg_return_pct) }}
                </span>
                <span class="sub">命中 {{ fmtRate(row.horizons[i]!.win_rate) }}</span>
              </span>
              <span v-else class="sub">—</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 打乱对照 -->
      <el-card shadow="never" class="mb">
        <template #header>
          <div class="card-title">
            打乱对照（shuffle test）
            <el-button size="small" type="primary" plain :loading="shuffling" @click="runShuffle">
              {{ shuffle ? '重新检验' : '运行检验' }}
            </el-button>
          </div>
        </template>
        <div v-if="!shuffle" class="hint">
          最狠也最诚实的自检：保持方向组合不变、把入场日随机打乱 N 次构造对照分布。
          若真实信号收益与随机对照无显著差异（p &gt; 0.05）→ 系统不含择时信息。
        </div>
        <template v-else>
          <div class="verdict" :class="{ good: shuffle.verdict.includes('显著优于随机') }">{{ shuffle.verdict }}</div>
          <el-table :data="shuffle.horizons" size="small" stripe>
            <el-table-column label="持有期" width="80" align="center">
              <template #default="{ row }">{{ row.horizon }} 日</template>
            </el-table-column>
            <el-table-column label="真实信号" width="100" align="center">
              <template #default="{ row }">
                <span v-if="row.real_avg_pct !== null" :class="row.real_avg_pct >= 0 ? 'text-pos' : 'text-neg'">
                  {{ fmtPct(row.real_avg_pct) }}
                </span>
                <span v-else class="sub">—</span>
              </template>
            </el-table-column>
            <el-table-column label="随机对照" width="130" align="center">
              <template #default="{ row }">
                <span v-if="row.null_mean_pct !== null">
                  {{ fmtPct(row.null_mean_pct) }} ±{{ (row.null_std_pct ?? 0).toFixed(2) }}
                </span>
                <span v-else class="sub">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="z_score" label="z 值" width="80" align="center">
              <template #default="{ row }">
                <span v-if="row.z_score !== null" :class="row.z_score >= 0 ? 'text-pos' : 'text-neg'">
                  {{ row.z_score.toFixed(2) }}
                </span>
                <span v-else class="sub">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="p_value" label="p 值" width="80" align="center">
              <template #default="{ row }">
                <span v-if="row.p_value !== null">{{ row.p_value.toFixed(3) }}</span>
                <span v-else class="sub">—</span>
              </template>
            </el-table-column>
            <el-table-column label="结论" min-width="140">
              <template #default="{ row }">
                <el-tag :type="shuffleTagType(row.verdict)" size="small" effect="plain">{{ row.verdict }}</el-tag>
                <span class="sub">n={{ row.samples }}</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="hint">p = P(随机对照 ≥ 真实)，单侧；多持有期同时检验存在多重比较膨胀，结论作探索性诊断。</div>
        </template>
      </el-card>

      <!-- 分 Agent 记分卡 -->
      <el-card v-if="scorecard && scorecardRows.length" shadow="never" class="mb">
        <template #header>
          <div class="card-title">
            分 Agent 记分卡
            <el-tag size="small" effect="plain">
              {{ scorecard.horizon }} 日口径{{ scorecard.horizon_source === 'auto' ? '（自动取信号有效期）' : '' }}
            </el-tag>
          </div>
        </template>
        <div class="verdict">{{ scorecard.verdict }}</div>
        <el-table :data="scorecardRows" size="small" stripe
                  :row-class-name="scorecardRowClass">
          <el-table-column label="Agent" width="110">
            <template #default="{ row }">
              <span :style="row.isComposite ? 'font-weight:700' : ''">{{ row.label }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="samples" label="样本" width="64" align="center" />
          <el-table-column label="命中率" width="90" align="center">
            <template #default="{ row }">{{ fmtRate(row.hit_rate) }}</template>
          </el-table-column>
          <el-table-column label="方向化收益" width="110" align="center">
            <template #default="{ row }">
              <span :class="(row.avg_return_pct ?? 0) >= 0 ? 'text-pos' : 'text-neg'">{{ fmtPct(row.avg_return_pct) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="t 值" width="80" align="center">
            <template #default="{ row }">
              <span v-if="row.t_stat !== null">{{ row.t_stat.toFixed(2) }}</span>
              <span v-else class="sub">—</span>
            </template>
          </el-table-column>
          <el-table-column label="自报置信度" width="100" align="center">
            <template #default="{ row }">
              <span v-if="row.avg_confidence !== null">{{ fmtRate(row.avg_confidence) }}</span>
              <span v-else class="sub">—</span>
            </template>
          </el-table-column>
          <el-table-column label="结论" min-width="140">
            <template #default="{ row }">
              <el-tag :type="scorecardTagType(row.verdict)" size="small" effect="plain">{{ row.verdict }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <div class="hint">HOLD 判断不参与定向记分；「拖后腿」的 Agent 可在系统设置中降权，或开启 IC 自适应调权自动处理。</div>
      </el-card>


      <!-- 信号明细 -->
      <el-card shadow="never">
        <template #header><div class="card-title">信号明细（最近 100 条已评估）</div></template>
        <el-table :data="detailRows" size="small" stripe max-height="480">
          <el-table-column prop="trade_date" label="信号日" width="100" />
          <el-table-column label="基金" min-width="120">
            <template #default="{ row }">{{ fundNameMap[row.fund_code] ?? row.fund_code }}</template>
          </el-table-column>
          <el-table-column label="信号" width="70">
            <template #default="{ row }">
              <el-tag :type="row.action === 'BUY' ? 'danger' : 'success'" effect="dark" size="small">
                {{ row.action }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="entry_date" label="入场日" width="100" />
          <el-table-column v-for="n in horizons" :key="n" :label="`${n} 日`" width="86" align="center">
            <template #default="{ row }">
              <span v-if="row.returns_pct[String(n)] !== null && row.returns_pct[String(n)] !== undefined"
                    :class="(row.returns_pct[String(n)] ?? 0) >= 0 ? 'text-pos' : 'text-neg'">
                {{ fmtPct(row.returns_pct[String(n)]) }}
              </span>
              <span v-else class="sub">待</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

      <!-- IC 调权 A/B 对照 -->
      <el-card shadow="never" class="mb">
        <template #header>
          <div class="card-title">
            IC 调权对照（A/B 重放）
            <el-button size="small" type="primary" plain :loading="icabRunning" @click="runICAB">
              {{ icab ? '重新对照' : '运行对照' }}
            </el-button>
          </div>
        </template>
        <div v-if="!icab" class="hint">
          对历史每个信号日用 ≤当日 数据重算三路确定性 Agent（消息面不可离线复现，两臂同时剔除），
          仅比「开 / 关 IC 调权」两臂的信号质量——回答「IC 调权到底该不该默认开」。
        </div>
        <template v-else>
          <div class="verdict" :class="{ good: icVerdictType(icab.verdict) === 'success' }">
            {{ icab.verdict }}
            <el-tag size="small" effect="plain" style="margin-left: 8px">
              {{ icab.horizon }} 日口径{{ icab.horizon_source === 'auto' ? '（自动取信号有效期）' : '' }}
            </el-tag>
          </div>
          <el-table :data="icab.funds" size="small" stripe>
            <el-table-column label="基金" min-width="120">
              <template #default="{ row }">{{ row.fund_name }}</template>
            </el-table-column>
            <el-table-column prop="signal_days" label="重放信号日" width="90" align="center" />
            <el-table-column label="关闭：均值 / 命中率" width="130" align="center">
              <template #default="{ row }">
                <span v-if="row.off && row.off.avg_return_pct !== null" :class="row.off.avg_return_pct >= 0 ? 'text-pos' : 'text-neg'">
                  {{ fmtPct(row.off.avg_return_pct) }} / {{ fmtRate(row.off.win_rate) }}
                </span>
                <span v-else class="sub">—</span>
              </template>
            </el-table-column>
            <el-table-column label="开启：均值 / 命中率" width="130" align="center">
              <template #default="{ row }">
                <span v-if="row.on && row.on.avg_return_pct !== null" :class="row.on.avg_return_pct >= 0 ? 'text-pos' : 'text-neg'">
                  {{ fmtPct(row.on.avg_return_pct) }} / {{ fmtRate(row.on.win_rate) }}
                </span>
                <span v-else class="sub">—</span>
              </template>
            </el-table-column>
            <el-table-column label="Δ 均值" width="86" align="center">
              <template #default="{ row }">
                <span v-if="row.delta_avg_pct !== null" :class="row.delta_avg_pct >= 0 ? 'text-pos' : 'text-neg'">
                  {{ row.delta_avg_pct >= 0 ? '+' : '' }}{{ row.delta_avg_pct.toFixed(2) }}pp
                </span>
                <span v-else class="sub">—</span>
              </template>
            </el-table-column>
            <el-table-column label="动作一致率" width="86" align="center">
              <template #default="{ row }">
                <span v-if="row.agreement_rate !== null">{{ fmtRate(row.agreement_rate) }}</span>
                <span v-else class="sub">—</span>
              </template>
            </el-table-column>
            <el-table-column label="换手（关/开）" width="96" align="center">
              <template #default="{ row }">{{ row.churn_off }} / {{ row.churn_on }}</template>
            </el-table-column>
            <el-table-column label="结论" min-width="150">
              <template #default="{ row }">
                <el-tag :type="icVerdictType(row.verdict)" size="small" effect="plain">{{ row.verdict }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="icab.pooled_off && icab.pooled_on" class="hint">
            合并口径（{{ icab.pooled_off.evaluated }} vs {{ icab.pooled_on.evaluated }} 条可评估）：
            关闭 {{ fmtPct(icab.pooled_off.avg_return_pct) }} / 开启 {{ fmtPct(icab.pooled_on.avg_return_pct) }}
            （t 值 {{ icab.pooled_off.t_stat?.toFixed(2) ?? '—' }} / {{ icab.pooled_on.t_stat?.toFixed(2) ?? '—' }}）。
            IC 调整实际生效的信号日 {{ icab.funds.reduce((s, f) => s + f.ic_active_days, 0) }} / {{ icab.funds.reduce((s, f) => s + f.signal_days, 0) }}。
          </div>
          <div class="hint">无未来数据（每信号日仅用 ≤当日 序列）；IC 检验约月度重估，与实盘节奏一致；样本随净值历史自然积累。</div>
        </template>
      </el-card>
  </div>
</template>

<style scoped lang="scss">
.mb { margin-bottom: 16px; }
.card-title { display: flex; align-items: center; justify-content: space-between; font-size: 14px; }

.verdict {
  font-size: 15px; font-weight: 600; margin-bottom: 14px; color: var(--muted);
  &.good { color: var(--accent); }
}
.stat-row { display: flex; gap: 40px; flex-wrap: wrap; }
.stat {
  .stat-num { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .stat-label { font-size: 12px; color: var(--muted); margin-top: 2px; }
}
.hint { margin-top: 14px; font-size: 12px; color: var(--muted); line-height: 1.7; }

.chart { height: 280px; }
:deep(.composite-row) { background: color-mix(in srgb, var(--accent) 8%, transparent) !important; }
.sub { display: block; font-size: 11px; color: var(--muted); font-weight: 400; }
</style>
