<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal">
      <header class="modal-head">
        <h3>{{ mode === 'approve' ? '现场负责人批准' : '调整作业窗口' }} · 任务 #{{ task.id }}</h3>
        <button class="link" type="button" @click="$emit('close')">关闭</button>
      </header>
      <div class="modal-body">
        <p class="modal-sub">
          {{ task.隧道名称 }} · {{ task.检查类别 }}检查（{{ task.检查班组 }}）
        </p>
        <table class="data-table">
          <tbody>
            <tr>
              <th style="width: 120px">当前作业窗口</th>
              <td>{{ task.作业开始 }} ~ {{ task.作业结束 }}<span v-if="task.封闭作业">（封闭作业）</span></td>
            </tr>
            <tr v-if="task.冲突说明">
              <th>冲突提示</th>
              <td class="text-danger">{{ task.冲突说明 }}。已批准时段不会被本任务覆盖，请改窗避让。</td>
            </tr>
          </tbody>
        </table>

        <div class="form-grid">
          <label v-if="mode === 'adjust'" class="form-item">
            <span>作业开始 *</span>
            <input v-model="form.作业开始" placeholder="YYYY-MM-DD HH:MM" />
          </label>
          <label v-if="mode === 'adjust'" class="form-item">
            <span>作业结束 *</span>
            <input v-model="form.作业结束" placeholder="YYYY-MM-DD HH:MM" />
          </label>
          <label class="form-item">
            <span>{{ mode === 'approve' ? '现场负责人（批准人）*' : '操作人' }}</span>
            <input v-model="form.操作人" :placeholder="task.现场负责人 ? `默认：${task.现场负责人}` : '填写姓名'" />
          </label>
          <label class="form-item form-item-wide">
            <span>{{ mode === 'approve' ? '批准说明' : '调整说明' }}</span>
            <input v-model="form.说明" :placeholder="mode === 'approve' ? '如：服从封闭作业计划，以已批准时段为准' : '改窗原因'" />
          </label>
        </div>
        <p v-if="error" class="error-text">{{ error }}</p>
      </div>
      <footer class="modal-foot">
        <button class="btn" type="button" @click="$emit('close')">取消</button>
        <button class="btn primary" type="button" :disabled="submitting" @click="submit">
          {{ submitting ? '提交中…' : mode === 'approve' ? '确认批准' : '提交调整' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'

const props = defineProps<{
  task: Record<string, any>
  mode: 'approve' | 'adjust'
  onSubmit: (values: Record<string, any>) => Promise<boolean>
}>()

defineEmits<{ (e: 'close'): void }>()

const submitting = ref(false)
const error = ref('')
const form = reactive({ 作业开始: '', 作业结束: '', 操作人: '', 说明: '' })

watch(
  () => props.task.id,
  () => {
    form.作业开始 = String(props.task.作业开始 ?? '')
    form.作业结束 = String(props.task.作业结束 ?? '')
    form.操作人 = String(props.task.现场负责人 ?? '')
    form.说明 = ''
    error.value = ''
  },
  { immediate: true },
)

async function submit() {
  error.value = ''
  if (props.mode === 'adjust' && (!form.作业开始 || !form.作业结束)) {
    error.value = '请填写完整的作业窗口'
    return
  }
  if (!form.操作人.trim()) {
    error.value = '请填写现场负责人姓名'
    return
  }
  submitting.value = true
  try {
    const ok = await props.onSubmit({ ...form })
    if (ok === false) error.value = '提交未生效，请检查页面提示后重试'
  } finally {
    submitting.value = false
  }
}
</script>
