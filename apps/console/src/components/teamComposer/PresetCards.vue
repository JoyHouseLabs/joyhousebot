<script setup lang="ts">
import type { BlueprintPreset } from '../../api/teams'

/** Step 2: choose one explainable collaboration preset and bind members. */

const props = defineProps<{ presets: BlueprintPreset[]; modelValue: string; bindings: Record<string, string[]>; members: string[]; coordinatorMemberId: string }>()
const emit = defineEmits<{ 'update:modelValue': [preset: string]; 'update:bindings': [bindings: Record<string, string[]>] }>()

const SLOT_LABELS: Record<string, string> = {
  producers: '产出者', reviewers: '复核者', chain: '交接链（有序）',
  challengers: '挑战者', monitors: '监控', diagnosticians: '诊断', executors: '执行', verifiers: '验证',
}

function slots(preset: BlueprintPreset | undefined) {
  const names = preset?.bindings || []
  return names.filter((name) => name !== 'chain' || preset?.preset === 'sequential_handoff')
}

function toggle(preset: string, slot: string, memberId: string) {
  const next = { ...props.bindings }
  const current = new Set(next[slot] || [])
  if (preset === 'sequential_handoff' && slot === 'chain') {
    // Ordered chain: click toggles membership at the end.
    if (current.has(memberId)) current.delete(memberId)
    else current.add(memberId)
    next[slot] = props.members.filter((id) => current.has(id))
  } else if (current.has(memberId)) {
    current.delete(memberId)
    next[slot] = [...current]
  } else {
    current.add(memberId)
    next[slot] = [...current]
  }
  emit('update:bindings', next)
}

function inSlot(slot: string, memberId: string) { return (props.bindings[slot] || []).includes(memberId) }
</script>

<template>
  <div class="preset-step">
    <div class="preset-cards">
      <button
        v-for="preset in presets"
        :key="preset.preset"
        type="button"
        class="preset-card"
        :class="{ selected: modelValue === preset.preset }"
        @click="emit('update:modelValue', preset.preset)"
      >
        <header>
          <span class="preset-flow">
            <em v-for="(phase, index) in preset.phase_template" :key="phase.id + index" :class="phase.kind">{{ phase.id }}</em>
          </span>
          <strong>{{ preset.label }}</strong>
        </header>
        <p>{{ preset.guidance }}</p>
        <small>{{ preset.preset }}</small>
      </button>
    </div>
    <div class="binding-panel panel">
      <div class="panel-heading"><div><span class="eyebrow">ROLE BINDINGS</span><h3>把成员绑定到协作角色</h3><p>复核者不能同时是产出者；未绑定的成员不参与该阶段的计划。</p></div></div>
      <div v-for="slot in slots(presets.find((item) => item.preset === modelValue))" :key="slot" class="binding-slot">
        <strong>{{ SLOT_LABELS[slot] || slot }} <code>{{ slot }}</code></strong>
        <div class="binding-members">
          <button
            v-for="member in members.filter((id) => id !== coordinatorMemberId)"
            :key="member"
            type="button"
            class="binding-chip"
            :class="{ active: inSlot(slot, member) }"
            @click="toggle(modelValue, slot, member)"
          >
            {{ member }}
            <i v-if="modelValue === 'sequential_handoff' && slot === 'chain'">{{ (bindings.chain || []).indexOf(member) + 1 }}</i>
          </button>
        </div>
      </div>
      <p class="empty-hint">协调者（{{ coordinatorMemberId }}）固定负责汇总阶段。</p>
    </div>
  </div>
</template>

<style scoped>
.preset-step { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(300px, 0.9fr); gap: 14px; }
.preset-cards { display: grid; gap: 10px; }
.preset-card { display: grid; gap: 7px; padding: 14px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 12px; text-align: left; cursor: pointer; }
.preset-card:hover { background: var(--surface-hover); }
.preset-card.selected { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-subtle); }
.preset-card header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.preset-flow { display: flex; gap: 4px; align-items: center; }
.preset-flow em { padding: 3px 6px; color: var(--text-muted); background: var(--surface-raised); border-radius: 5px; font: 8px var(--font-mono); font-style: normal; }
.preset-flow em.review, .preset-flow em.revise { color: var(--warning); }
.preset-flow em.synthesize { color: var(--accent); }
.preset-card strong { color: var(--text-strong); font-size: 12px; }
.preset-card p { margin: 0; color: var(--text-muted); font-size: 10px; line-height: 1.6; }
.preset-card small { color: var(--accent); font: 8px var(--font-mono); }
.binding-panel { display: grid; gap: 12px; align-content: start; }
.binding-slot { display: grid; gap: 7px; margin: 0 12px; }
.binding-slot > strong { color: var(--text-strong); font-size: 10px; }
.binding-slot code { color: var(--text-muted); font: 8px var(--font-mono); }
.binding-members { display: flex; flex-wrap: wrap; gap: 6px; }
.binding-chip { display: flex; gap: 5px; align-items: center; padding: 6px 9px; color: var(--text-muted); background: var(--surface); border: 1px solid var(--border); border-radius: 999px; font: 9px var(--font-mono); cursor: pointer; }
.binding-chip.active { color: var(--accent); border-color: var(--accent-border); background: var(--accent-subtle); }
.binding-chip i { font-style: normal; opacity: 0.7; }
.empty-hint { padding: 0 12px 12px; color: var(--text-muted); font-size: 9px; }
@media (max-width: 1100px) { .preset-step { grid-template-columns: 1fr; } }
</style>
