# App Market 治理与跨实例分发协议

状态：协议 v1 已冻结；参考实现为 Alpha（2026-08-10）
适用范围：JoyhouseBot Runtime、Joyhouse/Console、Market Registry、App 作者、购买者与自部署实例

## 1. 定位与非目标

App Market 是 JoyhouseBot 生态的**解决方案发现、交易与分发控制面**。它分发的是可验证、可安装、可更新、
可撤销的 App 解决方案，而不是把市场自身变成第二个 Agent Runtime。

```text
作者工作区                     Remote Market                    用户实例
App Pack / Release ──签名──▶ Catalog / Registry ──验证下载──▶ Acquisition
                                  │                              │
                         Offer / Order / Entitlement             ▼
                         Review / Usage / Settlement       local App Pack
                                                                │
                                                                ▼
                                                     Runtime Run / Task / Audit
```

本协议采用以下强制边界：

- Market **不得**执行用户 Run、读取个人 Memory/Knowledge、接收 Prompt、Artifact 或业务数据库内容；
- Market 负责目录、制品、作者身份、商业授权和市场治理，PostgreSQL Runtime 仍是本地执行事实源；
- App Pack 是 Runtime 侧可安装组合；独立 App 的业务服务、用户系统、订单和数据库仍由 App 自己拥有；
- Extension 是部署级技术制品。Market 可以分发它，但不能通过一次普通用户安装自动把 Python 代码加载进
  API/Worker；
- 购买、下载、安装、授权、Capability 权限和启用是彼此独立的动作；
- App Market 是可替换、可自建、可并存的远程 Registry，不要求用户依赖唯一官方服务；
- 离线导入和本地 Registry 必须是一等路径。没有 Market 账号时，开源 App 仍可按签名包安装；
- Runtime 不保存银行卡信息，不成为支付处理方、税务系统或创作者会计事实源。

本文中的 **MUST / MUST NOT / SHOULD / MAY** 分别表示必须、禁止、建议和可选要求。

## 2. 概念与权责

### 2.1 核心对象

| 对象 | 事实源 | 用途 |
| --- | --- | --- |
| `MarketRegistry` | Market | 协议发现、目录、制品索引、信任根和策略版本 |
| `PublisherIdentity` | Market + 作者签名 | 稳定作者身份、验证级别、签名密钥和命名空间 |
| `AppListing` | Market | 可变展示页、分类、说明、支持政策和当前上架状态 |
| `AppRelease` | 作者 | 不可变版本、App Pack 清单、组件、依赖和摘要 |
| `MarketAttestation` | Market | 上架审核、扫描、兼容性和治理决定，不替代作者签名 |
| `Offer` | Market/作者 | 价格、计费模式、地区、试用、退款和商业条款 |
| `Entitlement` | Market 或授权发行方 | 对某个安装主体签发的机器可验证使用授权 |
| `Acquisition` | 用户实例 | 解析、下载、验证、暂存和导入过程，不是执行状态机 |
| `Installation` | 用户实例 PostgreSQL | 已安装版本、依赖锁、配置、启停、升级和回滚 |
| `UpdateSubscription` | 用户实例 | 更新频道、版本约束、自动化策略和上次已知安全元数据 |
| `Review` | Market | 有资格约束、保留历史的用户评价与作者回复 |
| `UsageReceipt` | 用户实例 | 对明确计费 Meter 的最小化、签名、幂等用量回执 |
| `SettlementStatement` | Market | 销售、退款、费用、税和创作者应结金额的签名报表 |
| `Advisory/Revocation` | 作者或 Market | 漏洞、撤回、密钥泄露、恶意制品和处置建议 |

### 2.2 三重信任，不做传递授权

每次安装必须分别验证三件事：

1. **作者签名**：证明某个不可变 Release 确实由指定作者密钥发布；
2. **Market 证明**：证明该 Release 在某个 Market 中的上架、审核、商业和撤销状态；
3. **本地批准**：由用户或实例管理员决定是否接受依赖、权限、成本、数据范围和可执行制品。

任何一层都不能替代另外两层。作者签名不代表安全，Market 上架不代表获得本地权限，购买成功也不代表
自动启用。Market 不得签发 JoyhouseBot Capability 权限；本地安装程序不得因为作者被认证就跳过依赖校验。

## 3. Registry 信任与作者身份

### 3.1 Registry 身份

一个 Market 的稳定身份是规范化 HTTPS Origin，例如 `https://market.joyhouse.example`。用户实例通过
显式添加 Registry 或随发行版预置的信任根建立信任，不采用“网络搜索到即可信任”。

Registry MUST 提供：

```http
GET /.well-known/joyhouse-market
```

最小响应：

