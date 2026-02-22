<template>
  <div class="config-panel" v-if="config !== undefined">
    <div class="config-panel-header">
      <h2 class="config-panel-title">环境变量</h2>
      <p class="config-panel-desc">启动时注入（已有同名变量不会被覆盖）</p>
    </div>
    <div class="env-list">
      <div v-for="(val, key) in varsObj" :key="String(key)" class="env-row">
        <n-input :value="String(key)" disabled class="env-key" />
        <n-input
          :value="String(val)"
          type="password"
          show-password-on="click"
          placeholder="值"
          class="env-val"
          @update:value="(v) => setVar(String(key), v)"
        />
        <n-button quaternary type="error" size="small" @click="removeVar(key)">删除</n-button>
      </div>
    </div>
    <div class="env-add">
      <n-input v-model:value="newKey" placeholder="变量名" class="env-key" @keyup.enter="addVar" />
      <n-input v-model:value="newVal" placeholder="值" class="env-val" @keyup.enter="addVar" />
      <n-button size="small" @click="addVar">添加</n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()
const newKey = ref('')
const newVal = ref('')

function ensureEnv() {
  const c = props.config as Record<string, unknown>
  if (!c) return
  if (!c.env || typeof c.env !== 'object') c.env = { vars: {} }
  const e = c.env as Record<string, unknown>
  if (!e.vars || typeof e.vars !== 'object') e.vars = {}
}

const varsObj = computed(() => {
  ensureEnv()
  const e = (props.config as Record<string, unknown>)?.env as Record<string, unknown>
  const v = e?.vars as Record<string, string> | undefined
  return v ?? {}
})

function setVar(key: string, value: string) {
  ensureEnv()
  const e = (props.config as Record<string, unknown>)?.env as Record<string, unknown>
  const v = e?.vars as Record<string, string>
  if (v) v[key] = value
}

function removeVar(key: string) {
  const e = (props.config as Record<string, unknown>)?.env as Record<string, unknown>
  const v = e?.vars as Record<string, string>
  if (v) delete v[key]
}

function addVar() {
  const k = newKey.value.trim()
  if (!k) return
  ensureEnv()
  const e = (props.config as Record<string, unknown>)?.env as Record<string, unknown>
  const v = e?.vars as Record<string, string>
  if (v) {
    v[k] = newVal.value
    newKey.value = ''
    newVal.value = ''
  }
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
.env-list {
  max-width: 640px;
}
.env-row,
.env-add {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.env-key {
  width: 180px;
  flex-shrink: 0;
}
.env-val {
  flex: 1;
  min-width: 0;
}
.env-add {
  margin-top: 12px;
}
</style>
