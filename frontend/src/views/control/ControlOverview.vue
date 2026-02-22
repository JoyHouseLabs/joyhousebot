<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">概览</h1>
      <p class="page-sub">连接状态、Gateway、会话、Cron、通道、实例</p>
    </div>
    <n-spin :show="overviewLoading">
      <div class="overview-cards">
        <n-card size="small" class="overview-card">
          <template #header>连接状态</template>
          <div class="overview-row">
            <n-tag :type="overview?.health ? 'success' : 'error'" round>
              {{ overview?.health ? '正常' : '异常' }}
            </n-tag>
            <n-button quaternary size="tiny" @click="loadOverview">刷新</n-button>
          </div>
        </n-card>
        <n-card size="small" class="overview-card">
          <template #header>Gateway</template>
          <div class="overview-meta">
            <span>{{ overview?.gateway?.host ?? '—' }}:{{ overview?.gateway?.port ?? '—' }}</span>
            <span v-if="overview?.uptime_seconds != null" class="muted">运行 {{ formatUptime(overview.uptime_seconds) }}</span>
          </div>
        </n-card>
        <n-card size="small" class="overview-card">
          <template #header>会话</template>
          <div class="overview-meta">
            <span>{{ overview?.sessions_count ?? 0 }} 个会话</span>
            <router-link to="/chat" class="link">前往对话</router-link>
          </div>
        </n-card>
        <n-card size="small" class="overview-card">
          <template #header>Cron</template>
          <div class="overview-meta" v-if="overview?.cron != null">
            <span>{{ overview.cron.jobs }} 个任务</span>
            <span v-if="overview.cron.next_wake_at_ms" class="muted">
              下次: {{ formatNextRun(overview.cron.next_wake_at_ms) }}
            </span>
            <span v-else class="muted">无待执行</span>
          </div>
          <div class="overview-meta muted" v-else>未接入（请使用 gateway 启动）</div>
        </n-card>
        <n-card size="small" class="overview-card">
          <template #header>通道</template>
          <div class="overview-meta" v-if="overview?.channels != null">
            <span>已启用 {{ overview.channels.count }}，运行中 {{ overview.channels.running }}</span>
          </div>
          <div class="overview-meta muted" v-else>未接入</div>
        </n-card>
        <n-card size="small" class="overview-card">
          <template #header>实例</template>
          <div class="overview-meta" v-if="overview?.presence_count != null">
            <span>{{ overview.presence_count }} 个连接</span>
            <span class="muted">控制台 / WebSocket 客户端</span>
          </div>
          <div class="overview-meta muted" v-else>—</div>
        </n-card>
      </div>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useGatewayInject } from '../../composables/useGateway'
import { useMessage } from 'naive-ui'
import type { ControlOverview } from '../../api/control'

const message = useMessage()
const gateway = useGatewayInject()
const overview = ref<ControlOverview | null>(null)
const overviewLoading = ref(false)

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function formatNextRun(ms: number): string {
  try {
    return new Date(ms).toLocaleString()
  } catch {
    return String(ms)
  }
}

async function loadOverview() {
  if (!gateway?.request) return
  overviewLoading.value = true
  try {
    overview.value = (await gateway.request<ControlOverview>('health')) ?? null
  } catch (e) {
    message.error(String(e))
  } finally {
    overviewLoading.value = false
  }
}

onMounted(loadOverview)
watch(() => gateway?.connected, (connected) => {
  if (connected) void loadOverview()
})
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
.overview-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
  margin-top: 16px;
}
.overview-card :deep(.n-card-header) {
  font-weight: 600;
  font-size: 13px;
}
.overview-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.overview-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.overview-meta .muted {
  color: var(--muted);
  font-size: 12px;
}
.overview-meta .link {
  color: var(--accent);
  text-decoration: none;
  font-size: 12px;
}
.overview-meta .link:hover {
  text-decoration: underline;
}
</style>
