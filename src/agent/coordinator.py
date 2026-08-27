"""Coordinator Agent - 多 Agent 协作调度中枢（替代 MasterAgent）

Coordinator 是 Agent 架构升级后的核心调度器，它继承自 MasterAgent 的编排思路，
并扩展了以下能力：
1. 任务分解与专家路由（SRE / Network / DB / Compute 四类专家）
2. 告警触发的事件自动调查（联动 IncidentInvestigator）
3. 高风险操作的审批工作流（联动 Reviewer）
4. 最终报告汇总与通知下发
"""

from typing import Dict, Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from ..models.llm_factory import LLMFactory
from ..models.prompts import COORDINATOR_SYSTEM_PROMPT
from ..tools.base import ToolRegistry
from ..utils.logger import get_logger

logger = get_logger("coordinator", agent_name="coordinator")


class Coordinator:
    """Coordinator Agent - 负责任务理解、分解、专家路由、审批与结果汇总

    工作流程：
    1. 分析用户任务或告警，判断需要哪些专家参与
    2. 将任务分解为子任务并路由到对应专家 Agent
    3. 对于高风险操作，提交给 Reviewer 走审批流程
    4. 告警触发时，调用 IncidentInvestigator 自动调查
    5. 汇总各专家结果，生成最终报告并按需通知
    """

    def __init__(self, config):
        self.config = config
        self.llm = LLMFactory.create_for_agent(config, "coordinator")

        # 延迟导入避免循环依赖
        from .specialist_sre import SpecialistSRE
        from .specialist_network import SpecialistNetwork
        from .specialist_db import SpecialistDB
        from .specialist_compute import SpecialistCompute
        from .incident_investigator import IncidentInvestigator
        from .reviewer import Reviewer

        # 专家 Agent 注册表
        self.specialists = {
            "sre": SpecialistSRE(config),
            "network": SpecialistNetwork(config),
            "db": SpecialistDB(config),
            "compute": SpecialistCompute(config),
        }

        # 事件调查与审批 Agent
        self.investigator = IncidentInvestigator(config)
        self.reviewer = Reviewer(config)

        # 任务类型到专家的映射
        self._type_to_specialist = {
            "inspect": "sre",
            "sre": "sre",
            "network": "network",
            "db": "db",
            "database": "db",
            "compute": "compute",
            "k8s": "compute",
            "container": "compute",
        }

    async def run(self, task: str, **kwargs) -> Dict[str, Any]:
        """执行完整的多 Agent 协作流程

        Args:
            task: 用户任务描述
            **kwargs: 扩展参数，支持 alert_data（告警数据）、auto_approve（是否自动审批）

        Returns:
            包含完整执行结果的字典
        """
        logger.info(f"Coordinator 开始处理任务: {task}")

        result: Dict[str, Any] = {
            "input": task,
            "task_type": None,
            "sub_tasks": [],
            "specialist_results": {},
            "investigation_result": None,
            "approval_result": None,
            "final_report": None,
        }

        try:
            # 1. 告警触发模式：直接进入事件调查
            alert_data = kwargs.get("alert_data")
            if alert_data:
                await self._run_investigation(task, result, alert_data)
                result["final_report"] = self._generate_report(result)
                return result

            # 2. 分析任务并分解子任务
            task_plan = await self._decompose_task(task)
            result["task_type"] = task_plan.get("task_type", "composite")
            result["sub_tasks"] = task_plan.get("sub_tasks", [])
            logger.info(
                f"任务分解完成，类型={result['task_type']}，"
                f"子任务数={len(result['sub_tasks'])}"
            )

            # 3. 依次调度专家执行子任务
            for sub_task in result["sub_tasks"]:
                specialist_name = sub_task.get("specialist")
                sub_desc = sub_task.get("description", "")
                if specialist_name in self.specialists:
                    await self._dispatch_specialist(
                        specialist_name, sub_desc, result
                    )

            # 4. 如果发现高风险操作，提交审批
            if self._need_approval(result):
                await self._request_approval(task, result)

            # 5. 生成最终报告
            result["final_report"] = self._generate_report(result)

            # 6. 发现严重问题时发送告警通知
            if self._has_critical_issue(result):
                await self._send_alert(result)

        except Exception as e:
            logger.error(f"Coordinator 执行失败: {e}")
            result["final_report"] = f"任务执行失败: {type(e).__name__}: {e}"
            result["error"] = str(e)

        return result

    async def _decompose_task(self, task: str) -> Dict[str, Any]:
        """分解任务，返回任务类型和子任务列表"""
        try:
            available = ", ".join(self.specialists.keys())
            response = await self.llm.ainvoke([
                SystemMessage(content=f"""分析以下运维任务，分解为子任务并指定执行的专家。

可用专家：{available}
- sre: 服务器巡检、资源分析、容量规划
- network: 网络连通性、SNMP 设备发现、接口轮询
- db: 数据库性能、慢查询、连接池分析
- compute: K8s 工作负载、容器状态

任务类型关键词：inspect(巡检), network(网络), db(数据库), compute(计算), composite(复合)

请按以下 JSON 格式输出：
{{"task_type": "类型", "sub_tasks": [{{"specialist": "专家名", "description": "子任务描述"}}]}}

任务: {task}

只返回 JSON，不要其他内容。"""),
            ])
            import json
            content = response.content.strip()
            # 去除可能的 Markdown 代码块标记
            if content.startswith("```"):
                content = content.split("```", 2)[-1].strip()
                if content.startswith("json"):
                    content = content[4:].strip()
            plan = json.loads(content)
            return plan
        except Exception as e:
            logger.warning(f"任务分解失败，默认使用 sre 巡检: {e}")
            return {
                "task_type": "inspect",
                "sub_tasks": [{"specialist": "sre", "description": task}],
            }

    async def _dispatch_specialist(
        self, name: str, sub_task: str, result: dict
    ):
        """调度单个专家 Agent 执行子任务"""
        logger.info(f"调度专家 [{name}] 执行子任务: {sub_task}")
        specialist = self.specialists[name]
        try:
            spec_result = await specialist.run(sub_task)
            result["specialist_results"][name] = spec_result.get("result", "")
        except Exception as e:
            logger.error(f"专家 [{name}] 执行失败: {e}")
            result["specialist_results"][name] = f"执行失败: {e}"

    async def _run_investigation(
        self, task: str, result: dict, alert_data: dict
    ):
        """告警触发的事件调查"""
        logger.info("检测到告警数据，启动事件自动调查")
        try:
            inv_result = await self.investigator.run(task, alert_data=alert_data)
            result["investigation_result"] = inv_result.get("result", "调查完成")
        except Exception as e:
            logger.error(f"事件调查失败: {e}")
            result["investigation_result"] = f"调查失败: {e}"

    def _need_approval(self, result: dict) -> bool:
        """判断结果中是否包含需要审批的高风险操作"""
        for content in result.get("specialist_results", {}).values():
            if not content:
                continue
            risk_keywords = [
                "重启", "restart", "删除", "delete", "扩容", "scale",
                "高风险", "需审批", "approval", "terminate",
            ]
            if any(kw in content.lower() for kw in risk_keywords):
                return True
        return False

    async def _request_approval(self, task: str, result: dict):
        """提交高风险操作到审批流程"""
        logger.info("检测到高风险操作，提交审批")
        # 汇总需要审批的操作摘要
        operations = []
        for name, content in result.get("specialist_results", {}).items():
            if content:
                operations.append(f"[{name}] {content[:500]}")
        operation_summary = "\n".join(operations)

        try:
            appr_result = await self.reviewer.run(
                task, operation_summary=operation_summary
            )
            result["approval_result"] = appr_result.get("result", "审批完成")
        except Exception as e:
            logger.error(f"审批流程失败: {e}")
            result["approval_result"] = f"审批失败: {e}"

    def _has_critical_issue(self, result: dict) -> bool:
        """判断结果中是否有严重问题需要告警"""
        for content in result.get("specialist_results", {}).values():
            if content and "critical" in content.lower():
                return True
        inv = result.get("investigation_result", "")
        return bool(inv) and "critical" in inv.lower()

    def _generate_report(self, result: dict) -> str:
        """汇总所有结果生成最终报告"""
        parts: List[str] = [
            f"# 运维任务报告\n\n## 原始任务\n{result['input']}\n",
            f"## 任务类型\n{result.get('task_type', '未知')}\n",
        ]

        sub_tasks = result.get("sub_tasks", [])
        if sub_tasks:
            parts.append("## 子任务分解")
            for i, st in enumerate(sub_tasks, 1):
                parts.append(
                    f"{i}. [{st.get('specialist', '?')}] {st.get('description', '')}"
                )
            parts.append("")

        for name, content in result.get("specialist_results", {}).items():
            if content:
                parts.append(f"## {name} 专家结果\n{content}\n")

        if result.get("investigation_result"):
            parts.append(
                f"## 事件调查结果\n{result['investigation_result']}\n"
            )
        if result.get("approval_result"):
            parts.append(f"## 审批结果\n{result['approval_result']}\n")

        report = "\n".join(parts)
        logger.info("最终报告已生成")
        return report

    async def _send_alert(self, result: dict):
        """发送告警通知"""
        notify_tool = ToolRegistry.get("send_notification")
        if not notify_tool:
            return

        try:
            report = result.get("final_report", "")
            content = report[:2000] if len(report) > 2000 else report
            await notify_tool.execute(
                title="运维告警 - 发现严重问题",
                content=content,
                level="critical",
                channel="all",
            )
            logger.info("告警通知已发送")
        except Exception as e:
            logger.error(f"发送告警通知失败: {e}")
