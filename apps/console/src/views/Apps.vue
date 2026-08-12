<template>
  <div class="page apps-page">
    <header class="page-heading app-heading">
      <div>
        <span class="eyebrow">APP ARCHITECTURE</span>
        <h1>应用与 Runtime 协作</h1>
        <p>App 是可以独立交付和售卖的业务产品；JoyhouseBot 是它按需使用的长期执行引擎，不接管 App 的用户系统、交易和业务事实。</p>
      </div>
      <div class="product-badge"><span>PRODUCT</span><strong>App ≠ Extension</strong></div>
    </header>

    <section class="app-control panel">
      <div class="section-heading control-heading">
        <div><span class="eyebrow">APP PACK CONTROL PLANE</span><h2>应用包与安装状态</h2></div>
        <div class="heading-actions"><button class="secondary-button" type="button" @click="newDraft">新建草稿</button><button class="secondary-button" type="button" :disabled="loading" @click="load">刷新</button></div>
      </div>
      <p class="control-note">App Pack 只组合已发布资产；Entry Point 将业务动作锁定到 Agent、Team、Scenario 或 Workflow，但不会创建第五种执行模式。</p>
      <div v-if="error" class="control-error">{{ error }}</div>
      <div class="app-console-grid">
        <aside class="release-list">
          <header><strong>发布目录</strong><span>{{ releases.length }}</span></header>
          <button v-for="release in releases" :key="`${release.app_id}:${release.version}`" type="button" :class="{ active: selected?.app_id === release.app_id && selected?.version === release.version }" @click="selectRelease(release)">
            <span :class="['release-status', release.status]">{{ release.status }}</span>
            <strong>{{ release.name }}</strong><small>{{ release.app_id }} · {{ release.version }}</small>
          </button>
          <div v-if="!releases.length && !loading" class="empty-mini">还没有 App Pack 草稿。</div>
        </aside>
        <div class="manifest-editor">
          <header><div><strong>joyhouse.app.json</strong><small>精确版本 + 摘要构成可复现依赖锁</small></div><span v-if="validation" :class="validation.valid ? 'valid' : 'invalid'">{{ validation.valid ? '依赖通过' : '依赖缺失' }}</span></header>
          <textarea v-model="manifestText" rows="22" spellcheck="false" />
          <div v-if="validation?.errors?.length" class="validation-errors"><span v-for="item in validation.errors" :key="item">{{ item }}</span></div>
          <footer>
            <button class="secondary-button" type="button" :disabled="busy" @click="save">保存草稿</button>
            <button class="secondary-button" type="button" :disabled="busy || !selected" @click="validateSelected">校验依赖</button>
            <button class="primary-button" type="button" :disabled="busy || !selected || selected.status !== 'draft'" @click="publishSelected">发布</button>
            <button class="primary-button" type="button" :disabled="busy || !selected || selected.status !== 'published'" @click="installSelected">安装</button>
          </footer>
        </div>
      </div>
      <div class="installation-strip">
        <header><strong>当前用户的安装</strong><span>{{ installations.length }}</span></header>
        <article v-for="item in installations" :key="item.installation_id">
          <div><span :class="['install-dot', item.status]"></span><p><strong>{{ item.name }}</strong><small>{{ item.version }} · {{ item.status }}</small><small>{{ item.manifest?.entrypoints?.length || 0 }} ENTRYPOINTS · {{ item.manifest?.permissions?.length || 0 }} PERMISSIONS</small></p></div>
          <nav>
            <button type="button" @click="manageInstallation(item)">治理</button>
            <button v-if="['installed', 'disabled'].includes(item.status)" type="button" @click="act(item, 'activate')">启用</button>
            <button v-if="item.status === 'active'" type="button" @click="act(item, 'disable')">停用</button>
            <button v-if="item.previous_version" type="button" @click="act(item, 'rollback')">回滚</button>
            <button v-if="item.status !== 'uninstalled'" type="button" @click="beginUpdateSubscription(item)">更新策略</button>
            <button v-if="item.status !== 'uninstalled'" type="button" class="danger" @click="act(item, 'uninstall')">卸载</button>
          </nav>
        </article>
        <div v-if="!installations.length && !loading" class="empty-mini">尚未安装应用包。</div>
      </div>
    </section>

    <section v-if="governanceInstallation" class="governance panel">
      <div class="section-heading control-heading">
        <div><span class="eyebrow">APP OPERATIONS</span><h2>{{ governanceInstallation.name }} · 身份、回调与用量</h2></div>
        <button class="secondary-button" type="button" @click="governanceInstallation = null">关闭</button>
      </div>
      <p class="control-note">Secret 只在创建或轮换时显示一次；Callback 重放会创建新的投递身份，不会重置原 Outbox。</p>
      <div v-if="revealedSecret" class="secret-once"><strong>仅显示一次</strong><code>{{ revealedSecret }}</code><button type="button" @click="revealedSecret = ''">已保存</button></div>
      <div v-if="appUsage" class="usage-grid">
        <article><span>RUNS · 30D</span><strong>{{ appUsage.totals.runs }}</strong></article>
        <article><span>MODEL CALLS</span><strong>{{ appUsage.totals.model_invocations }}</strong></article>
        <article><span>TOKENS</span><strong>{{ appUsage.totals.input_tokens + appUsage.totals.output_tokens }}</strong></article>
        <article><span>MODEL COST</span><strong>${{ appUsage.totals.model_cost_usd.toFixed(4) }}</strong></article>
      </div>
      <div class="governance-grid">
        <article class="governance-card">
          <header><div><strong>App Clients</strong><small>平台创建 · App 后端持有</small></div><span>{{ appClients.length }}</span></header>
          <div v-for="item in appClients" :key="item.client_id" class="governance-row"><p><strong>{{ item.name }}</strong><small>{{ item.client_id }} · {{ item.enabled ? 'enabled' : 'revoked' }}</small><small>{{ item.allowed_scopes.join(', ') }}</small></p><nav><button v-if="item.enabled" type="button" @click="rotateClient(item)">轮换</button><button v-if="item.enabled" class="danger" type="button" @click="removeClient(item)">撤销</button></nav></div>
          <div class="compact-form"><label>名称<input v-model.trim="clientForm.name" placeholder="My App backend" /></label><label>Scopes<input v-model.trim="clientForm.scopes" /></label><button class="primary-button" type="button" @click="addClient">创建 Client</button></div>
        </article>
        <article class="governance-card">
          <header><div><strong>Completion Callbacks</strong><small>终态通知 · HMAC</small></div><span>{{ appCallbacks.length }}</span></header>
          <div v-for="item in appCallbacks" :key="item.callback_id" class="governance-row"><p><strong>{{ item.endpoint }}</strong><small>{{ item.events.join(', ') }}</small><small>{{ item.secret_ref }} · {{ item.enabled ? 'enabled' : 'revoked' }}</small></p><nav><button v-if="item.enabled" class="danger" type="button" @click="removeCallback(item)">撤销</button></nav></div>
          <div class="compact-form"><label>HTTPS Endpoint<input v-model.trim="callbackForm.endpoint" placeholder="https://app.example/callbacks" /></label><label>Secret env 引用<input v-model.trim="callbackForm.secret_ref" placeholder="env://MY_APP_CALLBACK_SECRET" /></label><button class="primary-button" type="button" @click="addCallback">登记 Callback</button></div>
        </article>
        <article class="governance-card">
          <header><div><strong>User Grants</strong><small>当前用户 · 单安装授权</small></div><span>{{ appGrants.length }}</span></header>
          <div v-for="item in appGrants" :key="item.grant_id" class="governance-row"><p><strong>{{ item.client_id }}</strong><small>{{ item.scopes.join(', ') }}</small><small>expires {{ item.expires_at }} · {{ item.enabled ? 'enabled' : 'revoked' }}</small></p><nav><button v-if="item.enabled" class="danger" type="button" @click="removeGrant(item)">撤销</button></nav></div>
          <div class="compact-form"><label>Client<select v-model="grantForm.client_id"><option value="">选择 Client</option><option v-for="item in appClients.filter(value => value.enabled)" :key="item.client_id" :value="item.client_id">{{ item.name }}</option></select></label><label>Scopes<input v-model.trim="grantForm.scopes" /></label><label>Expires<input v-model="grantForm.expires_at" type="datetime-local" /></label><button class="primary-button" type="button" @click="addGrant">授权</button></div>
        </article>
      </div>
      <div class="delivery-diagnostics">
        <header><div><strong>Callback 投递诊断</strong><small>输入 App Run ID 查看状态并重放 sent/dead 记录</small></div><div><input v-model.trim="deliveryRunId" placeholder="run_id" /><button class="secondary-button" type="button" @click="loadDeliveries">查询</button></div></header>
        <div v-for="item in callbackDeliveries" :key="item.event_id" class="delivery-row"><code>{{ item.event_id }}</code><span :class="['delivery-status', item.status]">{{ item.status }}</span><small>attempt {{ item.attempt }}/{{ item.max_attempts }} · HTTP {{ item.response_status || '-' }}</small><button v-if="['sent', 'dead'].includes(item.status)" type="button" @click="replayDelivery(item)">创建重放</button></div>
      </div>
    </section>

    <section class="market-control panel">
      <div class="section-heading control-heading">
        <div><span class="eyebrow">JOYHOUSE MARKET</span><h2>Market 账号、购买与可信获取</h2></div>
        <span class="market-summary">{{ registries.length }} REGISTRIES · {{ acquisitions.length }} ACQUISITIONS</span>
      </div>
      <p class="control-note">市场只负责发现、签名制品、商业授权与治理。购买不会自动安装，Market 也不能授予本地 Capability 权限。</p>
      <div class="market-grid">
        <div class="market-column">
          <header><strong>Registry 信任根</strong><small>TUF ROOT + DISCOVERY</small></header>
          <article v-for="item in registries" :key="item.registry_id" class="market-card">
            <div><span :class="['install-dot', item.status === 'active' ? 'active' : 'failed']"></span><strong>{{ item.market_id }}</strong></div>
            <small>{{ item.status }} · protocol {{ item.protocol_version }} · {{ Object.keys(item.discovery?.contract_keys || {}).length }} contract keys</small>
            <button class="secondary-button market-open" type="button" :disabled="busy || item.status !== 'active'" @click="openMarket(item)">登录 Market / 浏览购买</button>
          </article>
          <details class="market-form">
            <summary>添加可信 Registry</summary>
            <label>HTTPS Origin<input v-model.trim="registryForm.base_url" placeholder="https://market.example" /></label>
            <label>Access Token 引用<input v-model.trim="registryForm.auth_token_ref" placeholder="env://JOYHOUSE_MARKET_TOKEN" /></label>
            <label>Discovery JSON<textarea v-model="registryForm.discovery" rows="7" spellcheck="false" /></label>
            <label>TUF root.json<textarea v-model="registryForm.trusted_root" rows="9" spellcheck="false" /></label>
            <button class="primary-button" type="button" :disabled="busy" @click="registerRegistry">固定信任根</button>
          </details>
        </div>
        <div class="market-column">
          <header><strong>获取签名 App</strong><small>WORKER VERIFIED</small></header>
          <div class="acquire-form">
            <label>Registry<select v-model="acquireForm.registry_id"><option value="">选择 Registry</option><option v-for="item in registries" :key="item.registry_id" :value="item.registry_id">{{ item.market_id }}</option></select></label>
            <label>Publisher ID<input v-model.trim="acquireForm.publisher_id" placeholder="pub_..." /></label>
            <label>App ID<input v-model.trim="acquireForm.app_id" placeholder="app.market-radar" /></label>
            <label>Offer ID<input v-model.trim="acquireForm.offer_id" placeholder="购买后自动填写" /></label>
            <label>Version<input v-model.trim="acquireForm.version" placeholder="1.0.0（空为当前稳定版）" /></label>
            <label>Channel<select v-model="acquireForm.channel"><option value="stable">stable</option><option value="beta">beta</option><option value="security">security</option></select></label>
            <p v-if="receivedEntitlement" class="trust-line">已从 JoyHouse Market 接收绑定本机公钥的签名 Entitlement；Bearer Token 不会写入 Runtime。</p>
            <button class="primary-button" type="button" :disabled="busy || !acquireForm.registry_id" @click="requestAcquisition">解析并暂存</button>
          </div>
        </div>
      </div>
      <div v-if="updateForm.installation_id" class="update-config">
        <header><div><strong>持续更新策略</strong><small>{{ updateForm.app_id }} · 自动激活保持关闭</small></div><button type="button" @click="clearUpdateForm">取消</button></header>
        <label>Registry<select v-model="updateForm.registry_id"><option v-for="item in registries" :key="item.registry_id" :value="item.registry_id">{{ item.market_id }}</option></select></label>
        <label>Publisher ID<input v-model.trim="updateForm.publisher_id" /></label>
        <label>版本范围<input v-model.trim="updateForm.version_constraint" placeholder=">=1.0.0 <2.0.0" /></label>
        <label>频道<select v-model="updateForm.channel"><option value="stable">stable</option><option value="beta">beta</option><option value="security">security</option></select></label>
        <label>策略<select v-model="updateForm.policy"><option value="notify">仅通知</option><option value="download">验证并下载</option><option value="stage">验证并暂存，等待确认</option></select></label>
        <button class="primary-button" type="button" :disabled="busy || !updateForm.registry_id" @click="persistUpdateSubscription">保存更新策略</button>
      </div>
      <div v-if="updateSubscriptions.length" class="update-subscriptions">
        <article v-for="item in updateSubscriptions" :key="item.subscription_id">
          <header><strong>{{ item.app_id }}</strong><span>{{ item.policy }} · {{ item.channel }}</span></header>
          <small>当前 {{ item.current_version }} · 范围 {{ item.version_constraint }}</small>
          <p v-if="item.latest_release?.version">发现 {{ item.latest_release.version }}，Scheduler 已按策略处理。</p>
          <p v-if="item.last_error" class="market-error">{{ item.last_error.type }} · {{ item.last_error.message }}</p>
        </article>
      </div>
      <div class="acquisition-list">
        <article v-for="item in acquisitions" :key="item.acquisition_id">
          <header><div><span :class="['acquisition-state', item.status]">{{ item.status }}</span><strong>{{ item.app_id }} {{ item.resolved_version || item.requested_version }}</strong></div><small>{{ item.publisher_id }} · {{ item.channel }}</small></header>
          <p v-if="item.bundle_digest"><code>{{ item.bundle_digest }}</code></p>
          <p v-if="['staged', 'awaiting_acceptance'].includes(item.status)" class="trust-line">作者签名、TUF Target 与 Market Attestation 已验证；仍需你确认权限差异。</p>
          <pre v-if="['staged', 'awaiting_acceptance'].includes(item.status)">{{ JSON.stringify(item.permission_diff, null, 2) }}</pre>
          <p v-if="item.error" class="market-error">{{ item.error.type }} · {{ item.error.message }}</p>
          <nav v-if="['staged', 'awaiting_acceptance'].includes(item.status)"><button class="primary-button" type="button" :disabled="busy" @click="acquisitionAction(item, 'accept')">接受并导入草稿</button><button class="secondary-button" type="button" :disabled="busy" @click="acquisitionAction(item, 'reject')">拒绝</button></nav>
        </article>
        <div v-if="!acquisitions.length && !loading" class="empty-mini">还没有跨实例获取记录。</div>
      </div>
    </section>

    <section class="concept-map panel">
      <div class="section-heading">
        <div><span class="eyebrow">PRODUCT LANGUAGE</span><h2>统一概念</h2></div>
        <p>面向用户谈 App、持续任务和成果；只有开发者与运维人员需要看到 Capability 和 Extension。</p>
      </div>
      <div class="concept-grid">
        <article v-for="item in concepts" :key="item.name" :class="item.level">
          <header><span>{{ item.mark }}</span><small>{{ item.audience }}</small></header>
          <h3>{{ item.name }}</h3>
          <strong>{{ item.definition }}</strong>
          <p>{{ item.boundary }}</p>
        </article>
      </div>
    </section>

    <section class="collaboration panel">
      <div class="section-heading">
        <div><span class="eyebrow">INDEPENDENT APP</span><h2>业务边界保持独立</h2></div>
        <p>最稳妥的结构是两个独立系统通过版本化协议协作，而不是把 App 代码安装进 Runtime。</p>
      </div>
      <div class="ownership-map">
        <article>
          <span class="system-label app-label">BUSINESS APP</span>
          <h3>App 自己拥有</h3>
          <ul><li v-for="item in appOwns" :key="item">{{ item }}</li></ul>
        </article>
        <div class="contract-column">
          <div><span>提交执行</span><strong>App Entry Point + Idempotency-Key</strong><small>锁定安装与版本，返回 run_id，通过 SSE 或查询跟踪结果</small></div>
          <b>⇄</b>
          <div><span>反向业务操作</span><strong>Remote Capability</strong><small>签名请求、action_id、回执与对账</small></div>
        </div>
        <article>
          <span class="system-label runtime-label">JOYHOUSEBOT</span>
          <h3>Runtime 统一承担</h3>
          <ul><li v-for="item in runtimeOwns" :key="item">{{ item }}</li></ul>
        </article>
      </div>
    </section>

    <section class="flows-grid">
      <article class="panel flow-card">
        <span class="eyebrow">APP → RUNTIME</span><h2>把长期任务交给执行引擎</h2>
        <div class="flow-steps"><div v-for="(item, index) in submitFlow" :key="item.title"><span>{{ index + 1 }}</span><p><strong>{{ item.title }}</strong><small>{{ item.detail }}</small></p></div></div>
      </article>
      <article class="panel flow-card">
        <span class="eyebrow">RUNTIME → APP</span><h2>受控调用 App 的业务能力</h2>
        <div class="flow-steps"><div v-for="(item, index) in callbackFlow" :key="item.title"><span>{{ index + 1 }}</span><p><strong>{{ item.title }}</strong><small>{{ item.detail }}</small></p></div></div>
      </article>
    </section>

    <section class="identity-commerce panel">
      <div class="identity-block">
        <span class="eyebrow">IDENTITY & DATA</span><h2>用户与数据如何对齐</h2>
        <p>App 的用户表仍是身份事实源。App 后端只向 Runtime 传递稳定、无个人信息的主体映射；Runtime 按 <code>user_id + agent_id + root_run_id</code> 隔离执行数据。</p>
        <div class="identity-rule"><strong>生产规则</strong><span>不能使用 <code>X-User-ID</code> 代替认证。App Client 必须获得用户对单个安装的 Grant，再交换最长一小时、scope 缩减且绑定安装的 Token；重新授权或撤销会立即使旧 Token 失效。</span></div>
      </div>
      <div class="commerce-block">
        <span class="eyebrow">COMMERCIAL DELIVERY</span><h2>App 可以独立售卖</h2>
        <div class="commerce-options"><article v-for="item in commerce" :key="item.name"><span>{{ item.index }}</span><div><strong>{{ item.name }}</strong><p>{{ item.description }}</p></div></article></div>
      </div>
    </section>

    <section class="readiness panel">
      <div class="section-heading">
        <div><span class="eyebrow">INTEGRATION READINESS</span><h2>协作能力现状</h2></div>
        <p>控制台不把规划能力伪装成已经完成的产品能力。</p>
      </div>
      <div class="readiness-grid">
        <article><span class="status ready">当前可用</span><ul><li v-for="item in ready" :key="item">{{ item }}</li></ul></article>
        <article><span class="status next">独立 App 规模化前</span><ul><li v-for="item in next" :key="item">{{ item }}</li></ul></article>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  acquireMarketApp,
  actOnAppAcquisition,
  authorizeAppGrant,
  createAppClient,
  ensureMarketInstallationKey,
  getAppUsage,
  installAppPack,
  listAppAcquisitions,
  listAppCallbacks,
  listAppClients,
  listAppGrants,
  listAppInstallations,
  listAppPacks,
  listMarketRegistries,
  listRunAppCallbacks,
  listUpdateSubscriptions,
  publishAppRelease,
  registerAppCallback,
  registerMarketRegistry,
  replayRunAppCallback,
  revokeAppCallback,
  revokeAppClient,
  revokeAppGrant,
  rotateAppClientSecret,
  saveAppRelease,
  saveUpdateSubscription,
  transitionAppPack,
  validateAppRelease,
  type AppAcquisition,
  type AppCallback,
  type AppCallbackDelivery,
  type AppClient,
  type AppGrant,
  type AppInstallation,
  type AppRelease,
  type AppUsage,
  type AppValidationReport,
  type MarketRegistry,
  type UpdateSubscription,
} from '../api/apps'