```json
{
  "market_id": "https://market.joyhouse.example",
  "protocol_versions": ["1.0"],
  "api_base": "https://market.joyhouse.example/api/market/v1",
  "oci_registry": "registry.joyhouse.example",
  "tuf_metadata_base": "https://market.joyhouse.example/tuf",
  "auth_issuer": "https://accounts.joyhouse.example",
  "features": ["catalog", "entitlements", "updates", "reviews", "metering"],
  "privacy_policy": {"uri": "https://market.joyhouse.example/privacy", "sha256": "sha256:..."}
}
```

发现文档只提供端点，不能自行替换已固定的信任根。Registry 的可信更新元数据 MUST 使用 TUF 的
`root / targets / snapshot / timestamp` 角色、过期时间和阈值签名，以抵御回滚、冻结、混搭和单个在线
密钥泄露。每个 Registry 是独立信任根；多个 Registry 之间不得自动合并信任。

### 3.2 作者身份

`PublisherIdentity` 至少包含：

```json
{
  "publisher_id": "pub_01K...",
  "namespace": "joyhouselabs",
  "display_name": "JoyHouse Labs",
  "verification": {
    "level": "domain_verified",
    "verified_domains": ["joyhouse.example"],
    "checked_at": "2026-08-10T00:00:00Z"
  },
  "keys": [{
    "key_id": "ed25519:sha256:...",
    "algorithm": "ed25519",
    "public_key": "base64url:...",
    "valid_from": "2026-08-10T00:00:00Z",
    "status": "active"
  }]
}
```

- `display_name` 不是身份，`publisher_id + namespace` 才是稳定标识；
- 验证等级固定为 `self_asserted | domain_verified | organization_verified | regulated_verified`；
- Market MUST 明示每个等级验证了什么，禁止用模糊“官方/安全”徽章替代事实；
- 作者私钥不得上传 Market。v1 Release 的最低签名算法是 Ed25519；
- 正常轮换应由旧密钥和新密钥共同证明，并由 Market 写入新身份版本；旧密钥丢失时必须走人工恢复、等待期
  和高风险审计；
- 密钥泄露必须发布 `PublisherKeyRevocation`，列出影响时间窗和 Release。撤销密钥不等于自动认定此前
  所有 Release 恶意，Market 必须逐个给出处置状态；
- 命名空间转让需要转出方、接收方和 Market 三方证明，并保留历史，不允许静默改名冒充原作者。

一个 App 的全局来源身份是：

```text
market_id + publisher_id + app_id
```

同名 App 在不同 Market 或不同作者名下不是同一对象。镜像必须保存原始来源和签名，不能重新包装后继承
原评价、下载量或认证徽章。

## 4. Release 制品与签名协议

### 4.1 分发单元

Market 的顶层商品是 `AppListing`，其 Release 可以组合：

- 一个 `runtime_app_pack`：声明式 Agent、Team、Skill、Workflow、Scenario、Capability 和 Integration；
- 零个或多个精确 `extension_release` 引用：部署管理员另行安装并等待 Worker build ACK；
- 一个 `external_app_binding`：独立 App 的入口、OAuth/Remote Capability 契约和可选部署说明；
- 文档、图标、Eval、SBOM、来源证明和许可证文本。

Market 安装 `external_app_binding` 只创建本地 Integration/App Pack 草稿，不部署远程 App 服务，也不取得
该 App 的用户数据库权限。Market 不能把 Extension 伪装成低风险 Skill，不能把任意安装脚本放进 App Pack。

### 4.2 不可变 Release Descriptor

Release Descriptor 使用媒体类型
`application/vnd.joyhouse.app.release.v1+json`，至少包含：

```json
{
  "schema_version": "1.0",
  "source": {
    "market_id": "https://market.joyhouse.example",
    "publisher_id": "pub_01K...",
    "app_id": "app.market-radar"
  },
  "version": "1.4.0",
  "released_at": "2026-08-10T00:00:00Z",
  "app_manifest": {
    "media_type": "application/vnd.joyhouse.app.manifest.v1+json",
    "digest": "sha256:...",
    "size": 4812
  },
  "components": [{
    "kind": "skill",
    "logical_id": "skill.market-analysis",
    "version": "1.2.0",
    "media_type": "application/vnd.joyhouse.skill.v1+json",
    "digest": "sha256:...",
    "size": 9280
  }],
  "compatibility": {
    "core": {"min_version": "0.2.0", "max_version": ""},
    "platforms": ["any"],
    "architectures": ["any"]
  },
  "licenses": {
    "code_expression": "Apache-2.0",
    "content_expression": "CC-BY-4.0",
    "commercial_terms": {"uri": "https://market.example/terms/42", "sha256": "sha256:..."}
  },
  "evidence": {
    "sbom_digest": "sha256:...",
    "provenance_digest": "sha256:...",
    "eval_report_digest": "sha256:..."
  }
}
```

