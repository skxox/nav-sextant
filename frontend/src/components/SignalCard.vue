<script setup lang="ts">
import { computed } from 'vue'
import { usePanelStore, AGENT_LABELS } from '@/stores/panel'
import type { AgentName, SignalAction, DebateResult } from '@/api/types'

const props = defineProps<{ signal: { fund_code: string; fund_name: string; action: SignalAction; composite_score: number; target_position: number; risk_passed: boolean; reasons: string[]; agents: Record<AgentName, { score: number; confidence: number; fallback_used: boolean; data_quality?: number; summary: string }>; risk_checks: { name: string; passed: boolean; detail: string }[]; debate?: DebateResult }, active: boolean }>()
const emit = defineEmits<{ select: [code: string] }>()

const store = usePanelStore()
const expanded = computed(() => props.signal.fund_code === store.selectedFund)
const scoreClass = computed(() => (props.signal.composite_score >= 0 ? 'text-pos' : 'text-neg'))
</script>

<template>
  <div class="signal-card" :class="{ active }" @click="emit('select', signal.fund_code)">
    <div class="row">
      <span class="name">{{ signal.fund_name }}</span>
      <span class="code text-muted">{{ signal.fund_code }}</span>
      <el-tag :type="signal.action === 'BUY' ? 'danger' : signal.action === 'SELL' ? 'success' : 'warning'" effect="dark" size="small">
        {{ { BUY: '买入', SELL: '卖出', HOLD: '持有' }[signal.action] }}
      </el-tag>
      <span class="score" :class="scoreClass">{{ signal.composite_score.toFixed(3) }}</span>
    </div>
    <div class="meta">
      <span>建议仓位 <b>{{ (signal.target_position * 100).toFixed(1) }}%</b></span>
      <span>风控 <span :class="signal.risk_passed ? 'ok-text' : 'no-text'">{{ signal.risk_passed ? '通过' : '拦截' }}</span></span>
    </div>
    <div class="chips">
      <el-tooltip v-for="(a, name) in signal.agents" :key="name"
                  :content="a.summary" placement="top" :show-after="200">
        <span class="chip" :class="{ fallback: a.fallback_used || (a.data_quality ?? 1) < 0.6 }">
          {{ AGENT_LABELS[name as AgentName] }}
          <b :class="a.score >= 0 ? 'text-pos' : 'text-neg'">{{ a.score.toFixed(2) }}</b>
          <i class="conf">确{{ a.confidence.toFixed(2) }}</i>
          <template v-if="a.fallback_used">（回退）</template>
          <template v-else-if="(a.data_quality ?? 1) < 0.6">（数据不足）</template>
        </span>
      </el-tooltip>
    </div>
    <el-collapse-transition>
      <div v-if="expanded" class="detail">
        <div v-for="(a, name) in signal.agents" :key="'d' + name" class="agent-line">
          <b>{{ AGENT_LABELS[name as AgentName] }}：</b>{{ a.summary }}
        </div>
        <div v-if="signal.debate?.triggered" class="debate">
          <b>Bull/Bear 辩论（{{ signal.debate.rounds_used || 1 }} 轮）：</b>{{ signal.debate.agree ? '维持信号' : '风控审核否决' }} → {{ signal.debate.final_action }}
          <div v-for="(r, i) in signal.debate.rounds" :key="'r' + i" class="round">
            <div class="round-title text-muted">第 {{ r.round }} 轮</div>
            <div class="side bull">Bull（{{ r.bull_confidence.toFixed(2) }}）：{{ r.bull_points.join('；') }}</div>
            <div class="side bear">Bear（{{ r.bear_confidence.toFixed(2) }}）：{{ r.bear_points.join('；') }}</div>
          </div>
          <div class="judge">审核：{{ signal.debate.judge_reasoning }}</div>
        </div>
        <div v-else-if="signal.debate && !signal.debate.triggered" class="debate text-muted">
          <b>辩论层：</b>{{ signal.debate.unavailable_reason === 'debate_disabled' ? '未启用' : '未触发（HOLD 信号）' }}
        </div>
      </div>
    </el-collapse-transition>
  </div>
</template>

<style scoped>
.signal-card { border: 1px solid var(--border); border-radius: 10px; padding: 14px; cursor: pointer; transition: border-color .15s; }
.signal-card:hover { border-color: var(--accent); }
.signal-card.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.row { display: flex; align-items: center; gap: 10px; }
.name { font-weight: 600; font-size: 15px; }
.code { font-size: 12px; }
.score { margin-left: auto; font-size: 16px; }
.meta { display: flex; gap: 16px; margin-top: 8px; font-size: 12px; color: var(--muted); }
.ok-text { color: var(--sell); font-weight: 600; }
.no-text { color: var(--buy); font-weight: 600; }
.chips { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
.chip { font-size: 11px; padding: 3px 8px; border-radius: 5px; background: var(--panel-2); color: var(--text); cursor: default; border: 1px solid var(--border); }
.chip.fallback { opacity: .55; }
.chip .conf { font-style: normal; color: var(--muted); font-size: 10px; margin-left: 2px; }
.detail { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 10px; font-size: 12px; color: var(--muted); line-height: 1.8; }
.debate { margin-top: 6px; border-top: 1px dashed var(--border); padding-top: 6px; }
.round { margin-top: 6px; padding-left: 8px; border-left: 2px solid var(--border); }
.round-title { font-size: 11px; margin-bottom: 2px; }
.side.bull { color: var(--buy); }
.side.bear { color: var(--sell); }
.judge { margin-top: 4px; color: var(--warn); }
</style>
