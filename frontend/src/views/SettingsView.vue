<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api/client'
import type { SettingsBundle, SettingsSection } from '@/api/types'

const bundle = ref<SettingsBundle | null>(null)
const saving = ref<SettingsSection | null>(null)

// 本地编辑副本（加载与保存间互不干扰）
const llm = reactive({ base_url: '', api_key: '', model: '', model_name: '', vision_model: '', timeout_seconds: 30, debate_enabled: true, fallback_enabled: true, sentiment_samples: 3, cache_ttl_minutes: 360 })
const strategy = reactive({
  w_tech: 0.3, w_val: 0.3, w_sent: 0.25, w_risk: 0.15,
  buy: 0.25, sell: -0.25,
  max_pos: 0.3, max_dd: 0.15, cash: 0.1,
  momentum_windows: '5, 20, 60', momentum_weights: '0.2, 0.3, 0.5', val_window: 750,
  ic_enabled: false, ic_min_samples: 30,
  whipsaw_days: 3, min_trade: 0.05,
})
const debate = reactive({ rounds: 2, consensus_early_stop: true })
const scheduler = reactive({ enabled: false, run_at: '18:00', weekdays_only: true })
const notifications = reactive({ enabled: false, feishu_webhook: '', wecom_webhook: '', email_to: '' as string | string[], smtp_host: '', smtp_port: 465, smtp_user: '', email_from: '' })
const backtestCfg = reactive({ buy_cost: 0.0015, sell_cost: 0.005, risk_free_rate: 0.02 })

function parseNumList(text: string): number[] {
  return String(text).split(/[,，\s]+/).filter(Boolean).map(Number).filter((n) => !Number.isNaN(n))
}

// 基金管理
const newFund = reactive({ code: '', name: '', benchmark_index: '000300', valuation_index: '', sector: '' })
const fundSaving = ref(false)

function fill(b: SettingsBundle): void {
  Object.assign(llm, {
    base_url: b.llm.base_url, api_key: b.llm.api_key, model: b.llm.model,
    model_name: b.llm.model_name, vision_model: b.llm.vision_model,
    timeout_seconds: b.llm.timeout_seconds, debate_enabled: b.llm.debate_enabled,
    fallback_enabled: b.llm.fallback_enabled, sentiment_samples: b.llm.sentiment_samples ?? 3,
    cache_ttl_minutes: b.llm.cache_ttl_minutes ?? 360,
  })
  Object.assign(strategy, {
    w_tech: b.settings.weights.technical, w_val: b.settings.weights.valuation,
    w_sent: b.settings.weights.sentiment, w_risk: b.settings.weights.risk,
    buy: b.settings.thresholds.buy, sell: b.settings.thresholds.sell,
    max_pos: b.settings.risk.max_single_fund_position, max_dd: b.settings.risk.max_drawdown,
    cash: b.settings.risk.cash_buffer,
    momentum_windows: b.settings.momentum_windows.join(', '),
    momentum_weights: b.settings.momentum_windows_weights.join(', '),
    val_window: b.settings.valuation_percentile_window,
    ic_enabled: b.settings.ic_weighting_enabled ?? false,
    ic_min_samples: b.settings.ic_min_samples ?? 30,
    whipsaw_days: b.settings.whipsaw_cooldown_days ?? 3,
    min_trade: b.settings.min_trade_change ?? 0.05,
  })
  Object.assign(debate, b.debate)
  Object.assign(scheduler, b.scheduler)
  Object.assign(notifications, {
    ...b.notifications,
    email_to: b.notifications.email_to.join(', '),
  })
  Object.assign(backtestCfg, b.backtest)
}

async function load(): Promise<void> {
  bundle.value = await api.getSettings()
  fill(bundle.value)
}

async function save(section: SettingsSection, patch: Record<string, unknown>): Promise<void> {
  saving.value = section
  try {
    await api.updateSettings(section, patch)
    ElMessage.success('已保存并立即生效')
    await load()
  } catch {
    /* 拦截器已提示（含 pydantic 校验错误） */
  } finally {
    saving.value = null
  }
}

