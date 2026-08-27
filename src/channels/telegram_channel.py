"""Telegram 通道 - 通过 Bot API 发送消息"""

import logging
from typing import Optional

import httpx

from .base import BaseChannel

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    """Telegram 通道 - 调用 Telegram Bot API 发送消息

    Bot Token 与 Chat ID 从配置中读取，通过 sendMessage 接口发送。
    """

    name = "telegram"

    # Telegram Bot API 基础地址
    API_BASE = "https://api.telegram.org/bot{token}/{method}"

    # 告警级别对应的 emoji 标记
    LEVEL_EMOJI = {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
        "critical": "🚨",
    }

    def __init__(self, config: dict):
        """初始化 Telegram 通道

        Args:
            config: 通道配置字典，需包含:
                - bot_token: Telegram Bot Token
                - chat_id: 目标会话 ID（群组/用户）
                - parse_mode: 解析模式 Markdown / HTML / None，默认 Markdown
                - timeout: 请求超时时间（秒），默认 10
        """
        self.bot_token = config.get("bot_token", "")
        self.chat_id = config.get("chat_id", "")
        self.parse_mode = config.get("parse_mode", "Markdown")
        self.timeout = config.get("timeout", 10)

    def _api_url(self, method: str) -> str:
        """构建 Bot API 请求地址"""
        return self.API_BASE.format(token=self.bot_token, method=method)

    async def send_message(self, title: str, content: str, level: str = "info") -> bool:
        """发送消息到 Telegram"""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram Bot Token 或 Chat ID 未配置，跳过发送")
            return False

        emoji = self.LEVEL_EMOJI.get(level, "ℹ️")
        text = f"{emoji} *{title}*\n\n{content}"

        payload = {
            "chat_id": self.chat_id,
            "text": text,
        }
        if self.parse_mode:
            payload["parse_mode"] = self.parse_mode

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self._api_url("sendMessage"), json=payload)
            result = resp.json()
            if result.get("ok") is True:
                logger.info("Telegram 通知发送成功")
                return True
            logger.error(
                f"Telegram 通知发送失败: {result.get('description')}"
            )
            return False
        except httpx.TimeoutException as e:
            logger.error(f"Telegram 通知发送超时: {e}")
            return False
        except httpx.ConnectError as e:
            logger.error(f"Telegram 通知发送连接失败: {e}")
            return False
        except Exception as e:
            logger.error(f"Telegram 通知发送异常: {e}")
            return False

    async def receive_message(self) -> Optional[dict]:
        """Telegram 通过 getUpdates 轮询获取消息（双向通道）

        采用短轮询方式拉取最新的一条更新，处理后返回。
        """
        if not self.bot_token:
            logger.warning("Telegram Bot Token 未配置，无法接收消息")
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self._api_url("getUpdates"), json={"limit": 1})
            result = resp.json()
            if result.get("ok") is not True:
                logger.warning(f"Telegram 获取消息失败: {result.get('description')}")
                return None
            updates = result.get("result", [])
            if not updates:
                return None
            update = updates[-1]
            message = update.get("message") or update.get("edited_message")
            if not message:
                return None
            return {
                "channel": self.name,
                "update_id": update.get("update_id"),
                "chat_id": message.get("chat", {}).get("id"),
                "text": message.get("text", ""),
                "raw": message,
            }
        except Exception as e:
            logger.error(f"Telegram 接收消息异常: {e}")
            return None

    def is_bidirectional(self) -> bool:
        """Telegram 支持双向通信"""
        return True
