<template>
  <div class="config-tools-block" v-if="cr">
    <n-form label-placement="left" label-width="160" class="config-form">
      <n-form-item label="启用">
        <n-switch v-model:value="cr.enabled" />
      </n-form-item>
      <n-form-item label="默认后端">
        <n-input v-model:value="cr.default_backend" placeholder="claude_code" />
      </n-form-item>
      <n-form-item label="默认模式">
        <n-select
          v-model:value="cr.default_mode"
          :options="[
            { label: 'auto', value: 'auto' },
            { label: 'host', value: 'host' },
            { label: 'container', value: 'container' },
          ]"
        />
      </n-form-item>
      <n-form-item label="超时 (秒)">
        <n-input-number v-model:value="cr.timeout" :min="1" style="width: 100%" />
      </n-form-item>
      <n-form-item label="需审批后执行">
        <n-switch v-model:value="cr.require_approval" />
      </n-form-item>
      <n-form-item label="Claude Code 命令">
        <n-input v-model:value="cr.claude_code_command" placeholder="claude" />
      </n-form-item>
      <n-form-item label="容器镜像">
        <n-input v-model:value="cr.container_image" placeholder="可选" />
      </n-form-item>
      <n-form-item label="工作区挂载">
        <n-input v-model:value="cr.container_workspace_mount" />
      </n-form-item>
      <n-form-item label="容器网络">
        <n-input v-model:value="cr.container_network" placeholder="none" />
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

const cr = computed(() => {
  const tools = props.config?.tools as Record<string, unknown> | undefined
  const c = tools?.code_runner as Record<string, unknown> | undefined
  if (!c) return null
  return c as {
    enabled: boolean
    default_backend: string
    default_mode: string
    timeout: number
    require_approval: boolean
    claude_code_command: string
    container_image: string
    container_workspace_mount: string
    container_user: string
    container_network: string
  }
})
</script>

<style scoped>
.config-form {
  max-width: 520px;
}
</style>
