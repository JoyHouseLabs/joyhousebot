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
        { path: '', redirect: '/overview' },
        { path: 'build', name: 'BuildCenter', component: CenterHome, props: { centerId: 'build' } },
        { path: 'settings', name: 'SettingsCenter', component: CenterHome, props: { centerId: 'settings' } },
        { path: 'apps', name: 'Apps', component: () => import('../views/Apps.vue') },
        { path: 'overview', name: 'Overview', component: () => import('../views/Overview.vue') },
        { path: 'runs', name: 'Runs', component: () => import('../views/Runs.vue') },
        { path: 'runs/:runId', name: 'RunDetail', component: () => import('../views/Runs.vue') },
        { path: 'agents', name: 'Agents', component: () => import('../views/Agents.vue') },
        { path: 'teams', name: 'AgentTeams', component: () => import('../views/Teams.vue') },
        { path: 'teams/compose', name: 'TeamComposer', component: () => import('../views/TeamComposer.vue') },
        { path: 'workflows', name: 'Workflows', component: () => import('../views/Workflows.vue') },
        { path: 'skills', name: 'Skills', component: () => import('../views/Skills.vue') },
        { path: 'capabilities', name: 'Capabilities', component: () => import('../views/CapabilityEditor.vue') },
        { path: 'channels', name: 'Channels', component: () => import('../views/Channels.vue') },
        { path: 'connections', name: 'RemoteConnections', component: () => import('../views/RemoteConnections.vue') },
        { path: 'devices', name: 'DeviceHosts', component: () => import('../views/DeviceHosts.vue') },
        { path: 'scenarios', name: 'Scenarios', component: () => import('../views/Scenarios.vue') },
        { path: 'platform', name: 'Platform', component: () => import('../views/Platform.vue') },
        { path: 'security', name: 'Security', component: () => import('../views/Security.vue') },
        { path: 'evaluations', name: 'Evaluations', component: () => import('../views/Evaluations.vue') },
        { path: 'prompt-governance', name: 'PromptGovernance', component: () => import('../views/PromptGovernance.vue') },
        { path: 'extensions', name: 'Extensions', component: () => import('../views/Extensions.vue') },
        { path: 'models', name: 'ModelProviders', component: () => import('../views/ModelProviders.vue') },
        { path: 'extensions/:extensionId', name: 'ExtensionDetail', component: () => import('../views/ExtensionDetail.vue') },
        { path: ':pathMatch(.*)*', redirect: '/overview' },
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
