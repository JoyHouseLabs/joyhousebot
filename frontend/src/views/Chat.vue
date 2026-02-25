<template>
  <div class="chat-page">
    <div class="chat-header">
      <div class="chat-header-left">
        <h1 class="chat-title">Chat</h1>
        <p class="chat-subtitle">直连网关对话，快速介入</p>
      </div>
      <div class="chat-header-right">
        <n-button quaternary size="small" class="agent-select-btn" :loading="agentsLoading" @click="showAgentModal = true">
          <span class="agent-select-label">{{ currentAgentLabel }}</span>
          <span class="agent-select-arrow">▾</span>
        </n-button>
        <n-select
          v-model:value="currentSessionKey"
          :options="sessionOptions"
          placeholder="选择会话"
          size="small"
          class="session-select"
          :loading="sessionsLoading"
          @update:value="onSessionChange"
        />
        <n-button quaternary circle size="small" @click="loadSessions" :loading="sessionsLoading" title="刷新会话列表">
          <template #icon>
            <span class="icon-refresh">↻</span>
          </template>
        </n-button>
        <n-button quaternary size="small" @click="newSession" class="btn-header-new">新建</n-button>
        <n-button quaternary size="small" class="btn-warm-orange btn-header-delete" :disabled="!currentSessionKey" @click="confirmDeleteSession">删除</n-button>
      </div>
    </div>
    <div class="chat-layout">
      <div class="chat-messages" ref="messagesEl">
        <div v-if="noAgentAvailable" class="chat-empty chat-empty-no-agent">
          <span class="chat-empty-text">暂无可用 Agent</span>
          <p class="chat-empty-hint">请确保网关已启动并已配置 Agent，或刷新页面重试。</p>
          <n-button size="small" quaternary @click="loadAgents">刷新 Agent 列表</n-button>
        </div>
        <div v-else-if="messages.length === 0" class="chat-empty">
          <span class="chat-empty-text">发送一条消息开始对话</span>
        </div>
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="chat-message"
          :class="msg.role"
        >
          <div class="chat-message-avatar" :class="msg.role">
            <img v-if="msg.role === 'assistant'" :src="assistantAvatarUrl" alt="" class="chat-avatar-img" />
            <span v-else>U</span>
          </div>
          <div class="chat-message-body">
            <div class="chat-message-bubble">
              <div class="chat-message-content">{{ msg.content }}</div>
            </div>
            <div class="chat-message-meta">{{ msg.role === 'user' ? 'You' : 'Assistant' }} {{ formatTime(msg.time) }}</div>
          </div>
        </div>
        <div v-if="loading && streamingContent === null" class="chat-message assistant">
          <div class="chat-message-avatar assistant">
            <img :src="assistantAvatarUrl" alt="" class="chat-avatar-img" />
          </div>
          <div class="chat-message-body">
            <div class="chat-message-bubble">
              <n-spin size="small" />
            </div>
          </div>
        </div>
        <div v-if="loading && streamingContent !== null" class="chat-message assistant streaming">
          <div class="chat-message-avatar assistant">
            <img :src="assistantAvatarUrl" alt="" class="chat-avatar-img" />
          </div>
          <div class="chat-message-body">
            <div class="chat-message-bubble">
              <div class="chat-message-content">{{ streamingContent }}<span class="chat-cursor">▌</span></div>
            </div>
            <div class="chat-message-meta">Assistant {{ formatTime(Date.now()) }}</div>
          </div>
        </div>
      </div>
      <div class="chat-input-area">
        <n-input
          v-model:value="inputText"
          type="textarea"
          placeholder="Message (↵ 发送，Shift+↵ 换行，可粘贴图片)"
          :autosize="{ minRows: 2, maxRows: 6 }"
          :disabled="loading || noAgentAvailable"
          class="chat-input"
          @keydown.enter.exact.prevent="send"
        />
        <div class="chat-input-actions">
          <n-button secondary @click="newSession" class="btn-new-session" :disabled="noAgentAvailable">新会话</n-button>
          <n-button
            secondary
            :disabled="loading || transcribing || noAgentAvailable"
            @click="toggleRecording"
            class="btn-record"
          >
            {{ recording ? '停止录音' : transcribing ? '转写中…' : '录音' }}
          </n-button>
          <input
            ref="voiceFileInputRef"
            type="file"
            accept="audio/*,.mp3,.wav,.webm,.m4a,.ogg"
            class="voice-file-input"
            @change="onVoiceFileChange"
          />
          <n-button secondary :disabled="loading || transcribing || noAgentAvailable" @click="triggerVoiceFileUpload" class="btn-upload-voice">
            上传语音
          </n-button>
          <n-button type="primary" :loading="loading" :disabled="!inputText.trim() || recording || noAgentAvailable" @click="send" class="btn-send btn-warm-orange">
            发送 →
          </n-button>
        </div>
      </div>
    </div>
    <n-modal v-model:show="showAgentModal" preset="card" title="选择 Agent" class="agent-modal" style="width: 360px;">
      <n-spin :show="agentsLoading">
        <n-list v-if="activatedAgents.length > 0" class="agent-list">
          <n-list-item
            v-for="a in activatedAgents"
            :key="a.id"
            class="agent-list-item"
            :class="{ selected: selectedAgentId === a.id }"
            clickable
            @click="selectAgent(a.id)"
          >
            <n-thing>
              <template #header>{{ a.name || a.id }}</template>
              <template #header-extra>
                <n-tag v-if="a.is_default" size="tiny" type="info">默认</n-tag>
              </template>
              <template #description>
                <span class="agent-meta">{{ a.model }}</span>
              </template>
            </n-thing>
          </n-list-item>
        </n-list>
        <div v-else class="agent-empty">暂无可用 Agent</div>
      </n-spin>
    </n-modal>
    <n-modal v-model:show="showDeleteConfirm" preset="dialog" title="删除会话" positive-text="删除" negative-text="取消" :loading="deleteLoading"
      @positive-click="doDeleteSession" @negative-click="showDeleteConfirm = false">
      <template #default>
        确定要删除会话「{{ currentSessionKey }}」吗？删除后无法恢复。
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch, onMounted, onUnmounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { messageContentToText } from '../api/chat'
import { transcribeAudio } from '../api/transcribe'
import { useGatewayInject } from '../composables/useGateway'
import { getSessions, getSessionHistory, deleteSession, type SessionItem } from '../api/sessions'
import { getAgents, type AgentListItem } from '../api/agent'
import { normalizeApiError } from '../api/error'
import { useVoiceRecorder } from '../composables/useVoiceRecorder'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const gateway = useGatewayInject()
const messages = ref<{ role: 'user' | 'assistant'; content: string; time?: number }[]>([])
const inputText = ref('')
const loading = ref(false)
const streamingContent = ref<string | null>(null)
const messagesEl = ref<HTMLElement | null>(null)
const transcribing = ref(false)
const voiceFileInputRef = ref<HTMLInputElement | null>(null)
const { recording, startRecording, stopRecording } = useVoiceRecorder()

