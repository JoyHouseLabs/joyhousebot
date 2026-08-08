<template>
  <div class="page channels-page">
    <header class="page-heading">
      <div><span class="eyebrow">CHANNEL CONNECTORS</span><h1>Channels 配置</h1><p>管理外部消息连接器的启用状态与安全边界。连接凭据仍由服务器环境变量或密钥引用管理，不在浏览器回显。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新状态' }}</button>
    </header>
    <div class="notice info-notice"><strong>当前配置来源</strong><span>连接器配置目前由 config.json / 环境变量加载，修改凭据后需要重启 Channel Worker。数据库化配置与热加载将在下一阶段接入。</span></div>
    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section class="channel-grid">
      <article v-for="channel in channels" :key="channel.id" class="panel channel-card" :class="{ enabled: channel.enabled }">
        <div class="channel-card-heading"><div><span class="channel-icon">{{ channel.icon }}</span><div><h2>{{ channel.name }}</h2><small>{{ channel.id }}</small></div></div><span class="status-badge" :class="channel.enabled ? 'completed' : 'cancelled'">{{ channel.enabled ? '已启用' : '未启用' }}</span></div>
        <p>{{ channel.description }}</p>
        <div class="channel-meta"><span><b>支持</b>{{ channel.capabilities }}</span><span><b>凭据</b>{{ channel.enabled ? '服务器已配置' : '未启用' }}</span></div>
        <div class="channel-footer"><span>{{ channel.enabled ? '由 Channel Worker 通过 PG Lease 接管' : '启用后由 Channel Worker 按 Lease 启动' }}</span><code>{{ channel.secretHint }}</code></div>
      </article>
    </section>
    <section class="panel channel-boundary"><div class="panel-heading"><div><span class="eyebrow">RUNTIME BOUNDARY</span><h2>Channel 与 Agent Runtime 的关系</h2></div></div><div class="boundary-flow"><span>Channel Plugin</span><b>→</b><span>RunAdapter</span><b>→</b><span>PostgreSQL Run / Task</span><b>→</b><span>Coordinator / Worker</span></div><p>Channel 插件只负责收发消息和连接生命周期，不直接调用模型或维护执行队列。入站消息会生成带有 channel、sender_id、chat_id 的用户 Run，出站消息进入 PG Outbox，支持重试、审计和多进程接管。</p></section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getAdminConfig } from '../api/admin'

const loading = ref(false)
const error = ref('')
const configured = ref<Record<string, boolean>>({})
const channels = [
  { id: 'telegram', name: 'Telegram', icon: 'T', description: 'Telegram Bot 长轮询连接。', capabilities: '私聊、群聊、媒体、反应', secretHint: 'TELEGRAM_BOT_TOKEN' },
  { id: 'feishu', name: '飞书', icon: '飞', description: '飞书/Lark WebSocket 长连接。', capabilities: '私聊、群聊、线程', secretHint: 'FEISHU_APP_SECRET' },
  { id: 'dingtalk', name: '钉钉', icon: '钉', description: '钉钉 Stream 模式连接。', capabilities: '私聊、群聊', secretHint: 'DINGTALK_CLIENT_SECRET' },
  { id: 'slack', name: 'Slack', icon: 'S', description: 'Slack Socket Mode 连接。', capabilities: '私聊、群组、线程', secretHint: 'SLACK_APP_TOKEN' },
  { id: 'discord', name: 'Discord', icon: 'D', description: 'Discord Gateway 机器人连接。', capabilities: '私聊、群组、媒体', secretHint: 'DISCORD_BOT_TOKEN' },
  { id: 'whatsapp', name: 'WhatsApp', icon: 'W', description: '通过 WhatsApp Bridge 连接。', capabilities: '私聊、媒体', secretHint: 'WHATSAPP_BRIDGE_TOKEN' },
  { id: 'email', name: 'Email', icon: '@', description: 'IMAP 入站与 SMTP 出站。', capabilities: '邮件收发、重试', secretHint: 'IMAP / SMTP credentials' },
  { id: 'qq', name: 'QQ', icon: 'Q', description: 'QQ Botpy 机器人连接。', capabilities: '私聊、群聊', secretHint: 'QQ_APP_SECRET' },
].map((item) => ({ ...item, get enabled() { return Boolean(configured.value[item.id]) } }))

async function load() {
  loading.value = true; error.value = ''
  try { const value = await getAdminConfig(); configured.value = (value.channels || {}) as Record<string, boolean> }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 Channel 配置失败' }
  finally { loading.value = false }
}
onMounted(load)
</script>

<style scoped>
.channel-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }.channel-card { padding: 18px; }.channel-card.enabled { border-color: var(--accent-border); }.channel-card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.channel-card-heading>div { display: flex; gap: 10px; align-items: center; }.channel-card-heading h2 { margin: 0 0 3px; font-size: 16px; color: var(--text-strong); }.channel-card-heading small { color: var(--text-muted); font-family: var(--font-mono); font-size: 10px; }.channel-icon { display: grid; width: 34px; height: 34px; place-items: center; color: var(--accent); background: var(--accent-subtle); border-radius: 10px; font-weight: 700; }.channel-card p { min-height: 34px; margin: 15px 0; color: var(--text-muted); font-size: 12px; }.channel-meta { display: grid; gap: 7px; padding: 11px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); color: var(--text-muted); font-size: 11px; }.channel-meta span { display: flex; justify-content: space-between; gap: 10px; }.channel-meta b { color: var(--text-strong); }.channel-footer { display: flex; justify-content: space-between; gap: 10px; margin-top: 12px; color: var(--text-muted); font-size: 10px; }.channel-footer code { color: var(--accent); white-space: nowrap; }.info-notice { display: flex; gap: 12px; color: var(--text-muted); background: var(--surface-raised); border: 1px solid var(--border); }.info-notice strong { color: var(--text-strong); white-space: nowrap; }.channel-boundary { margin-top: 16px; padding: 18px; }.boundary-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--accent); font-size: 12px; }.boundary-flow span { padding: 8px 11px; background: var(--accent-subtle); border-radius: 8px; }.boundary-flow b { color: var(--text-muted); }.channel-boundary p { margin: 14px 0 0; color: var(--text-muted); font-size: 12px; line-height: 1.7; }
@media (max-width: 760px) { .channel-grid { grid-template-columns: 1fr; }.channel-footer { flex-direction: column; }.info-notice { flex-direction: column; gap: 5px; } }
</style>
