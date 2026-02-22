<template>
  <div class="config-panel" v-if="config !== undefined">
    <div class="config-panel-header">
      <h2 class="config-panel-title">命令</h2>
      <p class="config-panel-desc">原生命令与技能斜杠命令</p>
    </div>
    <n-form label-placement="left" label-width="140" class="config-form">
      <n-form-item label="原生命令 (/new, /help)">
        <n-select
          v-model:value="nativeVal"
          :options="[
            { label: 'auto', value: 'auto' },
            { label: '开启', value: true },
            { label: '关闭', value: false },
          ]"
          style="width: 160px"
        />
      </n-form-item>
      <n-form-item label="技能斜杠命令">
        <n-select
          v-model:value="nativeSkillsVal"
          :options="[
            { label: 'auto', value: 'auto' },
            { label: '开启', value: true },
            { label: '关闭', value: false },
          ]"
          style="width: 160px"
        />
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

function ensureCommands() {
  const c = props.config as Record<string, unknown>
  if (!c) return
  if (!c.commands || typeof c.commands !== 'object') c.commands = { native: 'auto', native_skills: 'auto' }
}

const nativeVal = computed({
  get() {
    ensureCommands()
    const v = (props.config as Record<string, unknown>)?.commands as Record<string, unknown>
    return v?.native ?? 'auto'
  },
  set(val: string | boolean) {
    ensureCommands()
    const v = (props.config as Record<string, unknown>)?.commands as Record<string, unknown>
    if (v) v.native = val
  },
})

const nativeSkillsVal = computed({
  get() {
    ensureCommands()
    const v = (props.config as Record<string, unknown>)?.commands as Record<string, unknown>
    return v?.native_skills ?? 'auto'
  },
  set(val: string | boolean) {
    ensureCommands()
    const v = (props.config as Record<string, unknown>)?.commands as Record<string, unknown>
    if (v) v.native_skills = val
  },
})
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
  max-width: 480px;
}
</style>
