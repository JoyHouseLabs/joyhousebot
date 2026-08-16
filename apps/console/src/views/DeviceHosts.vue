<template>
  <div class="page device-page">
    <header class="page-heading">
      <div><span class="eyebrow">DEVICE HOST CONTROL</span><h1>本机执行环境</h1><p>查看 Desktop 管理的 Node Host、精确 Extension 能力和短期模型授权；设备总 Token 不会下放给 Extension。</p></div>
      <button class="secondary-button" type="button" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新状态' }}</button>
    </header>

    <div v-if="error" class="notice error-notice">{{ error }}</div>
    <section class="summary-grid">
      <article class="panel"><span>已登记设备</span><strong>{{ devices.length }}</strong><small>由 JoyHouse Desktop 完成首次配对</small></article>
      <article class="panel"><span>当前在线</span><strong>{{ onlineCount }}</strong><small>最近 90 秒内完成心跳</small></article>
      <article class="panel"><span>Node 能力</span><strong>{{ capabilityCount }}</strong><small>精确版本与实现摘要</small></article>
      <article class="panel"><span>Host 模型授权</span><strong>{{ activeGrantCount }}</strong><small>短期预算凭证，不展示密钥</small></article>
    </section>

    <section class="panel boundary-panel">
      <div><span class="eyebrow">SECURITY BOUNDARY</span><h2>设备身份、模型授权、工具授权彼此分离</h2></div>
      <p>Desktop 在系统 Keychain 保存设备 Token；Extension 只获得当前 delivery 的短期 <code>jhm_</code> / <code>jht_</code> grant。Run 结束、设备撤销或 claim 变化后自动失效。</p>
    </section>

    <section class="device-grid">
      <article v-for="device in devices" :key="device.device_id" class="panel device-card">
        <header><div class="device-mark">N</div><div><strong>{{ device.display_name }}</strong><code>{{ device.device_id }}</code></div><span class="status-badge" :class="isOnline(device) ? 'completed' : 'cancelled'">{{ isOnline(device) ? '在线' : device.status === 'revoked' ? '已撤销' : '离线' }}</span></header>
        <dl><dt>Host Revision</dt><dd>{{ device.host_revision }}</dd><dt>Manifest</dt><dd><code>{{ shortDigest(device.host_manifest_digest) }}</code></dd><dt>最后心跳</dt><dd>{{ formatDate(device.last_seen_at) }}</dd><dt>默认设备</dt><dd>{{ device.is_default ? '是' : '否' }}</dd></dl>
        <div class="capabilities"><span v-for="item in device.capabilities" :key="`${item.capability_id}:${item.version}`"><strong>{{ item.capability_id }}</strong><code>v{{ item.version }} · {{ item.portable ? 'portable' : 'local' }}</code></span><small v-if="!device.capabilities.length">尚未上报能力</small></div>
        <footer><button class="secondary-button" type="button" :disabled="device.status === 'revoked' || rotating === device.device_id" @click="rotate(device)">{{ rotating === device.device_id ? '轮换中…' : '轮换设备 Token' }}</button><button class="secondary-button danger-text" type="button" :disabled="device.status === 'revoked' || revoking === device.device_id" @click="revoke(device)">{{ revoking === device.device_id ? '撤销中…' : '撤销设备' }}</button></footer>
      </article>
      <article v-if="!devices.length && !loading" class="panel empty-state"><strong>尚未登记 Device Host</strong><p>在 JoyHouse Desktop 的“本机执行环境”中完成配对；控制台不会生成或保存本机配置文件。</p></article>
    </section>

    <section v-if="rotatedToken" class="notice token-notice"><strong>新设备 Token 只显示一次</strong><code>{{ rotatedToken }}</code><button class="secondary-button" type="button" @click="copyToken">复制后关闭</button></section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { listDeviceHosts, listHostModelGrants, revokeDeviceHost, rotateDeviceHostToken, type DeviceHost, type HostModelGrant } from '../api/deviceHosts'