版本号只是人类选择依据，摘要才是机器身份。发布后 Descriptor 及其所有 Blob MUST 不可变；修复任何字节
都必须产生新版本和新摘要。金额、计数和时间戳等需要精确表达的值使用字符串，避免跨语言浮点差异。

开源许可证 SHOULD 使用 SPDX License Expression；自定义或商业条款必须提供不可变文本摘要。许可证元数据
只传达事实，不由 Runtime 判断法律合规。

### 4.3 内容传输与供应链证据

- Blob 和 Manifest SHOULD 通过 OCI Distribution 1.1 兼容 Registry 按 digest 分发；
- 自定义 OCI Artifact Type 必须保留上述 Joyhouse 媒体类型，不伪装成容器镜像；
- TUF Targets 元数据绑定 Descriptor 路径、digest、size、版本和作者委托；
- 作者使用 DSSE Envelope 对**收到的确切 Descriptor 字节**签名，`payloadType` 必须是 Release 媒体类型，
  防止跨类型替换；
- Market 使用独立 `MarketAttestation` 证明扫描、审核和上架结果，不重签作者身份；
- 可选 Sigstore Bundle 可以附加证书和透明日志证明，但不能替代 Registry 信任根或本地权限批准；
- 含可执行代码的 Extension MUST 带 SBOM 和构建来源证明；纯声明式 App Pack SHOULD 带组件清单和 Eval
  报告；
- 解析器必须限制 Manifest/Blob 数量、单项大小、总解压大小和嵌套深度，并拒绝路径穿越、符号链接逃逸、
  重复字段和摘要不符。

本地历史 App Manifest v1 的 `manifest_sha256` 使用 Python 确定性 JSON 序列化，只用于本实例兼容。
跨实例分发强制使用 App Manifest v2、RFC 8785 JCS + SHA-256；v1 不得打入 `.joyhouse-app` Bundle。

## 5. Remote Market API

### 5.1 通用约束

- 所有端点只允许 HTTPS；写请求使用 OAuth/OIDC access token 和最小 scope；
- Console 交互登录 SHOULD 使用 Authorization Code + PKCE；无浏览器实例可使用设备授权或一次性领取码；
- 每个写请求 MUST 携带 `Idempotency-Key`。相同键、相同请求摘要返回原结果；相同键、不同请求返回 409；
- 响应携带 `Market-Protocol-Version`、`Request-ID` 和明确错误码；
- 分页使用不透明 cursor，时间使用 UTC RFC 3339，金额使用 ISO 4217 currency + 十进制定点字符串；
- Access Token、Entitlement、安装密钥和支付凭据不得写入普通日志；
- Search/Catalog 响应是展示数据，不能作为安装信任依据。安装只接受 TUF + digest + 签名验证后的对象。

建议 scope 至少拆成 `catalog.read`、`releases.pull`、`checkout.write`、`entitlements.read`、
`reviews.write`、`usage.write`、`publisher.releases.write`、`publisher.advisories.write` 和
`publisher.statements.read`。购买者 Token 不能发布 Release，作者发布 Token 不能读取结算，Market 后台
Token 不能调用用户实例。

### 5.2 最小端点族

```text
GET  /apps                                  搜索和分类
GET  /apps/{publisher_namespace}/{app_id}   Listing
GET  /apps/{publisher_namespace}/{app_id}/releases
POST /resolutions                           解析版本、依赖、兼容性和授权要求

POST /checkout-sessions                     创建 Market 托管购买会话
GET  /checkout-sessions/{id}                轮询完成状态
POST /entitlements/resolve                  获取/刷新签名授权
GET  /entitlements/{id}/events              续费、退款、暂停和撤销事件

GET  /update-feeds/{subscription_id}        增量更新与安全通告
POST /reviews                               创建评价
POST /usage-receipts                        幂等上传计费用量
GET  /publishers/{publisher_id}/statements  创作者结算报表

POST /publishers/{publisher_id}/keys        登记或轮换作者公钥
POST /publisher-releases                    创建上传会话并固定预期 digest
POST /publisher-releases/{id}/complete      验证作者签名、SBOM 和上传 Blob
POST /publisher-releases/{id}/submit        提交上架审核，不直接激活
POST /publisher-advisories                  发布漏洞、撤回或密钥影响说明
```

作者上传使用预签名 Blob URL 或 OCI push token，Token 只允许写入本次上传会话声明的 repository 和 digest。
Market 完成上传只生成不可变候选 Release；审核通过后由独立 Market targets/attestation 流程上架，不能由
作者发布 Token 直接把候选切成可信目标。

`POST /resolutions` 不返回“latest”字符串，而返回短期有效、由 Market 用 DSSE Envelope 签名的精确
`ResolutionLock`。下面是 Envelope 内的 Payload，不在 Payload 内嵌自己的签名：

