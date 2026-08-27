"""Slack 通道 - 基于 Incoming Webhook 发送消息"""

import logging
from typing import Optional

import httpx

from .base import BaseChannel

logger = logging.getLogger(__name__)

# 尝试导入 slack_sdk，未安装时降级使用 httpx 直接请求 Webhook
try:
    from slack_sdk.webhook import WebhookUrlSender
    HAS_SLACK_SDK = True
except ImportError:
    HAS_SLACK_SDK = False
    WebhookUrlSender = None  # type: ignore


class SlackChannel(BaseChannel):
    """Slack 通道 - 通过 Incoming Webhook URL 发送消息

    若已安装 slack_sdk，则优先使用官方 WebhookUrlSender；
    否则降级为 httpx 直接 POST 到 Webhook URL。
    """

    name = "slack"

    # 告警级别对应的 Slack emoji 标签
    LEVEL_EMOJI = {
        "info": ":information_source:",
        "warning": ":warning:",
        "error": ":x:",
        "critical": ":rotating_light:",
    }

    def __init__(self, config: dict):
        """初始化 Slack 通道

        Args:
            config: 通道配置字典，需包含:
                - webhook_url: Slack Incoming Webhook URL
                - timeout: 请求超时时间（秒），默认 10
        """
        self.webhook_url = config.get("webhook_url", "")
        self.timeout = config.get("timeout", 10)
        # 若 slack_sdk 可用，预创建官方发送器
        self._sender = None
        if HAS_SLACK_SDK and self.webhook_url:
            try:
                self._sender = WebhookUrlSender(self.webhook_url, timeout=self.timeout)
            except Exception as e:
                logger.warning(f"Slack WebhookUrlSender 初始化失败，将降级使用 httpx: {e}")
                self._sender = None

    async def send_message(self, title: str, content: str, level: str = "info") -> bool:
        """发送消息到 Slack"""
        if not self.webhook_url:
            logger.warning("Slack Webhook URL 未配置，跳过发送")
            return False

        emoji = self.LEVEL_EMOJI.get(level, ":information_source:")
        text = f"{emoji} *{title}*\n\n{content}"

        # 优先使用 slack_sdk 官方发送器
        if self._sender is not None:
            try:
                resp = self._sender.send(text=text)
                if resp.status_code == 200 and resp.body == "ok":
                    logger.info("Slack 通知发送成功（slack_sdk）")
                    return True
                logger.error(
                    f"Slack 通知发送失败: status={resp.status_code}, body={resp.body}"
                )
                return False
            except Exception as e:
                logger.error(f"Slack 通知发送异常（slack_sdk），尝试降级 httpx: {e}")
                # 降级到 httpx 继续尝试

        # 降级使用 httpx 异步发送
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.webhook_url, json={"text": text})
            if resp.status_code == 200 and resp.text == "ok":
                logger.info("Slack 通知发送成功（httpx）")
                return True
            logger.error(
                f"Slack 通知发送失败: status={resp.status_code}, body={resp.text}"
            )
            return False
        except Exception as e:
            logger.error(f"Slack 通知发送异常（httpx）: {e}")
            return False

    async def receive_message(self) -> Optional[dict]:
        """Slack Incoming Webhook 为单向通道，不支持接收消息"""
        return None

    def is_bidirectional(self) -> bool:
        """Slack Webhook 为单向通道"""
        return False
