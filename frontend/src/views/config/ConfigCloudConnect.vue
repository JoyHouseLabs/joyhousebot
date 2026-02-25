<template>
  <div class="config-panel" v-if="config !== undefined">
    <div class="config-panel-header">
      <h2 class="config-panel-title">云端连接</h2>
      <p class="config-panel-desc">连接到 Joyhouse Labs 云端，接收任务并上报状态</p>
    </div>

    <n-spin :show="loadingIdentity">
      <div v-if="houseIdentity" class="house-identity-section">
        <h3 class="subsection-title">House 身份</h3>
        <n-descriptions :column="2" bordered>
          <n-descriptions-item label="House ID">
            <n-tag type="info">{{ houseIdentity.house_id }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item label="名称">
            <n-tag>{{ houseIdentity.house_name || '未设置' }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item label="注册状态">
            <n-tag v-if="houseIdentity.registered" type="success">已注册</n-tag>
            <n-tag v-else type="warning">未注册</n-tag>
          </n-descriptions-item>
          <n-descriptions-item label="绑定用户">
            <n-tag v-if="houseIdentity.bound_user_id" type="success">{{ houseIdentity.bound_user_id }}</n-tag>
            <n-tag v-else type="default">未绑定</n-tag>
          </n-descriptions-item>
          <n-descriptions-item label="公钥" :span="2">
            <n-input
              :value="houseIdentity.public_key"
              type="textarea"
              readonly
              :autosize="{ minRows: 2, maxRows: 4 }"
              style="font-family: monospace; font-size: 11px;"
            />
          </n-descriptions-item>
          <n-descriptions-item label="创建时间" :span="2">
            {{ formatDate(houseIdentity.created_at) }}
          </n-descriptions-item>
        </n-descriptions>

        <div class="identity-actions">
          <n-space>
            <n-button
              v-if="!houseIdentity.registered"
              type="primary"
              :loading="registering"
              @click="handleRegister"
            >
              注册到云端
            </n-button>
            <n-button
              v-if="houseIdentity.registered && !houseIdentity.bound_user_id"
              type="primary"
              :loading="binding"
              @click="handleBind"
            >
              绑定到用户
            </n-button>
            <n-button @click="copyPublicKey">
              复制公钥
            </n-button>
          </n-space>
        </div>
      </div>
    </n-spin>

    <n-divider />

    <div class="connection-status-section">
      <h3 class="subsection-title">连接状态</h3>
      <n-spin :show="loadingStatus">
        <n-space vertical :size="16">
          <n-card>
            <n-space align="center">
              <n-badge
                :type="connectionStatus.connected ? 'success' : 'error'"
                :value="connectionStatus.connected ? '已连接' : '未连接'"
              />
              <n-tag v-if="connectionStatus.authenticated" type="success">已认证</n-tag>
              <n-tag v-else type="default">未认证</n-tag>
            </n-space>
            <template #footer>
              <n-space vertical :size="8">
                <div v-if="connectionStatus.house_id">
                  <strong>House ID:</strong> {{ connectionStatus.house_id }}
                </div>
                <div v-if="connectionStatus.last_connected">
                  <strong>最后连接:</strong> {{ formatDate(connectionStatus.last_connected) }}
                </div>
                <div v-if="connectionStatus.error" style="color: #d03050;">
                  <strong>错误:</strong> {{ connectionStatus.error }}
                </div>
              </n-space>
            </template>
          </n-card>

          <n-space>
            <n-button
              v-if="connectionStatus.connected"
              type="error"
              :loading="stopping"
              @click="handleStop"
            >
              断开连接
            </n-button>
            <n-button
              v-else
              type="primary"
              :loading="starting"
              @click="handleStart"
            >
              启动连接
            </n-button>
            <n-button @click="loadConnectionStatus">刷新状态</n-button>
          </n-space>
        </n-space>
      </n-spin>
    </div>

    <n-divider />

    <div class="cloud-config-section">
      <h3 class="subsection-title">云端配置</h3>
      <n-form label-placement="left" label-width="150" class="config-form">
        <n-form-item label="启用云连接">
          <n-switch v-model:value="cloudConfig.enabled" />
        </n-form-item>
        <n-form-item label="后端地址">
          <n-input
            v-model:value="cloudConfig.backend_url"
            placeholder="ws://localhost:8000/ws/cloud-connect"
          />
        </n-form-item>
        <n-form-item label="节点名称">
          <n-input
            v-model:value="cloudConfig.house_name"
            placeholder="我的 AI 助手"
          />
        </n-form-item>
        <n-form-item label="节点描述">
          <n-input
            v-model:value="cloudConfig.description"
            type="textarea"
            placeholder="基于 Claude 的智能助手"
            :autosize="{ minRows: 2, maxRows: 4 }"
          />
        </n-form-item>
        <n-form-item label="自动重连">
          <n-switch v-model:value="cloudConfig.auto_reconnect" />
        </n-form-item>
        <n-form-item label="重连间隔 (秒)">
          <n-input-number
            v-model:value="cloudConfig.reconnect_interval"
            :min="10"
            :max="300"
          />
        </n-form-item>
      </n-form>

      <h4 class="subsubsection-title">能力配置</h4>
      <n-checkbox-group v-model:value="enabledCapabilities">
        <n-space vertical>
          <n-checkbox
            v-for="cap in cloudConfig.capabilities"
            :key="cap.id"
            :value="cap.id"
            :label="cap.name"
          >
            {{ cap.description }}
          </n-checkbox>
        </n-space>
      </n-checkbox-group>
    </div>

    <n-divider />

    <div class="config-actions">
      <n-space>
        <n-button type="primary" :loading="saving" @click="saveCloudConfig">
          保存配置
        </n-button>
        <n-button @click="resetCloudConfig">
          重置
        </n-button>
      </n-space>
    </div>

    <n-modal v-model:show="showBindDialog" preset="card" title="绑定到用户" style="width: 500px;">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="用户 ID">
          <n-input v-model:value="bindUserId" placeholder="请输入要绑定的用户 ID" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showBindDialog = false">取消</n-button>
          <n-button type="primary" :loading="binding" @click="confirmBind">确认绑定</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import type { ConfigData } from '../../api/config'
import {
  getHouseIdentity,
  registerHouse,
  bindHouse,
  getCloudConnectConfig,
  updateCloudConnectConfig,
  getConnectionStatus,
  startCloudConnect,
  stopCloudConnect,
  type CloudConnectConfig,
  type ConnectionStatus,
  type CapabilityItem,
} from '../../api/cloudConnect'

const message = useMessage()
const dialog = useDialog()

const props = defineProps<{ config: ConfigData | null }>()

const loadingIdentity = ref(true)
const loadingStatus = ref(true)
const saving = ref(false)
const registering = ref(false)
const binding = ref(false)
const starting = ref(false)
const stopping = ref(false)

const houseIdentity = ref<HouseIdentity | null>(null)
const connectionStatus = ref<ConnectionStatus>({
  connected: false,
  authenticated: false,
  house_id: null,
  last_connected: null,
  error: null,
})

const showBindDialog = ref(false)
const bindUserId = ref('')

const cloudConfig = ref<CloudConnectConfig>({
  enabled: false,
  backend_url: 'ws://localhost:8000/ws/cloud-connect',
  house_name: '',
  description: '',
  auto_reconnect: true,
  reconnect_interval: 30,
  capabilities: [
    { id: 'chat.v1', name: '对话', description: '多轮对话与上下文记忆', version: '1.0', enabled: true },
    { id: 'code_execution.v1', name: '代码执行', description: '支持多种编程语言', version: '1.0', enabled: true },
    { id: 'file_operations.v1', name: '文件操作', description: '读写本地文件', version: '1.0', enabled: true },
    { id: 'web_search.v1', name: '网络搜索', description: '搜索互联网信息', version: '1.0', enabled: true },
  ],
})

const enabledCapabilities = computed({
  get: () => cloudConfig.value.capabilities.filter(c => c.enabled).map(c => c.id),
  set: (value: string[]) => {
    cloudConfig.value.capabilities.forEach(cap => {
      cap.enabled = value.includes(cap.id)
    })
  },
})

async function loadHouseIdentity() {
  loadingIdentity.value = true
  try {
    houseIdentity.value = await getHouseIdentity()
  } catch (error: any) {
    message.error('加载 House 身份失败: ' + error.message)
  } finally {
    loadingIdentity.value = false
  }
}

async function loadConnectionStatus() {
  loadingStatus.value = true
  try {
    connectionStatus.value = await getConnectionStatus()
  } catch (error: any) {
    console.error('加载连接状态失败:', error)
  } finally {
    loadingStatus.value = false
  }
}

async function loadCloudConfig() {
  try {
    cloudConfig.value = await getCloudConnectConfig()
  } catch (error: any) {
    console.error('加载云端配置失败:', error)
  }
}

async function handleRegister() {
  registering.value = true
  try {
    const result = await registerHouse()
    if (result.ok) {
      message.success('注册成功')
      await loadHouseIdentity()
    } else {
      message.error('注册失败: ' + (result.message || '未知错误'))
    }
  } catch (error: any) {
    message.error('注册失败: ' + error.message)
  } finally {
    registering.value = false
  }
}

function handleBind() {
  bindUserId.value = ''
  showBindDialog.value = true
}

async function confirmBind() {
  if (!bindUserId.value.trim()) {
    message.warning('请输入用户 ID')
    return
  }

  binding.value = true
  try {
    const result = await bindHouse(bindUserId.value)
    if (result.ok) {
      message.success('绑定成功')
      showBindDialog.value = false
      await loadHouseIdentity()
    } else {
      message.error('绑定失败: ' + (result.message || '未知错误'))
    }
  } catch (error: any) {
    message.error('绑定失败: ' + error.message)
  } finally {
    binding.value = false
  }
}

async function handleStart() {
  starting.value = true
  try {
    const result = await startCloudConnect()
    if (result.ok) {
      message.success('启动连接成功')
      await loadConnectionStatus()
    } else {
      message.error('启动连接失败: ' + (result.message || '未知错误'))
    }
  } catch (error: any) {
    message.error('启动连接失败: ' + error.message)
  } finally {
    starting.value = false
  }
}

async function handleStop() {
  stopping.value = true
  try {
    const result = await stopCloudConnect()
    if (result.ok) {
      message.success('断开连接成功')
      await loadConnectionStatus()
    } else {
      message.error('断开连接失败: ' + (result.message || '未知错误'))
    }
  } catch (error: any) {
    message.error('断开连接失败: ' + error.message)
  } finally {
    stopping.value = false
  }
}

async function saveCloudConfig() {
  saving.value = true
  try {
    const result = await updateCloudConnectConfig(cloudConfig.value)
    if (result.ok) {
      message.success('配置保存成功')
    } else {
      message.error('保存失败: ' + (result.message || '未知错误'))
    }
  } catch (error: any) {
    message.error('保存失败: ' + error.message)
  } finally {
    saving.value = false
  }
}

function resetCloudConfig() {
  dialog.warning({
    title: '确认重置',
    content: '确定要重置云端配置吗？',
    positiveText: '确定',
    negativeText: '取消',
    onPositiveClick: async () => {
      await loadCloudConfig()
      message.info('配置已重置')
    },
  })
}

function copyPublicKey() {
  if (houseIdentity.value) {
    navigator.clipboard.writeText(houseIdentity.value.public_key)
    message.success('公钥已复制到剪贴板')
  }
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString('zh-CN')
}

onMounted(() => {
  loadHouseIdentity()
  loadConnectionStatus()
  loadCloudConfig()
})
</script>

<style scoped>
.house-identity-section,
.connection-status-section,
.cloud-config-section {
  padding: 16px 0;
}

.identity-actions {
  margin-top: 16px;
}

.subsection-title {
  font-size: 16px;
  font-weight: 600;
  margin: 20px 0 12px 0;
  color: #333;
}

.subsubsection-title {
  font-size: 14px;
  font-weight: 500;
  margin: 16px 0 8px 0;
  color: #555;
}

.config-actions {
  padding: 16px 0;
  text-align: right;
}

.config-form {
  max-width: 600px;
}
</style>
