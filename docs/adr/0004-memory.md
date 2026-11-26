# ADR 0004: Memory without a model

Date: 2026-11-26

Accepted for 0.4.0. The model interface shipped in 0.5.0. See [ADR 0005](0005-model-interface.md). Remembering still does not call a model.

## Context

The runtime can persist its own lifecycle, a heartbeat, and an event log. It still forgets anything the application learned. Phase 4 needs working memory, episodic memory, and semantic memory that survive a process restart and can be read back through one API.

Search must not require a language model. Embeddings and providers belong to Phase 5.

## Decision

1. **Three stores, one handle.** `engine.memory` exposes working memory, episodes, and facts. `MemoryKind` is `working`, `episode`, or `fact`. The event log stays a separate table. Remembering does not publish an event and does not call `heartbeat.wake()`.

2. **Working memory is a scratchpad.** `remember_working(key, value)` inserts or replaces one key. At most 32 keys. A new key past that limit raises `LifecycleError`. Updating an existing key is allowed. `forget_working` and `clear_working` delete explicitly. Nothing is evicted silently.

3. **Episodes are append-only.** `remember_episode(summary)` adds a row. The store keeps the newest 200 rows and deletes older ones. The same summary may be stored more than once.

4. **Facts are semantic memory addressed by key.** `remember_fact(key, content)` inserts or replaces. The fact id and creation time stay stable across replacement. At most 200 facts. A new key past that limit raises. `forget_fact` deletes one key.

5. **Search is a substring.** `search(query, kind=None)` matches key, value, summary, or content without regard to case. There is no embedding, ranking model, or tokenizer. Results are newest first.

6. **Text is bounded and secret-free.** Keys and sources are 1..64 lowercase names. Values, summaries, fact content, and queries are printable single-line text, at most 500 characters (200 for a query). Payloads are string maps and reject secret-looking keys.

7. **Persistence is additive.** Schema version moves to `"4"`. Versions `"1"`, `"2"`, and `"3"` upgrade by creating the memory tables. Version `"99"` still raises `schema_mismatch`. A store without the memory methods can still boot. `remember` and `search` then raise `ConfigurationError`.

8. **Identity is required.** Memory methods refresh the runtime record and refuse to write before `initialize` or `start`.

9. **Health stays quiet.** The `memory` check passes when the store is empty and when it holds records. It does not fail the runtime. The name is reserved.

## Consequences

An application can write memory, exit, open the same data directory, and search what it wrote. That works with no model configured. Phase 5 can add a model later without changing these records into vectors. A caller that wants a heartbeat to notice new memory publishes an event itself.

## Not decided here

Embeddings, automatic extraction from events, forgetting policies based on importance, and memory shared across more than one runtime identity.
