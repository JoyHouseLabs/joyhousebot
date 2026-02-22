<template>
  <div class="agents-page">
    <div class="agents-header">
      <div class="agents-header-left">
        <h1 class="agents-title">Agents</h1>
        <p class="agents-subtitle">{{ agents.length }} configured.</p>
      </div>
      <n-button quaternary size="small" @click="load" :loading="loading">刷新</n-button>
    </div>
    <div class="agents-layout">
      <aside class="agents-list-panel">
        <div v-if="loading && !agents.length" class="agents-list-loading">
          <n-spin size="small" />
        </div>
        <template v-else-if="agents.length">
          <div
            v-for="a in agents"
            :key="a.id"
            class="agent-card"
            :class="{ selected: selectedAgent?.id === a.id }"
            @click="selectedAgent = a"
          >
            <div class="agent-card-avatar">{{ (a.name || a.id).charAt(0).toUpperCase() }}</div>
            <div class="agent-card-body">
              <div class="agent-card-name">{{ a.name || a.id }}</div>
              <div class="agent-card-id">{{ a.id }}</div>
            </div>
            <n-tag v-if="a.is_default" size="tiny" round class="agent-card-default">DEFAULT</n-tag>
          </div>
        </template>
        <n-empty v-else description="暂无 Agent" size="small" />
      </aside>
      <main class="agents-detail-panel">
        <template v-if="selectedAgent">
          <div class="detail-header">
            <div class="detail-header-left">
              <div class="detail-avatar">{{ (selectedAgent.name || selectedAgent.id).charAt(0).toUpperCase() }}</div>
              <div>
                <h2 class="detail-name">{{ selectedAgent.name || selectedAgent.id }}</h2>
                <p class="detail-desc">Agent workspace and routing.</p>
              </div>
            </div>
            <n-tag v-if="selectedAgent.is_default" size="small" round type="success">DEFAULT</n-tag>
          </div>
          <n-tabs v-model:value="activeTab" type="line" size="medium" class="detail-tabs">
            <n-tab-pane name="overview" tab="Overview">
              <div class="tab-section">
                <h3 class="tab-section-title">Overview</h3>
                <p class="tab-section-desc">Workspace paths and identity metadata.</p>
                <n-descriptions :column="1" bordered size="small" class="overview-desc">
                  <n-descriptions-item label="是否激活">
                    <n-switch
                      :value="selectedAgentActivated"
                      @update:value="(v: boolean) => setAgentActivated(selectedAgent!, v)"
                    />
                    <span class="overview-activated-hint">{{ selectedAgentActivated ? '激活（对话页可选）' : '未激活（对话页不显示）' }}</span>
                  </n-descriptions-item>
                  <n-descriptions-item label="Workspace">{{ selectedAgent.workspace }}</n-descriptions-item>
                  <n-descriptions-item label="Default">{{ selectedAgent.is_default ? 'yes' : 'no' }}</n-descriptions-item>
                  <n-descriptions-item label="Primary Model">{{ selectedAgent.model }}</n-descriptions-item>
                  <n-descriptions-item label="Provider">{{ selectedAgent.provider_name }}</n-descriptions-item>
                  <n-descriptions-item label="Temperature">{{ selectedAgent.temperature }}</n-descriptions-item>
                  <n-descriptions-item label="Max tokens">{{ selectedAgent.max_tokens }}</n-descriptions-item>
                  <n-descriptions-item label="Memory window">{{ selectedAgent.memory_window }}</n-descriptions-item>
                </n-descriptions>
              </div>
            </n-tab-pane>
            <n-tab-pane name="files" tab="Files">
              <div class="tab-section">
                <h3 class="tab-section-title">Core Files</h3>
                <p class="tab-section-desc">Bootstrap persona, identity, and tool guidance.</p>
                <p class="tab-section-meta">Workspace: <code>{{ selectedAgent.workspace }}</code></p>
                <n-alert type="info" class="tab-stub">Workspace 核心文件（如 AGENTS.md、SOUL.md、IDENTITY.md）需在配置目录或文件系统中编辑。</n-alert>
              </div>
            </n-tab-pane>
            <n-tab-pane name="tools" tab="Tools">
              <div class="tab-section">
                <h3 class="tab-section-title">Tool Access</h3>
                <p class="tab-section-desc">Profile + per-tool overrides for this agent.</p>
                <n-alert type="info" class="tab-stub">工具为全局配置，可在「设置 → 配置」中查看；后续可支持按 Agent 的 tool 开关。</n-alert>
              </div>
            </n-tab-pane>
            <n-tab-pane name="skills" tab="Skills">
              <div class="tab-section skills-tab">
                <div class="skills-header">
                  <div>
                    <h3 class="tab-section-title">Skills</h3>
                    <p class="tab-section-desc">当前 Agent 可见的技能（全局配置）. {{ enabledCount }}/{{ skillsList.length }}</p>
                  </div>
                  <div class="skills-actions">
                    <n-button size="small" secondary @click="useAllSkills">全部启用</n-button>
                    <n-button size="small" secondary @click="disableAllSkills">全部禁用</n-button>
                    <n-button size="small" quaternary @click="loadSkills" :loading="skillsLoading">刷新</n-button>
                  </div>
                </div>
                <n-alert type="info" class="skills-banner" v-if="skillsList.length">
                  禁用某技能后，该技能不会进入 Agent 的 system prompt。当前为全局配置，对所有 Agent 生效。
                </n-alert>
                <div class="skills-filter">
                  <span class="skills-filter-label">Filter</span>
                  <n-input
                    v-model:value="skillSearch"
                    placeholder="Search skills"
                    clearable
                    size="small"
                    class="skills-search"
                  />
                  <span class="skills-filter-count">{{ filteredSkills.length }} shown</span>
                </div>
                <template v-if="skillsLoading && !skillsList.length">
                  <n-spin size="medium" style="margin-top: 24px" />
                </template>
                <template v-else>
                  <div v-if="builtinSkills.length" class="skills-group">
                    <button type="button" class="skills-group-title" @click="builtinSkillsCollapsed = !builtinSkillsCollapsed">
                      BUILT-IN SKILLS <span class="skills-group-count">{{ builtinSkills.length }} {{ builtinSkillsCollapsed ? '▶' : '▼' }}</span>
                    </button>
                    <div v-show="!builtinSkillsCollapsed" class="skill-cards">
                      <div v-for="s in builtinSkills" :key="s.name" class="skill-card">
                        <div class="skill-card-icon">S</div>
                        <div class="skill-card-body">
                          <div class="skill-card-name">{{ s.name }}</div>
                          <div class="skill-card-desc">{{ s.description || s.name }}</div>
                          <div class="skill-card-tags">
                            <n-tag size="tiny" round>{{ s.source || 'builtin' }}</n-tag>
                            <n-tag v-if="!s.available" size="tiny" round type="warning">blocked</n-tag>
                          </div>
                          <div v-if="!s.available" class="skill-card-missing">Missing dependency</div>
                        </div>
                        <n-switch :value="s.enabled" @update:value="(v: boolean) => toggleSkill(s, v)" class="skill-card-toggle" />
                      </div>
                    </div>
                  </div>
                  <div v-if="workspaceSkills.length" class="skills-group">
                    <button type="button" class="skills-group-title" @click="workspaceSkillsCollapsed = !workspaceSkillsCollapsed">
                      WORKSPACE SKILLS <span class="skills-group-count">{{ workspaceSkills.length }} {{ workspaceSkillsCollapsed ? '▶' : '▼' }}</span>
                    </button>
                    <div v-show="!workspaceSkillsCollapsed" class="skill-cards">
                      <div v-for="s in workspaceSkills" :key="s.name" class="skill-card">
                        <div class="skill-card-icon">W</div>
                        <div class="skill-card-body">
                          <div class="skill-card-name">{{ s.name }}</div>
                          <div class="skill-card-desc">{{ s.description || s.name }}</div>
                          <div class="skill-card-tags">
                            <n-tag size="tiny" round>{{ s.source || 'workspace' }}</n-tag>
                            <n-tag v-if="!s.available" size="tiny" round type="warning">blocked</n-tag>
                          </div>
                          <div v-if="!s.available" class="skill-card-missing">Missing dependency</div>
                        </div>
                        <n-switch :value="s.enabled" @update:value="(v: boolean) => toggleSkill(s, v)" class="skill-card-toggle" />
                      </div>
                    </div>
                  </div>
                  <n-empty v-if="filteredSkills.length === 0" description="无匹配技能" style="margin-top: 24px" />
                </template>
              </div>
            </n-tab-pane>
          </n-tabs>
        </template>
        <div v-else class="detail-empty">
          <n-empty description="从左侧选择一个 Agent 查看配置" />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { getAgents, patchAgent, type AgentListItem } from '../api/agent'
