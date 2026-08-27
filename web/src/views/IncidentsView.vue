<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search, View } from '@element-plus/icons-vue'
import request from '@/api/request'

/* ===================== 类型定义 ===================== */

interface Incident {
  id?: number | string
  title: string
  severity?: string
  status?: string
  source?: string
  created_at?: string
  created_at_timestamp?: string
  acknowledged?: boolean
  silenced?: boolean
  rca_report?: string
  root_cause?: string
  timeline?: { time: string; event: string }[]
  [key: string]: unknown
}

/* ===================== 状态 ===================== */

const loading = ref(false)
const incidents = ref<Incident[]>([])
const detailVisible = ref(false)
const currentIncident = ref<Incident | null>(null)

const query = reactive({
  severity: '' as string,
  status: '' as string,
})

const severityOptions = [
  { label: '全部', value: '' },
  { label: '严重', value: 'critical' },
  { label: '警告', value: 'warning' },
  { label: '信息', value: 'info' },
]

const statusOptions = [
  { label: '全部', value: '' },
  { label: '触发中', value: 'firing' },
  { label: '已确认', value: 'acknowledged' },
  { label: '已静默', value: 'silenced' },
  { label: '已解决', value: 'resolved' },
]

/* ===================== 工具函数 ===================== */

function severityTagType(severity?: string): 'danger' | 'warning' | 'info' {
  if (severity === 'critical') return 'danger'
  if (severity === 'warning') return 'warning'
  return 'info'
}

function severityText(severity?: string): string {
  const map: Record<string, string> = {
    critical: '严重',
    warning: '警告',
    info: '信息',
  }
  return map[severity || ''] || severity || '-'
}

function statusTagType(status?: string): 'danger' | 'warning' | 'success' | 'info' {
  if (status === 'firing') return 'danger'
  if (status === 'acknowledged') return 'warning'
  if (status === 'resolved') return 'success'
  return 'info'
}

function statusText(status?: string): string {
  const map: Record<string, string> = {
    firing: '触发中',
    acknowledged: '已确认',
    silenced: '已静默',
    resolved: '已解决',
  }
  return map[status || ''] || status || '-'
}

function fmtTime(t?: string): string {
  if (!t) return '-'
  const d = new Date(t)
  return isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN')
}

function getVal(row: Record<string, unknown>, ...keys: string[]): string {
  for (const k of keys) {
    if (row[k] != null && row[k] !== '') return String(row[k])
  }
  return '-'
}

/* ===================== 筛选 ===================== */

const filteredIncidents = computed(() => {
  return incidents.value.filter((inc) => {
    if (query.severity && inc.severity !== query.severity) return false
    if (query.status && inc.status !== query.status) return false
    return true
  })
})

/* ===================== 数据加载 ===================== */

async function loadData() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {}
    if (query.severity) params.severity = query.severity
    if (query.status) params.status = query.status
    const res = await request.get('/alerts/incidents', { params })
    const data = res.data
    incidents.value = Array.isArray(data)
      ? (data as Incident[])
      : (data as { items?: Incident[] })?.items ||
        (data as { incidents?: Incident[] })?.incidents ||
        (data as { data?: Incident[] })?.data ||
        []
  } catch {
    // 错误提示由拦截器处理
  } finally {
    loading.value = false
  }
}

/* ===================== 详情弹窗 ===================== */

function openDetail(row: Incident) {
  currentIncident.value = row
  detailVisible.value = true
}

/* ===================== 事件操作 ===================== */

async function acknowledgeIncident(row: Incident) {
  try {
    await request.post(`/alerts/incidents/${row.id}/acknowledge`)
    row.status = 'acknowledged'
    ElMessage.success('事件已确认')
  } catch {
    // 错误提示由拦截器处理
  }
}

async function resolveIncident(row: Incident) {
  try {
    await ElMessageBox.confirm(
      `确定将事件「${row.title}」标记为已解决吗？`,
      '解决确认',
      { type: 'warning', confirmButtonText: '确定解决', cancelButtonText: '取消' },
    )
    await request.post(`/alerts/incidents/${row.id}/resolve`)
    row.status = 'resolved'
    ElMessage.success('事件已解决')
  } catch (e) {
    if (e !== 'cancel') {
      // 接口错误由拦截器处理
    }
  }
}

async function silenceIncident(row: Incident) {
  try {
    await ElMessageBox.confirm(
      `确定静默事件「${row.title}」吗？静默期间不会再产生通知。`,
      '静默确认',
      { type: 'warning', confirmButtonText: '确定静默', cancelButtonText: '取消' },
    )
    await request.post(`/alerts/incidents/${row.id}/silence`)
    row.status = 'silenced'
    ElMessage.success('事件已静默')
  } catch (e) {
    if (e !== 'cancel') {
      // 接口错误由拦截器处理
    }
  }
}

