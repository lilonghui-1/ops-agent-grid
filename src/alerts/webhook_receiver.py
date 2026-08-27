"""Webhook 接收器 - 接收外部告警系统的告警推送

支持接收来自 Prometheus Alertmanager、Grafana 等外部系统的告警，
将异构的告警数据归一化为统一格式，并可选触发自动事件调查。

主要功能：
- receive_alert_webhook(payload): 解析并归一化外部告警载荷
- normalize_alert(raw): 将不同来源的告警数据转换为统一格式
- create_incident_from_alert(): 根据归一化告警创建事件
- trigger_investigation(incident): 触发 IncidentInvestigator 自动调查
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..tools.base import ToolRegistry
from ..utils.logger import get_logger
from ..web.database import SessionLocal
from ..web.models.incident import Incident

logger = get_logger("webhook_receiver")


# 严重级别归一化映射
_SEVERITY_MAP = {
    # Alertmanager / Grafana 常见级别
    "critical": "critical",
    "high": "critical",
    "error": "critical",
    "fatal": "critical",
    "warning": "warning",
    "moderate": "warning",
    "medium": "warning",
    "info": "info",
    "low": "info",
    "debug": "info",
    "ok": "info",
    "resolved": "info",
    # 中文级别
    "严重": "critical",
    "警告": "warning",
    "提示": "info",
    "信息": "info",
}


class WebhookReceiver:
    """Webhook 接收器

    接收外部告警系统的推送，归一化后创建事件并可选触发调查。

    Args:
        config: 应用配置对象，用于读取自动调查开关与调查器配置
        auto_investigate: 是否在创建事件后自动触发调查（默认从 config 读取）
    """

    def __init__(
        self,
        config: Any = None,
        auto_investigate: Optional[bool] = None,
    ):
        self.config = config
        # 自动调查开关：显式参数优先，其次从 config 读取，默认关闭
        if auto_investigate is not None:
            self.auto_investigate = auto_investigate
        elif config and hasattr(config, "auto_investigate"):
            self.auto_investigate = bool(config.auto_investigate)
        else:
            self.auto_investigate = False

    # ------------------------------------------------------------------
    # 接收 Webhook
    # ------------------------------------------------------------------
    def receive_alert_webhook(
        self, payload: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """接收外部告警 Webhook 载荷，归一化并创建事件。

        兼容 Prometheus Alertmanager 的 alerts 数组格式与单条告警格式。

        Args:
            payload: Webhook 请求体（JSON 解析后的字典）

        Returns:
            已创建的事件列表，每项包含事件 ID 与归一化告警信息
        """
        # Alertmanager 标准格式：{"alerts": [...]}, "commonLabels" 等
        alerts_raw: List[Dict[str, Any]] = []

        if isinstance(payload, dict) and isinstance(
            payload.get("alerts"), list
        ):
            # Alertmanager 批量告警
            alerts_raw = payload["alerts"]
        elif isinstance(payload, list):
            # 顶层为数组的情况
            alerts_raw = payload
        else:
            # 单条告警
            alerts_raw = [payload]

        logger.info(f"接收到 {len(alerts_raw)} 条外部告警")

        results: List[Dict[str, Any]] = []
        for raw_alert in alerts_raw:
            if not isinstance(raw_alert, dict):
                continue
            try:
                normalized = self.normalize_alert(raw_alert)
                incident = self.create_incident_from_alert(normalized)
                if incident is not None:
                    results.append({
                        "incident_id": incident.id,
                        "title": incident.title,
                        "severity": incident.severity,
                        "source": incident.source,
                        "normalized": normalized,
                    })

                    # 自动触发事件调查
                    if self.auto_investigate:
                        self.trigger_investigation(incident)
            except Exception as e:
                logger.error(
                    f"处理外部告警失败: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                results.append({
                    "error": str(e),
                    "raw_alert": raw_alert,
                })

        logger.info(
            f"Webhook 处理完成，成功创建 {len([r for r in results if r.get('incident_id')])} 个事件"
        )
        return results

    # ------------------------------------------------------------------
    # 归一化告警数据
    # ------------------------------------------------------------------
    def normalize_alert(
        self, raw: Dict[str, Any]
    ) -> Dict[str, Any]:
        """将不同来源的告警数据归一化为统一格式。

        统一格式字段：
        - alertname: 告警名称
        - severity: 严重级别（info / warning / critical）
        - status: 告警状态（firing / resolved）
        - message: 告警消息
        - summary: 摘要
        - description: 描述
        - server_host: 服务器地址
        - service_name: 服务名称
        - starts_at: 开始时间
        - ends_at: 结束时间
        - labels: 标签字典
        - annotations: 注解字典
        - source_system: 来源系统

        Args:
            raw: 原始告警数据

        Returns:
            归一化后的告警字典
        """
        # 标签与注解（Alertmanager 格式）
        labels = raw.get("labels", {}) or {}
        annotations = raw.get("annotations", {}) or {}

        # 告警名称：优先从 labels 取，其次从顶层字段取
        alertname = (
            labels.get("alertname")
            or raw.get("alertname")
            or raw.get("name")
            or "未命名告警"
        )

        # 严重级别归一化
        raw_severity = (
            labels.get("severity")
            or raw.get("severity")
            or raw.get("priority")
            or "warning"
        )
        severity = _SEVERITY_MAP.get(
            str(raw_severity).lower(), "warning"
        )

        # 告警状态
        status = raw.get("status") or labels.get("status") or "firing"

        # 服务器地址：优先 instance / hostname，其次 service
        server_host = (
            labels.get("instance")
            or labels.get("host")
            or labels.get("hostname")
            or raw.get("instance")
            or raw.get("hostname")
            or labels.get("node")
        )
        service_name = (
            labels.get("job")
            or labels.get("service")
            or raw.get("service")
            or raw.get("job")
        )

        # 消息内容
        summary = (
            annotations.get("summary")
            or raw.get("summary")
            or raw.get("message")
            or ""
        )
        description = (
            annotations.get("description")
            or raw.get("description")
            or raw.get("message")
            or summary
        )
        message = summary or description or alertname

        # 时间
        starts_at = raw.get("startsAt") or raw.get("starts_at")
        ends_at = raw.get("endsAt") or raw.get("ends_at")

        # 来源系统
        source_system = (
            raw.get("source")
            or raw.get("source_system")
            or labels.get("source")
            or "external"
        )

        return {
            "alertname": alertname,
            "severity": severity,
            "status": status,
            "message": message,
            "summary": summary,
            "description": description,
            "server_host": server_host,
            "service_name": service_name,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "labels": labels,
            "annotations": annotations,
            "source_system": source_system,
        }

    # ------------------------------------------------------------------
    # 根据归一化告警创建事件
    # ------------------------------------------------------------------
    def create_incident_from_alert(
        self, alert: Dict[str, Any]
    ) -> Optional[Incident]:
        """根据归一化后的告警数据创建事件记录。

        Args:
            alert: 归一化后的告警字典

        Returns:
            创建的 Incident 对象，失败返回 None
        """
        db = SessionLocal()
        try:
            title = f"[外部告警] {alert.get('alertname', '未知告警')}"
            desc_lines = [
                f"来源系统: {alert.get('source_system', '未知')}",
                f"严重级别: {alert.get('severity', 'warning')}",
                f"状态: {alert.get('status', 'firing')}",
            ]
            if alert.get("summary"):
                desc_lines.append(f"摘要: {alert['summary']}")
            if alert.get("description"):
                desc_lines.append(f"描述: {alert['description']}")
            if alert.get("server_host"):
                desc_lines.append(f"服务器: {alert['server_host']}")
            if alert.get("service_name"):
                desc_lines.append(f"服务: {alert['service_name']}")
            if alert.get("starts_at"):
                desc_lines.append(f"开始时间: {alert['starts_at']}")
            if alert.get("ends_at"):
                desc_lines.append(f"结束时间: {alert['ends_at']}")
            description = "\n".join(desc_lines)

            # 构建时间线
            timeline = [
                {
                    "time": datetime.now().isoformat(),
                    "event": "外部告警接入，事件创建",
                    "source_system": alert.get("source_system"),
                    "alertname": alert.get("alertname"),
                    "raw_labels": alert.get("labels", {}),
                    "raw_annotations": alert.get("annotations", {}),
                }
            ]

            # 已解决的告警直接创建为 resolved 状态
            initial_status = (
                "resolved"
                if alert.get("status") == "resolved"
                else "open"
            )

            incident = Incident(
                title=title,
                description=description,
                alert_rule_id=None,
                severity=alert.get("severity", "warning"),
                status=initial_status,
                source="webhook",
                server_host=alert.get("server_host"),
                service_name=alert.get("service_name"),
                timeline=json.dumps(timeline, ensure_ascii=False),
                created_by="webhook_receiver",
            )
            # 已解决的告警补充解决信息
            if initial_status == "resolved":
                incident.resolved_at = datetime.now()
                incident.resolved_by = "webhook_receiver"

            db.add(incident)
            db.commit()
            db.refresh(incident)
            logger.info(
                f"已根据外部告警创建事件 id={incident.id}，"
                f"告警={alert.get('alertname')}"
            )
            return incident
        except Exception as e:
            db.rollback()
            logger.error(f"根据告警创建事件失败: {e}", exc_info=True)
            return None
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 触发自动事件调查
    # ------------------------------------------------------------------
    def trigger_investigation(self, incident: Incident) -> Dict[str, Any]:
        """触发 IncidentInvestigator 对事件进行自动根因分析。

        将调查结果写回事件的 rca_report 与 timeline。

        Args:
            incident: 待调查的事件对象

        Returns:
            调查结果字典
        """
        try:
            from ..agent.incident_investigator import IncidentInvestigator

            if not self.config:
                logger.warning(
                    "未提供配置，无法初始化事件调查器，跳过自动调查"
                )
                return {"error": "未提供配置，无法初始化调查器"}

            investigator = IncidentInvestigator(self.config)

            # 构建告警数据
            alert_data = {
                "id": incident.id,
                "server_host": incident.server_host or "",
                "alert_type": incident.title,
                "severity": incident.severity,
                "message": incident.description or "",
            }

            # 在事件循环中执行异步调查
            task = f"请调查事件：{incident.title}"
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    investigator.run(task=task, alert_data=alert_data)
                )
            finally:
                loop.close()

            # 将 RCA 报告写回事件
            rca_text = result.get("result", "")
            self._update_incident_with_rca(incident, rca_text, result)

            logger.info(
                f"事件自动调查完成，事件 id={incident.id}，"
                f"工具调用次数: {len(result.get('tool_calls', []))}"
            )
            return result
        except ImportError:
            logger.warning("IncidentInvestigator 不可用，跳过自动调查")
            return {"error": "IncidentInvestigator 不可用"}
        except Exception as e:
            logger.error(
                f"自动事件调查失败: {type(e).__name__}: {e}",
                exc_info=True,
            )
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # 将调查结果写回事件
    # ------------------------------------------------------------------
    def _update_incident_with_rca(
        self,
        incident: Incident,
        rca_text: str,
        result: Dict[str, Any],
    ) -> None:
        """将根因分析结果写回事件的 rca_report 与 timeline。

        Args:
            incident: 事件对象
            rca_text: RCA 报告文本
            result: 调查器返回的完整结果
        """
        db = SessionLocal()
        try:
            db_obj = db.query(Incident).filter(
                Incident.id == incident.id
            ).first()
            if db_obj is None:
                logger.warning(
                    f"事件 id={incident.id} 不存在，无法更新 RCA"
                )
                return

            # 写入 RCA 报告
            db_obj.rca_report = rca_text

            # 追加时间线事件
            existing_timeline = []
            if db_obj.timeline:
                try:
                    existing_timeline = json.loads(db_obj.timeline)
                except (ValueError, TypeError):
                    existing_timeline = []
            existing_timeline.append({
                "time": datetime.now().isoformat(),
                "event": "自动根因分析完成",
                "tool_calls": len(result.get("tool_calls", [])),
                "has_error": bool(result.get("error")),
            })
            db_obj.timeline = json.dumps(
                existing_timeline, ensure_ascii=False
            )

            # 事件状态从 open 推进到 investigating
            if db_obj.status == "open":
                db_obj.status = "investigating"

            db.commit()
            logger.info(
                f"已将 RCA 报告写回事件 id={incident.id}"
            )
        except Exception as e:
            db.rollback()
            logger.error(f"更新事件 RCA 失败: {e}", exc_info=True)
        finally:
            db.close()