```json
{
  "resolution_id": "resolve_01K...",
  "expires_at": "2026-08-10T01:00:00Z",
  "tuf_snapshot_version": 284,
  "root": {
    "release_digest": "sha256:...",
    "version": "1.4.0"
  },
  "dependencies": [{
    "kind": "extension",
    "id": "capability-research",
    "version": "0.4.2",
    "digest": "sha256:...",
    "required": true
  }],
  "entitlement": {"required": true, "offer_id": "offer_pro_monthly"},
  "policy_digest": "sha256:..."
}
```

用户实例保存该锁用于复现安装。锁过期后可以继续完成已下载且验证通过的本地导入，但不得用它解析新的
Blob 或悄悄切换版本。

## 6. 跨实例下载、导入与安装

### 6.1 安装主体

每个用户实例为每个 Market 生成独立 Ed25519 `installation_key`。Market 只看到公钥指纹和账号授权，
不得要求上传本地 `user_id`、Agent ID、Run ID 或机器序列号。不同 Market 使用不同密钥，防止跨站关联。

重装/迁移通过 Market 账号确认后把 Entitlement 转移到新安装公钥；转移事件可审计，旧公钥进入短暂重叠期
后失效。Runtime 不使用不可恢复的硬件指纹锁死用户资产。

### 6.2 Acquisition 与本地 Installation 分离

跨实例传输使用 `Acquisition` 状态，不创建第二套 Run/Task 状态机：

```text
requested -> resolving -> fetching -> verifying -> staged -> awaiting_acceptance -> imported
                                 \-> rejected
                                 \-> quarantined
```

`imported` 只代表受验证制品已经进入本地 App Pack/资产草稿。之后仍走现有本地状态机：

```text
draft -> dependency validation -> published
published release -> installed -> active / disabled -> upgrade / rollback / uninstall
```

所有 Acquisition 和 Installation 事件写 PostgreSQL，包含 actor、来源 Market、release digest、TUF snapshot、
签名结果、策略版本、权限差异和失败原因，但不保存 Market access token 明文。

### 6.3 安装事务

1. 用户选择 App、版本范围和更新频道；本地把选择解析并持久化为精确版本后再提交；
2. Market 校验 Entitlement 与设备安装公钥、精确 Release、Bundle digest 和已确认权限摘要，返回
   短期有效的 DSSE `InstallationGrant`，其中绑定稳定 `operation_id + installation_id + intent_revision`；
3. Runtime 获取并固定 Registry root/TUF 元数据，验证时间、版本和阈值签名；
4. Market 返回精确 `ResolutionLock`；
5. Runtime 检查 Entitlement，按 digest 下载到内容寻址暂存区；
6. 依次验证 size、digest、作者 DSSE 签名、Market Attestation、SBOM/来源和撤销状态；
7. 在不执行制品代码的情况下解析 App Manifest，生成依赖、权限、Secret、外部域名、数据分级、成本和
   迁移差异；
8. 用户或管理员批准安装。新的高风险权限、Extension、外部写入、付费 Meter 和数据出站必须逐项显示；
9. 声明式 Agent/Skill/Workflow/Scenario 导入 Draft，保存 `origin_ref + content_digest`；冲突项使用来源
   命名空间，不能静默覆盖本地资产；
10. Extension 只进入“待部署”清单，由部署管理员按独立扩展协议安装，Worker 精确 build ACK 后才满足依赖；
11. 本地发布验证通过后创建 Installation；只有用户再次启用才切换 active；
12. Runtime 用稳定 `receipt_id` 回写本机状态、安装 fingerprint 和已接受权限；回执必须绑定当前 revision，
    相同内容可重放、不同内容冲突，旧 revision 不能覆盖新意图；
13. 任一步失败都删除或隔离暂存 Blob，不覆盖当前 active 版本，不删除历史 Run、Artifact、Work 或配置。

Runtime 的用户侧 Market 安装入口是
`POST /v1/apps/market/acquisitions/{acquisition_id}/install`。它只接受当前认证 `user_id` 已验证并确认的
Acquisition；委托 App Token 不得调用。管理员控制面保留同语义的审计/运维入口，但普通用户不需要获得
`apps.install` 平台权限。安装用例
在安装前校验 Registry 固定的 `installation` 合约公钥、Grant 过期时间、Market/Release/Bundle/
Entitlement/权限摘要和目标状态；通过后才发布导入的 App Pack 并进入统一 Installation 状态机。Grant 消费按
`registry_id + user_id + grant_id` 持久幂等，不允许把普通本地安装接口伪装成 Market 安装完成。

设备安装私钥始终由 Runtime 持有。产品端通过
`POST /v1/apps/market/registries/{registry_id}/installation-receipts/sign` 请求签名时，只能提交 Cloud 身份、
revision 和目标状态；版本、Bundle digest、权限范围与 fingerprint 必须由 Runtime 从当前用户真实
`Installation` 重新读取并冻结。相同 `receipt_id` 仅允许完全相同的请求重放，不能由 Desktop、Product 或
独立 App 自报安装结果、导出私钥或覆盖历史签名。

