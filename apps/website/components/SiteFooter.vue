<script setup lang="ts">
import { siteCopy } from '~/data/site'
import { localPath, type Locale } from '~/utils/routes'

const props = defineProps<{ locale: Locale }>()
const copy = computed(() => siteCopy[props.locale])
const config = useRuntimeConfig()
</script>

<template>
  <footer class="mt-20 border-t border-black/5 bg-white/65">
    <div class="container-x grid gap-10 py-12 md:grid-cols-[1.5fr_1fr_1fr_1fr]">
      <div>
        <BrandMark />
        <p class="mt-4 max-w-md text-sm leading-7 text-bot-muted">{{ copy.footer.summary }}</p>
      </div>
      <div>
        <p class="text-sm font-bold">{{ copy.footer.product }}</p>
        <ul class="mt-4 space-y-3 text-sm text-bot-muted">
          <li><NuxtLink :to="localPath(locale, '/extension')" class="hover:text-bot">Extension</NuxtLink></li>
          <li><NuxtLink :to="localPath(locale, '/agent')" class="hover:text-bot">Agent</NuxtLink></li>
          <li><NuxtLink :to="localPath(locale, '/hardware')" class="hover:text-bot">Hardware</NuxtLink></li>
        </ul>
      </div>
      <div>
        <p class="text-sm font-bold">{{ copy.footer.company }}</p>
        <ul class="mt-4 space-y-3 text-sm text-bot-muted">
          <li><a :href="config.public.visionUrl" target="_blank" rel="noopener" class="hover:text-bot">JoyHouse · {{ locale === 'zh' ? '数据与智能' : 'Data & Intelligence' }}</a></li>
          <li><a :href="config.public.agentUrl" target="_blank" rel="noopener" class="hover:text-bot">JoyhouseBot Agent · GitHub</a></li>
          <li><NuxtLink :to="localPath(locale, '/docs')" class="hover:text-bot">{{ locale === 'zh' ? '开发文档' : 'Documentation' }}</NuxtLink></li>
          <li><a :href="config.public.extensionRepoUrl" target="_blank" rel="noopener" class="hover:text-bot">Browser Extension · GitHub</a></li>
          <li><NuxtLink :to="localPath(locale, '/terms')" class="hover:text-bot">{{ locale === 'zh' ? '服务条款' : 'Terms' }}</NuxtLink></li>
        </ul>
      </div>
      <div>
        <p class="text-sm font-bold">{{ copy.footer.contact }}</p>
        <ul class="mt-4 space-y-3 text-sm text-bot-muted">
          <li><a :href="`mailto:${config.public.supportEmail}`" class="hover:text-bot">{{ config.public.supportEmail }}</a></li>
          <li><NuxtLink :to="localPath(locale, '/support')" class="hover:text-bot">{{ locale === 'zh' ? '帮助与常见问题' : 'Help & FAQ' }}</NuxtLink></li>
          <li><NuxtLink :to="localPath(locale, '/privacy')" class="hover:text-bot">{{ locale === 'zh' ? '隐私政策' : 'Privacy' }}</NuxtLink></li>
        </ul>
      </div>
    </div>
    <div class="container-x flex flex-col gap-2 border-t border-black/5 py-6 text-xs text-bot-faint sm:flex-row sm:items-center sm:justify-between">
      <p>© {{ new Date().getFullYear() }} JoyHouse.</p>
      <p>{{ copy.footer.rights }}</p>
    </div>
  </footer>
</template>
