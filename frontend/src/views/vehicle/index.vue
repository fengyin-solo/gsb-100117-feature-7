<template>
  <section class="page" data-module="vehicle">
    <header class="page-head">
      <div>
        <h2>养护车辆管理</h2>
        <p class="page-desc">维护养护车辆，围绕车辆编号、车辆类型、车牌号、所属单位做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记养护车辆</button>
        <button class="btn" type="button" @click="exportRows">导出养护车辆清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="panel">
      <div class="section-head">
        <h3 class="panel-title" style="margin: 0">洞群任务调度待办
          <span class="sub">计划签发生成、开始作业派出、现场收口归库，随编排台任务结论同步</span>
        </h3>
        <RouterLink class="btn" to="/tunnel_console">去编排台处理</RouterLink>
      </div>
      <div class="stat-row" style="margin-bottom: 10px">
        <article class="stat-card"><span class="stat-label">待派车</span><strong class="stat-value">{{ summary['待派车'] ?? 0 }}</strong></article>
        <article class="stat-card"><span class="stat-label">已派出</span><strong class="stat-value">{{ summary['已派出'] ?? 0 }}</strong></article>
        <article class="stat-card"><span class="stat-label">已归库</span><strong class="stat-value">{{ summary['已归库'] ?? 0 }}</strong></article>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>调度编号</th>
            <th>隧道</th>
            <th>检查班组</th>
            <th>车辆</th>
            <th>作业窗口</th>
            <th>状态</th>
            <th>派出/归库</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in dispatchRows" :key="String(row.id)">
            <td>{{ row['调度编号'] }}</td>
            <td>{{ row['洞群'] }} · {{ row['隧道名称'] }}<span v-if="row['封闭作业']" class="tag tag-red">封闭</span></td>
            <td>{{ row['检查类别'] }} · {{ row['检查班组'] }}</td>
            <td>{{ row['车辆类型'] }}（{{ row['车牌号'] }}）</td>
            <td>{{ row['作业开始'] }} ~ {{ row['作业结束'] }}</td>
            <td>
              <span class="tag" :class="row.status === '待派车' ? 'tag-amber' : row.status === '已派出' ? 'tag-blue' : 'tag-green'">
                {{ row.status }}
              </span>
            </td>
            <td class="muted-note">{{ row['派出时间'] || '—' }} / {{ row['归库时间'] || '—' }}</td>
          </tr>
          <tr v-if="!dispatchRows.length">
            <td colspan="7" class="empty-state">暂无洞群任务调度待办</td>
          </tr>
        </tbody>
      </table>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无养护车辆数据，可先登记养护车辆</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条养护车辆记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/vehicle'
const columns = ["车辆编号", "车辆类型", "车牌号", "所属单位", "年检日期", "驾驶员", "当前里程", "车辆状态"]
const actions = ["派车出车", "收车归库", "送修车辆"]
const statuses = ["在库", "出车作业", "维修", "报废"]
const stats = [{"label": "在库车辆", "value": 0}, {"label": "出车车辆", "value": 0}, {"label": "维修车辆", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const dispatchRows = ref<Row[]>([])
const summary = ref<Record<string, number>>({})

async function loadDispatch() {
  try {
    const response = await request('/api/tunnel-console/dispatch/summary')
    if (!response.ok) return
    const payload = (await response.json()) as Record<string, any>
    dispatchRows.value = (payload.items ?? []) as Row[]
    summary.value = Object.fromEntries(
      Object.entries(payload).filter(([key]) => key !== 'items').map(([key, value]) => [key, Number(value)]),
    )
  } catch {
    // 调度汇总是增强信息，读不到不影响车辆台账
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '养护车辆登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('养护车辆动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '养护车辆操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('养护车辆列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '养护车辆列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadDispatch()
})
</script>