普通用户安装 MUST NOT：

- 执行任意 `post_install` Shell/Python；
- 写入 `config.json`、环境变量或宿主文件系统；
- 自动接受 OAuth、Secret、Capability allowlist 或数据出站；
- 自动安装未被部署 `allowedIds` 准入的 Extension；
- 读取发布者或上一位安装者的私有上下文；
- 把原始 Artifact URL 当作公共 Market 预览或分享链接。

## 7. 购买、许可证与授权

### 7.1 法律许可证与机器授权分离

- `License` 描述代码、内容和数据可以如何使用；
- `Offer` 描述价格、周期、退款、税和计费 Meter；
- `Entitlement` 是 Market 签发给安装主体的机器可验证授权。

开源许可证不因 Market 分发而改变。付费服务条款不能覆盖包内第三方开源许可证。商业条款更新不能
追溯修改已购买的不可变条款摘要；续费采用新条款时必须在续费前明确提示。

### 7.2 托管购买流程

1. 实例用 `offer_id + installation_key_thumbprint + Idempotency-Key` 创建 Checkout Session；
2. 用户在 Market 托管页面登录并支付；JoyhouseBot 不接触卡号或支付密码；
3. 实例轮询 Session 或兑换一次性短期授权码，不要求公网回调；
4. Market 返回签名 Entitlement；实例验证发行方、签名、受众、App、版本范围、功能、期限和公钥绑定；
5. 退款、拒付、续费和撤销通过签名事件 feed 对账，不依赖一次可能丢失的 Webhook。

购买成功仅解除商业下载门槛，不自动安装、启用或授予权限。

### 7.3 Entitlement 最小结构

```json
{
  "schema_version": "1.0",
  "entitlement_id": "ent_01K...",
  "issuer": "https://market.joyhouse.example",
  "subject": {"installation_key_thumbprint": "sha256:..."},
  "app": {
    "publisher_id": "pub_01K...",
    "app_id": "app.market-radar",
    "version_constraint": ">=1.4.0 <2.0.0"
  },
  "offer_id": "offer_pro_monthly",
  "features": ["weekly-radar", "export-pdf"],
  "limits": {"local_principals": "1", "included_runs_month": "200"},
  "not_before": "2026-08-10T00:00:00Z",
  "expires_at": "2026-09-10T00:00:00Z",
  "offline_until": "2026-09-17T00:00:00Z",
  "terms_digest": "sha256:...",
  "revocation_feed": "https://market.example/api/market/v1/entitlements/ent_01K/events"
}
```

Entitlement 使用 Market 授权密钥签名并有独立轮换策略。过期或商业撤销不得删除本地产物、历史和用户配置；
实例应停止新的付费能力或更新，并提供导出、续费和申诉入口。安全撤销与商业授权撤销必须使用不同事件
类型，禁止用“退款”触发删除或安全隔离。

## 8. 更新订阅与安全回滚

Update Subscription 至少保存：

```json
{
  "channel": "stable",
  "version_constraint": ">=1.4.0 <2.0.0",
  "policy": "notify",
  "allow_security_patch_download": true,
  "allow_auto_stage": true,
  "allow_auto_activate": false
}
```

频道固定为 `stable | beta | security`，频道只是 TUF Targets 的签名指针，最终仍解析为精确 digest。
允许的策略是 `notify | download | stage | activate_safe`：

- `notify` 只提示；`download` 只缓存；`stage` 完成验证和差异分析；
- `activate_safe` 只适用于同一 major、无新增权限/Secret/外部域名/付费 Meter、无新 Extension、无破坏性
  数据迁移，且 Eval 和 Worker ACK 全部通过的版本；
- major 升级、许可证变化、权限扩大、可执行制品变化和商业条款变化 MUST 人工确认；
- 更新失败保留前一 active 版本；回滚使用历史精确 digest，不重新解析“上一版”；
- Registry 元数据过期、时间异常、版本回退或 snapshot 混搭时 fail closed，并产生安全事件。

版本撤销分级：

| 状态 | 新下载/安装 | 已安装版本 | 典型原因 |
| --- | --- | --- | --- |
| `delisted` | 不再展示，可按已知 digest 获取 | 不影响 | 作者停止销售 |
| `suspended` | 暂停新安装 | 提示检查 | 审核或争议中 |
| `security_advisory` | 允许但强警告/要求升级 | 按本地策略 | 已知漏洞 |
| `release_revoked` | 拒绝新激活 | 隔离或回滚 | 恶意/不可接受制品 |
| `publisher_key_revoked` | 拒绝受影响签名 | 逐 Release 判定 | 密钥泄露 |