const saveLLM = (): Promise<void> => save('llm', { ...llm })
function strategyPatch(): Record<string, unknown> | null {
  const windows = parseNumList(strategy.momentum_windows)
  const weights = parseNumList(strategy.momentum_weights)
  if (!windows.length || windows.length !== weights.length) {
    ElMessage.warning('动量窗口与窗口权重数量不一致（如 5, 20, 60 对应 0.2, 0.3, 0.5）')
    return null
  }
  return {
    weights: { technical: strategy.w_tech, valuation: strategy.w_val, sentiment: strategy.w_sent, risk: strategy.w_risk },
    thresholds: { buy: strategy.buy, sell: strategy.sell },
    risk: { max_single_fund_position: strategy.max_pos, max_drawdown: strategy.max_dd, cash_buffer: strategy.cash },
    momentum_windows: windows.map((n) => Math.round(n)),
    momentum_windows_weights: weights,
    valuation_percentile_window: Math.round(strategy.val_window),
    ic_weighting_enabled: strategy.ic_enabled,
    ic_min_samples: Math.round(strategy.ic_min_samples),
    whipsaw_cooldown_days: Math.round(strategy.whipsaw_days),
    min_trade_change: strategy.min_trade,
  }
}
const saveStrategy = (): Promise<void> => {
  const patch = strategyPatch()
  return patch ? save('settings', patch) : Promise.resolve()
}
const saveDebate = (): Promise<void> => save('debate', { ...debate })
const saveScheduler = (): Promise<void> => save('scheduler', { ...scheduler })
const saveNotify = (): Promise<void> => save('notifications', {
  ...notifications,
  email_to: String(notifications.email_to).split(/[,，\s]+/).filter(Boolean),
})
const saveBacktest = (): Promise<void> => save('backtest', { ...backtestCfg })

async function resetSection(section: SettingsSection, label: string): Promise<void> {
  await ElMessageBox.confirm(`确定将「${label}」恢复为内置默认值？（删除数据库中的覆盖项）`, '恢复默认', { type: 'warning' })
  await api.resetSettings(section)
  ElMessage.success('已恢复内置默认值')
  await load()
}

async function addFund(): Promise<void> {
  if (!/^\d{6}$/.test(newFund.code)) { ElMessage.warning('基金代码须为 6 位数字'); return }
  if (!newFund.name.trim()) { ElMessage.warning('请填写基金名称'); return }
  fundSaving.value = true
  try {
    await api.addFund({ ...newFund })
    ElMessage.success(`基金 ${newFund.code} 已添加`)
    newFund.code = ''; newFund.name = ''; newFund.sector = ''
    await load()
  } finally {
    fundSaving.value = false
  }
}

async function removeFund(code: string): Promise<void> {
  await ElMessageBox.confirm(`确定从自选中删除基金 ${code}？历史数据保留。`, '删除基金', { type: 'warning' })
  await api.deleteFund(code)
  ElMessage.success('已删除')
  await load()
}

async function saveFundField(row: { code: string; name: string; benchmark_index: string; sector?: string; valuation_index?: string }): Promise<void> {
  await api.addFund({ code: row.code, name: row.name, benchmark_index: row.benchmark_index,
                      sector: row.sector ?? '', valuation_index: row.valuation_index ?? '' })
  ElMessage.success(`已更新 ${row.code} 的基金配置`)
  await load()
}

onMounted(load)
</script>