const assistantAvatarUrl = import.meta.env.BASE_URL + 'joyhouse.png'
const agents = ref<AgentListItem[]>([])
const agentsLoading = ref(false)
const selectedAgentId = ref<string | null>(null)
const sessions = ref<SessionItem[]>([])
const sessionsLoading = ref(false)
const currentSessionKey = ref('ui:default')
const showDeleteConfirm = ref(false)
const deleteLoading = ref(false)
const showAgentModal = ref(false)
/** When using RPC stream, the run we're waiting for; chat events are applied only when payload.runId === chatRunId */
const chatRunId = ref<string | null>(null)
/** 已处理过的 final 的 runId，避免同一 run 的 final 被推送两次导致同一条回复显示两遍 */
const lastFinalizedRunId = ref<string | null>(null)
let unsubscribeChat: (() => void) | null = null

/** Only agents with activated !== false show in agent picker */
const activatedAgents = computed(() =>
  agents.value.filter((a) => a.activated !== false)
)

/** 未选择 Agent 或暂无可用 Agent 时展示「暂无可用 Agent」提示 */
const noAgentAvailable = computed(
  () => !agentsLoading.value && (selectedAgentId.value == null || activatedAgents.value.length === 0)
)

const currentAgentLabel = computed(() => {
  if (!selectedAgentId.value) return '选择 Agent'
  const a = activatedAgents.value.find((x) => x.id === selectedAgentId.value)
  return a ? (a.name || a.id) : '选择 Agent'
})

function selectAgent(agentId: string) {
  selectedAgentId.value = agentId
  showAgentModal.value = false
  onAgentChange(agentId)
}

const sessionOptions = computed(() => {
  const list = sessions.value.map((s) => ({ label: s.key, value: s.key }))
  if (currentSessionKey.value && !list.some((o) => o.value === currentSessionKey.value)) {
    list.unshift({ label: currentSessionKey.value, value: currentSessionKey.value })
  }
  return list
})

