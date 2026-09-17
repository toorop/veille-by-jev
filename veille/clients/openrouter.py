"""Adapter for an OpenAI-compatible chat-completions API, OpenRouter by default.

Everything provider-specific lives here: the request shape, the answer shape and the cost
accounting. Verified against OpenRouter's OpenAPI specification: the response carries
`usage.prompt_tokens`, `usage.completion_tokens` and `usage.cost`, the last one being the real
cost of the call in USD. When it is absent — a provider that does not report it — the cost is
left as `None` rather than estimated from a price list.

The writer only speaks the chat-completions protocol, so another provider or a local server
means changing the base URL, not this code.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from veille.config import WriteConfig

API_KEY_ENV = "WRITE_LLM_API_KEY"
USER_AGENT = "veille-by-jev/0.1"
# Error bodies are quoted back to the operator, but not without end.
MAX_ERROR_BODY = 400


class ChatError(RuntimeError):
    """Raised when the writing model cannot be reached or answers nothing usable."""


@dataclass(frozen=True)
class ChatReply:
    """One answer from the writing model, with what it cost."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    duration_s: float


def _as_int(value: object) -> int:
    """Return `value` as an int, or zero when the provider left it unset."""
    return int(value) if isinstance(value, (int, float)) else 0


def _as_float(value: object) -> float | None:
    """Return `value` as a float, or `None` when the provider left it unset."""
    return float(value) if isinstance(value, (int, float)) else None


def complete(system_prompt: str, user_prompt: str, cfg: WriteConfig, api_key: str) -> ChatReply:
    """Ask the writing model once.

    Args:
        system_prompt: The instructions of `config/write-prompt.md`.
        user_prompt: The state of the night, as JSON.
        cfg: Writing settings.
        api_key: Provider key, read from the environment by the caller.

    Returns:
        The answer and its measured cost.

    Raises:
        ChatError: If the provider refuses the call or returns no content.
    """
    started = time.monotonic()
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-Title": USER_AGENT,
    }

    try:
        response = httpx.post(
            f"{cfg.base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
            timeout=cfg.timeout_s,
        )
    except httpx.HTTPError as exc:
        raise ChatError(f"{type(exc).__name__}: {exc}") from exc

    if response.status_code >= 400:
        body = response.text[:MAX_ERROR_BODY]
        raise ChatError(f"HTTP {response.status_code} from the provider: {body}")

    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ChatError(
            f"unusable answer from the provider: {response.text[:MAX_ERROR_BODY]}"
        ) from exc

    if not content or not str(content).strip():
        raise ChatError("the provider returned an empty answer")

    usage = body.get("usage") or {}
    return ChatReply(
        content=str(content),
        model=str(body.get("model") or cfg.model),
        input_tokens=_as_int(usage.get("prompt_tokens")),
        output_tokens=_as_int(usage.get("completion_tokens")),
        cost_usd=_as_float(usage.get("cost")),
        duration_s=round(time.monotonic() - started, 2),
    )


class OpenRouterChat:
    """The real writer: one call to an OpenAI-compatible endpoint.

    Building it validates the key, so a missing key fails once rather than after a long
    prompt has been assembled.
    """

    def __init__(self, cfg: WriteConfig, api_key: str) -> None:
        """Store the settings and the key, refusing an empty key.

        Args:
            cfg: Writing settings.
            api_key: Provider key.

        Raises:
            ChatError: If the key is empty.
        """
        if not api_key.strip():
            raise ChatError(
                f"no writing key: set {API_KEY_ENV} in .env (see .env.example), then try again"
            )
        self.cfg = cfg
        self.api_key = api_key

    def complete(self, system_prompt: str, user_prompt: str) -> ChatReply:
        """Ask the model once; see `complete` for the details."""
        return complete(system_prompt, user_prompt, self.cfg, self.api_key)
