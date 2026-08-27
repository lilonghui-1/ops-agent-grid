"""Web SSH 终端桥接工具 - 提供交互式 Shell 会话管理

直接使用 paramiko 建立交互式 PTY 会话（不经过 SSHConnectionPool，
因为交互式终端需要独占 channel 和实时 I/O）。提供异步接口供 Web 层调用。
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, Optional

import paramiko

from .base import BaseTool, ToolResult, ToolParameter

logger = logging.getLogger(__name__)


class TerminalSession:
    """单个终端会话的上下文容器"""

    def __init__(self, session_id: str, client: paramiko.SSHClient,
                 channel, host: str, width: int, height: int):
        self.session_id = session_id
        self.client = client
        self.channel = channel
        self.host = host
        self.width = width
        self.height = height
        self.created_at = time.time()
        self.last_activity = time.time()

    def touch(self):
        """更新最后活动时间"""
        self.last_activity = time.time()

    def is_active(self) -> bool:
        """检查会话是否仍然活跃"""
        try:
            transport = self.client.get_transport()
            return transport is not None and transport.is_active()
        except Exception:
            return False

    def close(self):
        """关闭会话"""
        try:
            if self.channel:
                self.channel.close()
        except Exception:
            pass
        try:
            if self.client:
                self.client.close()
        except Exception:
            pass


class WebTerminalTool(BaseTool):
    """Web SSH 终端桥接工具 - 管理交互式 Shell 会话

    提供 create_session / resize_session / send_input / get_output 异步接口，
    供 Web 前端通过 WebSocket 调用。会话存储在类级别字典中，跨实例共享。
    """

    name = "web_terminal"
    description = ("创建和管理 Web SSH 交互式终端会话。支持创建会话、调整终端"
                   "尺寸、发送输入、获取输出。用于在 Web 界面中提供远程终端能力。")
    parameters = [
        ToolParameter(
            name="action",
            type="string",
            description=("操作类型: create(创建会话), resize(调整尺寸), "
                         "send(发送输入), get_output(获取输出), "
                         "close(关闭会话), list(列出会话)"),
        ),
        ToolParameter(
            name="host",
            type="string",
            description="目标主机地址（action=create 时必填）",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="session_id",
            type="string",
            description="会话 ID（resize/send/get_output/close 时必填）",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="width",
            type="integer",
            description="终端宽度（字符列数）",
            required=False,
            default=80,
        ),
        ToolParameter(
            name="height",
            type="integer",
            description="终端高度（字符行数）",
            required=False,
            default=24,
        ),
        ToolParameter(
            name="data",
            type="string",
            description="要发送的输入数据（action=send 时使用）",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="username",
            type="string",
            description="SSH 用户名（可选，默认从配置读取）",
            required=False,
            default=None,
        ),
    ]

    # 类级别会话存储：session_id -> TerminalSession
    _sessions: Dict[str, TerminalSession] = {}

    def __init__(self, config=None):
        self._config = config
        self._server_map = {}
        if config and hasattr(config, 'servers'):
            for s in config.servers:
                self._server_map[s.host] = s

    def _find_server(self, host: str):
        """查找服务器配置"""
        return self._server_map.get(host)

    def _create_ssh_client(self, host: str, username: str = None,
                           password: str = None,
                           private_key_path: str = None,
                           port: int = 22) -> paramiko.SSHClient:
        """创建并连接 SSH 客户端（不经过连接池，独立管理生命周期）"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            'hostname': host,
            'port': port,
            'username': username,
            'timeout': 10,
            'banner_timeout': 30,
            'auth_timeout': 30,
        }

        if private_key_path:
            try:
                pkey = paramiko.RSAKey.from_private_key_file(private_key_path)
                connect_kwargs['pkey'] = pkey
            except paramiko.ssh_exception.SSHException:
                # 尝试 Ed25519 密钥
                pkey = paramiko.Ed25519Key.from_private_key_file(private_key_path)
                connect_kwargs['pkey'] = pkey
        elif password:
            connect_kwargs['password'] = password

        client.connect(**connect_kwargs)
        return client

    # ------------------------------------------------------------------ #
    # 同步核心实现（异步方法基于此封装）
    # ------------------------------------------------------------------ #

    def _create_session_sync(self, host: str, width: int = 80,
                             height: int = 24, username: str = None,
                             password: str = None,
                             private_key_path: str = None,
                             port: int = 22) -> str:
        """同步创建终端会话，返回 session_id"""
        # 优先使用配置中的服务器信息
        server = self._find_server(host)
        if server and not username:
            username = server.username
            port = server.port
            if not password:
                password = server.password
            if not private_key_path:
                private_key_path = server.private_key_path

        if not username:
            username = 'root'

        # 建立 SSH 连接
        client = self._create_ssh_client(
            host=host,
            username=username,
            password=password,
            private_key_path=private_key_path,
            port=port,
        )

        # 创建交互式 PTY Shell
        channel = client.invoke_shell(
            term='xterm-256color',
            width=width,
            height=height,
        )
        # 设置非阻塞读取，避免 recv 长时间阻塞
        channel.setblocking(False)

        # 生成唯一会话 ID
        session_id = str(uuid.uuid4())
        session = TerminalSession(
            session_id=session_id,
            client=client,
            channel=channel,
            host=host,
            width=width,
            height=height,
        )
        WebTerminalTool._sessions[session_id] = session

        logger.info(
            f"终端会话已创建: session={session_id}, host={host}, "
            f"size={width}x{height}"
        )
        return session_id

    def _resize_session_sync(self, session_id: str,
                             width: int, height: int) -> None:
        """同步调整终端尺寸"""
        session = self._get_session(session_id)
        if not session:
            raise ValueError(f"会话不存在: {session_id}")

        session.channel.resize_pty(width=width, height=height)
        session.width = width
        session.height = height
        session.touch()
        logger.debug(
            f"终端尺寸已调整: session={session_id}, size={width}x{height}"
        )

    def _send_input_sync(self, session_id: str, data: str) -> int:
        """同步向终端发送输入，返回已发送字节数"""
        session = self._get_session(session_id)
        if not session:
            raise ValueError(f"会话不存在: {session_id}")

        # 将字符串编码为 UTF-8 字节
        encoded = data.encode('utf-8') if isinstance(data, str) else data
        sent = session.channel.sendall(encoded)
        session.touch()
        logger.debug(
            f"已发送输入: session={session_id}, bytes={len(encoded)}"
        )
        return len(encoded)

    def _get_output_sync(self, session_id: str,
                         max_bytes: int = 65536) -> str:
        """同步读取终端输出（非阻塞，读取当前可用数据）"""
        session = self._get_session(session_id)
        if not session:
            raise ValueError(f"会话不存在: {session_id}")

        chunks = []
        total = 0
        # 循环读取所有当前可用的输出数据
        while session.channel.recv_ready() and total < max_bytes:
            remaining = max_bytes - total
            chunk = session.channel.recv(min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)

        session.touch()
        if not chunks:
            return ""

        # 拼接并解码为字符串
        raw = b''.join(chunks)
        try:
            return raw.decode('utf-8', errors='replace')
        except Exception:
            return raw.decode('latin-1', errors='replace')

    def _close_session_sync(self, session_id: str) -> None:
        """同步关闭并清理会话"""
        session = WebTerminalTool._sessions.pop(session_id, None)
        if session:
            session.close()
            logger.info(f"终端会话已关闭: session={session_id}")

    def _get_session(self, session_id: str) -> Optional[TerminalSession]:
        """获取会话，若不存在或已失效返回 None"""
        session = WebTerminalTool._sessions.get(session_id)
        if session is None:
            return None
        if not session.is_active():
            # 会话已失效，自动清理
            self._close_session_sync(session_id)
            return None
        return session

    # ------------------------------------------------------------------ #
    # 异步接口（供 Web 层通过 async/await 调用）
    # ------------------------------------------------------------------ #

    async def create_session(self, host: str, width: int = 80,
                             height: int = 24, username: str = None,
                             password: str = None,
                             private_key_path: str = None,
                             port: int = 22) -> str:
        """异步创建终端会话

        Args:
            host: 目标主机地址
            width: 终端宽度（字符列数，默认 80）
            height: 终端高度（字符行数，默认 24）
            username: SSH 用户名（可选）
            password: SSH 密码（可选）
            private_key_path: SSH 私钥路径（可选）
            port: SSH 端口（默认 22）

        Returns:
            session_id: 会话唯一标识
        """
        return await asyncio.to_thread(
            self._create_session_sync,
            host, width, height, username,
            password, private_key_path, port,
        )

    async def resize_session(self, session_id: str,
                              width: int, height: int) -> None:
        """异步调整终端尺寸

        Args:
            session_id: 会话 ID
            width: 新的终端宽度
            height: 新的终端高度
        """
        await asyncio.to_thread(
            self._resize_session_sync, session_id, width, height
        )

    async def send_input(self, session_id: str, data: str) -> int:
        """异步向终端发送输入

        Args:
            session_id: 会话 ID
            data: 要发送的输入数据

        Returns:
            已发送的字节数
        """
        return await asyncio.to_thread(
            self._send_input_sync, session_id, data
        )

    async def get_output(self, session_id: str,
                         max_bytes: int = 65536) -> str:
        """异步获取终端输出

        Args:
            session_id: 会话 ID
            max_bytes: 单次最大读取字节数

        Returns:
            当前可用的终端输出文本
        """
        return await asyncio.to_thread(
            self._get_output_sync, session_id, max_bytes
        )

    async def close_session(self, session_id: str) -> None:
        """异步关闭终端会话"""
        await asyncio.to_thread(self._close_session_sync, session_id)

    @classmethod
    def list_sessions(cls) -> list:
        """列出所有活跃会话"""
        result = []
        for sid, session in list(cls._sessions.items()):
            result.append({
                "session_id": sid,
                "host": session.host,
                "width": session.width,
                "height": session.height,
                "created_at": session.created_at,
                "last_activity": session.last_activity,
                "active": session.is_active(),
            })
        return result

    @classmethod
    def cleanup_inactive(cls, max_idle_seconds: int = 1800) -> int:
        """清理超时未活动的会话

        Args:
            max_idle_seconds: 最大空闲秒数（默认 30 分钟）

        Returns:
            已清理的会话数
        """
        now = time.time()
        to_remove = []
        for sid, session in list(cls._sessions.items()):
            if (not session.is_active()
                    or (now - session.last_activity) > max_idle_seconds):
                to_remove.append(sid)
        for sid in to_remove:
            session = cls._sessions.pop(sid, None)
            if session:
                session.close()
                logger.info(f"已清理超时会话: session={sid}")
        return len(to_remove)

    # ------------------------------------------------------------------ #
    # BaseTool 同步执行接口（通过 action 参数分发）
    # ------------------------------------------------------------------ #

    def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get('action', '')

        try:
            if action == 'create':
                session_id = self._create_session_sync(
                    host=kwargs['host'],
                    width=kwargs.get('width', 80),
                    height=kwargs.get('height', 24),
                    username=kwargs.get('username'),
                )
                return ToolResult(
                    success=True,
                    data={"session_id": session_id},
                    metadata={"host": kwargs['host']},
                )

            elif action == 'resize':
                self._resize_session_sync(
                    session_id=kwargs['session_id'],
                    width=kwargs.get('width', 80),
                    height=kwargs.get('height', 24),
                )
                return ToolResult(
                    success=True,
                    data={"session_id": kwargs['session_id']},
                    metadata={"action": "resize"},
                )

            elif action == 'send':
                sent = self._send_input_sync(
                    session_id=kwargs['session_id'],
                    data=kwargs.get('data', ''),
                )
                return ToolResult(
                    success=True,
                    data={"bytes_sent": sent},
                    metadata={"session_id": kwargs['session_id']},
                )

            elif action == 'get_output':
                output = self._get_output_sync(
                    session_id=kwargs['session_id'],
                )
                return ToolResult(
                    success=True,
                    data={"output": output},
                    metadata={"session_id": kwargs['session_id']},
                )

            elif action == 'close':
                self._close_session_sync(kwargs['session_id'])
                return ToolResult(
                    success=True,
                    data={"closed": kwargs['session_id']},
                )

            elif action == 'list':
                return ToolResult(
                    success=True,
                    data={"sessions": self.list_sessions()},
                )

            else:
                valid_actions = ('create', 'resize', 'send',
                                 'get_output', 'close', 'list')
                return ToolResult(
                    success=False,
                    error=f"不支持的操作: {action}，支持: {', '.join(valid_actions)}",
                )

        except KeyError as e:
            return ToolResult(
                success=False,
                error=f"缺少必要参数: {e}",
            )
        except paramiko.AuthenticationException as e:
            return ToolResult(
                success=False,
                error=f"SSH 认证失败: {e}",
                metadata={"action": action},
            )
        except paramiko.SSHException as e:
            return ToolResult(
                success=False,
                error=f"SSH 连接错误: {e}",
                metadata={"action": action},
            )
        except ValueError as e:
            return ToolResult(
                success=False,
                error=str(e),
                metadata={"action": action},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"终端操作失败: {type(e).__name__}: {e}",
                metadata={"action": action},
            )
