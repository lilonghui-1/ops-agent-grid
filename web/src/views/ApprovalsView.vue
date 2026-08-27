<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search, View } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import request from '@/api/request'

const auth = useAuthStore()

interface ApprovalItem {
  id?: number | string
  title: string
  tool_name?: string
  risk_level?: string
  status?: string
  requested_by?: string
  requester?: string
  requested_at?: string
  created_at?: string
  impact_analysis?: string
  rollback_plan?: string
  tool_params?: Record<string, unknown> | string
  reason?: string
  review_comment?: string
  [key: string]: unknown
}

const loading = ref(false)
const tableData = ref<ApprovalItem[]>([])
const detailVisible = ref(false)
const currentRow = ref<ApprovalItem | null>(null)
const reviewDialogVisible = ref(false)
const reviewMode = ref<'approve' | 'reject'>('approve')
const reviewComment = ref('')

const query = reactive({
  status: '' as string,
})

const statusOptions = [
  { label: '全部', value: '' },
  { label: '待审批', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
  { label: '已过期', value: 'expired' },
]

const riskOptions = [
  { label: '全部', value: '' },
  { label: '低风险', value: 'low' },
  { label: '中风险', value: 'medium' },
  { label: '高风险', value: 'high' },
]

function statusTagType(status?: string): 'warning' | 'success' | 'danger' | 'info' {
  if (status === 'pending') return 'warning'
  if (status === 'approved') return 'success'
  if (status === 'rejected') return 'danger'
  return 'info'
}

function statusText(status?: string): string {
  const map: Record<string, string> = {
    pending: '待审批',
    approved: '已通过',
    rejected: '已拒绝',
    expired: '已过期',
  }
  return map[status || ''] || status || '-'
}

function riskTagType(risk?: string): 'success' | 'warning' | 'danger' | 'info' {
  if (risk === 'high') return 'danger'
  if (risk === 'medium') return 'warning'
  if (risk === 'low') return 'success'
  return 'info'
}

function riskText(risk?: string): string {
  const map: Record<string, string> = {
    high: '高风险',
    medium: '中风险',
    low: '低风险',
  }
  return map[risk || ''] || risk || '-'
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

function formatParams(params?: Record<string, unknown> | string): string {
  if (!params) return '-'
  if (typeof params === 'string') return params
  try {
    return JSON.stringify(params, null, 2)
  } catch {
    return String(params)
  }
}

const isAdmin = computed(() => {
  const role = auth.userInfo?.role
  return role === 'admin' || role === 'administrator'
})

function isOwn(row: ApprovalItem): boolean {
  const user = auth.userInfo?.username || ''
  return (
    getVal(row, 'requested_by', 'requester') === user ||
    String(row.requested_by || row.requester || '') === String(auth.userInfo?.id || '')
  )
}

const filteredData = computed(() => {
  if (!query.status) return tableData.value
  return tableData.value.filter((item) => item.status === query.status)
})

async function loadData() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {}
    if (query.status) params.status = query.status
    const res = await request.get('/approvals/', { params })
    const data = res.data
    tableData.value = Array.isArray(data)
      ? (data as ApprovalItem[])
      : (data as { items?: ApprovalItem[] })?.items ||
        (data as { approvals?: ApprovalItem[] })?.approvals ||
        (data as { data?: ApprovalItem[] })?.data ||
        []
  } catch {
    // 错误提示由拦截器处理
  } finally {
    loading.value = false
  }
}

async function handleSearch() {
  await loadData()
  ElMessage.success('查询完成')
}

async function handleReset() {
  query.status = ''
  await loadData()
}

function openDetail(row: ApprovalItem) {
  currentRow.value = row
  detailVisible.value = true
}

function openReview(row: ApprovalItem, mode: 'approve' | 'reject') {
  currentRow.value = row
  reviewMode.value = mode
  reviewComment.value = ''
  reviewDialogVisible.value = true
}

async function submitReview() {
  if (!currentRow.value?.id) return
  try {
    await request.post(`/approvals/${currentRow.value.id}/review`, {
      action: reviewMode.value,
      comment: reviewComment.value,
    })
    ElMessage.success(reviewMode.value === 'approve' ? '审批通过' : '已拒绝')
    reviewDialogVisible.value = false
    await loadData()
  } catch {
    // 错误提示由拦截器处理
  }
}

async function cancelApproval(row: ApprovalItem) {
  try {
    await ElMessageBox.confirm(
      `确定撤销审批请求「${row.title}」吗？`,
      '撤销确认',
      { type: 'warning', confirmButtonText: '确定撤销', cancelButtonText: '取消' },
    )
    await request.post(`/approvals/${row.id}/cancel`)
    ElMessage.success('审批请求已撤销')
    await loadData()
  } catch (e) {
    if (e !== 'cancel') {
      // 接口错误由拦截器处理
    }
  }
}

