<script setup lang="ts">
import {
  ArrowRight,
  ArrowUpRight,
  Building2,
  BookOpen,
  Bot,
  Box,
  Check,
  Chrome,
  Cpu,
  Database,
  Headphones,
  Languages,
  LockKeyhole,
  MessageSquareText,
  MousePointer2,
  ScanText,
  Sparkles,
  UserRound,
  Workflow,
} from 'lucide-vue-next'
import { siteCopy } from '~/data/site'
import { localPath, type Locale } from '~/utils/routes'

const props = defineProps<{ locale: Locale }>()
const copy = computed(() => siteCopy[props.locale])
const config = useRuntimeConfig()
const extensionUrl = computed(() => config.public.chromeStoreUrl || config.public.extensionDownloadUrl)

const productIcon = (key: string) => key === 'extension' ? Chrome : key === 'agent' ? Bot : Cpu
const featureIcons = [ScanText, Languages, Database]
</script>

<template>
  <div>
    <SiteHeader :locale="locale" />

    <main>
      <section class="relative overflow-hidden pb-20 pt-14 sm:pb-28 sm:pt-20 lg:pt-24">
        <div class="hero-grid absolute inset-0 -z-20" />
        <div class="glow-orb absolute -right-40 -top-40 -z-10 h-[38rem] w-[38rem]" />
        <div class="container-x grid items-center gap-14 lg:grid-cols-[1.02fr_.98fr]">
          <div>
            <p class="eyebrow">{{ copy.hero.eyebrow }}</p>
            <h1 class="mt-5 max-w-3xl text-4xl font-bold leading-[1.12] tracking-[-0.035em] sm:text-6xl lg:text-7xl">{{ copy.hero.title }}</h1>
            <p class="mt-7 max-w-2xl text-lg leading-8 text-bot-muted sm:text-xl">{{ copy.hero.copy }}</p>
            <div class="mt-9 flex flex-col gap-3 sm:flex-row">
              <a :href="config.public.agentUrl" target="_blank" rel="noopener" class="btn-primary">
                <Bot :size="19" /> {{ copy.hero.agent }}
              </a>
              <a :href="extensionUrl" :target="config.public.chromeStoreUrl ? '_blank' : undefined" rel="noopener" class="btn-secondary">
                <Chrome :size="18" /> {{ config.public.chromeStoreUrl ? copy.hero.install : copy.hero.coming }}
              </a>
            </div>
            <ul class="mt-8 grid gap-3 text-sm text-bot-muted sm:grid-cols-3">
              <li v-for="item in copy.hero.proof" :key="item" class="flex items-start gap-2"><Check :size="16" class="mt-0.5 shrink-0 text-bot" />{{ item }}</li>
            </ul>
          </div>

          <div class="relative mx-auto w-full max-w-[34rem]">
            <div class="absolute -left-10 top-20 -z-10 h-52 w-52 rounded-full bg-fuchsia-200/35 blur-3xl" />
            <div class="rounded-panel border border-white bg-white/75 p-3 shadow-float backdrop-blur sm:p-4">
              <div class="mb-3 flex items-center gap-2 px-2 py-1 text-xs text-bot-faint">
                <span class="h-2.5 w-2.5 rounded-full bg-red-300" /><span class="h-2.5 w-2.5 rounded-full bg-amber-300" /><span class="h-2.5 w-2.5 rounded-full bg-emerald-300" />
                <span class="ml-2">JoyhouseBot · AI-native Work Center</span>
              </div>
              <WorkCenterCarousel :locale="locale" class="group" />
            </div>
            <div class="absolute -bottom-5 -left-6 hidden items-center gap-3 rounded-2xl border border-black/5 bg-white px-4 py-3 shadow-card sm:flex">
              <div class="grid h-10 w-10 place-items-center rounded-xl bg-bot-soft text-bot"><Sparkles :size="20" /></div>
              <div><p class="text-xs text-bot-faint">JOYHOUSEBOT</p><p class="text-sm font-bold">{{ locale === 'zh' ? '智能工作中心' : 'Intelligent work center' }}</p></div>
            </div>
          </div>
        </div>
      </section>

      <section id="products" class="container-x scroll-mt-24 py-20 sm:py-24">
        <div class="max-w-3xl">
          <p class="eyebrow">{{ copy.products.eyebrow }}</p>
          <h2 class="section-title">{{ copy.products.title }}</h2>
          <p class="section-copy">{{ copy.products.copy }}</p>
        </div>
        <div class="mt-12 grid gap-5 lg:grid-cols-3">
          <NuxtLink v-for="item in copy.products.items" :key="item.key" :to="localPath(locale, `/${item.key}`)" class="surface-card group p-7 transition duration-200 hover:-translate-y-1 hover:border-bot/20 hover:shadow-float">
            <div class="flex items-start justify-between">
              <div class="grid h-14 w-14 place-items-center rounded-2xl bg-bot-soft text-bot"><component :is="productIcon(item.key)" :size="27" /></div>
              <span class="rounded-full bg-bot-soft px-3 py-1 text-xs font-bold text-bot">{{ item.status }}</span>
            </div>
            <h3 class="mt-7 text-xl font-bold">{{ item.name }}</h3>
            <p class="mt-3 min-h-20 leading-7 text-bot-muted">{{ item.summary }}</p>
            <span class="mt-6 inline-flex items-center gap-2 text-sm font-bold text-bot">{{ item.action }} <ArrowRight :size="16" class="transition group-hover:translate-x-1" /></span>
          </NuxtLink>
        </div>
      </section>

      <section class="border-y border-black/5 bg-white/70 py-20 sm:py-24">
        <div class="container-x grid items-center gap-14 lg:grid-cols-[.92fr_1.08fr]">
          <div>
            <p class="eyebrow">{{ copy.extension.eyebrow }}</p>
            <h2 class="section-title">{{ copy.extension.title }}</h2>
            <p class="section-copy">{{ copy.extension.copy }}</p>
            <div class="mt-9 space-y-5">
              <article v-for="(item, index) in copy.extension.features" :key="item[0]" class="flex gap-4">
                <div class="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-bot-soft text-bot"><component :is="featureIcons[index]" :size="21" /></div>
                <div><h3 class="font-bold">{{ item[0] }}</h3><p class="mt-1 text-sm leading-6 text-bot-muted">{{ item[1] }}</p></div>
              </article>
            </div>
            <div class="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3">
              <NuxtLink :to="localPath(locale, '/extension')" class="inline-flex items-center gap-2 font-bold text-bot">{{ copy.products.items[0].action }} <ArrowRight :size="17" /></NuxtLink>
              <a :href="config.public.extensionRepoUrl" target="_blank" rel="noopener" class="inline-flex items-center gap-2 text-sm font-bold text-bot-muted hover:text-bot">GitHub <ArrowUpRight :size="15" /></a>
            </div>
          </div>
          <figure class="surface-card overflow-hidden p-3 sm:p-5">
            <img src="/images/selection-translation.png" :alt="copy.extension.caption" class="w-full rounded-[1.1rem] object-cover object-top" />
            <figcaption class="flex items-center gap-2 px-2 pb-1 pt-4 text-sm text-bot-muted"><MousePointer2 :size="16" class="text-bot" />{{ copy.extension.caption }}</figcaption>
          </figure>
        </div>
      </section>

      <section id="flow" class="container-x scroll-mt-24 py-20 sm:py-24">
        <div class="mx-auto max-w-3xl text-center">
          <p class="eyebrow">{{ copy.flow.eyebrow }}</p>
          <h2 class="section-title">{{ copy.flow.title }}</h2>
          <p class="section-copy mx-auto">{{ copy.flow.copy }}</p>
        </div>
        <div class="mt-12 grid gap-4 md:grid-cols-4">
          <article v-for="step in copy.flow.steps" :key="step[0]" class="surface-card relative p-6">
            <p class="text-sm font-black text-bot">{{ step[0] }}</p>
            <h3 class="mt-7 text-lg font-bold">{{ step[1] }}</h3>
            <p class="mt-3 text-sm leading-6 text-bot-muted">{{ step[2] }}</p>
          </article>
        </div>
      </section>

      <section id="scenarios" class="border-y border-black/5 bg-white/70 py-20 sm:py-24">
        <div class="container-x">
          <div class="mx-auto max-w-3xl text-center">
            <p class="eyebrow">{{ copy.scenarios.eyebrow }}</p>
            <h2 class="section-title">{{ copy.scenarios.title }}</h2>
            <p class="section-copy mx-auto">{{ copy.scenarios.copy }}</p>
          </div>
          <div class="mt-12 grid gap-5 lg:grid-cols-2">
            <article class="surface-card p-7 sm:p-9">
              <div class="grid h-14 w-14 place-items-center rounded-2xl bg-bot-soft text-bot"><UserRound :size="27" /></div>
              <p class="mt-8 text-xs font-bold tracking-[0.18em] text-bot">{{ copy.scenarios.personal.label }}</p>
              <h3 class="mt-3 text-2xl font-bold">{{ copy.scenarios.personal.title }}</h3>
              <p class="mt-4 max-w-xl leading-8 text-bot-muted">{{ copy.scenarios.personal.copy }}</p>
              <a :href="`${config.public.appUrl}/clips`" target="_blank" rel="noopener" class="mt-7 inline-flex items-center gap-2 font-bold text-bot">{{ locale === 'zh' ? '从一条资料开始' : 'Start with one source' }} <ArrowUpRight :size="17" /></a>
            </article>
            <article class="surface-card bg-bot-ink p-7 text-white sm:p-9">
              <div class="grid h-14 w-14 place-items-center rounded-2xl bg-white/10 text-violet-300"><Building2 :size="27" /></div>
              <p class="mt-8 text-xs font-bold tracking-[0.18em] text-violet-300">{{ copy.scenarios.organization.label }}</p>
              <h3 class="mt-3 text-2xl font-bold">{{ copy.scenarios.organization.title }}</h3>
              <p class="mt-4 max-w-xl leading-8 text-white/65">{{ copy.scenarios.organization.copy }}</p>
              <a :href="config.public.agentUrl" target="_blank" rel="noopener" class="mt-7 inline-flex items-center gap-2 font-bold text-violet-200 hover:text-white">{{ locale === 'zh' ? '查看 Runtime 能力' : 'Explore Runtime capabilities' }} <ArrowUpRight :size="17" /></a>
            </article>
          </div>
        </div>
      </section>

      <section class="border-y border-black/5 bg-bot-ink py-20 text-white sm:py-24">
        <div class="container-x grid items-center gap-14 lg:grid-cols-[1fr_.9fr]">
          <div>
            <p class="eyebrow !text-violet-300">{{ copy.agent.eyebrow }}</p>
            <h2 class="mt-3 text-3xl font-bold leading-tight sm:text-5xl">{{ copy.agent.title }}</h2>
            <p class="mt-5 max-w-2xl text-lg leading-8 text-white/65">{{ copy.agent.copy }}</p>
            <ul class="mt-8 grid gap-3 sm:grid-cols-2">
              <li v-for="item in copy.agent.bullets" :key="item" class="flex items-center gap-2 text-sm text-white/80"><Check :size="17" class="text-violet-300" />{{ item }}</li>
            </ul>
            <a :href="config.public.agentUrl" target="_blank" rel="noopener" class="mt-9 inline-flex items-center gap-2 rounded-full bg-white px-6 py-3.5 font-bold text-bot-ink">{{ copy.agent.action }} <ArrowUpRight :size="18" /></a>
          </div>
          <figure class="rounded-panel overflow-hidden border border-white/10 bg-white/5 p-3 shadow-float sm:p-4">
            <div class="mb-3 flex items-center justify-between px-2 py-1 text-xs text-white/45"><span>{{ locale === 'zh' ? '可观测执行 · 任务回放' : 'Observable execution · task replay' }}</span><span class="rounded-full bg-emerald-400/15 px-2 py-1 text-emerald-300">{{ locale === 'zh' ? '真实界面' : 'LIVE PRODUCT' }}</span></div>
            <img src="/images/intelligent-work-center-replay.png" :alt="locale === 'zh' ? 'JoyhouseBot 可回放的任务执行时间线' : 'JoyhouseBot replayable task execution timeline'" class="w-full rounded-[1.1rem]" />
            <figcaption class="px-2 pb-1 pt-4 text-sm leading-6 text-white/60">{{ locale === 'zh' ? '每一步执行、协同、工具调用与人工反馈，都能被查看、回放并用于下一次优化。' : 'Execution, coordination, tool calls and human feedback can be inspected, replayed and used to improve the next run.' }}</figcaption>
          </figure>
        </div>
      </section>

      <section class="container-x py-20 sm:py-24">
        <div class="grid items-center gap-14 lg:grid-cols-[.9fr_1.1fr]">
          <div class="order-2 lg:order-1">
            <figure class="surface-card overflow-hidden p-3 shadow-float">
              <img src="/images/joyhouse-library.png" :alt="locale === 'zh' ? 'JoyHouse 私人书房' : 'Private JoyHouse library'" class="max-h-[660px] w-full rounded-[1.1rem] object-cover object-top" />
            </figure>
          </div>
          <div class="order-1 lg:order-2">
            <p class="eyebrow">{{ copy.house.eyebrow }}</p>
            <h2 class="section-title">{{ copy.house.title }}</h2>
            <p class="section-copy">{{ copy.house.copy }}</p>
            <a :href="config.public.visionUrl" target="_blank" rel="noopener" class="btn-primary mt-8">{{ copy.house.action }} <ArrowUpRight :size="18" /></a>
          </div>
        </div>
      </section>

      <section class="container-x grid gap-5 pb-20 lg:grid-cols-2">
        <article class="surface-card overflow-hidden p-8 sm:p-10">
          <div class="grid h-12 w-12 place-items-center rounded-2xl bg-bot-soft text-bot"><Box :size="24" /></div>
          <p class="eyebrow mt-8">{{ copy.hardware.eyebrow }}</p>
          <h2 class="mt-3 text-3xl font-bold">{{ copy.hardware.title }}</h2>
          <p class="mt-5 leading-8 text-bot-muted">{{ copy.hardware.copy }}</p>
          <a :href="`mailto:${config.public.supportEmail}?subject=JoyhouseBot Hardware`" class="mt-7 inline-flex items-center gap-2 font-bold text-bot">{{ copy.hardware.action }} <ArrowRight :size="17" /></a>
        </article>
        <article class="surface-card overflow-hidden bg-bot-soft p-8 sm:p-10">
          <div class="grid h-12 w-12 place-items-center rounded-2xl bg-white text-bot"><LockKeyhole :size="24" /></div>
          <p class="eyebrow mt-8">{{ copy.privacy.eyebrow }}</p>
          <h2 class="mt-3 text-3xl font-bold">{{ copy.privacy.title }}</h2>
          <p class="mt-5 leading-8 text-bot-muted">{{ copy.privacy.copy }}</p>
          <NuxtLink :to="localPath(locale, '/privacy')" class="mt-7 inline-flex items-center gap-2 font-bold text-bot">{{ copy.privacy.action }} <ArrowRight :size="17" /></NuxtLink>
        </article>
      </section>

      <section class="container-x pb-8">
        <div class="relative overflow-hidden rounded-panel bg-bot-gradient px-7 py-12 text-white shadow-float sm:px-12 sm:py-14">
          <div class="absolute -right-20 -top-28 h-72 w-72 rounded-full border-[36px] border-white/10" />
          <div class="relative flex flex-col items-start justify-between gap-8 lg:flex-row lg:items-end">
            <div class="max-w-3xl"><p class="text-sm font-bold uppercase tracking-[.18em] text-white/65">JOYHOUSEBOT</p><h2 class="mt-4 text-3xl font-bold sm:text-5xl">{{ copy.final.title }}</h2><p class="mt-4 max-w-2xl leading-7 text-white/75">{{ copy.final.copy }}</p></div>
            <div class="flex flex-wrap gap-3"><a :href="config.public.agentUrl" target="_blank" rel="noopener" class="inline-flex items-center gap-2 rounded-full bg-white px-6 py-3.5 font-bold text-bot">{{ copy.final.support }} <ArrowUpRight :size="18" /></a><a :href="extensionUrl" :target="config.public.chromeStoreUrl ? '_blank' : undefined" rel="noopener" class="inline-flex items-center gap-2 rounded-full border border-white/30 px-6 py-3.5 font-bold">{{ copy.final.install }} <ArrowRight :size="18" /></a></div>
          </div>
        </div>
      </section>
    </main>

    <SiteFooter :locale="locale" />
  </div>
</template>
