<template>
  <div class="skills-page">
    <div class="content-header">
      <h1 class="page-title">技能</h1>
      <p class="page-sub">内置与工作区技能，可启用/禁用（禁用后不会进入 Agent 上下文）</p>
    </div>
    <n-card size="small" class="skills-card">
      <div class="toolbar">
        <n-input
          v-model:value="searchText"
          placeholder="搜索名称或描述"
          clearable
          style="max-width: 240px"
        />
        <n-button quaternary size="small" @click="load">刷新</n-button>
      </div>
      <n-spin :show="loading">
        <n-data-table
          :columns="columns"
          :data="filteredSkills"
          :bordered="false"
          size="small"
          :row-key="(row: SkillItem) => row.name"
        />
      </n-spin>
      <n-empty v-if="!loading && filteredSkills.length === 0" description="暂无技能或无匹配结果" style="margin-top: 16px" />
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { h, ref, computed, onMounted } from 'vue'
import { NTag, NSwitch, useMessage } from 'naive-ui'
import { getSkills, patchSkill, type SkillItem } from '../api/skills'

const message = useMessage()
const loading = ref(true)
const searchText = ref('')
const skills = ref<SkillItem[]>([])

const filteredSkills = computed(() => {
  const q = searchText.value.trim().toLowerCase()
  if (!q) return skills.value
  return skills.value.filter(
    (s) =>
      s.name.toLowerCase().includes(q) ||
      (s.description && s.description.toLowerCase().includes(q))
  )
})

const columns = [
  {
    title: '名称',
    key: 'name',
    width: 140,
    ellipsis: { tooltip: true },
  },
  {
    title: '来源',
    key: 'source',
    width: 90,
    render(row: SkillItem) {
      return row.source === 'builtin' ? '内置' : 'workspace'
    },
  },
  {
    title: '描述',
    key: 'description',
    ellipsis: { tooltip: true },
  },
  {
    title: '可用',
    key: 'available',
    width: 80,
    render(row: SkillItem) {
      return row.available
        ? h(NTag, { type: 'success', size: 'small' }, () => '是')
        : h(NTag, { type: 'warning', size: 'small' }, () => '否')
    },
  },
  {
    title: '启用',
    key: 'enabled',
    width: 80,
    render(row: SkillItem) {
      return h(NSwitch, {
        value: row.enabled,
        onUpdateValue: (v: boolean) => toggleEnabled(row, v),
      })
    },
  },
]

async function load() {
  loading.value = true
  try {
    const res = await getSkills()
    if (res.ok && Array.isArray(res.skills)) skills.value = res.skills
    else skills.value = []
  } catch (e) {
    message.error(String(e))
    skills.value = []
  } finally {
    loading.value = false
  }
}

async function toggleEnabled(row: SkillItem, enabled: boolean) {
  try {
    await patchSkill(row.name, enabled)
    const idx = skills.value.findIndex((s) => s.name === row.name)
    if (idx >= 0) skills.value[idx].enabled = enabled
    message.success(enabled ? '已启用' : '已禁用')
  } catch (e) {
    message.error(String(e))
  }
}

onMounted(load)
</script>

<style scoped>
.skills-page {
  padding: 16px;
}
.content-header {
  margin-bottom: 16px;
}
.page-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0 0 4px 0;
}
.page-sub {
  font-size: 0.875rem;
  color: var(--text-muted, #666);
  margin: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.skills-card {
  max-width: 960px;
}
</style>