import { getSkills, patchSkill, type SkillItem } from '../api/skills'

const loading = ref(true)
const agents = ref<AgentListItem[]>([])
const selectedAgent = ref<AgentListItem | null>(null)
const activeTab = ref<string>('overview')

const skillsList = ref<SkillItem[]>([])
const skillsLoading = ref(false)
const skillSearch = ref('')
const builtinSkillsCollapsed = ref(false)
const workspaceSkillsCollapsed = ref(false)

const filteredSkills = computed(() => {
  const q = (skillSearch.value || '').trim().toLowerCase()
  if (!q) return skillsList.value
  return skillsList.value.filter(
    (s) =>
      s.name.toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q)
  )
})
const builtinSkills = computed(() =>
  filteredSkills.value.filter((s) => (s.source || '').toLowerCase() === 'builtin' || (s.source || '').toLowerCase() === 'openclaw-bundled' || (s.source || '').toLowerCase() === 'bundle')
)
const workspaceSkills = computed(() =>
  filteredSkills.value.filter((s) => {
    const src = (s.source || '').toLowerCase()
    return src !== 'builtin' && src !== 'openclaw-bundled' && src !== 'bundle'
  })
)
const enabledCount = computed(() => skillsList.value.filter((s) => s.enabled).length)

const selectedAgentActivated = computed(() => selectedAgent.value?.activated !== false)

