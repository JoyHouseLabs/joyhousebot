import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import ImpersonationControl from './components/ImpersonationControl.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

// Explicit impersonation entry, mounted outside the router layout so it stays
// visible on every console page and cannot be toggled off by in-flight views.
const impersonationHost = document.createElement('div')
impersonationHost.id = 'impersonation-control'
document.body.appendChild(impersonationHost)
createApp(ImpersonationControl).mount(impersonationHost)
