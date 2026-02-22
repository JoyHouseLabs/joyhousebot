<template>
  <div class="config-panel config-placeholder">
    <div class="config-panel-header">
      <h2 class="config-panel-title">{{ label }}</h2>
      <p class="config-panel-desc">该配置项表单开发中，可通过配置文件或 RPC 编辑。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  label: string
}>()
</script>

<style scoped>
.config-panel-header {
  margin-bottom: 16px;
}
.config-panel-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px 0;
}
.config-panel-desc {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 0;
}
</style>