Market 可以要求本地停止**新的激活**，不能远程删除数据。是否暂停当前 Run 由本地信任策略决定；官方策略
对已确认恶意制品可默认隔离新的执行，但必须保留导出、证据、审计和已知安全版本回滚能力。作者和用户应
有申诉渠道，紧急处置可以先执行后复核，但不能抹除决定历史。

## 9. 评价、评分和使用统计

### 9.1 评价资格与历史

Market 的 `verified_purchase`、`verified_install`、`unverified` 必须明确区分。付费 Entitlement 可以直接
证明购买；安装证明只包含 `release_digest + installation_key` 对挑战的签名，不包含本地用户、Run、Prompt
或成果。

- 一个 Market 账号对一个 App 只能有一条当前评价，修改产生不可变 revision 历史；
- 评分尺度、汇总窗口、加权方式、最小样本和是否包含退款用户必须公开；
- 作者回复与评价分开保存，不得由作者删除负面评价；
- Market 可因垃圾、利益冲突、违法内容或隐私泄露隐藏内容，但必须记录原因码、策略摘要、处理者和申诉；
- 赠送、返现或关联方评价必须标注；删除 Listing 不得复用原评分给不同来源 App；
- 评价 API 不接受 Prompt、Artifact、密钥、客户信息或未脱敏截图。

### 9.2 三类使用数据

| 数据 | 默认 | 去向 | 规则 |
| --- | --- | --- | --- |
| Runtime 运维数据 | 本地私有 | 本地 PostgreSQL/监控 | 不上传 Market |
| 计费 Meter | Offer 明确声明后启用 | Market Billing | 最小化、可审计、签名回执 |
| 产品分析 | 关闭 | Market/作者 | 单独 opt-in，可撤回，不影响已购功能 |

Market 展示的安装数、活跃数、留存和成功率必须标注统计周期、样本口径和是否为估算。作者不得通过
App Pack 自行埋点绕开许可；所有远程遥测域名和字段必须出现在清单及安装差异中。

### 9.3 Usage Receipt

只有 Offer 声明的 Meter 才可产生回执：

```json
{
  "receipt_id": "usage_01K...",
  "entitlement_id": "ent_01K...",
  "installation_key_thumbprint": "sha256:...",
  "meter_id": "market_report.generated",
  "period": {"start": "2026-08-10T00:00:00Z", "end": "2026-08-11T00:00:00Z"},
  "quantity": "3",
  "unit": "report",
  "sequence": "41",
  "source_event_digest": "sha256:...",
  "created_at": "2026-08-11T00:00:01Z"
}
```

- 回执由安装密钥签名，`receipt_id + sequence` 幂等；Market 检测缺口、重复和倒序；
- `source_event_digest` 指向本地审计投影，不暴露原事件；用户可在 Console 对账每一笔 Meter；
- 禁止把 Token 文本、Prompt、模型响应、文件名、客户标识、URL 或地理位置放入回执；
- 离线期间可累计，恢复后批量提交；超过 `offline_until` 时按 Offer 明示策略暂停计费功能，不能伪造用量；
- 计量争议必须冻结相关结算行，保留双方原始签名和协议版本。

## 10. 创作者结算

Market 是订单、退款、税、平台费和付款的商业事实源；Runtime 只提供经用户授权的 Usage Receipt。
创作者结算使用不可变 ledger event 计算，每个周期生成签名 `SettlementStatement`：

```json
{
  "statement_id": "stmt_2026_08_pub_01K",
  "publisher_id": "pub_01K...",
  "period": {"start": "2026-08-01", "end": "2026-09-01"},
  "currency": "USD",
  "lines": [{
    "event_id": "ledger_01K...",
    "event_type": "sale",
    "order_ref": "order_opaque_01K...",
    "app_id": "app.market-radar",
    "gross": "20.00",
    "tax": "0.00",
    "market_fee": "3.00",
    "adjustment": "0.00",
    "publisher_net": "17.00"
  }],
  "totals": {"gross": "20.00", "publisher_net": "17.00"},
  "policy_digest": "sha256:...",
  "generated_at": "2026-09-02T00:00:00Z"
}
```

- 每个 `event_id` 唯一；销售、退款、拒付、税、手续费、调整和 payout 分行，不覆盖历史行；
- 金额使用定点十进制字符串并按币种分别结算，禁止跨币种直接求和；
- Statement 由 Market settlement key 签名，提供 CSV/JSON 导出、证据引用、争议期限和状态；
- 买方身份以不透明引用表示，作者不能从结算报表取得用户个人数据；
- 平台费率、退款准备金、最低付款额、付款周期和税务责任必须在购买前版本化并显示；
- 费率变更只影响声明生效时间后的交易，不重写历史 Statement；
- KYC、税票和实际打款由 Market/支付服务处理，不进入 JoyhouseBot Core。

## 11. 治理、审核与申诉

### 11.1 角色分离

