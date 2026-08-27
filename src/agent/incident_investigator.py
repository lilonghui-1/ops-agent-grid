"""事件调查 Agent - 告警自动调查与根因分析（RCA）

本 Agent 接收告警数据作为输入，自动收集证据并产出根因分析报告。
工作流程：
1. 解析告警数据（服务器、类型、严重级别、阈值、当前值等）
2. 使用工具收集证据（系统指标、日志、数据库状态）
3. 分析证据，进行根因分析（RCA）
4. 将调查发现写回结果
"""

import json
from typing import Dict, Any, Optional

from langchain_core.messages import HumanMessage

from ..models.llm_factory import LLMFactory
from ..models.prompts import INVESTIGATOR_SYSTEM_PROMPT
from ..tools.base import ToolRegistry
from ..knowledge.knowledge_base import KnowledgeBase
from ..utils.logger import get_logger

logger = get_logger("incident_investigator", agent_name="investigator")


class IncidentInvestigator:
    """事件调查 Agent - 自动调查告警并产出 RCA 报告"""

    def __init__(self, config):
        self.config = config
        self.llm = LLMFactory.create_for_agent(config, "investigator")
        self.knowledge_base = KnowledgeBase()

        # 事件调查需要全面的信息收集工具
        tool_names = [
            "system_metrics", "db_status", "redis_info",
            "log_fetch", "log_analyze", "ssh_execute",
        ]
        self.tools = [ToolRegistry.get(name) for name in tool_names]
        self.tools = [t for t in self.tools if t is not None]

        self._agent = None

    def _get_agent(self):
        """延迟初始化 Agent"""
        if self._agent is not None:
            return self._agent

        try:
            from langgraph.prebuilt import create_react_agent
        except ImportError:
            raise ImportError("langgraph 未安装，请执行: pip install langgraph")

        langchain_tools = []
        for tool in self.tools:
            lc_tool = ToolRegistry._convert_single_tool(tool)
            if lc_tool:
                langchain_tools.append(lc_tool)

        # 增强的系统 Prompt，包含知识库辅助与 RCA 报告格式
        enhanced_prompt = INVESTIGATOR_SYSTEM_PROMPT + """

## 知识库辅助
在调查时，参考运维知识库中的历史经验。发现异常症状后，先思考可能原因，再用工具验证。

## RCA 报告格式
请按以下 JSON 格式输出根因分析结果：
```json
{
  "incident_id": "事件标识（如告警 ID）",
  "summary": "一句话总结事件",
  "alert_source": "告警来源描述",
  "evidence": ["证据1", "证据2"],
  "root_cause": "根因分析",
  "contributing_factors": ["促成因素1"],
  "impact": "影响范围",
  "severity": "critical|high|medium|low",
  "timeline": ["时间线事件1"],
  "recommendations": ["处置建议1", "处置建议2"],
  "follow_up": ["后续改进项1"]
}
```
"""
        self._agent = create_react_agent(
            model=self.llm,
            tools=langchain_tools,
            prompt=enhanced_prompt,
        )
        return self._agent

    async def run(self, task: str, **kwargs) -> Dict[str, Any]:
        """执行事件调查任务

        Args:
            task: 调查任务描述（如告警标题或概述）
            **kwargs: alert_data 告警数据字典，包含 server_host、alert_type、
                      severity、message、threshold、current_value 等字段

        Returns:
            包含 RCA 调查结果的字典
        """
        alert_data = kwargs.get("alert_data") or {}
        logger.info(f"事件调查开始，告警数据: {alert_data}")

        agent = self._get_agent()

        try:
            # 构建告警上下文
            alert_context = self._format_alert(alert_data)

            # 查询知识库获取相关知识
            symptoms = (
                alert_data.get("alert_type", "")
                + " "
                + alert_data.get("message", "")
            )
            kb_context = self.knowledge_base.get_context_for_agent(symptoms)
            if kb_context and "未找到" not in kb_context:
                alert_context += f"\n\n## 参考知识\n{kb_context}"

            # 构建完整任务描述
            full_task = (
                f"## 调查任务\n{task}\n\n## 告警数据\n{alert_context}\n\n"
                f"请基于上述告警信息收集证据并完成根因分析。"
            )

            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=full_task)]}
            )
            final_message = result["messages"][-1].content
            tool_calls = self._extract_tool_calls(result["messages"])

            logger.info(
                f"事件调查完成，工具调用次数: {len(tool_calls)}"
            )
            return {
                "agent": "investigator",
                "task": task,
                "result": final_message,
                "alert_data": alert_data,
                "tool_calls": tool_calls,
            }
        except Exception as e:
            logger.error(f"事件调查失败: {e}")
            return {
                "agent": "investigator",
                "task": task,
                "result": f"事件调查失败: {type(e).__name__}: {e}",
                "alert_data": alert_data,
                "tool_calls": [],
                "error": str(e),
            }

    def _format_alert(self, alert_data: dict) -> str:
        """将告警数据格式化为可读文本"""
        if not alert_data:
            return "无告警数据"
        parts = []
        key_map = {
            "id": "告警ID",
            "server_host": "服务器地址",
            "alert_type": "告警类型",
            "severity": "严重级别",
            "message": "告警消息",
            "threshold": "阈值",
            "current_value": "当前值",
            "created_at": "触发时间",
        }
        for key, label in key_map.items():
            val = alert_data.get(key)
            if val is not None:
                parts.append(f"- {label}: {val}")
        # 补充其他未映射的字段
        for key, val in alert_data.items():
            if key not in key_map and val is not None:
                parts.append(f"- {key}: {val}")
        return "\n".join(parts) if parts else "无告警数据"

    def _extract_tool_calls(self, messages) -> list:
        """提取工具调用记录"""
        calls = []
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    calls.append({
                        "tool": tc.get("name", "unknown"),
                        "args": tc.get("args", {}),
                    })
        return calls
