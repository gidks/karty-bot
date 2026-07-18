"""Клиент нейросети. Работает с любым OpenAI-совместимым API (AITunnel, ProxyAPI,
VseGPT и т.д.) — провайдер меняется одной строкой LLM_BASE_URL в .env."""

import asyncio
import logging

from openai import AsyncOpenAI

import config

log = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


class LLMError(Exception):
    pass


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
            timeout=60.0,
        )
    return _client


async def chat(
    messages: list[dict],
    max_tokens: int = 900,
    temperature: float = 0.9,
    retries: int = 2,
) -> str:
    """Один вызов чата с ретраями. Бросает LLMError, если всё плохо."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await _get_client().chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
            raise LLMError("Пустой ответ модели")
        except Exception as e:  # noqa: BLE001 — ретраим любые сетевые/API ошибки
            last_err = e
            log.warning("LLM attempt %s failed: %s", attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
    raise LLMError(str(last_err))
