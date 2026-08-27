"""服务拓扑工具 - 构建服务依赖关系图、计算爆炸半径

通过 SSH 查询各主机的监听端口和已建立连接，自动推断服务间的依赖关系，
构建有向依赖图（A -> B 表示 A 依赖 B）。支持基于 BFS 计算故障爆炸半径。
"""

import re
import logging
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .base import BaseTool, ToolRegistry, ToolResult, ToolParameter

logger = logging.getLogger(__name__)

# 匹配地址:端口 格式（如 0.0.0.0:3306、[::]:8080、192.168.1.10:5432）
_ADDR_PORT_RE = re.compile(r'(?<![\w\[\]:])(\[?[\dA-Fa-f:.]+\]?):(\d+)(?![\d])')
# 匹配 ss 输出中的进程名：users:(("nginx",pid=1234,fd=6))
_PROC_RE = re.compile(r'users:\(\("([^"]+)"')


def _parse_ss_listening(output: str) -> List[dict]:
    """解析 `ss -tlnp` 输出，提取监听端口与进程名

    返回: [{"port": 3306, "process": "mysqld", "address": "0.0.0.0"}, ...]
    """
    results = []
    for line in output.splitlines():
        if 'LISTEN' not in line:
            continue
        # 提取地址:端口
        addr_match = _ADDR_PORT_RE.search(line)
        if not addr_match:
            continue
        address = addr_match.group(1).strip('[]')
        port = int(addr_match.group(2))
        # 提取进程名
        proc_match = _PROC_RE.search(line)
        process = proc_match.group(1) if proc_match else "unknown"
        results.append({
            "port": port,
            "process": process,
            "address": address,
        })
    return results


def _parse_ss_established(output: str) -> List[dict]:
    """解析 `ss -tnp` 输出，提取已建立连接的本地进程与对端地址

    返回: [{"local_process": "app", "peer_address": "192.168.1.20",
            "peer_port": 3306}, ...]
    """
    results = []
    for line in output.splitlines():
        if 'ESTAB' not in line:
            continue
        # 提取所有地址:端口匹配（第一个是本地，第二个是对端）
        matches = _ADDR_PORT_RE.findall(line)
        if len(matches) < 2:
            continue
        peer_address = matches[1][0].strip('[]')
        peer_port = int(matches[1][1])
        # 提取本地进程名
        proc_match = _PROC_RE.search(line)
        local_process = proc_match.group(1) if proc_match else "unknown"
        results.append({
            "local_process": local_process,
            "peer_address": peer_address,
            "peer_port": peer_port,
        })
    return results


