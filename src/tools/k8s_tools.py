"""Kubernetes 运维工具 - 基于 kubernetes Python 客户端，支持 Pod 状态、事件、日志、资源描述

配置加载策略：
1. 优先尝试从 ~/.kube/config 加载 kubeconfig
2. 若 kubeconfig 不可用，回退到集群内服务账号（in-cluster）配置
3. 若 kubernetes 包未安装，所有工具返回带错误信息的 ToolResult
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from .base import BaseTool, ToolResult, ToolParameter

# 尝试导入 kubernetes 客户端，若未安装则优雅降级
try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    HAS_K8S = True
except ImportError:
    HAS_K8S = False
    ApiException = Exception  # 类型占位，保证 except 语法可用

logger = logging.getLogger(__name__)


def _load_k8s_config() -> Optional[str]:
    """加载 Kubernetes 集群配置

    优先尝试 kubeconfig 文件，失败后回退到 in-cluster 配置。

    Returns:
        加载成功返回 None，失败返回错误描述字符串
    """
    if not HAS_K8S:
        return "kubernetes 包未安装，请执行: pip install kubernetes"

    # 1. 尝试从默认 kubeconfig 路径加载
    kubeconfig_path = os.path.expanduser("~/.kube/config")
    try:
        if os.path.exists(kubeconfig_path):
            config.load_kube_config(config_file=kubeconfig_path)
            logger.info(f"已加载 kubeconfig: {kubeconfig_path}")
            return None
    except Exception as e:
        logger.warning(f"加载 kubeconfig 失败: {e}")

    # 2. 回退到集群内配置（in-cluster）
    try:
        config.load_incluster_config()
        logger.info("已加载 in-cluster 配置")
        return None
    except Exception as e:
        return (f"无法加载 Kubernetes 配置：kubeconfig 不可用且 in-cluster "
                f"配置加载失败（{type(e).__name__}: {e}）")


def _format_age(creation_timestamp) -> str:
    """计算并格式化资源年龄（如 1d2h、3h15m、10m5s）"""
    if not creation_timestamp:
        return "unknown"
    # kubernetes 客户端返回的 creation_timestamp 为 datetime 对象
    now = datetime.now(timezone.utc)
    try:
        if creation_timestamp.tzinfo is None:
            creation_timestamp = creation_timestamp.replace(tzinfo=timezone.utc)
        delta = now - creation_timestamp
    except Exception:
        return "unknown"

    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "unknown"
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"{days}d{hours}h"
    if hours > 0:
        return f"{hours}h{minutes}m"
    if minutes > 0:
        return f"{minutes}m{seconds}s"
    return f"{seconds}s"


class K8sPodStatusTool(BaseTool):
    """Kubernetes Pod 状态查询工具 - 跨命名空间查询 Pod 运行状态"""

    name = "k8s_pod_status"
    description = ("查询 Kubernetes 集群中 Pod 的运行状态，包括名称、命名空间、"
                   "状态、重启次数、年龄、所在节点。支持按命名空间和标签过滤。")
    parameters = [
        ToolParameter(
            name="namespace",
            type="string",
            description="命名空间（不指定则查询所有命名空间）",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="label_selector",
            type="string",
            description="标签选择器（如 'app=nginx,env=prod'）",
            required=False,
            default=None,
        ),
    ]

    def __init__(self, config=None):
        self._config = config

    def execute(self, **kwargs) -> ToolResult:
        if not HAS_K8S:
            return ToolResult(
                success=False,
                error="kubernetes 包未安装，请执行: pip install kubernetes",
            )

        namespace = kwargs.get('namespace')
        label_selector = kwargs.get('label_selector')

        # 加载集群配置
        err = _load_k8s_config()
        if err:
            return ToolResult(success=False, error=err)

        try:
            v1 = client.CoreV1Api()

            if namespace:
                pods = v1.list_namespaced_pod(
                    namespace, label_selector=label_selector
                )
            else:
                pods = v1.list_pod_for_all_namespaces(
                    label_selector=label_selector
                )

            pod_list = []
            for pod in pods.items:
                # 统计所有容器的重启次数
                restart_count = 0
                container_statuses = pod.status.container_statuses or []
                for cs in container_statuses:
                    restart_count += cs.restart_count

                pod_list.append({
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "status": pod.status.phase,
                    "restarts": restart_count,
                    "age": _format_age(pod.metadata.creation_timestamp),
                    "node": pod.spec.node_name or "N/A",
                })

            return ToolResult(
                success=True,
                data={
                    "pods": pod_list,
                    "total": len(pod_list),
                },
                metadata={
                    "namespace": namespace or "all",
                    "label_selector": label_selector or "none",
                },
            )

        except ApiException as e:
            return ToolResult(
                success=False,
                error=f"Kubernetes API 异常: {e}",
                metadata={"namespace": namespace or "all"},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"查询 Pod 状态失败: {type(e).__name__}: {e}",
                metadata={"namespace": namespace or "all"},
            )


class K8sEventTool(BaseTool):
    """Kubernetes 事件查询工具 - 查询集群事件，支持命名空间过滤"""

    name = "k8s_events"
    description = ("查询 Kubernetes 集群事件，可按命名空间和字段选择器过滤。"
                   "常用于排查 Pod 调度失败、镜像拉取错误、资源不足等问题。")
    parameters = [
        ToolParameter(
            name="namespace",
            type="string",
            description="命名空间（不指定则查询所有命名空间）",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="field_selector",
            type="string",
            description="字段选择器（如 'type=Warning'）",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="返回事件数量上限",
            required=False,
            default=100,
        ),
    ]

    def __init__(self, config=None):
        self._config = config

    def execute(self, **kwargs) -> ToolResult:
        if not HAS_K8S:
            return ToolResult(
                success=False,
                error="kubernetes 包未安装，请执行: pip install kubernetes",
            )

        namespace = kwargs.get('namespace')
        field_selector = kwargs.get('field_selector')
        limit = kwargs.get('limit', 100)

        err = _load_k8s_config()
        if err:
            return ToolResult(success=False, error=err)

        try:
            v1 = client.CoreV1Api()

            if namespace:
                events = v1.list_namespaced_event(
                    namespace,
                    field_selector=field_selector,
                    limit=limit,
                )
            else:
                events = v1.list_event_for_all_namespaces(
                    field_selector=field_selector,
                    limit=limit,
                )

            event_list = []
            for ev in events.items:
                event_list.append({
                    "namespace": ev.metadata.namespace,
                    "name": ev.metadata.name,
                    "type": ev.type,
                    "reason": ev.reason,
                    "message": ev.message,
                    "involved_object": (
                        f"{ev.involved_object.kind}/"
                        f"{ev.involved_object.name}"
                        if ev.involved_object else None
                    ),
                    "last_timestamp": (
                        str(ev.last_timestamp) if ev.last_timestamp else None
                    ),
                    "count": ev.count,
                })

            return ToolResult(
                success=True,
                data={
                    "events": event_list,
                    "total": len(event_list),
                },
                metadata={
                    "namespace": namespace or "all",
                    "field_selector": field_selector or "none",
                },
            )

        except ApiException as e:
            return ToolResult(
                success=False,
                error=f"Kubernetes API 异常: {e}",
                metadata={"namespace": namespace or "all"},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"查询事件失败: {type(e).__name__}: {e}",
                metadata={"namespace": namespace or "all"},
            )


class K8sLogsTool(BaseTool):
    """Kubernetes 容器日志获取工具 - 获取指定 Pod 容器日志"""

    name = "k8s_logs"
    description = ("获取 Kubernetes Pod 中容器的日志。支持指定容器名和尾部行数。"
                   "用于排查应用启动失败、运行时异常等问题。")
    parameters = [
        ToolParameter(name="pod_name", type="string", description="Pod 名称"),
        ToolParameter(name="namespace", type="string", description="命名空间"),
        ToolParameter(
            name="container",
            type="string",
            description="容器名称（多容器 Pod 时必填）",
            required=False,
            default=None,
        ),
        ToolParameter(
            name="tail_lines",
            type="integer",
            description="从末尾获取的行数",
            required=False,
            default=100,
        ),
        ToolParameter(
            name="previous",
            type="boolean",
            description="是否获取上一个（已终止）容器的日志",
            required=False,
            default=False,
        ),
    ]

    def __init__(self, config=None):
        self._config = config

    def execute(self, **kwargs) -> ToolResult:
        if not HAS_K8S:
            return ToolResult(
                success=False,
                error="kubernetes 包未安装，请执行: pip install kubernetes",
            )

        pod_name = kwargs['pod_name']
        namespace = kwargs['namespace']
        container = kwargs.get('container')
        tail_lines = kwargs.get('tail_lines', 100)
        previous = kwargs.get('previous', False)

        err = _load_k8s_config()
        if err:
            return ToolResult(success=False, error=err)

        try:
            v1 = client.CoreV1Api()
            log_text = v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
                previous=previous,
            )

            lines = log_text.splitlines() if log_text else []

            return ToolResult(
                success=True,
                data={
                    "logs": log_text or "",
                    "line_count": len(lines),
                    "pod_name": pod_name,
                    "namespace": namespace,
                    "container": container or "default",
                },
                metadata={
                    "pod_name": pod_name,
                    "namespace": namespace,
                    "container": container or "default",
                    "tail_lines": tail_lines,
                    "previous": previous,
                },
            )

        except ApiException as e:
            return ToolResult(
                success=False,
                error=f"Kubernetes API 异常: {e}",
                metadata={"pod_name": pod_name, "namespace": namespace},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"获取容器日志失败: {type(e).__name__}: {e}",
                metadata={"pod_name": pod_name, "namespace": namespace},
            )


class K8sDescribeTool(BaseTool):
    """Kubernetes 资源描述工具 - 描述任意资源（Deployment、Service、Pod 等）"""

    name = "k8s_describe"
    description = ("描述 Kubernetes 集群中的资源详情，支持 Deployment、Service、"
                   "Pod、ConfigMap、StatefulSet、DaemonSet、ReplicaSet 等常见资源类型。")
    parameters = [
        ToolParameter(
            name="resource_type",
            type="string",
            description=("资源类型（小写复数形式）：pods, services, deployments, "
                         "configmaps, statefulsets, daemonsets, replicasets"),
        ),
        ToolParameter(name="name", type="string", description="资源名称"),
        ToolParameter(name="namespace", type="string", description="命名空间"),
    ]

    # 资源类型 -> (API 客户端类, 读取方法名)
    RESOURCE_MAP = {
        "pods": ("CoreV1Api", "read_namespaced_pod"),
        "services": ("CoreV1Api", "read_namespaced_service"),
        "configmaps": ("CoreV1Api", "read_namespaced_config_map"),
        "secrets": ("CoreV1Api", "read_namespaced_secret"),
        "nodes": ("CoreV1Api", "read_node"),
        "namespaces": ("CoreV1Api", "read_namespace"),
        "deployments": ("AppsV1Api", "read_namespaced_deployment"),
        "statefulsets": ("AppsV1Api", "read_namespaced_stateful_set"),
        "daemonsets": ("AppsV1Api", "read_namespaced_daemon_set"),
        "replicasets": ("AppsV1Api", "read_namespaced_replica_set"),
    }

    def __init__(self, config=None):
        self._config = config

    def execute(self, **kwargs) -> ToolResult:
        if not HAS_K8S:
            return ToolResult(
                success=False,
                error="kubernetes 包未安装，请执行: pip install kubernetes",
            )

        resource_type = kwargs['resource_type'].lower()
        name = kwargs['name']
        namespace = kwargs['namespace']

        mapping = self.RESOURCE_MAP.get(resource_type)
        if not mapping:
            supported = ", ".join(sorted(self.RESOURCE_MAP.keys()))
            return ToolResult(
                success=False,
                error=f"不支持的资源类型: {resource_type}，支持的类型: {supported}",
            )

        api_class_name, method_name = mapping

        err = _load_k8s_config()
        if err:
            return ToolResult(success=False, error=err)

        try:
            # 根据映射获取对应的 API 客户端
            api_class = getattr(client, api_class_name)
            api_instance = api_class()
            read_method = getattr(api_instance, method_name)

            # nodes 和 namespaces 是非命名空间资源
            if resource_type in ("nodes", "namespaces"):
                resource = read_method(name)
            else:
                resource = read_method(name, namespace)

            # 将资源对象转换为字典表示
            # 使用 client 自带的 to_dict 序列化
            try:
                from kubernetes.client.api_client import ApiClient
                serialized = ApiClient().sanitize_for_serialization(resource)
                result_data = serialized
            except Exception:
                # 回退到手动提取关键字段
                result_data = self._extract_fields(resource, resource_type)

            return ToolResult(
                success=True,
                data=result_data,
                metadata={
                    "resource_type": resource_type,
                    "name": name,
                    "namespace": namespace,
                },
            )

        except ApiException as e:
            return ToolResult(
                success=False,
                error=f"Kubernetes API 异常: {e}",
                metadata={
                    "resource_type": resource_type,
                    "name": name,
                    "namespace": namespace,
                },
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"描述资源失败: {type(e).__name__}: {e}",
                metadata={
                    "resource_type": resource_type,
                    "name": name,
                    "namespace": namespace,
                },
            )

    @staticmethod
    def _extract_fields(resource, resource_type: str) -> dict:
        """从资源对象中手动提取关键字段（序列化失败时的回退方案）"""
        try:
            meta = resource.metadata
            spec = resource.spec
            status = resource.status
            return {
                "kind": resource.kind if hasattr(resource, 'kind') else resource_type,
                "name": meta.name,
                "namespace": meta.namespace,
                "labels": meta.labels if meta and meta.labels else {},
                "annotations": meta.annotations if meta and meta.annotations else {},
                "creation_timestamp": (
                    str(meta.creation_timestamp) if meta and meta.creation_timestamp else None
                ),
                "spec": str(spec) if spec else None,
                "status": str(status) if status else None,
            }
        except Exception as e:
            return {"raw": str(resource), "extract_error": str(e)}
