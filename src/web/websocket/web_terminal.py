"""WebSocket Web 终端 - 交互式 SSH Shell 会话桥接

通过 WebSocket 建立与远程主机的交互式终端会话，支持：
- 创建会话（create）-> 返回 session_id
- 发送输入（input）-> 将数据写入 SSH Shell
- 调整终端尺寸（resize）-> 调整 PTY 窗口大小
- 关闭会话（close）-> 关闭 SSH 连接

服务器主动推送终端输出（output）。

消息格式：
- 客户端 -> 服务端:
    {action: "create", host: "...", width: 80, height: 24}
    {action: "input", session_id: "...", data: "ls\\n"}
    {action: "resize", session_id: "...", width: 100, height: 30}
    {action: "close", session_id: "..."}

- 服务端 -> 客户端:
    {type: "session_created", session_id: "...", host: "..."}
    {type: "output", session_id: "...", data: "..."}
    {type: "resized", session_id: "...", width: ..., height: ...}
    {type: "closed", session_id: "..."}
    {type: "error", message: "...", session_id?: "..."}
"""
import asyncio
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError

from ..core.security import decode_token
from ...tools.base import ToolRegistry

router = APIRouter()
logger = logging.getLogger(__name__)

# 输出轮询间隔（秒），50ms 保证终端输出低延迟推送
_OUTPUT_POLL_INTERVAL = 0.05


@router.websocket("/terminal")
async def web_terminal(websocket: WebSocket, token: str = ""):
    """Web 终端 WebSocket 端点 - 管理交互式 SSH Shell 会话

    使用 token 查询参数进行认证（与 log_stream.py 保持一致）。
    连接建立后，客户端通过 JSON 消息控制终端会话的生命周期。
    服务器通过后台轮询协程持续推送终端输出。
    """
    # 1. 验证 token
    try:
        decode_token(token)
    except (JWTError, Exception):
        await websocket.close(code=4001)
        return

    await websocket.accept()

    # 2. 获取 WebTerminalTool
    terminal_tool = ToolRegistry.get("web_terminal")
    if not terminal_tool:
        await websocket.send_json(
            {"type": "error", "message": "Web 终端工具未注册"}
        )
        await websocket.close()
        return

    # 本连接管理的会话集合（session_id 字符串）
    managed_sessions: Set[str] = set()
    # 输出轮询协程
    output_task = None

    try:
        # 3. 启动输出轮询任务
        output_task = asyncio.create_task(
            _output_loop(websocket, terminal_tool, managed_sessions)
        )

        # 4. 主循环：接收客户端消息并分发处理
        while True:
            message = await websocket.receive_json()
            action = message.get("action", "")

            if action == "create":
                await _handle_create(websocket, terminal_tool, managed_sessions, message)

            elif action == "input":
                await _handle_input(websocket, terminal_tool, managed_sessions, message)

            elif action == "resize":
                await _handle_resize(websocket, terminal_tool, managed_sessions, message)

            elif action == "close":
                await _handle_close(websocket, terminal_tool, managed_sessions, message)

            else:
                await websocket.send_json(
                    {"type": "error", "message": f"不支持的操作: {action}"}
                )

    except WebSocketDisconnect:
        logger.info("WebSocket 终端连接断开")
    except Exception as e:
        logger.error(f"Web 终端异常: {e}")
    finally:
        # 取消输出轮询任务
        if output_task and not output_task.done():
            output_task.cancel()
            try:
                await output_task
            except asyncio.CancelledError:
                pass

        # 清理本连接管理的所有会话
        for session_id in list(managed_sessions):
            try:
                await terminal_tool.close_session(session_id=session_id)
            except Exception:
                pass
        managed_sessions.clear()


# ---------------------------------------------------------------------------
# 消息处理函数
# ---------------------------------------------------------------------------
async def _handle_create(
    websocket: WebSocket,
    terminal_tool,
    managed_sessions: Set[str],
    message: dict,
):
    """处理创建会话请求"""
    host = message.get("host", "")
    width = message.get("width", 80)
    height = message.get("height", 24)

    if not host:
        await websocket.send_json(
            {"type": "error", "message": "缺少 host 参数"}
        )
        return

    try:
        session_id = await terminal_tool.create_session(
            host=host, width=width, height=height
        )
        managed_sessions.add(session_id)
        await websocket.send_json(
            {
                "type": "session_created",
                "session_id": session_id,
                "host": host,
                "width": width,
                "height": height,
            }
        )
        logger.info(f"终端会话已创建: session={session_id}, host={host}")
    except Exception as e:
        await websocket.send_json(
            {"type": "error", "message": f"创建会话失败: {e}"}
        )


