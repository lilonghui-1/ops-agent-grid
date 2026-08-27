"""钉钉通道 - 通过 Webhook URL 发送文本和 Markdown 消息"""

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Optional

import httpx

from .base import BaseChannel

logger = logging.getLogger(__name__)


class DingTalkChannel(BaseChannel):
    """钉钉通道 - 支持文本（text）和 Markdown 消息

    在已有 Webhook 通知基础上扩展为统一的通道接口，
    支持消息类型切换与可选的加签鉴权。
    """

    name = "dingtalk"

    # 告警级别对应的 emoji 标记
    LEVEL_EMOJI = {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "❌",
        "critical": "🔴",
    }

    def __init__(self, config: dict):
        """初始化钉钉通道

        Args:
            config: 通道配置字典，需包含:
                - webhook_url: 钉钉机器人 Webhook URL
                - msg_type: 消息类型 text / markdown，默认 markdown
                - secret: 加签密钥（可选，启用签名校验时填写）
                - timeout: 请求超时时间（秒），默认 10
        """
        self.webhook_url = config.get("webhook_url", "")
        self.msg_type = config.get("msg_type", "markdown")  # text / markdown
        self.secret = config.get("secret", "")
        self.timeout = config.get("timeout", 10)

    def _build_signed_url(self) -> str:
        """根据加签密钥生成带签名的 Webhook URL"""
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"

    def _build_payload(self, title: str, content: str, level: str) -> dict:
        """根据消息类型构建钉钉请求体"""
        emoji = self.LEVEL_EMOJI.get(level, "")
        if self.msg_type == "text":
            return {
                "msgtype": "text",
                "text": {"content": f"{emoji} {title}\n\n{content}"},
            }
        # 默认 markdown
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": f"{emoji} {title}",
                "text": f"## {emoji} {title}\n\n{content}",
            },
        }

    async def send_message(self, title: str, content: str, level: str = "info") -> bool:
        """发送消息到钉钉"""
        if not self.webhook_url:
            logger.warning("钉钉 Webhook URL 未配置，跳过发送")
            return False

        # 若配置了加签密钥，则使用带签名的 URL
        url = self._build_signed_url() if self.secret else self.webhook_url
        payload = self._build_payload(title, content, level)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("钉钉通知发送成功")
                return True
            logger.error(f"钉钉通知发送失败: {result.get('errmsg')}")
            return False
        except httpx.TimeoutException as e:
            logger.error(f"钉钉通知发送超时: {e}")
            return False
        except httpx.ConnectError as e:
            logger.error(f"钉钉通知发送连接失败: {e}")
            return False
        except Exception as e:
            logger.error(f"钉钉通知发送异常: {e}")
            return False

    async def receive_message(self) -> Optional[dict]:
        """钉钉 Webhook 为单向通道，不支持接收消息"""
        return None

    def is_bidirectional(self) -> bool:
        """钉钉 Webhook 为单向通道"""
        return False
