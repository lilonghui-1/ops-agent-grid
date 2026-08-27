<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Grid, Refresh, Search } from '@element-plus/icons-vue'
import request from '@/api/request'

/* ===================== 类型定义 ===================== */

interface SkillParam {
  name: string
  type?: string
  description?: string
  required?: boolean
  default?: unknown
  [key: string]: unknown
}

interface SkillItem {
  id?: number | string
  name: string
  tool_name?: string
  description?: string
  category?: string
  params?: SkillParam[]
  parameters?: SkillParam[]
  param_count?: number
  [key: string]: unknown
}

/* ===================== 状态 ===================== */

const loading = ref(false)
const skills = ref<SkillItem[]>([])
const categories = ref<string[]>([])
const keyword = ref('')
const selectedCategory = ref('')
const detailVisible = ref(false)
const currentSkill = ref<SkillItem | null>(null)

/* ===================== 工具函数 ===================== */

function getParams(skill: SkillItem): SkillParam[] {
  return skill.params || skill.parameters || []
}

function paramCount(skill: SkillItem): number {
  if (skill.param_count != null) return skill.param_count
  return getParams(skill).length
}

function getCategory(skill: SkillItem): string {
  return skill.category || '未分类'
}

/* ===================== 数据加载 ===================== */

async function loadCategories() {
  try {
    const res = await request.get('/skills/categories')
    const data = res.data
    if (Array.isArray(data)) {
      categories.value = (data as string[]).map((c) => String(c))
    } else if (data && typeof data === 'object') {
      const arr = (data as { categories?: string[] }).categories
      if (Array.isArray(arr)) categories.value = arr.map((c) => String(c))
    }
  } catch {
    // 错误提示由拦截器处理
  }
}

async function loadSkills() {
  loading.value = true
  try {
    const res = await request.get('/skills/')
    const data = res.data
    const list: SkillItem[] = Array.isArray(data)
      ? (data as SkillItem[])
      : (data as { items?: SkillItem[] })?.items ||
        (data as { skills?: SkillItem[] })?.skills ||
        (data as { data?: SkillItem[] })?.data ||
        []
    skills.value = list
    // 如果接口未返回分类列表，从技能数据中自动提取
    if (categories.value.length === 0) {
      const cats = new Set<string>()
      list.forEach((s) => cats.add(getCategory(s)))
      categories.value = Array.from(cats)
    }
  } catch {
    // 错误提示由拦截器处理
  } finally {
    loading.value = false
  }
}

/* ===================== 筛选与分组 ===================== */

const filteredSkills = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return skills.value.filter((s) => {
    if (selectedCategory.value && getCategory(s) !== selectedCategory.value) {
      return false
    }
    if (!kw) return true
    return (
      (s.name || '').toLowerCase().includes(kw) ||
      (s.tool_name || '').toLowerCase().includes(kw) ||
      (s.description || '').toLowerCase().includes(kw)
    )
  })
})

const groupedSkills = computed(() => {
  const groups: Record<string, SkillItem[]> = {}
  for (const s of filteredSkills.value) {
    const cat = getCategory(s)
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(s)
  }
  return groups
})

const groupKeys = computed(() => Object.keys(groupedSkills.value))

const totalSkills = computed(() => skills.value.length)
const filteredCount = computed(() => filteredSkills.value.length)

/* ===================== 详情弹窗 ===================== */

function openDetail(skill: SkillItem) {
  currentSkill.value = skill
  detailVisible.value = true
}

/* ===================== 操作 ===================== */

async function refresh() {
  await Promise.all([loadCategories(), loadSkills()])
  ElMessage.success('技能目录已刷新')
}

onMounted(async () => {
  await Promise.all([loadCategories(), loadSkills()])
})
</script>

