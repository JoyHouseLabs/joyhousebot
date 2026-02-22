<template>
  <n-message-provider>
    <n-dialog-provider>
      <n-config-provider
        :locale="zhCN"
        :date-locale="dateZhCN"
        :theme-overrides="themeOverrides"
        :theme="naiveTheme"
      >
        <router-view />
      </n-config-provider>
    </n-dialog-provider>
  </n-message-provider>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { zhCN, dateZhCN, darkTheme } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import './styles/base.css'
import './styles/layout.css'

const naiveTheme = ref<typeof darkTheme | null>(null)
function updateNaiveTheme() {
  const t = document.documentElement.getAttribute('data-theme')
  naiveTheme.value = t === 'light' ? null : darkTheme
}
onMounted(() => {
  updateNaiveTheme()
  const ob = new MutationObserver(updateNaiveTheme)
  ob.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
})

const themeOverrides: GlobalThemeOverrides = {
  common: {
    fontFamily: 'var(--font-body)',
    primaryColor: '#ff5c5c',
    primaryColorHover: '#ff7070',
    primaryColorPressed: '#e55050',
    primaryColorSuppl: '#ff5c5c',
    borderRadius: 'var(--radius-md)',
  },
  Card: {
    color: 'var(--card)',
    borderRadius: 'var(--radius-lg)',
  },
  Button: {
    borderRadius: 'var(--radius-md)',
  },
  Input: {
    borderRadius: 'var(--radius-md)',
  },
}
</script>
