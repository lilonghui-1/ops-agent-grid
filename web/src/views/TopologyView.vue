<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
} from 'vue'
import { ElMessage } from 'element-plus'
import { Position, Refresh, Share } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import request from '@/api/request'

/* ===================== 类型定义 ===================== */

interface ServerItem {
  host: string
  name?: string
  online?: boolean
  [key: string]: unknown
}

interface TopologyNode {
  id: string
  name: string
  category?: number
  symbolSize?: number
  value?: string
  [key: string]: unknown
}

interface TopologyEdge {
  source: string
  target: string
  [key: string]: unknown
}

interface TopologyData {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
  [key: string]: unknown
}

/* ===================== 状态 ===================== */

const loading = ref(false)
const building = ref(false)
const servers = ref<ServerItem[]>([])
const selectedHost = ref('')
const chartRef = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

const topologyData = ref<TopologyData>({ nodes: [], edges: [] })
const selectedNode = ref<string>('')
const blastRadiusNodes = ref<string[]>([])

// 节点类别
const categories = [
  { name: '服务器' },
  { name: '服务' },
  { name: '数据库' },
  { name: '中间件' },
  { name: '外部依赖' },
]

const categoryColors = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399']

/* ===================== 工具函数 ===================== */

function fmtVal(v: unknown): string {
  if (v == null || v === '') return '-'
  return String(v)
}

/* ===================== 数据加载 ===================== */

async function loadServers() {
  try {
    const res = await request.get<ServerItem[] | { items?: ServerItem[] }>('/servers')
    const raw = res.data
    servers.value = Array.isArray(raw)
      ? raw
      : (raw as { items?: ServerItem[] }).items || []
  } catch {
    // 错误提示由拦截器处理
  }
}

async function loadTopology() {
  loading.value = true
  try {
    const res = await request.get('/topology/')
    const data = res.data as TopologyData
    topologyData.value = {
      nodes: data?.nodes || [],
      edges: data?.edges || [],
    }
    renderChart()
  } catch {
    // 错误提示由拦截器处理
  } finally {
    loading.value = false
  }
}

async function buildTopology() {
  if (!selectedHost.value) {
    ElMessage.warning('请先选择一台服务器')
    return
  }
  building.value = true
  try {
    await request.post(`/topology/build/${encodeURIComponent(selectedHost.value)}`)
    ElMessage.success('拓扑构建请求已发送')
    await loadTopology()
  } catch {
    // 错误提示由拦截器处理
  } finally {
    building.value = false
  }
}

/* ===================== 爆炸半径计算 ===================== */

/** 通过 BFS 计算从指定节点出发可达的所有下游节点（爆炸半径） */
function computeBlastRadius(nodeId: string): string[] {
  const edges = topologyData.value.edges
  const adj = new Map<string, string[]>()
  for (const e of edges) {
    const src = e.source
    const tgt = e.target
    if (!adj.has(src)) adj.set(src, [])
    adj.get(src)!.push(tgt)
  }
  const visited = new Set<string>()
  const queue = [nodeId]
  visited.add(nodeId)
  while (queue.length > 0) {
    const cur = queue.shift()!
    const neighbors = adj.get(cur) || []
    for (const n of neighbors) {
      if (!visited.has(n)) {
        visited.add(n)
        queue.push(n)
      }
    }
  }
  return Array.from(visited)
}

/* ===================== ECharts 渲染 ===================== */

function renderChart() {
  if (!chartRef.value) return
  if (!chart) chart = echarts.init(chartRef.value)

  const nodes = topologyData.value.nodes.map((n) => ({
    id: n.id,
    name: n.name || n.id,
    category: n.category ?? 0,
    symbolSize: n.symbolSize ?? 36,
    value: n.value || '',
    itemStyle: {
      color: categoryColors[n.category ?? 0] || '#409eff',
    },
  }))

  const edges = topologyData.value.edges.map((e) => ({
    source: e.source,
    target: e.target,
  }))

  const option: echarts.EChartsOption = {
    tooltip: {
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const name = params.data.name || params.data.id
          const cat = categories[params.data.category || 0]?.name || ''
          return `<b>${name}</b><br/>类型：${cat}`
        }
        if (params.dataType === 'edge') {
          return `${params.data.source} → ${params.data.target}`
        }
        return ''
      },
    },
    legend: [
      {
        data: categories.map((c) => c.name),
        top: 8,
        textStyle: { color: '#606266' },
      },
    ],
    animationDuration: 800,
    animationEasingUpdate: 'quinticInOut',
    series: [
      {
        type: 'graph',
        layout: 'force',
        data: nodes,
        links: edges,
        categories: categories.map((c, i) => ({
          name: c.name,
          itemStyle: { color: categoryColors[i] },
        })),
        roam: true,
        label: {
          show: true,
          position: 'right',
          fontSize: 12,
          color: '#303133',
        },
        edgeLabel: {
          show: false,
        },
        lineStyle: {
          color: '#c0c4cc',
          width: 1.5,
          curveness: 0.1,
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: {
            width: 3,
            color: '#409eff',
          },
          label: {
            fontSize: 14,
            fontWeight: 'bold',
          },
        },
        force: {
          repulsion: 200,
          edgeLength: 120,
          gravity: 0.1,
        },
      },
    ],
  }

  chart.setOption(option, true)

  // 点击节点：高亮爆炸半径
  chart.off('click')
  chart.on('click', (params: any) => {
    if (params.dataType !== 'node') return
    const nodeId = params.data.id
    if (selectedNode.value === nodeId) {
      // 取消选择
      selectedNode.value = ''
      blastRadiusNodes.value = []
      highlightBlastRadius()
    } else {
      selectedNode.value = nodeId
      blastRadiusNodes.value = computeBlastRadius(nodeId)
      highlightBlastRadius()
      ElMessage.info(
        `节点「${params.data.name}」爆炸半径：影响 ${blastRadiusNodes.value.length} 个节点`,
      )
    }
  })
}