const releases = ref<AppRelease[]>([])
const installations = ref<AppInstallation[]>([])
const registries = ref<MarketRegistry[]>([])
const acquisitions = ref<AppAcquisition[]>([])
const updateSubscriptions = ref<UpdateSubscription[]>([])
const selected = ref<AppRelease | null>(null)
const validation = ref<AppValidationReport | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const manifestText = ref('')
const registryForm = ref({ base_url: '', auth_token_ref: '', discovery: '{}', trusted_root: '{}' })
const acquireForm = ref<{ registry_id: string; publisher_id: string; app_id: string; offer_id: string; version: string; channel: 'stable' | 'beta' | 'security' }>({ registry_id: '', publisher_id: '', app_id: '', offer_id: '', version: '', channel: 'stable' })
const receivedEntitlement = ref<Record<string, any> | null>(null)
const updateForm = ref<{ installation_id: string; registry_id: string; publisher_id: string; app_id: string; channel: 'stable' | 'beta' | 'security'; version_constraint: string; policy: 'notify' | 'download' | 'stage' }>({ installation_id: '', registry_id: '', publisher_id: '', app_id: '', channel: 'stable', version_constraint: '*', policy: 'notify' })
const governanceInstallation = ref<AppInstallation | null>(null)
const appClients = ref<AppClient[]>([])
const appCallbacks = ref<AppCallback[]>([])
const appGrants = ref<AppGrant[]>([])
const appUsage = ref<AppUsage | null>(null)
const callbackDeliveries = ref<AppCallbackDelivery[]>([])
const deliveryRunId = ref('')
const revealedSecret = ref('')
const clientForm = ref({ name: '', scopes: 'apps.read, apps.launch, runs.read, runs.write' })
const callbackForm = ref({ endpoint: '', secret_ref: '', events: ['run.completed', 'run.failed', 'run.cancelled', 'run.timed_out'], max_attempts: 8 })
const grantForm = ref({ client_id: '', scopes: 'apps.read, apps.launch, runs.read, runs.write', expires_at: new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 16) })

