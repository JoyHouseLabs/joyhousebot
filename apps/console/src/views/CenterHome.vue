<template>
  <div class="page center-page">
    <header class="center-hero panel">
      <div class="center-hero-copy">
        <span class="eyebrow">{{ center.eyebrow }}</span>
        <h1>{{ center.title }}</h1>
        <p>{{ center.description }}</p>
        <div class="center-hero-actions">
          <router-link class="primary-button" :to="center.primaryAction.to">{{ center.primaryAction.label }}</router-link>
          <span>{{ readyCount }} 项入口已接入</span>
        </div>
      </div>
      <div class="center-mark" aria-hidden="true"><span>{{ center.icon }}</span><strong>{{ center.label }}</strong></div>
    </header>

    <section class="center-boundary">
      <span>中心边界</span>
      <p>{{ center.boundary }}</p>
    </section>

    <section class="center-section">
      <div class="center-section-heading">
        <div><span class="eyebrow">CAPABILITIES</span><h2>功能入口</h2></div>
        <p>规划能力会明确标记，不影响现有功能入口。</p>
      </div>
      <div class="center-module-grid">
        <template v-for="item in center.modules" :key="item.name">
          <router-link v-if="item.to" class="center-module panel" :class="item.status" :to="item.to">
            <div class="center-module-top"><span class="center-module-icon">{{ item.icon }}</span><span class="center-status">{{ item.statusLabel }}</span></div>
            <strong>{{ item.name }}</strong>
            <p>{{ item.description }}</p>
            <small>打开入口 <span>→</span></small>
          </router-link>
          <article v-else class="center-module panel planned">
            <div class="center-module-top"><span class="center-module-icon">{{ item.icon }}</span><span class="center-status">{{ item.statusLabel }}</span></div>
            <strong>{{ item.name }}</strong>
            <p>{{ item.description }}</p>
            <small>尚未提供独立页面</small>
          </article>
        </template>
      </div>
    </section>

    <section class="center-flow panel">
      <div class="center-section-heading compact"><div><span class="eyebrow">RUNTIME FLOW</span><h2>中心闭环</h2></div></div>
      <div class="center-flow-steps">
        <template v-for="(step, index) in center.flow" :key="step">
          <div><span>{{ index + 1 }}</span><strong>{{ step }}</strong></div>
          <b v-if="index < center.flow.length - 1">→</b>
        </template>
      </div>
    </section>

    <section class="center-map">
      <div class="center-section-heading compact"><div><span class="eyebrow">CONTROL PLANE</span><h2>切换控制面</h2></div></div>
      <div class="center-map-grid">
        <router-link v-for="item in consoleCenters" :key="item.id" :to="item.to" :class="{ active: item.id === center.id }">
          <span>{{ item.icon }}</span><div><strong>{{ item.label }}</strong><small>{{ item.caption }}</small></div>
        </router-link>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { consoleCenters, getConsoleCenter, type CenterId } from '../navigation/centers'

const props = defineProps<{ centerId: CenterId }>()
const center = computed(() => getConsoleCenter(props.centerId))
const readyCount = computed(() => center.value.modules.filter((item) => item.status !== 'planned').length)
</script>
