<template>
  <section class="page" data-module="tunnel">
    <header class="page-head">
      <div>
        <h2>隧道管养台账</h2>
        <p class="page-desc">
          隧道基础台账，洞群任务的现场收口结论会回写本表（最近任务、结论与留痕）；
          批量编排请前往「洞群任务编排台」。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn primary" to="/tunnel_console">打开洞群编排台</RouterLink>
        <button class="btn" type="button" @click="exportRows">导出隧道台账</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>隧道编号</span>
        <input v-model="keyword" placeholder="按隧道编号检索" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="keyword = ''; reload()">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>任务结论回写</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <template v-if="row['最近任务']">
              <div>{{ row['最近任务'] }}</div>
              <span :class="row['最近结论'] === '合格' ? 'text-ok' : 'text-warn'">结论：{{ row['最近结论'] }}</span>
              <div class="muted-note">{{ row['最近收口时间'] }}</div>
              <button class="link" type="button" @click="openTrace(row)">留痕（{{ ((row['任务留痕'] as Row[]) ?? []).length }}）</button>
            </template>
            <span v-else class="muted-note">暂无编排任务</span>
          </td>
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
          <td :colspan="columns.length + 2" class="empty-state">暂无隧道数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条隧道台账记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="traceTunnel" class="modal-mask" @click.self="traceTunnel = null">
      <div class="modal">
        <header class="modal-head">
          <h3>任务留痕 · {{ traceTunnel['隧道名称'] }}</h3>
          <button class="link" type="button" @click="traceTunnel = null">关闭</button>
        </header>
        <div class="modal-body">
          <table class="data-table">
            <thead>
              <tr><th>任务</th><th>类别/原班组</th><th>作业窗口</th><th>收口结论</th><th>负责人/时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="(trace, idx) in traceTunnel['任务留痕'] ?? []" :key="idx">
                <td>{{ trace['任务编号'] }}</td>
                <td>{{ trace['检查类别'] }}检查 · {{ trace['检查班组'] }}</td>
                <td>{{ trace['作业开始'] }}<br />{{ trace['作业结束'] }}</td>
                <td :class="trace['收口结论'] === '合格' ? 'text-ok' : 'text-warn'">{{ trace['收口结论'] }}<br /><span class="muted-note">{{ trace['收口说明'] }}</span></td>
                <td>{{ trace['收口负责人'] }}<br /><span class="muted-note">{{ trace['收口时间'] }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | Row[]>

const ENDPOINT = '/api/tunnel'
const columns = ['隧道编号', '隧道名称', '洞群', '隧道长度', '通风方式', '照明方式', '消防设施', '最近定检', '隧道状态']
const actions = ['设置限速', '安排维修', '封闭交通']
const stats = ref([{ label: '隧道总数', value: 0 }, { label: '封闭中', value: 0 }, { label: '维修/限速', value: 0 }])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const traceTunnel = ref<Row | null>(null)

function openTrace(row: Row) {
  traceTunnel.value = row
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!payload.ok) {
      throw new Error(payload.message)
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '隧道动作未生效'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  query.set('size', '200')
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('隧道台账读取失败')
    }
    const payload = (await response.json()) as { items: Row[]; total: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '隧道总数', value: rows.value.length },
      { label: '封闭中', value: rows.value.filter((row) => row['隧道状态'] === '封闭').length },
      { label: '维修/限速', value: rows.value.filter((row) => ['维修', '限速'].includes(String(row['隧道状态']))).length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '隧道台账读取失败'
  }
}

onMounted(reload)
</script>
