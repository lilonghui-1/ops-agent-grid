"""告警规则引擎 - 按计划评估告警规则并触发事件

支持三种规则类型：
1. threshold    : 阈值规则，通过 system_metrics 工具采集指标并与阈值比较
2. log_keyword   : 日志关键词规则，通过日志工具检索日志中是否包含指定关键词
3. promql        : Prometheus 查询规则，通过 httpx 调用 Prometheus API 执行 PromQL

工作流程：
1. evaluate_rules() 拉取所有 enabled=True 的规则
2. 逐条调用 evaluate_rule(rule) 进行评估
3. 命中规则时调用 _fire_alert() 创建事件并发送通知
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from ..tools.base import ToolRegistry
from ..utils.logger import get_logger
from ..web.database import SessionLocal
from ..web.models.alert_rule import AlertRule
from ..web.models.incident import Incident

logger = get_logger("rule_engine")


# 指标类型到 system_metrics metric_type 的映射
_METRIC_TYPE_MAP = {
    "cpu": "cpu",
    "memory": "memory",
    "mem": "memory",
    "disk": "disk",
    "network": "network",
    "net": "network",
}

# 从系统指标文本中提取数值百分比的正则
# 匹配类似 "Memory Usage: 85.2%" 或 "CPU Usage: 90%" 的文本
_PERCENT_RE = re.compile(r"(\d+\.?\d*)\s*%")


class AlertRuleEngine:
    """告警规则引擎

    定时评估所有启用的告警规则，命中时创建事件并通知。

    Args:
        config: 应用配置对象，用于读取 Prometheus 地址、服务器列表等
        prometheus_url: Prometheus API 地址（优先级高于 config）
    """

    def __init__(
        self,
        config: Any = None,
        prometheus_url: Optional[str] = None,
    ):
        self.config = config
        # Prometheus 地址：显式参数优先，其次从 config 读取，最后回退默认值
        self.prometheus_url = prometheus_url
        if not self.prometheus_url and config:
            self.prometheus_url = getattr(
                getattr(config, "prometheus", None), "url", None
            ) if hasattr(config, "prometheus") else None
        if not self.prometheus_url:
            # 尝试从 config 的字典属性中读取
            if config and hasattr(config, "prometheus_url"):
                self.prometheus_url = config.prometheus_url

        # 是否自动触发事件调查
        self.auto_investigate = True
        if config and hasattr(config, "auto_investigate"):
            self.auto_investigate = bool(config.auto_investigate)

    # ------------------------------------------------------------------
    # 批量评估
    # ------------------------------------------------------------------
    def evaluate_rules(self) -> List[Dict[str, Any]]:
        """评估所有启用的告警规则。

        Returns:
            评估结果列表，每项包含规则 ID、是否命中、详情等
        """
        results: List[Dict[str, Any]] = []
        db = SessionLocal()
        try:
            rules = (
                db.query(AlertRule)
                .filter(AlertRule.enabled.is_(True))
                .all()
            )
            logger.info(f"开始评估告警规则，共 {len(rules)} 条启用规则")

            for rule in rules:
                try:
                    fired = self.evaluate_rule(rule, db=db)
                    results.append({
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "fired": fired,
                    })
                except Exception as e:
                    logger.error(
                        f"评估规则失败 [{rule.name}]: {type(e).__name__}: {e}"
                    )
                    results.append({
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "fired": False,
                        "error": str(e),
                    })
        finally:
            db.close()

        fired_count = sum(1 for r in results if r.get("fired"))
        logger.info(
            f"告警规则评估完成，命中 {fired_count}/{len(results)} 条"
        )
        return results

    # ------------------------------------------------------------------
    # 单条规则评估
    # ------------------------------------------------------------------
    def evaluate_rule(
        self,
        rule: AlertRule,
        db: Any = None,
    ) -> bool:
        """评估单条告警规则。

        根据规则类型分别调用对应的评估方法，命中时触发告警。

        Args:
            rule: 告警规则对象
            db: 数据库会话（如不提供则新建）

        Returns:
            是否命中规则（触发告警）
        """
        own_session = db is None
        if own_session:
            db = SessionLocal()
        try:
            # 更新最近评估时间
            rule.last_evaluated_at = datetime.now()
            db.flush()

            # 根据规则类型分发到对应评估器
            evaluator_map = {
                "threshold": self._evaluate_threshold,
                "log_keyword": self._evaluate_log_keyword,
                "promql": self._evaluate_promql,
            }
            evaluator = evaluator_map.get(rule.rule_type)
            if evaluator is None:
                logger.warning(
                    f"未知的规则类型: {rule.rule_type}（规则: {rule.name}）"
                )
                return False

            fired, context = evaluator(rule)
            if fired:
                logger.warning(
                    f"告警规则命中 [{rule.name}]，类型={rule.rule_type}，"
                    f"严重级别={rule.severity}，上下文={context}"
                )
                self._fire_alert(rule, context, db=db)
                rule.last_fired_at = datetime.now()
                db.commit()

            return fired
        finally:
            if own_session:
                db.close()

    # ------------------------------------------------------------------
    # 阈值规则评估
    # ------------------------------------------------------------------
    def _evaluate_threshold(
        self, rule: AlertRule
    ) -> tuple:
        """评估阈值规则：通过 system_metrics 工具采集指标并与阈值比较。

        Args:
            rule: 告警规则，需包含 metric_name / operator / threshold_value

        Returns:
            (是否命中, 上下文信息字典)
        """
        metric_tool = ToolRegistry.get("system_metrics")
        if metric_tool is None:
            logger.warning("system_metrics 工具未注册，跳过阈值规则评估")
            return False, {"error": "system_metrics 工具未注册"}

        # metric_name 格式可为 "cpu" 或 "host:cpu"
        metric_name = rule.metric_name or ""
        host = self._get_first_server_host()
        metric_type = _METRIC_TYPE_MAP.get(
            metric_name.lower(), metric_name.lower()
        )
        if not metric_type:
            return False, {"error": "未配置 metric_name"}

        try:
            result = metric_tool.execute_with_logging(
                host=host,
                metric_type=metric_type,
            )
        except Exception as e:
            logger.error(f"采集指标失败 [{metric_name}]: {e}")
            return False, {"error": f"采集指标失败: {e}"}

        if not result.success:
            return False, {"error": result.error or "采集指标失败"}

        # 从结果文本中解析数值
        current_value = self._extract_metric_value(
            result.data, metric_name, metric_type
        )
        if current_value is None:
            logger.warning(
                f"无法从指标输出中解析 {metric_name} 的数值"
            )
            return False, {"error": "无法解析指标数值"}

        threshold = rule.threshold_value
        operator = rule.operator or ">"
        matched = self._compare(current_value, operator, threshold)

        return matched, {
            "metric_name": metric_name,
            "current_value": current_value,
            "operator": operator,
            "threshold": threshold,
            "server_host": host,
            "raw_output": self._summarize_output(result.data),
        }

    # ------------------------------------------------------------------
    # 日志关键词规则评估
    # ------------------------------------------------------------------
    def _evaluate_log_keyword(
        self, rule: AlertRule
    ) -> tuple:
        """评估日志关键词规则：检索日志是否包含指定关键词。

        Args:
            rule: 告警规则，需包含 log_query（关键词或查询语句）

        Returns:
            (是否命中, 上下文信息字典)
        """
        query = rule.log_query or ""
        if not query:
            return False, {"error": "未配置 log_query"}

        log_tool = ToolRegistry.get("log_fetch") or ToolRegistry.get("log_analyze")
        if log_tool is None:
            logger.warning("日志工具未注册，跳过日志关键词规则评估")
            return False, {"error": "日志工具未注册"}

        host = self._get_first_server_host()
        try:
            result = log_tool.execute_with_logging(
                host=host,
                keyword=query,
                log_file="/var/log/syslog",
                lines=200,
            )
        except Exception as e:
            # 部分日志工具参数签名不同，退化为按日志路径检索
            try:
                result = log_tool.execute_with_logging(host=host)
            except Exception as e2:
                logger.error(f"日志检索失败 [{query}]: {e2}")
                return False, {"error": f"日志检索失败: {e2}"}

        if not result.success:
            return False, {"error": result.error or "日志检索失败"}

        output = self._summarize_output(result.data)
        # 判断日志内容是否包含关键词
        matched = query.lower() in output.lower()

        return matched, {
            "log_query": query,
            "server_host": host,
            "matched_lines": output[:500] if matched else "",
            "raw_output": output,
        }

    # ------------------------------------------------------------------
    # Prometheus PromQL 规则评估
    # ------------------------------------------------------------------
    def _evaluate_promql(
        self, rule: AlertRule
    ) -> tuple:
        """评估 PromQL 规则：通过 httpx 调用 Prometheus API 执行查询。

        Args:
            rule: 告警规则，需包含 promql_expr

        Returns:
            (是否命中, 上下文信息字典)
        """
        expr = rule.promql_expr or ""
        if not expr:
            return False, {"error": "未配置 promql_expr"}

        if not self.prometheus_url:
            logger.warning("未配置 Prometheus 地址，跳过 PromQL 规则评估")
            return False, {"error": "未配置 Prometheus 地址"}

        # Prometheus 即时查询接口
        query_url = self.prometheus_url.rstrip("/") + "/api/v1/query"
        try:
            resp = httpx.get(
                query_url,
                params={"query": expr},
                timeout=10,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"Prometheus 查询失败 [{expr}]: {e}")
            return False, {"error": f"Prometheus 查询失败: {e}"}

        try:
            payload = resp.json()
        except ValueError as e:
            logger.error(f"解析 Prometheus 响应失败: {e}")
            return False, {"error": f"解析响应失败: {e}"}

        if payload.get("status") != "success":
            return False, {
                "error": payload.get("error", "Prometheus 查询非成功状态"),
            }

        result_data = payload.get("data", {}).get("result", [])
        # 当查询返回非空结果时视为命中
        matched = bool(result_data)

        # 提取数值用于上下文
        current_value = None
        if matched and isinstance(result_data, list):
            first = result_data[0]
            value_arr = first.get("value") if isinstance(first, dict) else None
            if isinstance(value_arr, list) and len(value_arr) >= 2:
                try:
                    current_value = float(value_arr[1])
                except (ValueError, TypeError):
                    current_value = None

        # 如果配置了阈值，则进一步比较
        threshold = rule.threshold_value
        if matched and threshold is not None and current_value is not None:
            operator = rule.operator or ">"
            matched = self._compare(current_value, operator, threshold)

        return matched, {
            "promql_expr": expr,
            "current_value": current_value,
            "result_count": len(result_data),
            "threshold": threshold,
        }

    # ------------------------------------------------------------------
    # 触发告警：创建事件并发送通知
    # ------------------------------------------------------------------
    def _fire_alert(
        self,
        rule: AlertRule,
        context: Dict[str, Any],
        db: Any = None,
    ) -> Optional[Incident]:
        """命中规则后创建事件记录并发送通知。

        Args:
            rule: 命中的告警规则
            context: 评估上下文信息
            db: 数据库会话

        Returns:
            创建的 Incident 对象
        """
        own_session = db is None
        if own_session:
            db = SessionLocal()
        try:
            # 构建事件标题与描述
            title = f"[告警] {rule.name}"
            desc_parts = [f"规则类型: {rule.rule_type}", f"严重级别: {rule.severity}"]
            if context.get("metric_name"):
                desc_parts.append(
                    f"指标: {context['metric_name']}"
                )
            if context.get("current_value") is not None:
                desc_parts.append(
                    f"当前值: {context['current_value']}"
                )
            if context.get("threshold") is not None:
                desc_parts.append(
                    f"阈值: {context['threshold']}"
                )
            if context.get("operator"):
                desc_parts.append(f"比较: {context['operator']}")
            if context.get("error"):
                desc_parts.append(f"错误: {context['error']}")
            description = "\n".join(desc_parts)

            # 构建初始时间线
            timeline = [
                {
                    "time": datetime.now().isoformat(),
                    "event": "规则命中，事件创建",
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "context": {
                        k: v
                        for k, v in context.items()
                        if k != "raw_output"
                    },
                }
            ]

            incident = Incident(
                title=title,
                description=description,
                alert_rule_id=rule.id,
                severity=rule.severity,
                status="open",
                source="scheduled",
                server_host=context.get("server_host"),
                service_name=context.get("service_name"),
                timeline=json.dumps(timeline, ensure_ascii=False),
                created_by="rule_engine",
            )
            db.add(incident)
            db.commit()
            db.refresh(incident)
            logger.warning(
                f"已创建事件 id={incident.id}，来源规则 [{rule.name}]"
            )

            # 发送通知
            self._notify(incident, context)

            # 自动触发事件调查
            if self.auto_investigate:
                self._trigger_investigation(incident)

            return incident
        except Exception as e:
            db.rollback()
            logger.error(f"创建事件失败 [{rule.name}]: {e}")
            return None
        finally:
            if own_session:
                db.close()

    # ------------------------------------------------------------------
    # 发送通知
    # ------------------------------------------------------------------
    def _notify(self, incident: Incident, context: Dict[str, Any]) -> None:
        """通过通知工具发送告警通知。

        Args:
            incident: 事件对象
            context: 评估上下文
        """
        notify_tool = ToolRegistry.get("send_notification")
        if not notify_tool:
            logger.warning("通知工具未注册，跳过通知发送")
            return

        # 根据严重级别映射通知等级
        level_map = {
            "critical": "error",
            "warning": "warning",
            "info": "info",
        }
        level = level_map.get(incident.severity, "info")

        content = (
            f"事件 ID: {incident.id}\n"
            f"标题: {incident.title}\n"
            f"严重级别: {incident.severity}\n"
            f"服务器: {incident.server_host or '未知'}\n"
            f"描述: {incident.description or '无'}\n"
            f"创建时间: {incident.created_at}"
        )

        try:
            notify_tool.execute(
                title=incident.title,
                content=content,
                level=level,
                channel="all",
            )
            logger.info(f"告警通知已发送，事件 id={incident.id}")
        except Exception as e:
            logger.error(f"发送告警通知失败: {e}")

    # ------------------------------------------------------------------
    # 自动触发事件调查
    # ------------------------------------------------------------------
    def _trigger_investigation(self, incident: Incident) -> None:
        """触发 IncidentInvestigator 对事件进行自动根因分析。

        Args:
            incident: 事件对象
        """
        try:
            from .webhook_receiver import WebhookReceiver

            receiver = WebhookReceiver(self.config)
            receiver.trigger_investigation(incident)
        except Exception as e:
            logger.error(f"触发事件调查失败: {e}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _get_first_server_host(self) -> str:
        """获取配置中的第一台服务器地址，用于指标采集。"""
        if not self.config or not hasattr(self.config, "servers"):
            return "localhost"
        servers = getattr(self.config, "servers", [])
        for s in servers:
            host = getattr(s, "host", None)
            if host:
                return host
        return "localhost"

    def _extract_metric_value(
        self,
        data: Any,
        metric_name: str,
        metric_type: str,
    ) -> Optional[float]:
        """从 system_metrics 工具的返回数据中提取数值。

        system_metrics 返回的 data 可能是字符串或字典，
        本方法尝试从中提取百分比数值。

        Args:
            data: 工具返回的原始数据
            metric_name: 指标名称
            metric_type: 指标类型

        Returns:
            提取到的数值，失败返回 None
        """
        if data is None:
            return None

        # 将不同形态的数据统一为文本
        if isinstance(data, dict):
            # metric_type == "all" 时返回的是各类型文本的字典
            target = data.get(metric_type) or data.get(metric_name)
            if target is not None:
                text = str(target)
            else:
                # 退化为全部内容
                text = json.dumps(data, ensure_ascii=False)
        elif isinstance(data, str):
            text = data
        else:
            text = str(data)

        # 尝试从 stdout 中提取数值
        if isinstance(data, dict) and isinstance(
            data.get("stdout"), str
        ):
            text = data["stdout"]

        # 提取百分比数值
        matches = _PERCENT_RE.findall(text)
        if not matches:
            return None

        # 优先匹配包含指标关键词的行
        keyword = metric_name.lower()
        for line in text.splitlines():
            if keyword in line.lower():
                line_match = _PERCENT_RE.search(line)
                if line_match:
                    try:
                        return float(line_match.group(1))
                    except ValueError:
                        continue

        # 退化为第一个数值
        try:
            return float(matches[0])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _compare(
        current: float, operator: str, threshold: float
    ) -> bool:
        """根据运算符比较当前值与阈值。

        Args:
            current: 当前值
            operator: 运算符 > / < / >= / <=
            threshold: 阈值

        Returns:
            是否满足比较条件
        """
        if operator == ">":
            return current > threshold
        if operator == "<":
            return current < threshold
        if operator == ">=":
            return current >= threshold
        if operator == "<=":
            return current <= threshold
        logger.warning(f"未知的运算符: {operator}，默认使用 >")
        return current > threshold

    @staticmethod
    def _summarize_output(data: Any, max_len: int = 1000) -> str:
        """将工具输出数据汇总为字符串，限制最大长度。

        Args:
            data: 原始数据
            max_len: 最大字符数

        Returns:
            汇总后的字符串
        """
        if data is None:
            return ""
        if isinstance(data, dict):
            stdout = data.get("stdout")
            if isinstance(stdout, str):
                return stdout[:max_len]
            return json.dumps(data, ensure_ascii=False)[:max_len]
        return str(data)[:max_len]
