<script setup lang="ts">
import { ChevronLeft, ChevronRight, Expand, X } from 'lucide-vue-next'
import type { Locale } from '~/utils/routes'

const props = defineProps<{ locale: Locale }>()
const active = ref(0)
const expanded = ref(false)
let timer: ReturnType<typeof setInterval> | undefined

const shots = computed(() => props.locale === 'zh'
  ? [
      { src: '/images/intelligent-work-center-search.png', title: '自然语言搜索、核验与富化', alt: 'JoyhouseBot 人才搜索与富化工作中心' },
      { src: '/images/intelligent-work-center-replay.png', title: '每一步任务都可观测、可回放', alt: 'JoyhouseBot 可回放的任务执行时间线' },
      { src: '/images/intelligent-work-center-agent-config.png', title: 'Agent 身份、工具与协作策略可配置', alt: 'JoyhouseBot Agent 配置与角色管理界面' },
    ]
  : [
      { src: '/images/intelligent-work-center-search.png', title: 'Search, verify and enrich in natural language', alt: 'JoyhouseBot talent discovery work center' },
      { src: '/images/intelligent-work-center-replay.png', title: 'Every task can be observed and replayed', alt: 'JoyhouseBot replayable task execution timeline' },
      { src: '/images/intelligent-work-center-agent-config.png', title: 'Configure agent identity, tools and coordination', alt: 'JoyhouseBot Agent configuration and role management interface' },
    ])

const show = (index: number) => { active.value = (index + shots.value.length) % shots.value.length }
const next = () => show(active.value + 1)
const previous = () => show(active.value - 1)
const closePreview = () => { expanded.value = false }
const onKeydown = (event: KeyboardEvent) => { if (event.key === 'Escape') closePreview() }

onMounted(() => {
  timer = setInterval(next, 5200)
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <figure>
    <div class="relative overflow-hidden rounded-[1.15rem] bg-white">
      <button type="button" class="block w-full cursor-zoom-in" :aria-label="locale === 'zh' ? `查看大图：${shots[active].title}` : `View full image: ${shots[active].title}`" @click="expanded = true">
        <Transition name="work-shot" mode="out-in">
          <img :key="shots[active].src" :src="shots[active].src" :alt="shots[active].alt" class="w-full object-cover object-top" />
        </Transition>
      </button>
      <div class="pointer-events-none absolute right-3 top-3 grid h-9 w-9 place-items-center rounded-full bg-bot-ink/55 text-white opacity-0 backdrop-blur transition group-hover:opacity-100"><Expand :size="17" /></div>
      <button type="button" :aria-label="locale === 'zh' ? '上一张产品截图' : 'Previous product screenshot'" class="absolute left-3 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full border border-black/5 bg-white/90 text-bot opacity-0 shadow-card transition hover:bg-white group-hover:opacity-100 focus:opacity-100" @click="previous"><ChevronLeft :size="18" /></button>
      <button type="button" :aria-label="locale === 'zh' ? '下一张产品截图' : 'Next product screenshot'" class="absolute right-3 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full border border-black/5 bg-white/90 text-bot opacity-0 shadow-card transition hover:bg-white group-hover:opacity-100 focus:opacity-100" @click="next"><ChevronRight :size="18" /></button>
      <div class="absolute bottom-3 left-1/2 flex -translate-x-1/2 gap-1.5 rounded-full bg-bot-ink/55 px-2 py-1.5 backdrop-blur">
        <button v-for="(_, index) in shots" :key="index" type="button" :aria-label="locale === 'zh' ? `查看第 ${index + 1} 张产品截图` : `View product screenshot ${index + 1}`" :aria-current="index === active ? 'true' : undefined" class="h-1.5 rounded-full transition" :class="index === active ? 'w-5 bg-white' : 'w-1.5 bg-white/50 hover:bg-white/80'" @click="show(index)" />
      </div>
    </div>
    <figcaption class="px-2 pb-1 pt-4 text-sm leading-6 text-bot-muted">{{ shots[active].title }}</figcaption>
  </figure>
  <Teleport to="body">
    <Transition name="preview">
      <div v-if="expanded" class="fixed inset-0 z-[100] grid place-items-center bg-bot-ink/80 p-4 backdrop-blur-sm sm:p-8" role="dialog" aria-modal="true" :aria-label="locale === 'zh' ? '产品截图大图预览' : 'Full-size product screenshot preview'" @click.self="closePreview">
        <div class="relative max-h-full max-w-6xl">
          <img :src="shots[active].src" :alt="shots[active].alt" class="max-h-[88vh] w-auto max-w-full rounded-[1.15rem] bg-white shadow-float" />
          <button type="button" class="absolute -right-2 -top-2 grid h-10 w-10 place-items-center rounded-full bg-white text-bot shadow-card transition hover:scale-105" :aria-label="locale === 'zh' ? '关闭大图预览' : 'Close full-size preview'" @click="closePreview"><X :size="20" /></button>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.work-shot-enter-active,
.work-shot-leave-active { transition: opacity 260ms ease, transform 260ms ease; }
.work-shot-enter-from { opacity: 0; transform: translateX(10px); }
.work-shot-leave-to { opacity: 0; transform: translateX(-10px); }
.preview-enter-active,
.preview-leave-active { transition: opacity 180ms ease; }
.preview-enter-from,
.preview-leave-to { opacity: 0; }
</style>
