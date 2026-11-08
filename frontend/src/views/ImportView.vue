<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { api } from '@/api/client'
import { usePanelStore } from '@/stores/panel'
import type { ImportResult } from '@/api/types'

const store = usePanelStore()
const textInput = ref('')
const importing = ref(false)
const result = ref<ImportResult | null>(null)

const placeholder = `每行一条持仓，格式（空格/逗号分隔）：
110022 0.35 1200 2.85
161725 35%
# 井号开头为注释；仓位支持 0.35 或 35% 两种写法`

function previewRows(): string {
  return textInput.value.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).length.toString()
}

async function importText(): Promise<void> {
  importing.value = true
  try {
    result.value = await api.importText(textInput.value)
    ElMessage.success(`成功导入 ${result.value.imported.length} 条持仓`)
  } finally {
    importing.value = false
  }
}

async function onFileChange(file: File): Promise<void> {
  importing.value = true
  try {
    result.value = await api.importImage(file)
    ElMessage.success(`截图识别成功，导入 ${result.value.imported.length} 条持仓`)
  } catch {
    /* 拦截器已提示（未配置 vision_model 等） */
  } finally {
    importing.value = false
  }
}

function beforeUpload(file: File): boolean {
  void onFileChange(file)
  return false // 阻止 el-upload 自动上传，走自定义逻辑
}
</script>

<template>
  <div class="import-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-title">截图导入（自动识别持仓）</div>
      </template>
      <el-upload drag accept="image/png,image/jpeg,image/webp" :show-file-list="false"
                 :before-upload="beforeUpload" :auto-upload="false" class="drop">
        <div v-loading="importing" class="drop-inner">
          <el-icon :size="40" color="var(--accent)"><UploadFilled /></el-icon>
          <div class="drop-title">拖拽券商/基金 App 持仓截图到此处，或点击选择图片</div>
          <div class="drop-sub text-muted">
            由视觉多模态模型识别基金代码、仓位、份额与成本；需在 config.yaml 配置 <code>llm.vision_model</code>
          </div>
        </div>
      </el-upload>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-title">文本导入（粘贴/编辑）</div>
      </template>
      <el-input v-model="textInput" type="textarea" :rows="8" :placeholder="placeholder" class="ta" />
      <div class="ta-actions">
        <span class="text-muted">共 {{ previewRows() }} 条待导入</span>
        <el-button type="primary" :loading="importing" :disabled="!previewRows() || previewRows() === '0'"
                   @click="importText">解析并导入</el-button>
      </div>
    </el-card>

    <el-card v-if="result" shadow="never">
      <template #header>
        <div class="card-title">
          导入结果
          <el-tag type="success" size="small" effect="dark">{{ result.imported.length }} 条成功</el-tag>
          <el-tag v-if="result.skipped.length" type="warning" size="small" effect="plain">
            {{ result.skipped.length }} 条跳过
          </el-tag>
        </div>
      </template>
      <el-table :data="result.imported" size="small">
        <el-table-column prop="fund_code" label="基金代码" width="110" />
        <el-table-column label="仓位" width="100" align="right">
          <template #default="{ row }">{{ (row.position * 100).toFixed(1) }}%</template>
        </el-table-column>
        <el-table-column prop="shares" label="份额" width="120" align="right" />
        <el-table-column prop="cost" label="成本单价" width="120" align="right" />
        <el-table-column label="校验">
          <template #default="{ row }">
            <el-tag v-if="!store.funds.some((f) => f.code === row.fund_code)" type="warning" size="small" effect="plain">
              不在自选列表（config.yaml funds），估值与信号不会覆盖
            </el-tag>
            <el-tag v-else type="success" size="small" effect="plain">已匹配自选</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="result.skipped.length" class="skipped">
        <div class="text-muted" style="margin-bottom: 6px">跳过的内容：</div>
        <code v-for="(s, i) in result.skipped" :key="i" class="skip-line">{{ s }}</code>
      </div>
    </el-card>
  </div>
</template>

<style scoped lang="scss">
.import-page { display: flex; flex-direction: column; gap: 16px; max-width: 860px; }
.drop-inner { padding: 28px 0 24px; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.drop-title { font-size: 14px; font-weight: 600; }
.drop-sub { font-size: 12px; code { color: var(--accent); } }
.ta-actions { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; }
.skipped { margin-top: 12px; }
.skip-line { display: block; font-size: 12px; color: var(--muted); padding: 2px 0; }
</style>
