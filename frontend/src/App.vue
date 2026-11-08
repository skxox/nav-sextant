<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  DataLine, DataAnalysis, Coin, TrendCharts, Clock, Document,
  Upload, Setting, Fold, Expand, VideoPlay, Compass,
} from '@element-plus/icons-vue'
import { usePanelStore } from '@/stores/panel'

const route = useRoute()
const store = usePanelStore()
onMounted(() => { if (!store.meta) void store.loadMetaSafe() })

const collapsed = ref(false)

const navs = [
  { path: '/', label: '仪表盘', icon: Compass },
  { path: '/signals', label: '每日信号', icon: DataAnalysis },
  { path: '/holdings', label: '持仓管理', icon: Coin },
  { path: '/history', label: '信号历史', icon: Clock },
  { path: '/backtest', label: '策略回测', icon: TrendCharts },
  { path: '/logs', label: 'Agent 日志', icon: Document },
  { path: '/import', label: '数据导入', icon: Upload },
  { path: '/settings', label: '系统设置', icon: Setting },
]

const pageTitle = computed(() => navs.find((n) => n.path === route.path)?.label ?? '')

async function onRun(): Promise<void> {
  const r = await store.runPipeline()
  const { ElMessage } = await import('element-plus')
  if (r.failed > 0) ElMessage.warning(`分析完成：${r.ok} 只成功，${r.failed} 只失败`)
  else ElMessage.success(`分析完成：${r.ok} 只基金`)
}
</script>

<template>
  <div class="shell">
    <!-- 侧边栏 -->
    <aside class="sidebar" :class="{ collapsed }">
      <div class="brand">
        <el-icon :size="24" color="var(--accent)"><DataLine /></el-icon>
        <div v-if="!collapsed" class="brand-text">
          <div class="brand-name">nav-sextant</div>
          <div class="brand-sub">基金六分仪</div>
        </div>
      </div>

      <nav class="menu">
        <router-link v-for="n in navs" :key="n.path" :to="n.path" class="menu-item"
                     :class="{ active: route.path === n.path }">
          <el-icon :size="17"><component :is="n.icon" /></el-icon>
          <span v-if="!collapsed">{{ n.label }}</span>
        </router-link>
      </nav>

      <div class="sidebar-foot">
        <button class="collapse-btn" @click="collapsed = !collapsed">
          <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
          <span v-if="!collapsed">收起菜单</span>
        </button>
        <div v-if="!collapsed" class="ver">v{{ store.meta?.version ?? '0.2.0' }}</div>
      </div>
    </aside>

    <!-- 主区 -->
    <div class="shell-main">
      <header class="topbar">
        <div class="page-title">{{ pageTitle }}</div>
        <div class="topbar-right">
          <el-tag v-if="store.meta?.llm.configured" size="small" effect="dark" class="model-tag">
            {{ store.modelName }}
          </el-tag>
          <el-tag v-else size="small" type="warning" effect="dark">LLM 未配置</el-tag>
          <el-button type="primary" :icon="VideoPlay" :loading="store.pipelineRunning"
                     @click="onRun">
            {{ store.pipelineRunning ? '分析中…' : '运行流水线' }}
          </el-button>
        </div>
      </header>
      <main class="main-area">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped lang="scss">
.shell { display: flex; min-height: 100vh; }

.sidebar {
  width: 216px;
  flex-shrink: 0;
  background: var(--sidebar);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  transition: width .18s ease;
  &.collapsed { width: 64px; }
}

.brand {
  display: flex; align-items: center; gap: 10px;
  padding: 18px 16px 16px;
  border-bottom: 1px solid var(--border);
  .brand-name { font-size: 16px; font-weight: 700; letter-spacing: .3px; }
  .brand-sub { font-size: 11px; color: var(--muted); }
}

.menu {
  flex: 1;
  padding: 10px 8px;
  display: flex; flex-direction: column; gap: 2px;
  overflow-y: auto;
}

.menu-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  color: var(--muted);
  text-decoration: none;
  font-size: 13.5px;
  white-space: nowrap;
  transition: background .12s, color .12s;
  &:hover { background: var(--panel-2); color: var(--text); }
  &.active {
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    color: var(--accent);
    font-weight: 600;
  }
}

.sidebar-foot {
  padding: 10px 12px;
  border-top: 1px solid var(--border);
  .collapse-btn {
    display: flex; align-items: center; gap: 8px;
    background: none; border: none; color: var(--muted);
    font-size: 12px; cursor: pointer; padding: 6px 4px; width: 100%;
    &:hover { color: var(--text); }
  }
  .ver { font-size: 11px; color: var(--muted); margin-top: 4px; padding-left: 4px; }
}

.shell-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }

.topbar {
  height: 56px;
  display: flex; align-items: center;
  padding: 0 24px;
  background: var(--sidebar);
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 20;
  .page-title { font-size: 16px; font-weight: 600; }
  .topbar-right { margin-left: auto; display: flex; align-items: center; gap: 12px; }
}

.main-area {
  flex: 1;
  padding: 20px 24px 32px;
  max-width: 1440px;
  width: 100%;
  margin: 0 auto;
}
</style>
