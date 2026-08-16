<template>
  <div class="page security-page">
    <header class="page-heading">
      <div><span class="eyebrow">ACCOUNT SECURITY</span><h1>账户安全</h1><p>管理控制台密码、Google Authenticator 与一次性恢复码。</p></div>
      <span v-if="authState" class="security-state" :class="authState.totp_enabled ? 'enabled' : ''">{{ authState.totp_enabled ? '2FA 已启用' : '仅密码' }}</span>
    </header>

    <div v-if="requiredChange" class="notice security-warning"><strong>必须先修改初始密码</strong><span>完成改密后才能访问其他控制台功能。</span></div>
    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <div v-if="success" class="notice security-success">{{ success }}</div>

    <section class="security-grid">
      <article class="panel security-card">
        <div class="security-card-heading"><span class="security-icon">P</span><div><span class="eyebrow">PASSWORD</span><h2>管理员密码</h2></div></div>
        <p>至少 12 个字符。修改后会立即吊销该账号的其他浏览器会话。</p>
        <form @submit.prevent="changePassword">
          <label><span>当前密码</span><input v-model="passwordForm.current" required type="password" autocomplete="current-password" /></label>
          <label><span>新密码</span><input v-model="passwordForm.next" required type="password" minlength="12" autocomplete="new-password" /></label>
          <label><span>确认新密码</span><input v-model="passwordForm.confirm" required type="password" minlength="12" autocomplete="new-password" /></label>
          <button class="primary-button" type="submit" :disabled="savingPassword">{{ savingPassword ? '保存中…' : '修改密码' }}</button>
        </form>
        <small v-if="authState?.password_changed_at">上次修改：{{ formatDate(authState.password_changed_at) }}</small>
      </article>

      <article class="panel security-card">
        <div class="security-card-heading"><span class="security-icon">G</span><div><span class="eyebrow">GOOGLE AUTHENTICATOR</span><h2>动态验证码</h2></div></div>
        <template v-if="authState?.totp_enabled">
          <p>登录时必须提交 6 位动态码。当前剩余 {{ authState.recovery_codes_remaining }} 枚恢复码。</p>
          <form class="danger-zone" @submit.prevent="turnOffTotp">
            <label><span>当前密码</span><input v-model="disableForm.password" required type="password" autocomplete="current-password" /></label>
            <label><span>动态码或恢复码</span><input v-model.trim="disableForm.code" required autocomplete="one-time-code" /></label>
            <button class="danger-button" type="submit" :disabled="disabling">{{ disabling ? '停用中…' : '停用双因素认证' }}</button>
          </form>
        </template>
        <template v-else-if="setup">
          <p>用 Google Authenticator 扫描二维码，或手工输入设置密钥，再提交当前的 6 位动态码。</p>
          <div class="totp-enrollment">
            <img v-if="qrDataUrl" :src="qrDataUrl" alt="Google Authenticator 配置二维码" />
            <div><small>设置密钥</small><code>{{ setup.secret }}</code><button type="button" @click="copySecret">复制密钥</button></div>
          </div>
          <form @submit.prevent="activateTotp">
            <label><span>6 位动态码</span><input v-model.trim="confirmCode" required autocomplete="one-time-code" inputmode="numeric" pattern="[0-9]{6}" placeholder="000000" /></label>
            <button class="primary-button" type="submit" :disabled="confirming">{{ confirming ? '校验中…' : '确认并激活' }}</button>
          </form>
        </template>
        <template v-else>
          <p>激活后，即使密码泄露，没有手机中的动态验证码也无法登录控制台。</p>
          <button class="primary-button" type="button" :disabled="preparing || authState?.must_change_password" @click="startTotp">{{ preparing ? '准备中…' : '激活 Google Authenticator' }}</button>
          <small v-if="authState?.must_change_password">请先修改初始密码。</small>
        </template>
      </article>
    </section>

    <section v-if="recoveryCodes.length" class="panel recovery-panel">
      <div><span class="eyebrow">ONE-TIME RECOVERY</span><h2>立即保存恢复码</h2><p>每枚只能使用一次；关闭此页面后不会再次展示。请离线保存，不要放在密码管理之外的公开文档中。</p></div>
      <div class="recovery-codes"><code v-for="code in recoveryCodes" :key="code">{{ code }}</code></div>
      <div class="recovery-actions"><button class="secondary-button" type="button" @click="copyRecoveryCodes">复制全部</button><button class="primary-button" type="button" @click="downloadRecoveryCodes">下载文本</button></div>
    </section>
  </div>
</template>

<script setup lang="ts">
import QRCode from 'qrcode'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  changeAdminPassword,
  confirmTotp,
  disableTotp,
  getAuthStatus,
  prepareTotp,
  type AuthStatus,
  type TotpSetup,
} from '../api/auth'
import { setControlToken } from '../api/http'

const route = useRoute()
const router = useRouter()
const authState = ref<AuthStatus | null>(null)
const setup = ref<TotpSetup | null>(null)
const qrDataUrl = ref('')
const confirmCode = ref('')
const recoveryCodes = ref<string[]>([])
const error = ref('')
const success = ref('')
const savingPassword = ref(false)
const preparing = ref(false)
const confirming = ref(false)
const disabling = ref(false)
const passwordForm = reactive({ current: '', next: '', confirm: '' })
const disableForm = reactive({ password: '', code: '' })
const requiredChange = computed(() => route.query.required === '1' || Boolean(authState.value?.must_change_password))

function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
function errorText(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback }

async function refresh() {
  try { authState.value = await getAuthStatus() }
  catch (cause) { error.value = errorText(cause, '读取账户安全状态失败') }
}

