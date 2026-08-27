"""工具基类与注册中心 - 借鉴 MCP 协议思想，定义标准化工具接口"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str
    type: str = "string"  # string / integer / number / boolean / array / object
    description: str
    required: bool = True
    default: Any = None


class ToolResult(BaseModel):
    """工具执行结果 - 标准化返回格式"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseTool(ABC):
    """所有运维工具的抽象基类

    每个工具必须：
    1. 定义 name、description、parameters 类属性
    2. 实现 execute(**kwargs) -> ToolResult 方法
    """

    name: str = ""
    description: str = ""
    parameters: List[ToolParameter] = []
    requires_approval: bool = False  # 标记该工具执行前是否需要人工审批

    def execute_with_logging(self, **kwargs) -> ToolResult:
        """带日志记录和耗时统计的执行包装"""
        start_time = time.time()
        logger.info(f"Tool [{self.name}] executing with args: {list(kwargs.keys())}")
        try:
            result = self.execute(**kwargs)
            elapsed = round(time.time() - start_time, 3)
            logger.info(f"Tool [{self.name}] completed in {elapsed}s, success={result.success}")
            result.metadata['elapsed_seconds'] = elapsed
            result.metadata['tool_name'] = self.name
            return result
        except Exception as e:
            elapsed = round(time.time() - start_time, 3)
            logger.error(f"Tool [{self.name}] failed after {elapsed}s: {e}")
            return ToolResult(
                success=False,
                error=f"{type(e).__name__}: {str(e)}",
                metadata={'elapsed_seconds': elapsed, 'tool_name': self.name}
            )

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """执行工具操作，返回标准化结果"""
        ...

    def get_schema(self) -> dict:
        """返回工具的 JSON Schema"""
        properties = {}
        required = []
        for p in self.parameters:
            properties[p.name] = {
                "type": p.type,
                "description": p.description
            }
            if p.required:
                required.append(p.name)
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }


class ToolRegistry:
    """工具注册中心 - 全局单例管理所有工具"""

    _tools: Dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool: BaseTool):
        """注册工具"""
        cls._tools[tool.name] = tool
        logger.info(f"Tool registered: {tool.name}")

    @classmethod
    def get(cls, name: str) -> Optional[BaseTool]:
        """按名称获取工具"""
        return cls._tools.get(name)

    @classmethod
    def get_all(cls) -> Dict[str, BaseTool]:
        """获取所有已注册工具"""
        return cls._tools.copy()

    @classmethod
    def get_names(cls) -> List[str]:
        """获取所有已注册工具名称"""
        return list(cls._tools.keys())

    @classmethod
    def clear(cls):
        """清空注册表（用于测试）"""
        cls._tools.clear()

    @classmethod
    def _convert_single_tool(cls, tool: 'BaseTool'):
        """将单个 BaseTool 转换为 LangChain StructuredTool"""
        try:
            from langchain_core.tools import StructuredTool
        except ImportError:
            return None

        try:
            fields = {}
            for p in tool.parameters:
                field_type = cls._map_type(p.type)
                if not p.required:
                    fields[p.name] = (
                        Optional[field_type],
                        Field(default=p.default, description=p.description)
                    )
                else:
                    fields[p.name] = (field_type, Field(description=p.description))

            InputModel = type(f"{tool.name}Input", (BaseModel,), fields)

            lc_tool = StructuredTool.from_function(
                func=lambda **kw: tool.execute_with_logging(**kw).model_dump(),
                name=tool.name,
                description=tool.description,
                args_schema=InputModel,
            )
            return lc_tool
        except Exception as e:
            logger.error(f"转换工具 [{tool.name}] 失败: {e}")
            return None

    @classmethod
    def to_langchain_tools(cls) -> list:
        """将注册的工具转换为 LangChain StructuredTool 列表"""
        tools = []
        for name, tool in cls._tools.items():
            lc_tool = cls._convert_single_tool(tool)
            if lc_tool:
                tools.append(lc_tool)
        return tools

    @staticmethod
    def _map_type(type_str: str) -> Type:
        """将字符串类型映射为 Python 类型"""
        mapping = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        return mapping.get(type_str, str)
