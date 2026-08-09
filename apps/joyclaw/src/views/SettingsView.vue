<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { consoleUrl, getApiToken, getPreferredAgentId, getUserId, listAgents, setApiToken, setPreferredAgentId, setUserId, type AgentSummary } from '../api'

const userId = ref(getUserId())
const token = ref(getApiToken())
const agentId = ref(getPreferredAgentId())
const agents = ref<AgentSummary[]>([])
const saved = ref(false)
const error = ref('')

async function load() { try { agents.value = await listAgents(); if (!agentId.value) agentId.value = agents.value.find(item => item.is_default)?.id || agents.value[0]?.id || '' } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 Agent 失败' } }
function save() { setUserId(userId.value); setApiToken(token.value); setPreferredAgentId(agentId.value); saved.value = true; window.setTimeout(() => { saved.value = false }, 1800) }
onMounted(load)
</script>

<template>
  <section class="page-header"><div><span class="eyebrow">SETTINGS</span><h1>保持简单，按需深入</h1><p>这里只保留个人使用必需的设置。模型、能力、安全策略和版本发布由 JoyhouseBot Console 管理。</p></div></section>
  <div class="settings-grid">
    <form class="settings-card" @submit.prevent="save"><span class="eyebrow">BASIC</span><h2>个人执行设置</h2><label>默认 Agent<select v-model="agentId"><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }} · {{ agent.id }}</option></select></label><label>本地用户 ID<input v-model="userId" autocomplete="username" /></label><label>API Token <small>仅保存在当前浏览器标签页</small><input v-model="token" type="password" autocomplete="off" placeholder="本地开发可留空" /></label><p v-if="error" class="error-message">{{ error }}</p><button class="primary-button" type="submit">{{ saved ? '已保存' : '保存设置' }}</button></form>
    <article class="settings-card advanced-card"><span class="eyebrow">ADVANCED</span><h2>高级配置仍在控制台</h2><p>在控制台管理 Agent、模型、Skills、Tools、插件、记忆策略、自动化和运行治理。JoyClaw 不复制这些配置。</p><ul><li>Agent 身份与模型策略</li><li>能力权限与高风险审批</li><li>执行时间线、评测和回放</li><li>插件、渠道与外部系统</li></ul><a :href="consoleUrl('/agents')" class="secondary-button">打开 JoyhouseBot Console →</a></article>
  </div>
</template>
