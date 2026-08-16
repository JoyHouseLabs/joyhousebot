<template>
  <main class="login-page">
    <section class="login-card">
      <div class="login-brand"><img :src="logoSrc" alt="Porthousebot" /><div><strong>Porthousebot</strong><span>AGENT CLOUD</span></div></div>
      <span class="eyebrow">CONTROL PLANE</span>
      <h1>登录控制台</h1>
      <p class="login-lead">验证管理员身份，并选择本次要管理的个人 user_id。</p>
      <div v-if="error" class="notice error-notice">{{ error }}</div>
      <form v-if="phase === 'mfa'" @submit.prevent="verifyMfa">
        <div class="login-step"><strong>双因素认证</strong><span>输入 Google Authenticator 中的 6 位动态码，也可使用一枚恢复码。</span></div>
        <label><span>动态验证码或恢复码</span><input v-model.trim="mfaCode" required autocomplete="one-time-code" inputmode="numeric" autofocus placeholder="000000" /></label>
        <button class="primary-button login-submit" type="submit" :disabled="loading">{{ loading ? '验证中…' : '完成登录' }}</button>
        <button class="login-mode-button" type="button" @click="resetLogin">返回账号密码</button>
      </form>
      <form v-else-if="mode === 'password'" @submit.prevent="loginWithAdminPassword">
        <label><span>管理员账号</span><input v-model.trim="adminUserId" required maxlength="128" autocomplete="username" placeholder="porthouse" /></label>
        <label>
          <span>操作 user_id <small>个人数据与执行归属</small></span>
          <input v-model.trim="operationUserId" maxlength="128" autocomplete="off" placeholder="留空则使用管理员账号" />
        </label>
        <div v-if="isDelegatedLogin" class="login-scope-note">
          <strong>将进入 {{ operationUserId }} 的个人空间</strong>
          <span>Run、会话、记忆、自动化和成果按此 user_id 隔离；平台设置仍以 {{ adminUserId }} 管理员身份执行。</span>
        </div>
        <label><span>管理员密码</span><input v-model="password" required type="password" autocomplete="current-password" placeholder="输入管理员密码" /></label>
        <button class="primary-button login-submit" type="submit" :disabled="loading">{{ loading ? '验证中…' : '登录' }}</button>
        <button class="login-mode-button" type="button" @click="mode = 'token'">使用 API Token 登录</button>
      </form>
      <form v-else @submit.prevent="loginWithToken">
        <label><span>Token 归属 user_id</span><input v-model.trim="tokenUserId" required maxlength="128" autocomplete="username" placeholder="porthouse" /></label>
        <label><span>API 访问令牌</span><input v-model.trim="token" type="password" autocomplete="current-password" placeholder="粘贴数据库访问令牌" /></label>
        <button class="primary-button login-submit" type="submit" :disabled="loading">{{ loading ? '验证中…' : '验证 Token' }}</button>
        <button class="login-mode-button" type="button" @click="mode = 'password'">返回账号密码登录</button>
      </form>
      <div class="login-hint"><strong>本地开发默认管理员</strong><p>账号 <code>porthouse</code>，初始密码 <code>porthouse</code>。首次密码登录后必须立即修改；生产环境没有内置固定密码。</p></div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { loginWithPassword, verifyLoginMfa, type AuthenticatedLogin } from '../api/auth'
import { clearControlToken, setControlToken } from '../api/http'
import { getIdentity } from '../api/monitoring'
import { clearImpersonationTarget, getImpersonationTarget, getRuntimeUserId, setImpersonationTarget, setRuntimeUserId } from '../api/identity'

const router = useRouter()
const route = useRoute()
const logoSrc = `${import.meta.env.BASE_URL}porthouse.png`
const existingAdminUserId = getRuntimeUserId()
const adminUserId = ref(existingAdminUserId)
const operationUserId = ref(getImpersonationTarget() || existingAdminUserId)
const tokenUserId = ref(existingAdminUserId)
const password = ref('')
const token = ref('')
const mfaCode = ref('')
const challengeToken = ref('')
const mode = ref<'password' | 'token'>('password')
const phase = ref<'login' | 'mfa'>('login')
const error = ref('')
const loading = ref(false)
const validUserId = /^[^\s\u0000-\u001f\u007f]{1,128}$/
const isDelegatedLogin = computed(() => {
  const target = operationUserId.value.trim()
  return Boolean(target && target !== adminUserId.value.trim())
})

async function completeLogin(result: AuthenticatedLogin) {
  setRuntimeUserId(result.user_id)
  const targetUserId = operationUserId.value.trim() || result.user_id
  if (targetUserId === result.user_id) clearImpersonationTarget()
  else setImpersonationTarget(targetUserId)
  setControlToken(result.token)
  localStorage.setItem('porthouse_auth_session', '1')
  if (result.must_change_password) {
    await router.replace('/security?required=1')
    return
  }
  const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') ? route.query.redirect : '/overview'
  await router.replace(redirect)
}

async function loginWithAdminPassword() {
  loading.value = true
  error.value = ''
  try {
    const requestedUserId = operationUserId.value.trim()
    if (requestedUserId && !validUserId.test(requestedUserId)) {
      throw new Error('操作 user_id 需为 1–128 个不含空格的字符')
    }
    clearControlToken()
    const result = await loginWithPassword(adminUserId.value, password.value)
    if (result.status === 'mfa_required') {
      challengeToken.value = result.challenge_token
      phase.value = 'mfa'
      mfaCode.value = ''
      return
    }
    await completeLogin(result)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '登录失败，请检查管理员账号和密码'
  } finally {
    loading.value = false
  }
}

async function verifyMfa() {
  loading.value = true
  error.value = ''
  try {
    const result = await verifyLoginMfa(challengeToken.value, mfaCode.value)
    await completeLogin(result)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '动态验证码校验失败'
  } finally {
    loading.value = false
  }
}

function resetLogin() {
  phase.value = 'login'
  challengeToken.value = ''
  mfaCode.value = ''
  error.value = ''
}

async function loginWithToken() {
  loading.value = true
  error.value = ''
  try {
    clearImpersonationTarget()
    setRuntimeUserId(tokenUserId.value)
    if (token.value) setControlToken(token.value)
    else clearControlToken()
    const identity = await getIdentity()
    if (!identity?.user_id) throw new Error('身份验证失败')
    setRuntimeUserId(identity.user_id)
    localStorage.setItem('porthouse_auth_session', '1')
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') ? route.query.redirect : '/overview'
    await router.replace(redirect)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '登录失败，请检查 user_id 或访问令牌'
  } finally {
    loading.value = false
  }
}
</script>
