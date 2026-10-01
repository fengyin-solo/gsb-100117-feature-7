<template>
  <div class="map-wrap">
    <svg class="map-svg" viewBox="0 0 100 72" preserveAspectRatio="none" aria-label="洞群作业窗口影响图">
      <!-- 洞群底纹 -->
      <g v-for="group in groups" :key="group.name">
        <ellipse
          :cx="group.cx"
          :cy="group.cy"
          :rx="group.rx"
          :ry="group.ry"
          class="map-group-area"
          :class="`area-${group.cls}`"
        />
        <text :x="group.cx" :y="group.cy - group.ry + 4" class="map-group-label">{{ group.name }}</text>
      </g>

      <!-- 互相影响连线 -->
      <line
        v-for="(link, index) in data.links"
        :key="`link-${index}`"
        :x1="pointOf(link.from).x"
        :y1="pointOf(link.from).y"
        :x2="pointOf(link.to).x"
        :y2="pointOf(link.to).y"
        class="map-link"
        :class="{ 'link-closure': link.封闭相关 }"
      />

      <!-- 隧道点位 -->
      <g v-for="tunnel in data.tunnels" :key="tunnel.id" class="map-tunnel">
        <circle
          :cx="tunnel.x"
          :cy="tunnel.y"
          :r="2.6"
          class="map-dot"
          :class="{
            'dot-closed': tunnel.status === '封闭',
            'dot-conflict': conflictTunnels.has(tunnel.id),
            'dot-repair': tunnel.status === '维修',
          }"
          @click="$emit('select-tunnel', tunnel.id)"
        />
        <text :x="tunnel.x + 3.4" :y="tunnel.y + 1.1" class="map-tunnel-label">
          {{ tunnel.隧道名称 }}
        </text>
      </g>
    </svg>
    <div class="map-legend">
      <span><i class="legend-dot dot-normal"></i>正常</span>
      <span><i class="legend-dot dot-closed"></i>封闭作业</span>
      <span><i class="legend-dot dot-conflict"></i>窗口冲突待裁</span>
      <span><i class="legend-line"></i>作业窗口互相影响</span>
      <span><i class="legend-line link-closure"></i>封闭相关冲突</span>
    </div>
    <ul v-if="props.data.links.length" class="map-influence">
      <li v-for="(link, index) in props.data.links" :key="`desc-${index}`">
        <strong :class="{ 'text-danger': link.封闭相关 }">{{ link.类型 }}</strong>
        ：任务 #{{ link.from }}（{{ windowOf(link.from).隧道名称 }} ·
        {{ windowOf(link.from).检查班组 }}）与任务 #{{ link.to }}
        （{{ windowOf(link.to).隧道名称 }} · {{ windowOf(link.to).检查班组 }}）窗口重叠
        {{ windowOf(link.from).作业开始 }}~{{ windowOf(link.from).作业结束 }}
      </li>
    </ul>
    <p v-else class="map-empty">当前没有互相影响的在期作业窗口</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface MapWindow {
  task_id: number
  隧道id: number
  隧道名称: string
  洞群: string
  检查类别: string
  检查班组: string
  作业开始: string
  作业结束: string
  封闭作业: boolean
  status: string
  冲突待裁: boolean
}

interface MapTunnel {
  id: number
  隧道编号: string
  隧道名称: string
  洞群: string
  x: number
  y: number
  status: string
}

interface MapLink {
  from: number
  to: number
  类型: string
  封闭相关: boolean
}

const props = defineProps<{
  data: { tunnels: MapTunnel[]; windows: MapWindow[]; links: MapLink[] }
}>()

defineEmits<{ (e: 'select-tunnel', tunnelId: number): void }>()

const groups = computed(() => {
  const names = [...new Set(props.data.tunnels.map((item) => item.洞群))]
  const palette = ['blue', 'green']
  return names.map((name, index) => {
    const points = props.data.tunnels.filter((item) => item.洞群 === name)
    const xs = points.map((item) => item.x)
    const ys = points.map((item) => item.y)
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...ys)
    const maxY = Math.max(...ys)
    return {
      name,
      cls: palette[index % palette.length],
      cx: (minX + maxX) / 2,
      cy: (minY + maxY) / 2,
      rx: Math.max((maxX - minX) / 2 + 9, 16),
      ry: Math.max((maxY - minY) / 2 + 8, 14),
    }
  })
})

const conflictTunnels = computed(
  () =>
    new Set(
      props.data.windows.filter((item) => item.冲突待裁).map((item) => item.隧道id),
    ),
)

function windowOf(taskId: number): MapWindow {
  return props.data.windows.find((item) => item.task_id === taskId) ?? props.data.windows[0]
}

function pointOf(taskId: number): { x: number; y: number } {
  const win = windowOf(taskId)
  const tunnel = props.data.tunnels.find((item) => item.id === win.隧道id)
  return tunnel ? { x: tunnel.x, y: tunnel.y } : { x: 0, y: 0 }
}
</script>

<style scoped>
.map-wrap {
  background: #fff;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
  padding: 12px;
}
.map-svg {
  width: 100%;
  height: 340px;
  display: block;
}
.map-group-area {
  fill: #eff6ff;
  stroke: #bfdbfe;
  stroke-width: 0.3;
  stroke-dasharray: 1.2 1;
}
.area-green {
  fill: #f0fdf4;
  stroke: #bbf7d0;
}
.map-group-label {
  font-size: 3px;
  fill: #475569;
  font-weight: 600;
}
.map-link {
  stroke: #f59e0b;
  stroke-width: 0.5;
  stroke-dasharray: 1.4 1;
}
.map-link.link-closure {
  stroke: #dc2626;
  stroke-width: 0.8;
}
.map-dot {
  fill: #16a34a;
  stroke: #fff;
  stroke-width: 0.5;
  cursor: pointer;
}
.dot-closed {
  fill: #2563eb;
}
.dot-conflict {
  fill: #dc2626;
}
.dot-repair {
  fill: #d97706;
}
.map-tunnel-label {
  font-size: 2.6px;
  fill: #1f2937;
}
.map-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 8px;
  font-size: 12px;
  color: #475569;
}
.legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: -1px;
}
.dot-normal { background: #16a34a; }
.legend-dot.dot-closed { background: #2563eb; }
.legend-dot.dot-conflict { background: #dc2626; }
.legend-line {
  display: inline-block;
  width: 22px;
  border-top: 2px dashed #f59e0b;
  margin-right: 4px;
  vertical-align: 3px;
}
.legend-line.link-closure {
  border-top-color: #dc2626;
}
.map-influence {
  margin: 10px 0 0;
  padding-left: 18px;
  font-size: 12.5px;
  color: #334155;
  display: grid;
  gap: 4px;
}
.text-danger {
  color: #b42318;
}
.map-empty {
  margin: 8px 0 0;
  font-size: 12.5px;
  color: #94a3b8;
}
</style>
