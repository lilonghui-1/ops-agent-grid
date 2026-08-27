"""工具注册入口 - 初始化并注册所有运维工具"""

import logging

from .base import ToolRegistry
from .ssh_tools import SSHExecuteTool
from .db_tools import DBQueryTool, DBStatusTool, RedisInfoTool
from .log_tools import LogFetchTool, LogAnalyzeTool, LogPlatformQueryTool
from .system_tools import SystemMetricsTool, ServiceControlTool
from .notify_tools import NotifyTool

# Kubernetes 工具（可选依赖：kubernetes 包，未安装时跳过）
try:
    from .k8s_tools import (
        K8sPodStatusTool, K8sEventTool, K8sLogsTool, K8sDescribeTool,
    )
    HAS_K8S_TOOLS = True
except ImportError:
    HAS_K8S_TOOLS = False

# 网络工具（SNMPWalkTool 依赖 pysnmp，其余使用标准库；模块内部已优雅降级）
try:
    from .network_tools import SNMPWalkTool, NetworkPingTool, PortScanTool
    HAS_NETWORK_TOOLS = True
except ImportError:
    HAS_NETWORK_TOOLS = False

# 拓扑工具（依赖 SSH 工具）
from .topology_tool import TopologyBuildTool, BlastRadiusTool

# Web 终端工具（依赖 paramiko，与 SSH 工具同源）
from .web_terminal_tool import WebTerminalTool

logger = logging.getLogger(__name__)


def register_all_tools(config):
    """初始化并注册所有工具到 ToolRegistry

    Args:
        config: AppConfig 配置实例
    """
    ToolRegistry.clear()

    # 1. SSH 工具（其他工具依赖它）
    ssh_tool = SSHExecuteTool(config)
    ToolRegistry.register(ssh_tool)

    # 2. 数据库工具（凭证从配置内部读取，不经过 LLM）
    # 支持: mysql, postgresql, oracle, dm(达梦), kingbase(人大金仓), redis
    ToolRegistry.register(DBQueryTool(config))
    ToolRegistry.register(DBStatusTool(config))
    ToolRegistry.register(RedisInfoTool(config))

    # 3. 日志工具
    # SSH 文件读取
    log_fetch_tool = LogFetchTool(ssh_tool, config)
    ToolRegistry.register(log_fetch_tool)
    # 日志平台 API 查询（ELK/Loki）
    ToolRegistry.register(LogPlatformQueryTool(config))
    # 日志分析
    ToolRegistry.register(LogAnalyzeTool())

    # 4. 系统工具（依赖 SSH 工具和配置）
    system_metrics_tool = SystemMetricsTool(ssh_tool, config)
    ToolRegistry.register(system_metrics_tool)
    service_control_tool = ServiceControlTool(ssh_tool, config)
    ToolRegistry.register(service_control_tool)

    # 5. 通知工具
    ToolRegistry.register(NotifyTool(config))

    # 6. Kubernetes 工具（可选，需安装 kubernetes 包）
    if HAS_K8S_TOOLS:
        try:
            ToolRegistry.register(K8sPodStatusTool(config))
            ToolRegistry.register(K8sEventTool(config))
            ToolRegistry.register(K8sLogsTool(config))
            ToolRegistry.register(K8sDescribeTool(config))
        except Exception as e:
            logger.warning(f"Kubernetes 工具注册失败，已跳过: {e}")
    else:
        logger.info("kubernetes 包未安装，跳过 K8s 工具注册")

    # 7. 网络设备工具（NetworkPingTool/PortScanTool 无额外依赖，
    #    SNMPWalkTool 依赖 pysnmp，未安装时工具内部返回错误）
    if HAS_NETWORK_TOOLS:
        # Ping 和端口扫描无外部依赖，正常注册
        ToolRegistry.register(NetworkPingTool(config))
        ToolRegistry.register(PortScanTool(config))
        # SNMP 工具依赖 pysnmp，单独 try/except 保护
        try:
            ToolRegistry.register(SNMPWalkTool(config))
        except Exception as e:
            logger.warning(f"SNMP 工具注册失败，已跳过: {e}")
    else:
        logger.info("网络工具模块加载失败，跳过网络工具注册")

    # 8. 拓扑工具（依赖 SSH 工具构建依赖图）
    topology_build_tool = TopologyBuildTool(ssh_tool, config)
    ToolRegistry.register(topology_build_tool)
    ToolRegistry.register(BlastRadiusTool(config))

    # 9. Web 终端工具（交互式 SSH 会话桥接）
    ToolRegistry.register(WebTerminalTool(config))

    logger.info(f"所有工具注册完成，共 {len(ToolRegistry.get_all())} 个工具: {ToolRegistry.get_names()}")