async function refresh() {
  await loadData()
  ElMessage.success('数据已刷新')
}

onMounted(loadData)
</script>

<template>
  <div class="approvals-view">
    <!-- 筛选区 -->
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" class="filter-form">
        <el-form-item label="状态">
          <el-select
            v-model="query.status"
            placeholder="全部"
            clearable
            style="width: 160px"
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

    <!-- 审批列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="card-title">审批管理</span>
          <div class="head-right">
            <el-tag size="small" type="info">共 {{ filteredData.length }} 条</el-tag>
            <el-button :icon="Refresh" size="small" @click="refresh">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table
        :data="filteredData"
        v-loading="loading"
        stripe
        style="width: 100%"
        empty-text="暂无审批请求"
        @row-click="openDetail"
      >
        <el-table-column prop="title" label="审批标题" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title }}</template>
        </el-table-column>
        <el-table-column label="工具名称" width="140">
          <template #default="{ row }">{{ getVal(row, 'tool_name') }}</template>
        </el-table-column>
        <el-table-column label="风险级别" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="riskTagType(row.risk_level)">
              {{ riskText(row.risk_level) }}
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
        <el-table-column label="申请人" width="120">
          <template #default="{ row }">
            {{ getVal(row, 'requested_by', 'requester') }}
          </template>
        </el-table-column>
        <el-table-column label="申请时间" width="180">
          <template #default="{ row }">
            {{ fmtTime(row.requested_at || row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" :icon="View" @click.stop="openDetail(row)">
              详情
            </el-button>
            <template v-if="row.status === 'pending'">
              <el-button
                v-if="isAdmin"
                type="success"
                link
                size="small"
                @click.stop="openReview(row, 'approve')"
              >
                通过
              </el-button>
              <el-button
                v-if="isAdmin"
                type="danger"
                link
                size="small"
                @click.stop="openReview(row, 'reject')"
              >
                拒绝
              </el-button>
              <el-button
                v-if="isOwn(row)"
                type="warning"
                link
                size="small"
                @click.stop="cancelApproval(row)"
              >
                撤销
              </el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailVisible" title="审批详情" width="680px">
      <template v-if="currentRow">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="审批标题">
            {{ currentRow.title }}
          </el-descriptions-item>
          <el-descriptions-item label="工具名称">
            {{ getVal(currentRow, 'tool_name') }}
          </el-descriptions-item>
          <el-descriptions-item label="风险级别">
            <el-tag size="small" :type="riskTagType(currentRow.risk_level)">
              {{ riskText(currentRow.risk_level) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag size="small" :type="statusTagType(currentRow.status)">
              {{ statusText(currentRow.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="申请人">
            {{ getVal(currentRow, 'requested_by', 'requester') }}
          </el-descriptions-item>
          <el-descriptions-item label="申请时间">
            {{ fmtTime(currentRow.requested_at || currentRow.created_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <!-- 影响分析 -->
        <div class="section-block">
          <h4 class="block-title">影响分析</h4>
          <pre class="detail-pre">{{
            currentRow.impact_analysis || '暂无影响分析'
          }}</pre>
        </div>

        <!-- 回滚计划 -->
        <div class="section-block">
          <h4 class="block-title">回滚计划</h4>
          <pre class="detail-pre">{{
            currentRow.rollback_plan || '暂无回滚计划'
          }}</pre>
        </div>

        <!-- 工具参数 -->
        <div class="section-block">
          <h4 class="block-title">工具参数</h4>
          <pre class="detail-pre">{{
            formatParams(currentRow.tool_params as Record<string, unknown> | string)
          }}</pre>
        </div>

        <!-- 审批意见 -->
        <div v-if="currentRow.review_comment" class="section-block">
          <h4 class="block-title">审批意见</h4>
          <pre class="detail-pre">{{ currentRow.review_comment }}</pre>
        </div>
      </template>
    </el-dialog>

    <!-- 审批弹窗 -->
    <el-dialog
      v-model="reviewDialogVisible"
      :title="reviewMode === 'approve' ? '通过审批' : '拒绝审批'"
      width="480px"
    >
      <el-form label-width="80px">
        <el-form-item label="审批标题">
          <span>{{ currentRow?.title }}</span>
        </el-form-item>
        <el-form-item label="审批意见">
          <el-input
            v-model="reviewComment"
            type="textarea"
            :rows="4"
            :placeholder="reviewMode === 'approve' ? '请输入通过意见（可选）' : '请输入拒绝原因'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviewDialogVisible = false">取消</el-button>
        <el-button
          :type="reviewMode === 'approve' ? 'success' : 'danger'"
          @click="submitReview"
        >
          {{ reviewMode === 'approve' ? '确认通过' : '确认拒绝' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.approvals-view {
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
  max-height: 240px;
  overflow-y: auto;
}
</style>
