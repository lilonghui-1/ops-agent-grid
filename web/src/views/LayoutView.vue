<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth = useAuthStore()

const isCollapse = ref(false)

interface MenuItem {
  index: string
  title: string
  icon: string
}

const menus: MenuItem[] = [
  { index: '/dashboard', title: '总览仪表盘', icon: 'Odometer' },
  { index: '/servers', title: '服务器监控', icon: 'Monitor' },
  { index: '/logs', title: '日志查询', icon: 'Document' },
  { index: '/services', title: '应用服务', icon: 'Setting' },
  { index: '/configs', title: '配置管理', icon: 'Tools' },
  { index: '/chat', title: 'AI 对话', icon: 'ChatLineRound' },
  { index: '/audit', title: '审计日志', icon: 'Tickets' },
  { index: '/alerts', title: '告警管理', icon: 'Bell' },
  { index: '/incidents', title: '事件中心', icon: 'Warning' },
  { index: '/approvals', title: '审批管理', icon: 'Check' },
  { index: '/topology', title: '拓扑图', icon: 'Share' },
  { index: '/terminal', title: 'Web 终端', icon: 'Monitor' },
  { index: '/skills', title: '技能目录', icon: 'Grid' },
]

// /servers/:host 等子路由高亮父级 /servers
const activeMenu = computed(() => {
  if (route.path.startsWith('/servers')) return '/servers'
  return route.path
})

function toggleCollapse() {
  isCollapse.value = !isCollapse.value
}

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '退出确认', {
      type: 'warning',
      confirmButtonText: '确定退出',
      cancelButtonText: '取消',
    })
    auth.logout()
  } catch {
    // 用户取消
  }
}

const displayName = computed(
  () => auth.userInfo?.display_name || auth.userInfo?.username || '用户',
)
</script>

<template>
  <el-container class="layout">
    <el-aside :width="isCollapse ? '64px' : '220px'" class="sidebar">
      <div class="logo-bar">
        <el-icon :size="26" color="#409eff"><Monitor /></el-icon>
        <span v-show="!isCollapse" class="logo-text">Ops Agent</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapse"
        :collapse-transition="false"
        router
        background-color="#1f2d3d"
        text-color="#bfcbd9"
        active-text-color="#409eff"
      >
        <el-menu-item v-for="m in menus" :key="m.index" :index="m.index">
          <el-icon><component :is="m.icon" /></el-icon>
          <template #title>{{ m.title }}</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-left">
          <el-icon class="collapse-btn" :size="20" @click="toggleCollapse">
            <Fold v-if="!isCollapse" />
            <Expand v-else />
          </el-icon>
          <span class="page-title">{{ route.meta.title || '运维管理平台' }}</span>
        </div>
        <div class="header-right">
          <el-dropdown trigger="click">
            <span class="user-info">
              <el-icon :size="18"><UserFilled /></el-icon>
              <span class="username">{{ displayName }}</span>
              <el-tag size="small" type="info" effect="plain">
                {{ auth.userInfo?.role || '-' }}
              </el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="handleLogout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped lang="scss">
.layout {
  height: 100%;
}

.sidebar {
  background: #1f2d3d;
  transition: width 0.28s ease;
  overflow-x: hidden;
  overflow-y: auto;

  :deep(.el-menu) {
    border-right: none;
  }
}

.logo-bar {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #fff;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);

  .logo-text {
    font-size: 18px;
    font-weight: 700;
    white-space: nowrap;
    background: linear-gradient(90deg, #409eff, #67c23a);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
  }
}

.header {
  height: 60px;
  background: #fff;
  border-bottom: 1px solid #e6e6e6;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);

  .header-left {
    display: flex;
    align-items: center;
    gap: 12px;

    .collapse-btn {
      cursor: pointer;
      color: #5a5e66;

      &:hover {
        color: #409eff;
      }
    }

    .page-title {
      font-size: 16px;
      font-weight: 600;
      color: #303133;
    }
  }

  .header-right {
    .user-info {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      padding: 6px 10px;
      border-radius: 6px;
      transition: background 0.2s;

      &:hover {
        background: #f5f7fa;
      }

      .username {
        font-size: 14px;
        color: #303133;
      }
    }
  }
}

.main {
  background: #f0f2f5;
  padding: 16px;
  overflow: auto;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
