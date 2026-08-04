import { createRouter, createWebHistory } from 'vue-router'
import ShellLayout from '../components/ShellLayout.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', name: 'Login', component: () => import('../views/Login.vue'), meta: { public: true } },
    {
      path: '/',
      component: ShellLayout,
      children: [
        { path: '', redirect: '/overview' },
        { path: 'overview', name: 'Overview', component: () => import('../views/Overview.vue') },
        { path: 'runs', name: 'Runs', component: () => import('../views/Runs.vue') },
        { path: 'chat', name: 'Chat', component: () => import('../views/Chat.vue') },
        { path: 'agents', name: 'Agents', component: () => import('../views/Agents.vue') },
        { path: 'skills', name: 'Skills', component: () => import('../views/CapabilityEditor.vue') },
        { path: 'tools', name: 'Tools', component: () => import('../views/CapabilityEditor.vue') },
        { path: 'mcp', name: 'MCP', component: () => import('../views/MCPServers.vue') },
        { path: 'channels', name: 'Channels', component: () => import('../views/Channels.vue') },
        { path: 'scenarios', name: 'Scenarios', component: () => import('../views/Scenarios.vue') },
        { path: 'platform', name: 'Platform', component: () => import('../views/Platform.vue') },
        { path: 'plugins/dinq', name: 'DinqPlugin', component: () => import('../views/DinqPlugin.vue') },
        { path: 'workspace', redirect: '/runs' },
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
