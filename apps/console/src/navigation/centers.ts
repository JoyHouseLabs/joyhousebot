export type CenterId = 'overview' | 'runs' | 'build' | 'extensions' | 'settings'

export interface CenterNavItem {
  to: string
  label: string
  icon: string
}

export interface CenterModule {
  name: string
  description: string
  icon: string
  status: 'available' | 'connected' | 'planned'
  statusLabel: string
  to?: string
}

export interface ConsoleCenter {
  id: CenterId
  to: string
  label: string
  mobileLabel: string
  caption: string
  icon: string
  eyebrow: string
  title: string
  description: string
  boundary: string
  primaryAction: { to: string; label: string }
  navItems: CenterNavItem[]
  routePrefixes: string[]
  modules: CenterModule[]
  flow: string[]
}

export const consoleCenters: ConsoleCenter[] = [
  {
    id: 'overview',
    to: '/overview',
    label: '概览',
    mobileLabel: '概览',
    caption: '健康与容量',
    icon: 'O',
    eyebrow: 'RUNTIME OVERVIEW',
    title: 'Runtime 运行概览',
    description: '集中观察 API、PostgreSQL、Worker、Provider、Run 与调度器的健康和容量。',
    boundary: '这里只呈现 Runtime 控制面状态，不承载 JoyHouse 或其他 App 的用户工作台。',
    primaryAction: { to: '/overview', label: '查看运行状态' },
    navItems: [],
    routePrefixes: ['/overview'],
    modules: [
      { name: '服务健康', description: '检查 API、数据库、Worker 和模型提供方的就绪状态。', icon: 'H', status: 'available', statusLabel: '可用', to: '/overview' },
      { name: '执行容量', description: '观察 Run、Task、Schedule 的积压、吞吐和异常。', icon: 'C', status: 'available', statusLabel: '可用', to: '/overview' },
      { name: '执行回放', description: '从异常指标进入统一 Run 时间线排障。', icon: 'R', status: 'connected', statusLabel: '关联', to: '/runs' },
    ],
    flow: ['发现异常', '定位服务', '进入 Run', '审计回放', '验证恢复'],
  },
  {
    id: 'runs',
    to: '/runs',
    label: '执行',
    mobileLabel: '执行',
    caption: 'Run 与回放',
    icon: 'R',
    eyebrow: 'RUN OPERATIONS',
    title: '统一执行与审计时间线',
    description: '查询 Run、Task、Event、Trace、Artifact、审批和恢复过程。',
    boundary: 'Console 负责观察和治理执行；业务 App 通过公共 HTTP/SSE 提交目标并呈现用户体验。',
    primaryAction: { to: '/runs', label: '查询 Runs' },
    navItems: [],
    routePrefixes: ['/runs'],
    modules: [
      { name: 'Run 列表', description: '按状态、来源与时间定位执行。', icon: 'R', status: 'available', statusLabel: '可用', to: '/runs' },
      { name: 'Task 与事件', description: '沿单一事实链检查状态转换和恢复。', icon: 'T', status: 'available', statusLabel: '可用', to: '/runs' },
      { name: 'Trace 与 Artifact', description: '核对调用证据、产物和错误边界。', icon: 'A', status: 'available', statusLabel: '可用', to: '/runs' },
    ],
    flow: ['筛选 Run', '检查 Task', '核对事件', '查看证据', '处置异常'],
  },
  {
    id: 'build',
    to: '/build',
    label: '构建',
    mobileLabel: '构建',
    caption: '发布执行资产',
    icon: 'B',
    eyebrow: 'BUILD CONTROL PLANE',
    title: '构建并发布 Runtime 执行资产',
    description: '管理 App 契约、Agent、Team、Workflow、Scenario、Skill 和评测门禁。',
    boundary: '这里只定义可发布的执行资产与协议；产品页面、交易和业务数据库始终留在独立 App。',
    primaryAction: { to: '/apps', label: '管理 Apps' },
    navItems: [
      { to: '/apps', label: 'Apps', icon: 'P' },
      { to: '/agents', label: 'Agents', icon: 'A' },
      { to: '/teams/compose', label: 'Team Composer', icon: 'T' },
      { to: '/teams', label: 'Teams', icon: 'M' },
      { to: '/workflows', label: 'Workflows', icon: 'W' },
      { to: '/scenarios', label: 'Scenarios', icon: 'S' },
      { to: '/skills', label: 'Skills', icon: 'K' },
      { to: '/evaluations', label: 'Evaluations', icon: 'E' },
      { to: '/prompt-governance', label: 'Prompts', icon: 'P' },
    ],
    routePrefixes: ['/build', '/apps', '/agents', '/teams', '/workflows', '/scenarios', '/skills', '/evaluations', '/prompt-governance'],
    modules: [
      { name: 'App', description: '发布独立产品使用 Runtime 的版本化契约与 EntryPoint。', icon: 'P', status: 'available', statusLabel: '可用', to: '/apps' },
      { name: 'Agent 与 Team', description: '冻结模型、提示、能力准入和协作边界。', icon: 'A', status: 'available', statusLabel: '可用', to: '/agents' },
      { name: 'Workflow 与 Scenario', description: '定义可回放的编排和路由。', icon: 'W', status: 'available', statusLabel: '可用', to: '/workflows' },
      { name: 'Skill', description: '沉淀不直接获得执行权限的方法和模板。', icon: 'K', status: 'available', statusLabel: '可用', to: '/skills' },
      { name: '评测门禁', description: '以可审计证据控制版本发布和生效。', icon: 'E', status: 'available', statusLabel: '可用', to: '/evaluations' },
    ],
    flow: ['创建草稿', '校验依赖', '运行评测', '发布版本', '确认 Worker 加载'],
  },
  {
    id: 'extensions',
    to: '/extensions',
    label: '扩展',
    mobileLabel: '扩展',
    caption: '模型与连接',
    icon: 'X',
    eyebrow: 'EXTENSION OPERATIONS',
    title: '管理 Runtime 技术扩展',
    description: '统一管理模型、Channel、远程 Connector、Device Host 与原子 Capability。',
    boundary: 'Extension 是技术安装单元，不是面向用户售卖的业务 App，也不创建第二套执行状态机。',
    primaryAction: { to: '/extensions', label: '查看 Extensions' },
    navItems: [
      { to: '/extensions', label: 'Extensions', icon: 'X' },
      { to: '/models', label: 'Models', icon: 'M' },
      { to: '/connections', label: 'Remote Connectors', icon: 'C' },
      { to: '/devices', label: 'Device Hosts', icon: 'D' },
      { to: '/channels', label: 'Channels', icon: 'H' },
      { to: '/capabilities', label: 'Capabilities', icon: 'C' },
    ],
    routePrefixes: ['/extensions', '/models', '/connections', '/devices', '/channels', '/capabilities'],
    modules: [
      { name: 'Extension', description: '发现、准入、发布并观察独立扩展。', icon: 'X', status: 'available', statusLabel: '可用', to: '/extensions' },
      { name: 'Model', description: '管理 Provider、模型目录和密钥引用。', icon: 'M', status: 'available', statusLabel: '可用', to: '/models' },
      { name: 'Remote Connector', description: '以签名协议连接受保护的业务能力。', icon: 'C', status: 'available', statusLabel: '可用', to: '/connections' },
      { name: 'Capability', description: '维护统一 Dispatcher 的能力目录和准入策略。', icon: 'C', status: 'available', statusLabel: '可用', to: '/capabilities' },
    ],
    flow: ['安装扩展', '发现 Manifest', '能力准入', 'Worker 加载', '健康审计'],
  },
  {
    id: 'settings',
    to: '/settings',
    label: '设置',
    mobileLabel: '设置',
    caption: '平台与安全',
    icon: 'S',
    eyebrow: 'RUNTIME SETTINGS',
    title: '配置 Runtime 与安全边界',
    description: '管理平台参数、身份、安全策略和高风险操作审计。',
    boundary: '这些设置只属于 Runtime Operator；Owner 授权与 App 产品设置由各自产品处理。',
    primaryAction: { to: '/platform', label: '平台设置' },
    navItems: [
      { to: '/platform', label: 'Platform', icon: 'P' },
      { to: '/security', label: 'Security', icon: 'S' },
    ],
    routePrefixes: ['/settings', '/platform', '/security'],
    modules: [
      { name: '平台设置', description: '管理 Runtime 策略、资源和公共配置。', icon: 'P', status: 'available', statusLabel: '可用', to: '/platform' },
      { name: '安全与审计', description: '检查管理员身份、密钥引用和高风险操作。', icon: 'S', status: 'available', statusLabel: '可用', to: '/security' },
    ],
    flow: ['核对身份', '调整策略', '验证配置', '审计变更', '观察生效'],
  },
]

export function centerForPath(path: string): ConsoleCenter {
  return consoleCenters.find((center) => (
    path === center.to
    || center.routePrefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))
  )) || consoleCenters[0]
}

export function getConsoleCenter(id: CenterId): ConsoleCenter {
  return consoleCenters.find((center) => center.id === id) || consoleCenters[0]
}
