<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { checkHealth, consoleUrl } from './api'

const healthy = ref<boolean | null>(null)
onMounted(async () => { healthy.value = await checkHealth() })

const navigation = [
  { to: '/', label: '开始', icon: '✦' },
  { to: '/activity', label: '进行中', icon: '◷' },
  { to: '/works', label: '成果', icon: '◆' },
  { to: '/automation', label: '自动化', icon: '↻' },
]
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink to="/" class="brand" aria-label="JoyClaw 首页">
        <span class="brand-mark">JOY</span>
        <span><strong>JoyClaw</strong><small>个人智能执行外挂</small></span>
      </RouterLink>
      <nav class="desktop-nav" aria-label="主导航">
        <RouterLink v-for="item in navigation" :key="item.to" :to="item.to">
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="topbar-actions">
        <span class="health" :class="{ online: healthy, offline: healthy === false }">
          <i />{{ healthy === null ? '连接中' : healthy ? '执行体在线' : '执行体离线' }}
        </span>
        <a :href="consoleUrl()" class="advanced-link">高级控制台</a>
        <RouterLink to="/settings" class="icon-button" aria-label="设置">⌘</RouterLink>
      </div>
    </header>

    <main class="page-container"><RouterView /></main>

    <nav class="mobile-nav" aria-label="移动端主导航">
      <RouterLink v-for="item in navigation" :key="item.to" :to="item.to">
        <span>{{ item.icon }}</span>{{ item.label }}
      </RouterLink>
    </nav>
  </div>
</template>
