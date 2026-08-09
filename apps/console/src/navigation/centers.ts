export type CenterId = 'work' | 'studio' | 'assets' | 'automation' | 'governance' | 'integrations'

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
    id: 'work',
    to: '/work',
    label: '工作中心',
    mobileLabel: '工作',
    caption: '目标、会话与执行',
    icon: '✦',
    eyebrow: 'WORK CENTER',
    title: '把目标推进成可验证的成果',
    description: '从自然语言目标出发，在同一条 Run 时间线上处理会话、长任务、人工反馈和交付结果。',
    boundary: '这里面向日常使用者组织工作，不承载 Agent 配置和平台运维。',
    primaryAction: { to: '/chat', label: '开始一项工作' },
    navItems: [
      { to: '/chat', label: 'Agent 工作台', icon: '✦' },
      { to: '/runs', label: '执行记录', icon: 'R' },
    ],
    routePrefixes: ['/chat', '/runs'],
    modules: [
      { name: 'Agent 工作台', description: '提交真实任务，持续查看消息、会话和执行时间线。', icon: '✦', status: 'available', statusLabel: '可用', to: '/chat' },
      { name: 'Run 与 Task', description: '查询状态、事件、Trace、日志、产物和恢复过程。', icon: 'R', status: 'available', statusLabel: '可用', to: '/runs' },
      { name: '成果交付', description: '将执行产物沉淀为可版本化、可分享、可撤销的 Work。', icon: 'W', status: 'connected', statusLabel: '跨中心', to: '/works' },
      { name: '统一待办与审批', description: '汇总需要用户确认、补充信息或审批的运行节点。', icon: 'A', status: 'planned', statusLabel: '规划中' },
    ],
    flow: ['明确目标', '提交 Run', '协作与反馈', '验证结果', '沉淀 Work'],
  },
  {
    id: 'studio',
    to: '/studio',
    label: 'Agent Studio',
    mobileLabel: 'Studio',
    caption: 'Agent、场景与能力',
    icon: '◇',
    eyebrow: 'AGENT STUDIO',
    title: '构建可发布、可复用的智能能力',
    description: '统一设计 Agent、Scenario、Skill 和 Tool，让每次能力变化都经过版本发布、加载确认和审计。',
    boundary: 'Studio 负责能力定义与发布；实际执行仍统一进入 Run/Task 链路。',
    primaryAction: { to: '/agents', label: '管理 Agent' },
    navItems: [
      { to: '/agents', label: 'Agent', icon: 'A' },
      { to: '/workflows', label: 'Workflows', icon: 'F' },
      { to: '/scenarios', label: 'Scenarios', icon: 'S' },
      { to: '/skills', label: 'Skills', icon: 'K' },
      { to: '/tools', label: 'Tools', icon: 'T' },
    ],
    routePrefixes: ['/agents', '/workflows', '/scenarios', '/skills', '/tools'],
    modules: [
      { name: 'Agent', description: '配置模型、提示、能力准入、Monitor 策略和发布版本。', icon: 'A', status: 'available', statusLabel: '可用', to: '/agents' },
      { name: 'Scenario', description: '定义路由、追问、DAG 编排和场景级能力边界。', icon: 'S', status: 'available', statusLabel: '可用', to: '/scenarios' },
      { name: 'Skill', description: '管理可复用的方法、知识说明和版本化执行指导。', icon: 'K', status: 'available', statusLabel: '可用', to: '/skills' },
      { name: 'Tool', description: '登记并校验 Agent 可调用的确定性执行能力。', icon: 'T', status: 'available', statusLabel: '可用', to: '/tools' },
      { name: 'AI Workflow', description: '对 Agent 描述目标，自动生成可执行流程；可视化审查、对话修改、预演并发布。', icon: 'F', status: 'available', statusLabel: '可用', to: '/workflows' },
    ],
    flow: ['定义能力', '组合场景', '评测验证', '发布版本', 'Worker 加载'],
  },
  {
    id: 'assets',
    to: '/assets',
    label: '资产中心',
    mobileLabel: '资产',
    caption: '知识、记忆与成果',
    icon: '◆',
    eyebrow: 'ASSET CENTER',
    title: '让数据、经验和成果持续复利',
    description: '把执行中的 Artifact、个人记忆、知识和最终 Work 组织成私有、可追溯、可演进的长期资产。',
    boundary: '个人上下文默认私有；只有经过版本化和验证的发布物才进入分享链路。',
    primaryAction: { to: '/works', label: '查看成果作品' },
    navItems: [
      { to: '/works', label: 'Works', icon: 'W' },
      { to: '/assets/memory', label: 'Memory', icon: 'M' },
      { to: '/assets/knowledge', label: 'Knowledge', icon: 'K' },
    ],
    routePrefixes: ['/works', '/assets/memory', '/assets/knowledge'],
    modules: [
      { name: 'Work', description: '管理不可变版本、发布、协作者、分享链接、撤销和审计。', icon: 'W', status: 'available', statusLabel: '可用', to: '/works' },
      { name: 'Artifact', description: '沿 Run 查看原始执行产物，并选择有价值的结果形成 Work。', icon: 'F', status: 'connected', statusLabel: '跨中心', to: '/runs' },
      { name: 'Memory', description: '提供个人长期记忆的检索、来源、纠错和候选确认视图。', icon: 'M', status: 'available', statusLabel: '可用', to: '/assets/memory' },
      { name: 'Knowledge', description: '创建私有知识库，组织知识源、索引分块、引用证据和采集来源。', icon: 'K', status: 'available', statusLabel: '可用', to: '/assets/knowledge' },
      { name: '评测资产', description: '将数据集、基线和评测证据沉淀为能力迭代依据。', icon: 'E', status: 'connected', statusLabel: '跨中心', to: '/evaluations' },
    ],
    flow: ['产生 Artifact', '筛选与验证', '形成 Work', '版本演进', '受控分享'],
  },
  {
    id: 'automation',
    to: '/automation',
    label: '自动化中心',
    mobileLabel: '自动化',
    caption: '计划、Monitor 与触发',
    icon: '◷',
    eyebrow: 'AUTOMATION CENTER',
    title: '让 Agent 在需要时持续运行',
    description: '统一观察 Schedule、Monitor、渠道入站和外部事件触发，并让每次自动执行都进入同一条可恢复链路。',
    boundary: '自动化只负责决定何时触发；执行、状态回写和重试仍由统一 Run/Task 状态机负责。',
    primaryAction: { to: '/automation/tasks', label: '管理自动化任务' },
    navItems: [
      { to: '/automation/tasks', label: '自动化任务', icon: 'A' },
      { to: '/automation/events', label: 'Webhook 事件', icon: 'H' },
    ],
    routePrefixes: ['/automation/tasks', '/automation/events'],
    modules: [
      { name: 'Schedule 状态', description: '在运行概览中查看计划任务、最近触发和下一次执行。', icon: 'S', status: 'connected', statusLabel: '已有链路', to: '/overview' },
      { name: 'Agent Monitor', description: '在 Agent 配置中维护定时巡检和持续运行策略。', icon: 'M', status: 'connected', statusLabel: '已有链路', to: '/agents' },
      { name: 'Channel 投递', description: '管理渠道连接，并承接自动任务的结果投递。', icon: 'C', status: 'connected', statusLabel: '跨中心', to: '/channels' },
      { name: '自动化任务管理', description: '集中创建、暂停、恢复、补跑和查看触发历史。', icon: 'A', status: 'available', statusLabel: '可用', to: '/automation/tasks' },
      { name: 'Webhook 与外部事件', description: '用统一策略接收业务事件并映射为幂等 Run。', icon: 'H', status: 'available', statusLabel: '可用', to: '/automation/events' },
    ],
    flow: ['定义触发', '冻结幂等键', '创建 Run', '执行与重试', '回写并投递'],
  },
  {
    id: 'governance',
    to: '/governance',
    label: '治理中心',
    mobileLabel: '治理',
    caption: '质量、安全与运行治理',
    icon: '◎',
    eyebrow: 'GOVERNANCE CENTER',
    title: '让每次执行可控、可查、可改进',
    description: '统一观察系统健康、发布门禁、安全边界、审计回放、成本和性能，形成从问题到改进的闭环。',
    boundary: '治理规则在控制面定义，在每次能力发布和 Runtime 执行中强制生效。',
    primaryAction: { to: '/overview', label: '打开运行概览' },
    navItems: [
      { to: '/overview', label: '运行概览', icon: 'O' },
      { to: '/evaluations', label: '评测门禁', icon: 'E' },
      { to: '/platform', label: '平台治理', icon: 'P' },
      { to: '/security', label: '安全', icon: 'G' },
    ],
    routePrefixes: ['/overview', '/evaluations', '/platform', '/security'],
    modules: [
      { name: '运行概览', description: '观察 API、PostgreSQL、Worker、Provider、Run 和 Schedule。', icon: 'O', status: 'available', statusLabel: '可用', to: '/overview' },
      { name: '评测门禁', description: '用评测结果决定版本是否具备发布和切换资格。', icon: 'E', status: 'available', statusLabel: '可用', to: '/evaluations' },
      { name: '平台治理', description: '管理运行时策略、资源限制和控制面公共配置。', icon: 'P', status: 'available', statusLabel: '可用', to: '/platform' },
      { name: '安全与审计', description: '检查身份、权限、密钥引用和高风险操作边界。', icon: 'G', status: 'available', statusLabel: '可用', to: '/security' },
      { name: '执行回放', description: '沿 Run 还原 Task、Event、Trace、Artifact 和错误恢复过程。', icon: 'R', status: 'connected', statusLabel: '跨中心', to: '/runs' },
    ],
    flow: ['准入策略', '发布门禁', '运行观测', '审计回放', '持续优化'],
  },
  {
    id: 'integrations',
    to: '/integrations',
    label: '集成中心',
    mobileLabel: '集成',
    caption: '模型、渠道与插件',
    icon: '⌘',
    eyebrow: 'INTEGRATION CENTER',
    title: '连接已有系统，而不是制造数据孤岛',
    description: '统一接入模型、Channel、Plugin 和 MCP Server，并通过能力目录、权限和审计进入 Runtime。',
    boundary: '桥接和插件只做适配，不创建第二套执行状态机或绕开 Capability Dispatcher。',
    primaryAction: { to: '/plugins', label: '管理插件' },
    navItems: [
      { to: '/plugins', label: 'Plugins', icon: 'X' },
      { to: '/channels', label: 'Channels', icon: 'C' },
      { to: '/mcp', label: 'MCP Servers', icon: 'M' },
    ],
    routePrefixes: ['/plugins', '/channels', '/mcp'],
    modules: [
      { name: 'Plugin', description: '安装独立业务能力包并查看其注册的 Runtime 资源。', icon: 'X', status: 'available', statusLabel: '可用', to: '/plugins' },
      { name: 'Channel', description: '连接消息入口和结果投递渠道，保持统一 Run 语义。', icon: 'C', status: 'available', statusLabel: '可用', to: '/channels' },
      { name: 'MCP Server', description: '登记外部工具服务并纳入能力准入、权限和审计。', icon: 'M', status: 'available', statusLabel: '可用', to: '/mcp' },
      { name: 'Provider 状态', description: '观察模型提供方可用性、路由状态和运行依赖。', icon: 'P', status: 'connected', statusLabel: '跨中心', to: '/overview' },
      { name: 'Connector Catalog', description: '集中管理业务系统连接器、凭据引用和健康检查。', icon: 'D', status: 'planned', statusLabel: '规划中' },
    ],
    flow: ['登记连接', '能力准入', '权限校验', '统一调用', '对账审计'],
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
