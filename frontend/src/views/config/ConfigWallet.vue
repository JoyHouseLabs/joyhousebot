<template>
  <div class="config-panel">
    <div class="config-panel-header">
      <h2 class="config-panel-title">钱包配置</h2>
      <p class="config-panel-desc">启用后生成 EVM 地址，私钥以密码加密存储（仅支持 CLI 解密）</p>
    </div>
    <n-form label-placement="left" label-width="120" class="config-form">
      <n-form-item label="启用钱包">
        <n-space align="center">
          <n-switch
            :value="config?.wallet?.enabled ?? false"
            @update:value="onEnabledChange"
          />
          <span>{{ config?.wallet?.enabled ? '已启用' : '未启用' }}</span>
        </n-space>
      </n-form-item>
      <template v-if="config?.wallet?.enabled">
        <n-form-item v-if="config.wallet.address" label="EVM 地址">
          <n-input :value="config.wallet.address" readonly class="address-readonly" />
        </n-form-item>
        <n-form-item label="密码" required>
          <n-input
            v-model:value="password"
            type="password"
            placeholder="不少于8位，须包含大小写字母（仅启用时必填）"
            show-password-on="click"
            clearable
            style="max-width: 320px"
          />
          <template #feedback>
            <span v-if="passwordHint" class="hint" :class="{ error: passwordError }">{{ passwordHint }}</span>
            <span v-else-if="config?.wallet?.enabled && !config?.wallet?.address" class="hint">保存后将生成 EVM 地址</span>
          </template>
        </n-form-item>
      </template>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{
  config: ConfigData | null
}>()

const password = ref('')

const passwordHint = computed(() => {
  const p = password.value
  if (!p) return ''
  if (p.length < 8) return '密码至少 8 位'
  if (!/[A-Z]/.test(p)) return '密码须包含大写字母'
  if (!/[a-z]/.test(p)) return '密码须包含小写字母'
  return '符合要求'
})

const passwordError = computed(() => {
  const p = password.value
  if (!p) return false
  return p.length < 8 || !/[A-Z]/.test(p) || !/[a-z]/.test(p)
})

function onEnabledChange(enabled: boolean) {
  if (!props.config) return
  if (!props.config.wallet) {
    (props.config as Record<string, unknown>).wallet = { enabled: false, address: '' }
  }
  const w = props.config.wallet as { enabled: boolean; address: string }
  w.enabled = enabled
  if (!enabled) password.value = ''
}

/** 供父组件保存时获取 wallet 提交内容 */
function getWalletUpdatePayload(): { enabled: boolean; password?: string } {
  const enabled = props.config?.wallet?.enabled ?? false
  return {
    enabled,
    ...(enabled && password.value ? { password: password.value } : {}),
  }
}

defineExpose({ getWalletUpdatePayload })
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
.address-readonly {
  font-family: ui-monospace, monospace;
  max-width: 420px;
}
.hint {
  font-size: 12px;
  color: var(--n-text-color-3);
}
.hint.error {
  color: var(--n-error-color);
}
</style>
