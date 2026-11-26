# SPDX-License-Identifier: Apache-2.0
"""Model providers.

``echo`` and ``scripted`` run in process. ``openai-compatible`` speaks the
chat-completions JSON used by many HTTP servers. This module does not import
a provider SDK.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urlsplit

from ai_life_engine.brain.models import ModelRequest, ModelResponse, TokenUsage
from ai_life_engine.exceptions import ConfigurationError, ModelError

_MAX_RESPONSE_BYTES = 1_000_000


class ModelProvider(Protocol):
    """One completion backend. Switching this object does not change the runtime."""

    provider_name: str
    model_name: str

    def complete(self, request: ModelRequest, *, timeout: float) -> ModelResponse: ...


class EchoProvider:
    """Return the prompt unchanged. Useful as a local stand-in."""

    provider_name = "echo"
    model_name = "echo"

    def complete(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        del timeout
        tokens = len(request.prompt)
        return ModelResponse(
            text=request.prompt,
            provider=self.provider_name,
            model=self.model_name,
            usage=TokenUsage(prompt_tokens=tokens, completion_tokens=tokens),
        )


class ScriptedProvider:
    """Return a fixed list of replies, one per call."""

    provider_name = "scripted"

    def __init__(self, replies: list[str], *, model: str = "scripted") -> None:
        if not isinstance(replies, list) or not all(isinstance(item, str) for item in replies):
            raise ConfigurationError("Scripted replies must be a list of strings.")
        self.model_name = _model_name(model)
        self._replies = list(replies)
        self._lock = threading.Lock()

    def complete(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        del request, timeout
        with self._lock:
            if not self._replies:
                raise ModelError(
                    "The scripted provider has no replies left.",
                    code="unavailable",
                )
            text = self._replies.pop(0)
        return ModelResponse(
            text=text,
            provider=self.provider_name,
            model=self.model_name,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=max(len(text), 1)),
        )


class HttpChatProvider:
    """POST a chat completion to an OpenAI-compatible HTTP endpoint.

    ``api_key`` is sent as a bearer token when it is not None. This class
    does not read the process environment.
    """

    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
    ) -> None:
        self._base_url = _base_url(base_url)
        self.model_name = _model_name(model)
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
            raise ConfigurationError("api_key must be a non-empty string.")
        self._api_key = api_key

    def complete(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        messages: list[dict[str, str]] = []
        if request.system is not None:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        body: dict[str, object] = {"model": self.model_name, "messages": messages}
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        http_request = urllib.request.Request(
            self._base_url + "/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except TimeoutError as exc:
            raise ModelError("The model request timed out.", code="timeout") from exc
        except urllib.error.HTTPError as exc:
            code = "rejected" if 400 <= exc.code < 500 else "unavailable"
            raise ModelError(f"The model endpoint returned HTTP {exc.code}.", code=code) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ModelError("The model request timed out.", code="timeout") from exc
            raise ModelError("The model endpoint is unreachable.", code="unavailable") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ModelError("The model response is too large.", code="rejected")
        return _parse_chat_response(raw, provider=self.provider_name, model=self.model_name)


def open_model(name: str, **kwargs: object) -> ModelProvider:
    """Build a built-in provider by name.

    ``echo`` takes no arguments. ``openai-compatible`` takes ``base_url``,
    ``model``, and an optional ``api_key``.
    """

    if name == "echo":
        if kwargs:
            raise ConfigurationError("The echo provider does not take arguments.")
        return EchoProvider()
    if name == "openai-compatible":
        base_url = kwargs.pop("base_url", None)
        model = kwargs.pop("model", None)
        api_key = kwargs.pop("api_key", None)
        if kwargs or not isinstance(base_url, str) or not isinstance(model, str):
            raise ConfigurationError(
                "openai-compatible requires base_url and model, and an optional api_key."
            )
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigurationError("api_key must be a string.")
        return HttpChatProvider(base_url=base_url, model=model, api_key=api_key)
    raise ConfigurationError(f"Unknown model provider {name}.")


def _base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigurationError("base_url must be an http or https URL.")
    cleaned = value.strip().rstrip("/")
    parts = urlsplit(cleaned)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigurationError("base_url must be an http or https URL.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ConfigurationError("base_url must not embed credentials.")
    if len(cleaned) > 256:
        raise ConfigurationError("base_url must be at most 256 characters.")
    return cleaned


def _model_name(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigurationError("model must be a string.")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 128:
        raise ConfigurationError("model must be 1..128 characters.")
    if any(ord(character) < 33 or ord(character) == 127 for character in cleaned):
        raise ConfigurationError("model must not contain spaces or control characters.")
    return cleaned


def _parse_chat_response(raw: bytes, *, provider: str, model: str) -> ModelResponse:
    try:
        payload = json.loads(raw.decode("utf-8"))
        choice = payload["choices"][0]["message"]["content"]
        if not isinstance(choice, str):
            raise TypeError("content is not a string")
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            usage = {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        finish = payload["choices"][0].get("finish_reason", "stop")
        if not isinstance(finish, str):
            finish = "stop"
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, UnicodeError) as exc:
        raise ModelError("The model response was not a chat completion.", code="rejected") from exc
    try:
        tokens = TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    except ValueError as exc:
        raise ModelError("The model response reported invalid usage.", code="rejected") from exc
    if len(choice) > 100_000:
        raise ModelError("The model response is too large.", code="rejected")
    return ModelResponse(
        text=choice,
        provider=provider,
        model=model,
        usage=tokens,
        finish_reason=finish[:32] or "stop",
    )
