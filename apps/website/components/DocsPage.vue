<script setup lang="ts">
import { ArrowLeft, ArrowUpRight, BookOpen, Boxes, Cable, Terminal } from 'lucide-vue-next'
import { localPath, type Locale } from '~/utils/routes'

const props = defineProps<{ locale: Locale }>()
const config = useRuntimeConfig()

const docs = computed(() => props.locale === 'zh'
  ? [
      { icon: BookOpen, title: '开始使用', copy: '从安装、配置到启动第一个 Agent Runtime。', path: '/README.md' },
      { icon: Boxes, title: '架构与设计', copy: '理解 Runtime、消息、上下文、工具与执行记录如何协作。', path: '/docs/DESIGN_AND_ARCHITECTURE.md' },
      { icon: Terminal, title: 'CLI 参考', copy: '查看命令行、配置和本地运行说明。', path: '/docs/CLI_REFERENCE.md' },
      { icon: Cable, title: '渠道与插件', copy: '连接消息渠道、原生插件、Skills 与工具。', path: '/docs/zh/CHANNEL_PLUGIN_GUIDE.md' },
    ]
  : [
      { icon: BookOpen, title: 'Get started', copy: 'Install, configure and start your first Agent Runtime.', path: '/README_EN.md' },
      { icon: Boxes, title: 'Architecture & design', copy: 'Understand how runtime, messages, context, tools and execution records work together.', path: '/docs/DESIGN_AND_ARCHITECTURE.md' },
      { icon: Terminal, title: 'CLI reference', copy: 'Read the command-line, configuration and local-run reference.', path: '/docs/CLI_REFERENCE.md' },
      { icon: Cable, title: 'Channels & plugins', copy: 'Connect messaging channels, native plugins, skills and tools.', path: '/docs/zh/CHANNEL_PLUGIN_GUIDE.md' },
    ])

const documentUrl = (path: string) => `${config.public.agentUrl}/blob/main${path}`
</script>

<template>
  <div>
    <SiteHeader :locale="locale" path="/docs" />
    <main>
      <section class="relative overflow-hidden py-20 sm:py-28">
        <div class="hero-grid absolute inset-0 -z-10" />
        <div class="container-x">
          <NuxtLink :to="localPath(locale)" class="inline-flex items-center gap-2 text-sm font-bold text-bot-muted hover:text-bot"><ArrowLeft :size="16" />{{ locale === 'zh' ? '返回首页' : 'Back home' }}</NuxtLink>
          <div class="mt-14 max-w-3xl">
            <p class="eyebrow">JOYHOUSEBOT DOCS</p>
            <h1 class="mt-4 text-4xl font-bold leading-tight tracking-tight sm:text-6xl">{{ locale === 'zh' ? '把智能工作中心接入真实工作' : 'Put the intelligent work center into real work' }}</h1>
            <p class="mt-6 text-lg leading-8 text-bot-muted sm:text-xl">{{ locale === 'zh' ? '从部署、架构和命令行，到渠道、插件、Skills 与工具：文档帮助个人和团队把 JoyhouseBot 接入已有系统，并保持执行可配置、可观察、可治理。' : 'From deployment, architecture and the CLI to channels, plugins, skills and tools: the documentation helps people and teams connect JoyhouseBot to existing systems with configurable, observable and governable execution.' }}</p>
            <a :href="config.public.agentDocsUrl" target="_blank" rel="noopener" class="btn-primary mt-9">{{ locale === 'zh' ? '浏览全部文档' : 'Browse all documentation' }} <ArrowUpRight :size="18" /></a>
          </div>
        </div>
      </section>

      <section class="container-x pb-20 sm:pb-28">
        <div class="grid gap-5 md:grid-cols-2">
          <a v-for="item in docs" :key="item.title" :href="documentUrl(item.path)" target="_blank" rel="noopener" class="surface-card group p-7 transition hover:-translate-y-1 hover:border-bot/20 hover:shadow-float sm:p-8">
            <div class="flex items-start justify-between"><div class="grid h-12 w-12 place-items-center rounded-2xl bg-bot-soft text-bot"><component :is="item.icon" :size="23" /></div><ArrowUpRight :size="19" class="text-bot-muted transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-bot" /></div>
            <h2 class="mt-8 text-xl font-bold">{{ item.title }}</h2>
            <p class="mt-3 leading-7 text-bot-muted">{{ item.copy }}</p>
          </a>
        </div>
      </section>
    </main>
    <SiteFooter :locale="locale" />
  </div>
</template>