function draftManifest() {
  return {
    schema_version: 2,
    app_id: 'app.my-app',
    version: '0.1.0',
    name: 'My App',
    description: '',
    publisher: '',
    publisher_id: 'pub_myapp01',
    core: { min_version: '0.1.2', max_version: '' },
    extensions: [], capabilities: [],
    assets: { agents: [], teams: [], skills: [], workflows: [], scenarios: [] },
    entrypoints: [],
    integrations: [], permissions: [], secrets: [], triggers: [], evaluations: [],
    configuration_schema: {}, ui: {}, metadata: {},
    licenses: { code_expression: 'Apache-2.0' }, evidence: {},
    data_practices: { telemetry: 'none', outbound_domains: [], collects_personal_data: false, retention_days: 0 },
    metering: [],
  }
}
function newDraft() { selected.value = null; validation.value = null; manifestText.value = JSON.stringify(draftManifest(), null, 2) }
function selectRelease(release: AppRelease) { selected.value = release; validation.value = release.validation_report?.valid === undefined ? null : release.validation_report as AppValidationReport; manifestText.value = JSON.stringify(release.manifest, null, 2) }
function parseManifest() { const value = JSON.parse(manifestText.value); if (!value.app_id || !value.version) throw new Error('manifest 必须包含 app_id 和 version'); return value }
async function load() {
  loading.value = true; error.value = ''
  try { [releases.value, installations.value, registries.value, acquisitions.value, updateSubscriptions.value] = await Promise.all([listAppPacks(), listAppInstallations(), listMarketRegistries(), listAppAcquisitions(), listUpdateSubscriptions()]); if (!manifestText.value) newDraft() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取 App Pack 失败' }
  finally { loading.value = false }
}
async function save() {
  busy.value = true; error.value = ''
  try { const release = await saveAppRelease(parseManifest()); await load(); const current = releases.value.find(item => item.app_id === release.app_id && item.version === release.version) || release; selectRelease(current) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '保存失败' }
  finally { busy.value = false }
}
async function validateSelected() { if (!selected.value) return; busy.value = true; error.value = ''; try { validation.value = await validateAppRelease(selected.value.app_id, selected.value.version); await load(); const current = releases.value.find(item => item.app_id === selected.value?.app_id && item.version === selected.value?.version); if (current) selectRelease(current) } catch (cause) { error.value = cause instanceof Error ? cause.message : '校验失败' } finally { busy.value = false } }
async function publishSelected() { if (!selected.value) return; const key = `${selected.value.app_id}:${selected.value.version}`; busy.value = true; error.value = ''; try { await publishAppRelease(selected.value.app_id, selected.value.version); await load(); const current = releases.value.find(item => `${item.app_id}:${item.version}` === key); if (current) selectRelease(current) } catch (cause) { error.value = cause instanceof Error ? cause.message : '发布失败' } finally { busy.value = false } }
async function installSelected() { if (!selected.value || !window.confirm(`安装 ${selected.value.name} ${selected.value.version} 并授予清单声明的权限？`)) return; busy.value = true; error.value = ''; try { await installAppPack(selected.value); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '安装失败' } finally { busy.value = false } }
async function act(item: AppInstallation, action: 'activate' | 'disable' | 'rollback' | 'uninstall') { if (action === 'uninstall' && !window.confirm(`卸载 ${item.name}？执行记录和审计事件仍会保留。`)) return; busy.value = true; error.value = ''; try { await transitionAppPack(item.installation_id, action); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '状态切换失败' } finally { busy.value = false } }
async function registerRegistry() { busy.value = true; error.value = ''; try { await registerMarketRegistry({ base_url: registryForm.value.base_url, auth_token_ref: registryForm.value.auth_token_ref, discovery: JSON.parse(registryForm.value.discovery), trusted_root: JSON.parse(registryForm.value.trusted_root), policy: {} }); registryForm.value = { base_url: '', auth_token_ref: '', discovery: '{}', trusted_root: '{}' }; await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Registry 登记失败' } finally { busy.value = false } }
async function openMarket(item: MarketRegistry) {
  busy.value = true; error.value = ''
  try {
    const key = await ensureMarketInstallationKey(item.registry_id)
    const marketUrl = String(item.discovery?.market_web_url || item.base_url)
    const url = new URL(marketUrl)
    url.searchParams.set('installation_public_key', key.public_key)
    url.searchParams.set('return_origin', window.location.origin)
    url.searchParams.set('registry_id', item.registry_id)
    window.open(url.toString(), 'joyhouse-market', 'popup,width=1180,height=820')
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Market 打开失败' }
  finally { busy.value = false }
}
function receiveMarketEntitlement(event: MessageEvent) {
  const registry = registries.value.find((item) => {
    try { return new URL(String(item.discovery?.market_web_url || item.base_url)).origin === event.origin } catch { return false }
  })
  const value = event.data
  if (!registry || value?.type !== 'joyhouse-market-entitlement' || !value.entitlement?.payload || !value.entitlement?.envelope) return
  const payload = value.entitlement.payload as Record<string, any>
  const app = payload.app as Record<string, any>
  if (!app?.publisher_id || !app?.app_id || !payload.offer_id) return
  receivedEntitlement.value = value.entitlement
  acquireForm.value = { ...acquireForm.value, registry_id: registry.registry_id, publisher_id: String(app.publisher_id), app_id: String(app.app_id), offer_id: String(payload.offer_id) }
}
async function requestAcquisition() { busy.value = true; error.value = ''; try { await acquireMarketApp({ ...acquireForm.value, offer_id: acquireForm.value.offer_id || null, version: acquireForm.value.version || null, entitlement: receivedEntitlement.value }); receivedEntitlement.value = null; await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'App 获取请求失败' } finally { busy.value = false } }
async function acquisitionAction(item: AppAcquisition, action: 'accept' | 'reject') { if (action === 'accept' && !window.confirm('确认权限、数据出站、Secret、Meter 与 Extension 差异，并把已验证 App 导入为本地草稿？')) return; busy.value = true; error.value = ''; try { await actOnAppAcquisition(item.acquisition_id, action); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '获取状态切换失败' } finally { busy.value = false } }
function clearUpdateForm() { updateForm.value = { installation_id: '', registry_id: '', publisher_id: '', app_id: '', channel: 'stable', version_constraint: '*', policy: 'notify' } }
function beginUpdateSubscription(item: AppInstallation) {
  const release = releases.value.find(value => value.app_id === item.app_id && value.version === item.version)
  const origin = (release?.origin_ref || {}) as Record<string, string>
  const existing = updateSubscriptions.value.find(value => value.installation_id === item.installation_id)
  if (!existing && (!origin.registry_id || !origin.publisher_id)) { error.value = '只有从可信 Market 获取的 App 才能建立远程更新订阅。'; return }
  updateForm.value = {
    installation_id: item.installation_id,
    registry_id: existing?.registry_id || origin.registry_id,
    publisher_id: existing?.publisher_id || origin.publisher_id,
    app_id: item.app_id,
    channel: existing?.channel || 'stable',
    version_constraint: existing?.version_constraint || '*',
    policy: existing?.policy === 'activate_safe' ? 'notify' : existing?.policy || 'notify',
  }
}
async function persistUpdateSubscription() {
  busy.value = true; error.value = ''
  try {
    await saveUpdateSubscription({ ...updateForm.value, allow_security_patch_download: true, allow_auto_stage: updateForm.value.policy === 'stage', allow_auto_activate: false })
    clearUpdateForm(); await load()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '更新策略保存失败' }
  finally { busy.value = false }
}

function scopeList(value: string) { return [...new Set(value.split(',').map(item => item.trim()).filter(Boolean))] }
async function refreshGovernance() {
  if (!governanceInstallation.value) return
  const item = governanceInstallation.value
  ;[appClients.value, appCallbacks.value, appGrants.value, appUsage.value] = await Promise.all([
    listAppClients(item.app_id), listAppCallbacks(item.installation_id), listAppGrants(item.installation_id), getAppUsage(item.installation_id),
  ])
}
async function manageInstallation(item: AppInstallation) { governanceInstallation.value = item; callbackDeliveries.value = []; deliveryRunId.value = ''; busy.value = true; error.value = ''; try { await refreshGovernance() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'App 治理信息读取失败' } finally { busy.value = false } }
async function addClient() { if (!governanceInstallation.value) return; busy.value = true; error.value = ''; try { const value = await createAppClient({ app_id: governanceInstallation.value.app_id, name: clientForm.value.name, allowed_scopes: scopeList(clientForm.value.scopes) }); revealedSecret.value = value.client_secret || ''; clientForm.value.name = ''; await refreshGovernance() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Client 创建失败' } finally { busy.value = false } }
async function rotateClient(item: AppClient) { if (!window.confirm(`轮换 ${item.name} 的 Secret？现有短期 Token 会立即失效。`)) return; busy.value = true; try { const value = await rotateAppClientSecret(item.client_id); revealedSecret.value = value.client_secret || ''; await refreshGovernance() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Secret 轮换失败' } finally { busy.value = false } }
async function removeClient(item: AppClient) { if (!window.confirm(`撤销 ${item.name}、其 Grants 和 Token？`)) return; busy.value = true; try { await revokeAppClient(item.client_id); await refreshGovernance() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Client 撤销失败' } finally { busy.value = false } }
async function addCallback() { if (!governanceInstallation.value) return; busy.value = true; try { await registerAppCallback(governanceInstallation.value.installation_id, callbackForm.value); callbackForm.value.endpoint = ''; callbackForm.value.secret_ref = ''; await refreshGovernance() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Callback 登记失败' } finally { busy.value = false } }
async function removeCallback(item: AppCallback) { if (!governanceInstallation.value) return; busy.value = true; try { await revokeAppCallback(governanceInstallation.value.installation_id, item.callback_id); await refreshGovernance() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Callback 撤销失败' } finally { busy.value = false } }
async function addGrant() { if (!governanceInstallation.value) return; busy.value = true; try { await authorizeAppGrant(governanceInstallation.value.installation_id, { client_id: grantForm.value.client_id, scopes: scopeList(grantForm.value.scopes), expires_at: new Date(grantForm.value.expires_at).toISOString() }); await refreshGovernance() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Grant 授权失败' } finally { busy.value = false } }
async function removeGrant(item: AppGrant) { busy.value = true; try { await revokeAppGrant(item.grant_id); await refreshGovernance() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Grant 撤销失败' } finally { busy.value = false } }
async function loadDeliveries() { if (!deliveryRunId.value) return; busy.value = true; try { callbackDeliveries.value = await listRunAppCallbacks(deliveryRunId.value) } catch (cause) { error.value = cause instanceof Error ? cause.message : '投递记录读取失败' } finally { busy.value = false } }
async function replayDelivery(item: AppCallbackDelivery) { if (!window.confirm(`为 ${item.event_id} 创建一次新的重放投递？`)) return; busy.value = true; try { await replayRunAppCallback(item.run_id, item.event_id); await loadDeliveries() } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Callback 重放失败' } finally { busy.value = false } }

onMounted(() => { window.addEventListener('message', receiveMarketEntitlement); load() })
onBeforeUnmount(() => window.removeEventListener('message', receiveMarketEntitlement))

const concepts = [
  { mark: 'A', name: 'App', audience: '用户产品', level: 'product', definition: '解决一个完整业务问题', boundary: '拥有独立界面、用户、计费与业务逻辑，可以单独部署和售卖。' },
  { mark: 'T', name: 'Task Pack', audience: '持续任务', level: 'product', definition: '可安装的长期任务产品', boundary: '定义目标、节奏、确认点、成本、依赖和成果，不自动授予权限。' },
  { mark: 'S', name: 'Skill', audience: '方法资产', level: 'asset', definition: '如何完成某类工作的版本化方法包', boundary: '包含说明、模板、Schema 与 Eval；不直接获得网络、代码或业务写权限。' },
  { mark: 'W', name: 'Workflow', audience: '执行结构', level: 'asset', definition: '步骤、分支和状态如何流转', boundary: '描述执行顺序，最终仍编译到统一 Run / Task 链路。' },
  { mark: 'G', name: 'Agent', audience: '执行角色', level: 'runtime', definition: '承担角色并选择 Skill 与 Capability', boundary: '冻结模型、策略和能力准入，不等同于一个完整业务产品。' },
  { mark: 'C', name: 'Capability', audience: '原子动作', level: 'runtime', definition: 'Runtime 可以治理和调用的动作', boundary: '所有调用经过参数、权限、审批、幂等、配额和审计。' },
  { mark: 'I', name: 'Integration', audience: '外部连接', level: 'technical', definition: '连接模型、邮件和既有业务系统', boundary: '负责协议和账号连接，不创建第二套任务状态机。' },
  { mark: 'E', name: 'Extension', audience: '技术安装', level: 'technical', definition: '扩展 Runtime 的代码制品', boundary: '安装 Provider、Channel、Connector 或 Capability；不是业务 App。' },
]

const appOwns = ['登录、用户关系与会员体系', '订阅、订单、授权与售后', '业务页面、领域规则和交易事务', '完整业务数据库与隐私策略', '产品定价、品牌和发布节奏']
const runtimeOwns = ['Run / Task / Schedule 持久状态', '长任务恢复、Lease 与故障接管', 'Agent、Workflow 与能力调度', '审批、幂等、重试、对账和审计', 'Artifact / Work、成本、质量与 Eval']
const submitFlow = [
  { title: '用户授权安装', detail: 'App Client 获取单安装 Grant，再交换短期、最小 scope Token。' },
  { title: '启动 App Entry Point', detail: '携带稳定 Idempotency-Key；Runtime 重新校验锁定版本并提交统一 Run。' },
  { title: '保存 run_id', detail: 'App 只保存执行引用，不复制 Runtime 状态机。' },
  { title: '接收终态通知', detail: '可选 HMAC 回调来自事务 Outbox；失败自动重试并可查询死信。' },
  { title: '读取与展示结果', detail: '用同一安装 Token 查询 Run、确认点和 Artifact；回调不携带私有结果。' },
]
const callbackFlow = [
  { title: '登记远程连接', detail: 'App 暴露窄 Capability，而不是开放内部数据库。' },
  { title: 'Worker 签名调用', detail: 'Runtime 冻结 action_id 与 idempotency_key。' },
  { title: 'App 执行业务事务', detail: '在自己的权限与事务边界内验证并写入。' },
  { title: '回执与对账', detail: '同步返回 WriteReceipt；异步操作提供查询与最终状态。' },
]
const commerce = [
  { index: '01', name: '独立 SaaS', description: 'App 自己获客、收费和托管，通过 API 使用官方或自建 Runtime。' },
  { index: '02', name: 'Runtime 随产品交付', description: 'App 套餐中捆绑托管 JoyhouseBot，但用户仍只感知 App 品牌。' },
  { index: '03', name: 'Bring Your Own Runtime', description: '客户填写自己的 Runtime 地址和授权，App 仅销售业务价值。' },
]
const ready = ['App Manifest v2、不可变摘要与版本化 Entry Point', '公共 App 列表、安装级幂等 Run 启动与结果隔离', 'App Client、用户 Grant、短期 Token、Secret 轮换与审计', '签名终态 Outbox、重试、死信、人工重放与投递观测', 'App SDK、无数据库模拟器、安装级 Token/模型成本归因', '作者 DSSE、Market Attestation、TUF、Acquisition 与更新策略']
const next = ['生产支付服务商与税务/KYC 适配', '第三方 Market 互操作认证与产品工作台', '跨区域部署的容量基线与灾备常态演练']
</script>

<style scoped>
.apps-page{display:grid;gap:16px}.app-heading{align-items:center}.app-heading>div:first-child{max-width:900px}.product-badge{display:grid;min-width:176px;gap:4px;padding:15px 18px;background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:12px}.product-badge span{color:var(--accent);font:9px var(--font-mono);letter-spacing:.12em}.product-badge strong{color:var(--text-strong);font-size:15px}.app-control{padding:22px}.control-heading{margin-bottom:8px}.heading-actions{display:flex;gap:8px}.control-note{margin:0 0 16px;color:var(--text-muted);font-size:10px}.control-error{margin-bottom:12px;padding:10px 12px;color:var(--danger);background:var(--danger-subtle);border:1px solid var(--danger-border);border-radius:8px;font-size:10px}.app-console-grid{display:grid;grid-template-columns:260px 1fr;gap:14px}.release-list{display:flex;min-height:440px;flex-direction:column;gap:7px;padding:12px;background:var(--surface);border:1px solid var(--border);border-radius:11px}.release-list>header,.installation-strip>header,.manifest-editor>header{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}.release-list>header strong,.installation-strip>header strong,.manifest-editor>header strong{color:var(--text-strong);font-size:11px}.release-list>header span,.installation-strip>header span{color:var(--text-muted);font:9px var(--font-mono)}.release-list>button{display:grid;gap:4px;padding:11px;text-align:left;background:var(--surface-raised);border:1px solid var(--border);border-radius:9px}.release-list>button.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent-border)}.release-list>button strong{color:var(--text-strong);font-size:10px}.release-list>button small{color:var(--text-muted);font:8px var(--font-mono)}.release-status{width:max-content;padding:3px 5px;border-radius:4px;color:var(--text-muted);background:var(--surface-muted);font:7px var(--font-mono);text-transform:uppercase}.release-status.published{color:var(--success);background:rgba(50,182,122,.09)}.manifest-editor{display:grid;gap:10px}.manifest-editor>header small{display:block;margin-top:3px;color:var(--text-muted);font-size:8px}.manifest-editor>header>span{padding:5px 7px;border-radius:6px;font:8px var(--font-mono)}.manifest-editor>header>span.valid{color:var(--success);background:rgba(50,182,122,.09)}.manifest-editor>header>span.invalid{color:var(--danger);background:var(--danger-subtle)}.manifest-editor textarea{width:100%;min-height:420px;padding:14px;color:var(--text);background:#111820;border:1px solid var(--border);border-radius:10px;font:10px/1.6 var(--font-mono);resize:vertical}.manifest-editor footer{display:flex;justify-content:flex-end;gap:8px}.validation-errors{display:grid;gap:4px;padding:10px;color:var(--danger);background:var(--danger-subtle);border-radius:8px;font:8px var(--font-mono)}.installation-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-top:16px}.installation-strip>header{grid-column:1/-1}.installation-strip article{display:grid;gap:10px;padding:12px;background:var(--surface-raised);border:1px solid var(--border);border-radius:10px}.installation-strip article>div{display:flex;align-items:center;gap:8px}.installation-strip p{display:grid;gap:3px;margin:0}.installation-strip p strong{color:var(--text-strong);font-size:10px}.installation-strip p small{color:var(--text-muted);font:8px var(--font-mono)}.install-dot{width:8px;height:8px;background:var(--text-muted);border-radius:50%}.install-dot.active{background:var(--success)}.install-dot.failed{background:var(--danger)}.installation-strip nav{display:flex;flex-wrap:wrap;gap:6px}.installation-strip nav button{padding:4px 7px;color:var(--text-muted);background:var(--surface);border:1px solid var(--border);border-radius:5px;font-size:8px}.installation-strip nav button.danger{color:var(--danger)}.empty-mini{padding:20px;color:var(--text-muted);text-align:center;font-size:9px}.concept-map,.collaboration,.identity-commerce,.readiness{padding:22px}.section-heading{display:flex;align-items:end;justify-content:space-between;gap:24px;margin-bottom:18px}.section-heading h2,.flow-card h2,.identity-commerce h2{margin:5px 0 0;color:var(--text-strong);font-size:20px}.section-heading>p{max-width:600px;margin:0;color:var(--text-muted);font-size:11px;line-height:1.65;text-align:right}.concept-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.concept-grid article{min-height:190px;padding:16px;background:var(--surface-raised);border:1px solid var(--border);border-top:3px solid var(--border-strong);border-radius:11px}.concept-grid article.product{border-top-color:var(--accent)}.concept-grid article.asset{border-top-color:#a875d3}.concept-grid article.runtime{border-top-color:#3ca87a}.concept-grid article.technical{border-top-color:#6f7f91}.concept-grid header{display:flex;align-items:center;justify-content:space-between}.concept-grid header>span{display:grid;width:30px;height:30px;place-items:center;color:var(--accent);background:var(--accent-subtle);border-radius:8px;font:700 11px var(--font-mono)}.concept-grid header small{color:var(--text-muted);font:8px var(--font-mono)}.concept-grid h3{margin:13px 0 5px;color:var(--text-strong);font-size:16px}.concept-grid article>strong{display:block;color:var(--text);font-size:10px;line-height:1.5}.concept-grid article>p{margin:8px 0 0;color:var(--text-muted);font-size:9px;line-height:1.65}.ownership-map{display:grid;grid-template-columns:minmax(260px,1fr) 250px minmax(260px,1fr);gap:18px;align-items:stretch}.ownership-map>article{padding:19px;background:var(--surface-raised);border:1px solid var(--border);border-radius:12px}.system-label{display:inline-flex;padding:5px 8px;border-radius:6px;font:8px var(--font-mono);letter-spacing:.08em}.app-label{color:var(--accent);background:var(--accent-subtle)}.runtime-label{color:var(--success);background:rgba(50,182,122,.09)}.ownership-map h3{margin:13px 0 12px;color:var(--text-strong);font-size:16px}.ownership-map ul,.readiness ul{display:grid;gap:8px;margin:0;padding-left:18px;color:var(--text-muted);font-size:10px;line-height:1.5}.contract-column{display:flex;flex-direction:column;justify-content:center;gap:8px;text-align:center}.contract-column div{display:grid;gap:4px;padding:13px;background:var(--surface);border:1px dashed var(--accent-border);border-radius:10px}.contract-column span{color:var(--accent);font-size:9px}.contract-column strong{color:var(--text-strong);font-size:10px}.contract-column small{color:var(--text-muted);font-size:8px;line-height:1.5}.contract-column>b{color:var(--accent);font-size:20px}.flows-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.flow-card{padding:22px}.flow-steps{display:grid;margin-top:18px}.flow-steps>div{display:grid;grid-template-columns:28px 1fr;gap:11px;min-height:67px}.flow-steps>div>span{display:grid;width:26px;height:26px;place-items:center;color:var(--accent);background:var(--accent-subtle);border:1px solid var(--accent-border);border-radius:50%;font:9px var(--font-mono)}.flow-steps p{display:grid;gap:4px;margin:0;padding-bottom:12px;border-bottom:1px solid var(--border)}.flow-steps strong{color:var(--text-strong);font-size:11px}.flow-steps small{color:var(--text-muted);font-size:9px;line-height:1.55}.identity-commerce{display:grid;grid-template-columns:1fr 1fr;gap:36px}.identity-block>p{color:var(--text-muted);font-size:11px;line-height:1.75}.identity-block code{font-size:9px}.identity-rule{display:grid;gap:5px;padding:13px;background:var(--warning-subtle);border-left:2px solid var(--warning);border-radius:5px}.identity-rule strong{color:var(--warning);font-size:10px}.identity-rule span{color:var(--text-muted);font-size:9px;line-height:1.6}.commerce-options{display:grid;gap:8px;margin-top:15px}.commerce-options article{display:grid;grid-template-columns:32px 1fr;gap:10px;padding:10px;background:var(--surface-raised);border:1px solid var(--border);border-radius:9px}.commerce-options article>span{color:var(--accent);font:10px var(--font-mono)}.commerce-options article div{display:grid;gap:3px}.commerce-options strong{color:var(--text-strong);font-size:10px}.commerce-options p{margin:0;color:var(--text-muted);font-size:9px;line-height:1.5}.readiness-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.readiness-grid article{padding:16px;background:var(--surface-raised);border:1px solid var(--border);border-radius:11px}.status{display:inline-flex;margin-bottom:12px;padding:5px 8px;border-radius:6px;font:9px var(--font-mono)}.status.ready{color:var(--success);background:rgba(50,182,122,.09)}.status.next{color:var(--warning);background:var(--warning-subtle)}@media(max-width:1150px){.app-console-grid{grid-template-columns:1fr}.release-list{min-height:auto}.installation-strip{grid-template-columns:1fr 1fr}.concept-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ownership-map{grid-template-columns:1fr}.contract-column{flex-direction:row;align-items:center}.contract-column div{flex:1}.contract-column>b{transform:rotate(90deg)}}@media(max-width:760px){.section-heading{align-items:start;flex-direction:column}.section-heading>p{text-align:left}.manifest-editor footer{justify-content:flex-start;flex-wrap:wrap}.installation-strip,.concept-grid,.flows-grid,.identity-commerce,.readiness-grid{grid-template-columns:1fr}.contract-column{flex-direction:column}.contract-column>b{transform:none}.app-heading{align-items:flex-start}.product-badge{width:100%}}
.market-control{padding:22px}.market-summary{color:var(--text-muted);font:9px var(--font-mono)}.market-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.market-column{display:grid;align-content:start;gap:9px;padding:14px;background:var(--surface);border:1px solid var(--border);border-radius:11px}.market-column>header{display:flex;justify-content:space-between}.market-column>header strong{color:var(--text-strong);font-size:11px}.market-column>header small{color:var(--text-muted);font:8px var(--font-mono)}.market-card{display:grid;gap:5px;padding:11px;background:var(--surface-raised);border:1px solid var(--border);border-radius:9px}.market-card>div{display:flex;align-items:center;gap:7px}.market-card strong{font-size:10px}.market-card small{color:var(--text-muted);font:8px var(--font-mono)}.market-form,.acquire-form{display:grid;gap:9px;padding:12px;background:var(--surface-raised);border:1px dashed var(--accent-border);border-radius:9px}.market-form summary{color:var(--accent);font-size:10px;cursor:pointer}.market-form label,.acquire-form label{display:grid;gap:5px;color:var(--text-muted);font-size:8px}.market-form input,.market-form textarea,.acquire-form input,.acquire-form select{width:100%;padding:8px;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px;font:9px var(--font-mono)}.market-form textarea{resize:vertical}.update-config{display:grid;grid-template-columns:1.2fr 1fr 1fr .7fr 1fr auto;gap:9px;align-items:end;margin-top:14px;padding:13px;background:var(--surface);border:1px dashed var(--accent-border);border-radius:10px}.update-config>header{display:flex;grid-column:1/-1;align-items:center;justify-content:space-between}.update-config>header div{display:grid;gap:3px}.update-config>header strong{font-size:10px}.update-config>header small{color:var(--text-muted);font:8px var(--font-mono)}.update-config>header button{color:var(--text-muted);background:none;border:0;font-size:9px}.update-config label{display:grid;gap:5px;color:var(--text-muted);font-size:8px}.update-config input,.update-config select{width:100%;padding:8px;color:var(--text);background:var(--surface-raised);border:1px solid var(--border);border-radius:6px;font:9px var(--font-mono)}.update-subscriptions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-top:10px}.update-subscriptions article{display:grid;gap:5px;padding:11px;background:var(--surface-raised);border:1px solid var(--border);border-radius:9px}.update-subscriptions header{display:flex;justify-content:space-between}.update-subscriptions strong{font-size:10px}.update-subscriptions span,.update-subscriptions small{color:var(--text-muted);font:8px var(--font-mono)}.update-subscriptions p{margin:0;color:var(--text-muted);font-size:9px}.acquisition-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}.acquisition-list>article{display:grid;gap:9px;padding:14px;background:var(--surface-raised);border:1px solid var(--border);border-radius:10px}.acquisition-list header{display:grid;gap:5px}.acquisition-list header>div{display:flex;align-items:center;gap:8px}.acquisition-list header small,.acquisition-list code{color:var(--text-muted);font:8px var(--font-mono)}.acquisition-list p{margin:0}.acquisition-state{padding:4px 6px;color:var(--text-muted);background:var(--surface-muted);border-radius:5px;font:7px var(--font-mono);text-transform:uppercase}.acquisition-state.awaiting_acceptance,.acquisition-state.imported{color:var(--success);background:rgba(50,182,122,.09)}.acquisition-state.quarantined,.acquisition-state.failed{color:var(--danger);background:var(--danger-subtle)}.trust-line{color:var(--success);font-size:9px;line-height:1.6}.market-error{color:var(--danger);font-size:9px}.acquisition-list pre{max-height:180px;overflow:auto;padding:9px;color:var(--text-muted);background:var(--surface);border-radius:6px;font:8px/1.5 var(--font-mono)}.acquisition-list nav{display:flex;gap:7px}@media(max-width:1100px){.update-config{grid-template-columns:1fr 1fr 1fr}.update-config>.primary-button{width:100%}}@media(max-width:900px){.market-grid,.acquisition-list,.update-subscriptions,.update-config{grid-template-columns:1fr}}
.governance{padding:22px}.secret-once{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;margin-bottom:14px;padding:12px;color:var(--warning);background:var(--warning-subtle);border:1px solid var(--warning);border-radius:9px}.secret-once code{overflow:auto;color:var(--text-strong);font:10px var(--font-mono)}.secret-once button,.governance-row button,.delivery-row button{padding:5px 7px;color:var(--text-muted);background:var(--surface);border:1px solid var(--border);border-radius:5px;font-size:8px}.usage-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:12px}.usage-grid article{display:grid;gap:6px;padding:13px;background:var(--surface-raised);border:1px solid var(--border);border-radius:9px}.usage-grid span{color:var(--text-muted);font:8px var(--font-mono)}.usage-grid strong{font-size:18px}.governance-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.governance-card{display:grid;align-content:start;gap:8px;padding:13px;background:var(--surface);border:1px solid var(--border);border-radius:10px}.governance-card>header,.delivery-diagnostics>header{display:flex;justify-content:space-between;gap:10px}.governance-card>header div,.delivery-diagnostics>header>div:first-child{display:grid;gap:3px}.governance-card>header strong,.delivery-diagnostics strong{font-size:11px}.governance-card>header small,.delivery-diagnostics small{color:var(--text-muted);font:8px var(--font-mono)}.governance-card>header>span{font:8px var(--font-mono)}.governance-row{display:flex;justify-content:space-between;gap:8px;padding:9px;background:var(--surface-raised);border-radius:7px}.governance-row p{display:grid;min-width:0;gap:3px;margin:0}.governance-row p strong{overflow:hidden;font-size:9px;text-overflow:ellipsis}.governance-row p small{overflow:hidden;color:var(--text-muted);font:7px var(--font-mono);text-overflow:ellipsis}.governance-row nav{display:flex;gap:4px;align-items:start}.governance-row button.danger{color:var(--danger)}.compact-form{display:grid;gap:7px;margin-top:4px;padding-top:10px;border-top:1px solid var(--border)}.compact-form label{display:grid;gap:4px;color:var(--text-muted);font-size:8px}.compact-form input,.compact-form select,.delivery-diagnostics input{width:100%;padding:7px;color:var(--text);background:var(--surface-raised);border:1px solid var(--border);border-radius:6px;font:8px var(--font-mono)}.delivery-diagnostics{display:grid;gap:7px;margin-top:12px;padding:13px;background:var(--surface);border:1px solid var(--border);border-radius:10px}.delivery-diagnostics>header>div:last-child{display:flex;gap:6px}.delivery-row{display:grid;grid-template-columns:1fr auto auto auto;gap:8px;align-items:center;padding:8px;background:var(--surface-raised);border-radius:7px}.delivery-row code{overflow:hidden;color:var(--text-muted);font:8px var(--font-mono);text-overflow:ellipsis}.delivery-status{padding:3px 5px;border-radius:4px;font:7px var(--font-mono)}.delivery-status.sent{color:var(--success);background:rgba(50,182,122,.09)}.delivery-status.dead{color:var(--danger);background:var(--danger-subtle)}@media(max-width:1100px){.governance-grid{grid-template-columns:1fr}.usage-grid{grid-template-columns:1fr 1fr}}@media(max-width:700px){.usage-grid{grid-template-columns:1fr}.delivery-diagnostics>header{flex-direction:column}.delivery-row{grid-template-columns:1fr auto}.delivery-row small{grid-column:1/-1}}
</style>
  ensureMarketInstallationKey,
