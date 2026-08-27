"""Master Agent - 多 Agent 协作调度中枢

兼容性说明：
    本模块是 Agent 架构升级前的旧版调度器，保留用于向后兼容。
    新代码应使用 coordinator.py 中的 Coordinator 类，它基于本类的编排思路
    扩展了专家路由、告警触发调查与高风险操作审批能力。
    MasterAgent 将继续可用，但建议逐步迁移到 Coordinator。
"""

import logging
from typing import Dict, Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..models.llm_factory import LLMFactory
from ..models.prompts import MASTER_SYSTEM_PROMPT
from ..tools.base import ToolRegistry
from ..utils.logger import get_logger

logger = get_logger("master_agent", agent_name="master")


class MasterAgent:
    """Master Agent - 负责任务理解、分解、调度和结果汇总

    工作流程：
    1. 分析用户任务，判断任务类型
    2. 路由到对应的专业 Agent 执行
    3. 根据执行结果决定是否需要进一步操作（如巡检异常→诊断→自愈）
    4. 汇总所有结果生成最终报告

    注意：本类为兼容性封装，新架构请使用 Coordinator。
    """

    def __init__(self, config):
        self.config = config
        self.llm = LLMFactory.create_for_agent(config, "master")

        # 延迟导入避免循环依赖
        from .inspect_agent import InspectAgent
        from .diagnose_agent import DiagnoseAgent
        from .log_agent import LogAgent
        from .heal_agent import HealAgent

        self.inspect_agent = InspectAgent(config)
        self.diagnose_agent = DiagnoseAgent(config)
        self.log_agent = LogAgent(config)
        self.heal_agent = HealAgent(config)

    async def run(self, task: str) -> Dict[str, Any]:
        """执行完整的多 Agent 协作流程

        Args:
            task: 用户任务描述

        Returns:
            包含完整执行结果的字典
        """
        logger.info(f"Master Agent 开始处理任务: {task}")

        result = {
            "input": task,
            "task_type": None,
            "inspection_result": None,
            "diagnosis_result": None,
            "log_analysis_result": None,
            "heal_result": None,
            "final_report": None,
        }

        try:
            # 1. 分析任务类型
            task_type = await self._analyze_task(task)
            result["task_type"] = task_type
            logger.info(f"任务类型分析结果: {task_type}")

            # 2. 根据任务类型执行
            if task_type == "inspect":
                await self._run_inspection(task, result)
                # 巡检后检查是否需要诊断
                if self._need_diagnosis(result.get("inspection_result", "")):
                    await self._run_diagnosis(task, result, context=result["inspection_result"])
                    # 诊断后检查是否需要自愈
                    if self._need_heal(result.get("diagnosis_result", "")):
                        await self._run_heal(task, result)

            elif task_type == "diagnose":
                await self._run_diagnosis(task, result)
                if self._need_heal(result.get("diagnosis_result", "")):
                    await self._run_heal(task, result)

            elif task_type == "log":
                await self._run_log_analysis(task, result)
                if self._need_diagnosis(result.get("log_analysis_result", "")):
                    await self._run_diagnosis(task, result, context=result["log_analysis_result"])
                    if self._need_heal(result.get("diagnosis_result", "")):
                        await self._run_heal(task, result)

            elif task_type == "heal":
                await self._run_heal(task, result)

            elif task_type == "composite":
                # 复合任务：巡检 → 诊断 → 自愈
                await self._run_inspection(task, result)
                if self._need_diagnosis(result.get("inspection_result", "")):
                    await self._run_diagnosis(task, result, context=result["inspection_result"])
                    if self._need_heal(result.get("diagnosis_result", "")):
                        await self._run_heal(task, result)

            # 3. 生成最终报告
            result["final_report"] = self._generate_report(result)

            # 4. 发送通知（如果有异常）
            if self._has_critical_issue(result):
                await self._send_alert(result)

        except Exception as e:
            logger.error(f"Master Agent 执行失败: {e}")
            result["final_report"] = f"任务执行失败: {type(e).__name__}: {e}"
            result["error"] = str(e)

        return result

    async def _analyze_task(self, task: str) -> str:
        """分析任务类型"""
        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=f"""分析以下运维任务，判断任务类型。

任务类型说明：
- inspect: 服务器/数据库巡检
- diagnose: 故障诊断
- log: 日志分析
- heal: 自愈处理
- composite: 复合任务（需要多个 Agent 协作）

任务: {task}

只返回任务类型关键词（inspect/diagnose/log/heal/composite），不要其他内容。"""),
            ])
            task_type = response.content.strip().lower()
            # 校验返回值
            valid_types = {"inspect", "diagnose", "log", "heal", "composite"}
            if task_type in valid_types:
                return task_type
            # 模糊匹配
            for vt in valid_types:
                if vt in task_type:
                    return vt
            return "inspect"  # 默认巡检
        except Exception as e:
            logger.warning(f"任务类型分析失败，默认使用巡检: {e}")
            return "inspect"

    async def _run_inspection(self, task: str, result: dict):
        """执行巡检"""
        logger.info("执行巡检...")
        inspect_result = await self.inspect_agent.run(task)
        result["inspection_result"] = inspect_result.get("result", "巡检完成")

    async def _run_diagnosis(self, task: str, result: dict, context: str = None):
        """执行诊断"""
        logger.info("执行诊断...")
        diagnose_result = await self.diagnose_agent.run(task, context=context)
        result["diagnosis_result"] = diagnose_result.get("result", "诊断完成")

    async def _run_log_analysis(self, task: str, result: dict):
        """执行日志分析"""
        logger.info("执行日志分析...")
        log_result = await self.log_agent.run(task)
        result["log_analysis_result"] = log_result.get("result", "日志分析完成")

    async def _run_heal(self, task: str, result: dict):
        """执行自愈"""
        logger.info("执行自愈处理...")
        heal_result = await self.heal_agent.run(
            task,
            diagnosis_result=result.get("diagnosis_result")
        )
        result["heal_result"] = heal_result.get("result", "自愈处理完成")

    def _need_diagnosis(self, inspect_result: str) -> bool:
        """判断巡检结果是否需要进一步诊断"""
        if not inspect_result:
            return False
        critical_keywords = ['异常', '告警', '警告', 'error', 'critical', 'high', '失败', '故障']
        return any(kw in inspect_result.lower() for kw in critical_keywords)

    def _need_heal(self, diagnosis_result: str) -> bool:
        """判断诊断结果是否需要自愈"""
        if not diagnosis_result:
            return False
        heal_keywords = ['need_heal', 'true', '建议自愈', '建议重启', '建议清理', '建议修复']
        return any(kw in diagnosis_result.lower() for kw in heal_keywords)

    def _has_critical_issue(self, result: dict) -> bool:
        """判断结果中是否有严重问题需要告警"""
        for key in ['inspection_result', 'diagnosis_result', 'log_analysis_result']:
            content = result.get(key, '')
            if content and 'critical' in content.lower():
                return True
        return False

    def _generate_report(self, result: dict) -> str:
        """汇总所有结果生成最终报告"""
        parts = [f"# 运维任务报告\n\n## 原始任务\n{result['input']}\n"]
        parts.append(f"## 任务类型\n{result.get('task_type', '未知')}\n")

        if result.get('inspection_result'):
            parts.append(f"## 巡检结果\n{result['inspection_result']}\n")
        if result.get('diagnosis_result'):
            parts.append(f"## 诊断结果\n{result['diagnosis_result']}\n")
        if result.get('log_analysis_result'):
            parts.append(f"## 日志分析结果\n{result['log_analysis_result']}\n")
        if result.get('heal_result'):
            parts.append(f"## 自愈处理结果\n{result['heal_result']}\n")

        report = "\n".join(parts)
        logger.info("最终报告已生成")
        return report

    async def _send_alert(self, result: dict):
        """发送告警通知"""
        notify_tool = ToolRegistry.get("send_notification")
        if not notify_tool:
            return

        try:
            report = result.get('final_report', '')
            # 截断过长的报告
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

    # 兼容性提示：本类已由 Coordinator 替代，详见 coordinator.py
    # 新架构应使用 Coordinator 进行任务编排与专家路由
    _replaced_by = "Coordinator"