| 角色 | 可以做 | 不能单独做 |
| --- | --- | --- |
| 作者 | 签名 Release、定价建议、响应通告 | 给本地实例授予 Capability 权限 |
| Market 目录运营 | 上下架、分类、内容审核 | 修改作者已签名 Release |
| Market 安全响应 | 发布通告、紧急暂停和撤销建议 | 删除用户本地数据 |
| Market 商业运营 | Offer、订单、退款、结算 | 把商业撤销伪装成安全撤销 |
| 本地用户/管理员 | 信任 Registry、批准安装和权限、回滚 | 伪造作者或 Market 签名 |
| JoyhouseBot Core | 验证协议、执行本地策略和审计 | 决定某个具体 App 值得购买 |

安全撤销、作者身份恢复、命名空间转让和大额人工结算调整 SHOULD 使用双人复核；Market 运营账号不能
直接使用离线 TUF root key 或作者私钥。

### 11.2 决定记录

所有审核决定使用不可变 `GovernanceDecision`：

```json
{
  "decision_id": "decision_01K...",
  "subject": {"type": "app_release", "digest": "sha256:..."},
  "action": "suspend",
  "reason_code": "security_investigation",
  "policy": {"version": "2026-08", "digest": "sha256:..."},
  "evidence_refs": ["evidence_opaque_01K..."],
  "effective_at": "2026-08-10T00:00:00Z",
  "review_due_at": "2026-08-12T00:00:00Z",
  "appeal_status": "available"
}
```

证据可以受限访问，但决定、原因码、策略版本、时间、作用范围和复核状态必须对受影响方可见。申诉产生
新的决定，不能改写旧记录。

### 11.3 协议和政策变更

- 协议版本与运营政策版本分离。价格、审核政策变化不应迫使 Runtime 升级协议；
- `1.x` 只允许接收方可忽略的新增字段或可协商 feature；删除字段、改变签名输入、状态含义或安全默认值
  必须发布新 major；
- 客户端必须显式协商版本，不识别的必选 feature 或签名算法 fail closed；
- Registry 必须公告支持窗口、弃用日期、数据导出和迁移方法；安全紧急变更可以立即生效，但必须同步提供
  影响范围、临时措施和复核日期；
- 已签 Entitlement、Release、Statement 和条款摘要不可原地修改；更正通过新事件或 superseding 对象；
- 协议仓库应提供 JSON Schema、测试向量、错误码、参考签名样例和 Registry/Client conformance suite；
- 官方 Registry 只是一个实现。协议许可第三方 Registry、镜像和私有企业 Registry 通过同一套一致性测试。

## 12. 隐私与安全不变量

Market 默认只可获得：Market 账号、安装公钥指纹、购买/授权、选择的 App/版本、显式 Meter 回执和用户主动
提交的评价。下列内容默认禁止离开实例：

- `user_id`、Agent session、Prompt、模型响应和 reasoning Blob；
- Memory、Knowledge、业务输入、客户名单和 Connector 数据；
- Run/Event/Trace/Log 原文、Artifact/Work 内容和私有分享 URL；
- Secret、Provider key、Channel 凭据和 Remote Capability 签名密钥；
- 未在 Meter Manifest 中声明的成本、性能和质量数据。

必须满足的安全不变量：

1. Catalog 被攻破不能产生有效作者签名；
2. Blob 镜像被攻破不能绕过 digest、TUF 和 DSSE；
3. 作者密钥被攻破不能绕过 Market 撤销和本地权限批准；
4. Market 商业系统被攻破不能直接调用本地 Capability；
5. 安装包不能在验证/批准前执行代码；
6. 更新不能扩大权限、数据出站或成本而不重新确认；
7. 撤销不能删除个人数据、历史成果或审计证据；
8. 禁用遥测不影响本地开源 App 的基本执行；明确用量计费 Offer 除外；
9. Registry 离线时，已验证且仍在本地授权宽限内的版本可继续运行；
10. 所有跨实例写请求、用量、购买领取和安装动作都可幂等、可审计、可对账。

## 13. 与现有 JoyhouseBot 的映射

