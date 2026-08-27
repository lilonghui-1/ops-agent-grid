"""飞书 / Lark 通道 - 通过 Webhook URL 发送文本和交互卡片消息"""

import logging
from typing import Optional

import httpx

from .base import BaseChannel

logger = logging.getLogger(__name__)


class LarkChannel(BaseChannel):
    """飞书 / Lark 通道 - 支持文本（text）和交互卡片（interactive）消息

    通过自定义机器人 Webhook URL 发送，符合飞书自定义机器人消息格式。
    """

    name = "lark"

    # 告警级别对应的颜色与标记
    LEVEL_STYLE = {
        "info": {"color": "blue", "tag": "Info"},
        "warning": {"color": "yellow", "tag": "Warning"},
        "error": {"color": "red", "tag": "Error"},
        "critical": {"color": "red", "tag": "Critical"},
    }

    def __init__(self, config: dict):
        """初始化飞书通道

        Args:
            config: 通道配置字典，需包含:
                - webhook_url: 飞书自定义机器人 Webhook URL
                - msg_type: 消息类型 text / interactive，默认 interactive
                - secret: 加签密钥（可选，启用签名校验时填写）
                - timeout: 请求超时时间（秒），默认 10
        """
        self.webhook_url = config.get("webhook_url", "")
        self.msg_type = config.get("msg_type", "interactive")  # text / interactive
        self.secret = config.get("secret", "")
        self.timeout = config.get("timeout", 10)

    async def send_message(self, title: str, content: str, level: str = "info") -> bool:
        """发送消息到飞书"""
        if not self.webhook_url:
            logger.warning("飞书 Webhook URL 未配置，跳过发送")
            return False

        if self.msg_type == "text":
            payload = self._build_text_payload(title, content, level)
        else:
            payload = self._build_card_payload(title, content, level)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.webhook_url, json=payload)
            result = resp.json()
            # 飞书 Webhook 成功时返回 code 为 0 或无 code 字段且 statusCode 为 0
            code = result.get("code", result.get("StatusCode", -1))
            if code == 0:
                logger.info("飞书通知发送成功")
                return True
            logger.error(f"飞书通知发送失败: {result.get('msg', result)}")
            return False
        except httpx.TimeoutException as e:
            logger.error(f"飞书通知发送超时: {e}")
            return False
        except httpx.ConnectError as e:
            logger.error(f"飞书通知发送连接失败: {e}")
            return False
        except Exception as e:
            logger.error(f"飞书通知发送异常: {e}")
            return False

    def _build_text_payload(self, title: str, content: str, level: str) -> dict:
        """构建文本消息体"""
        style = self.LEVEL_STYLE.get(level, self.LEVEL_STYLE["info"])
        text = f"[{style['tag']}] {title}\n\n{content}"
        return {
            "msg_type": "text",
            "content": {"text": text},
        }

    def _build_card_payload(self, title: str, content: str, level: str) -> dict:
        """构建交互卡片消息体"""
        style = self.LEVEL_STYLE.get(level, self.LEVEL_STYLE["info"])
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": style["color"],
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": content,
                        },
                    },
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": f"级别: {style['tag']}",
                            }
                        ],
                    },
                ],
            },
        }

    async def receive_message(self) -> Optional[dict]:
        """飞书 Webhook 为单向通道，不支持接收消息

        双向通信需通过飞书事件订阅实现，此处不展开。
        """
        return None

    def is_bidirectional(self) -> bool:
        """飞书 Webhook 为单向通道"""
        return False
