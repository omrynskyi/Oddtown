"""Every LLM call a character makes goes through `call_json`.

A prompt is a list of named sections, stable ones first. The system sections
(rules and persona) never change for a character's whole life, so they are
marked for prompt caching. The user sections change every call.

    result = await call_json(
        kind="decision", model=persona["model"],
        system=[Section("rules", RULES), Section("persona", persona_text)],
        user=[Section("world", ...), Section("memories", ...), Section("inbox", ...)],
        out=Decision, mock=lambda: Decision(...),
    )
    result.value     # a validated Decision
    result.usage     # input, cache read, cache write and output tokens
    result.sections  # estimated input tokens per section

`sections` splits the provider's exact input total across sections by their
share of the prompt's characters, so it needs no extra tokenizer calls and
works for any model.

Providers, by model id:

    mock        no network, returns `mock()`, so the whole town runs with no API key.
                Mock calls still report estimated tokens, so budgets and llm.call events work.
    claude-*    the Anthropic SDK with structured outputs and prompt caching.
    anything    an OpenAI-compatible chat completions endpoint (OpenCode Zen by default,
    else        for big-pickle). The JSON schema goes in the prompt, and the reply is
                validated with pydantic and retried once if it doesn't fit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from agents import config

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Models that take the server-side refusal fallback ("fallbacks": "default").
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Section:
    name: str
    text: str


@dataclass
class Usage:
    input_tokens: int = 0  # uncached input
    cache_read: int = 0
    cache_write: int = 0
    output_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read + self.cache_write

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.output_tokens

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.prompt_tokens,
            "uncached_tokens": self.input_tokens,
            "cache_read_tokens": self.cache_read,
            "cache_write_tokens": self.cache_write,
            "output_tokens": self.output_tokens,
        }


@dataclass
class Result(Generic[T]):
    value: T
    kind: str
    model: str
    usage: Usage
    latency_ms: int
    retries: int
    sections: dict[str, int] = field(default_factory=dict)


class LLMError(Exception):
    """The call failed or came back unusable. The caller skips the turn."""


def resolve_model(model: str | None) -> str:
    return config.model_override() or model or config.DEFAULT_MODEL


def estimate_sections(sections: list[Section], prompt_tokens: int) -> dict[str, int]:
    total_chars = sum(len(s.text) for s in sections) or 1
    estimate: dict[str, int] = {}
    for s in sections:
        estimate[s.name] = estimate.get(s.name, 0) + round(prompt_tokens * len(s.text) / total_chars)
    return estimate


async def call_json(
    *,
    kind: str,
    model: str | None,
    system: list[Section],
    user: list[Section],
    out: type[T],
    mock: Callable[[], T],
) -> Result[T]:
    model = resolve_model(model)
    started = time.monotonic()
    if model == config.MOCK_MODEL:
        value, usage, retries = _mock_call(system, user, mock)
    elif model.startswith("claude-"):
        value, usage, retries, model = await _claude_call(kind, model, system, user, out)
    else:
        # No structured outputs to lean on, so the schema goes in the prompt and counts as a section.
        user = user + [Section("format", format_instructions(out))]
        value, usage, retries, model = await _openai_call(kind, model, system, user, out)
    _log_prompt(kind, model, system, user, value)
    return Result(
        value=value,
        kind=kind,
        model=model,
        usage=usage,
        latency_ms=round((time.monotonic() - started) * 1000),
        retries=retries,
        sections=estimate_sections(system + user, usage.prompt_tokens),
    )


def _log_prompt(kind: str, model: str, system: list[Section], user: list[Section], value: BaseModel) -> None:
    """With ODDTOWN_PROMPT_LOG set to a folder, append every prompt and answer there, one JSONL file per character."""
    folder = os.environ.get("ODDTOWN_PROMPT_LOG")
    if not folder:
        return
    try:
        os.makedirs(folder, exist_ok=True)
        record = {
            "ts": time.time(), "agent": os.environ.get("AGENT_ID"), "run": os.environ.get("RUN_ID"), "kind": kind,
            "model": model, "system": {s.name: s.text for s in system}, "user": {s.name: s.text for s in user},
            "answer": value.model_dump(),
        }
        with open(os.path.join(folder, f"{os.environ.get('AGENT_ID', 'agent')}.jsonl"), "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        log.exception("couldn't log the prompt")


# -- mock ---------------------------------------------------------------------


def _mock_call(system: list[Section], user: list[Section], mock: Callable[[], T]) -> tuple[T, Usage, int]:
    value = mock()
    chars = sum(len(s.text) for s in system + user)
    return value, Usage(input_tokens=chars // 4, output_tokens=len(value.model_dump_json()) // 4), 0


# -- Claude -------------------------------------------------------------------

_client = None


def _claude():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.AsyncAnthropic(timeout=90.0, max_retries=2)
    return _client


async def _claude_call(
    kind: str, model: str, system: list[Section], user: list[Section], out: type[T]
) -> tuple[T, Usage, int, str]:
    import anthropic

    blocks = [{"type": "text", "text": s.text} for s in system]
    if blocks:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}  # rules and persona never change for this character
    request = {
        "model": model,
        "max_tokens": config.MAX_TOKENS.get(kind, 4000),
        "system": blocks,
        "messages": [{"role": "user", "content": "\n\n".join(s.text for s in user)}],
        "output_format": out,
        "output_config": {"effort": config.EFFORT.get(kind, "low")},
    }
    if model in FALLBACK_MODELS:
        request["betas"] = [FALLBACK_BETA]
        request["fallbacks"] = "default"

    usage = Usage()
    for attempt in range(2):
        try:
            response = await _claude().beta.messages.parse(**request)
        except ValidationError as exc:
            log.warning("%s call returned JSON that failed validation (attempt %d): %s", kind, attempt + 1, exc)
            continue
        except anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}") from exc

        u = response.usage
        usage.input_tokens += u.input_tokens or 0
        usage.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        usage.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        usage.output_tokens += u.output_tokens or 0

        if response.stop_reason == "refusal":
            raise LLMError(f"{kind} call was refused")
        if response.stop_reason == "max_tokens":
            log.warning("%s call hit max_tokens (attempt %d)", kind, attempt + 1)
            continue
        if response.parsed_output is not None:
            return response.parsed_output, usage, attempt, response.model
    raise LLMError(f"{kind} call never returned valid JSON")


# -- OpenAI-compatible (big-pickle on OpenCode Zen) ----------------------------

_http = None
_json_mode = True  # turned off for the process if the endpoint rejects response_format
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def format_instructions(out: type[BaseModel]) -> str:
    schema = json.dumps(out.model_json_schema(), separators=(",", ":"))
    return f"Reply with only one JSON object that matches this JSON Schema, with no other text:\n{schema}"


def extract_json(text: str) -> dict:
    """The JSON object in a reply, even if it came wrapped in a code fence or a sentence."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[start : end + 1])