/* ===================== 操作 ===================== */

async function handleSearch() {
  await loadData()
  ElMessage.success('查询完成')
}

async function handleReset() {
  query.severity = ''
  query.status = ''
  await loadData()
}

async function refresh() {
  await loadData()
  ElMessage.success('数据已刷新')
}

onMounted(loadData)
</script>

<template>
  <div class="incidents-view">
    <!-- 筛选区 -->
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" class="filter-form">
        <el-form-item label="严重级别">
          <el-select
            v-model="query.severity"
            placeholder="全部"
            clearable
            style="width: 150px"
          >
            <el-option
              v-for="opt in severityOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="query.status"
            placeholder="全部"
            clearable
            style="width: 150px"
          >
            <el-option
              v-for="opt in statusOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="handleSearch">查询</el-button>
          <el-button :icon="Refresh" @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 事件列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="card-title">事件中心</span>
          <div class="head-right">
            <el-tag size="small" type="info">共 {{ filteredIncidents.length }} 条</el-tag>
            <el-button :icon="Refresh" size="small" @click="refresh">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table
        :data="filteredIncidents"
        v-loading="loading"
        stripe
        style="width: 100%"
        empty-text="暂无事件数据"
        @row-click="openDetail"
      >
        <el-table-column prop="title" label="事件标题" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title }}</template>
        </el-table-column>
        <el-table-column label="严重级别" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="severityTagType(row.severity)">
              {{ severityText(row.severity) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTagType(row.status)">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" min-width="140">
          <template #default="{ row }">{{ getVal(row, 'source') }}</template>
        </el-table-column>
        <el-table-column label="触发时间" width="180">
          <template #default="{ row }">
            {{ fmtTime(row.created_at || row.created_at_timestamp) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              type="primary"
              link
              size="small"
              :icon="View"
              @click.stop="openDetail(row)"
            >
              详情
            </el-button>
            <el-button
              type="warning"
              link
              size="small"
              :disabled="row.status !== 'firing'"
              @click.stop="acknowledgeIncident(row)"
            >
              确认
            </el-button>
            <el-button
              type="info"
              link
              size="small"
              :disabled="row.status === 'resolved' || row.status === 'silenced'"
              @click.stop="silenceIncident(row)"
            >
              静默
            </el-button>
            <el-button
              type="success"
              link
              size="small"
              :disabled="row.status === 'resolved'"
              @click.stop="resolveIncident(row)"
            >
              解决
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 事件详情弹窗 -->
    <el-dialog v-model="detailVisible" title="事件详情" width="680px">
      <template v-if="currentIncident">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="事件标题">
            {{ currentIncident.title }}
          </el-descriptions-item>
          <el-descriptions-item label="严重级别">
            <el-tag size="small" :type="severityTagType(currentIncident.severity)">
              {{ severityText(currentIncident.severity) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag size="small" :type="statusTagType(currentIncident.status)">
              {{ statusText(currentIncident.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="来源">
            {{ getVal(currentIncident, 'source') }}
          </el-descriptions-item>
          <el-descriptions-item label="触发时间">
            {{ fmtTime(currentIncident.created_at || currentIncident.created_at_timestamp) }}
          </el-descriptions-item>
        </el-descriptions>

        <!-- 事件时间线 -->
        <div
          v-if="currentIncident.timeline && currentIncident.timeline.length"
          class="section-block"
        >
          <h4 class="block-title">事件时间线</h4>
          <el-timeline>
            <el-timeline-item
              v-for="(item, idx) in currentIncident.timeline"
              :key="idx"
              :timestamp="item.time"
              placement="top"
            >
              {{ item.event }}
            </el-timeline-item>
          </el-timeline>
        </div>

        <!-- RCA 报告 -->
        <div class="section-block">
          <h4 class="block-title">根因分析报告（RCA）</h4>
          <pre class="detail-pre">{{
            currentIncident.rca_report ||
            currentIncident.root_cause ||
            '暂无根因分析报告'
          }}</pre>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.incidents-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.filter-form {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;

  .head-right {
    display: flex;
    align-items: center;
    gap: 12px;
  }
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

:deep(.el-table__row) {
  cursor: pointer;
}

.section-block {
  margin-top: 20px;

  .block-title {
    font-size: 14px;
    font-weight: 600;
    color: #303133;
    margin: 0 0 12px 0;
  }
}

.detail-pre {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 6px;
  font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
  font-size: 12px;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  max-height: 300px;
  overflow-y: auto;
}
</style>
