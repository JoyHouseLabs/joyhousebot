<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">通道状态</h1>
      <p class="page-sub">各通道启用与运行状态</p>
    </div>
    <n-spin :show="channelsLoading">
      <div class="channels-grid">
        <n-card
          v-for="ch in channelsList"
          :key="ch.name"
          size="small"
          class="channel-card"
          :class="{ enabled: ch.enabled, running: ch.running }"
        >
          <template #header>
            <span class="channel-name">{{ channelLabel(ch.name) }}</span>
            <n-tag :type="ch.running ? 'success' : ch.enabled ? 'warning' : 'default'" size="small" round>
              {{ ch.running ? '运行中' : ch.enabled ? '已启用' : '未启用' }}
            </n-tag>
          </template>
          <div class="channel-meta">
            <span>配置: {{ ch.enabled ? '已启用' : '未启用' }}</span>
            <span>运行时: {{ ch.running ? '已连接' : '未连接' }}</span>
          </div>
          <n-button quaternary size="tiny" style="margin-top: 8px" @click="loadChannels">刷新</n-button>
        </n-card>
      </div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useGatewayInject } from '../../composables/useGateway'
import { useMessage } from 'naive-ui'
import type { ChannelStatus } from '../../api/control'

const message = useMessage()
const gateway = useGatewayInject()
const channelsList = ref<ChannelStatus[]>([])
const channelsLoading = ref(false)

const channelLabels: Record<string, string> = {
  telegram: 'Telegram',
  whatsapp: 'WhatsApp',
  feishu: '飞书',
  dingtalk: '钉钉',
  discord: 'Discord',
  email: 'Email',
  slack: 'Slack',
  qq: 'QQ',
  mochat: 'Mochat',
}

function channelLabel(name: string) {
  return channelLabels[name] ?? name
}

async function loadChannels() {
  if (!gateway?.request) return
  channelsLoading.value = true
  try {
    const res = await gateway.request<{ ok: boolean; channels: ChannelStatus[] }>('channels.status')
    channelsList.value = res?.channels ?? []
  } catch (e) {
    message.error(String(e))
  } finally {
    channelsLoading.value = false
  }
}

onMounted(loadChannels)
watch(() => gateway?.connected, (connected) => { if (connected) void loadChannels() })
</script>

<style scoped>
.control-page {
  padding: 16px;
  max-width: 960px;
}
.content-header {
  margin-bottom: 16px;
}
.page-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0 0 4px 0;
}
.page-sub {
  font-size: 0.875rem;
  color: var(--text-muted, #666);
  margin: 0;
}
.channels-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
  margin-top: 16px;
}
.channel-card :deep(.n-card-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  font-size: 13px;
}
.channel-name {
  text-transform: capitalize;
}
.channel-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
</style>