<template>
  <div class="skills-view" v-loading="loading">
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" class="filter-form">
        <el-form-item label="搜索">
          <el-input
            v-model="keyword"
            placeholder="搜索技能名称 / 描述"
            :prefix-icon="Search"
            clearable
            style="width: 260px"
          />
        </el-form-item>
        <el-form-item label="分类">
          <el-select
            v-model="selectedCategory"
            placeholder="全部分类"
            clearable
            style="width: 180px"
          >
            <el-option
              v-for="cat in categories"
              :key="cat"
              :label="cat"
              :value="cat"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button :icon="Refresh" @click="refresh">刷新</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <div class="stats-bar">
      <el-tag type="info" size="large">
        <el-icon><Grid /></el-icon>
        技能总数：{{ totalSkills }}
      </el-tag>
      <el-tag type="success" size="large">
        当前展示：{{ filteredCount }}
      </el-tag>
      <el-tag type="warning" size="large">
        分类数：{{ categories.length }}
      </el-tag>
    </div>

    <!-- 按分类分组的卡片网格 -->
    <div v-if="groupKeys.length === 0" class="empty-state">
      <el-empty description="暂无技能数据" />
    </div>

    <div v-for="group in groupKeys" :key="group" class="skill-group">
      <div class="group-header">
        <span class="group-title">{{ group }}</span>
        <el-tag size="small" type="info">
          {{ groupedSkills[group].length }} 个技能
        </el-tag>
      </div>
      <el-row :gutter="16">
        <el-col
          v-for="skill in groupedSkills[group]"
          :key="String(skill.id || skill.name)"
          :xs="24"
          :sm="12"
          :md="8"
          :lg="6"
        >
          <el-card
            shadow="hover"
            class="skill-card"
            @click="openDetail(skill)"
          >
            <div class="card-header">
              <span class="skill-name">{{ skill.name }}</span>
              <el-tag size="small" type="primary">
                {{ paramCount(skill) }} 参数
              </el-tag>
            </div>
            <div class="card-tool-name" v-if="skill.tool_name">
              工具：{{ skill.tool_name }}
            </div>
            <div class="card-desc">
              {{ skill.description || '暂无描述' }}
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 技能详情弹窗 -->
    <el-dialog v-model="detailVisible" title="技能详情" width="640px">
      <template v-if="currentSkill">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="技能名称">
            {{ currentSkill.name }}
          </el-descriptions-item>
          <el-descriptions-item label="工具名称" v-if="currentSkill.tool_name">
            {{ currentSkill.tool_name }}
          </el-descriptions-item>
          <el-descriptions-item label="分类">
            {{ getCategory(currentSkill) }}
          </el-descriptions-item>
          <el-descriptions-item label="参数数量">
            {{ paramCount(currentSkill) }} 个
          </el-descriptions-item>
          <el-descriptions-item label="描述">
            {{ currentSkill.description || '暂无描述' }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="param-section">
          <h4 class="param-title">参数列表</h4>
          <el-table
            :data="getParams(currentSkill)"
            stripe
            size="small"
            empty-text="该技能无参数"
            style="width: 100%"
          >
            <el-table-column prop="name" label="参数名" width="160">
              <template #default="{ row }">
                <span class="param-name">{{ row.name }}</span>
                <el-tag
                  v-if="row.required"
                  size="small"
                  type="danger"
                  style="margin-left: 6px"
                >
                  必填
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="120">
              <template #default="{ row }">{{ row.type || '-' }}</template>
            </el-table-column>
            <el-table-column label="默认值" width="120">
              <template #default="{ row }">
                {{ row.default != null ? String(row.default) : '-' }}
              </template>
            </el-table-column>
            <el-table-column label="说明" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                {{ row.description || '-' }}
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.skills-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.filter-form {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
}

.stats-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;

  .el-tag {
    display: flex;
    align-items: center;
    gap: 4px;
  }
}

.empty-state {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}

.skill-group {
  margin-bottom: 8px;

  .group-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
    padding-left: 4px;

    .group-title {
      font-size: 16px;
      font-weight: 600;
      color: #303133;
    }
  }
}

.skill-card {
  margin-bottom: 16px;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;

  &:hover {
    transform: translateY(-2px);
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;

    .skill-name {
      font-size: 15px;
      font-weight: 600;
      color: #303133;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .card-tool-name {
    font-size: 12px;
    color: #909399;
    margin-bottom: 6px;
  }

  .card-desc {
    font-size: 13px;
    color: #606266;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    min-height: 39px;
  }
}

.param-section {
  margin-top: 20px;

  .param-title {
    font-size: 14px;
    font-weight: 600;
    color: #303133;
    margin: 0 0 12px 0;
  }
}

.param-name {
  font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
  font-size: 13px;
  color: #409eff;
}
</style>
