<template>
  <div class="config-panel" v-if="exec">
    <div class="config-panel-header">
      <h2 class="config-panel-title">审批</h2>
      <p class="config-panel-desc">执行 (exec) 审批转发：将审批请求发往会话或指定目标</p>
    </div>
    <n-form label-placement="left" label-width="140" class="config-form">
      <n-form-item label="启用">
        <n-switch v-model:value="exec.enabled" />
      </n-form-item>
      <n-form-item label="模式">
        <n-select
          v-model:value="exec.mode"
          :options="[
            { label: 'session', value: 'session' },
            { label: 'targets', value: 'targets' },
            { label: 'both', value: 'both' },
          ]"
          style="width: 160px"
        />
      </n-form-item>
      <n-form-item label="Agent 过滤">
        <n-dynamic-tags v-model:value="exec.agent_filter" />
        <p class="form-hint">留空表示全部；填写则仅这些 agent 触发转发</p>
      </n-form-item>
      <n-form-item label="会话过滤">
        <n-dynamic-tags v-model:value="exec.session_filter" />
      </n-form-item>
    </n-form>
    <div class="targets-section">
      <h3 class="subsection-title">转发目标</h3>
      <div v-for="(t, idx) in (exec.targets || [])" :key="idx" class="target-row">
        <n-input v-model:value="t.channel" placeholder="channel" class="target-field" />
        <n-input v-model:value="t.to" placeholder="to" class="target-field" />
        <n-input v-model:value="t.account_id" placeholder="account_id" class="target-field" clearable />
        <n-input v-model:value="t.thread_id" placeholder="thread_id" class="target-field" clearable />
        <n-button quaternary type="error" size="small" @click="removeTarget(idx)">删除</n-button>
      </div>
      <n-button size="small" quaternary @click="addTarget">添加目标</n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

function ensureApprovals() {
  const c = props.config as Record<string, unknown>
  if (!c) return
  if (!c.approvals || typeof c.approvals !== 'object') c.approvals = {}
  const a = c.approvals as Record<string, unknown>
  if (!a.exec || typeof a.exec !== 'object') {
    a.exec = {
      enabled: false,
      mode: 'session',
      agent_filter: null,
      session_filter: null,
      targets: null,
    }
  }
  const e = a.exec as Record<string, unknown>
  if (!Array.isArray(e.agent_filter)) e.agent_filter = e.agent_filter ? [e.agent_filter] : []
  if (!Array.isArray(e.session_filter)) e.session_filter = e.session_filter ? [e.session_filter] : []
  if (!Array.isArray(e.targets)) e.targets = e.targets ? [e.targets] : []
}

const exec = computed(() => {
  ensureApprovals()
  return (props.config as Record<string, unknown>)?.approvals?.exec as Record<string, unknown> | undefined
})

function addTarget() {
  ensureApprovals()
  const e = (props.config as Record<string, unknown>)?.approvals?.exec as Record<string, unknown>
  const t = e?.targets as Array<Record<string, unknown>>
  if (t) t.push({ channel: '', to: '', account_id: '', thread_id: '' })
}

function removeTarget(idx: number) {
  const e = (props.config as Record<string, unknown>)?.approvals?.exec as Record<string, unknown>
  const t = e?.targets as Array<Record<string, unknown>>
  if (t) t.splice(idx, 1)
}
</script>

<style scoped>
.config-panel-header {
  margin-bottom: 16px;
}
.config-panel-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px 0;
}
.config-panel-desc {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 0;
}
.config-form {
  max-width: 520px;
}
.form-hint {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 4px 0 0 0;
}
.targets-section {
  margin-top: 20px;
}
.subsection-title {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 8px 0;
}
.target-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.target-field {
  width: 120px;
  min-width: 0;
}
</style>
