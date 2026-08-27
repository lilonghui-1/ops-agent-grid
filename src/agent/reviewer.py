"""审批 Agent - 高风险操作评估与审批工作流

本 Agent 负责评估高风险运维操作，创建审批请求，并在获得审批后才允许执行。
工作流程：
1. 接收待评估的操作摘要
2. 分析操作的风险等级与影响范围
3. 生成结构化审批请求
4. 等待审批（通过/拒绝/修改）
5. 输出最终审批结论
"""

import json
from typing import Dict, Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from ..models.llm_factory import LLMFactory
from ..models.prompts import REVIEWER_SYSTEM_PROMPT
from ..tools.base import ToolRegistry
from ..utils.logger import get_logger

logger = get_logger("reviewer", agent_name="reviewer")


class Reviewer:
    """审批 Agent - 高风险操作评估与审批管理"""

    # 操作风险等级映射
    RISK_LEVELS = {
        "low": "低风险（无需审批，记录即可）",
        "medium": "中风险（需通知确认）",
        "high": "高风险（需人工审批后执行）",
        "critical": "严重风险（需多人审批，建议停机窗口）",
    }

    # 默认高风险操作关键词
    HIGH_RISK_KEYWORDS = [
        "重启", "restart", "reboot",
        "删除", "delete", "remove", "rm",
        "扩容", "scale", "resize",
        "terminate", "shutdown", "halt",
        "truncate", "drop", "kill",
    ]

    def __init__(self, config):
        self.config = config
        self.llm = LLMFactory.create_for_agent(config, "reviewer")

        # 审批 Agent 需要通知工具来发送审批请求
        tool_names = ["send_notification", "ssh_execute"]
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

        # 增强的系统 Prompt，注入风险等级说明
        risk_text = "\n".join(
            f"- {level}: {desc}" for level, desc in self.RISK_LEVELS.items()
        )
        enhanced_prompt = REVIEWER_SYSTEM_PROMPT + f"""

## 风险等级定义
{risk_text}

## 高风险操作关键词（出现即需审批）
{", ".join(self.HIGH_RISK_KEYWORDS)}

## 审批请求格式
请按以下 JSON 格式输出审批请求：
```json
{{
  "summary": "操作摘要",
  "risk_level": "low|medium|high|critical",
  "impact": "影响范围",
  "rollback_plan": "回滚方案",
  "approval_required": true/false,
  "approvers": ["建议审批人"],
  "decision": "pending|approved|rejected",
  "conditions": ["执行前提条件"],
  "notes": "补充说明"
}}
```
"""
        self._agent = create_react_agent(
            model=self.llm,
            tools=langchain_tools,
            prompt=enhanced_prompt,
        )
        return self._agent

    async def run(self, task: str, **kwargs) -> Dict[str, Any]:
        """执行审批评估任务

        Args:
            task: 待评估的操作描述
            **kwargs: operation_summary 操作摘要文本、auto_approve 是否自动审批

        Returns:
            包含审批结论的字典
        """
        operation_summary = kwargs.get("operation_summary", "")
        auto_approve = kwargs.get("auto_approve", False)

        logger.info(f"审批 Agent 开始评估操作: {task}")

        # 预评估风险等级
        risk_level = self._assess_risk(operation_summary or task)
        logger.info(f"预评估风险等级: {risk_level}")

        # 低风险直接放行
        if risk_level == "low":
            logger.info("低风险操作，直接放行")
            return {
                "agent": "reviewer",
                "task": task,
                "result": (
                    "低风险操作，无需审批，已自动放行。"
                    f"\n风险等级: {risk_level}\n操作摘要: {operation_summary}"
                ),
                "risk_level": risk_level,
                "decision": "approved",
                "approval_required": False,
            }

        # 需要审批的操作
        approval_request = await self._evaluate_and_create_request(
            task, operation_summary, risk_level
        )

        # 等待审批结果
        if approval_request.get("approval_required", True) and not auto_approve:
            decision = await self._wait_for_approval(approval_request)
            approval_request["decision"] = decision
        else:
            approval_request["decision"] = "approved"

        result_text = self._format_approval_result(approval_request)

        return {
            "agent": "reviewer",
            "task": task,
            "result": result_text,
            "risk_level": approval_request.get("risk_level", risk_level),
            "decision": approval_request.get("decision", "pending"),
            "approval_required": approval_request.get(
                "approval_required", True
            ),
        }

    def _assess_risk(self, operation_text: str) -> str:
        """根据关键词快速评估风险等级"""
        if not operation_text:
            return "low"
        text_lower = operation_text.lower()
        # 严重风险关键词
        critical_keywords = ["drop", "truncate", "shutdown", "halt", "rm -rf"]
        if any(kw in text_lower for kw in critical_keywords):
            return "critical"
        # 高风险关键词
        if any(kw in text_lower for kw in self.HIGH_RISK_KEYWORDS):
            return "high"
        # 中风险关键词
        medium_keywords = [
            "config", "配置", "update", "modify", "change",
            "stop", "停止",
        ]
        if any(kw in text_lower for kw in medium_keywords):
            return "medium"
        return "low"

    async def _evaluate_and_create_request(
        self, task: str, operation_summary: str, risk_level: str
    ) -> dict:
        """调用 LLM 生成结构化审批请求"""
        agent = self._get_agent()
        try:
            full_task = (
                f"## 待评估操作\n{task}\n\n"
                f"## 操作摘要\n{operation_summary}\n\n"
                f"## 预评估风险等级\n{risk_level}\n\n"
                f"请生成结构化审批请求。"
            )
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=full_task)]}
            )
            final_message = result["messages"][-1].content

            # 尝试解析 JSON
            parsed = self._try_parse_json(final_message)
            if parsed:
                # 确保风险等级与预评估一致
                parsed.setdefault("risk_level", risk_level)
                parsed.setdefault("decision", "pending")
                parsed["raw_response"] = final_message
                return parsed

            return {
                "summary": task,
                "risk_level": risk_level,
                "impact": "未知",
                "rollback_plan": "未知",
                "approval_required": risk_level in ("high", "critical"),
                "approvers": [],
                "decision": "pending",
                "conditions": [],
                "notes": final_message,
                "raw_response": final_message,
            }
        except Exception as e:
            logger.error(f"生成审批请求失败: {e}")
            return {
                "summary": task,
                "risk_level": risk_level,
                "impact": "评估失败",
                "rollback_plan": "未知",
                "approval_required": risk_level in ("high", "critical"),
                "approvers": [],
                "decision": "pending",
                "conditions": [],
                "notes": f"审批评估失败: {e}",
                "raw_response": "",
            }

    async def _wait_for_approval(self, approval_request: dict) -> str:
        """等待审批结果

        当前实现为同步等待占位：发送审批通知后返回 pending。
        实际生产中应对接审批系统或人工确认回调。
        """
        notify_tool = ToolRegistry.get("send_notification")
        if not notify_tool:
            logger.warning("未找到通知工具，无法发送审批请求")
            return "pending"

        try:
            risk_level = approval_request.get("risk_level", "high")
            summary = approval_request.get("summary", "")
            impact = approval_request.get("impact", "")
            rollback = approval_request.get("rollback_plan", "")

            content = (
                f"## 操作摘要\n{summary}\n\n"
                f"## 风险等级\n{risk_level}\n\n"
                f"## 影响范围\n{impact}\n\n"
                f"## 回滚方案\n{rollback}\n\n"
                "请审批确认后再执行。"
            )
            await notify_tool.execute(
                title=f"运维审批请求 - [{risk_level}]",
                content=content,
                level="warning" if risk_level != "critical" else "critical",
                channel="all",
            )
            logger.info("审批请求通知已发送，等待审批")
        except Exception as e:
            logger.error(f"发送审批请求失败: {e}")

        # 占位：返回 pending，实际由外部审批系统回调更新
        return "pending"

    def _try_parse_json(self, text: str) -> Optional[dict]:
        """尝试从文本中解析 JSON"""
        # 去除 Markdown 代码块
        candidate = text.strip()
        if candidate.startswith("```"):
            parts = candidate.split("```")
            if len(parts) >= 2:
                candidate = parts[1].strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].strip()
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass

        # 尝试提取第一个 JSON 对象
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    def _format_approval_result(self, approval: dict) -> str:
        """格式化审批结果为可读文本"""
        parts = [
            "# 审批结果\n",
            f"## 操作摘要\n{approval.get('summary', '未知')}\n",
            f"## 风险等级\n{approval.get('risk_level', '未知')}\n",
            f"## 影响范围\n{approval.get('impact', '未知')}\n",
            f"## 回滚方案\n{approval.get('rollback_plan', '未知')}\n",
            f"## 是否需要审批\n"
            f"{'是' if approval.get('approval_required') else '否'}\n",
            f"## 审批决定\n{approval.get('decision', 'pending')}\n",
        ]
        approvers = approval.get("approvers", [])
        if approvers:
            parts.append(f"## 建议审批人\n{', '.join(approvers)}\n")
        conditions = approval.get("conditions", [])
        if conditions:
            parts.append("## 执行前提条件")
            for i, c in enumerate(conditions, 1):
                parts.append(f"{i}. {c}")
            parts.append("")
        notes = approval.get("notes", "")
        if notes:
            parts.append(f"## 补充说明\n{notes}\n")
        return "\n".join(parts)

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
