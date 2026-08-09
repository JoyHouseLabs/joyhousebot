<template>
  <div v-if="!isLoginPage && target" class="impersonation-chip active" role="status">
    <span class="dot" aria-hidden="true"></span>
    <span>当前个人空间：<strong>{{ target }}</strong>（个人数据请求按此 user_id 隔离）</span>
    <button type="button" @click="exit">退出代操作</button>
  </div>
  <button v-else-if="!isLoginPage" type="button" class="impersonation-chip entry" title="切换当前操作的个人 user_id" @click="enter">
    切换 user_id…
  </button>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { clearImpersonationTarget, getImpersonationTarget, setImpersonationTarget } from '../api/identity'

// 显式代操作入口：默认关闭，操作员主动输入目标用户后才发送
// X-Impersonate-User-ID。切换后整页刷新，保证所有视图按新身份重新拉取。
const target = ref<string | null>(getImpersonationTarget())
const isLoginPage = window.location.pathname.endsWith('/login')

function enter() {
  const input = window.prompt('输入要操作的个人 user_id（留空取消）：', target.value || '')
  const normalized = (input ?? '').trim()
  if (!normalized) return
  setImpersonationTarget(normalized)
  window.location.reload()
}

function exit() {
  clearImpersonationTarget()
  window.location.reload()
}
</script>

<style scoped>
.impersonation-chip {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 9999;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.4;
  font-family: inherit;
  cursor: pointer;
}
.impersonation-chip.entry {
  border: 1px solid #94a3b8;
  background: rgba(15, 23, 42, 0.85);
  color: #cbd5e1;
}
.impersonation-chip.entry:hover {
  background: rgba(15, 23, 42, 1);
  color: #f1f5f9;
}
.impersonation-chip.active {
  border: 1px solid #b45309;
  background: #fef3c7;
  color: #78350f;
  cursor: default;
  box-shadow: 0 2px 10px rgba(180, 83, 9, 0.35);
}
.impersonation-chip.active .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #d97706;
}
.impersonation-chip.active button {
  border: 1px solid #b45309;
  background: #fff;
  color: #78350f;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
}
.impersonation-chip.active button:hover {
  background: #fde68a;
}
@media (max-width: 920px) {
  .impersonation-chip {
    right: 14px;
    bottom: 78px;
  }
  .impersonation-chip.active {
    left: 14px;
    max-width: calc(100vw - 28px);
    flex-wrap: wrap;
    border-radius: 14px;
  }
}
</style>