const message = useMessage()
const loading = ref(false); const error = ref(''); const devices = ref<DeviceHost[]>([]); const grants = ref<HostModelGrant[]>([])
const rotating = ref(''); const revoking = ref(''); const rotatedToken = ref('')
const onlineCount = computed(() => devices.value.filter(isOnline).length)
const capabilityCount = computed(() => devices.value.reduce((total, item) => total + item.capabilities.length, 0))
const activeGrantCount = computed(() => grants.value.filter((item) => item.status === 'active' && new Date(item.expires_at).getTime() > Date.now()).length)
function isOnline(device: DeviceHost) { return device.status === 'active' && !!device.last_seen_at && Date.now() - new Date(device.last_seen_at).getTime() < 90_000 }
async function load() { loading.value = true; error.value = ''; try { [devices.value, grants.value] = await Promise.all([listDeviceHosts(), listHostModelGrants()]) } catch (cause) { error.value = errorText(cause) } finally { loading.value = false } }
async function rotate(device: DeviceHost) { if (!window.confirm(`轮换 ${device.display_name} 的设备 Token？旧 Token 会立即失效。`)) return; rotating.value = device.device_id; try { rotatedToken.value = (await rotateDeviceHostToken(device.device_id)).device_token; message.success('Token 已轮换，请立即写入系统 Keychain') } catch (cause) { error.value = errorText(cause) } finally { rotating.value = '' } }
async function revoke(device: DeviceHost) { if (!window.confirm(`撤销设备 ${device.display_name}？未完成的本机任务将停止。`)) return; revoking.value = device.device_id; try { await revokeDeviceHost(device.device_id); message.success('设备已撤销'); await load() } catch (cause) { error.value = errorText(cause) } finally { revoking.value = '' } }
async function copyToken() { await navigator.clipboard.writeText(rotatedToken.value); rotatedToken.value = ''; message.success('已复制，请保存到 Keychain') }
function shortDigest(value: string) { return value.length > 24 ? `${value.slice(0, 22)}…` : value }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN') : '从未连接' }
function errorText(value: unknown) { return value instanceof Error ? value.message : '操作失败' }
onMounted(load)
</script>

<style scoped>
.summary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px}.summary-grid article{display:grid;gap:7px;padding:18px}.summary-grid span,.summary-grid small{color:var(--text-muted);font-size:10px}.summary-grid strong{color:var(--text-strong);font:600 28px var(--font-mono)}.boundary-panel{display:grid;grid-template-columns:minmax(260px,.8fr) minmax(360px,1.2fr);gap:24px;align-items:center;margin-bottom:16px;padding:22px}.boundary-panel h2{margin:7px 0 0;color:var(--text-strong);font-size:20px}.boundary-panel p{margin:0;color:var(--text-muted);line-height:1.7}.device-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.device-card{overflow:hidden}.device-card>header{display:grid;grid-template-columns:42px minmax(0,1fr) auto;align-items:center;gap:12px;padding:18px}.device-mark{display:grid;width:40px;height:40px;place-items:center;border-radius:11px;color:var(--accent);background:var(--accent-subtle);font:600 14px var(--font-mono)}.device-card header>div:nth-child(2){display:grid;gap:4px}.device-card header strong{color:var(--text-strong)}.device-card code{color:var(--text-muted);font-size:9px}.device-card dl{display:grid;grid-template-columns:120px minmax(0,1fr);margin:0;padding:0 18px}.device-card dt,.device-card dd{margin:0;padding:9px 0;border-top:1px solid var(--border);font-size:10px}.device-card dt{color:var(--text-muted)}.device-card dd{overflow-wrap:anywhere;color:var(--text-strong)}.capabilities{display:flex;flex-wrap:wrap;gap:7px;padding:16px 18px}.capabilities span{display:grid;gap:2px;padding:7px 9px;border:1px solid var(--border);border-radius:8px}.capabilities strong{font-size:10px}.device-card footer{display:flex;justify-content:flex-end;gap:8px;padding:14px 18px;border-top:1px solid var(--border)}.danger-text{color:var(--danger)}.token-notice{position:sticky;bottom:18px;display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:14px;margin-top:16px}.token-notice code{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.empty-state{grid-column:1/-1;min-height:260px}@media(max-width:1000px){.summary-grid{grid-template-columns:repeat(2,1fr)}.device-grid{grid-template-columns:1fr}}@media(max-width:680px){.summary-grid,.boundary-panel{grid-template-columns:1fr}.token-notice{grid-template-columns:1fr}}
</style>
