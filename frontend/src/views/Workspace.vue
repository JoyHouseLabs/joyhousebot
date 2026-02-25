<template>
  <div class="workspace-page">
    <div class="workspace-header">
      <h1 class="workspace-title">编程</h1>
      <p class="workspace-subtitle">与 Agent 对话并查看回复</p>
      <div class="workspace-status">
        <span class="status-dot" :class="{ connected: wsConnected }" />
        {{ wsConnected ? '已连接' : '未连接' }}
      </div>
    </div>
    <div class="workspace-layout">
      <div class="workspace-chat">
        <div class="workspace-messages" ref="messagesEl">
          <div v-if="messages.length === 0 && !streamingContent" class="workspace-empty">
            输入指令并发送，将在此显示对话与执行过程
          </div>
          <div v-for="(msg, i) in messages" :key="i" class="workspace-msg" :class="msg.role">
            <div class="workspace-msg-label">{{ msg.role === 'user' ? 'You' : 'Assistant' }}</div>
            <div class="workspace-msg-content">{{ msg.content }}</div>
          </div>
          <div v-if="loading && (streamingContent !== null || !replyDone)" class="workspace-msg assistant streaming">
            <div class="workspace-msg-label">Assistant</div>
            <div class="workspace-msg-content">{{ streamingContent }}<span class="stream-cursor">▌</span></div>
          </div>
        </div>
        <div class="workspace-input-row">
          <n-input
            v-model:value="inputText"
            type="textarea"
            placeholder="输入指令，例如：用 Python 打印 1 到 10"
            :autosize="{ minRows: 2, maxRows: 6 }"
            :disabled="!wsConnected || loading"
            @keydown.enter.ctrl="send"
          />
          <n-button
            secondary
            :disabled="!wsConnected || loading || transcribing"
            @click="toggleRecording"
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
          <n-button secondary :disabled="!wsConnected || loading || transcribing" @click="triggerVoiceFileUpload">
            上传语音
          </n-button>
          <n-button type="primary" :disabled="!wsConnected || loading || !inputText.trim() || recording" @click="send">
            {{ loading ? '执行中…' : '发送' }}
          </n-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { NInput, NButton, useMessage } from 'naive-ui'
import { transcribeAudio } from '../api/transcribe'
import { normalizeApiError } from '../api/error'
import { useVoiceRecorder } from '../composables/useVoiceRecorder'

const message = useMessage()
const SESSION_ID = 'ui:workspace'
const AGENT_ID = 'default'

const inputText = ref('')
const wsConnected = ref(false)
const loading = ref(false)
const transcribing = ref(false)
const voiceFileInputRef = ref<HTMLInputElement | null>(null)
const { recording, startRecording, stopRecording } = useVoiceRecorder()
const streamingContent = ref<string | null>(null)
const replyDone = ref(true)
const messages = ref<{ role: 'user' | 'assistant'; content: string }[]>([])
const messagesEl = ref<HTMLElement | null>(null)

let ws: WebSocket | null = null
let currentReply = ''

function getWsUrl(): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}/ws/agent-stream`
}

function connect() {
  if (ws?.readyState === WebSocket.OPEN) return
  const url = getWsUrl()
  ws = new WebSocket(url)
  ws.onopen = () => { wsConnected.value = true }
  ws.onclose = () => { wsConnected.value = false }
  ws.onerror = () => { wsConnected.value = false }
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data as string)
      if (data.type === 'event') {
        const { event, payload } = data
        if (event === 'llm_delta' && payload?.content) {
          if (streamingContent.value === null) streamingContent.value = ''
          streamingContent.value += payload.content
          currentReply += payload.content
        } else if (event === 'tool_end') {
          const r = payload?.result
          if (payload?.tool === 'open_app' && typeof r === 'string') {
            try {
              const out = JSON.parse(r) as { ok?: boolean; navigate_to?: string; app_link?: string }
              if (out?.ok) {
                const url = out.app_link || out.navigate_to
                if (url) window.open(url, '_blank', 'noopener,noreferrer')
              }
            } catch (_) {}
          }
        } else if (event === 'final' && payload?.content !== undefined) {
          streamingContent.value = payload.content
          currentReply = payload.content
          replyDone.value = true
          messages.value.push({ role: 'assistant', content: currentReply })
          streamingContent.value = null
          loading.value = false
          scrollMessages()
        }
      } else if (data.type === 'error') {
        loading.value = false
        if (currentReply) messages.value.push({ role: 'assistant', content: currentReply })
        streamingContent.value = null
        replyDone.value = true
      }
    } catch (_) {}
  }
}

function scrollMessages() {
  nextTick(() => {
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  })
}

function send() {
  const msg = inputText.value.trim()
  if (!msg || !ws || ws.readyState !== WebSocket.OPEN || loading.value) return
  loading.value = true
  streamingContent.value = null
  currentReply = ''
  replyDone.value = false
  messages.value.push({ role: 'user', content: msg })
  inputText.value = ''
  scrollMessages()
  ws.send(JSON.stringify({
    type: 'message',
    message: msg,
    session_id: SESSION_ID,
    agent_id: AGENT_ID,
  }))
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

onMounted(() => {
  connect()
})

onUnmounted(() => {
  if (ws) {
    ws.close()
    ws = null
  }
  wsConnected.value = false
})
</script>

<style scoped>
.workspace-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.workspace-header {
  flex-shrink: 0;
  padding: 12px 16px;
  border-bottom: 1px solid var(--n-border-color, #e0e0e0);
  display: flex;
  align-items: center;
  gap: 12px;
}
.workspace-title {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
}
.workspace-subtitle {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: 0.875rem;
}
.workspace-status {
  margin-left: auto;
  font-size: 0.75rem;
  color: var(--n-text-color-3, #999);
  display: flex;
  align-items: center;
  gap: 6px;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c0c0c0;
}
.status-dot.connected {
  background: #18a058;
}
.workspace-layout {
  flex: 1;
  display: flex;
  min-height: 0;
}
.workspace-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.workspace-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}
.workspace-empty,
.workspace-placeholder {
  color: var(--n-text-color-3, #999);
  font-size: 0.875rem;
}
.workspace-msg {
  margin-bottom: 12px;
}
.workspace-msg-label {
  font-size: 0.75rem;
  color: var(--n-text-color-3, #999);
  margin-bottom: 4px;
}
.workspace-msg-content {
  white-space: pre-wrap;
  word-break: break-word;
}
.workspace-msg.assistant .workspace-msg-content {
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--n-color-modal, #f5f5f5);
}
.stream-cursor {
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
.workspace-input-row {
  flex-shrink: 0;
  padding: 12px;
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.workspace-input-row .n-input {
  flex: 1;
}
.voice-file-input {
  display: none;
}
</style>
