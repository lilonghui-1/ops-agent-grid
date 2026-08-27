"""拓扑图路由 - 查询服务依赖拓扑、触发拓扑构建、计算爆炸半径

端点：
- GET /                 : 获取完整拓扑图（节点 + 边）
- POST /build/{host}     : 触发指定主机的拓扑构建（调用 topology_build 工具）
- GET /blast-radius/{node} : 计算指定节点的故障爆炸半径
- GET /nodes             : 列出所有拓扑节点
- GET /edges             : 列出所有拓扑边
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.deps import get_current_active_user
from ..database import get_db
from ..models.topology import TopologyEdge, TopologyNode
from ..models.user import User
from ...tools.base import ToolRegistry

router = APIRouter(tags=["拓扑图"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------
class TopologyNodeItem(BaseModel):
    """拓扑节点条目"""

    id: int = Field(..., description="节点 ID")
    host: str = Field(..., description="主机地址")
    service_name: Optional[str] = Field(None, description="服务名称")
    port: Optional[int] = Field(None, description="监听端口")
    node_type: str = Field(..., description="节点类型（server/service/database/external）")
    metadata: Optional[Dict[str, Any]] = Field(None, description="节点元数据")


class TopologyEdgeItem(BaseModel):
    """拓扑边条目"""

    id: int = Field(..., description="边 ID")
    source_id: int = Field(..., description="源节点 ID")
    target_id: int = Field(..., description="目标节点 ID")
    edge_type: str = Field(..., description="边类型（dependency/calls）")
    metadata: Optional[Dict[str, Any]] = Field(None, description="边元数据")


class TopologyGraph(BaseModel):
    """完整拓扑图"""

    nodes: List[TopologyNodeItem] = Field(
        default_factory=list, description="节点列表"
    )
    edges: List[TopologyEdgeItem] = Field(
        default_factory=list, description="边列表"
    )
    node_count: int = Field(0, description="节点数量")
    edge_count: int = Field(0, description="边数量")


class BlastRadiusResult(BaseModel):
    """爆炸半径计算结果"""

    source_node: str = Field(..., description="起始节点标识")
    direction: str = Field("downstream", description="遍历方向（downstream/upstream）")
    impacted_count: int = Field(0, description="受影响服务数量")
    impacted_services: List[Dict[str, Any]] = Field(
        default_factory=list, description="受影响服务详情"
    )
    affected_nodes: List[Dict[str, Any]] = Field(
        default_factory=list, description="受影响节点列表"
    )
    traversal_path: List[Dict[str, Any]] = Field(
        default_factory=list, description="遍历路径"
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _node_to_item(node: TopologyNode) -> TopologyNodeItem:
    """将 ORM 节点对象转换为响应模型"""
    return TopologyNodeItem(
        id=node.id,
        host=node.host,
        service_name=node.service_name,
        port=node.port,
        node_type=node.node_type,
        metadata=node.meta,
    )


def _edge_to_item(edge: TopologyEdge) -> TopologyEdgeItem:
    """将 ORM 边对象转换为响应模型"""
    return TopologyEdgeItem(
        id=edge.id,
        source_id=edge.source_id,
        target_id=edge.target_id,
        edge_type=edge.edge_type,
        metadata=edge.meta,
    )


def _build_graph(db: Session) -> TopologyGraph:
    """从数据库构建完整拓扑图"""
    nodes = db.query(TopologyNode).all()
    edges = db.query(TopologyEdge).all()

    node_items = [_node_to_item(n) for n in nodes]
    edge_items = [_edge_to_item(e) for e in edges]

    return TopologyGraph(
        nodes=node_items,
        edges=edge_items,
        node_count=len(node_items),
        edge_count=len(edge_items),
    )


def _build_tool_topology(db: Session) -> Dict[str, Any]:
    """从数据库构建 blast_radius 工具所需的拓扑结构

    工具拓扑格式：{"nodes": [{"id": "...", ...}], "edges": [{"source": "...", ...}]}
    节点 id 使用 "host:service:port" 格式，与 topology_build 工具一致。
    """
    nodes = db.query(TopologyNode).all()
    edges = db.query(TopologyEdge).all()

    # 数据库节点主键 -> 字符串标识的映射
    db_id_map: Dict[int, str] = {}

    tool_nodes: List[Dict[str, Any]] = []
    for n in nodes:
        node_key = f"{n.host}:{n.service_name or 'unknown'}:{n.port or 0}"
        db_id_map[n.id] = node_key
        tool_nodes.append({
            "id": node_key,
            "host": n.host,
            "service": n.service_name or "unknown",
            "port": n.port,
            "type": n.node_type,
        })

    tool_edges: List[Dict[str, Any]] = []
    for e in edges:
        source_key = db_id_map.get(e.source_id, "")
        target_key = db_id_map.get(e.target_id, "")
        if source_key and target_key:
            tool_edges.append({
                "source": source_key,
                "target": target_key,
                "type": e.edge_type,
            })

    return {"nodes": tool_nodes, "edges": tool_edges}


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("", response_model=TopologyGraph, summary="获取完整拓扑图")
def get_topology(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取完整的拓扑图，包含所有节点和边。

    Returns:
        TopologyGraph: 拓扑图（节点 + 边）
    """
    return _build_graph(db)


