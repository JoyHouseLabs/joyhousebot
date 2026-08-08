<script setup lang="ts">
import { ArrowUpRight, Menu, X } from 'lucide-vue-next'
import { siteCopy } from '~/data/site'
import { alternatePath, localPath, type Locale } from '~/utils/routes'

const props = defineProps<{ locale: Locale; path?: string }>()
const copy = computed(() => siteCopy[props.locale])
const open = ref(false)
</script>

<template>
  <header class="sticky top-0 z-40 border-b border-black/5 bg-bot-warm/90 backdrop-blur-xl">
    <div class="container-x flex h-16 items-center justify-between py-3">
      <NuxtLink :to="localPath(locale)" aria-label="JoyhouseBot home"><BrandMark /></NuxtLink>

      <nav class="hidden items-center gap-7 text-sm font-medium text-bot-muted lg:flex">
        <NuxtLink :to="localPath(locale, '/agent')" class="transition hover:text-bot">{{ copy.nav.agent }}</NuxtLink>
        <NuxtLink :to="localPath(locale, '/extension')" class="transition hover:text-bot">{{ copy.nav.extension }}</NuxtLink>
        <NuxtLink :to="localPath(locale, '/hardware')" class="transition hover:text-bot">{{ copy.nav.hardware }}</NuxtLink>
        <NuxtLink :to="localPath(locale, '/docs')" class="transition hover:text-bot">{{ copy.nav.docs }}</NuxtLink>
      </nav>

      <div class="flex items-center gap-2">
        <NuxtLink :to="alternatePath(locale, path || '/')" class="rounded-full px-3 py-2 text-sm font-semibold text-bot-muted transition hover:bg-white hover:text-bot">
          {{ copy.language }}
        </NuxtLink>
        <a href="https://app.joyhouse.chat" target="_blank" rel="noopener" class="btn-primary hidden !px-4 !py-2.5 text-sm sm:inline-flex">
          {{ copy.openHouse }} <ArrowUpRight :size="15" />
        </a>
        <button class="grid h-10 w-10 place-items-center rounded-full border border-black/5 bg-white lg:hidden" :aria-label="open ? 'Close menu' : 'Open menu'" @click="open = !open">
          <X v-if="open" :size="19" /><Menu v-else :size="19" />
        </button>
      </div>
    </div>

    <nav v-if="open" class="container-x grid gap-2 border-t border-black/5 py-4 text-sm font-semibold lg:hidden" @click="open = false">
      <NuxtLink :to="localPath(locale, '/agent')" class="rounded-xl px-3 py-2 hover:bg-white">{{ copy.nav.agent }}</NuxtLink>
      <NuxtLink :to="localPath(locale, '/extension')" class="rounded-xl px-3 py-2 hover:bg-white">{{ copy.nav.extension }}</NuxtLink>
      <NuxtLink :to="localPath(locale, '/hardware')" class="rounded-xl px-3 py-2 hover:bg-white">{{ copy.nav.hardware }}</NuxtLink>
      <NuxtLink :to="localPath(locale, '/docs')" class="rounded-xl px-3 py-2 hover:bg-white">{{ copy.nav.docs }}</NuxtLink>
    </nav>
  </header>
</template>