async function setAgentActivated(agent: AgentListItem, activated: boolean) {
  try {
    await patchAgent(agent.id, { activated })
    const idx = agents.value.findIndex((a) => a.id === agent.id)
    if (idx >= 0) agents.value[idx] = { ...agents.value[idx], activated }
    if (selectedAgent.value?.id === agent.id) selectedAgent.value = { ...selectedAgent.value, activated }
  } catch {
    // keep UI unchanged on error
  }
}

async function loadSkills() {
  skillsLoading.value = true
  try {
    const res = await getSkills()
    skillsList.value = res.ok && Array.isArray(res.skills) ? res.skills : []
  } catch {
    skillsList.value = []
  } finally {
    skillsLoading.value = false
  }
}

function toggleSkill(skill: SkillItem, enabled: boolean) {
  const idx = skillsList.value.findIndex((s) => s.name === skill.name)
  if (idx >= 0) skillsList.value[idx] = { ...skillsList.value[idx], enabled }
  patchSkill(skill.name, enabled).catch(() => {
    if (idx >= 0) skillsList.value[idx] = { ...skillsList.value[idx], enabled: !enabled }
  })
}

async function useAllSkills() {
  for (const s of skillsList.value) {
    if (!s.enabled) {
      const idx = skillsList.value.findIndex((x) => x.name === s.name)
      if (idx >= 0) skillsList.value[idx] = { ...skillsList.value[idx], enabled: true }
      await patchSkill(s.name, true).catch(() => {})
    }
  }
}

async function disableAllSkills() {
  for (const s of skillsList.value) {
    if (s.enabled) {
      const idx = skillsList.value.findIndex((x) => x.name === s.name)
      if (idx >= 0) skillsList.value[idx] = { ...skillsList.value[idx], enabled: false }
      await patchSkill(s.name, false).catch(() => {})
    }
  }
}

watch(activeTab, (tab) => {
  if (tab === 'skills') loadSkills()
})

