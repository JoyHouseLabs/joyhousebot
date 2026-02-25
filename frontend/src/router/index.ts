import { createRouter, createWebHistory } from 'vue-router'
import ShellLayout from '../components/ShellLayout.vue'
import Chat from '../views/Chat.vue'
import Config from '../views/Config.vue'
import ControlLayout from '../views/control/ControlLayout.vue'
import ControlOverview from '../views/control/ControlOverview.vue'
import ControlChannels from '../views/control/ControlChannels.vue'
import ControlInstances from '../views/control/ControlInstances.vue'
import ControlCron from '../views/control/ControlCron.vue'
import ControlCronNew from '../views/control/ControlCronNew.vue'
import ControlSandbox from '../views/control/ControlSandbox.vue'
import ControlQueue from '../views/control/ControlQueue.vue'
import ControlTraces from '../views/control/ControlTraces.vue'
import ControlDevices from '../views/control/ControlDevices.vue'
import ControlApprovals from '../views/control/ControlApprovals.vue'
import ControlUsage from '../views/control/ControlUsage.vue'
import Agent from '../views/Agent.vue'
import Skills from '../views/Skills.vue'
import Workspace from '../views/Workspace.vue'
import AppHost from '../views/AppHost.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', alias: [''], component: ShellLayout, children: [
      { path: '', redirect: 'chat' },
      { path: 'chat', name: 'Chat', component: Chat },
      { path: 'workspace', name: 'Workspace', component: Workspace },
      {
        path: 'control',
        component: ControlLayout,
        children: [
          { path: '', redirect: 'overview' },
          { path: 'overview', name: 'ControlOverview', component: ControlOverview },
          { path: 'channels', name: 'ControlChannels', component: ControlChannels },
          { path: 'queue', name: 'ControlQueue', component: ControlQueue },
          { path: 'traces', name: 'ControlTraces', component: ControlTraces },
          { path: 'instances', name: 'ControlInstances', component: ControlInstances },
          { path: 'cron', name: 'ControlCron', component: ControlCron },
          { path: 'cron/new', name: 'ControlCronNew', component: ControlCronNew },
          { path: 'usage', name: 'ControlUsage', component: ControlUsage },
          { path: 'devices', name: 'ControlDevices', component: ControlDevices },
          { path: 'approvals', name: 'ControlApprovals', component: ControlApprovals },
          { path: 'sandbox', name: 'ControlSandbox', component: ControlSandbox },
        ],
      },
      { path: 'agent', name: 'Agent', component: Agent },
      { path: 'skills', name: 'Skills', component: Skills },
      { path: 'app', name: 'AppHost', component: AppHost },
      { path: 'app/:appId', name: 'App', redirect: '/app' },
      { path: 'config', name: 'Config', component: Config },
    ] },
  ],
})

export default router
