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


def _log_usage(resp) -> None:
    """Пишет в лог расход токенов и попадания в кэш промпта.

    cached=0 при живом трафике значит, что кэш не сработал: либо провайдер
    не принял cache_control, либо префикс короче 1024 токенов, либо между
    запросами прошло больше 5 минут (TTL) — тогда это холодный старт."""
    try:
        usage = resp.usage.model_dump() if resp.usage else {}
        details = usage.get("prompt_tokens_details") or {}
        log.info(
            "LLM usage: in=%s out=%s cached=%s cache_write=%s",
            usage.get("prompt_tokens"), usage.get("completion_tokens"),
            details.get("cached_tokens", 0), details.get("cache_write_tokens", 0),
        )
    except Exception as e:  # noqa: BLE001 — логирование не должно ронять расклад
        log.debug("usage log failed: %s", e)


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
            _log_usage(resp)
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
