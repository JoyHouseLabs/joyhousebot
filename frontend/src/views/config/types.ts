import type { ConfigData } from '../../api/config'

export type ConfigCategoryKey =
  | 'agents'
  | 'providers'
  | 'channels'
  | 'tools'
  | 'gateway'
  | 'memory'
  | 'wallet'
  | 'auth'
  | 'skills'
  | 'plugins'
  | 'approvals'
  | 'browser'
  | 'messages'
  | 'commands'
  | 'env'

export interface ConfigCategoryItem {
  key: ConfigCategoryKey
  label: string
  icon?: string
}

export const CONFIG_CATEGORIES: ConfigCategoryItem[] = [
  { key: 'agents', label: 'Agent 默认' },
  { key: 'providers', label: 'API 提供商' },
  { key: 'channels', label: '通道' },
  { key: 'tools', label: '工具' },
  { key: 'gateway', label: 'Gateway' },
  { key: 'memory', label: '记忆' },
  { key: 'wallet', label: '钱包配置' },
  { key: 'auth', label: '认证与用量' },
  { key: 'skills', label: '技能' },
  { key: 'plugins', label: '插件' },
  { key: 'approvals', label: '审批' },
  { key: 'browser', label: '浏览器' },
  { key: 'messages', label: '消息行为' },
  { key: 'commands', label: '命令' },
  { key: 'env', label: '环境变量' },
]

export type { ConfigData }
