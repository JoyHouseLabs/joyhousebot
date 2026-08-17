<script setup lang="ts">
import type { BlueprintGuardrails } from '../../api/teams'

/** Step 3: guardrails — concurrency, review, and human confirmations. */

defineProps<{ guardrails: BlueprintGuardrails; maxParallelBudget: number }>()
const emit = defineEmits<{ 'update:guardrails': [value: BlueprintGuardrails] }>()
</script>

<template>
  <div class="guardrails panel">
    <div class="panel-heading"><div><span class="eyebrow">GUARDRAILS</span><h3>协作护栏</h3><p>护栏会冻结进 Team Revision；Coordinator 计划越界时先收到结构化反馈，仍越界则运行失败关闭。</p></div></div>
    <div class="guardrail-grid">
      <label class="guardrail-field">
        <span>最大并行任务</span>
        <input
          type="number"
          min="1"
          :max="maxParallelBudget"
          :value="guardrails.max_parallel_tasks"
          @change="emit('update:guardrails', { ...guardrails, max_parallel_tasks: Math.max(1, Math.min(maxParallelBudget, Number(($event.target as HTMLInputElement).value) || 1)) })"
        />
        <small>不能超过团队预算上限 {{ maxParallelBudget }}</small>
      </label>
      <label class="guardrail-toggle" :class="{ on: guardrails.require_review }">
        <input type="checkbox" :checked="guardrails.require_review" @change="emit('update:guardrails', { ...guardrails, require_review: ($event.target as HTMLInputElement).checked })" />
        <span><strong>必须包含独立复核</strong><small>计划缺少 review 阶段时要求 Coordinator 重写</small></span>
      </label>
      <label class="guardrail-toggle" :class="{ on: guardrails.require_plan_confirmation }">
        <input type="checkbox" :checked="guardrails.require_plan_confirmation" @change="emit('update:guardrails', { ...guardrails, require_plan_confirmation: ($event.target as HTMLInputElement).checked })" />
        <span><strong>执行前人工确认计划</strong><small>Run 进入 waiting_input，确认后才物化 Task DAG</small></span>
      </label>
      <label class="guardrail-toggle" :class="{ on: guardrails.require_final_confirmation }">
        <input type="checkbox" :checked="guardrails.require_final_confirmation" @change="emit('update:guardrails', { ...guardrails, require_final_confirmation: ($event.target as HTMLInputElement).checked })" />
        <span><strong>最终输出前人工确认</strong><small>沿用结果审批链（approval_policy）</small></span>
      </label>
    </div>
  </div>
</template>

<style scoped>
.guardrails { display: grid; gap: 12px; }
.guardrail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 0 14px 14px; }
.guardrail-field { display: grid; gap: 6px; }
.guardrail-field span { color: var(--text-strong); font-size: 10px; font-weight: 600; }
.guardrail-field input { width: 100%; padding: 9px 11px; color: var(--text); background: var(--input); border: 1px solid var(--border-strong); border-radius: 8px; font-size: 11px; }
.guardrail-field small { color: var(--text-muted); font-size: 9px; }
.guardrail-toggle { display: flex; gap: 10px; align-items: flex-start; padding: 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; cursor: pointer; }
.guardrail-toggle.on { border-color: var(--accent-border); background: var(--accent-subtle); }
.guardrail-toggle input { margin-top: 2px; accent-color: var(--accent); }
.guardrail-toggle span { display: grid; gap: 3px; }
.guardrail-toggle strong { color: var(--text-strong); font-size: 10px; }
.guardrail-toggle small { color: var(--text-muted); font-size: 9px; line-height: 1.5; }
@media (max-width: 900px) { .guardrail-grid { grid-template-columns: 1fr; } }
</style>
