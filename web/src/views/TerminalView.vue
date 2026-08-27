<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  shallowRef,
} from 'vue'
import { ElMessage } from 'element-plus'
import { Link, Refresh, Switch } from '@element-plus/icons-vue'
import request from '@/api/request'

/* ===================== 类型定义 ===================== */

interface ServerItem {
  host: string
  name?: string
  online?: boolean
  [key: string]: unknown
}

/* ===================== 状态 ===================== */

const servers = ref<ServerItem[]>([])
const selectedHost = ref('')
const connected = ref(false)
const connecting = ref(false)
const terminalRef = ref<HTMLElement>()

// 使用 shallowRef 避免对 xterm 实例做深度响应式代理
const term = shallowRef<any>(null)
const fitAddon = shallowRef<any>(null)
let ws: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null

const wsUrl = computed(() => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('token') || ''
  return `${protocol}//${window.location.host}/ws/terminal?token=${encodeURIComponent(token)}&host=${encodeURIComponent(selectedHost.value)}`
})

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

/* ===================== 终端连接 ===================== */

async function connect() {
  if (!selectedHost.value) {
    ElMessage.warning('请先选择一台服务器')
    return
  }
  if (connected.value || connecting.value) return

  connecting.value = true
  try {
    // 动态导入 xterm 及其插件
    const xtermModule = await import('@xterm/xterm')
    const fitModule = await import('@xterm/addon-fit')
    const Terminal = xtermModule.Terminal
    const FitAddon = fitModule.FitAddon

    // 导入 xterm 样式
    await import('@xterm/xterm/css/xterm.css')

    // 创建终端实例
    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "'SFMono-Regular', Consolas, Menlo, monospace",
      theme: {
        background: '#1e1e2e',
        foreground: '#cdd6f4',
        cursor: '#f5e0dc',
        selectionBackground: '#585b70',
      },
    })

    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(terminalRef.value!)
    fit.fit()

    term.value = terminal
    fitAddon.value = fit

    terminal.writeln(`\x1b[32m正在连接到 ${selectedHost.value}...\x1b[0m`)

    // 建立 WebSocket 连接
    ws = new WebSocket(wsUrl.value)

    ws.onopen = () => {
      connected.value = true
      connecting.value = false
      terminal.writeln(`\x1b[32m连接成功 - ${selectedHost.value}\x1b[0m`)
      terminal.writeln('')
      // 发送初始窗口大小
      sendResize()
    }

    ws.onmessage = (event: MessageEvent) => {
      const data = event.data
      if (typeof data === 'string') {
        terminal.write(data)
      } else if (data instanceof Blob) {
        data.text().then((text) => terminal.write(text))
      }
    }

    ws.onerror = () => {
      terminal.writeln('\x1b[31m连接发生错误\x1b[0m')
    }

    ws.onclose = (event: CloseEvent) => {
      connected.value = false
      connecting.value = false
      terminal.writeln(
        `\x1b[33m连接已断开 (code: ${event.code})\x1b[0m`,
      )
    }

    // 终端输入 -> WebSocket 发送
    terminal.onData((data: string) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({ type: 'input', data }),
        )
      }
    })

    // 终端尺寸变化 -> 通知后端
    terminal.onResize(({ cols, rows }: { cols: number; rows: number }) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({ type: 'resize', cols, rows }),
        )
      }
    })

    // 监听容器尺寸变化
    resizeObserver = new ResizeObserver(() => {
      fitAddon.value?.fit()
    })
    resizeObserver.observe(terminalRef.value!)
  } catch {
    connecting.value = false
    ElMessage.error('终端初始化失败')
  }
}

function sendResize() {
  if (fitAddon.value && ws && ws.readyState === WebSocket.OPEN) {
    fitAddon.value.fit()
  }
}

function disconnect() {
  if (ws) {
    ws.close()
    ws = null
  }
  if (term.value) {
    term.value.dispose()
    term.value = null
  }
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  connected.value = false
  connecting.value = false
  ElMessage.success('终端已断开')
}

function clearTerminal() {
  if (term.value) {
    term.value.clear()
  }
}

/* ===================== 生命周期 ===================== */

onMounted(async () => {
  await loadServers()
})

onBeforeUnmount(() => {
  disconnect()
})
</script>

<template>
  <div class="terminal-view">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span class="card-title">
            <el-icon><Monitor /></el-icon>
            Web 终端
          </span>
          <div class="head-right">
            <el-tag
              :type="connected ? 'success' : 'info'"
              size="small"
              effect="light"
            >
              {{ connected ? '已连接' : '未连接' }}
            </el-tag>
          </div>
        </div>
      </template>

      <!-- 操作栏 -->
      <div class="toolbar">
        <div class="toolbar-left">
          <el-select
            v-model="selectedHost"
            placeholder="选择服务器"
            filterable
            :disabled="connected || connecting"
            style="width: 280px"
          >
            <el-option
              v-for="s in servers"
              :key="s.host"
              :label="s.name || s.host"
              :value="s.host"
            >
              <span style="float: left">{{ s.name || s.host }}</span>
              <span style="float: right; color: #909399; font-size: 12px">
                {{ s.host }}
              </span>
            </el-option>
          </el-select>

          <el-button
            v-if="!connected"
            type="primary"
            :icon="Link"
            :loading="connecting"
            @click="connect"
          >
            连接
          </el-button>
          <el-button
            v-else
            type="danger"
            :icon="Switch"
            @click="disconnect"
          >
            断开
          </el-button>

          <el-button :icon="Refresh" @click="clearTerminal" :disabled="!term">
            清屏
          </el-button>
        </div>
      </div>

      <!-- 终端容器 -->
      <div class="terminal-wrapper">
        <div ref="terminalRef" class="terminal-container"></div>
        <div v-if="!connected && !connecting" class="terminal-placeholder">
          <el-icon :size="48" color="#909399"><Monitor /></el-icon>
          <p>请选择服务器并点击「连接」开始会话</p>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped lang="scss">
.terminal-view {
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

  .head-right {
    display: flex;
    align-items: center;
    gap: 8px;
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
}

.terminal-wrapper {
  position: relative;
  width: 100%;
  height: 560px;
  background: #1e1e2e;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #313244;
}

.terminal-container {
  width: 100%;
  height: 100%;
  padding: 8px;
}

.terminal-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #909399;

  p {
    font-size: 14px;
    margin: 0;
  }
}

:deep(.terminal-container .xterm) {
  height: 100%;
}

:deep(.terminal-container .xterm-viewport) {
  overflow-y: auto;
}
</style>
