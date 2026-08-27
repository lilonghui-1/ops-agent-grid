"""企业微信通道 - 通过 Webhook URL 发送文本和 Markdown 消息"""

import logging
from typing import Optional

import httpx

from .base import BaseChannel

logger = logging.getLogger(__name__)


class WeComChannel(BaseChannel):
    """企业微信通道 - 支持文本（text）和 Markdown 消息

    沿用已有 Webhook 通知方式，统一为 BaseChannel 接口实现。
    """

    name = "wecom"

    # 告警级别对应的 emoji 标记
    LEVEL_EMOJI = {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
        "critical": "🔴",
    }

    def __init__(self, config: dict):
        """初始化企业微信通道

        Args:
            config: 通道配置字典，需包含:
                - webhook_url: 企业微信机器人 Webhook URL
                - msg_type: 消息类型 text / markdown，默认 markdown
                - timeout: 请求超时时间（秒），默认 10
        """
        self.webhook_url = config.get("webhook_url", "")
        self.msg_type = config.get("msg_type", "markdown")  # text / markdown
        self.timeout = config.get("timeout", 10)

    def _build_payload(self, title: str, content: str, level: str) -> dict:
        """根据消息类型构建企业微信请求体"""
        emoji = self.LEVEL_EMOJI.get(level, "")
        if self.msg_type == "text":
            return {
                "msgtype": "text",
                "text": {"content": f"{emoji} {title}\n\n{content}"},
            }
        # 默认 markdown
        return {
            "msgtype": "markdown",
            "markdown": {"content": f"## {emoji} {title}\n\n{content}"},
        }

    async def send_message(self, title: str, content: str, level: str = "info") -> bool:
        """发送消息到企业微信"""
        if not self.webhook_url:
            logger.warning("企业微信 Webhook URL 未配置，跳过发送")
            return False

        payload = self._build_payload(title, content, level)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.webhook_url, json=payload)
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("企业微信通知发送成功")
                return True
            logger.error(f"企业微信通知发送失败: {result.get('errmsg')}")
            return False
        except httpx.TimeoutException as e:
            logger.error(f"企业微信通知发送超时: {e}")
            return False
        except httpx.ConnectError as e:
            logger.error(f"企业微信通知发送连接失败: {e}")
            return False
        except Exception as e:
            logger.error(f"企业微信通知发送异常: {e}")
            return False

    async def receive_message(self) -> Optional[dict]:
        """企业微信 Webhook 为单向通道，不支持接收消息"""
        return None

    def is_bidirectional(self) -> bool:
        """企业微信 Webhook 为单向通道"""
        return False
