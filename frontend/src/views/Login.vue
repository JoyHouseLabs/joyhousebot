<template>
  <main class="login-page">
    <section class="login-card">
      <div class="login-brand"><img :src="logoSrc" alt="Joyhousebot" /><div><strong>Joyhousebot</strong><span>AGENT CLOUD</span></div></div>
      <span class="eyebrow">CONTROL PLANE</span>
      <h1>登录控制台</h1>
      <p class="login-lead">使用绑定的 user_id 或数据库访问令牌进入平台。</p>
      <div v-if="error" class="notice error-notice">{{ error }}</div>
      <form @submit.prevent="login">
        <label><span>user_id</span><input v-model.trim="userId" required autocomplete="username" placeholder="local-dev" /></label>
        <label><span>API 访问令牌 <small>生产环境必填</small></span><input v-model.trim="token" type="password" autocomplete="current-password" placeholder="开发模式可留空" /></label>
        <button class="primary-button login-submit" type="submit" :disabled="loading">{{ loading ? '验证中…' : '登录' }}</button>
      </form>
      <div class="login-hint"><strong>默认开发管理员</strong><p><code>local-dev</code> 已绑定平台管理员权限。生产环境请为指定 user_id 签发数据库 API Token，不要使用开发模式。</p></div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearControlToken, setControlToken } from '../api/http'
import { getIdentity } from '../api/monitoring'
import { setRuntimeUserId } from '../api/identity'

const router = useRouter()
const route = useRoute()
const logoSrc = `${import.meta.env.BASE_URL}joyhouse.png`
const userId = ref('local-dev')
const token = ref('')
const error = ref('')
const loading = ref(false)

async function login() {
  loading.value = true
  error.value = ''
  try {
    setRuntimeUserId(userId.value)
    if (token.value) setControlToken(token.value)
    else clearControlToken()
    const identity = await getIdentity()
    if (!identity?.user_id) throw new Error('身份验证失败')
    localStorage.setItem('joyhousebot_auth_session', '1')
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') ? route.query.redirect : '/overview'
    await router.replace(redirect)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '登录失败，请检查 user_id 或访问令牌'
  } finally {
    loading.value = false
  }
}
</script>