async function load() {
  loading.value = true
  try {
    const res = await getAgents()
    agents.value = res.ok && Array.isArray(res.agents) ? res.agents : []
    if (agents.value.length && !selectedAgent.value) {
      selectedAgent.value = agents.value.find((a) => a.is_default) ?? agents.value[0]
    }
  } catch {
    agents.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.agents-page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--shell-topbar-height, 56px) - 48px);
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}
.agents-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.agents-title {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0 0 2px 0;
}
.agents-subtitle {
  font-size: 0.875rem;
  color: var(--text-muted);
  margin: 0;
}
.agents-layout {
  flex: 1;
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 0;
  min-height: 0;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
  overflow: hidden;
}
.agents-list-panel {
  border-right: 1px solid var(--border);
  padding: 12px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.agents-list-loading {
  padding: 24px;
  display: flex;
  justify-content: center;
}
.agent-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  border: 2px solid transparent;
}
.agent-card:hover {
  background: var(--bg-hover);
}
.agent-card.selected {
  background: var(--accent-soft-bg, var(--accent-subtle));
  border-color: var(--accent-soft, var(--accent));
}
.agent-card-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--accent-soft, var(--accent));
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 600;
  flex-shrink: 0;
}
.agent-card-body {
  flex: 1;
  min-width: 0;
}
.agent-card-name {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-card-id {
  font-size: 12px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-card-default {
  flex-shrink: 0;
}
.agents-detail-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.detail-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.detail-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.detail-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--accent-soft, var(--accent));
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: 600;
  flex-shrink: 0;
}
.detail-name {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0 0 2px 0;
}
.detail-desc {
  font-size: 0.875rem;
  color: var(--text-muted);
  margin: 0;
}
.detail-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0 20px;
  overflow: hidden;
}
/* 温暖橘色：当前选中的 tab 文字与下划线（替代 Naive UI 默认红色/primary） */
.detail-tabs :deep(.n-tabs) {
  --n-bar-color: var(--accent);
  --n-tab-text-color-active-line: var(--accent);
  --n-tab-text-color-hover-line: var(--accent-hover);
}
.detail-tabs :deep(.n-tabs-tab.n-tabs-tab--active),
.detail-tabs :deep(.n-tabs-tab-pad) {
  color: var(--accent);
}
.detail-tabs :deep(.n-tabs-bar) {
  background-color: var(--accent);
}
.detail-tabs :deep(.n-tabs-tab:hover) {
  color: var(--accent-hover);
}
.detail-tabs :deep(.n-tabs-pane-wrapper) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-bottom: 20px;
}
.detail-tabs :deep(.n-tab-pane) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-bottom: 20px;
}
.tab-section {
  padding-top: 16px;
}
.tab-section-title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 4px 0;
}
.tab-section-desc {
  font-size: 0.875rem;
  color: var(--text-muted);
  margin: 0 0 12px 0;
}
.tab-section-meta {
  font-size: 0.875rem;
  margin: 0 0 12px 0;
}
.tab-section-meta code {
  background: var(--bg-hover);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.8125rem;
}
.overview-desc {
  max-width: 480px;
}
.overview-activated-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--text-muted);
}
.tab-stub {
  max-width: 560px;
}
.tab-link {
  color: var(--accent);
  font-size: 0.875rem;
}
.tab-link:hover {
  text-decoration: underline;
}

/* Skills tab */
.skills-tab {
  max-width: 720px;
}
.skills-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.skills-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.skills-banner {
  margin-bottom: 12px;
}
.skills-filter {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.skills-filter-label {
  font-size: 0.875rem;
  color: var(--text-muted);
}
.skills-search {
  width: 220px;
}
.skills-filter-count {
  font-size: 0.875rem;
  color: var(--text-muted);
}
.skills-group {
  margin-bottom: 20px;
}
.skills-group-title {
  display: block;
  width: 100%;
  text-align: left;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: var(--text-muted);
  margin: 0 0 8px 0;
  padding: 4px 0;
  border: none;
  background: none;
  cursor: pointer;
  border-radius: 4px;
}
.skills-group-title:hover {
  color: var(--text-color);
  background: var(--bg-hover);
}
.skills-group-count {
  font-weight: 400;
}
.skill-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skill-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--card);
}
.skill-card-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--bg-hover);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
}
.skill-card-body {
  flex: 1;
  min-width: 0;
}
.skill-card-name {
  font-weight: 600;
  font-size: 0.9375rem;
  margin-bottom: 4px;
}
.skill-card-desc {
  font-size: 0.8125rem;
  color: var(--text-muted);
  line-height: 1.4;
  margin-bottom: 6px;
}
.skill-card-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.skill-card-missing {
  font-size: 0.75rem;
  color: var(--warning);
}
.skill-card-toggle {
  flex-shrink: 0;
  margin-top: 2px;
}

.detail-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}
</style>