async def _handle_input(
    websocket: WebSocket,
    terminal_tool,
    managed_sessions: Set[str],
    message: dict,
):
    """处理输入请求 - 将客户端输入发送到 SSH Shell"""
    session_id = message.get("session_id", "")
    data = message.get("data", "")

    if not session_id:
        await websocket.send_json(
            {"type": "error", "message": "缺少 session_id 参数"}
        )
        return

    try:
        await terminal_tool.send_input(session_id=session_id, data=data)
    except ValueError as e:
        # 会话不存在或已失效
        managed_sessions.discard(session_id)
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "message": f"会话无效: {e}",
            }
        )
    except Exception as e:
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "message": f"发送输入失败: {e}",
            }
        )


async def _handle_resize(
    websocket: WebSocket,
    terminal_tool,
    managed_sessions: Set[str],
    message: dict,
):
    """处理调整终端尺寸请求"""
    session_id = message.get("session_id", "")
    width = message.get("width", 80)
    height = message.get("height", 24)

    if not session_id:
        await websocket.send_json(
            {"type": "error", "message": "缺少 session_id 参数"}
        )
        return

    try:
        await terminal_tool.resize_session(
            session_id=session_id, width=width, height=height
        )
        await websocket.send_json(
            {
                "type": "resized",
                "session_id": session_id,
                "width": width,
                "height": height,
            }
        )
    except ValueError as e:
        managed_sessions.discard(session_id)
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "message": f"会话无效: {e}",
            }
        )
    except Exception as e:
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "message": f"调整尺寸失败: {e}",
            }
        )


async def _handle_close(
    websocket: WebSocket,
    terminal_tool,
    managed_sessions: Set[str],
    message: dict,
):
    """处理关闭会话请求"""
    session_id = message.get("session_id", "")

    if not session_id:
        await websocket.send_json(
            {"type": "error", "message": "缺少 session_id 参数"}
        )
        return

    try:
        await terminal_tool.close_session(session_id=session_id)
        managed_sessions.discard(session_id)
        await websocket.send_json(
            {"type": "closed", "session_id": session_id}
        )
        logger.info(f"终端会话已关闭: session={session_id}")
    except Exception as e:
        managed_sessions.discard(session_id)
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "message": f"关闭会话失败: {e}",
            }
        )


# ---------------------------------------------------------------------------
# 输出轮询协程
# ---------------------------------------------------------------------------
async def _output_loop(
    websocket: WebSocket,
    terminal_tool,
    managed_sessions: Set[str],
):
    """后台输出轮询协程 - 持续读取所有活跃会话的终端输出并推送到客户端

    每 50ms 轮询一次，从 SSH channel 非阻塞读取可用数据，
    通过 WebSocket 以 {type: "output", session_id, data} 格式推送。
    """
    try:
        while True:
            # 遍历当前管理的所有会话，读取并推送输出
            for session_id in list(managed_sessions):
                try:
                    output = await terminal_tool.get_output(
                        session_id=session_id
                    )
                    if output:
                        await websocket.send_json(
                            {
                                "type": "output",
                                "session_id": session_id,
                                "data": output,
                            }
                        )
                except ValueError:
                    # 会话已失效，从管理集合中移除
                    managed_sessions.discard(session_id)
                except WebSocketDisconnect:
                    return
                except Exception as e:
                    logger.debug(
                        f"读取会话 {session_id} 输出异常: {e}"
                    )

            await asyncio.sleep(_OUTPUT_POLL_INTERVAL)

    except asyncio.CancelledError:
        # 正常取消，静默退出
        pass
    except WebSocketDisconnect:
        logger.info("输出轮询检测到 WebSocket 断开")
    except Exception as e:
        logger.error(f"输出轮询异常: {e}")
