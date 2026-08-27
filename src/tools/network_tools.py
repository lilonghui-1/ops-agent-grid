"""网络设备运维工具 - SNMP Walk、批量 Ping、端口扫描

依赖说明：
- SNMPWalkTool 依赖 pysnmp（可选，未安装时优雅降级）
- NetworkPingTool 依赖系统 ping 命令（通过 subprocess 调用）
- PortScanTool 使用 Python 标准库 socket，无额外依赖
"""

import logging
import re
import socket
import subprocess
from typing import List, Optional

from .base import BaseTool, ToolResult, ToolParameter

# 尝试导入 pysnmp，未安装时优雅降级
try:
    from pysnmp.hlapi import (
        SnmpEngine,
        CommunityData,
        UdpTransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        nextCmd,
    )
    HAS_PYSNMP = True
except ImportError:
    HAS_PYSNMP = False

logger = logging.getLogger(__name__)

# 常见端口扫描列表（服务名: 端口）
COMMON_PORTS = {
    22: "SSH",
    21: "FTP",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    80: "HTTP",
    110: "POP3",
    143: "IMAP",
    443: "HTTPS",
    445: "SMB",
    993: "IMAPS",
    995: "POP3S",
    1433: "SQL Server",
    1521: "Oracle",
    3306: "MySQL",
    3389: "RDP",
    5432: "PostgreSQL",
    5984: "CouchDB",
    6379: "Redis",
    6443: "Kubernetes API",
    8080: "HTTP Alt",
    8443: "HTTPS Alt",
    9200: "Elasticsearch",
    9300: "ES Transport",
    11211: "Memcached",
    27017: "MongoDB",
    9092: "Kafka",
    15672: "RabbitMQ",
}


class SNMPWalkTool(BaseTool):
    """SNMP Walk 工具 - 使用 pysnmp 遍历网络设备 MIB 树"""

    name = "snmp_walk"
    description = ("对网络设备执行 SNMP Walk，遍历指定 OID 子树下的所有节点。"
                   "用于获取交换机、路由器等网络设备的接口状态、流量、端口等信息。"
                   "需要安装 pysnmp（pip install pysnmp）。")
    parameters = [
        ToolParameter(name="host", type="string", description="目标设备 IP 地址"),
        ToolParameter(
            name="community",
            type="string",
            description="SNMP community 字符串",
            required=False,
            default="public",
        ),
        ToolParameter(
            name="oid",
            type="string",
            description="起始 OID（默认从 .1.3.6.1 开始遍历整个 MIB 树）",
            required=False,
            default="1.3.6.1",
        ),
        ToolParameter(
            name="port",
            type="integer",
            description="SNMP UDP 端口",
            required=False,
            default=161,
        ),
        ToolParameter(
            name="timeout",
            type="integer",
            description="超时时间(秒)",
            required=False,
            default=10,
        ),
        ToolParameter(
            name="max_repetitions",
            type="integer",
            description="每次请求获取的最大变量数",
            required=False,
            default=25,
        ),
    ]

    def __init__(self, config=None):
        self._config = config

    def execute(self, **kwargs) -> ToolResult:
        if not HAS_PYSNMP:
            return ToolResult(
                success=False,
                error="pysnmp 未安装，请执行: pip install pysnmp",
            )

        host = kwargs['host']
        community = kwargs.get('community', 'public')
        oid = kwargs.get('oid', '1.3.6.1')
        port = kwargs.get('port', 161)
        timeout = kwargs.get('timeout', 10)
        max_repetitions = kwargs.get('max_repetitions', 25)

        try:
            # 构建 SNMP Walk 迭代器
            iterator = nextCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),  # mpModel=1 表示 SNMPv2c
                UdpTransportTarget(
                    (host, port),
                    timeout=timeout,
                    retries=1,
                ),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
                lexicographicMode=False,  # 关闭词典模式以遍历子树
                maxRows=max_repetitions,
            )

            results = []
            error_count = 0

            for (error_indication, error_status, error_index,
                 var_binds) in iterator:
                if error_indication:
                    # 遍历过程中的错误指示（可能是到达子树末尾）
                    logger.debug(f"SNMP 遍历结束指示: {error_indication}")
                    break
                elif error_status:
                    error_count += 1
                    logger.warning(
                        f"SNMP 错误: {error_status.prettyPrint()} "
                        f"at {error_index}"
                    )
                    if error_count > 5:
                        # 错误过多则终止
                        break
                    continue
                else:
                    for var_bind in var_binds:
                        results.append({
                            "oid": str(var_bind[0]),
                            "value": str(var_bind[1]),
                        })

            return ToolResult(
                success=True,
                data={
                    "results": results,
                    "total": len(results),
                    "error_count": error_count,
                },
                metadata={
                    "host": host,
                    "community": community,
                    "oid": oid,
                    "port": port,
                },
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"SNMP Walk 失败: {type(e).__name__}: {e}",
                metadata={"host": host, "oid": oid},
            )


