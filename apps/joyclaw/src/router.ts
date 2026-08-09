import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'Home', component: () => import('./views/HomeView.vue') },
    { path: '/activity', name: 'Activity', component: () => import('./views/ActivityView.vue') },
    { path: '/runs/:runId', name: 'Run', component: () => import('./views/RunView.vue') },
    { path: '/works', name: 'Works', component: () => import('./views/WorksView.vue') },
    { path: '/automation', name: 'Automation', component: () => import('./views/AutomationView.vue') },
    { path: '/settings', name: 'Settings', component: () => import('./views/SettingsView.vue') },
  ],
})

export default router