async function changePassword() {
  error.value = ''; success.value = ''
  if (passwordForm.next !== passwordForm.confirm) { error.value = '两次输入的新密码不一致'; return }
  savingPassword.value = true
  try {
    const result = await changeAdminPassword(passwordForm.current, passwordForm.next)
    if (result.status === 'authenticated') setControlToken(result.token)
    Object.assign(passwordForm, { current: '', next: '', confirm: '' })
    success.value = '管理员密码已修改，其他浏览器会话已吊销。'
    await refresh()
    if (route.query.required === '1') await router.replace('/security')
  } catch (cause) { error.value = errorText(cause, '修改密码失败') }
  finally { savingPassword.value = false }
}

async function startTotp() {
  error.value = ''; success.value = ''; preparing.value = true; recoveryCodes.value = []
  try {
    setup.value = await prepareTotp()
    qrDataUrl.value = await QRCode.toDataURL(setup.value.otpauth_uri, { width: 220, margin: 1, errorCorrectionLevel: 'M' })
  } catch (cause) { error.value = errorText(cause, '创建验证器配置失败') }
  finally { preparing.value = false }
}

async function activateTotp() {
  error.value = ''; success.value = ''; confirming.value = true
  try {
    const result = await confirmTotp(confirmCode.value)
    recoveryCodes.value = result.recovery_codes
    setup.value = null; qrDataUrl.value = ''; confirmCode.value = ''
    success.value = 'Google Authenticator 已激活。请立即保存下方恢复码。'
    await refresh()
  } catch (cause) { error.value = errorText(cause, '激活验证器失败') }
  finally { confirming.value = false }
}

async function turnOffTotp() {
  error.value = ''; success.value = ''; disabling.value = true
  try {
    await disableTotp(disableForm.password, disableForm.code)
    Object.assign(disableForm, { password: '', code: '' })
    recoveryCodes.value = []
    success.value = '双因素认证已停用。'
    await refresh()
  } catch (cause) { error.value = errorText(cause, '停用验证器失败') }
  finally { disabling.value = false }
}

async function copySecret() { if (setup.value) await navigator.clipboard.writeText(setup.value.secret); success.value = '设置密钥已复制。' }
async function copyRecoveryCodes() { await navigator.clipboard.writeText(recoveryCodes.value.join('\n')); success.value = '恢复码已复制。' }
function downloadRecoveryCodes() {
  const blob = new Blob([`Porthousebot recovery codes\n${recoveryCodes.value.join('\n')}\n`], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'porthouse-recovery-codes.txt'; anchor.click()
  URL.revokeObjectURL(url)
}

onMounted(refresh)
</script>

<style scoped>
.security-page{max-width:1180px}.security-state{padding:8px 11px;color:var(--text-muted);background:var(--surface);border:1px solid var(--border);border-radius:9px;font:600 10px var(--font-mono)}.security-state.enabled{color:var(--success);border-color:rgba(50,182,122,.35);background:rgba(50,182,122,.08)}.security-warning,.security-success{display:flex;gap:10px;align-items:center}.security-warning{color:var(--warning);background:var(--warning-subtle);border:1px solid var(--warning)}.security-success{color:var(--success);background:rgba(50,182,122,.08);border:1px solid rgba(50,182,122,.3)}.security-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.security-card{padding:22px}.security-card-heading{display:flex;align-items:center;gap:11px}.security-card-heading h2{margin:4px 0 0;color:var(--text-strong);font-size:18px}.security-icon{display:grid;width:38px;height:38px;place-items:center;color:var(--accent);background:var(--accent-subtle);border-radius:10px;font:700 14px var(--font-mono)}.security-card>p{min-height:42px;margin:17px 0;color:var(--text-muted);font-size:12px;line-height:1.65}.security-card form{display:grid;gap:12px}.security-card label{display:grid;gap:6px;color:var(--text-muted);font-size:10px}.security-card input{width:100%;padding:10px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:9px;outline:none}.security-card input:focus{border-color:var(--accent-border);box-shadow:0 0 0 3px var(--accent-subtle)}.security-card form button{justify-self:start;margin-top:3px}.security-card>small{display:block;margin-top:13px;color:var(--text-muted);font-size:10px}.totp-enrollment{display:grid;grid-template-columns:180px minmax(0,1fr);gap:17px;align-items:center;margin-bottom:16px;padding:15px;background:var(--surface-raised);border:1px solid var(--border);border-radius:12px}.totp-enrollment img{width:180px;height:180px;border-radius:8px}.totp-enrollment div{display:grid;gap:8px;min-width:0}.totp-enrollment code{overflow-wrap:anywhere;color:var(--text-strong);font-size:12px}.totp-enrollment button{justify-self:start;padding:6px 9px;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:7px;cursor:pointer}.danger-zone{margin-top:16px;padding-top:16px;border-top:1px solid var(--border)}.recovery-panel{display:grid;grid-template-columns:minmax(260px,.8fr) 1fr auto;gap:24px;align-items:center;margin-top:16px;padding:22px}.recovery-panel h2{margin:5px 0;color:var(--text-strong);font-size:18px}.recovery-panel p{margin:0;color:var(--text-muted);font-size:11px;line-height:1.6}.recovery-codes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.recovery-codes code{padding:7px 9px;color:var(--text-strong);background:var(--input);border-radius:7px;text-align:center}.recovery-actions{display:grid;gap:8px}@media(max-width:900px){.security-grid{grid-template-columns:1fr}.recovery-panel{grid-template-columns:1fr}.recovery-actions{display:flex}}@media(max-width:560px){.totp-enrollment{grid-template-columns:1fr}.recovery-codes{grid-template-columns:1fr}}
</style>