| 协议能力 | 当前状态 | 实施位置 |
| --- | --- | --- |
| 本地 App Pack manifest、发布、安装、启停、升级、回滚 | 已有 MVP | Core PostgreSQL 控制面 |
| 精确 Agent/Team/Skill/Scenario/Capability/Extension 引用 | 已有 MVP | App Pack + Registry 校验 |
| Extension desired state、Worker 精确 build ACK、失败保留旧版 | 已有 | Extension 控制面 |
| Remote Market Registry / TUF / OCI 拉取 | 已有 Alpha | Core `joyhousebot/market` + 独立 `joyhouse-market` + Acquisition Worker |
| 作者身份、DSSE 签名和双证明密钥轮换 | 已有 Alpha | Publisher CLI + Market Identity API |
| 可携带声明式资产的跨实例 App Bundle | 格式和验签已实现 | App Manifest v2 + `.joyhouse-app`；资产导入继续走各自发布门禁 |
| Entitlement、Checkout 和 Update Subscription | 已有 Alpha 状态机 | Scheduler 轮询签名 Feed，`notify/download/stage` 可用；`activate_safe` 在 Eval/ACK 门禁接入前 fail closed；真实支付需独立适配器 |
| 设备 Installation Grant、精确版本意图与幂等 Receipt | 已有 Alpha | JoyHouse Market `cloud_*` 协议；本机实际安装仍由 Runtime 状态机负责 |
| Release Gate 与聚合 Eval 证明 | 已有 Alpha | Market Attestation 绑定五项门禁、`release_acceptance` Eval 和精确 Bundle；不上传私人 Run 内容 |
| 评价、更新通告和统计口径 | 已有 Alpha | Market 服务；Runtime 不上传私有运行数据 |
| Usage Receipt、退款/拒付与结算 Statement | 已有 Alpha | 签名 Meter 回执 + append-only ledger；真实 payout 不在 Core |

Market 实现不得直接扩展现有 `/v1/admin/apps` 为支付/评价巨型路由。Core 只增加通用的 Registry 信任、
Acquisition、签名验证、Entitlement 缓存和本地安装投影；搜索、支付、评价、作者后台和结算属于独立 Market
服务及 Joyhouse 产品界面。

## 14. 实现范围、分阶段发布与门槛

独立 `joyhouse-market` 参考实现已经覆盖 P0-P3 的协议对象、PostgreSQL 状态机和端到端测试，但 **Alpha 不等于可以直接
经营真实市场**。生产启用付费前仍必须接入受监管支付 Provider、税务/KYC/payout、对象存储/CDN、离线
TUF root ceremony、双人高风险操作、申诉工单和隐私/法律评审。这些属于具体 Market 运营系统，不进入
JoyhouseBot Core，也不能用内置 `manual` Provider 冒充。

### P0：可移植签名包

- 冻结 App Manifest v2、跨语言 canonical digest 和 JSON Schema；
- Publisher CLI 生成 DSSE 签名、SBOM/Eval 引用和离线 `.joyhouse-app` bundle；
- Console 完成导入、差异、权限确认、来源展示和本地回滚；
- 不做购买、评价和遥测。

### P1：远程 Registry

- `/.well-known/joyhouse-market`、TUF repository、OCI artifact 和 Registry trust 管理；
- Catalog、ResolutionLock、Acquisition、更新通知、安全通告和多 Registry；
- 安装路径完成断网、过期元数据、回滚、镜像篡改和密钥轮换测试。

### P2：商业授权

- Hosted Checkout、安装公钥、离线 Entitlement、迁移/退款/续费对账；
- 商业授权失效不删除资产，安全撤销与商业撤销彻底分离；
- 支付和身份信息不进入 Runtime 日志或数据库普通字段。

### P3：生态治理

- 评价资格、统计口径、Usage Receipt、创作者 Statement、争议和申诉；
- 双人安全操作、透明决定记录、协议 conformance suite 和第三方 Registry 互操作；
- 只有明确按量计费的 App 启用 Meter，产品分析继续默认关闭。

任何阶段上线前至少验证：签名测试向量、TUF 回滚/冻结攻击、重复请求幂等、依赖混搭、恶意压缩包、权限
扩大、更新失败回滚、Entitlement 断网宽限、退款与安全撤销分离、个人数据零上传，以及当前 active Release
在新版本失败时保持可用。

## 15. 采用的开放标准

- [OCI Image/Distribution Specification 1.1](https://specs.opencontainers.org/distribution-spec/?v=v1.1.1)：
  内容寻址 Blob、Manifest 和 Registry 传输；
- [The Update Framework (TUF) 1.x](https://theupdateframework.github.io/specification/latest/)：Registry
  root、targets、snapshot、timestamp 和安全更新；
- [DSSE](https://github.com/secure-systems-lab/dsse/blob/master/protocol.md)：Release 类型绑定签名 Envelope；
- Ed25519 + SHA-256：v1 最低作者签名与摘要算法；
- [RFC 8785 JCS](https://www.rfc-editor.org/rfc/rfc8785.html)：下一版跨语言 JSON 摘要的建议
  canonicalization；
- [SPDX License Expression / SPDX SBOM](https://spdx.dev/use/specifications/)：许可证和软件物料清单；
- [Sigstore Bundle](https://docs.sigstore.dev/cosign/signing/signing_with_blobs/)：可选的证书与透明日志来源证明。

开放标准只解决传输、更新、签名或元数据问题；JoyhouseBot 的本地权限、审批、Worker ACK、Run/Task、
Artifact/Work 和个人数据边界仍由 Core 协议强制。
