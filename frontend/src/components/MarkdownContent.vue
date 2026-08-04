<template>
  <div class="markdown-content" v-html="html" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps<{ content: string }>()

const html = computed(() => DOMPurify.sanitize(
  marked.parse(props.content, { async: false, breaks: true, gfm: true }) as string,
  {
    USE_PROFILES: { html: true },
    ADD_ATTR: ['target'],
  },
))
</script>

<style scoped>
.markdown-content { min-width: 0; color: var(--text); line-height: 1.75; overflow-wrap: anywhere; }
.markdown-content :deep(:first-child) { margin-top: 0; }
.markdown-content :deep(:last-child) { margin-bottom: 0; }
.markdown-content :deep(p) { margin: 0 0 11px; }
.markdown-content :deep(h1), .markdown-content :deep(h2), .markdown-content :deep(h3), .markdown-content :deep(h4) { margin: 18px 0 9px; color: var(--text-strong); line-height: 1.35; }
.markdown-content :deep(h1) { font-size: 19px; }
.markdown-content :deep(h2) { font-size: 16px; }
.markdown-content :deep(h3) { font-size: 14px; }
.markdown-content :deep(ul), .markdown-content :deep(ol) { margin: 0 0 12px; padding-left: 22px; }
.markdown-content :deep(li + li) { margin-top: 4px; }
.markdown-content :deep(a) { color: var(--accent); text-decoration: underline; text-underline-offset: 2px; }
.markdown-content :deep(code) { padding: 2px 5px; color: var(--text-strong); background: var(--surface-raised); border: 1px solid var(--border); border-radius: 4px; font: 0.88em var(--font-mono); }
.markdown-content :deep(pre) { margin: 12px 0; overflow: auto; padding: 13px 14px; color: #d9e1ec; background: #0b1018; border: 1px solid rgba(255,255,255,.08); border-radius: 9px; }
.markdown-content :deep(pre code) { padding: 0; color: inherit; background: transparent; border: 0; font-size: 11px; line-height: 1.65; }
.markdown-content :deep(blockquote) { margin: 12px 0; padding: 3px 0 3px 12px; color: var(--text-muted); border-left: 3px solid var(--accent-border); }
.markdown-content :deep(table) { display: block; max-width: 100%; overflow-x: auto; margin: 12px 0; border-collapse: collapse; font-size: 12px; }
.markdown-content :deep(th), .markdown-content :deep(td) { padding: 7px 9px; border: 1px solid var(--border); text-align: left; }
.markdown-content :deep(th) { color: var(--text-strong); background: var(--surface-raised); }
</style>
