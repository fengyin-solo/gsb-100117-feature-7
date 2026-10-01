<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal">
      <header class="modal-head">
        <h3>现场收口确认 · 任务 #{{ task.id }}</h3>
        <button class="link" type="button" @click="$emit('close')">关闭</button>
      </header>

      <div class="modal-body">
        <p class="modal-sub">
          {{ task.隧道名称 }} · {{ task.检查类别 }}检查（{{ task.检查班组 }}）
          ｜{{ task.作业开始 }} ~ {{ task.作业结束 }}
        </p>

        <table class="data-table">
          <thead>
            <tr>
              <th>检查项（{{ task.检查班组 }}原班组留痕）</th>
              <th style="width: 200px">检查结论</th>
              <th>情况说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in checklist" :key="item.检查项目">
              <td>{{ item.检查项目 }}</td>
              <td>
                <label class="radio-inline" v-for="result in results" :key="result">
                  <input
                    type="radio"
                    :name="`result-${item.检查项目}`"
                    :value="result"
                    v-model="item.结果"
                  />
                  {{ result }}
                </label>
              </td>
              <td>
                <input v-model="item.说明" class="cell-input" placeholder="可填写现场说明" />
              </td>
            </tr>
          </tbody>
        </table>

        <div class="form-grid">
          <label class="form-item">
            <span>任务总评结论 *</span>
            <select v-model="form.收口结论">
              <option value="" disabled>请选择</option>
              <option v-for="result in results" :key="result" :value="result">{{ result }}</option>
            </select>
          </label>
          <label class="form-item">
            <span>现场收口负责人 *</span>
            <input v-model="form.收口负责人" :placeholder="task.现场负责人 ? `默认：${task.现场负责人}` : '填写负责人姓名'" />
          </label>
          <label class="form-item form-item-wide">
            <span>收口说明</span>
            <input v-model="form.说明" placeholder="遗留问题、复查安排等" />
          </label>
        </div>
        <p v-if="error" class="error-text">{{ error }}</p>
      </div>

      <footer class="modal-foot">
        <button class="btn" type="button" @click="$emit('close')">取消</button>
        <button class="btn primary" type="button" :disabled="submitting" @click="submit">
          {{ submitting ? '提交中…' : '确认收口并回写' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'

interface CheckRow {
  检查项目: string
  结果: string
  说明: string
}

const props = defineProps<{
  task: Record<string, any>
  meta: { results: string[] }
  checklistApi: Array<Record<string, any>>
  onSubmit: (payload: Record<string, any>) => Promise<boolean>
}>()

defineEmits<{ (e: 'close'): void }>()

const results = props.meta.results
const checklist = ref<CheckRow[]>([])
const submitting = ref(false)
const error = ref('')
const form = reactive({ 收口结论: '', 收口负责人: '', 说明: '' })

watch(
  () => props.task.id,
  () => {
    checklist.value = props.checklistApi
      .filter((row) => row.task_id === props.task.id)
      .map((row) => ({ 检查项目: String(row.检查项目), 结果: '', 说明: '' }))
    form.收口结论 = ''
    form.收口负责人 = String(props.task.现场负责人 ?? '')
    form.说明 = ''
    error.value = ''
  },
  { immediate: true },
)

async function submit() {
  error.value = ''
  if (checklist.value.some((item) => !item.结果)) {
    error.value = '请逐项给出检查结论后再收口'
    return
  }
  if (!form.收口结论 || !form.收口负责人.trim()) {
    error.value = '请选择任务总评结论并填写现场收口负责人'
    return
  }
  submitting.value = true
  try {
    const ok = await props.onSubmit({
      action: '现场收口',
      收口结论: form.收口结论,
      收口负责人: form.收口负责人.trim(),
      说明: form.说明.trim(),
      检查结果: checklist.value.map((item) => ({ ...item })),
    })
    if (ok === false) error.value = '提交未生效，请检查页面提示后重试'
  } finally {
    submitting.value = false
  }
}
</script>
