<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import request from '@/api/request'

/* ===================== 类型定义 ===================== */

interface AlertRule {
  id?: number | string
  name: string
  type?: string
  metric?: string
  condition?: string
  threshold?: number | string
  severity?: string
  enabled?: boolean
  last_fired?: string
  last_fired_at?: string
  description?: string
  [key: string]: unknown
}

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

/* ===================== 公共状态 ===================== */

const activeTab = ref<'rules' | 'incidents'>('rules')
const loading = ref(false)

/* ===================== 告警规则 ===================== */

const rules = ref<AlertRule[]>([])
const ruleDialogVisible = ref(false)
const ruleDialogMode = ref<'create' | 'edit'>('create')
const ruleForm = reactive<AlertRule>({
  name: '',
  type: 'metric',
  metric: '',
  condition: '>',
  threshold: 0,
  severity: 'warning',
  enabled: true,
  description: '',
})

const ruleTypeOptions = [
  { label: '指标告警', value: 'metric' },
  { label: '日志告警', value: 'log' },
  { label: '可用性告警', value: 'availability' },
  { label: '自定义', value: 'custom' },
]

const severityOptions = [
  { label: '严重', value: 'critical' },
  { label: '警告', value: 'warning' },
  { label: '信息', value: 'info' },
]

const conditionOptions = [
  { label: '大于 (>)', value: '>' },
  { label: '小于 (<)', value: '<' },
  { label: '大于等于 (>=)', value: '>=' },
  { label: '小于等于 (<=)', value: '<=' },
  { label: '等于 (=)', value: '=' },
]

function severityTagType(severity?: string): 'danger' | 'warning' | 'info' {
  if (severity === 'critical') return 'danger'
  if (severity === 'warning') return 'warning'
  return 'info'
}

