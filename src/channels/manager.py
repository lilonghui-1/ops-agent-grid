"""通道管理器 - 统一加载、调度和路由所有 IM 通道"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .base import BaseChannel
from .dingtalk_channel import DingTalkChannel
from .lark_channel import LarkChannel
from .slack_channel import SlackChannel
from .telegram_channel import TelegramChannel
from .wecom_channel import WeComChannel

logger = logging.getLogger(__name__)

# 通道名称 -> 通道实现类的映射表
CHANNEL_REGISTRY: Dict[str, type] = {
    "slack": SlackChannel,
    "dingtalk": DingTalkChannel,
    "wecom": WeComChannel,
    "telegram": TelegramChannel,
    "lark": LarkChannel,
}

# 消息处理回调类型：接收一个消息字典，返回可等待对象
MessageHandler = Callable[[dict], Awaitable[Any]]


class ChannelManager:
    """通道管理器 - 负责通道的加载、广播、定向发送与消息路由

    职责：
    1. 从配置（config.yaml 的 channels 段）加载所有已配置通道
    2. 向所有通道广播消息（send_to_all）
    3. 向指定通道发送消息（send_to_channel）
    4. 轮询所有双向通道接收消息（receive_from_all）
    5. 将接收到的消息路由给 Coordinator Agent（MasterAgent）
    """

    def __init__(self, config: Any = None):
        """
        Args:
            config: 应用配置对象（AppConfig）或配置字典。
                    优先读取 channels 段；若未配置则兼容读取 notify 段。
        """
        self._channels: Dict[str, BaseChannel] = {}
        # 由 Coordinator Agent 注册的消息处理回调
        self._message_handler: Optional[MessageHandler] = None
        self._load_channels(config)

    # ------------------------------------------------------------------
    # 通道加载
    # ------------------------------------------------------------------
    def _load_channels(self, config: Any) -> None:
        """根据配置加载所有通道实例"""
        if config is None:
            logger.warning("未提供配置，通道管理器未加载任何通道")
            return

        # 1. 优先读取 channels 段（标准配置方式）
        channels_config = self._extract_channels_config(config)

        # 2. 兼容旧版 notify 段，补充 wecom / dingtalk 配置
        self._merge_legacy_notify(config, channels_config)

        if not channels_config:
            logger.warning("未发现任何通道配置，通道管理器未加载通道")
            return

        for name, channel_conf in channels_config.items():
            channel_cls = CHANNEL_REGISTRY.get(name)
            if channel_cls is None:
                logger.warning(f"未知的通道类型: {name}，已跳过")
                continue
            if not isinstance(channel_conf, dict):
                logger.warning(f"通道 {name} 配置格式不正确，已跳过")
                continue
            try:
                channel = channel_cls(channel_conf)
                self._channels[channel.name] = channel
                logger.info(f"通道已加载: {channel.name}")
            except Exception as e:
                logger.error(f"加载通道 {name} 失败: {e}")

    def _extract_channels_config(self, config: Any) -> Dict[str, dict]:
        """从配置对象中提取 channels 段配置字典"""
        # 优先从 AppConfig.channels 属性读取
        channels = getattr(config, "channels", None)
        if isinstance(channels, dict):
            return dict(channels)
        # 兼容直接传入字典的情况
        if isinstance(config, dict):
            channels = config.get("channels")
            if isinstance(channels, dict):
                return dict(channels)
        return {}

    def _merge_legacy_notify(
        self, config: Any, channels_config: Dict[str, dict]
    ) -> None:
        """兼容旧版 notify 段，将 wecom / dingtalk 的 Webhook 补充到通道配置"""
        notify = getattr(config, "notify", None)
        if notify is None and isinstance(config, dict):
            notify = config.get("notify")
        if notify is None:
            return

        # 企业微信：旧版 wecom_webhook
        wecom_webhook = getattr(notify, "wecom_webhook", None)
        if not wecom_webhook and isinstance(notify, dict):
            wecom_webhook = notify.get("wecom_webhook")
        if wecom_webhook and "wecom" not in channels_config:
            channels_config["wecom"] = {"webhook_url": wecom_webhook}

        # 钉钉：旧版 dingtalk_webhook
        dingtalk_webhook = getattr(notify, "dingtalk_webhook", None)
        if not dingtalk_webhook and isinstance(notify, dict):
            dingtalk_webhook = notify.get("dingtalk_webhook")
        if dingtalk_webhook and "dingtalk" not in channels_config:
            channels_config["dingtalk"] = {"webhook_url": dingtalk_webhook}

    # ------------------------------------------------------------------
    # 消息处理回调（由 Coordinator Agent 注册）
    # ------------------------------------------------------------------
    def set_message_handler(self, handler: MessageHandler) -> None:
        """设置接收消息的处理回调，由 Coordinator Agent（MasterAgent）注册"""
        self._message_handler = handler
        logger.info("接收消息处理回调已注册")

    # ------------------------------------------------------------------
    # 通道访问
    # ------------------------------------------------------------------
    def get_channel(self, channel_name: str) -> Optional[BaseChannel]:
        """按名称获取通道实例"""
        return self._channels.get(channel_name)

    def get_channel_names(self) -> List[str]:
        """获取所有已加载通道名称"""
        return list(self._channels.keys())

    # ------------------------------------------------------------------
    # 发送消息
    # ------------------------------------------------------------------
    async def send_to_all(
        self, title: str, content: str, level: str = "info"
    ) -> Dict[str, bool]:
        """向所有已加载通道广播消息

        Args:
            title: 消息标题
            content: 消息内容（支持 Markdown）
            level: 告警级别 info / warning / error / critical

        Returns:
            各通道发送结果字典 {通道名: 是否成功}
        """
        if not self._channels:
            logger.warning("无可用的发送通道，跳过广播")
            return {}

        results: Dict[str, bool] = {}
        for name, channel in self._channels.items():
            results[name] = await self._safe_send(channel, title, content, level)

        success_count = sum(1 for v in results.values() if v)
        logger.info(
            f"消息广播完成: 成功 {success_count}/{len(results)} 个通道"
        )
        return results

    async def send_to_channel(
        self,
        channel_name: str,
        title: str,
        content: str,
        level: str = "info",
    ) -> bool:
        """向指定通道发送消息

        Args:
            channel_name: 通道名称
            title: 消息标题
            content: 消息内容（支持 Markdown）
            level: 告警级别

        Returns:
            是否发送成功
        """
        channel = self._channels.get(channel_name)
        if channel is None:
            logger.error(f"通道不存在: {channel_name}")
            return False
        return await self._safe_send(channel, title, content, level)

    async def _safe_send(
        self, channel: BaseChannel, title: str, content: str, level: str
    ) -> bool:
        """安全地调用通道发送，捕获所有异常避免影响其他通道"""
        try:
            return await channel.send_message(title, content, level)
        except Exception as e:
            logger.error(f"通道 {channel.name} 发送失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 接收消息
    # ------------------------------------------------------------------
    async def receive_from_all(self) -> List[dict]:
        """轮询所有支持双向通信的通道，收集接收到的消息

        Returns:
            所有收到的消息列表
        """
        messages: List[dict] = []
        for name, channel in self._channels.items():
            if not channel.is_bidirectional():
                continue
            try:
                message = await channel.receive_message()
                if message is not None:
                    # 补充来源通道信息
                    message.setdefault("channel", name)
                    messages.append(message)
                    logger.info(f"从通道 {name} 收到消息")
                    # 路由到 Coordinator Agent
                    await self._route_message(message)
            except Exception as e:
                logger.error(f"通道 {name} 接收消息失败: {e}")
        return messages

    async def _route_message(self, message: dict) -> None:
        """将接收到的消息路由给 Coordinator Agent（MasterAgent）

        通过消息处理回调实现解耦，避免循环依赖。
        若未注册回调则仅记录日志。
        """
        if self._message_handler is None:
            logger.debug("未注册消息处理回调，消息未路由")
            return
        try:
            await self._message_handler(message)
            logger.info("消息已路由至 Coordinator Agent")
        except Exception as e:
            logger.error(f"路由消息至 Coordinator Agent 失败: {e}")
