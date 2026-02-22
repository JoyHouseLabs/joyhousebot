<template>
  <div class="control-page">
    <div class="content-header">
      <h1 class="page-title">沙箱</h1>
      <p class="page-sub">Joyhousebot 沙箱容器列表与策略说明，支持按范围重建（移除）容器</p>
    </div>
    <n-spin :show="loading">
      <div class="sandbox-explain" v-if="explain">
        <n-card size="small" title="当前策略与后端" class="explain-card">
          <n-descriptions label-placement="left" :column="2" size="small">
            <n-descriptions-item label="后端">
              <n-tag :type="explain.docker_available ? 'success' : 'default'" size="small">
                {{ explain.backend }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="容器数">{{ explain.containers_count }}</n-descriptions-item>
            <n-descriptions-item label="会话">{{ explain.session }}</n-descriptions-item>
            <n-descriptions-item label="Agent">{{ explain.agent }}</n-descriptions-item>
            <n-descriptions-item label="工作区限制" :span="2">
              {{ explain.policy?.restrict_to_workspace ? '是' : '否' }}
            </n-descriptions-item>
            <n-descriptions-item label="执行超时(秒)" :span="2">
              {{ explain.policy?.exec_timeout ?? '—' }}
            </n-descriptions-item>
          </n-descriptions>
        </n-card>
      </div>
      <div class="sandbox-toolbar">
        <n-button quaternary size="small" @click="load">刷新</n-button>
        <n-button type="primary" size="small" @click="confirmRecreate(false)">全部重建</n-button>
        <n-button size="small" @click="confirmRecreate(true)">仅重建浏览器容器</n-button>
      </div>
      <n-data-table
        :columns="columns"
        :data="containers"
        :bordered="false"
        size="small"
        class="sandbox-table"
        :scroll-x="800"
      />
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { h, ref, onMounted } from 'vue'
import { NButton, NTag, useMessage, useDialog } from 'naive-ui'
import {
  listSandboxContainers,
  getSandboxExplain,
  sandboxRecreate,
  type SandboxContainerItem,
  type SandboxExplainResponse,
} from '../../api/sandbox'

const message = useMessage()
const dialog = useDialog()
const containers = ref<SandboxContainerItem[]>([])
const explain = ref<SandboxExplainResponse | null>(null)
const loading = ref(false)

const columns = [
  { title: 'ID', key: 'id', width: 100, ellipsis: { tooltip: true } },
  { title: '名称', key: 'names', width: 180, ellipsis: { tooltip: true } },
  { title: '镜像', key: 'image', width: 180, ellipsis: { tooltip: true } },
  {
    title: '类型',
    key: 'browser',
    width: 80,
    render: (row: SandboxContainerItem) =>
      row.browser ? h(NTag, { type: 'info', size: 'small' }, () => '浏览器') : '通用',
  },
]

async function load() {
  loading.value = true
  try {
    const [listRes, explainRes] = await Promise.all([
      listSandboxContainers(false),
      getSandboxExplain(),
    ])
    containers.value = listRes.items ?? []
    explain.value = explainRes
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

function confirmRecreate(browserOnly: boolean) {
  const title = browserOnly ? '仅重建浏览器容器' : '全部重建沙箱容器'
  const content = browserOnly
    ? '将移除所有带 browser 标签的沙箱容器，确定继续？'
    : '将移除当前所有沙箱容器，确定继续？'
  dialog.warning({
    title,
    content,
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const res = await sandboxRecreate({ all: true, browser_only: browserOnly, force: true })
        message.success(`已移除 ${res.removed?.length ?? 0} 个容器`)
        await load()
      } catch (e) {
        message.error(String(e))
      }
    },
  })
}

onMounted(load)
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
.sandbox-explain {
  margin-bottom: 16px;
}
.explain-card {
  max-width: 560px;
}
.sandbox-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.sandbox-table {
  margin-top: 8px;
}
</style>
