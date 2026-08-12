<template>
  <div class="schema-form">
    <label v-for="field in fields" :key="field.name" class="schema-field">
      <span class="field-heading">
        <strong>{{ field.title }}</strong>
        <small v-if="field.required">必填</small>
      </span>

      <select
        v-if="field.enumValues.length"
        :value="enumValue(field)"
        @change="setEnum(field, ($event.target as HTMLSelectElement).value)"
      >
        <option value="">未设置</option>
        <option v-for="option in field.enumValues" :key="token(option)" :value="token(option)">
          {{ String(option) }}
        </option>
      </select>

      <select
        v-else-if="field.type === 'boolean'"
        :value="booleanValue(field.name)"
        @change="setBoolean(field.name, ($event.target as HTMLSelectElement).value)"
      >
        <option value="">未设置</option>
        <option value="true">是</option>
        <option value="false">否</option>
      </select>

      <input
        v-else-if="field.type === 'integer' || field.type === 'number'"
        type="number"
        :step="field.type === 'integer' ? 1 : 'any'"
        :min="numberConstraint(field.schema.minimum)"
        :max="numberConstraint(field.schema.maximum)"
        :value="scalarValue(field.name)"
        :placeholder="placeholder(field)"
        @input="setNumber(field, ($event.target as HTMLInputElement).value)"
      />

      <textarea
        v-else-if="field.type === 'array' || field.type === 'object'"
        rows="4"
        spellcheck="false"
        :value="structuredValue(field)"
        :placeholder="field.type === 'array' ? '[]' : '{}'"
        @change="setStructured(field, ($event.target as HTMLTextAreaElement).value)"
      />

      <input
        v-else
        type="text"
        :value="scalarValue(field.name)"
        :placeholder="placeholder(field)"
        @input="setString(field.name, ($event.target as HTMLInputElement).value)"
      />

      <small v-if="field.description" class="field-description">{{ field.description }}</small>
      <small v-if="fieldError(field.name)" class="field-error">{{ fieldError(field.name) }}</small>
    </label>
    <p v-if="!fields.length" class="empty-note">此能力没有可配置的非敏感运行参数。</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

type SchemaValue = Record<string, unknown>
type Field = {
  name: string
  title: string
  description: string
  type: string
  required: boolean
  enumValues: unknown[]
  schema: SchemaValue
}

const props = defineProps<{ modelValue: SchemaValue; schema: SchemaValue }>()
const emit = defineEmits<{ 'update:modelValue': [value: SchemaValue] }>()
const errors = ref<Record<string, string>>({})

const fields = computed<Field[]>(() => {
  const properties = props.schema.properties
  const required = new Set(Array.isArray(props.schema.required) ? props.schema.required.map(String) : [])
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return []
  return Object.entries(properties as Record<string, unknown>).map(([name, raw]) => {
    const schema = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as SchemaValue : {}
    return {
      name,
      title: String(schema.title || humanize(name)),
      description: String(schema.description || ''),
      type: String(schema.type || 'string'),
      required: required.has(name),
      enumValues: Array.isArray(schema.enum) ? schema.enum : [],
      schema,
    }
  })
})

function humanize(value: string) {
  return value.split('_').map((item) => item ? `${item[0].toUpperCase()}${item.slice(1)}` : '').join(' ')
}
function token(value: unknown) { return JSON.stringify(value) }
function scalarValue(name: string) { const value = props.modelValue[name]; return value == null ? '' : String(value) }
function hasValue(name: string) { return Object.prototype.hasOwnProperty.call(props.modelValue, name) }
function enumValue(field: Field) { return hasValue(field.name) ? token(props.modelValue[field.name]) : '' }
function booleanValue(name: string) { return hasValue(name) ? String(Boolean(props.modelValue[name])) : '' }
function numberConstraint(value: unknown) { return typeof value === 'number' ? value : undefined }
function placeholder(field: Field) { return field.schema.default == null ? '' : `默认：${String(field.schema.default)}` }
function structuredValue(field: Field) {
  const value = props.modelValue[field.name]
  if (value == null) return ''
  return JSON.stringify(value, null, 2)
}
function fieldError(name: string) { return errors.value[name] || '' }
function update(name: string, value: unknown, remove = false) {
  const next = { ...props.modelValue }
  if (remove) delete next[name]
  else next[name] = value
  errors.value = { ...errors.value, [name]: '' }
  emit('update:modelValue', next)
}
function setEnum(field: Field, value: string) { update(field.name, value ? JSON.parse(value) : undefined, !value) }
function setBoolean(name: string, value: string) { update(name, value === 'true', !value) }
function setString(name: string, value: string) { update(name, value, value === '') }
function setNumber(field: Field, value: string) {
  if (!value) { update(field.name, undefined, true); return }
  const parsed = field.type === 'integer' ? Number.parseInt(value, 10) : Number(value)
  if (Number.isFinite(parsed)) update(field.name, parsed)
}
function setStructured(field: Field, value: string) {
  if (!value.trim()) { update(field.name, undefined, true); return }
  try {
    const parsed = JSON.parse(value)
    if (field.type === 'array' && !Array.isArray(parsed)) throw new Error('必须是 JSON 数组')
    if (field.type === 'object' && (!parsed || Array.isArray(parsed) || typeof parsed !== 'object')) throw new Error('必须是 JSON 对象')
    update(field.name, parsed)
  } catch (cause) {
    errors.value = { ...errors.value, [field.name]: cause instanceof Error ? cause.message : 'JSON 无效' }
  }
}
</script>

<style scoped>
.schema-form{display:grid;gap:14px}.schema-field{display:grid;gap:7px}.field-heading{display:flex;align-items:center;justify-content:space-between;gap:8px}.field-heading strong{color:var(--text-strong);font-size:11px}.field-heading small{padding:2px 5px;color:var(--warning);background:color-mix(in srgb,var(--warning) 12%,transparent);border-radius:4px;font-size:8px}.schema-field input,.schema-field select,.schema-field textarea{width:100%;box-sizing:border-box;padding:10px;color:var(--text);background:var(--input);border:1px solid var(--border-strong);border-radius:9px;font:11px var(--font-mono);line-height:1.45}.schema-field textarea{resize:vertical}.field-description,.empty-note{margin:0;color:var(--text-muted);font-size:9px;line-height:1.55}.field-error{color:var(--danger);font-size:9px}
</style>
