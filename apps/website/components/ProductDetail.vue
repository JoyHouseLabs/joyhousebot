<script setup lang="ts">
import { ArrowLeft, ArrowRight, ArrowUpRight, Bot, Check, Chrome, Cpu, Download, ExternalLink, Mail } from 'lucide-vue-next'
import { productMeta, siteCopy } from '~/data/site'
import { localPath, type Locale } from '~/utils/routes'

type Product = keyof typeof productMeta
const props = defineProps<{ locale: Locale; product: Product }>()
const copy = computed(() => siteCopy[props.locale])
const content = computed(() => productMeta[props.product][props.locale])
const config = useRuntimeConfig()
const icon = computed(() => props.product === 'extension' ? Chrome : props.product === 'agent' ? Bot : Cpu)

const primary = computed(() => {
  if (props.product === 'extension') return {
    label: props.locale === 'zh' ? '下载 Chrome 扩展' : 'Download Chrome extension',
    href: config.public.extensionDownloadUrl,
  }
  if (props.product === 'agent') return { label: copy.value.agent.action, href: config.public.agentUrl }
  return { label: copy.value.hardware.action, href: `mailto:${config.public.supportEmail}?subject=JoyhouseBot Hardware` }
})
</script>

<template>
  <div>
    <SiteHeader :locale="locale" :path="`/${product}`" />
    <main>
      <section class="relative overflow-hidden py-20 sm:py-28">
        <div class="hero-grid absolute inset-0 -z-20" />
        <div class="container-x">
          <NuxtLink :to="localPath(locale)" class="inline-flex items-center gap-2 text-sm font-bold text-bot-muted hover:text-bot"><ArrowLeft :size="16" />{{ locale === 'zh' ? '返回首页' : 'Back home' }}</NuxtLink>
          <div class="mt-14 grid items-center gap-12 lg:grid-cols-[1.05fr_.95fr]">
            <div>
              <div class="grid h-16 w-16 place-items-center rounded-2xl bg-bot-soft text-bot"><component :is="icon" :size="31" /></div>
              <p class="eyebrow mt-8">{{ content.eyebrow }}</p>
              <h1 class="mt-4 max-w-4xl text-4xl font-bold leading-tight tracking-tight sm:text-6xl">{{ content.title }}</h1>
              <p class="mt-6 max-w-2xl text-lg leading-8 text-bot-muted sm:text-xl">{{ content.intro }}</p>
              <a :href="primary.href" :target="product === 'hardware' ? undefined : '_blank'" rel="noopener" class="btn-primary mt-9">{{ primary.label }} <ArrowUpRight :size="18" /></a>
            </div>
            <div v-if="product === 'extension'" class="surface-card overflow-hidden p-3 shadow-float"><img src="/images/extension-sidepanel.png" alt="JoyhouseBot Extension" class="max-h-[580px] w-full rounded-[1.15rem] object-cover object-top" /></div>
            <div v-else-if="product === 'agent'" class="surface-card overflow-hidden p-3 shadow-float sm:p-4"><WorkCenterCarousel :locale="locale" class="group" /></div>
            <figure v-else class="surface-card overflow-hidden p-3 shadow-float sm:p-4"><img src="/images/joyhousebot-hardware-concept-v1.png" :alt="locale === 'zh' ? 'JoyhouseBot 硬件概念：桌面智能工作外设' : 'JoyhouseBot hardware concept: a desktop intelligent-work peripheral'" class="max-h-[640px] w-full rounded-[1.15rem] object-cover object-center" /><figcaption class="px-2 pb-1 pt-4 text-sm leading-6 text-bot-muted">{{ locale === 'zh' ? '概念图：一台能放在桌上的 JOY。具体产品形态仍在探索与验证中。' : 'Concept image: a JOY that can sit on your desk. The final device form remains under exploration and validation.' }}</figcaption></figure>
          </div>
        </div>
      </section>

      <section class="border-y border-black/5 bg-white/70 py-20 sm:py-24">
        <div class="container-x grid gap-5 md:grid-cols-2">
          <article v-for="(item, index) in content.highlights" :key="item[0]" class="surface-card p-7 sm:p-8">
            <div class="flex items-center justify-between"><span class="text-sm font-black text-bot">0{{ index + 1 }}</span><Check :size="20" class="text-bot" /></div>
            <h2 class="mt-8 text-xl font-bold">{{ item[0] }}</h2>
            <p class="mt-3 leading-7 text-bot-muted">{{ item[1] }}</p>
          </article>
        </div>
      </section>

      <section v-if="product === 'extension'" class="container-x py-20 sm:py-24">
        <div class="grid gap-10 rounded-panel border border-bot/10 bg-bot-soft/60 p-7 sm:p-10 lg:grid-cols-[.88fr_1.12fr] lg:p-12">
          <div>
            <p class="eyebrow">CHROME · MANUAL INSTALL</p>
            <h2 class="mt-3 text-3xl font-bold leading-tight sm:text-4xl">{{ locale === 'zh' ? '下载发布包，三步装进 Chrome' : 'Download the release, install it in Chrome in three steps' }}</h2>
            <p class="mt-5 max-w-xl leading-8 text-bot-muted">{{ locale === 'zh' ? '扩展当前通过 GitHub Releases 分发。下载后请先解压，再通过 Chrome 的“加载已解压的扩展程序”安装；不要直接把 ZIP 文件拖进浏览器。' : 'The extension is currently distributed through GitHub Releases. Download and unzip it first, then install it through Chrome’s “Load unpacked” flow—do not drag the ZIP into the browser.' }}</p>
            <div class="mt-8 flex flex-wrap gap-3">
              <a :href="config.public.extensionDownloadUrl" target="_blank" rel="noopener" class="btn-primary"><Download :size="18" />{{ locale === 'zh' ? '下载最新发布包' : 'Download latest release' }}</a>
              <a :href="config.public.extensionReleasesUrl" target="_blank" rel="noopener" class="btn-secondary"><ExternalLink :size="17" />GitHub Releases</a>
            </div>
          </div>
          <ol class="grid gap-4">
            <li class="surface-card flex gap-4 p-5"><span class="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-bot text-sm font-black text-white">1</span><div><h3 class="font-bold">{{ locale === 'zh' ? '下载并解压发布包' : 'Download and unzip the release' }}</h3><p class="mt-1 text-sm leading-6 text-bot-muted">{{ locale === 'zh' ? '下载 ZIP 后解压到一个固定位置，保留其中的 manifest.json 与全部文件。' : 'Unzip the download to a stable location, keeping manifest.json and every included file together.' }}</p></div></li>
            <li class="surface-card flex gap-4 p-5"><span class="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-bot text-sm font-black text-white">2</span><div><h3 class="font-bold">{{ locale === 'zh' ? '打开扩展管理页' : 'Open the extensions page' }}</h3><p class="mt-1 text-sm leading-6 text-bot-muted">{{ locale === 'zh' ? '在 Chrome 地址栏输入 chrome://extensions，并打开右上角的“开发者模式”。' : 'Enter chrome://extensions in Chrome, then enable Developer mode in the upper-right corner.' }}</p></div></li>
            <li class="surface-card flex gap-4 p-5"><span class="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-bot text-sm font-black text-white">3</span><div><h3 class="font-bold">{{ locale === 'zh' ? '加载已解压的扩展程序' : 'Load the unpacked extension' }}</h3><p class="mt-1 text-sm leading-6 text-bot-muted">{{ locale === 'zh' ? '点击“加载已解压的扩展程序”，选择刚才解压后的文件夹；随后点击浏览器工具栏中的 JoyhouseBot 图标即可开始。' : 'Click “Load unpacked”, select the extracted folder, then use the JoyhouseBot icon in the browser toolbar to begin.' }}</p></div></li>
          </ol>
        </div>
      </section>

      <section class="container-x py-20 sm:py-24">
        <div class="rounded-panel bg-bot-gradient px-7 py-12 text-white sm:px-12 sm:py-14">
          <div class="flex flex-col items-start justify-between gap-8 lg:flex-row lg:items-end">
            <div class="max-w-2xl"><h2 class="text-3xl font-bold">{{ locale === 'zh' ? '让每一种入口，都连接同一套数据与智能' : 'Let every entry point connect to the same data and intelligence' }}</h2><p class="mt-4 leading-7 text-white/75">{{ locale === 'zh' ? '浏览器扩展、开源 Agent Runtime 与未来硬件共享同一个方向：在真实工作现场接入智能，把理解、行动、作品和反馈带回 JoyHouse，并在你的确认下走向分享。' : 'Browser overlays, the open Agent Runtime and future hardware share one direction: attach intelligence to real work, then bring understanding, action, work and feedback back to JoyHouse for the sharing you choose.' }}</p></div>
            <a :href="primary.href" :target="product === 'hardware' ? undefined : '_blank'" rel="noopener" class="inline-flex items-center gap-2 rounded-full bg-white px-6 py-3.5 font-bold text-bot">{{ primary.label }} <ArrowRight :size="18" /></a>
          </div>
        </div>
      </section>
    </main>
    <SiteFooter :locale="locale" />
  </div>
</template>
