# SPDX-License-Identifier: Apache-2.0
"""Model facade.

``use`` selects a provider. ``complete`` validates the request, calls that
provider, and stores usage. The default selection is none, so a heartbeat
still does not call a model unless a policy asks and a provider is selected.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from ai_life_engine.brain.models import (
    ModelRequest,
    ModelResponse,
    UsageRecord,
    UsageStatus,
    UsageTotals,
)
from ai_life_engine.brain.providers import ModelProvider
from ai_life_engine.brain.store import supports_models
from ai_life_engine.core.time import utcnow
from ai_life_engine.exceptions import (
    ConfigurationError,
    LifecycleError,
    ModelError,
    PersistenceError,
)
from ai_life_engine.observability import get_logger
from ai_life_engine.validation import bounded_limit, clean_reason

logger = get_logger("ai_life_engine.brain")

_PROMPT_LIMIT = 8000
_SYSTEM_LIMIT = 2000


class BrainHost(Protocol):
    """Narrow view of the runtime the model facade is allowed to touch."""

    @property
    def lock(self) -> object: ...

    @property
    def store(self) -> object: ...

    def engine_id(self) -> UUID: ...

    def ensure_open(self) -> None: ...

    def refresh(self) -> None: ...

    def has_identity(self) -> bool: ...


class Brain:
    """Select a model provider and record each completion."""

    def __init__(self, host: BrainHost, *, timeout: float) -> None:
        self._host = host
        self._timeout = timeout
        self._provider: ModelProvider | None = None

    @property
    def configured(self) -> bool:
        """True when ``use`` has selected a provider."""

        with self._host.lock:
            self._host.ensure_open()
            return self._provider is not None

    @property
    def provider_name(self) -> str | None:
        with self._host.lock:
            self._host.ensure_open()
            if self._provider is None:
                return None
            return self._provider.provider_name

    @property
    def model_name(self) -> str | None:
        with self._host.lock:
            self._host.ensure_open()
            if self._provider is None:
                return None
            return self._provider.model_name

    def use(self, provider: ModelProvider | None) -> None:
        """Select a provider, or clear the selection when ``provider`` is None."""

        if provider is not None:
            _require_provider(provider)
        with self._host.lock:
            self._host.ensure_open()
            self._provider = provider
        if provider is None:
            logger.info("model provider cleared")
        else:
            logger.info(
                "model provider selected provider=%s model=%s",
                provider.provider_name,
                provider.model_name,
            )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        """Send one prompt to the selected provider and store the usage."""

        request = _request(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
        with self._host.lock:
            provider = self._borrow()
        try:
            response = provider.complete(request, timeout=self._timeout)
        except ModelError as exc:
            self._record(provider, status=UsageStatus.ERROR, usage=None, error=str(exc))
            raise
        except Exception as exc:
            error = ModelError("The model provider failed.", code="unavailable")
            self._record(provider, status=UsageStatus.ERROR, usage=None, error=str(error))
            raise error from exc
        checked = _check_response(response, provider)
        self._record(provider, status=UsageStatus.OK, usage=checked.usage, error=None)
        logger.info(
            "model completed provider=%s model=%s prompt_tokens=%s completion_tokens=%s",
            checked.provider,
            checked.model,
            checked.usage.prompt_tokens,
            checked.usage.completion_tokens,
        )
        return checked

    def usage(self, *, limit: int = 20) -> list[UsageRecord]:
        """Return recent usage rows, newest first."""

        bounded = _as_limit(limit)
        with self._host.lock:
            store = self._ready()
            return store.list_usage(self._host.engine_id(), limit=bounded)

    def totals(self) -> UsageTotals:
        """Return summed usage for this runtime."""

        with self._host.lock:
            store = self._ready()
            return store.usage_totals(self._host.engine_id())

    def latest_for_health(self) -> UsageRecord | None:
        """Return the newest usage row, or None when storage cannot answer."""

        try:
            self._host.ensure_open()
            store = self._host.store
            if not supports_models(store):
                return None
            with self._host.lock:
                if not self._host.has_identity():
                    return None
                return store.latest_usage(self._host.engine_id())
        except PersistenceError:
            return None

    def _borrow(self) -> ModelProvider:
        self._host.ensure_open()
        self._host.refresh()
        if not self._host.has_identity():
            raise LifecycleError(
                "Initialize or start the runtime before calling a model.",
                code="not_running",
            )
        store = self._host.store
        if not supports_models(store):
            raise ConfigurationError("This state store does not persist model usage.")
        if self._provider is None:
            raise ModelError("No language model is configured.", code="not_configured")
        return self._provider

    def _ready(self) -> object:
        self._host.ensure_open()
        self._host.refresh()
        if not self._host.has_identity():
            raise LifecycleError(
                "Initialize or start the runtime before reading model usage.",
                code="not_running",
            )
        store = self._host.store
        if not supports_models(store):
            raise ConfigurationError("This state store does not persist model usage.")
        return store

    def _record(
        self,
        provider: ModelProvider,
        *,
        status: UsageStatus,
        usage: object,
        error: str | None,
    ) -> None:
        prompt_tokens = 0
        completion_tokens = 0
        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0))
            completion_tokens = int(getattr(usage, "completion_tokens", 0))
        detail = None
        if error is not None:
            detail = clean_reason(error, limit=256)
        with self._host.lock:
            store = self._ready()
            store.append_usage(
                request_id=uuid4(),
                engine_id=self._host.engine_id(),
                provider=provider.provider_name,
                model=provider.model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                status=status,
                error=detail,
                created_at=utcnow(),
            )


def _require_provider(provider: object) -> None:
    complete = getattr(provider, "complete", None)
    name = getattr(provider, "provider_name", None)
    model = getattr(provider, "model_name", None)
    if not callable(complete) or not isinstance(name, str) or not isinstance(model, str):
        raise ConfigurationError(
            "A model provider needs provider_name, model_name, and complete()."
        )
    if not name or not model:
        raise ConfigurationError("provider_name and model_name must not be empty.")


def _request(
    prompt: object,
    *,
    system: object,
    temperature: object,
    max_tokens: object,
) -> ModelRequest:
    return ModelRequest(
        prompt=_text(prompt, "Prompt", _PROMPT_LIMIT),
        system=None if system is None else _text(system, "System prompt", _SYSTEM_LIMIT),
        temperature=_temperature(temperature),
        max_tokens=_max_tokens(max_tokens),
    )


def _text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ModelError(
            f"{label} must be text of 1..{limit} characters.",
            code="invalid_argument",
        )
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise ModelError(f"{label} must not contain control characters.", code="invalid_argument")
    return value


def _temperature(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelError("temperature must be a number from 0 to 2.", code="invalid_argument")
    number = float(value)
    if number < 0 or number > 2:
        raise ModelError("temperature must be a number from 0 to 2.", code="invalid_argument")
    return number


def _max_tokens(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1 or value > 4096:
        raise ModelError(
            "max_tokens must be an integer from 1 to 4096.",
            code="invalid_argument",
        )
    return value


def _check_response(response: object, provider: ModelProvider) -> ModelResponse:
    if not isinstance(response, ModelResponse):
        raise ModelError("The model provider returned an unexpected response.", code="rejected")
    if response.provider != provider.provider_name or response.model != provider.model_name:
        raise ModelError(
            "The model provider returned a different provider or model name.",
            code="rejected",
        )
    if len(response.text) > 100_000:
        raise ModelError("The model response is too large.", code="rejected")
    return response


def _as_limit(limit: int) -> int:
    try:
        return bounded_limit(limit)
    except ValueError as exc:
        raise ModelError(str(exc), code="invalid_argument") from exc