@router.post("/build/{host}", response_model=TopologyGraph, summary="触发拓扑构建")
def build_topology(
    host: str = Path(..., description="目标主机地址"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """触发指定主机的拓扑构建。

    通过 ToolRegistry 调用 topology_build 工具，SSH 采集该主机的监听端口
    和已建立连接，推断服务依赖关系，并将结果持久化到数据库。

    Args:
        host: 目标主机地址
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        TopologyGraph: 构建完成后的完整拓扑图
    """
    # 获取拓扑构建工具
    topology_tool = ToolRegistry.get("topology_build")
    if not topology_tool:
        raise HTTPException(
            status_code=503, detail="拓扑构建工具未注册，无法采集拓扑"
        )

    # 调用工具采集拓扑
    result = topology_tool.execute_with_logging(hosts=[host])
    if not result.success:
        raise HTTPException(
            status_code=500, detail=f"拓扑构建失败: {result.error}"
        )

    data = result.data or {}
    tool_nodes = data.get("nodes", [])
    tool_edges = data.get("edges", [])

    # 清除该主机已有的拓扑数据（先删边，再删节点）
    existing_node_ids = [
        nid
        for (nid,) in db.query(TopologyNode.id)
        .filter(TopologyNode.host == host)
        .all()
    ]
    if existing_node_ids:
        db.query(TopologyEdge).filter(
            (TopologyEdge.source_id.in_(existing_node_ids))
            | (TopologyEdge.target_id.in_(existing_node_ids))
        ).delete(synchronize_session=False)
        db.flush()

    db.query(TopologyNode).filter(
        TopologyNode.host == host
    ).delete(synchronize_session=False)
    db.flush()

    # 持久化新节点：工具节点字符串 id -> 数据库主键
    node_id_map: Dict[str, int] = {}
    for node in tool_nodes:
        node_key = node.get("id", "")
        db_node = TopologyNode(
            host=node.get("host", host),
            service_name=node.get("service"),
            port=node.get("port"),
            node_type=node.get("type", "service"),
            meta=node,
        )
        db.add(db_node)
        db.flush()
        node_id_map[node_key] = db_node.id

    # 持久化新边
    for edge in tool_edges:
        source_id = node_id_map.get(edge.get("source", ""))
        target_id = node_id_map.get(edge.get("target", ""))
        if source_id and target_id:
            db_edge = TopologyEdge(
                source_id=source_id,
                target_id=target_id,
                edge_type=edge.get("type", "dependency"),
                meta=edge,
            )
            db.add(db_edge)

    db.commit()
    logger.info(
        f"拓扑构建完成: host={host}, 节点={len(tool_nodes)}, "
        f"边={len(tool_edges)}"
    )

    # 返回更新后的完整拓扑图
    return _build_graph(db)


@router.get(
    "/blast-radius/{node}",
    response_model=BlastRadiusResult,
    summary="计算爆炸半径",
)
def get_blast_radius(
    node: str = Path(..., description="目标节点 ID 或节点标识"),
    direction: str = Query(
        "downstream",
        description="遍历方向：downstream（故障影响）/ upstream（依赖链路）",
    ),
    max_depth: int = Query(
        0, ge=0, description="最大遍历深度（0 表示不限制）"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """计算指定节点的故障爆炸半径。

    从数据库加载拓扑图，使用 blast_radius 工具执行 BFS 遍历，
    计算该节点故障后受影响的所有服务。

    Args:
        node: 目标节点，可以是数据库主键（数字）或节点标识字符串
        direction: 遍历方向（downstream=故障影响，upstream=依赖链路）
        max_depth: 最大遍历深度（0 表示不限制）
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        BlastRadiusResult: 爆炸半径计算结果
    """
    blast_tool = ToolRegistry.get("blast_radius")
    if not blast_tool:
        raise HTTPException(
            status_code=503, detail="爆炸半径工具未注册"
        )

    # 从数据库构建工具拓扑
    topology = _build_tool_topology(db)

    if not topology["nodes"]:
        raise HTTPException(
            status_code=404,
            detail="拓扑图为空，请先调用 POST /build/{host} 构建拓扑",
        )

    # 将路径参数匹配到拓扑节点标识
    # node 可能是数据库主键（数字字符串）或节点标识字符串
    target_node = node
    if node.isdigit():
        db_id = int(node)
        db_id_map: Dict[int, str] = {}
        for n in db.query(TopologyNode).all():
            node_key = f"{n.host}:{n.service_name or 'unknown'}:{n.port or 0}"
            db_id_map[n.id] = node_key
        target_node = db_id_map.get(db_id, node)

    # 调用爆炸半径工具
    result = blast_tool.execute_with_logging(
        node=target_node,
        topology=topology,
        direction=direction,
        max_depth=max_depth,
    )

    if not result.success:
        raise HTTPException(
            status_code=500, detail=f"爆炸半径计算失败: {result.error}"
        )

    data = result.data or {}
    return BlastRadiusResult(
        source_node=data.get("source_node", target_node),
        direction=data.get("direction", direction),
        impacted_count=data.get("impacted_count", 0),
        impacted_services=data.get("impacted_services", []),
        affected_nodes=data.get("affected_nodes", []),
        traversal_path=data.get("traversal_path", []),
    )


@router.get("/nodes", response_model=List[TopologyNodeItem], summary="列出所有拓扑节点")
def list_nodes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """列出所有拓扑节点。

    Returns:
        List[TopologyNodeItem]: 拓扑节点列表
    """
    nodes = db.query(TopologyNode).all()
    return [_node_to_item(n) for n in nodes]


@router.get("/edges", response_model=List[TopologyEdgeItem], summary="列出所有拓扑边")
def list_edges(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """列出所有拓扑边。

    Returns:
        List[TopologyEdgeItem]: 拓扑边列表
    """
    edges = db.query(TopologyEdge).all()
    return [_edge_to_item(e) for e in edges]
