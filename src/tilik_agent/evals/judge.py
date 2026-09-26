"""DeepEval judge backed by the ``evals.judge`` block, not the question model."""

from __future__ import annotations

import os
from typing import Any

from deepeval.models import DeepEvalBaseLLM
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from tilik_agent.config import LlmConfig


class YamlJudge(DeepEvalBaseLLM):
    """Chat client built only from ``evals.judge``."""

    def __init__(self, config: LlmConfig) -> None:
        self._config = config
        super().__init__(config.model)

    def load_model(self) -> ChatOpenAI:
        config = self._config
        return ChatOpenAI(
            model=config.model,
            temperature=config.temperature,
            base_url=config.base_url,
            api_key=os.environ[config.api_key_env],
            extra_body=config.extra_body,
        )

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        chat: ChatOpenAI = self.model
        if schema is not None:
            return chat.with_structured_output(schema).invoke(prompt)
        content = chat.invoke(prompt).content
        return content if isinstance(content, str) else "".join(str(item) for item in content)

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        return self.generate(prompt, schema=schema)

    def get_model_name(self) -> str:
        return self._config.model
