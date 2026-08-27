"""LLM 工厂 - 可插拔的多 provider 模型后端管理

本模块负责根据配置创建不同 provider 的 LangChain ChatModel 实例，
并支持「模型热路由」：不同 Agent 类型可绑定不同的 provider 与模型，
未配置时回退到 config.llm 中的默认模型。

支持的 provider:
- openai: OpenAI GPT 系列
- qwen: 通义千问（通过 OpenAI 兼容 API）
- deepseek: DeepSeek（通过 OpenAI 兼容 API）
- anthropic: Anthropic Claude 系列（可选依赖 langchain-anthropic）
- gemini: Google Gemini 系列（可选依赖 langchain-google-genai）
- custom / 自定义: 任何兼容 OpenAI API 的服务（vLLM、Ollama 等）
"""

import logging
import os
import re
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class LLMFactory:
    """LLM 工厂类 - 根据配置创建不同的 LLM 实例

    核心能力：
    1. 多 provider 支持：openai / qwen / deepseek / anthropic / gemini / 自定义
    2. 模型热路由：按 Agent 类型选择不同 provider 与模型
    3. 优雅降级：可选 provider（anthropic / gemini）未安装时自动回退到默认配置
    """

    # OpenAI 兼容的 provider 名称集合，统一走 ChatOpenAI
    OPENAI_COMPATIBLE_PROVIDERS = {"openai", "qwen", "deepseek", "custom"}

    # ------------------------------------------------------------------
    # 模型热路由配置
    # ------------------------------------------------------------------
    # 不同 Agent 的默认模型配置覆盖。
    # 每个 entry 可包含以下字段（均为可选，缺省回退到 config.llm）：
    #   model:        模型名称，如 gpt-4 / qwen-plus / claude-3-sonnet / gemini-1.5-pro
    #   provider:     provider 名称，如 openai / qwen / anthropic / gemini
    #   temperature: 采样温度
    #   max_tokens:   最大输出 token 数
    #   api_key:      该 Agent 专用 API Key（支持 ${ENV_VAR} 占位符）
    #   base_url:     该 Agent 专用 base_url（支持 ${ENV_VAR} 占位符）
    #
    # 默认全部留空（{}），表示使用 config.llm 的默认配置，保证向后兼容。
    # 如需启用按 Agent 路由，可参考下方示例填入对应字段。
    #
    # 路由示例（按需取消注释 / 调整后生效）：
    #   'coordinator': {
    #       'model': 'gpt-4',                 # 协调器使用较强模型
    #       'provider': 'openai',
    #   },
    #   'specialist_sre': {
    #       'model': 'qwen-plus',             # SRE 专家使用较快的模型
    #       'provider': 'qwen',
    #       'base_url': '${QWEN_BASE_URL}',
    #       'api_key': '${QWEN_API_KEY}',
    #   },
    #   'incident_investigator': {
    #       'model': 'claude-3-sonnet',        # 事件调查使用 Claude
    #       'provider': 'anthropic',
    #       'api_key': '${ANTHROPIC_API_KEY}',
    #   },
    #   'reviewer': {
    #       'model': 'gpt-4',
    #       'provider': 'openai',
    #   },
    AGENT_MODEL_OVERRIDES = {
        # ===== 原有 Agent =====
        'master': {},                  # Master Agent 使用默认配置
        'inspect': {},                 # 巡检 Agent
        'diagnose': {},                # 诊断 Agent（建议使用较强模型）
        'log': {},                     # 日志 Agent
        'heal': {},                    # 自愈 Agent
        # ===== 多 Agent 协作架构新增角色 =====
        'coordinator': {},             # 协调器：全局编排与任务分解
        'specialist_sre': {},          # SRE 专家：巡检 / 资源分析
        'specialist_network': {},      # 网络专家：连通性 / SNMP 分析
        'specialist_db': {},           # 数据库专家：慢查询 / 连接池分析
        'specialist_compute': {},      # 计算专家：K8s / 容器状态分析
        'incident_investigator': {},   # 事件调查 Agent：根因分析
        'reviewer': {},                # 审批 Agent：高风险判断
    }

    # Agent 名称别名映射：将历史代码使用的短名称归一为上面的规范名称
    # 例如 specialist_sre.py / incident_investigator.py 中传入的短名
    AGENT_NAME_ALIASES = {
        'sre': 'specialist_sre',
        'network': 'specialist_network',
        'db': 'specialist_db',
        'compute': 'specialist_compute',
        'investigator': 'incident_investigator',
    }

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------
    @classmethod
    def _resolve_agent_name(cls, agent_name: str) -> str:
        """将 Agent 短名称归一为 AGENT_MODEL_OVERRIDES 中的规范名称"""
        return cls.AGENT_NAME_ALIASES.get(agent_name, agent_name)

    @staticmethod
    def _resolve_env(value: Any) -> Any:
        """解析配置值中的 ${ENV_VAR} 占位符，便于 override 引用环境变量"""
        if not isinstance(value, str):
            return value
        # 完整匹配 ${VAR}
        if value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            return os.environ.get(env_key, value)
        # 内嵌 ${VAR}
        return re.sub(
            r'\$\{(\w+)\}',
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )

    @classmethod
    def _is_provider_available(cls, provider: str) -> bool:
        """检查某个 provider 对应的 langchain 包是否已安装"""
        provider = (provider or "openai").lower()
        if provider in cls.OPENAI_COMPATIBLE_PROVIDERS:
            try:
                import langchain_openai  # noqa: F401
                return True
            except ImportError:
                return False
        if provider == "anthropic":
            try:
                import langchain_anthropic  # noqa: F401
                return True
            except ImportError:
                return False
        if provider == "gemini":
            try:
                import langchain_google_genai  # noqa: F401
                return True
            except ImportError:
                return False
        return False

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    @classmethod
    def list_available_providers(cls) -> List[str]:
        """返回当前环境中可用的 provider 列表

        通过尝试导入对应的 langchain 包来判断可用性，
        未安装的可选 provider 会被静默跳过。

        Returns:
            可用 provider 名称列表，例如 ['openai', 'anthropic', 'gemini']
        """
        available: List[str] = []

        # OpenAI 兼容 provider（核心依赖）
        try:
            import langchain_openai  # noqa: F401
            available.append("openai")
        except ImportError:
            pass

        # Anthropic Claude（可选依赖）
        try:
            import langchain_anthropic  # noqa: F401
            available.append("anthropic")
        except ImportError:
            pass

        # Google Gemini（可选依赖）
        try:
            import langchain_google_genai  # noqa: F401
            available.append("gemini")
        except ImportError:
            pass

        return available

    @classmethod
    def _build_llm(
        cls,
        provider: str,
        model: str,
        api_key: str,
        base_url: Optional[str],
        temperature: float,
        max_tokens: int,
    ) -> 'BaseChatModel':
        """按 provider 路由到对应的 langchain chat 类，构建 LLM 实例（内部统一入口）

        Args:
            provider: provider 名称
            model: 模型名称
            api_key: API Key
            base_url: 自定义 base_url（可选）
            temperature: 采样温度
            max_tokens: 最大输出 token 数

        Returns:
            LangChain BaseChatModel 实例

        Raises:
            ImportError: 当所需的 langchain provider 包未安装时
        """
        provider = (provider or "openai").lower()

        # 1) OpenAI 兼容 provider：统一使用 ChatOpenAI
        #    覆盖 openai / qwen / deepseek / custom 等所有兼容 OpenAI API 的服务
        if provider in cls.OPENAI_COMPATIBLE_PROVIDERS:
            try:
                from langchain_openai import ChatOpenAI
            except ImportError:
                raise ImportError(
                    "langchain-openai 未安装，请执行: pip install langchain-openai"
                )

            kwargs = {
                'model': model,
                'api_key': api_key,
                'temperature': temperature,
                'max_tokens': max_tokens,
                'request_timeout': 120,
                'max_retries': 2,
            }
            # 如果配置了自定义 base_url，使用它（兼容本地模型 / 兼容服务）
            if base_url:
                kwargs['base_url'] = base_url
            return ChatOpenAI(**kwargs)

        # 2) Anthropic Claude（可选依赖）
        if provider == "anthropic":
            try:
                from langchain_anthropic import ChatAnthropic
            except ImportError:
                raise ImportError(
                    "langchain-anthropic 未安装，请执行: "
                    "pip install langchain-anthropic"
                )

            kwargs = {
                'model': model,
                'api_key': api_key,
                'temperature': temperature,
                'max_tokens': max_tokens,
                'max_retries': 2,
            }
            # Anthropic 支持通过 base_url 指定代理或自托管服务
            if base_url:
                kwargs['base_url'] = base_url
            return ChatAnthropic(**kwargs)

        # 3) Google Gemini（可选依赖）
        if provider == "gemini":
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
            except ImportError:
                raise ImportError(
                    "langchain-google-genai 未安装，请执行: "
                    "pip install langchain-google-genai"
                )

            kwargs = {
                'model': model,
                'google_api_key': api_key,
                'temperature': temperature,
                'max_output_tokens': max_tokens,
                'max_retries': 2,
            }
            return ChatGoogleGenerativeAI(**kwargs)

        # 未知 provider：默认按 OpenAI 兼容方式处理，保持向后兼容
        logger.warning(
            f"未知 provider [{provider}]，按 OpenAI 兼容方式创建 LLM"
        )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(
                "langchain-openai 未安装，请执行: pip install langchain-openai"
            )
        kwargs = {
            'model': model,
            'api_key': api_key,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'request_timeout': 120,
            'max_retries': 2,
        }
        if base_url:
            kwargs['base_url'] = base_url
        return ChatOpenAI(**kwargs)

    @classmethod
    def create_by_provider(
        cls,
        config,
        provider: str,
        model_name: str,
    ) -> 'BaseChatModel':
        """按显式 provider 名称创建 LLM 实例

        使用 config.llm 中的 api_key / base_url / temperature / max_tokens
        作为默认值，仅显式覆盖 provider 与 model。

        Args:
            config: AppConfig 配置实例
            provider: provider 名称，如 openai / qwen / deepseek /
                anthropic / gemini
            model_name: 模型名称，如 gpt-4 / claude-3-sonnet / gemini-1.5-pro

        Returns:
            LangChain BaseChatModel 实例

        Raises:
            ImportError: 当所需的 langchain provider 包未安装时
        """
        llm_config = config.llm
        return cls._build_llm(
            provider=provider,
            model=model_name,
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
        )

    @classmethod
    def create(cls, config, agent_name: str = "default") -> 'BaseChatModel':
        """根据配置创建 LLM 实例（支持模型热路由）

        路由优先级（每个字段独立回退）：
        1. config.agent_models[agent]   —— 配置文件驱动的按 Agent 路由（可选）
        2. AGENT_MODEL_OVERRIDES[agent]  —— 类内置的默认路由
        3. config.llm                    —— 最终兜底的默认配置

        若路由结果指向一个未安装的可选 provider（anthropic / gemini），
        会打印告警并自动回退到 config.llm 默认配置，保证不中断现有流程。
        OpenAI 兼容 provider 属于核心依赖，未安装时按原行为抛出 ImportError。

        Args:
            config: AppConfig 配置实例
            agent_name: Agent 名称，用于获取特定 Agent 的模型路由配置

        Returns:
            LangChain BaseChatModel 实例
        """
        llm_config = config.llm
        default_provider = getattr(llm_config, 'provider', 'openai') or 'openai'

        # 归一 Agent 名称（兼容短名称别名，如 sre -> specialist_sre）
        canonical_name = cls._resolve_agent_name(agent_name)

        # 1) 优先读取 config.agent_models（配置文件驱动的路由，可选）
        #    AppConfig 中未定义该字段时视为空，不影响现有配置
        config_agent_models = getattr(config, 'agent_models', None)
        if not isinstance(config_agent_models, dict):
            config_agent_models = {}
        overrides = dict(
            config_agent_models.get(canonical_name)
            or config_agent_models.get(agent_name)
            or {}
        )

        # 2) 叠加 AGENT_MODEL_OVERRIDES（类内置默认路由）
        if not overrides:
            overrides = dict(cls.AGENT_MODEL_OVERRIDES.get(canonical_name, {}))

        # 解析 override 中的 ${ENV_VAR} 环境变量占位符
        overrides = {k: cls._resolve_env(v) for k, v in overrides.items()}

        # 解析最终生效的 provider / model
        provider = overrides.get('provider', default_provider)
        model = overrides.get('model', llm_config.model)

        # 优雅降级：仅对可选 provider（anthropic / gemini）做未安装回退，
        # 核心的 OpenAI 兼容 provider 未安装时由 _build_llm 抛出 ImportError
        is_optional_provider = provider in {"anthropic", "gemini"}
        if is_optional_provider and not cls._is_provider_available(provider):
            logger.warning(
                f"Agent [{agent_name}] 期望 provider [{provider}] 未安装，"
                f"回退到默认 provider=[{default_provider}], "
                f"model=[{llm_config.model}]"
            )
            provider = default_provider
            model = llm_config.model
            api_key = llm_config.api_key
            base_url = llm_config.base_url
            temperature = llm_config.temperature
            max_tokens = llm_config.max_tokens
        else:
            api_key = overrides.get('api_key', llm_config.api_key)
            base_url = overrides.get('base_url', llm_config.base_url)
            temperature = overrides.get('temperature', llm_config.temperature)
            max_tokens = overrides.get('max_tokens', llm_config.max_tokens)

        llm = cls._build_llm(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        logger.info(
            f"LLM created for [{agent_name}] -> [{canonical_name}]: "
            f"provider={provider}, model={model}, temperature={temperature}"
        )
        return llm

    @classmethod
    def create_for_agent(cls, config, agent_name: str) -> 'BaseChatModel':
        """为特定 Agent 创建 LLM（语义化接口）

        等价于 create(config, agent_name)，保留以兼容现有调用方。
        """
        return cls.create(config, agent_name)


# 类型别名（用于类型提示）
try:
    from langchain_core.language_models import BaseChatModel
except ImportError:
    BaseChatModel = None  # type: ignore
