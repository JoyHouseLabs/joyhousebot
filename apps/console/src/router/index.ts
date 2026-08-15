import { createRouter, createWebHistory } from 'vue-router'
import ShellLayout from '../components/ShellLayout.vue'

const CenterHome = () => import('../views/CenterHome.vue')

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', name: 'Login', component: () => import('../views/Login.vue'), meta: { public: true } },
    {
      path: '/',
      component: ShellLayout,
      children: [
        { path: '', redirect: '/work' },
        { path: 'work', name: 'WorkCenter', component: CenterHome, props: { centerId: 'work' } },
        { path: 'studio', name: 'AgentStudio', component: CenterHome, props: { centerId: 'studio' } },
        { path: 'assets', name: 'AssetCenter', component: CenterHome, props: { centerId: 'assets' } },
        { path: 'assets/memory', name: 'MemoryAssets', component: () => import('../views/MemoryAssets.vue') },
        { path: 'assets/knowledge', name: 'KnowledgeAssets', component: () => import('../views/KnowledgeAssets.vue') },
        { path: 'automation', name: 'AutomationCenter', component: CenterHome, props: { centerId: 'automation' } },
        { path: 'automation/tasks', name: 'AutomationTasks', component: () => import('../views/AutomationTasks.vue') },
        { path: 'automation/events', name: 'EventTriggers', component: () => import('../views/EventTriggers.vue') },
        { path: 'governance', name: 'GovernanceCenter', component: CenterHome, props: { centerId: 'governance' } },
        { path: 'integrations', name: 'IntegrationCenter', component: CenterHome, props: { centerId: 'integrations' } },
        { path: 'apps', name: 'Apps', component: () => import('../views/Apps.vue') },
        { path: 'overview', name: 'Overview', component: () => import('../views/Overview.vue') },
        { path: 'runs', name: 'Runs', component: () => import('../views/Runs.vue') },
        { path: 'runs/:runId', name: 'RunDetail', component: () => import('../views/Runs.vue') },
        { path: 'action-items', name: 'ActionItems', component: () => import('../views/ActionItems.vue') },
        { path: 'works', name: 'Works', component: () => import('../views/Works.vue') },
        { path: 'chat', name: 'Chat', component: () => import('../views/Chat.vue') },
        { path: 'agents', name: 'Agents', component: () => import('../views/Agents.vue') },
        { path: 'teams', name: 'AgentTeams', component: () => import('../views/Teams.vue') },
        { path: 'workflows', name: 'Workflows', component: () => import('../views/Workflows.vue') },
        { path: 'skills', name: 'Skills', component: () => import('../views/Skills.vue') },
        { path: 'tools', name: 'Tools', component: () => import('../views/CapabilityEditor.vue') },
        { path: 'channels', name: 'Channels', component: () => import('../views/Channels.vue') },
        { path: 'connections', name: 'RemoteConnections', component: () => import('../views/RemoteConnections.vue') },
        { path: 'scenarios', name: 'Scenarios', component: () => import('../views/Scenarios.vue') },
        { path: 'platform', name: 'Platform', component: () => import('../views/Platform.vue') },
        { path: 'security', name: 'Security', component: () => import('../views/Security.vue') },
        { path: 'evaluations', name: 'Evaluations', component: () => import('../views/Evaluations.vue') },
        { path: 'prompt-governance', name: 'PromptGovernance', component: () => import('../views/PromptGovernance.vue') },
        { path: 'extensions', name: 'Extensions', component: () => import('../views/Plugins.vue') },
        { path: 'models', name: 'ModelProviders', component: () => import('../views/ModelProviders.vue') },
        { path: 'extensions/:pluginId', name: 'ExtensionDetail', component: () => import('../views/PluginDetail.vue') },
        { path: 'workspace', redirect: '/work' },
      ],
    },
  ],
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (typeof window !== 'undefined' && localStorage.getItem('joyhousebot_auth_session') !== '1') {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