class NetworkPingTool(BaseTool):
    """批量 Ping 工具 - 并行 Ping 多台主机，解析丢包率和延迟"""

    name = "network_ping"
    description = ("批量 Ping 多台主机，返回每台主机的丢包率、平均延迟和连通状态。"
                   "用于快速检查网络连通性和延迟情况。")
    parameters = [
        ToolParameter(
            name="hosts",
            type="array",
            description="目标主机 IP/域名列表",
        ),
        ToolParameter(
            name="count",
            type="integer",
            description="每个主机发送的 Ping 包数量",
            required=False,
            default=4,
        ),
        ToolParameter(
            name="timeout",
            type="integer",
            description="单个 Ping 命令总超时时间(秒)",
            required=False,
            default=5,
        ),
    ]

    def __init__(self, config=None):
        self._config = config

    @staticmethod
    def _ping_host(host: str, count: int, timeout: int) -> dict:
        """对单个主机执行 Ping 并解析结果"""
        # 构造 ping 命令参数
        # Linux: -c count -W timeout_ms（每次包等待秒数）
        # 使用 -W 设置每包超时（秒），整体超时由 subprocess 控制
        cmd = ["ping", "-c", str(count), "-W", str(timeout), host]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout * count + 10,
            )
            output = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            return {
                "host": host,
                "reachable": False,
                "packet_loss": 100.0,
                "min_rtt": None,
                "avg_rtt": None,
                "max_rtt": None,
                "error": "Ping 命令超时",
            }
        except FileNotFoundError:
            return {
                "host": host,
                "reachable": False,
                "packet_loss": 100.0,
                "min_rtt": None,
                "avg_rtt": None,
                "max_rtt": None,
                "error": "系统未安装 ping 命令",
            }

        return NetworkPingTool._parse_ping_output(host, output)

    @staticmethod
    def _parse_ping_output(host: str, output: str) -> dict:
        """解析 ping 命令输出，提取丢包率和延迟"""
        result = {
            "host": host,
            "reachable": False,
            "packet_loss": 100.0,
            "min_rtt": None,
            "avg_rtt": None,
            "max_rtt": None,
            "error": None,
        }

        # 解析丢包率: "4 packets transmitted, 4 received, 0% packet loss"
        loss_match = re.search(
            r'(\d+)\s+packets transmitted.*?(\d+)\s+received'
            r'(?:.*?(\d+(?:\.\d+)?)%\s+packet loss)?',
            output,
        )
        if loss_match:
            transmitted = int(loss_match.group(1))
            received = int(loss_match.group(2))
            if transmitted > 0:
                loss_pct = round((transmitted - received) / transmitted * 100, 2)
            else:
                loss_pct = 100.0
            result["packet_loss"] = loss_pct
            result["reachable"] = received > 0
        else:
            # 尝试匹配中文 ping 输出
            loss_match_cn = re.search(
                r'(\d+)\s+个数据包.*?(\d+)\s+个被接收',
                output,
            )
            if loss_match_cn:
                transmitted = int(loss_match_cn.group(1))
                received = int(loss_match_cn.group(2))
                if transmitted > 0:
                    loss_pct = round(
                        (transmitted - received) / transmitted * 100, 2
                    )
                else:
                    loss_pct = 100.0
                result["packet_loss"] = loss_pct
                result["reachable"] = received > 0

        # 解析延迟: "rtt min/avg/max/mdev = 10.169/10.318/10.498/0.194 ms"
        rtt_match = re.search(
            r'(?:rtt|round-trip)\s*(?:min/avg/max/(?:mdev|stddev)\s*=\s*)?'
            r'([\d.]+)/([\d.]+)/([\d.]+)',
            output,
        )
        if rtt_match:
            result["min_rtt"] = float(rtt_match.group(1))
            result["avg_rtt"] = float(rtt_match.group(2))
            result["max_rtt"] = float(rtt_match.group(3))

        return result

    def execute(self, **kwargs) -> ToolResult:
        hosts: List[str] = kwargs.get('hosts', [])
        if isinstance(hosts, str):
            # 允许传入逗号分隔的字符串
            hosts = [h.strip() for h in hosts.split(',') if h.strip()]
        count = kwargs.get('count', 4)
        timeout = kwargs.get('timeout', 5)

        if not hosts:
            return ToolResult(success=False, error="未提供目标主机列表")

        import threading

        results = [None] * len(hosts)

        def _worker(idx, target_host):
            results[idx] = self._ping_host(target_host, count, timeout)

        threads = []
        for idx, target_host in enumerate(hosts):
            t = threading.Thread(target=_worker, args=(idx, target_host))
            threads.append(t)
            t.start()

        # 等待所有线程完成（最长等待时间为合理上限）
        max_wait = (timeout * count + 10) * 2
        for t in threads:
            t.join(timeout=max_wait)
        # 对未完成的线程标记为不可达
        for idx in range(len(results)):
            if results[idx] is None:
                results[idx] = {
                    "host": hosts[idx],
                    "reachable": False,
                    "packet_loss": 100.0,
                    "min_rtt": None,
                    "avg_rtt": None,
                    "max_rtt": None,
                    "error": "Ping 线程超时未返回",
                }

        reachable_count = sum(1 for r in results if r.get("reachable"))

        return ToolResult(
            success=True,
            data={
                "results": results,
                "total_hosts": len(hosts),
                "reachable": reachable_count,
                "unreachable": len(hosts) - reachable_count,
            },
            metadata={
                "count": count,
                "timeout": timeout,
            },
        )


