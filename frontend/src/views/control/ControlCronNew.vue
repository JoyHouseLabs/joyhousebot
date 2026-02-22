<template>
  <div class="control-page cron-new-page">
    <div class="content-header">
      <div class="header-row">
        <n-button quaternary size="small" @click="goBack">返回列表</n-button>
      </div>
      <h1 class="page-title">新建定时任务</h1>
      <p class="page-sub">创建调度(schedule)、会话目标、投递(delivery) 等</p>
    </div>

    <n-card size="small" class="cron-new-card">
      <n-form :model="form" label-placement="left" label-width="120" class="cron-new-form">
        <n-form-item label="名称" required>
          <n-input v-model:value="form.name" placeholder="任务名称" />
        </n-form-item>
        <n-form-item label="执行者 (Agent)">
          <n-select
            v-model:value="form.agentId"
            :options="agentOptions"
            placeholder="不选即默认 Agent 执行"
            clearable
            style="width: 100%; max-width: 360px"
          />
        </n-form-item>
        <n-form-item label="调度类型" required>
          <n-radio-group v-model:value="form.scheduleKind">
            <n-radio value="at">单次(at)</n-radio>
            <n-radio value="every">间隔(every)</n-radio>
            <n-radio value="cron">Cron</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="form.scheduleKind === 'at'" label="执行时间" required>
          <n-date-picker
            v-model:value="form.atMs"
            type="datetime"
            clearable
            placeholder="选择日期时间"
            style="width: 100%; max-width: 360px"
          />
        </n-form-item>
        <n-form-item v-if="form.scheduleKind === 'every'" label="间隔(秒)" required>
          <n-input-number v-model:value="form.everySeconds" :min="1" placeholder="例如 3600" style="width: 100%; max-width: 200px" />
        </n-form-item>
        <template v-if="form.scheduleKind === 'cron'">
          <n-form-item label="Cron 表达式" required>
            <n-input v-model:value="form.cronExpr" placeholder="0 9 * * *" style="max-width: 200px" />
          </n-form-item>
          <n-form-item label="时区(tz)">
            <n-input v-model:value="form.tz" placeholder="如 America/Los_Angeles，空为本地" style="max-width: 280px" />
          </n-form-item>
        </template>
        <n-form-item label="消息/指令" required>
          <n-input v-model:value="form.message" type="textarea" placeholder="触发时发给 Agent 的消息" :autosize="{ minRows: 2 }" style="max-width: 480px" />
        </n-form-item>
        <n-form-item label="投递(delivery)">
          <n-space vertical size="small" style="width: 100%">
            <n-space align="center">
              <n-switch v-model:value="form.deliver" />
              <span>执行后投递到通道</span>
            </n-space>
            <template v-if="form.deliver">
              <n-input v-model:value="form.channel" placeholder="通道 channel，如 telegram / slack" style="max-width: 280px" />
              <n-input v-model:value="form.to" placeholder="目标 to，如群组 ID / 手机号" style="max-width: 280px" />
            </template>
          </n-space>
        </n-form-item>
        <n-form-item v-if="form.scheduleKind === 'at'" label="单次执行后删除">
          <n-space align="center">
            <n-switch v-model:value="form.deleteAfterRun" />
            <span class="form-hint">执行成功后自动删除任务</span>
          </n-space>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space>
          <n-button @click="goBack">取消</n-button>
          <n-button type="primary" :loading="submitting" @click="submit">创建</n-button>
        </n-space>
      </template>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { addCronJob } from '../../api/cron'
import { useGatewayInject } from '../../composables/useGateway'
import { getAgents } from '../../api/agent'

const router = useRouter()
const message = useMessage()
const gateway = useGatewayInject()
const submitting = ref(false)
const agentOptions = ref<{ label: string; value: string }[]>([])

const form = ref({
  name: '',
  agentId: '',
  scheduleKind: 'every' as 'at' | 'every' | 'cron',
  atMs: null as number | null,
  everySeconds: 3600,
  cronExpr: '0 9 * * *',
  tz: '',
  message: '',
  deliver: false,
  channel: '',
  to: '',
  deleteAfterRun: true,
})

function goBack() {
  router.push({ name: 'ControlCron' })
}

async function submit() {
  const f = form.value
  if (!f.name.trim()) {
    message.warning('请输入任务名称')
    return
  }
  if (f.scheduleKind === 'at') {
    if (f.atMs == null || f.atMs <= 0) {
      message.warning('请选择执行时间')
      return
    }
  } else if (f.scheduleKind === 'every' && (!f.everySeconds || f.everySeconds < 1)) {
    message.warning('请输入有效间隔秒数')
    return
  } else if (f.scheduleKind === 'cron' && !f.cronExpr?.trim()) {
    message.warning('请输入 Cron 表达式')
    return
  }
  submitting.value = true
  try {
    let schedule: {
      kind: 'at' | 'every' | 'cron'
      at_ms?: number
      every_seconds?: number
      expr?: string
      tz?: string
    } = { kind: f.scheduleKind }
    if (f.scheduleKind === 'at' && f.atMs != null) schedule.at_ms = f.atMs
    else if (f.scheduleKind === 'every') schedule.every_seconds = f.everySeconds
    else if (f.scheduleKind === 'cron') {
      schedule.expr = f.cronExpr!.trim()
      if (f.tz?.trim()) schedule.tz = f.tz.trim()
    }
    await addCronJob({
      name: f.name.trim(),
      schedule,
      message: f.message.trim() || '',
      deliver: f.deliver,
      channel: f.channel?.trim() || undefined,
      to: f.to?.trim() || undefined,
      delete_after_run: f.scheduleKind === 'at' ? f.deleteAfterRun : false,
      agent_id: f.agentId?.trim() || undefined,
    })
    message.success('已创建')
    router.push({ name: 'ControlCron' })
    await gateway?.request?.('health').catch(() => {})
  } catch (e) {
    message.error(String(e))
  } finally {
    submitting.value = false
  }
}

async function loadAgentOptions() {
  try {
    const res = await getAgents()
    if (res.ok && Array.isArray(res.agents)) {
      agentOptions.value = res.agents.map((a) => ({
        label: a.is_default ? `${a.name} (${a.id}) [默认]` : `${a.name} (${a.id})`,
        value: a.id,
      }))
    } else {
      agentOptions.value = []
    }
  } catch {
    agentOptions.value = []
  }
}

onMounted(loadAgentOptions)
</script>

<style scoped>
.cron-new-page {
  max-width: 640px;
}
.header-row {
  margin-bottom: 8px;
}
.content-header {
  margin-bottom: 20px;
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
.cron-new-card {
  border: 1px solid var(--border);
}
.cron-new-form {
  max-width: 520px;
}
.form-hint {
  font-size: 12px;
  color: var(--text-muted, #999);
}
</style>