function formatTime(ts?: number): string {
  if (ts == null) return ''
  const d = new Date(ts)
  return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0')
}

function syncUrl(sessionKey?: string, agentId?: string | null) {
  const q: Record<string, string> = { ...route.query } as Record<string, string>
  if (sessionKey != null) q.session = sessionKey
  if (agentId != null && agentId !== '') q.agent = agentId
  router.replace({ query: q })
}

async function loadAgents() {
  agentsLoading.value = true
  try {
    const res = await getAgents()
    if (res.ok && res.agents?.length) {
      agents.value = res.agents
      const activated = res.agents.filter((a: AgentListItem) => a.activated !== false)
      const hasCurrent = res.agents.some((a) => a.id === selectedAgentId.value)
      const currentActivated = activated.some((a) => a.id === selectedAgentId.value)
      if (activated.length === 0) {
        selectedAgentId.value = null
      } else if (selectedAgentId.value == null || !hasCurrent || !currentActivated) {
        const defaultAgent = activated.find((a: AgentListItem) => a.is_default) ?? activated[0]
        selectedAgentId.value = defaultAgent?.id ?? null
      }
    } else {
      agents.value = []
      selectedAgentId.value = null
    }
  } catch {
    agents.value = []
    selectedAgentId.value = null
  } finally {
    agentsLoading.value = false
  }
}

async function loadSessions() {
  sessionsLoading.value = true
  try {
    const res = await getSessions(selectedAgentId.value)
    if (res.ok && res.sessions) sessions.value = res.sessions
  } catch {
    sessions.value = []
  } finally {
    sessionsLoading.value = false
  }
}

async function loadHistory(key: string) {
  try {
    const res = await getSessionHistory(key, selectedAgentId.value)
    if (res.ok && res.messages) {
      messages.value = res.messages.map((m, idx) => ({
        role: (m.role === 'assistant' ? 'assistant' : 'user') as 'user' | 'assistant',
        content: m.content || '',
        time: (m as { timestamp?: number }).timestamp ?? Date.now() - (res.messages!.length - idx) * 60000,
      }))
    } else {
      messages.value = []
    }
  } catch {
    messages.value = []
  }
  nextTick(scrollToBottom)
}

function onAgentChange(agentId: string | null) {
  selectedAgentId.value = agentId
  syncUrl(currentSessionKey.value, agentId)
  loadSessions().then(() => {
    if (currentSessionKey.value && sessions.value.some((s) => s.key === currentSessionKey.value)) {
      loadHistory(currentSessionKey.value)
    } else {
      newSession()
    }
  })
}

function onSessionChange(key: string | null) {
  if (!key) return
  currentSessionKey.value = key
  syncUrl(key, selectedAgentId.value)
  loadHistory(key)
}

function newSession() {
  const key = `ui:${Date.now()}`
  currentSessionKey.value = key
  messages.value = []
  syncUrl(key, selectedAgentId.value)
}

function confirmDeleteSession() {
  if (!currentSessionKey.value) return
  showDeleteConfirm.value = true
}

async function doDeleteSession() {
  const key = currentSessionKey.value
  if (!key) return
  deleteLoading.value = true
  try {
    const res = await deleteSession(key, selectedAgentId.value)
    if (res.ok && res.removed) {
      message.success('会话已删除')
      showDeleteConfirm.value = false
      const rest = sessions.value.filter((s) => s.key !== key)
      if (rest.length > 0) {
        currentSessionKey.value = rest[0].key
        syncUrl(rest[0].key, selectedAgentId.value)
        await loadHistory(rest[0].key)
      } else {
        newSession()
      }
      await loadSessions()
    } else {
      message.warning(res.removed ? '已删除' : '会话不存在或删除失败')
      showDeleteConfirm.value = false
    }
  } catch (e) {
    message.error(normalizeApiError(e))
    showDeleteConfirm.value = false
  } finally {
    deleteLoading.value = false
  }
}

