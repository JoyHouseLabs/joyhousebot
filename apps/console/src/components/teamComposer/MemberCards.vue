<script setup lang="ts">
import type { AdminAgent } from '../../api/admin'
import type { ComposerMember } from '../../composables/useTeamComposer'

/** Step 1: pick published Agents and describe each member's responsibility. */

defineProps<{ members: ComposerMember[]; agents: AdminAgent[]; coordinatorMemberId: string }>()
const emit = defineEmits<{ add: [agent: AdminAgent]; remove: [memberId: string]; focus: [memberId: string] }>()

function selectable(agents: AdminAgent[]) {
  return agents.filter((agent) => agent.current_revision_id && agent.revision?.status === 'published' && agent.status === 'active')
}
</script>

<template>
  <div class="member-step">
    <div class="agent-pool panel">
      <div class="panel-heading"><div><span class="eyebrow">AGENT POOL</span><h3>选择已发布 Agent</h3><p>只有当前已发布版本的 Agent 才能进入 Team；发布时再次校验。</p></div></div>
      <div class="pool-list">
        <button v-for="agent in selectable(agents)" :key="agent.agent_id" type="button" class="pool-item" @click="emit('add', agent)">
          <span class="pool-mark">{{ (agent.name || agent.agent_id).slice(0, 1).toUpperCase() }}</span>
          <span class="pool-copy">
            <strong>{{ agent.name || agent.agent_id }}</strong>
            <small>{{ agent.agent_id }} · {{ agent.role }}</small>
            <em>{{ agent.description || '暂无描述' }}</em>
          </span>
          <span class="pool-add">＋ 加入</span>
        </button>
        <p v-if="!selectable(agents).length" class="empty-hint">没有已发布的 Agent。请先在 Agent 页面发布至少两个 Agent。</p>
      </div>
    </div>
    <div class="member-edit panel">
      <div class="panel-heading"><div><span class="eyebrow">MEMBERS</span><h3>成员与职责</h3><p>职责会注入每个成员的执行上下文；成员别名自动生成，通常无需修改。</p></div></div>
      <article v-for="member in members" :key="member.member_id" class="member-card">
        <header>
          <span class="member-mark">{{ (member.agentName || member.agent_id).slice(0, 1).toUpperCase() }}</span>
          <div class="member-title">
            <strong>{{ member.agentName }}</strong>
            <small>{{ member.agent_id }} · {{ member.published ? `已发布 ${member.agent_revision_id}` : '未发布' }}</small>
          </div>
          <label class="coordinator-toggle" :class="{ active: coordinatorMemberId === member.member_id }">
            <input type="radio" name="coordinator" :checked="coordinatorMemberId === member.member_id" @change="emit('focus', member.member_id)" />
            协调者
          </label>
          <button type="button" class="member-remove" @click="emit('remove', member.member_id)">移除</button>
        </header>
        <div class="member-fields">
          <label><span>成员别名</span><input :value="member.member_id" readonly /></label>
          <label><span>角色</span><input v-model="member.role" placeholder="如：课程设计专家" /></label>
        </div>
        <label class="member-duty"><span>职责说明</span><textarea v-model="member.responsibility" rows="2" placeholder="这个成员在协作中负责什么、边界在哪里" /></label>
      </article>
      <p v-if="members.length < 2" class="empty-hint">至少选择两名成员（其中一名将作为协调者）。</p>
    </div>
  </div>
</template>

<style scoped>
.member-step { display: grid; grid-template-columns: minmax(300px, 0.9fr) minmax(0, 1.4fr); gap: 14px; }
.pool-list { display: grid; gap: 8px; padding: 0 12px 12px; }
.pool-item { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 11px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 10px; text-align: left; cursor: pointer; }
.pool-item:hover { background: var(--surface-hover); border-color: var(--accent-border); }
.pool-mark { display: grid; width: 32px; height: 32px; place-items: center; color: var(--accent); background: var(--accent-subtle); border: 1px solid var(--accent-border); border-radius: 9px; font: 600 11px var(--font-mono); }
.pool-copy { display: grid; min-width: 0; gap: 2px; }
.pool-copy strong { overflow: hidden; color: var(--text-strong); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.pool-copy small { overflow: hidden; color: var(--text-muted); font: 8px var(--font-mono); text-overflow: ellipsis; white-space: nowrap; }
.pool-copy em { overflow: hidden; color: var(--text-muted); font-size: 9px; font-style: normal; text-overflow: ellipsis; white-space: nowrap; }
.pool-add { color: var(--accent); font: 9px var(--font-mono); }
.member-edit { display: grid; gap: 10px; align-content: start; }
.member-card { display: grid; gap: 9px; margin: 0 12px; padding: 13px; background: var(--surface); border: 1px solid var(--border); border-radius: 11px; }
.member-card header { display: flex; gap: 10px; align-items: center; }
.member-mark { display: grid; width: 32px; height: 32px; place-items: center; color: var(--accent); background: var(--accent-subtle); border: 1px solid var(--accent-border); border-radius: 9px; font: 600 11px var(--font-mono); }
.member-title { display: grid; flex: 1; min-width: 0; gap: 2px; }
.member-title strong { color: var(--text-strong); font-size: 11px; }
.member-title small { overflow: hidden; color: var(--text-muted); font: 8px var(--font-mono); text-overflow: ellipsis; white-space: nowrap; }
.coordinator-toggle { display: flex; gap: 5px; align-items: center; padding: 5px 8px; color: var(--text-muted); background: var(--surface-raised); border: 1px solid var(--border); border-radius: 999px; font-size: 9px; cursor: pointer; }
.coordinator-toggle.active { color: var(--accent); border-color: var(--accent-border); background: var(--accent-subtle); }
.member-remove { padding: 5px 8px; color: var(--danger); background: transparent; border: 0; font-size: 9px; cursor: pointer; }
.member-fields { display: grid; grid-template-columns: minmax(120px, 0.7fr) minmax(0, 1fr); gap: 9px; }
.member-fields label, .member-duty { display: grid; gap: 5px; }
.member-fields span, .member-duty span { color: var(--text-muted); font-size: 9px; }
input, textarea { width: 100%; padding: 8px 10px; color: var(--text); background: var(--input); border: 1px solid var(--border-strong); border-radius: 8px; outline: none; font-size: 10px; }
input[readonly] { color: var(--text-muted); font-family: var(--font-mono); }
textarea { resize: vertical; }
.empty-hint { padding: 12px; color: var(--text-muted); font-size: 10px; }
@media (max-width: 1100px) { .member-step { grid-template-columns: 1fr; } }
</style>