class TopologyBuildTool(BaseTool):
    """服务拓扑构建工具 - 通过 SSH 采集监听端口和连接信息，构建依赖图

    工作流程：
    1. 对每台主机执行 `ss -tlnp` 获取监听端口和绑定进程
    2. 对每台主机执行 `ss -tnp` 获取已建立的对外连接
    3. 根据监听映射将对端 (host, port) 解析为目标服务
    4. 构建有向边：源服务 -> 目标服务（表示源依赖目标）
    """

    name = "topology_build"
    description = ("构建服务依赖关系拓扑图。通过 SSH 查询各主机的监听端口和"
                   "已建立连接，自动推断服务间依赖关系，返回节点和边。")
    parameters = [
        ToolParameter(
            name="hosts",
            type="array",
            description="目标主机列表（IP 或主机名）",
        ),
        ToolParameter(
            name="ssh_timeout",
            type="integer",
            description="SSH 命令执行超时时间(秒)",
            required=False,
            default=15,
        ),
    ]

    def __init__(self, ssh_tool=None, config=None):
        self._ssh_tool = ssh_tool
        self._config = config
        self._server_map = {}
        if config and hasattr(config, 'servers'):
            for s in config.servers:
                self._server_map[s.host] = s

    def _set_ssh_tool(self, ssh_tool):
        """延迟设置 SSH 工具（避免循环依赖）"""
        self._ssh_tool = ssh_tool

    def _get_ssh_tool(self):
        """获取 SSH 工具，优先使用构造时传入的，否则从注册中心获取"""
        if self._ssh_tool:
            return self._ssh_tool
        return ToolRegistry.get("ssh_execute")

    def _query_host_topology(self, host: str, timeout: int) -> Tuple[List[dict], List[dict]]:
        """查询单台主机的监听端口和已建立连接"""
        ssh_tool = self._get_ssh_tool()
        if not ssh_tool:
            return [], []

        # 查询监听端口
        listen_cmd = "ss -tlnp 2>/dev/null"
        listen_result = ssh_tool.execute(host=host, command=listen_cmd, timeout=timeout)
        listening = []
        if listen_result.success and listen_result.data:
            listening = _parse_ss_listening(
                listen_result.data.get('stdout', '')
            )

        # 查询已建立连接
        estab_cmd = "ss -tnp state established 2>/dev/null"
        estab_result = ssh_tool.execute(host=host, command=estab_cmd, timeout=timeout)
        established = []
        if estab_result.success and estab_result.data:
            established = _parse_ss_established(
                estab_result.data.get('stdout', '')
            )

        return listening, established

    def execute(self, **kwargs) -> ToolResult:
        hosts: List[str] = kwargs.get('hosts', [])
        if isinstance(hosts, str):
            hosts = [h.strip() for h in hosts.split(',') if h.strip()]
        ssh_timeout = kwargs.get('ssh_timeout', 15)

        if not hosts:
            return ToolResult(success=False, error="未提供目标主机列表")

        ssh_tool = self._get_ssh_tool()
        if not ssh_tool:
            return ToolResult(success=False, error="SSH 工具未初始化，无法采集拓扑")

        # 第一阶段：采集所有主机的监听端口，建立 (host, port) -> 节点 的映射
        # host_aliases 用于将对端 IP 匹配回主机名
        nodes = []
        node_index: Dict[str, int] = {}  # (host, process, port) -> 节点索引
        host_listen_map: Dict[str, Dict[int, str]] = {}  # host -> {port: process}
        host_aliases: Dict[str, str] = {}  # 别名/IP -> 标准主机名

        host_topologies = {}
        for host in hosts:
            listening, established = self._query_host_topology(host, ssh_timeout)
            host_topologies[host] = (listening, established)

            # 注册主机别名（主机名自身即作为别名）
            host_aliases[host] = host

            # 构建监听映射
            port_to_proc = {}
            for item in listening:
                port = item['port']
                process = item['process']
                port_to_proc[port] = process
                # 创建节点（以 host:process:port 去重）
                node_key = f"{host}:{process}:{port}"
                if node_key not in node_index:
                    node_index[node_key] = len(nodes)
                    nodes.append({
                        "id": node_key,
                        "host": host,
                        "service": process,
                        "port": port,
                        "type": "service",
                    })
            host_listen_map[host] = port_to_proc

        # 第二阶段：根据已建立连接构建依赖边
        edges = []
        edge_set: Set[Tuple[str, str]] = set()  # 去重

        for host, (listening, established) in host_topologies.items():
            for conn in established:
                local_process = conn['local_process']
                peer_address = conn['peer_address']
                peer_port = conn['peer_port']

                # 将对端地址匹配到已知主机
                target_host = host_aliases.get(peer_address)
                if not target_host:
                    # 尝试在已知主机列表中查找匹配
                    for known_host in hosts:
                        if (peer_address == known_host
                                or known_host in peer_address
                                or peer_address in known_host):
                            target_host = known_host
                            break

                if not target_host:
                    # 对端不在采集范围内，创建外部节点
                    ext_node_key = f"{peer_address}:external:{peer_port}"
                    if ext_node_key not in node_index:
                        node_index[ext_node_key] = len(nodes)
                        nodes.append({
                            "id": ext_node_key,
                            "host": peer_address,
                            "service": "external",
                            "port": peer_port,
                            "type": "external",
                        })

                # 源节点：当前主机的本地进程
                # 查找源节点 - 用 host:process:0 表示发起连接的进程
                source_key = f"{host}:{local_process}:0"
                if source_key not in node_index:
                    node_index[source_key] = len(nodes)
                    nodes.append({
                        "id": source_key,
                        "host": host,
                        "service": local_process,
                        "port": 0,
                        "type": "service",
                    })

                # 目标节点
                target_service = "external"
                if target_host:
                    target_service = host_listen_map.get(
                        target_host, {}
                    ).get(peer_port, "unknown")
                    target_key = f"{target_host}:{target_service}:{peer_port}"
                else:
                    target_key = f"{peer_address}:external:{peer_port}"

                # 确保目标节点存在
                if target_key not in node_index:
                    node_index[target_key] = len(nodes)
                    nodes.append({
                        "id": target_key,
                        "host": target_host or peer_address,
                        "service": target_service,
                        "port": peer_port,
                        "type": "external" if target_service == "external" else "service",
                    })

                # 添加边（A 依赖 B）
                edge_tuple = (source_key, target_key)
                if edge_tuple not in edge_set:
                    edge_set.add(edge_tuple)
                    edges.append({
                        "source": source_key,
                        "target": target_key,
                        "type": "depends_on",
                        "detail": (
                            f"{local_process}@{host} -> "
                            f"{target_service}@{target_host or peer_address}:{peer_port}"
                        ),
                    })

        return ToolResult(
            success=True,
            data={
                "nodes": nodes,
                "edges": edges,
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
            metadata={
                "hosts": hosts,
                "ssh_timeout": ssh_timeout,
            },
        )


class BlastRadiusTool(BaseTool):
    """爆炸半径计算工具 - 基于拓扑图 BFS 计算故障影响范围

    给定一个节点，计算其故障后受影响的所有下游服务（谁依赖了它），
    或其上游依赖链（它依赖了谁）。
    """

    name = "blast_radius"
    description = ("基于服务拓扑图计算故障爆炸半径。给定一个节点，使用 BFS "
                   "遍历计算所有受影响的服务。direction=downstream 表示计算"
                   "故障影响范围（谁依赖此节点），direction=upstream 表示计算"
                   "依赖链路（此节点依赖谁）。")
    parameters = [
        ToolParameter(
            name="node",
            type="string",
            description="目标节点 ID（与拓扑图中的节点 id 一致）",
        ),
        ToolParameter(
            name="topology",
            type="object",
            description="拓扑图，包含 nodes 和 edges 两个列表（由 topology_build 生成）",
        ),
        ToolParameter(
            name="direction",
            type="string",
            description="遍历方向: downstream(故障影响，默认), upstream(依赖链路)",
            required=False,
            default="downstream",
        ),
        ToolParameter(
            name="max_depth",
            type="integer",
            description="最大遍历深度（0 表示不限制）",
            required=False,
            default=0,
        ),
    ]

    def __init__(self, config=None):
        self._config = config

    @staticmethod
    def _build_adjacency(
        edges: List[dict], direction: str
    ) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
        """根据方向构建邻接表

        边 A -> B 表示 A 依赖 B：
        - downstream（故障影响）：从目标节点沿反向边遍历，找出所有依赖它的节点
        - upstream（依赖链路）：从目标节点沿正向边遍历，找出它依赖的所有节点
        """
        adjacency: Dict[str, List[str]] = {}
        edge_detail: Dict[str, str] = {}

        for edge in edges:
            source = edge.get('source', '')
            target = edge.get('target', '')
            if not source or not target:
                continue

            if direction == 'downstream':
                # 反向：target -> source（谁依赖了 target）
                adjacency.setdefault(target, []).append(source)
                edge_detail[f"{target}->{source}"] = edge.get('detail', '')
            else:
                # 正向：source -> target（source 依赖了谁）
                adjacency.setdefault(source, []).append(target)
                edge_detail[f"{source}->{target}"] = edge.get('detail', '')

        return adjacency, edge_detail

    @staticmethod
    def _bfs(
        start: str,
        adjacency: Dict[str, List[str]],
        max_depth: int,
    ) -> Tuple[List[str], List[dict]]:
        """从起始节点执行 BFS，返回受影响节点列表和遍历路径"""
        if not start:
            return [], []

        visited: Set[str] = set()
        visited.add(start)
        queue = deque()
        queue.append((start, 0))
        affected_nodes: List[str] = [start]
        path_edges: List[dict] = []

        while queue:
            current, depth = queue.popleft()

            # 深度限制检查
            if max_depth > 0 and depth >= max_depth:
                continue

            neighbors = adjacency.get(current, [])
            for neighbor in neighbors:
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                affected_nodes.append(neighbor)
                path_edges.append({
                    "from": current,
                    "to": neighbor,
                    "depth": depth + 1,
                })
                queue.append((neighbor, depth + 1))

        return affected_nodes, path_edges

    def execute(self, **kwargs) -> ToolResult:
        node = kwargs['node']
        topology = kwargs.get('topology', {})
        direction = kwargs.get('direction', 'downstream')
        max_depth = kwargs.get('max_depth', 0)

        if not isinstance(topology, dict):
            return ToolResult(success=False, error="topology 参数必须为对象类型")

        nodes = topology.get('nodes', [])
        edges = topology.get('edges', [])

        if not nodes:
            return ToolResult(
                success=False,
                error="拓扑图中没有节点，请先使用 topology_build 构建拓扑"
            )

        # 验证目标节点存在
        node_ids = {n.get('id') for n in nodes}
        if node not in node_ids:
            return ToolResult(
                success=False,
                error=f"目标节点 '{node}' 不在拓扑图中。可用节点: {list(node_ids)[:10]}",
            )

        # 校验方向参数
        if direction not in ('downstream', 'upstream'):
            return ToolResult(
                success=False,
                error=f"不支持的遍历方向: {direction}，支持: downstream, upstream",
            )

        # 构建邻接表并执行 BFS
        adjacency, edge_detail = self._build_adjacency(edges, direction)
        affected_nodes, path_edges = self._bfs(node, adjacency, max_depth)

        # 收集受影响节点的详细信息
        node_info_map = {n.get('id'): n for n in nodes}
        affected_details = []
        for aid in affected_nodes:
            info = node_info_map.get(aid, {})
            affected_details.append({
                "id": aid,
                "host": info.get('host', ''),
                "service": info.get('service', ''),
                "port": info.get('port'),
                "type": info.get('type', ''),
            })

        # 排除起始节点本身（爆炸半径 = 除自身外受影响的服务）
        impacted = [d for d in affected_details if d['id'] != node]

        # 按深度分组统计
        depth_groups: Dict[int, List[str]] = {}
        for pe in path_edges:
            depth_groups.setdefault(pe['depth'], []).append(pe['to'])

        return ToolResult(
            success=True,
            data={
                "source_node": node,
                "direction": direction,
                "affected_nodes": affected_details,
                "impacted_services": impacted,
                "impacted_count": len(impacted),
                "traversal_path": path_edges,
                "depth_distribution": {
                    str(k): v for k, v in sorted(depth_groups.items())
                },
                "max_depth_reached": (
                    max(depth_groups.keys()) if depth_groups else 0
                ),
            },
            metadata={
                "node": node,
                "direction": direction,
                "max_depth": max_depth,
                "total_affected": len(affected_details),
            },
        )