function handleChatEvent(payload: unknown) {
  const p = payload as { runId?: string; sessionKey?: string; state?: string; message?: { content?: unknown }; error?: string; errorMessage?: string }
  const sessionKey = currentSessionKey.value || 'ui:default'
  if (p?.sessionKey !== sessionKey) return
  if (p?.runId && chatRunId.value && p.runId !== chatRunId.value) {
    if (p.state === 'final') loadSessions()
    return
  }
  if (p?.state === 'delta') {
    const text = messageContentToText(p?.message?.content)
    if (typeof text === 'string') {
      const cur = streamingContent.value ?? ''
      if (!cur || text.length >= cur.length) {
        streamingContent.value = text
        nextTick(scrollToBottom)
      }
    }
    return
  }
  if (p?.state === 'final') {
    if (p?.runId && lastFinalizedRunId.value === p.runId) return
    const final = streamingContent.value ?? messageContentToText(p?.message?.content) ?? ''
    // 先清空流式状态，再 push，避免「流式气泡」和「列表新消息」同时出现导致同一条回复显示两遍
    streamingContent.value = null
    loading.value = false
    messages.value.push({ role: 'assistant', content: final, time: Date.now() })
    if (p?.runId) lastFinalizedRunId.value = p.runId
    chatRunId.value = null
    loadSessions()
    nextTick(scrollToBottom)
    return
  }
  if (p?.state === 'aborted' || p?.state === 'error') {
    if (p?.state === 'error') {
      message.error(p?.errorMessage ?? p?.error ?? 'chat error')
    }
    streamingContent.value = null
    chatRunId.value = null
    loading.value = false
    nextTick(scrollToBottom)
  }
}

onMounted(async () => {
  await loadAgents()
  const qSession = route.query.session
  const qAgent = route.query.agent
  if (typeof qAgent === 'string' && qAgent.trim() && agents.value.some((a) => a.id === qAgent.trim() && a.activated !== false)) {
    selectedAgentId.value = qAgent.trim()
  }
  if (typeof qSession === 'string' && qSession.trim()) {
    currentSessionKey.value = qSession.trim()
  }
  await loadSessions()
  await loadHistory(currentSessionKey.value)
  syncUrl(currentSessionKey.value, selectedAgentId.value)
  if (gateway?.subscribe) {
    unsubscribeChat = gateway.subscribe('chat', handleChatEvent)
  }
})

onUnmounted(() => {
  unsubscribeChat?.()
  unsubscribeChat = null
})

