"""LLM 高层服务封装，提供简洁的文本/结构化/流式生成接口。"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from pydantic import BaseModel

from backend.llm.factory import create_llm_client
from backend.llm.logger import log_llm_stream_response
from backend.llm.models import LLMRequest, LLMResponse


class LLMService:
    """面向业务层的 LLM 能力封装。

    将底层客户端的请求构造、日志记录等细节隐藏，
    对外暴露 generate_text / generate_structured / stream_text 三个简洁方法。
    """

    def __init__(
        self,
        provider_name: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """创建一个锁定 Provider 的高层 LLM 服务。

        Args:
            provider_name: Provider 配置别名；为空时使用当前默认值。
            timeout_seconds: 可选请求超时覆盖值。

        Returns:
            无。
        """
        self._client = create_llm_client(
            provider_name,
            timeout_seconds=timeout_seconds,
        )

    def _make_request(
        self,
        prompt: str,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> LLMRequest:
        """将简单参数组装为 LLMRequest。"""
        messages = [{"role": "user", "content": prompt}]
        return LLMRequest(
            messages=messages,
            system_prompt=system_prompt,
            **kwargs,
        )

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> str:
        """调用锁定 Provider 生成普通文本。

        Args:
            prompt: 用户提示词正文。
            system_prompt: 可选系统提示词。
            **kwargs: 模型、采样参数等 Provider 请求选项。

        Returns:
            Provider 返回的纯文本内容。
        """
        request = self._make_request(prompt, system_prompt, **kwargs)
        response: LLMResponse = await self._client.text_generate(request)
        return response.content

    async def generate_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str = "",
        **kwargs: Any,
    ) -> BaseModel:
        """调用锁定 Provider 并按目标 Schema 解析结构化结果。

        Args:
            prompt: 用户提示词正文。
            schema: 结构化输出目标 Pydantic 模型。
            system_prompt: 可选系统提示词。
            **kwargs: 模型、采样参数等 Provider 请求选项。

        Returns:
            通过目标 Schema 校验的 Pydantic 模型实例。
        """
        request = self._make_request(prompt, system_prompt, **kwargs)
        response: LLMResponse = await self._client.schema_generate(request, schema)
        return schema.model_validate(json.loads(response.content))

    async def stream_text(
        self,
        prompt: str,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """调用锁定 Provider 并逐块转发文本流。

        Args:
            prompt: 用户提示词正文。
            system_prompt: 可选系统提示词。
            **kwargs: 模型、采样参数等 Provider 请求选项。

        Returns:
            逐块产出文本片段的异步生成器。
        """
        request = self._make_request(prompt, system_prompt, **kwargs)
        chunks: list[str] = []
        stream_error: BaseException | None = None
        try:
            async for chunk in self._client.stream_text(request):
                # 调试日志需要完整流式原文，但对调用方仍保持逐块返回。
                chunks.append(chunk)
                yield chunk
        except BaseException as exc:
            stream_error = exc
            raise
        finally:
            provider = getattr(self._client, "provider_name", "")
            config = getattr(self._client, "config", None)
            default_model = getattr(config, "default_model", "")
            log_llm_stream_response(
                provider=provider,
                model=request.model or default_model,
                chunks=chunks,
                error=stream_error,
            )
