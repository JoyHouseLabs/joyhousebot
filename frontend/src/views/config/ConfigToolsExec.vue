<template>
  <div class="config-tools-block" v-if="exec">
    <n-form label-placement="left" label-width="160" class="config-form">
      <n-form-item label="Timeout (秒)">
        <n-input-number v-model:value="exec.timeout" :min="1" style="width: 100%" />
      </n-form-item>
      <n-form-item label="Shell 模式">
        <n-switch v-model:value="exec.shell_mode" />
        <span class="form-hint">开启后通过 shell 执行，支持管道与重定向</span>
      </n-form-item>
      <n-form-item label="容器隔离">
        <n-switch v-model:value="exec.container_enabled" />
      </n-form-item>
      <n-form-item v-if="exec.container_enabled" label="容器镜像">
        <n-input v-model:value="exec.container_image" placeholder="alpine:3.18" />
      </n-form-item>
      <n-form-item v-if="exec.container_enabled" label="工作区挂载">
        <n-input v-model:value="exec.container_workspace_mount" placeholder="主机路径" />
      </n-form-item>
      <n-form-item v-if="exec.container_enabled" label="容器网络">
        <n-input v-model:value="exec.container_network" placeholder="none" />
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

const exec = computed(() => {
  const tools = props.config?.tools as Record<string, unknown> | undefined
  const e = tools?.exec as Record<string, unknown> | undefined
  if (!e) return null
  return e as {
    timeout: number
    shell_mode: boolean
    container_enabled: boolean
    container_image: string
    container_workspace_mount: string
    container_network: string
  }
})
</script>

<style scoped>
.config-form {
  max-width: 520px;
}
.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--n-text-color-3);
}
</style>
