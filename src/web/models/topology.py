"""拓扑图模型 - 服务依赖关系节点与边

存储通过 topology_build 工具采集的服务拓扑信息：
- TopologyNode: 拓扑节点（服务器 / 服务 / 数据库 / 外部依赖）
- TopologyEdge: 拓扑边（依赖 / 调用关系）

字段说明：
TopologyNode:
- id: 主键自增
- host: 主机地址
- service_name: 服务名称
- port: 监听端口
- node_type: 节点类型（server / service / database / external）
- metadata: 节点元数据（JSON，存储工具返回的完整节点信息）
- created_at: 创建时间

TopologyEdge:
- id: 主键自增
- source_id: 源节点 ID（外键 -> topology_nodes.id）
- target_id: 目标节点 ID（外键 -> topology_nodes.id）
- edge_type: 边类型（dependency / calls）
- metadata: 边元数据（JSON，存储工具返回的完整边信息）
- created_at: 创建时间
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class TopologyNode(Base):
    """拓扑节点表 - 表示服务拓扑图中的一个节点

    节点类型说明：
    - server: 物理或虚拟服务器节点
    - service: 运行在主机上的服务进程（如 nginx、mysqld）
    - database: 数据库服务节点
    - external: 拓扑采集范围外的外部依赖
    """

    __tablename__ = "topology_nodes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    host: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    service_name: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 节点类型：server / service / database / external
    node_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="service", index=True
    )
    # 节点元数据（JSON）- 注意：Python 属性名为 meta，数据库列名为 metadata，
    # 避免与 SQLAlchemy 声明式基类的 metadata 属性冲突
    meta: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<TopologyNode(id={self.id}, host={self.host!r}, "
            f"service_name={self.service_name!r}, port={self.port}, "
            f"node_type={self.node_type!r})>"
        )


class TopologyEdge(Base):
    """拓扑边表 - 表示节点间的依赖或调用关系

    边类型说明：
    - dependency: 源节点依赖目标节点（A -> B 表示 A 依赖 B）
    - calls: 源节点调用目标节点（RPC / HTTP 调用关系）
    """

    __tablename__ = "topology_edges"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("topology_nodes.id"), nullable=False, index=True
    )
    target_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("topology_nodes.id"), nullable=False, index=True
    )
    # 边类型：dependency / calls
    edge_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="dependency", index=True
    )
    # 边元数据（JSON）- 同上，Python 属性名为 meta
    meta: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<TopologyEdge(id={self.id}, source_id={self.source_id}, "
            f"target_id={self.target_id}, edge_type={self.edge_type!r})>"
        )
