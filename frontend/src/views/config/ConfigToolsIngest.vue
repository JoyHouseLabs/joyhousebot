<template>
  <div class="config-tools-block" v-if="ingest">
    <n-form label-placement="left" label-width="160" class="config-form">
      <n-form-item label="PDF 处理">
        <n-select
          v-model:value="ingest.pdf_processing"
          :options="[
            { label: 'local', value: 'local' },
            { label: 'cloud', value: 'cloud' },
            { label: 'auto', value: 'auto' },
          ]"
        />
      </n-form-item>
      <n-form-item label="图片处理">
        <n-select
          v-model:value="ingest.image_processing"
          :options="[
            { label: 'local', value: 'local' },
            { label: 'cloud', value: 'cloud' },
            { label: 'auto', value: 'auto' },
          ]"
        />
      </n-form-item>
      <n-form-item label="URL 处理">
        <n-select
          v-model:value="ingest.url_processing"
          :options="[
            { label: 'local', value: 'local' },
            { label: 'cloud', value: 'cloud' },
            { label: 'auto', value: 'auto' },
          ]"
        />
      </n-form-item>
      <n-form-item label="YouTube 处理">
        <n-select
          v-model:value="ingest.youtube_processing"
          :options="[
            { label: 'local_only', value: 'local_only' },
            { label: 'allow_cloud', value: 'allow_cloud' },
            { label: 'auto', value: 'auto' },
          ]"
        />
      </n-form-item>
      <n-form-item label="云 OCR 提供商">
        <n-input v-model:value="ingest.cloud_ocr_provider" placeholder="如 openai_vision" />
      </n-form-item>
      <n-form-item label="云 OCR API Key">
        <n-input
          v-model:value="ingest.cloud_ocr_api_key"
          type="password"
          show-password-on="click"
          placeholder="可选，或使用 providers 中的 key"
        />
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

const ingest = computed(() => {
  const tools = props.config?.tools as Record<string, unknown> | undefined
  const i = tools?.ingest as Record<string, unknown> | undefined
  if (!i) return null
  return i as {
    pdf_processing: string
    image_processing: string
    url_processing: string
    youtube_processing: string
    cloud_ocr_provider: string
    cloud_ocr_api_key: string
  }
})
</script>

<style scoped>
.config-form {
  max-width: 520px;
}
</style>
