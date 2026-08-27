"""IM 通道基类 - 定义统一的通道抽象接口"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseChannel(ABC):
    """IM 通道基类

    所有即时通讯通道（Slack / 钉钉 / 企业微信 / Telegram / 飞书等）
    均需继承本类，并实现 send_message 与 receive_message 方法。
    """

    name: str = ""

    @abstractmethod
    async def send_message(self, title: str, content: str, level: str = "info") -> bool:
        """发送消息到通道

        Args:
            title: 消息标题
            content: 消息内容（通常支持 Markdown）
            level: 告警级别: info / warning / error / critical

        Returns:
            是否发送成功
        """
        pass

    @abstractmethod
    async def receive_message(self) -> Optional[dict]:
        """接收来自通道的消息（如果支持双向通信）

        Returns:
            接收到的消息字典，无消息或不支持时返回 None
        """
        pass

    def is_bidirectional(self) -> bool:
        """是否支持双向通信"""
        return False