/** 根据爆炸半径高亮节点 */
function highlightBlastRadius() {
  if (!chart) return
  const blastSet = new Set(blastRadiusNodes.value)
  const option = chart.getOption() as any
  const series = option.series?.[0]
  if (!series) return

  const nodes = (topologyData.value.nodes || []).map((n) => {
    const isAffected = blastSet.has(n.id)
    const isSelected = n.id === selectedNode.value
    return {
      id: n.id,
      name: n.name || n.id,
      category: n.category ?? 0,
      symbolSize: isSelected ? 52 : isAffected ? 44 : n.symbolSize ?? 36,
      value: n.value || '',
      itemStyle: {
        color: categoryColors[n.category ?? 0] || '#409eff',
        opacity: blastSet.size === 0 || isAffected ? 1 : 0.25,
        borderColor: isSelected ? '#f56c6c' : 'transparent',
        borderWidth: isSelected ? 4 : 0,
      },
      label: {
        show: blastSet.size === 0 || isAffected,
      },
    }
  })

  const edges = (topologyData.value.edges || []).map((e) => {
    const inBlast = blastSet.has(e.source) && blastSet.has(e.target)
    return {
      source: e.source,
      target: e.target,
      lineStyle: {
        color: inBlast && blastSet.size > 0 ? '#f56c6c' : '#c0c4cc',
        width: inBlast && blastSet.size > 0 ? 3 : 1.5,
        opacity: blastSet.size === 0 || inBlast ? 1 : 0.15,
      },
    }
  })

  chart.setOption(
    {
      series: [
        {
          data: nodes,
          links: edges,
        },
      ],
    },
    { replaceMerge: ['series'] },
  )
}

function clearSelection() {
  selectedNode.value = ''
  blastRadiusNodes.value = []
  highlightBlastRadius()
}

/* ===================== 生命周期 ===================== */

function handleResize() {
  chart?.resize()
}

onMounted(async () => {
  await loadServers()
  await loadTopology()
  await nextTick()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

const hasData = computed(() => topologyData.value.nodes.length > 0)
</script>

<template>
  <div class="topology-view">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="card-title">
            <el-icon><Share /></el-icon>
            拓扑图
          </span>
          <el-button :icon="Refresh" size="small" @click="loadTopology">刷新</el-button>
        </div>
      </template>

      <!-- 操作栏 -->
      <div class="toolbar">
        <div class="toolbar-left">
          <el-select
            v-model="selectedHost"
            placeholder="选择服务器"
            filterable
            clearable
            style="width: 260px"
          >
            <el-option
              v-for="s in servers"
              :key="s.host"
              :label="s.name || s.host"
              :value="s.host"
            />
          </el-select>
          <el-button
            type="primary"
            :icon="Position"
            :loading="building"
            @click="buildTopology"
          >
            构建拓扑
          </el-button>
        </div>
        <div class="toolbar-right">
          <el-tag v-if="selectedNode" type="danger" closable @close="clearSelection">
            选中：{{ selectedNode }}（影响 {{ blastRadiusNodes.length }} 个节点）
          </el-tag>
          <el-tag v-else type="info">点击节点查看爆炸半径</el-tag>
        </div>
      </div>

      <!-- 图表容器 -->
      <div class="chart-wrapper" v-loading="loading">
        <div v-if="hasData" ref="chartRef" class="chart-container"></div>
        <el-empty v-else description="暂无拓扑数据，请选择服务器并构建拓扑" />
      </div>

      <!-- 图例说明 -->
      <div class="legend-bar">
        <div v-for="(cat, idx) in categories" :key="idx" class="legend-item">
          <span
            class="legend-dot"
            :style="{ background: categoryColors[idx] }"
          ></span>
          {{ cat.name }}
        </div>
        <div class="legend-hint">
          提示：拖拽节点可调整位置，滚轮缩放，点击节点高亮爆炸半径
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped lang="scss">
.topology-view {
  display: flex;
  flex-direction: column;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;

  .card-title {
    font-size: 15px;
    font-weight: 600;
    color: #303133;
    display: flex;
    align-items: center;
    gap: 6px;
  }
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;

  .toolbar-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.chart-wrapper {
  width: 100%;
  height: 520px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  background: #fafafa;
}

.chart-container {
  width: 100%;
  height: 100%;
}

.legend-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 12px;
  padding: 12px 0;
  border-top: 1px solid #ebeef5;

  .legend-item {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: #606266;
  }

  .legend-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    display: inline-block;
  }

  .legend-hint {
    font-size: 12px;
    color: #909399;
    margin-left: auto;
  }
}
</style>