function severityText(severity?: string): string {
  if (severity === 'critical') return '严重'
  if (severity === 'warning') return '警告'
  if (severity === 'info') return '信息'
  return severity || '-'
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

async function loadRules() {
  loading.value = true
  try {
    const res = await request.get('/alerts/rules')
    const data = res.data
    rules.value = Array.isArray(data)
      ? (data as AlertRule[])
      : (data as { items?: AlertRule[] })?.items ||
        (data as { rules?: AlertRule[] })?.rules ||
        (data as { data?: AlertRule[] })?.data ||
        []
  } catch {
    // 错误提示由拦截器处理
  } finally {
    loading.value = false
  }
}

function openCreateRule() {
  ruleDialogMode.value = 'create'
  Object.assign(ruleForm, {
    id: undefined,
    name: '',
    type: 'metric',
    metric: '',
    condition: '>',
    threshold: 0,
    severity: 'warning',
    enabled: true,
    description: '',
  })
  ruleDialogVisible.value = true
}

function openEditRule(row: AlertRule) {
  ruleDialogMode.value = 'edit'
  Object.assign(ruleForm, {
    id: row.id,
    name: row.name,
    type: row.type || 'metric',
    metric: row.metric || '',
    condition: row.condition || '>',
    threshold: row.threshold ?? 0,
    severity: row.severity || 'warning',
    enabled: row.enabled ?? true,
    description: row.description || '',
  })
  ruleDialogVisible.value = true
}

async function saveRule() {
  if (!ruleForm.name.trim()) {
    ElMessage.warning('请填写规则名称')
    return
  }
  try {
    if (ruleDialogMode.value === 'create') {
      await request.post('/alerts/rules', ruleForm)
      ElMessage.success('告警规则创建成功')
    } else {
      await request.put(`/alerts/rules/${ruleForm.id}`, ruleForm)
      ElMessage.success('告警规则更新成功')
    }
    ruleDialogVisible.value = false
    await loadRules()
  } catch {
    // 错误提示由拦截器处理
  }
}

async function toggleRule(row: AlertRule) {
  try {
    await request.patch(`/alerts/rules/${row.id}`, {
      enabled: !row.enabled,
    })
    row.enabled = !row.enabled
    ElMessage.success(`规则已${row.enabled ? '启用' : '禁用'}`)
  } catch {
    // 错误提示由拦截器处理
  }
}

async function deleteRule(row: AlertRule) {
  try {
    await ElMessageBox.confirm(
      `确定删除告警规则「${row.name}」吗？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确定删除', cancelButtonText: '取消' },
    )
    await request.delete(`/alerts/rules/${row.id}`)
    ElMessage.success('规则已删除')
    await loadRules()
  } catch (e) {
    if (e !== 'cancel') {
      // 接口错误由拦截器处理
    }
  }
}

/* ===================== 事件列表 ===================== */

const incidents = ref<Incident[]>([])
const incidentDialogVisible = ref(false)
const currentIncident = ref<Incident | null>(null)
const incidentFilter = reactive({
  severity: '',
  status: '',
})

const statusOptions = [
  { label: '全部', value: '' },
  { label: '触发中', value: 'firing' },
  { label: '已确认', value: 'acknowledged' },
  { label: '已静默', value: 'silenced' },
  { label: '已解决', value: 'resolved' },
]

const incidentSeverityOptions = [
  { label: '全部', value: '' },
  { label: '严重', value: 'critical' },
  { label: '警告', value: 'warning' },
  { label: '信息', value: 'info' },
]

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

const filteredIncidents = computed(() => {
  return incidents.value.filter((inc) => {
    if (incidentFilter.severity && inc.severity !== incidentFilter.severity) return false
    if (incidentFilter.status && inc.status !== incidentFilter.status) return false
    return true
  })
})

async function loadIncidents() {
  loading.value = true
  try {
    const res = await request.get('/alerts/incidents')
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

function openIncidentDetail(row: Incident) {
  currentIncident.value = row
  incidentDialogVisible.value = true
}

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

/* ===================== 刷新与初始化 ===================== */

async function refresh() {
  if (activeTab.value === 'rules') {
    await loadRules()
  } else {
    await loadIncidents()
  }
  ElMessage.success('数据已刷新')
}

function handleTabChange() {
  refresh()
}

onMounted(async () => {
  await loadRules()
  await loadIncidents()
})
</script>

<template>
  <div class="alerts-view">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="card-title">告警管理</span>
          <el-button :icon="Refresh" size="small" @click="refresh">刷新</el-button>
        </div>
      </template>

      <el-tabs v-model="activeTab" @tab-change="handleTabChange">
        <!-- ==================== 告警规则 Tab ==================== -->
        <el-tab-pane label="告警规则" name="rules">
          <div class="tab-toolbar">
            <el-tag size="small" type="info">共 {{ rules.length }} 条规则</el-tag>
            <el-button type="primary" :icon="Plus" @click="openCreateRule">
              新建规则
            </el-button>
          </div>

          <el-table
            :data="rules"
            v-loading="loading"
            stripe
            style="width: 100%"
            empty-text="暂无告警规则"
          >
            <el-table-column prop="name" label="规则名称" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.name }}</template>
            </el-table-column>
            <el-table-column label="类型" width="120">
              <template #default="{ row }">{{ getVal(row, 'type') }}</template>
            </el-table-column>
            <el-table-column label="严重级别" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="severityTagType(row.severity)">
                  {{ severityText(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="阈值条件" min-width="160">
              <template #default="{ row }">
                {{ getVal(row, 'metric') }} {{ getVal(row, 'condition') }} {{ getVal(row, 'threshold') }}
              </template>
            </el-table-column>
            <el-table-column label="启用状态" width="100" align="center">
              <template #default="{ row }">
                <el-switch
                  :model-value="row.enabled"
                  @change="toggleRule(row)"
                />
              </template>
            </el-table-column>
            <el-table-column label="最近触发" width="180">
              <template #default="{ row }">
                {{ fmtTime(row.last_fired || row.last_fired_at) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" align="center" fixed="right">
              <template #default="{ row }">
                <el-button type="primary" link size="small" @click.stop="openEditRule(row)">
                  编辑
                </el-button>
                <el-button type="danger" link size="small" @click.stop="deleteRule(row)">
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- ==================== 事件列表 Tab ==================== -->
        <el-tab-pane label="事件列表" name="incidents">
          <div class="tab-toolbar">
            <div class="filter-group">
              <el-select
                v-model="incidentFilter.severity"
                placeholder="严重级别"
                clearable
                style="width: 140px"
              >
                <el-option
                  v-for="opt in incidentSeverityOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
              <el-select
                v-model="incidentFilter.status"
                placeholder="状态"
                clearable
                style="width: 140px"
              >
                <el-option
                  v-for="opt in statusOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </div>
            <el-tag size="small" type="info">共 {{ filteredIncidents.length }} 条事件</el-tag>
          </div>

          <el-table
            :data="filteredIncidents"
            v-loading="loading"
            stripe
            style="width: 100%"
            empty-text="暂无告警事件"
            @row-click="openIncidentDetail"
          >
            <el-table-column prop="title" label="事件标题" min-width="200" show-overflow-tooltip>
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
            <el-table-column label="操作" width="260" align="center" fixed="right">
              <template #default="{ row }">
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
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- ==================== 规则创建/编辑弹窗 ==================== -->
    <el-dialog
      v-model="ruleDialogVisible"
      :title="ruleDialogMode === 'create' ? '新建告警规则' : '编辑告警规则'"
      width="560px"
    >
      <el-form :model="ruleForm" label-width="100px">
        <el-form-item label="规则名称">
          <el-input v-model="ruleForm.name" placeholder="请输入规则名称" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="ruleForm.type" style="width: 100%">
            <el-option
              v-for="opt in ruleTypeOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="监控指标">
          <el-input v-model="ruleForm.metric" placeholder="例如：cpu_usage" />
        </el-form-item>
        <el-form-item label="条件">
          <el-select v-model="ruleForm.condition" style="width: 100%">
            <el-option
              v-for="opt in conditionOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="阈值">
          <el-input-number v-model="ruleForm.threshold" :min="0" style="width: 100%" />
        </el-form-item>
        <el-form-item label="严重级别">
          <el-select v-model="ruleForm.severity" style="width: 100%">
            <el-option
              v-for="opt in severityOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="ruleForm.enabled" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="ruleForm.description"
            type="textarea"
            :rows="3"
            placeholder="规则描述（可选）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveRule">确定</el-button>
      </template>
    </el-dialog>

    <!-- ==================== 事件详情弹窗 ==================== -->
    <el-dialog
      v-model="incidentDialogVisible"
      title="事件详情"
      width="640px"
    >
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

        <!-- 时间线 -->
        <div v-if="currentIncident.timeline && currentIncident.timeline.length" class="section-block">
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
.alerts-view {
  display: flex;
  flex-direction: column;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.tab-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;

  .filter-group {
    display: flex;
    align-items: center;
    gap: 8px;
  }
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
