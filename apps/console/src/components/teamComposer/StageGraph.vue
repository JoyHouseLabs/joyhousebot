<script setup lang="ts">
import { computed } from 'vue'
import type { StagePhase } from '../../api/plans'

/** Read-only collaboration stage graph; phases laid out by dependency level. */

const props = defineProps<{ phases: Array<Partial<StagePhase> & { id: string; kind: string; participants?: string[]; depends_on?: string[]; step_ids?: string[] }>; compact?: boolean }>()

const KIND_LABELS: Record<string, string> = {
  produce: 'PRODUCE 产出',
  review: 'REVIEW 复核',
  revise: 'REVISE 修订',
  synthesize: 'SYNTHESIZE 汇总',
  checkpoint: 'CHECKPOINT 检查点',
}

interface PhasePosition { phase: typeof props.phases[number]; x: number; y: number; level: number }

const positions = computed<PhasePosition[]>(() => {
  const levels = new Map<string, number>()
  const byId = new Map(props.phases.map((item) => [item.id, item]))
  const resolve = (id: string, guard: Set<string>): number => {
    if (levels.has(id)) return levels.get(id) || 0
    if (guard.has(id)) return 0
    guard.add(id)
    const deps = byId.get(id)?.depends_on || []
    const level = deps.length ? Math.max(...deps.map((dep) => resolve(dep, guard) + 1)) : 0
    levels.set(id, level)
    return level
  }
  for (const phase of props.phases) resolve(phase.id, new Set())
  const perLevel = new Map<number, number>()
  const result: PhasePosition[] = []
  for (const phase of props.phases) {
    const level = levels.get(phase.id) || 0
    const index = perLevel.get(level) || 0
    perLevel.set(level, index + 1)
    result.push({ phase, level, x: 34 + level * 252, y: 36 + index * 138 })
  }
  return result
})

const size = computed(() => {
  const maxLevel = Math.max(0, ...positions.value.map((item) => item.level))
  const counts = [...new Set(positions.value.map((item) => item.level))].map(
    (level) => positions.value.filter((item) => item.level === level).length,
  )
  return {
    width: Math.max(720, 34 + (maxLevel + 1) * 252),
    height: Math.max(props.compact ? 200 : 300, 36 + Math.max(1, ...counts) * 138),
  }
})

const edges = computed(() => {
  const map = new Map(positions.value.map((item) => [item.phase.id, item]))
  return props.phases.flatMap((phase) => {
    return (phase.depends_on || []).flatMap((dep) => {
      const source = map.get(dep)
      const target = map.get(phase.id)
      if (!source || !target) return []
      const x1 = source.x + 198
      const y1 = source.y + 52
      const x2 = target.x
      const y2 = target.y + 52
      const bend = Math.max(34, (x2 - x1) / 2)
      return [{ source: dep, target: phase.id, path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}` }]
    })
  })
})

function participants(phase: typeof props.phases[number]) { return (phase.participants || []).join('、') || '—' }
function stepCount(phase: typeof props.phases[number]) { return (phase.step_ids || []).length }
</script>

<template>
  <div class="stage-viewport">
    <div class="stage-canvas" :style="{ width: `${size.width}px`, height: `${size.height}px` }">
      <svg :viewBox="`0 0 ${size.width} ${size.height}`" aria-hidden="true">
        <defs><marker id="stage-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" /></marker></defs>
        <path v-for="edge in edges" :key="`${edge.source}-${edge.target}`" class="stage-edge" :d="edge.path" marker-end="url(#stage-arrow)" />
      </svg>
      <div
        v-for="position in positions"
        :key="position.phase.id"
        class="stage-node"
        :class="position.phase.kind"
      >
        <span class="stage-kind">{{ KIND_LABELS[position.phase.kind] || position.phase.kind }}</span>
        <strong>{{ position.phase.id }}</strong>
        <small>{{ participants(position.phase) }}</small>
        <em>{{ stepCount(position.phase) ? `${stepCount(position.phase)} 个计划步骤` : `${position.phase.mode || 'parallel'} · 等待计划` }}</em>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stage-viewport { height: 100%; overflow: auto; background-image: linear-gradient(var(--border) 1px, transparent 1px), linear-gradient(90deg, var(--border) 1px, transparent 1px); background-size: 24px 24px; background-position: -1px -1px; border-radius: 0 0 var(--radius-md) var(--radius-md); }
.stage-canvas { position: relative; min-width: 100%; min-height: 100%; }
.stage-canvas svg { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.stage-edge { fill: none; stroke: var(--border-strong); stroke-width: 1.5; }
.stage-canvas marker path { fill: var(--border-strong); }
.stage-node { position: absolute; display: flex; width: 198px; min-height: 104px; flex-direction: column; align-items: flex-start; padding: 12px; color: var(--text); background: var(--surface); border: 1px solid var(--border-strong); border-radius: 12px; box-shadow: 0 8px 18px rgba(0, 0, 0, 0.08); }
.stage-node.review, .stage-node.revise { border-style: dashed; }
.stage-kind { color: var(--accent); font: 8px var(--font-mono); letter-spacing: 0.08em; }
.stage-node.synthesize .stage-kind { color: var(--warning); }
.stage-node strong { max-width: 100%; margin-top: 6px; overflow: hidden; color: var(--text-strong); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.stage-node small { max-width: 100%; margin-top: 2px; overflow: hidden; color: var(--text-muted); font: 8px var(--font-mono); text-overflow: ellipsis; white-space: nowrap; }
.stage-node em { margin-top: auto; color: var(--text-muted); font-size: 8px; font-style: normal; }
</style>
