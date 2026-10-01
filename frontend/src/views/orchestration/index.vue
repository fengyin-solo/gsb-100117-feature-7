<template>
  <section class="page orch-page">
    <header class="page-head">
      <div>
        <h2>洞群任务编排台</h2>
        <p class="page-desc">
          多选隧道后按通风、消防、照明检查项分派班组任务；计划签发 → 作业执行 → 现场收口单向推进，
          收口结论回写隧道台账、设施检查清单与车辆调度汇总。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reloadAll">刷新数据</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">编排任务总数</span>
        <strong class="stat-value">{{ taskStats.total }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待现场批准</span>
        <strong class="stat-value warn">{{ taskStats.pendingApproval }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">作业执行中</span>
        <strong class="stat-value">{{ taskStats.executing }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">车辆待派</span>
        <strong class="stat-value warn">{{ dispatchSummary.pending_vehicle ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">异常待复查</span>
        <strong class="stat-value danger">{{ abnormalChecklist }}</strong>
      </article>
    </div>

    <nav class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    <span v-if="successMessage" class="success-text">{{ successMessage }}</span>

    <!-- 批量签发 -->
    <section v-show="activeTab === 'issue'" class="panel">
      <h3>批量签发班组任务</h3>
      <div class="issue-grid">
        <div class="issue-tunnels">
          <div class="sub-head">
            <strong>① 选择隧道（可多选，按洞群走廊分组）</strong>
            <input v-model="tunnelKeyword" class="mini-input" placeholder="搜索编号/名称" />
          </div>
          <table class="data-table compact">
            <thead>
              <tr>
                <th style="width: 36px"><input type="checkbox" :checked="allFilteredSelected" @change="toggleAll" /></th>
                <th>隧道编号</th>
                <th>隧道名称</th>
                <th>所属走廊</th>
                <th>桩号</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in filteredTunnels" :key="String(t.id)" :class="{ selected: selectedTunnelIds.includes(Number(t.id)) }">
                <td><input type="checkbox" :value="Number(t.id)" v-model="selectedTunnelIds" /></td>
                <td>{{ t['隧道编号'] }}</td>
                <td>{{ t['隧道名称'] }}</td>
                <td>{{ t['所属走廊'] }}</td>
                <td>K{{ t['桩号km'] }}</td>
                <td>
                  <span class="badge" :class="tunnelBadgeClass(String(t['隧道状态'] || t.status))">
                    {{ t['隧道状态'] || t.status }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <p class="hint">已选 {{ selectedTunnelIds.length }} 座隧道</p>
        </div>

        <div class="issue-form">
          <strong>② 选择检查项（按类别分派不同班组与车辆）</strong>
          <label v-for="cat in categories" :key="cat.name" class="cat-card">
            <input type="checkbox" :value="cat.name" v-model="selectedCategories" />
            <span class="cat-name">{{ cat.name }}</span>
            <span class="cat-meta">班组：{{ cat['班组'] }} · 车辆：{{ cat['车辆'] }}</span>
            <span class="cat-items">{{ cat.items.join('、') }}</span>
          </label>

          <div class="form-row">
            <label>
              <span>计划开始</span>
              <input v-model="issueForm.planStart" placeholder="YYYY-MM-DD HH:MM" />
            </label>
            <label>
              <span>计划结束</span>
              <input v-model="issueForm.planEnd" placeholder="YYYY-MM-DD HH:MM" />
            </label>
          </div>
          <div class="form-row">
            <label>
              <span>现场负责人</span>
              <input v-model="issueForm.leader" placeholder="批准与协调责任人" />
            </label>
            <label class="check-line">
              <input type="checkbox" v-model="issueForm.closureRequired" />
              <span>需要封闭交通（任务将进入现场批准）</span>
            </label>
          </div>
          <p class="hint">
            将生成 {{ selectedTunnelIds.length * selectedCategories.length }} 个班组任务，经异步队列逐项签发；
            同洞群窗口冲突或隧道已有封闭作业时，以现场负责人批准的计划为准。
          </p>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitBatch">
            {{ submitting ? '正在入队…' : '生成批量任务' }}
          </button>

          <div v-if="currentBatch" class="batch-box">
            <div class="sub-head">
              <strong>批次 {{ currentBatch.batch['批次号'] }}</strong>
              <span class="badge" :class="batchStatusClass(currentBatch.batch.status)">{{ currentBatch.batch.status }}</span>
            </div>
            <div class="progress-track">
              <div
                class="progress-bar"
                :style="{ width: `${batchProgressPercent}%` }"
                :class="{ danger: currentBatch.progress.failed > 0 }"
              />
            </div>
            <p class="hint">
              共 {{ currentBatch.progress.total }} 项 · 已完成 {{ currentBatch.progress.done }} ·
              失败 {{ currentBatch.progress.failed }} · 待处理 {{ currentBatch.progress.pending }}
            </p>
            <table class="data-table compact">
              <tbody>
                <tr v-for="item in currentBatch.items" :key="String(item.id)">
                  <td>{{ item['隧道编号'] }}</td>
                  <td>{{ item['检查类别'] }}</td>
                  <td>
                    <span class="badge" :class="itemStatusClass(item['处理状态'])">{{ item['处理状态'] }}</span>
                  </td>
                  <td class="muted">{{ item['结果说明'] }}</td>
                </tr>
              </tbody>
            </table>
            <button class="btn" type="button" @click="resetBatch">复位批次（从未完成项继续）</button>
          </div>
        </div>
      </div>
    </section>

    <!-- 洞群地图 -->
    <section v-show="activeTab === 'map'" class="panel">
      <div class="sub-head">
        <h3>洞群作业窗口地图</h3>
        <label class="date-line">
          <span>日期</span>
          <input v-model="mapDate" type="date" @change="loadMap" />
          <button class="btn ghost" type="button" @click="mapDate = ''; loadMap()">全部</button>
        </label>
      </div>
      <div class="map-grid">
        <svg class="tunnel-map" viewBox="0 0 600 440" role="img" aria-label="洞群隧道分布与作业窗口">
          <template v-for="(points, corridor) in mapData.corridors" :key="corridor">
            <polyline
              :points="corridorLine(points)"
              class="corridor-line"
              :class="{ conflict: corridorHasConflict(String(corridor)) }"
            />
            <text v-for="p in points" :key="p['隧道编号']" :x="Number(p.x) + 12" :y="Number(p.y) - 12" class="map-label">
              {{ p['隧道名称'] }}
            </text>
            <circle
              v-for="p in points"
              :key="`${p['隧道编号']}-c`"
              :cx="Number(p.x)"
              :cy="Number(p.y)"
              r="9"
              class="tunnel-dot"
              :class="{
                conflict: tunnelHasWindowConflict(String(p['隧道编号'])),
                closed: String(p['隧道状态']) === '封闭',
                maintenance: String(p['隧道状态']) === '维修',
              }"
            >
              <title>{{ `${p['隧道名称']}（${p['隧道状态']}）` }}</title>
            </circle>
          </template>
        </svg>
        <div class="window-list">
          <strong>作业窗口（互相影响标红）</strong>
          <article
            v-for="w in mapData.windows"
            :key="String(w['任务编号'])"
            class="window-card"
            :class="{ conflict: w['冲突任务'] && w['冲突任务'].length, approval: w['阶段'] === '待现场批准' }"
          >
            <header>
              <span class="cat-tag">{{ w['检查类别'] }}</span>
              <strong>{{ w['隧道名称'] }}</strong>
              <span class="badge" :class="stageClass(w['阶段'])">{{ w['阶段'] }}</span>
            </header>
            <p class="muted">{{ w['所属走廊'] }} · {{ w['班组'] }}</p>
            <p>{{ w['开始'] }} ~ {{ w['结束'] }}</p>
            <p v-if="w['封闭交通'] === '是'" class="danger-text">封闭交通作业</p>
            <p v-if="w['冲突任务'] && w['冲突任务'].length" class="danger-text">
              冲突窗口：{{ w['冲突任务'].join('、') }}
            </p>
            <p v-if="w['协同任务']" class="warn-text">同批次协同：{{ w['协同任务'] }}</p>
          </article>
          <p v-if="!mapData.windows.length" class="hint">当前日期没有作业窗口</p>
        </div>
      </div>
    </section>

    <!-- 任务台账 -->
    <section v-show="activeTab === 'tasks'" class="panel">
      <h3>编排任务台账</h3>
      <form class="filter-bar" @submit.prevent="loadTasks">
        <label class="filter-item">
          <span>阶段</span>
          <select v-model="taskFilter.stage">
            <option value="">全部</option>
            <option v-for="s in stageOptions" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>检查类别</span>
          <select v-model="taskFilter.category">
            <option value="">全部</option>
            <option v-for="c in categories" :key="c.name" :value="c.name">{{ c.name }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>隧道</span>
          <input v-model="taskFilter.tunnel" placeholder="编号/名称" />
        </label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>任务编号</th>
            <th>隧道</th>
            <th>洞群走廊</th>
            <th>检查类别</th>
            <th>执行班组</th>
            <th>作业窗口</th>
            <th>当前阶段</th>
            <th>冲突/封闭</th>
            <th>任务结论</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="String(t.id)">
            <td>{{ t['任务编号'] }}</td>
            <td>{{ t['隧道名称'] }}<br /><span class="muted">{{ t['隧道编号'] }}</span></td>
            <td>{{ t['所属走廊'] }}</td>
            <td>{{ t['检查类别'] }}</td>
            <td>{{ t['班组'] }}</td>
            <td class="nowrap">{{ t['计划开始'] }}<br />~ {{ t['计划结束'] }}</td>
            <td><span class="badge" :class="stageClass(t['当前阶段'])">{{ t['当前阶段'] }}</span></td>
            <td>
              <p v-if="t['冲突任务']" class="danger-text">{{ t['冲突任务'] }}</p>
              <p v-if="t['既有封闭任务']" class="warn-text">封闭 {{ t['既有封闭任务'] }}</p>
              <span v-if="!t['冲突任务'] && !t['既有封闭任务']" class="muted">—</span>
            </td>
            <td class="conclusion-cell">{{ t['任务结论'] || '—' }}</td>
            <td class="row-actions vertical">
              <button
                v-if="t['当前阶段'] === '计划签发'"
                class="link"
                type="button"
                @click="startTask(t)"
              >
                开始作业
              </button>
              <button
                v-if="t['当前阶段'] === '作业执行'"
                class="link"
                type="button"
                @click="openCloseModal(t)"
              >
                现场收口
              </button>
              <button
                v-if="t['当前阶段'] === '待现场批准'"
                class="link warn-link"
                type="button"
                @click="openApprovalModal(t)"
              >
                现场批准
              </button>
              <button class="link" type="button" @click="viewDetail(t)">明细</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="hint">共 {{ tasks.length }} 条任务；状态只能按 计划签发 → 作业执行 → 现场收口 顺序推进。</p>

      <div v-if="detailTask" class="detail-box">
        <div class="sub-head">
          <strong>任务 {{ detailTask['任务编号'] }} 关联回写记录</strong>
          <button class="btn ghost" type="button" @click="detailTask = null">关闭</button>
        </div>
        <p class="muted">设施检查清单（{{ taskDetail.checklist.length }} 项，班组为签发时留痕）：</p>
        <table class="data-table compact">
          <thead>
            <tr><th>检查项</th><th>执行班组</th><th>检查结果</th><th>回写时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in taskDetail.checklist" :key="String(c.id)">
              <td>{{ c['检查项'] }}</td>
              <td>{{ c['执行班组'] }}</td>
              <td>
                <span class="badge" :class="resultClass(c['检查结果'])">{{ c['检查结果'] }}</span>
              </td>
              <td>{{ c['回写时间'] || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted">车辆调度待办（{{ taskDetail.dispatch.length }} 条）：</p>
        <table class="data-table compact">
          <thead>
            <tr><th>车辆</th><th>班组</th><th>调度状态</th><th>回写结论</th></tr>
          </thead>
          <tbody>
            <tr v-for="d in taskDetail.dispatch" :key="String(d.id)">
              <td>{{ d['安排车辆'] }}</td>
              <td>{{ d['作业班组'] }}</td>
              <td><span class="badge" :class="dispatchClass(d['调度状态'])">{{ d['调度状态'] }}</span></td>
              <td>{{ d['回写结论'] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 设施检查清单 -->
    <section v-show="activeTab === 'checklist'" class="panel">
      <h3>设施检查清单</h3>
      <form class="filter-bar" @submit.prevent="loadChecklist">
        <label class="filter-item">
          <span>检查类别</span>
          <select v-model="checklistFilter.category">
            <option value="">全部</option>
            <option v-for="c in categories" :key="c.name" :value="c.name">{{ c.name }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>检查结果</span>
          <select v-model="checklistFilter.result">
            <option value="">全部</option>
            <option value="待检查">待检查</option>
            <option value="合格">合格</option>
            <option value="异常待整改">异常待整改</option>
          </select>
        </label>
        <label class="filter-item">
          <span>执行班组</span>
          <input v-model="checklistFilter.crew" placeholder="按班组检索" />
        </label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>清单编号</th>
            <th>隧道</th>
            <th>检查类别</th>
            <th>检查项</th>
            <th>执行班组（留痕）</th>
            <th>计划日期</th>
            <th>检查结果</th>
            <th>异常描述</th>
            <th>回写时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in checklistRows" :key="String(c.id)">
            <td>{{ c['清单编号'] }}</td>
            <td>{{ c['隧道名称'] }}</td>
            <td>{{ c['检查类别'] }}</td>
            <td>{{ c['检查项'] }}</td>
            <td>{{ c['执行班组'] }}</td>
            <td>{{ c['计划日期'] }}</td>
            <td><span class="badge" :class="resultClass(c['检查结果'])">{{ c['检查结果'] }}</span></td>
            <td>{{ c['异常描述'] || '—' }}</td>
            <td>{{ c['回写时间'] || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p class="hint">共 {{ checklistRows.length }} 条；历史检查按原班组留痕，收口只回写结果不改班组。</p>
    </section>

    <!-- 车辆调度汇总 -->
    <section v-show="activeTab === 'dispatch'" class="panel">
      <h3>车辆调度汇总</h3>
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">调度单总数</span>
          <strong class="stat-value">{{ dispatchSummary.total ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">待派车/待批准</span>
          <strong class="stat-value warn">{{ dispatchSummary.pending_vehicle ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">作业中</span>
          <strong class="stat-value">{{ dispatchSummary.working ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">已归队</span>
          <strong class="stat-value">{{ dispatchSummary.closed ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">关联异常</span>
          <strong class="stat-value danger">{{ dispatchSummary.abnormal ?? 0 }}</strong>
        </article>
      </div>
      <form class="filter-bar" @submit.prevent="loadDispatch">
        <label class="filter-item">
          <span>调度状态</span>
          <select v-model="dispatchFilter.status">
            <option value="">全部</option>
            <option value="待批准">待批准</option>
            <option value="待派车">待派车</option>
            <option value="作业中">作业中</option>
            <option value="已归队">已归队</option>
            <option value="已取消">已取消</option>
          </select>
        </label>
        <label class="filter-item">
          <span>作业班组</span>
          <input v-model="dispatchFilter.crew" placeholder="按班组检索" />
        </label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>调度编号</th>
            <th>任务编号</th>
            <th>隧道</th>
            <th>检查类别</th>
            <th>作业班组</th>
            <th>安排车辆</th>
            <th>作业窗口</th>
            <th>调度状态</th>
            <th>回写结论</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in dispatchRows" :key="String(d.id)">
            <td>{{ d['调度编号'] }}</td>
            <td>{{ d['任务编号'] }}</td>
            <td>{{ d['隧道名称'] }}</td>
            <td>{{ d['检查类别'] }}</td>
            <td>{{ d['作业班组'] }}</td>
            <td>{{ d['安排车辆'] }}</td>
            <td class="nowrap">{{ d['计划开始'] }}<br />~ {{ d['计划结束'] }}</td>
            <td><span class="badge" :class="dispatchClass(d['调度状态'])">{{ d['调度状态'] }}</span></td>
            <td>{{ d['回写结论'] || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p class="hint">共 {{ dispatchRows.length }} 条；任务的签发、批准、开工、收口都会同步更新本页。</p>
    </section>

    <!-- 现场批准弹窗 -->
    <div v-if="approvalTarget" class="modal-mask" @click.self="approvalTarget = null">
      <div class="modal">
        <h3>现场负责人批准 · {{ approvalTarget['任务编号'] }}</h3>
        <p class="muted">{{ approvalTarget['隧道名称'] }} · {{ approvalTarget['检查类别'] }} · {{ approvalTarget['班组'] }}</p>
        <p v-if="approvalTarget['冲突任务']" class="danger-text">窗口冲突任务：{{ approvalTarget['冲突任务'] }}</p>
        <p v-if="approvalTarget['既有封闭任务']" class="warn-text">
          同一隧道封闭作业：{{ approvalTarget['既有封闭任务'] }}，新任务以批准计划为准
        </p>
        <div class="form-row">
          <label>
            <span>批准开始</span>
            <input v-model="approvalForm.approvedStart" placeholder="YYYY-MM-DD HH:MM" />
          </label>
          <label>
            <span>批准结束</span>
            <input v-model="approvalForm.approvedEnd" placeholder="YYYY-MM-DD HH:MM" />
          </label>
        </div>
        <label class="full-line">
          <span>现场负责人</span>
          <input v-model="approvalForm.approver" placeholder="批准人姓名（留痕）" />
        </label>
        <label class="full-line">
          <span>批准说明</span>
          <textarea v-model="approvalForm.note" rows="2" placeholder="与封闭作业协调、错峰确认等"></textarea>
        </label>
        <p class="hint">批准时段仍与已批准窗口冲突时无法提交，冲突任务不得覆盖已批准时段。</p>
        <div class="modal-actions">
          <button class="btn danger-btn" type="button" :disabled="approving" @click="submitApproval('驳回')">驳回</button>
          <button class="btn primary" type="button" :disabled="approving" @click="submitApproval('批准')">按计划批准</button>
          <button class="btn ghost" type="button" @click="approvalTarget = null">取消</button>
        </div>
      </div>
    </div>

    <!-- 现场收口弹窗 -->
    <div v-if="closeTarget" class="modal-mask" @click.self="closeTarget = null">
      <div class="modal">
        <h3>现场收口 · {{ closeTarget['任务编号'] }}</h3>
        <p class="muted">{{ closeTarget['隧道名称'] }} · {{ closeTarget['检查类别'] }} · {{ closeTarget['班组'] }}</p>
        <label class="full-line">
          <span>任务结论（必填）</span>
          <textarea v-model="closeForm.conclusion" rows="3" placeholder="作业完成情况、测试结果"></textarea>
        </label>
        <label class="full-line">
          <span>异常检查项（用、分隔，可空）</span>
          <input v-model="closeForm.abnormalItems" placeholder="如：灯具亮灯率、应急照明" />
        </label>
        <p class="hint">收口后结论将回写隧道台账、设施检查清单与车辆调度待办，异常项进入待复查。</p>
        <div class="modal-actions">
          <button class="btn primary" type="button" :disabled="closing" @click="submitClose">确认收口</button>
          <button class="btn ghost" type="button" @click="closeTarget = null">取消</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

interface Category {
  name: string
  班组: string
  车辆: string
  items: string[]
}
type Row = Record<string, any>

const ENDPOINT = '/api/orchestration'
const tabs = [
  { key: 'issue', label: '批量签发' },
  { key: 'map', label: '洞群地图' },
  { key: 'tasks', label: '任务台账' },
  { key: 'checklist', label: '设施检查清单' },
  { key: 'dispatch', label: '车辆调度汇总' },
]
const stageOptions = ['计划签发', '作业执行', '现场收口', '待现场批准', '批准驳回']

const activeTab = ref('issue')
const errorMessage = ref('')
const successMessage = ref('')
const categories = ref<Category[]>([])
const tunnels = ref<Row[]>([])
const tunnelKeyword = ref('')
const selectedTunnelIds = ref<number[]>([])
const selectedCategories = ref<string[]>([])
const issueForm = reactive({ planStart: '2026-10-05 09:00', planEnd: '2026-10-05 11:00', leader: '', closureRequired: false })
const submitting = ref(false)

const currentBatch = ref<Row | null>(null)
let batchTimer: ReturnType<typeof setInterval> | null = null

const mapDate = ref('')
const mapData = ref<{ corridors: Record<string, Row[]>; windows: Row[] }>({ corridors: {}, windows: [] })

const tasks = ref<Row[]>([])
const taskFilter = reactive({ stage: '', category: '', tunnel: '' })

const checklistRows = ref<Row[]>([])
const checklistFilter = reactive({ category: '', result: '', crew: '' })

const dispatchRows = ref<Row[]>([])
const dispatchFilter = reactive({ status: '', crew: '' })
const dispatchSummary = ref<Row>({})

const detailTask = ref<Row | null>(null)
const taskDetail = reactive<{ checklist: Row[]; dispatch: Row[] }>({ checklist: [], dispatch: [] })

const approvalTarget = ref<Row | null>(null)
const approvalForm = reactive({ approvedStart: '', approvedEnd: '', approver: '', note: '' })
const approving = ref(false)

const closeTarget = ref<Row | null>(null)
const closeForm = reactive({ conclusion: '', abnormalItems: '' })
const closing = ref(false)

const filteredTunnels = computed(() => {
  const kw = tunnelKeyword.value.trim()
  if (!kw) return tunnels.value
  return tunnels.value.filter(
    (t) => String(t['隧道编号']).includes(kw) || String(t['隧道名称']).includes(kw),
  )
})
const allFilteredSelected = computed(
  () => filteredTunnels.value.length > 0
    && filteredTunnels.value.every((t) => selectedTunnelIds.value.includes(Number(t.id))),
)
function toggleAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const ids = filteredTunnels.value.map((t) => Number(t.id))
  selectedTunnelIds.value = checked
    ? Array.from(new Set([...selectedTunnelIds.value, ...ids]))
    : selectedTunnelIds.value.filter((id) => !ids.includes(id))
}

const taskStats = computed(() => ({
  total: tasks.value.length,
  pendingApproval: tasks.value.filter((t) => t['当前阶段'] === '待现场批准').length,
  executing: tasks.value.filter((t) => t['当前阶段'] === '作业执行').length,
}))
const abnormalChecklist = computed(
  () => checklistRows.value.filter((c) => c['检查结果'] === '异常待整改').length,
)
const batchProgressPercent = computed(() => {
  if (!currentBatch.value) return 0
  const { total, done, failed } = currentBatch.value.progress
  return total ? Math.round(((done + failed) / total) * 100) : 0
})

function flash(message: string, ok = true) {
  if (ok) {
    successMessage.value = message
    errorMessage.value = ''
  } else {
    errorMessage.value = message
    successMessage.value = ''
  }
  setTimeout(() => {
    if (ok) successMessage.value = ''
    else errorMessage.value = ''
  }, 4000)
}

async function getJson(path: string): Promise<any> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return response.json()
}
async function postJson(path: string, body: unknown): Promise<Row> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.message || `接口返回 ${response.status}`)
  }
  return payload
}

async function loadCategories() {
  const payload = await getJson(`${ENDPOINT}/categories`)
  categories.value = payload.categories ?? []
}
async function loadTunnels() {
  const payload = await getJson(`${ENDPOINT}/tunnels`)
  tunnels.value = payload.items ?? []
}
async function loadTasks() {
  const query = new URLSearchParams()
  if (taskFilter.stage) query.set('stage', taskFilter.stage)
  if (taskFilter.category) query.set('category', taskFilter.category)
  if (taskFilter.tunnel) query.set('tunnel', taskFilter.tunnel)
  query.set('size', '200')
  const payload = await getJson(`${ENDPOINT}/tasks?${query.toString()}`)
  tasks.value = payload.items ?? []
}
async function loadMap() {
  const query = mapDate.value ? `?date=${mapDate.value}` : ''
  mapData.value = await getJson(`${ENDPOINT}/map/windows${query}`)
}
async function loadChecklist() {
  const query = new URLSearchParams()
  if (checklistFilter.category) query.set('category', checklistFilter.category)
  if (checklistFilter.result) query.set('result', checklistFilter.result)
  if (checklistFilter.crew) query.set('crew', checklistFilter.crew)
  query.set('size', '200')
  const payload = await getJson(`${ENDPOINT}/checklist?${query.toString()}`)
  checklistRows.value = payload.items ?? []
}
async function loadDispatch() {
  const query = new URLSearchParams()
  if (dispatchFilter.status) query.set('status', dispatchFilter.status)
  if (dispatchFilter.crew) query.set('crew', dispatchFilter.crew)
  query.set('size', '200')
  const [list, summary] = await Promise.all([
    getJson(`${ENDPOINT}/dispatch?${query.toString()}`),
    getJson(`${ENDPOINT}/dispatch/summary`),
  ])
  dispatchRows.value = list.items ?? []
  dispatchSummary.value = summary ?? {}
}
async function reloadAll() {
  try {
    await Promise.all([loadTasks(), loadMap(), loadChecklist(), loadDispatch()])
    flash('数据已刷新')
  } catch (error) {
    flash(error instanceof Error ? error.message : '刷新失败', false)
  }
}

async function submitBatch() {
  errorMessage.value = ''
  if (!selectedTunnelIds.value.length) {
    flash('请至少选择一座隧道', false)
    return
  }
  if (!selectedCategories.value.length) {
    flash('请至少选择通风、消防、照明中的一项', false)
    return
  }
  submitting.value = true
  try {
    const payload = await postJson(`${ENDPOINT}/batches`, {
      tunnel_ids: selectedTunnelIds.value,
      categories: selectedCategories.value,
      plan_start: issueForm.planStart,
      plan_end: issueForm.planEnd,
      leader: issueForm.leader,
      closure_required: issueForm.closureRequired,
    })
    currentBatch.value = null
    flash(payload.message || '批次已进入异步队列')
    await pollBatch(String(payload.entry['批次号']))
  } catch (error) {
    flash(error instanceof Error ? error.message : '批量签发失败', false)
  } finally {
    submitting.value = false
  }
}

async function fetchBatch(batchNo: string) {
  currentBatch.value = await getJson(`${ENDPOINT}/batches/${batchNo}`)
}
async function pollBatch(batchNo: string) {
  if (batchTimer) clearInterval(batchTimer)
  await fetchBatch(batchNo)
  batchTimer = setInterval(async () => {
    try {
      await fetchBatch(batchNo)
      const progress = currentBatch.value?.progress
      if (progress && progress.pending === 0 && batchTimer) {
        clearInterval(batchTimer)
        batchTimer = null
        await Promise.all([loadTasks(), loadMap(), loadDispatch(), loadChecklist()])
      }
    } catch {
      // 轮询失败保留定时器，下一轮继续
    }
  }, 800)
}
async function resetBatch() {
  if (!currentBatch.value) return
  try {
    const batchNo = String(currentBatch.value.batch['批次号'])
    const payload = await postJson(`${ENDPOINT}/batches/${batchNo}/reset`, {})
    flash(payload.message || '批次已复位')
    await pollBatch(batchNo)
  } catch (error) {
    flash(error instanceof Error ? error.message : '复位失败', false)
  }
}

async function startTask(task: Row) {
  try {
    const payload = await postJson(`${ENDPOINT}/tasks/${task.id}/actions`, { action: '开始作业' })
    flash(payload.message || '作业已开工')
    await Promise.all([loadTasks(), loadDispatch(), loadMap()])
  } catch (error) {
    flash(error instanceof Error ? error.message : '操作失败', false)
  }
}

function openCloseModal(task: Row) {
  closeTarget.value = task
  closeForm.conclusion = ''
  closeForm.abnormalItems = ''
}
async function submitClose() {
  if (!closeTarget.value) return
  if (!closeForm.conclusion.trim()) {
    flash('现场收口必须填写任务结论', false)
    return
  }
  closing.value = true
  try {
    const payload = await postJson(`${ENDPOINT}/tasks/${closeTarget.value.id}/actions`, {
      action: '现场收口',
      conclusion: closeForm.conclusion,
      abnormal_items: closeForm.abnormalItems,
    })
    flash(payload.message || '收口完成')
    closeTarget.value = null
    detailTask.value = null
    await Promise.all([loadTasks(), loadDispatch(), loadChecklist(), loadMap()])
  } catch (error) {
    flash(error instanceof Error ? error.message : '收口失败', false)
  } finally {
    closing.value = false
  }
}

function openApprovalModal(task: Row) {
  approvalTarget.value = task
  approvalForm.approvedStart = task['计划开始']
  approvalForm.approvedEnd = task['计划结束']
  approvalForm.approver = task['现场负责人'] || ''
  approvalForm.note = ''
}
async function submitApproval(decision: string) {
  if (!approvalTarget.value) return
  approving.value = true
  try {
    const payload = await postJson(`${ENDPOINT}/tasks/${approvalTarget.value.id}/approval`, {
      decision,
      approver: approvalForm.approver,
      approved_start: approvalForm.approvedStart,
      approved_end: approvalForm.approvedEnd,
      note: approvalForm.note,
    })
    flash(payload.message || '审批完成')
    approvalTarget.value = null
    await Promise.all([loadTasks(), loadDispatch(), loadMap()])
  } catch (error) {
    flash(error instanceof Error ? error.message : '审批失败', false)
  } finally {
    approving.value = false
  }
}

async function viewDetail(task: Row) {
  try {
    const payload = await getJson(`${ENDPOINT}/tasks/${task.id}`)
    detailTask.value = task
    taskDetail.checklist = payload.checklist ?? []
    taskDetail.dispatch = payload.dispatch ?? []
  } catch (error) {
    flash(error instanceof Error ? error.message : '明细读取失败', false)
  }
}

// ---- 地图与样式辅助 ----
function corridorLine(points: Row[]): string {
  return points
    .map((p) => `${Number(p.x)},${Number(p.y)}`)
    .join(' ')
}
function tunnelHasWindowConflict(tunnelCode: string): boolean {
  return mapData.value.windows.some(
    (w) => w['隧道编号'] === tunnelCode && Array.isArray(w['冲突任务']) && w['冲突任务'].length > 0,
  )
}
function corridorHasConflict(corridor: string): boolean {
  return mapData.value.windows.some(
    (w) => w['所属走廊'] === corridor && Array.isArray(w['冲突任务']) && w['冲突任务'].length > 0,
  )
}

function stageClass(stage: string): string {
  return {
    计划签发: 'badge-blue',
    作业执行: 'badge-orange',
    现场收口: 'badge-green',
    待现场批准: 'badge-red',
    批准驳回: 'badge-gray',
  }[stage] || 'badge-gray'
}
function resultClass(result: string): string {
  if (result === '合格') return 'badge-green'
  if (result === '异常待整改') return 'badge-red'
  return 'badge-gray'
}
function dispatchClass(status: string): string {
  return {
    待批准: 'badge-red',
    待派车: 'badge-orange',
    作业中: 'badge-blue',
    已归队: 'badge-green',
    已取消: 'badge-gray',
  }[status] || 'badge-gray'
}
function itemStatusClass(status: string): string {
  if (status === '已完成') return 'badge-green'
  if (status === '失败') return 'badge-red'
  if (status === '处理中') return 'badge-blue'
  return 'badge-gray'
}
function batchStatusClass(status: string): string {
  if (status === '已完成') return 'badge-green'
  if (status === '部分失败') return 'badge-red'
  if (status === '处理中') return 'badge-blue'
  return 'badge-orange'
}
function tunnelBadgeClass(status: string): string {
  if (status === '封闭') return 'badge-red'
  if (status === '维修') return 'badge-orange'
  if (status === '限速') return 'badge-orange'
  return 'badge-green'
}

onMounted(async () => {
  try {
    await Promise.all([loadCategories(), loadTunnels()])
    await Promise.all([loadTasks(), loadMap(), loadChecklist(), loadDispatch()])
  } catch (error) {
    flash(error instanceof Error ? error.message : '编排台数据加载失败', false)
  }
})
onUnmounted(() => {
  if (batchTimer) clearInterval(batchTimer)
})
</script>

<style scoped>
.orch-page .success-text { color: #067647; font-size: 13px; }
.warn { color: #b54708 !important; }
.danger { color: #b42318 !important; }
.warn-text { color: #b54708; font-size: 12px; margin: 2px 0; }
.danger-text { color: #b42318; font-size: 12px; margin: 2px 0; }
.muted { color: var(--muted); font-size: 12px; }
.nowrap { white-space: nowrap; }
.hint { color: var(--muted); font-size: 12px; margin: 6px 0; }

.tab-bar { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 12px; flex-wrap: wrap; }
.tab-btn { border: none; background: none; padding: 8px 14px; cursor: pointer; font-size: 13px; color: var(--muted); border-bottom: 2px solid transparent; }
.tab-btn.active { color: var(--brand); border-bottom-color: var(--brand); font-weight: 600; }

.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; }
.panel h3 { margin: 0 0 10px; font-size: 15px; }
.sub-head { display: flex; justify-content: space-between; align-items: center; margin: 8px 0; gap: 8px; flex-wrap: wrap; }
.mini-input { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }

.issue-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; }
.issue-tunnels { border-right: 1px solid var(--border); padding-right: 16px; }
.data-table.compact th, .data-table.compact td { padding: 5px 8px; font-size: 12px; }
tr.selected { background: #eff6ff; }
.cat-card { display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; margin: 8px 0; cursor: pointer; }
.cat-card input { grid-row: span 2; align-self: center; }
.cat-name { font-weight: 600; font-size: 13px; }
.cat-meta, .cat-items { color: var(--muted); font-size: 12px; grid-column: 2; }
.form-row { display: flex; gap: 10px; margin: 8px 0; }
.form-row label, .full-line { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); flex: 1; }
.form-row input, .full-line input, .full-line textarea { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.check-line { flex-direction: row !important; align-items: center; gap: 6px !important; color: #1f2937 !important; }
.batch-box { border: 1px dashed var(--border); border-radius: 8px; padding: 10px; margin-top: 12px; }
.progress-track { height: 8px; background: #eef2f7; border-radius: 4px; overflow: hidden; }
.progress-bar { height: 100%; background: var(--brand); transition: width 0.5s; }
.progress-bar.danger { background: #d92d20; }

.map-grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 14px; }
.tunnel-map { width: 100%; background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; }
.corridor-line { fill: none; stroke: #94a3b8; stroke-width: 3; stroke-dasharray: 6 4; }
.corridor-line.conflict { stroke: #f04438; }
.tunnel-dot { fill: #12b76a; stroke: #fff; stroke-width: 2; }
.tunnel-dot.closed { fill: #d92d20; }
.tunnel-dot.maintenance { fill: #f79009; }
.tunnel-dot.conflict { stroke: #d92d20; stroke-width: 4; animation: pulse 1.4s infinite; }
@keyframes pulse { 0%, 100% { stroke-opacity: 1; } 50% { stroke-opacity: 0.3; } }
.map-label { font-size: 11px; fill: #334155; }
.window-list { max-height: 440px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.window-card { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12px; }
.window-card.conflict { border-color: #f04438; background: #fef3f2; }
.window-card.approval { border-color: #f79009; background: #fffaeb; }
.window-card header { display: flex; align-items: center; gap: 6px; }
.cat-tag { background: #eff6ff; color: var(--brand); border-radius: 4px; padding: 1px 6px; font-size: 11px; }
.window-card p { margin: 3px 0; }
.date-line { display: flex; align-items: end; gap: 6px; font-size: 12px; color: var(--muted); }

.conclusion-cell { max-width: 180px; font-size: 12px; }
.row-actions.vertical { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.warn-link { color: #b54708; }
.detail-box { margin-top: 14px; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: #fbfdff; }

.badge { display: inline-block; border-radius: 999px; padding: 1px 8px; font-size: 11px; white-space: nowrap; }
.badge-blue { background: #eff6ff; color: #1d4ed8; }
.badge-green { background: #ecfdf3; color: #067647; }
.badge-orange { background: #fffaeb; color: #b54708; }
.badge-red { background: #fef3f2; color: #b42318; }
.badge-gray { background: #f2f4f7; color: #475467; }

.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { background: #fff; border-radius: 10px; padding: 18px 20px; width: 520px; max-width: 92vw; max-height: 88vh; overflow-y: auto; }
.modal h3 { margin: 0 0 6px; font-size: 16px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.danger-btn { border-color: #f04438; color: #b42318; }

@media (max-width: 1100px) {
  .issue-grid, .map-grid { grid-template-columns: 1fr; }
  .issue-tunnels { border-right: none; padding-right: 0; }
}
</style>
