<script setup lang="ts">
import { computed } from 'vue'
import type { BlueprintValidation, ConfigurationRolloutSummary } from '../../api/teams'
import type { ComposerMember } from '../../composables/useTeamComposer'

/** Step 4: publish readiness checks plus the Worker rollout/ACK state. */

const props = defineProps<{
  members: ComposerMember[]
  coordinatorMemberId: string
  validation: BlueprintValidation | null
  rollout: ConfigurationRolloutSummary | null
  saved: boolean
}>()

const ROLLOUT_LABELS: Record<string, string> = {
  rolling_out: 'Worker 预热中', awaiting_approval: '等待批准', completed: '已加载',
  failed: '预热失败', timed_out: '预热超时', cancelled: '已取消', rolled_back: '已回滚',
}

const checks = computed(() => {
  const items: Array<{ ok: boolean; label: string; detail: string }> = []
  const unpublished = props.members.filter((member) => !member.published)
  items.push({
    ok: props.members.length >= 2,
    label: '成员数量',
    detail: props.members.length >= 2 ? `${props.members.length} 名成员` : '至少需要 2 名成员',
  })
  items.push({
    ok: unpublished.length === 0,
    label: '成员版本锁定',
    detail: unpublished.length ? `未发布：${unpublished.map((item) => item.member_id).join('、')}` : '全部成员绑定已发布 Agent Revision',
  })
  const coordinator = props.members.find((member) => member.member_id === props.coordinatorMemberId)
  items.push({
    ok: Boolean(coordinator?.published),
    label: '协调者',
    detail: coordinator ? `${coordinator.member_id}（${coordinator.agent_id}）` : '尚未选择协调者',
  })
  items.push({
    ok: Boolean(props.validation?.ok),
    label: 'Blueprint 校验',
    detail: props.validation
      ? props.validation.ok
        ? `${props.validation.normalized?.phases.length || 0} 个阶段通过校验`
        : (props.validation.errors.map((item) => `${item.code}: ${item.message}`).join('；') || '未通过')
      : '尚未运行校验',
  })
  items.push({ ok: props.saved, label: '草稿保存', detail: props.saved ? '草稿已写入' : '尚未保存' })
  return items
})

const blocked = computed(() => checks.value.some((item) => !item.ok))
const rolloutPercent = computed(() => {
  const target = props.rollout?.target_worker_count || 0
  if (!target) return 100
  return Math.round(((props.rollout?.acknowledged_worker_count || 0) / target) * 100)
})
</script>

<template>
  <div class="publish-checks">
    <section class="panel">
      <div class="panel-heading"><div><span class="eyebrow">PUBLISH CHECKS</span><h3>发布检查</h3><p>所有检查通过后才能发布；发布会翻转当前版本指针并启动 Worker 预热。</p></div></div>
      <ul class="check-list">
        <li v-for="check in checks" :key="check.label" :class="{ ok: check.ok, bad: !check.ok }">
          <span class="check-mark">{{ check.ok ? '✓' : '✕' }}</span>
          <span class="check-copy"><strong>{{ check.label }}</strong><small>{{ check.detail }}</small></span>
        </li>
      </ul>
      <p v-if="blocked" class="check-note">存在未通过项：修复后再发布。</p>
    </section>
    <section class="panel">
      <div class="panel-heading"><div><span class="eyebrow">WORKER ROLLOUT</span><h3>Worker 加载确认</h3><p>发布即时生效；rollout 用于预热与 ACK，显示每个 Worker 是否已加载该版本。</p></div></div>
      <template v-if="rollout">
        <div class="rollout-progress">
          <div class="progress-track"><i :style="{ width: `${rolloutPercent}%` }" /></div>
          <span>{{ rollout.acknowledged_worker_count }} / {{ rollout.target_worker_count }} · {{ ROLLOUT_LABELS[rollout.status] || rollout.status }}</span>
        </div>
        <details class="rollout-targets">
          <summary>逐机状态</summary>
          <ul>
            <li v-for="target in rollout.targets || []" :key="target.worker_id">
              <code>{{ target.worker_id }}</code>
              <span :class="`target-${target.status}`">{{ target.status }}</span>
              <small v-if="target.error">· {{ (target.error as { message?: string }).message || '加载失败' }}</small>
            </li>
          </ul>
        </details>
      </template>
      <p v-else class="check-note">尚未发布过版本，暂无 rollout 记录。</p>
    </section>
  </div>
</template>

<style scoped>
.publish-checks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.check-list { display: grid; gap: 8px; margin: 0; padding: 0 14px 14px; list-style: none; }
.check-list li { display: flex; gap: 10px; align-items: flex-start; padding: 10px 11px; background: var(--surface); border: 1px solid var(--border); border-radius: 9px; }
.check-mark { display: grid; width: 20px; height: 20px; flex: none; place-items: center; border-radius: 6px; font: 600 10px var(--font-mono); }
.check-list li.ok .check-mark { color: var(--success); background: rgba(50, 182, 122, 0.12); }
.check-list li.bad .check-mark { color: var(--danger); background: rgba(226, 88, 62, 0.12); }
.check-copy { display: grid; gap: 2px; }
.check-copy strong { color: var(--text-strong); font-size: 10px; }
.check-copy small { color: var(--text-muted); font-size: 9px; line-height: 1.5; }
.check-note { margin: 0 14px 14px; color: var(--text-muted); font-size: 9px; }
.rollout-progress { display: grid; gap: 7px; padding: 0 14px; }
.rollout-progress span { color: var(--text-muted); font-size: 9px; }
.progress-track { height: 6px; overflow: hidden; background: var(--border); border-radius: 999px; }
.progress-track i { display: block; height: 100%; background: var(--accent); border-radius: inherit; transition: width 0.2s; }
.rollout-targets { margin: 10px 14px 14px; color: var(--text-muted); font-size: 9px; }
.rollout-targets summary { cursor: pointer; }
.rollout-targets ul { display: grid; gap: 4px; margin: 8px 0 0; padding: 0; list-style: none; }
.rollout-targets li { display: flex; gap: 7px; align-items: center; }
.rollout-targets code { font: 8px var(--font-mono); }
.target-loaded, .target-acknowledged { color: var(--success); }
.target-pending { color: var(--warning); }
.target-failed, .target-timed_out { color: var(--danger); }
@media (max-width: 1000px) { .publish-checks { grid-template-columns: 1fr; } }
</style>