async function send() {
  const text = inputText.value.trim()
  if (!text || loading.value) return
  if (!gateway?.connected?.value || !gateway.request) {
    message.warning('请先连接网关（刷新页面或检查网关地址）')
    return
  }
  inputText.value = ''
  const now = Date.now()
  messages.value.push({ role: 'user', content: text, time: now })
  loading.value = true
  streamingContent.value = null
  chatRunId.value = null
  try {
    const sessionKey = currentSessionKey.value || 'ui:default'
    const agentId = selectedAgentId.value ?? undefined
    const payload = await gateway.request<{
      runId?: string
      status?: string
      state?: string
      message?: { role?: string; content?: Array<{ type?: string; text?: string }> }
      error?: string
    }>('chat.send', {
      message: text,
      sessionKey,
      agentId: agentId || undefined,
      idempotencyKey: crypto.randomUUID?.() ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`,
    })
    if (payload?.runId) {
      chatRunId.value = payload.runId
      streamingContent.value = ''
      if (payload?.status === 'in_flight' || payload?.status === 'queued') {
        message.warning('会话忙，已加入队列')
      }
    } else if (payload?.status === 'queue_full') {
      message.error('队列已满，请稍后再试')
      loading.value = false
    } else if (payload?.error) {
      message.error(payload.error)
      loading.value = false
    } else {
      message.warning('未收到 runId')
      loading.value = false
    }
  } catch (e) {
    message.error(normalizeApiError(e))
    if ((streamingContent.value ?? '').length > 0) {
      messages.value.push({ role: 'assistant', content: streamingContent.value ?? '', time: Date.now() })
    }
    chatRunId.value = null
    loading.value = false
  } finally {
    if (!chatRunId.value) {
      streamingContent.value = null
      loading.value = false
    }
  }
  await nextTick()
  scrollToBottom()
}

async function toggleRecording() {
  if (recording.value) {
    const blob = await stopRecording()
    if (!blob) return
    transcribing.value = true
    try {
      const result = await transcribeAudio(blob, 'recording.webm')
      if (result.text) inputText.value = result.text
    } catch (e) {
      message.error(normalizeApiError(e))
    } finally {
      transcribing.value = false
    }
  } else {
    try {
      await startRecording()
    } catch (e) {
      message.error(normalizeApiError(e))
    }
  }
}

function triggerVoiceFileUpload() {
  voiceFileInputRef.value?.click()
}

async function onVoiceFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  transcribing.value = true
  try {
    const result = await transcribeAudio(file)
    if (result.text) inputText.value = result.text
  } catch (e) {
    message.error(normalizeApiError(e))
  } finally {
    transcribing.value = false
  }
}

function scrollToBottom() {
  const el = messagesEl.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(messages, () => nextTick().then(scrollToBottom), { deep: true })
</script>

<style scoped>
.chat-page {
  height: calc(100vh - var(--shell-topbar-height, 56px) - 48px);
  display: flex;
  flex-direction: column;
  width: 85%;
  max-width: none;
  margin: 0 auto;
}
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.chat-header-left {
  flex: 1;
  min-width: 0;
}
.chat-title {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0 0 4px 0;
  color: var(--text-strong);
}
.chat-subtitle {
  font-size: 0.875rem;
  color: var(--text-muted);
  margin: 0;
}
.chat-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.agent-select-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}
.agent-select-label {
  color: var(--text-strong);
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-select-arrow {
  color: var(--text-muted);
  font-size: 10px;
}
.agent-modal .agent-list {
  max-height: 320px;
  overflow-y: auto;
}
.agent-modal .agent-list-item {
  cursor: pointer;
  border-radius: 8px;
  padding: 8px 12px;
}
.agent-modal .agent-list-item:hover {
  background: var(--hover);
}
.agent-modal .agent-list-item.selected {
  background: var(--accent-subtle, rgba(0, 120, 212, 0.1));
  color: var(--accent);
}
.agent-modal .agent-meta {
  font-size: 12px;
  color: var(--text-muted);
}
.agent-modal .agent-empty {
  padding: 24px;
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
}
.session-select {
  min-width: 160px;
  max-width: 240px;
}
.session-select :deep(.n-input) {
  font-size: 12px;
}
.icon-refresh {
  font-size: 16px;
  line-height: 1;
}
.btn-header-new,
.btn-header-delete {
  font-size: 13px;
}
.chat-layout {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
}
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.chat-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.chat-empty-no-agent {
  flex-direction: column;
  gap: 8px;
  padding: 24px;
}
.chat-empty-no-agent .chat-empty-text {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-color);
}
.chat-empty-hint {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
  text-align: center;
  max-width: 320px;
}
.chat-empty-text {
  font-size: 14px;
}
.chat-message {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.chat-message.user {
  flex-direction: row-reverse;
}
.chat-message.user .chat-message-body {
  align-items: flex-end;
}
.chat-message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
  color: #fff;
}
.chat-message-avatar.user {
  background: var(--accent);
}
.chat-message-avatar.assistant {
  background: var(--accent);
  color: #fff;
}
.chat-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}
.chat-message-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  max-width: 85%;
  min-width: 0;
}
.chat-message-bubble {
  padding: 12px 16px;
  border-radius: 14px;
  line-height: 1.5;
}
.chat-message.user .chat-message-bubble {
  background: var(--joyhouse-orange-light-bg, #fff3e0);
  color: var(--joyhouse-orange-light-text, #e65100);
}
.chat-message.assistant .chat-message-bubble {
  background: #f5f5f5;
  color: var(--text-strong);
}
[data-theme="dark"] .chat-message.user .chat-message-bubble {
  background: var(--accent-subtle);
  color: var(--accent-hover);
}
[data-theme="dark"] .chat-message.assistant .chat-message-bubble {
  background: var(--bg-hover);
  color: var(--text);
}
.chat-message-content {
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}
.chat-message-meta {
  font-size: 12px;
  color: var(--text-muted);
  padding: 0 4px;
}
.chat-cursor {
  display: inline-block;
  animation: chat-cursor-blink 0.8s step-end infinite;
  color: var(--accent);
}
@keyframes chat-cursor-blink {
  50% { opacity: 0; }
}
.chat-input-area {
  padding: 16px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.chat-input :deep(.n-input__textarea-el) {
  font-size: 14px;
}
.chat-input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.voice-file-input {
  display: none;
}
.btn-new-session {
  color: var(--text-muted);
}
.btn-send {
  min-width: 90px;
}
/* 温暖橘色：发送按钮、删除按钮（替代红色） */
.chat-page :deep(.btn-send) {
  background-color: var(--accent) !important;
  border-color: var(--accent) !important;
}
.chat-page :deep(.btn-send:hover),
.chat-page :deep(.btn-send:focus) {
  background-color: var(--accent-hover) !important;
  border-color: var(--accent-hover) !important;
}
.chat-page :deep(.btn-header-delete) {
  color: var(--accent);
}
.chat-page :deep(.btn-header-delete:hover) {
  color: var(--accent-hover);
}
</style>