<template>
  <div v-if="bundle" class="settings">
    <el-alert v-if="bundle.overridden.length" type="info" :closable="false" show-icon class="hint"
              :title="`以下配置段使用数据库覆盖值（存于 app_settings，改完即生效）：${bundle.overridden.join('、')}；删除覆盖可恢复内置默认`" />

    <!-- LLM -->
    <el-card shadow="never">
      <template #header>
        <div class="card-title">LLM 配置
          <el-tag size="small" :type="bundle.llm.api_key ? 'success' : 'danger'" effect="plain">
            {{ bundle.llm.api_key ? '已配置' : '未配置密钥' }}
          </el-tag>
          <el-button size="small" text type="info" style="margin-left: auto"
                     @click="resetSection('llm', 'LLM 配置')">恢复默认</el-button>
        </div>
      </template>
      <div class="grid3">
        <div class="field"><label>接口地址（到 /v1）</label>
          <el-input v-model="llm.base_url" placeholder="https://api.openai.com/v1" /></div>
        <div class="field"><label>模型 ID</label>
          <el-input v-model="llm.model" placeholder="如 modelCFy30u" /></div>
        <div class="field"><label>模型显示名</label>
          <el-input v-model="llm.model_name" placeholder="如 DeepSeek-v4-Flash-0731" /></div>
        <div class="field"><label>API Key（留空 = 保持不变）</label>
          <el-input v-model="llm.api_key" type="password" show-password /></div>
        <div class="field"><label>视觉模型 ID（截图导入 OCR）</label>
          <el-input v-model="llm.vision_model" placeholder="留空 = 禁用图片导入" /></div>
        <div class="field"><label>超时（秒）</label>
          <el-input-number v-model="llm.timeout_seconds" :min="5" :max="300" style="width: 100%" /></div>
        <div class="field"><label>消息面采样次数（self-consistency）</label>
          <el-input-number v-model="llm.sentiment_samples" :min="1" :max="5" style="width: 100%" /></div>
        <div class="field"><label>语义缓存 TTL（分钟，0 = 关闭）</label>
          <el-input-number v-model="llm.cache_ttl_minutes" :min="0" :max="10080" :step="60" style="width: 100%" /></div>
      </div>
      <div class="row-actions">
        <el-switch v-model="llm.debate_enabled" active-text="Bull/Bear 辩论层" />
        <el-switch v-model="llm.fallback_enabled" active-text="LLM 失败回退量化基线（fail-closed）" />
        <el-button type="primary" :loading="saving === 'llm'" @click="saveLLM">保存 LLM 配置</el-button>
      </div>
    </el-card>

    <!-- 策略与风控 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-title">策略参数与风控红线
          <el-button size="small" text type="info" style="margin-left: auto"
                     @click="resetSection('settings', '策略参数')">恢复默认</el-button>
        </div>
      </template>
      <div class="grid4">
        <div class="field"><label>技术面权重 {{ strategy.w_tech.toFixed(2) }}</label>
          <el-slider v-model="strategy.w_tech" :min="0" :max="1" :step="0.05" /></div>
        <div class="field"><label>估值面权重 {{ strategy.w_val.toFixed(2) }}</label>
          <el-slider v-model="strategy.w_val" :min="0" :max="1" :step="0.05" /></div>
        <div class="field"><label>消息面权重 {{ strategy.w_sent.toFixed(2) }}</label>
          <el-slider v-model="strategy.w_sent" :min="0" :max="1" :step="0.05" /></div>
        <div class="field"><label>风险面权重 {{ strategy.w_risk.toFixed(2) }}</label>
          <el-slider v-model="strategy.w_risk" :min="0" :max="1" :step="0.05" /></div>
        <div class="field"><label>买入阈值</label>
          <el-input-number v-model="strategy.buy" :min="-1" :max="1" :step="0.05" style="width: 100%" /></div>
        <div class="field"><label>卖出阈值</label>
          <el-input-number v-model="strategy.sell" :min="-1" :max="1" :step="0.05" style="width: 100%" /></div>
        <div class="field"><label>单基仓位上限（风控）</label>
          <el-input-number v-model="strategy.max_pos" :min="0.01" :max="1" :step="0.05" style="width: 100%" /></div>
        <div class="field"><label>组合最大回撤（风控）</label>
          <el-input-number v-model="strategy.max_dd" :min="0.01" :max="0.9" :step="0.01" style="width: 100%" /></div>
        <div class="field"><label>现金缓冲（风控）</label>
          <el-input-number v-model="strategy.cash" :min="0" :max="0.5" :step="0.05" style="width: 100%" /></div>
        <div class="field"><label>动量窗口（交易日，逗号分隔）</label>
          <el-input v-model="strategy.momentum_windows" placeholder="5, 20, 60" /></div>
        <div class="field"><label>动量窗口权重（与窗口一一对应）</label>
          <el-input v-model="strategy.momentum_weights" placeholder="0.2, 0.3, 0.5" /></div>
        <div class="field"><label>估值分位回看窗口（交易日）</label>
          <el-input-number v-model="strategy.val_window" :min="60" :max="2500" :step="30" style="width: 100%" /></div>
        <div class="field"><label>IC 自适应调权
          <el-tooltip placement="top" content="用因子 IC 检验修正 Agent 权重：方向相反降权 ×0.5、显著反向( |t|≥2 )反向使用 ×0.7、显著有效 ×1.2。检验结果见「策略回测」页因子检验卡片。">
            <el-tag size="small" type="info" effect="plain" style="margin-left: 4px">?</el-tag>
          </el-tooltip></label>
          <el-switch v-model="strategy.ic_enabled" /></div>
        <div class="field"><label>IC 检验最小样本数（不足则不调整）</label>
          <el-input-number v-model="strategy.ic_min_samples" :min="10" :max="500" :step="10" :disabled="!strategy.ic_enabled" style="width: 100%" /></div>
        <div class="field"><label>反向信号冷却期（交易日）
          <el-tooltip placement="top" content="跨日翻烙饼防护：距上次反向信号 N 个交易日内，新的反向信号压制为 HOLD 并维持原仓位，避免阈值附近来回穿越被申赎费磨损。0 = 关闭。">
            <el-tag size="small" type="info" effect="plain" style="margin-left: 4px">?</el-tag>
          </el-tooltip></label>
          <el-input-number v-model="strategy.whipsaw_days" :min="0" :max="30" :step="1" style="width: 100%" /></div>
        <div class="field"><label>最小调仓幅度（仓位比例）
          <el-tooltip placement="top" content="费率盈亏平衡防护：目标仓位相对当前仓位的变动低于此值时压制为 HOLD 并维持现仓位——场外基金申赎费率高（申购约 0.15%、赎回 0.5% 起），小额调仓的收益盖不住费率。基准优先用实际持仓，其次上次信号仓位。0 = 关闭。">
            <el-tag size="small" type="info" effect="plain" style="margin-left: 4px">?</el-tag>
          </el-tooltip></label>
          <el-input-number v-model="strategy.min_trade" :min="0" :max="0.5" :step="0.01" :precision="2" style="width: 100%" /></div>
      </div>
      <div class="row-actions">
        <span class="text-muted" style="font-size: 12px">
          权重之和 = {{ (strategy.w_tech + strategy.w_val + strategy.w_sent + strategy.w_risk).toFixed(2) }}（必须为 1.00）
        </span>
        <el-button type="primary" :loading="saving === 'settings'" @click="saveStrategy">保存策略参数</el-button>
      </div>
    </el-card>

    <!-- 辩论 + 调度 + 告警 + 回测 -->
    <el-card shadow="never">
      <template #header><div class="card-title">辩论 / 调度 / 告警 / 回测成本</div></template>
      <div class="grid3">
        <div class="field"><label>辩论轮数（每轮 2 次 LLM 调用）</label>
          <el-input-number v-model="debate.rounds" :min="1" :max="4" style="width: 100%" /></div>
        <div class="field"><label>每日运行时间</label>
          <el-time-select v-model="scheduler.run_at" start="00:00" step="00:30" end="23:30" style="width: 100%" /></div>
        <div class="field"><label>SMTP 端口</label>
          <el-input-number v-model="notifications.smtp_port" :min="1" :max="65535" style="width: 100%" /></div>
        <div class="field"><label>飞书 Webhook</label>
          <el-input v-model="notifications.feishu_webhook" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." /></div>
        <div class="field"><label>企业微信 Webhook</label>
          <el-input v-model="notifications.wecom_webhook" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." /></div>
        <div class="field"><label>SMTP 主机</label>
          <el-input v-model="notifications.smtp_host" placeholder="smtp.qq.com" /></div>
        <div class="field"><label>SMTP 用户 / 发件人</label>
          <el-input v-model="notifications.smtp_user" /></div>
        <div class="field"><label>收件邮箱（逗号分隔）</label>
          <el-input v-model="notifications.email_to" placeholder="a@x.com, b@y.com" /></div>
        <div class="field"><label>回测申购/赎回费率</label>
          <div style="display: flex; gap: 8px">
            <el-input-number v-model="backtestCfg.buy_cost" :min="0" :max="0.05" :step="0.0005" style="flex: 1" />
            <el-input-number v-model="backtestCfg.sell_cost" :min="0" :max="0.05" :step="0.0005" style="flex: 1" />
          </div>
        </div>
        <div class="field"><label>无风险利率（夏普比率用）</label>
          <el-input-number v-model="backtestCfg.risk_free_rate" :min="0" :max="0.2" :step="0.005" style="width: 100%" /></div>
      </div>
      <div class="row-actions wrap">
        <el-switch v-model="debate.consensus_early_stop" active-text="辩论压倒性优势提前终局" />
        <el-switch v-model="scheduler.enabled" active-text="内置每日调度" />
        <el-switch v-model="scheduler.weekdays_only" active-text="仅交易日" />
        <el-switch v-model="notifications.enabled" active-text="告警推送" />
        <el-button :loading="saving === 'debate'" @click="saveDebate">存辩论</el-button>
        <el-button :loading="saving === 'scheduler'" @click="saveScheduler">存调度</el-button>
        <el-button :loading="saving === 'notifications'" @click="saveNotify">存告警</el-button>
        <el-button :loading="saving === 'backtest'" @click="saveBacktest">存回测</el-button>
      </div>
    </el-card>

    <!-- 基金管理 -->
    <el-card shadow="never">
      <template #header><div class="card-title">自选基金（存数据库，页面增删改即时生效）</div></template>
      <el-table :data="bundle.funds" size="small" max-height="300">
        <el-table-column prop="code" label="代码" width="100" />
        <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
        <el-table-column prop="benchmark_index" label="基准指数" width="100" />
        <el-table-column label="估值指数" width="120">
          <template #default="{ row }">
            <el-input v-model="row.valuation_index" size="small" placeholder="默认=基准"
                      @change="saveFundField(row)" />
          </template>
        </el-table-column>
        <el-table-column label="关联板块" width="120">
          <template #default="{ row }">
            <el-input v-model="row.sector" size="small" placeholder="如：消费/白酒"
                      @change="saveFundField(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="right">
          <template #default="{ row }">
            <el-button size="small" type="danger" text @click="removeFund(row.code)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="add-fund">
        <el-input v-model="newFund.code" placeholder="6 位代码" style="width: 110px" />
        <el-input v-model="newFund.name" placeholder="基金名称" style="width: 170px" />
        <el-input v-model="newFund.benchmark_index" placeholder="基准指数(默认000300)" style="width: 160px" />
        <el-input v-model="newFund.valuation_index" placeholder="估值指数(可选)" style="width: 130px" />
        <el-input v-model="newFund.sector" placeholder="关联板块(可选)" style="width: 120px" />
        <el-button type="primary" :loading="fundSaving" @click="addFund">添加基金</el-button>
      </div>
      <div class="text-muted" style="font-size: 12px; margin-top: 8px">
        穿透基准指数：估值面 Agent 用它做持仓穿透估值（推荐 000300 沪深300 / 000905 中证500 / 399997 中证白酒）。
        新增基金后下一次运行流水线会自动采集其净值、持仓与新闻。
      </div>
    </el-card>
  </div>
  <el-empty v-else description="加载配置中…" />
</template>

<style scoped lang="scss">
.settings { display: flex; flex-direction: column; gap: 16px; max-width: 1060px; }
.hint { border-radius: 10px; }
.grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px 18px; }
.grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px 18px; }
.field { display: flex; flex-direction: column; gap: 6px; min-width: 0;
  label { font-size: 12px; color: var(--muted); } }
.row-actions { display: flex; align-items: center; gap: 16px; margin-top: 16px; flex-wrap: wrap;
  .el-button--primary { margin-left: auto; } }
.add-fund { display: flex; gap: 10px; margin-top: 12px; align-items: center; }
@media (max-width: 900px) { .grid3, .grid4 { grid-template-columns: 1fr 1fr; } }
</style>