class PortScanTool(BaseTool):
    """端口扫描工具 - 使用 socket 探测目标主机端口开放状态"""

    name = "port_scan"
    description = ("扫描目标主机的端口开放状态。默认扫描常见服务端口，"
                   "也可指定自定义端口列表。用于排查服务可达性和防火墙配置问题。")
    parameters = [
        ToolParameter(name="host", type="string", description="目标主机 IP/域名"),
        ToolParameter(
            name="ports",
            type="array",
            description="要扫描的端口列表（不指定则扫描常见端口）",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="timeout",
            type="integer",
            description="单端口连接超时(秒)",
            required=False,
            default=2,
        ),
        ToolParameter(
            name="scan_common_only",
            type="boolean",
            description="是否仅扫描常见端口（默认 True）",
            required=False,
            default=True,
        ),
    ]

    def __init__(self, config=None):
        self._config = config

    @staticmethod
    def _scan_port(host: str, port: int, timeout: float) -> dict:
        """扫描单个端口"""
        result = {
            "port": port,
            "service": COMMON_PORTS.get(port, "unknown"),
            "open": False,
            "error": None,
        }
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            # connect_ex 返回 0 表示成功，非 0 表示错误码
            code = sock.connect_ex((host, port))
            if code == 0:
                result["open"] = True
            else:
                result["error"] = f"连接错误码: {code}"
        except socket.gaierror as e:
            result["error"] = f"域名解析失败: {e}"
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        return result

    def execute(self, **kwargs) -> ToolResult:
        host = kwargs['host']
        ports = kwargs.get('ports')
        timeout = kwargs.get('timeout', 2)
        scan_common_only = kwargs.get('scan_common_only', True)

        # 确定扫描端口列表
        if ports:
            port_list = ports
            scan_common_only = False  # 用户指定了端口，扫描全部
        elif scan_common_only:
            port_list = sorted(COMMON_PORTS.keys())
        else:
            port_list = sorted(COMMON_PORTS.keys())

        if not port_list:
            return ToolResult(success=False, error="未提供扫描端口列表")

        import threading

        results = [None] * len(port_list)

        def _worker(idx, port):
            results[idx] = self._scan_port(host, int(port), float(timeout))

        # 使用线程池加速扫描，限制并发数避免资源耗尽
        max_concurrency = 50
        batch_size = max_concurrency

        for start in range(0, len(port_list), batch_size):
            batch = port_list[start:start + batch_size]
            threads = []
            for offset, port in enumerate(batch):
                idx = start + offset
                t = threading.Thread(target=_worker, args=(idx, port))
                threads.append(t)
                t.start()
            for t in threads:
                t.join(timeout=timeout + 5)

        # 处理未完成的扫描
        for idx in range(len(results)):
            if results[idx] is None:
                results[idx] = {
                    "port": port_list[idx],
                    "service": COMMON_PORTS.get(int(port_list[idx]), "unknown"),
                    "open": False,
                    "error": "扫描超时",
                }

        open_ports = [r for r in results if r.get("open")]
        closed_ports = [r for r in results if not r.get("open")]

        return ToolResult(
            success=True,
            data={
                "host": host,
                "results": results,
                "open_ports": open_ports,
                "closed_ports": closed_ports,
                "total_scanned": len(port_list),
                "open_count": len(open_ports),
            },
            metadata={
                "host": host,
                "timeout": timeout,
                "scan_common_only": scan_common_only,
            },
        )
