# ADR 0005: One completion API, swappable providers

Date: 2026-11-26

Accepted for 0.5.0.

## Context

The runtime can live, tick, deliver events, and store memory without a language model. Phase 5 needs a way to call a model, switch the provider, and record the cost of those calls. The lifecycle, heartbeat, and memory code must stay the same when the provider changes.

Vendor SDKs are optional and large. The core must not import them.

## Decision

1. **The facade is `engine.brain`.** `use(provider)` selects a provider. `complete(prompt)` validates the request, calls that provider, and returns a `ModelResponse`. `use(None)` clears the selection. Until `use`, `configured` is false and `complete` raises `ModelError` with code `not_configured`.

2. **Providers are ordinary objects.** Each one has `provider_name`, `model_name`, and `complete(request, timeout=...)`. `open_model("echo")` and `open_model("openai-compatible", base_url=..., model=...)` build the built-ins. `ScriptedProvider` returns a fixed list of replies. Replacing the object is the whole switch. Callers do not edit `LifeEngine`.

3. **HTTP is stdlib.** `HttpChatProvider` POSTs chat-completions JSON with `urllib`. It does not import an OpenAI, Anthropic, or Ollama package. `api_key` is an argument. The class does not read the environment. The CLI reads `AI_LIFE_ENGINE_API_KEY` only for `complete --provider openai-compatible`. Base URLs must be `http` or `https` and must not carry credentials.

4. **Usage is persisted. Prompts are not.** Each call appends a row with provider, model, token counts, status, and a clipped error. Schema version moves to `"5"`. Versions `"1"` through `"4"` upgrade by creating `model_usage`. The newest 200 rows are kept. A store without these methods can still boot. `complete` then raises `ConfigurationError` before the provider is called.

5. **The heartbeat stays opt-in.** When a policy returns `required=True` and the constructor was given a reasoner, that reasoner still runs. When no reasoner was given and a provider is selected, the tick calls `brain.complete` with a short state prompt. The default policy returns `required=False`, so selecting a provider does not by itself call it from `run`.

6. **Failures stay on the call.** Timeouts, HTTP failures, and malformed responses become `ModelError`. The usage row is `error`. The `brain` health check warns. The runtime keeps running. `model_timeout` bounds the provider call (default 30 seconds, range 0.05..120).

7. **Identity is required.** `complete` and usage reads need an initialized runtime so the row has an engine id.

## Consequences

An application can boot with no model, then point `complete` at a local echo provider or an HTTP endpoint without rewriting the runtime. Token totals survive a restart. Goals shipped in 0.6.0. See [ADR 0006](0006-goals.md). Vendor packages are still later work.

## Not decided here

Streaming, tool calls, embeddings, automatic retries, and a package extra that installs a vendor SDK.
