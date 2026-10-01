<template>
  <section class="page" data-module="tunnel_console">
    <header class="page-head">
      <div>
        <h2>洞群任务编排台</h2>
        <p class="page-desc">
          多选隧道按通风、消防、照明检查项拆分班组任务；计划签发 → 作业执行 → 现场收口单向推进，
          收口结论同步回写隧道台账、设施检查清单与车辆调度汇总。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reloadAll">刷新数据</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">待签发任务</span>
        <strong class="stat-value">{{ taskStatusCount('待签发') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">冲突待裁</span>
        <strong class="stat-value text-danger">{{ tasks.filter((t) => t.冲突待裁).length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">作业中 / 待收口</span>
        <strong class="stat-value">{{ taskStatusCount('作业中') + taskStatusCount('待收口') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待派车 / 已派出</span>
        <strong class="stat-value">{{ dispatchSummary.待派车 ?? 0 }} / {{ dispatchSummary.已派出 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">本周期已收口</span>
        <strong class="stat-value text-ok">{{ taskStatusCount('已收口') }}</strong>
      </article>
    </div>

    <nav class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- ========================= 编排生成 + 批次队列 ========================= -->
    <div v-if="activeTab === 'compose'">
      <section class="panel">
        <h3 class="panel-title">第一步：选择隧道（可跨洞群多选）
          <span class="sub">已选 {{ selectedTunnels.length }} 座</span>
        </h3>
        <div v-for="group in tunnelGroups" :key="group.洞群" class="tunnel-group">
          <p class="group-name">{{ group.洞群 }}</p>
          <div class="check-grid">
            <label
              v-for="tunnel in group.tunnels"
              :key="tunnel.id"
              class="check-card"
              :class="{ selected: selectedIds.includes(tunnel.id) }"
            >
              <input type="checkbox" :value="tunnel.id" v-model="selectedIds" />
              <strong>{{ tunnel.隧道名称 }}</strong>
              <span class="muted">{{ tunnel.隧道编号 }} · {{ tunnel.隧道长度 }} · 负责人 {{ tunnel.现场负责人 }}</span>
              <span class="row-gap">
                <span class="tag" :class="statusTag(tunnel.status)">{{ tunnel.status }}</span>
                <span class="muted">{{ tunnel.洞群 }}</span>
              </span>
            </label>
          </div>
        </div>

        <h3 class="panel-title" style="margin-top: 14px">第二步：选择检查类别（每类对应固定班组与检查项）</h3>
        <div class="check-grid">
          <label
            v-for="category in meta.categories ?? []"
            :key="category"
            class="check-card"
            :class="{ selected: form.检查类别.includes(category) }"
          >
            <span class="row-gap">
              <input type="checkbox" :value="category" v-model="form.检查类别" />
              <strong>{{ category }}检查</strong>
              <span class="tag tag-blue">{{ crewOf(category) }}</span>
            </span>
            <span v-for="item in (meta.check_items?.[category] ?? [])" :key="item" class="muted">
              · {{ item }}
            </span>
          </label>
        </div>

        <h3 class="panel-title" style="margin-top: 14px">第三步：作业窗口与现场要求</h3>
        <div class="form-grid">
          <label class="form-item">
            <span>作业开始 *</span>
            <input v-model="form.作业开始" placeholder="YYYY-MM-DD HH:MM" />
          </label>
          <label class="form-item">
            <span>作业结束 *</span>
            <input v-model="form.作业结束" placeholder="YYYY-MM-DD HH:MM" />
          </label>
          <label class="form-item">
            <span>现场负责人</span>
            <input v-model="form.现场负责人" placeholder="不填则取各隧道负责人" />
          </label>
          <label class="form-item">
            <span>交通组织</span>
            <label class="radio-inline">
              <input type="checkbox" v-model="form.封闭作业" /> 封闭作业（占用行车时段）
            </label>
          </label>
          <label class="form-item form-item-wide">
            <span>备注</span>
            <input v-model="form.备注" placeholder="专项检查说明" />
          </label>
        </div>

        <div class="section-head" style="margin-top: 12px">
          <p class="muted-note">
            将生成 {{ selectedIds.length * form.检查类别.length }} 个班组任务，进入异步队列逐项编排；
            命中已批准封闭时段的任务转「冲突待裁」，不会覆盖已批准时段。
          </p>
          <button class="btn primary" type="button" :disabled="submittingBatch" @click="submitBatch">
            {{ submittingBatch ? '提交中…' : '生成班组任务（入队）' }}
          </button>
        </div>
        <p v-if="composeMessage" :class="composeOk ? 'text-ok' : 'error-text'">{{ composeMessage }}</p>
      </section>

      <section class="panel">
        <h3 class="panel-title">异步批量队列
          <span class="sub">业务键幂等 · 复位后从未完成项继续</span>
        </h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>批次</th>
              <th>隧道/类别</th>
              <th>作业窗口</th>
              <th>进度</th>
              <th>状态</th>
              <th>说明</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="batch in batches" :key="batch.id">
              <td>{{ batch.batch_no }}<br /><span class="muted-note">{{ batch.创建时间 }}</span></td>
              <td>{{ batch.隧道ids.length }} 座 × {{ batch.检查类别.join('、') }}<span v-if="batch.封闭作业" class="tag tag-red">封闭</span></td>
              <td>{{ batch.作业开始 }}<br />{{ batch.作业结束 }}</td>
              <td>
                <div class="progress">
                  <i :style="{ width: `${batch.total ? (batch.processed / batch.total) * 100 : 0}%` }"></i>
                </div>
                <span class="muted-note">{{ batch.processed }}/{{ batch.total }}<template v-if="batch.冲突数">，冲突 {{ batch.冲突数 }}</template></span>
              </td>
              <td><span class="tag" :class="batchStatusTag(batch.status)">{{ batch.status }}</span></td>
              <td>{{ batch.message }}</td>
              <td>
                <button
                  v-if="batch.status !== '已完成'"
                  class="link"
                  type="button"
                  @click="resetBatch(batch.id)"
                >复位续跑</button>
                <button class="link" type="button" @click="inspectBatch(batch.id)">查看明细</button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- ========================= 任务流转 ========================= -->
    <div v-if="activeTab === 'tasks'">
      <section class="panel">
        <div class="section-head">
          <h3 class="panel-title" style="margin: 0">班组任务</h3>
          <div class="row-gap">
            <span v-if="focusedTunnelId" class="tag tag-blue">
              仅看隧道 id={{ focusedTunnelId }}
              <button class="link" type="button" @click="clearTunnelFocus">×</button>
            </span>
            <select v-model="taskFilter.status" @change="loadTasks">
              <option value="">全部状态</option>
              <option v-for="s in meta.statuses ?? []" :key="s" :value="s">{{ s }}</option>
            </select>
            <select v-model="taskFilter.category" @change="loadTasks">
              <option value="">全部类别</option>
              <option v-for="c in meta.categories ?? []" :key="c" :value="c">{{ c }}检查（{{ crewOf(c) }}）</option>
            </select>
          </div>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>编号</th>
              <th>洞群/隧道</th>
              <th>类别/班组</th>
              <th>作业窗口</th>
              <th>封闭</th>
              <th>状态</th>
              <th>批准</th>
              <th>收口结论</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td>#{{ task.id }}</td>
              <td>{{ task.洞群 }}<br /><span class="muted-note">{{ task.隧道名称 }}</span></td>
              <td>{{ task.检查类别 }}检查<br /><span class="tag tag-blue">{{ task.检查班组 }}</span></td>
              <td>{{ task.作业开始 }}<br />{{ task.作业结束 }}</td>
              <td>
                <span v-if="task.封闭作业" class="tag tag-red">封闭作业</span>
                <span v-else class="tag tag-grey">占道巡查</span>
              </td>
              <td><span class="tag" :class="taskStatusTag(task)">{{ task.status }}</span></td>
              <td>
                <span v-if="task.已批准" class="text-ok">已批准<br /><span class="muted-note">{{ task.批准记录?.批准人 }}</span></span>
                <span v-else-if="task.冲突待裁" class="text-danger">冲突待裁</span>
                <span v-else class="muted-note">待批准</span>
              </td>
              <td>
                <span v-if="task.收口结论" :class="task.收口结论 === '合格' ? 'text-ok' : 'text-warn'">
                  {{ task.收口结论 }}<br /><span class="muted-note">{{ task.收口负责人 }} {{ task.收口时间 }}</span>
                </span>
                <span v-else class="muted-note">—</span>
              </td>
              <td class="task-actions">
                <button v-if="task.status === '待签发'" class="link" type="button" @click="openPlan(task, 'adjust')">调整窗口</button>
                <button v-if="task.status === '待签发'" class="link" type="button" :class="{ 'link-danger': task.冲突待裁 }" @click="openPlan(task, 'approve')">
                  {{ task.冲突待裁 ? '冲突报批' : '批准计划' }}
                </button>
                <button v-if="task.status === '待签发'" class="link" type="button" @click="advance(task, '签发计划')">签发计划</button>
                <button v-if="task.status === '待签发'" class="link" type="button" @click="advance(task, '取消任务')">取消</button>
                <button v-if="task.status === '已签发'" class="link" type="button" @click="advance(task, '开始作业')">开始作业</button>
                <button v-if="task.status === '作业中'" class="link" type="button" @click="advance(task, '完成作业')">完成作业</button>
                <button v-if="task.status === '待收口'" class="link link-danger" type="button" @click="openClose(task)">现场收口</button>
                <button class="link" type="button" @click="openHistory(task)">留痕</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!tasks.length" class="empty-state">暂无符合条件的任务</p>
      </section>
    </div>

    <!-- ========================= 地图 ========================= -->
    <div v-if="activeTab === 'map'">
      <section class="panel">
        <h3 class="panel-title">洞群作业窗口影响图
          <span class="sub">同隧道时段冲突、同洞群通风/视线联动，重叠窗口自动连线</span>
        </h3>
        <OrchestrationMap :data="mapData" @select-tunnel="focusTunnel" />
      </section>
    </div>

    <!-- ========================= 设施检查清单 ========================= -->
    <div v-if="activeTab === 'checklist'">
      <section class="panel">
        <div class="section-head">
          <h3 class="panel-title" style="margin: 0">设施检查清单
            <span class="sub">历史检查按原班组快照留痕，任务调整不改写既有清单班组</span>
          </h3>
          <div class="row-gap">
            <select v-model="checkFilter.status" @change="loadChecklist">
              <option value="">全部状态</option>
              <option value="待检">待检</option>
              <option value="已收口">已收口</option>
            </select>
            <select v-model="checkFilter.crew" @change="loadChecklist">
              <option value="">全部班组</option>
              <option v-for="crew in crews" :key="crew" :value="crew">{{ crew }}</option>
            </select>
          </div>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>检查编号</th>
              <th>洞群/隧道</th>
              <th>类别</th>
              <th>原班组（留痕）</th>
              <th>检查项目</th>
              <th>作业窗口</th>
              <th>结果</th>
              <th>状态</th>
              <th>收口</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in checklist" :key="row.id">
              <td>{{ row.检查编号 }}<br /><span class="muted-note">任务 #{{ row.task_id }}</span></td>
              <td>{{ row.洞群 }}<br /><span class="muted-note">{{ row.隧道名称 }}</span></td>
              <td>{{ row.检查类别 }}</td>
              <td><span class="tag tag-blue">{{ row.班组快照 }}</span></td>
              <td>{{ row.检查项目 }}<br /><span v-if="row.情况说明" class="muted-note">{{ row.情况说明 }}</span></td>
              <td>{{ row.作业开始 }}<br />{{ row.作业结束 }}</td>
              <td>
                <span :class="row.检查结果 === '隐患整改' ? 'text-warn' : row.检查结果 === '合格' ? 'text-ok' : 'muted-note'">
                  {{ row.检查结果 }}
                </span>
              </td>
              <td><span class="tag" :class="row.status === '已收口' ? 'tag-green' : row.status === '已取消' ? 'tag-grey' : 'tag-amber'">{{ row.status }}</span></td>
              <td>
                <span v-if="row.收口时间" class="muted-note">{{ row.收口结论 }}<br />{{ row.收口负责人 }}<br />{{ row.收口时间 }}</span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- ========================= 车辆调度汇总 ========================= -->
    <div v-if="activeTab === 'dispatch'">
      <div class="stat-row">
        <article class="stat-card"><span class="stat-label">待派车</span><strong class="stat-value text-warn">{{ dispatchSummary.待派车 ?? 0 }}</strong></article>
        <article class="stat-card"><span class="stat-label">已派出</span><strong class="stat-value">{{ dispatchSummary.已派出 ?? 0 }}</strong></article>
        <article class="stat-card"><span class="stat-label">已归库</span><strong class="stat-value text-ok">{{ dispatchSummary.已归库 ?? 0 }}</strong></article>
        <article class="stat-card"><span class="stat-label">车辆在库/出车</span><strong class="stat-value">{{ dispatchSummary.在库车辆 ?? 0 }} / {{ dispatchSummary.出车车辆 ?? 0 }}</strong></article>
      </div>
      <section class="panel">
        <h3 class="panel-title">车辆调度待办
          <span class="sub">计划签发时自动生成，作业开始派出、现场收口归库，随任务结论同步更新</span>
        </h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>调度编号</th>
              <th>洞群/隧道</th>
              <th>类别/班组</th>
              <th>车辆</th>
              <th>作业窗口</th>
              <th>封闭</th>
              <th>状态</th>
              <th>派出/归库时间</th>
              <th>关联任务</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in dispatchRows" :key="row.id">
              <td>{{ row.调度编号 }}</td>
              <td>{{ row.洞群 }}<br /><span class="muted-note">{{ row.隧道名称 }}</span></td>
              <td>{{ row.检查类别 }} · {{ row.检查班组 }}</td>
              <td>{{ row.车辆类型 }}<br /><span class="muted-note">{{ row.车牌号 }}（{{ row.车辆编号 }}）</span></td>
              <td>{{ row.作业开始 }}<br />{{ row.作业结束 }}</td>
              <td><span v-if="row.封闭作业" class="tag tag-red">封闭</span><span v-else class="tag tag-grey">常规</span></td>
              <td><span class="tag" :class="row.status === '待派车' ? 'tag-amber' : row.status === '已派出' ? 'tag-blue' : 'tag-green'">{{ row.status }}</span></td>
              <td class="muted-note">{{ row.派出时间 || '—' }}<br />{{ row.归库时间 || '—' }}</td>
              <td><button class="link" type="button" @click="jumpToTask(row.task_id)">#{{ row.task_id }}</button></td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- ========================= 弹窗 ========================= -->
    <PlanDialog
      v-if="planDialog.task"
      :task="planDialog.task"
      :mode="planDialog.mode"
      :on-submit="submitPlan"
      @close="planDialog.task = null"
    />
    <CloseDialog
      v-if="closeTarget"
      :task="closeTarget"
      :meta="(meta.results ? meta : metaFallback) as Meta"
      :checklist-api="checklist"
      :on-submit="submitClose"
      @close="closeTarget = null"
    />

    <div v-if="historyTask" class="modal-mask" @click.self="historyTask = null">
      <div class="modal">
        <header class="modal-head">
          <h3>任务留痕 · #{{ historyTask.id }}（{{ historyTask.隧道名称 }} {{ historyTask.检查班组 }}）</h3>
          <button class="link" type="button" @click="historyTask = null">关闭</button>
        </header>
        <div class="modal-body">
          <table class="data-table">
            <thead><tr><th>时间</th><th>动作</th><th>操作人</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="(log, idx) in historyTask.history ?? []" :key="idx">
                <td>{{ log.时间 }}</td>
                <td>{{ log.动作 }}</td>
                <td>{{ log.操作人 }}</td>
                <td>{{ log.说明 || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="historyTask.批准记录" class="muted-note" style="margin-top: 8px">
            批准人：{{ historyTask.批准记录.批准人 }}（{{ historyTask.批准记录.批准时间 }}）
          </p>
        </div>
      </div>
    </div>

    <div v-if="batchDetail" class="modal-mask" @click.self="batchDetail = null">
      <div class="modal">
        <header class="modal-head">
          <h3>批次明细 · {{ batchDetail.batch_no }}</h3>
          <button class="link" type="button" @click="batchDetail = null">关闭</button>
        </header>
        <div class="modal-body">
          <table class="data-table">
            <thead><tr><th>隧道</th><th>类别</th><th>班组</th><th>队列状态</th><th>说明</th><th>任务</th></tr></thead>
            <tbody>
              <tr v-for="(item, idx) in batchDetail.items" :key="idx">
                <td>{{ item.隧道名称 }}</td>
                <td>{{ item.检查类别 }}</td>
                <td>{{ item.检查班组 }}</td>
                <td><span class="tag" :class="item.状态 === '冲突' ? 'tag-red' : item.状态 === '已生成' ? 'tag-green' : 'tag-grey'">{{ item.状态 }}</span></td>
                <td>{{ item.message }}</td>
                <td>
                  <button v-if="item.task_id" class="link" type="button" @click="batchDetail = null; jumpToTask(item.task_id)">
                    #{{ item.task_id }}
                  </button>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import CloseDialog from './CloseDialog.vue'
import OrchestrationMap from './OrchestrationMap.vue'
import PlanDialog from './PlanDialog.vue'

interface Meta {
  categories: string[]
  crews: Record<string, string>
  check_items: Record<string, string[]>
  statuses: string[]
  results: string[]
}

const API = '/api/tunnel-console'
const tabs = [
  { key: 'compose', label: '编排与队列' },
  { key: 'tasks', label: '任务流转' },
  { key: 'map', label: '洞群窗口图' },
  { key: 'checklist', label: '设施检查清单' },
  { key: 'dispatch', label: '车辆调度汇总' },
] as const

const activeTab = ref<(typeof tabs)[number]['key']>('compose')
const metaFallback: Meta = { categories: [], crews: {}, check_items: {}, statuses: [], results: [] }
const meta = ref<Partial<Meta>>({})
const tunnelGroups = ref<Array<{ 洞群: string; tunnels: Array<Record<string, any>> }>>([])
const batches = ref<Array<Record<string, any>>>([])
const tasks = ref<Array<Record<string, any>>>([])
const checklist = ref<Array<Record<string, any>>>([])
const dispatchRows = ref<Array<Record<string, any>>>([])
const dispatchSummary = ref<Record<string, number>>({})
const mapData = ref<{ tunnels: any[]; windows: any[]; links: any[] }>({ tunnels: [], windows: [], links: [] })

const selectedIds = ref<number[]>([])
const selectedTunnels = computed(() => tunnelGroups.value.flatMap((g) => g.tunnels).filter((t) => selectedIds.value.includes(t.id)))
const form = reactive<{ 检查类别: string[]; 作业开始: string; 作业结束: string; 现场负责人: string; 封闭作业: boolean; 备注: string }>({
  检查类别: ['通风', '消防', '照明'],
  作业开始: '2026-10-06 09:00',
  作业结束: '2026-10-06 12:00',
  现场负责人: '',
  封闭作业: false,
  备注: '',
})
const submittingBatch = ref(false)
const composeMessage = ref('')
const composeOk = ref(true)

const taskFilter = reactive({ status: '', category: '' })
const focusedTunnelId = ref<number | null>(null)
const checkFilter = reactive({ status: '', crew: '' })
const crews = computed(() => Object.values(meta.value.crews ?? {}))

const planDialog = reactive<{ task: Record<string, any> | null; mode: 'approve' | 'adjust' }>({ task: null, mode: 'approve' })
const closeTarget = ref<Record<string, any> | null>(null)
const historyTask = ref<Record<string, any> | null>(null)
const batchDetail = ref<Record<string, any> | null>(null)

let pollTimer: number | undefined

function crewOf(category: string): string {
  return meta.value.crews?.[category] ?? category
}

function taskStatusCount(status: string): number {
  return tasks.value.filter((task) => task.status === status).length
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

async function postAction(path: string, values: Record<string, any>): Promise<{ ok: boolean; message: string; entry: any }> {
  const response = await request(path, { method: 'POST', body: JSON.stringify({ values }) })
  return (await response.json()) as { ok: boolean; message: string; entry: any }
}

async function loadMeta() {
  meta.value = await getJson(`${API}/meta`)
}
async function loadTunnels() {
  tunnelGroups.value = await getJson(`${API}/tunnels`)
}
async function loadBatches() {
  batches.value = await getJson(`${API}/batches`)
}
async function loadTasks() {
  const params = new URLSearchParams()
  if (taskFilter.status) params.set('status', taskFilter.status)
  if (taskFilter.category) params.set('category', taskFilter.category)
  if (focusedTunnelId.value) params.set('tunnel_id', String(focusedTunnelId.value))
  tasks.value = await getJson(`${API}/tasks?${params.toString()}`)
}
async function loadChecklist() {
  const params = new URLSearchParams()
  if (checkFilter.status) params.set('status', checkFilter.status)
  if (checkFilter.crew) params.set('crew', checkFilter.crew)
  checklist.value = await getJson(`${API}/checklist?${params.toString()}`)
}
async function loadDispatch() {
  const summary = await getJson<Record<string, any>>(`${API}/dispatch/summary`)
  dispatchRows.value = summary.items ?? []
  dispatchSummary.value = Object.fromEntries(
    Object.entries(summary).filter(([key]) => key !== 'items').map(([key, value]) => [key, Number(value)]),
  )
}
async function loadMap() {
  mapData.value = await getJson(`${API}/map`)
}

async function reloadAll() {
  await Promise.all([loadMeta(), loadTunnels(), loadBatches(), loadTasks(), loadChecklist(), loadDispatch(), loadMap()])
}

async function submitBatch() {
  composeMessage.value = ''
  submittingBatch.value = true
  try {
    const result = await postAction(`${API}/batches`, {
      隧道ids: selectedIds.value,
      检查类别: form.检查类别,
      作业开始: form.作业开始,
      作业结束: form.作业结束,
      封闭作业: form.封闭作业,
      现场负责人: form.现场负责人,
      备注: form.备注,
    })
    composeOk.value = result.ok
    composeMessage.value = result.message
    if (result.ok) {
      selectedIds.value = []
      await loadBatches()
    }
  } catch (error) {
    composeOk.value = false
    composeMessage.value = error instanceof Error ? error.message : '批次提交失败'
  } finally {
    submittingBatch.value = false
  }
}

async function resetBatch(batchId: number) {
  const result = await postAction(`${API}/batches/${batchId}/reset`, {})
  composeMessage.value = result.message
  composeOk.value = result.ok
  await loadBatches()
}

function inspectBatch(batchId: number) {
  batchDetail.value = batches.value.find((batch) => batch.id === batchId) ?? null
}

async function advance(task: Record<string, any>, action: string) {
  const result = await postAction(`${API}/tasks/${task.id}/advance`, { action })
  await afterMutation(result)
}

function openPlan(task: Record<string, any>, mode: 'approve' | 'adjust') {
  planDialog.task = task
  planDialog.mode = mode
}

async function submitPlan(values: Record<string, any>) {
  const path = planDialog.mode === 'approve' ? 'approve' : 'adjust'
  const payload = planDialog.mode === 'approve'
    ? { 批准人: values.操作人, 批准说明: values.说明 }
    : { 作业开始: values.作业开始, 作业结束: values.作业结束, 操作人: values.操作人, 说明: values.说明 }
  const result = await postAction(`${API}/tasks/${planDialog.task?.id}/${path}`, payload)
  const ok = await afterMutation(result)
  if (ok) planDialog.task = null
  return ok
}

function openClose(task: Record<string, any>) {
  closeTarget.value = task
}

async function submitClose(values: Record<string, any>) {
  const result = await postAction(`${API}/tasks/${closeTarget.value?.id}/advance`, values)
  const ok = await afterMutation(result)
  if (ok) closeTarget.value = null
  return ok
}

function openHistory(task: Record<string, any>) {
  historyTask.value = task
}

function focusTunnel(tunnelId: number) {
  activeTab.value = 'tasks'
  taskFilter.status = ''
  taskFilter.category = ''
  focusedTunnelId.value = tunnelId
  void loadTasks()
}

function clearTunnelFocus() {
  focusedTunnelId.value = null
  void loadTasks()
}

async function jumpToTask(taskId: number) {
  activeTab.value = 'tasks'
  taskFilter.status = ''
  focusedTunnelId.value = null
  await loadTasks()
  const target = tasks.value.find((task) => task.id === taskId)
  if (target) historyTask.value = target
}

async function afterMutation(result: { ok: boolean; message: string }) {
  composeMessage.value = result.message
  composeOk.value = result.ok
  if (result.ok) {
    // 变更不只更新弹窗：任务、台账、清单、调度、地图全部重新拉取
    await Promise.all([loadTasks(), loadBatches(), loadChecklist(), loadDispatch(), loadMap(), loadTunnels()])
  }
  return result.ok
}

function statusTag(status: string): string {
  return { 正常: 'tag-green', 限速: 'tag-amber', 维修: 'tag-amber', 封闭: 'tag-red' }[status] ?? 'tag-grey'
}

function batchStatusTag(status: string): string {
  return { 已完成: 'tag-green', 部分冲突: 'tag-red', 排队中: 'tag-grey', 生成中: 'tag-blue' }[status] ?? 'tag-grey'
}

function taskStatusTag(task: Record<string, any>): string {
  if (task.status === '待签发') return task.冲突待裁 ? 'tag-red' : 'tag-amber'
  return {
    已签发: 'tag-blue',
    作业中: 'tag-blue',
    待收口: 'tag-amber',
    已收口: 'tag-green',
    已取消: 'tag-grey',
  }[String(task.status)] ?? 'tag-grey'
}

onMounted(async () => {
  await reloadAll()
  // 队列进度轮询
  pollTimer = window.setInterval(() => {
    if (activeTab.value === 'compose') void loadBatches()
    void loadMap()
  }, 2500)
})

onUnmounted(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.tunnel-group { margin-bottom: 10px; }
.group-name { margin: 6px 0; font-size: 13px; font-weight: 600; color: #334155; }
.muted-note { color: var(--muted); font-size: 12px; }
.progress { width: 120px; height: 6px; background: #e2e8f0; border-radius: 999px; overflow: hidden; margin-bottom: 2px; }
.progress i { display: block; height: 100%; background: var(--brand); }
.task-actions { display: flex; flex-direction: column; gap: 2px 0; align-items: flex-start; }
.link-danger { color: #b42318; }
</style>