def _openai_http():
    global _http
    if _http is None:
        import httpx

        _http = httpx.AsyncClient(timeout=120.0)
    return _http


async def _post_chat(payload: dict) -> dict:
    """POST /chat/completions, retrying rate limits and server errors with backoff."""
    global _json_mode
    import httpx

    key = os.environ.get(config.LLM_API_KEY_ENV)
    if not key:
        raise LLMError(f"set {config.LLM_API_KEY_ENV} to use {payload['model']}, or ODDTOWN_MODEL=mock")
    url = f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in range(4):
        body = dict(payload)
        if _json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = await _openai_http().post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            if attempt == 3:
                raise LLMError(f"connection error: {exc}") from exc
            await asyncio.sleep(2 ** attempt)
            continue
        if response.status_code == 400 and _json_mode:
            log.warning("endpoint rejected JSON mode, asking for JSON in the prompt only: %s", response.text[:300])
            _json_mode = False
            continue
        if response.status_code in RETRY_STATUS and attempt < 3:
            wait = min(float(response.headers.get("retry-after") or 2 ** (attempt + 1)), 20.0)
            log.warning("LLM endpoint returned %d, retrying in %.0fs", response.status_code, wait)
            await asyncio.sleep(wait)
            continue
        if response.status_code >= 400:
            raise LLMError(f"API error {response.status_code}: {response.text[:300]}")
        return response.json()
    raise LLMError("LLM endpoint kept failing")


async def _openai_call(
    kind: str, model: str, system: list[Section], user: list[Section], out: type[T]
) -> tuple[T, Usage, int, str]:
    messages = [
        {"role": "system", "content": "\n\n".join(s.text for s in system)},  # stable first, for any prefix caching
        {"role": "user", "content": "\n\n".join(s.text for s in user)},
    ]
    usage = Usage()
    for attempt in range(2):
        payload = {"model": model, "messages": messages, "max_tokens": config.MAX_TOKENS.get(kind, 4000)}
        if config.LLM_REASONING_EFFORT:
            payload["reasoning_effort"] = config.LLM_REASONING_EFFORT
        data = await _post_chat(payload)
        u = data.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        usage.input_tokens += (u.get("prompt_tokens") or 0) - cached
        usage.cache_read += cached
        usage.output_tokens += u.get("completion_tokens") or 0

        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        try:
            return out.model_validate(extract_json(text)), usage, attempt, data.get("model") or model
        except (ValueError, ValidationError) as exc:
            problem = "it was cut off" if choice.get("finish_reason") == "length" else str(exc)[:500]
            log.warning("%s reply didn't fit the schema (attempt %d): %s", kind, attempt + 1, problem)
            messages = messages + [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": f"That reply didn't fit the schema ({problem}). Reply with only the JSON object, shorter."},
            ]
    raise LLMError(f"{kind} call never returned valid JSON")
