# ADR 0006: Goals are stored plans, not tool calls

Date: 2026-11-26

Accepted for 0.6.0.

## Context

The runtime can live, tick, remember, and call a model. Phase 6 needs a durable intention, an ordered plan, a way to choose the next step, and a record of what happened. Executing that step is a tool call. Tools belong to Phase 7. A plan must also work when no model is configured.

## Decision

1. **The facade is `engine.goals`.** `add` creates an active goal with a priority from 1 to 5. Priority 1 is considered first. A runtime stores at most 50 goals.

2. **`plan` does not call a model.** The caller supplies 1..8 step strings. That plan becomes the accepted plan. The previous accepted plan is marked superseded. Five plans are kept per goal. `propose` is the only method that calls the selected model. It parses non-empty lines into steps and then uses `plan`. The heartbeat never calls `propose`.

3. **`next_action` is a read.** It returns the first pending step of the accepted plan on the most urgent active goal. Blocked, done, and dropped goals are skipped. The return value is text. Nothing is spawned, and no shell command is derived from it.

4. **Reflection updates bookkeeping.** `succeeded` marks the chosen pending step, or the first pending step, done. `skipped` marks it skipped. When no pending step remains, the goal becomes `done`. `blocked` sets the goal blocked and leaves steps pending. `failed` and `noted` only append a note. A dropped goal cannot be reflected on. The newest 200 reflections are kept.

5. **The heartbeat only counts.** When the application does not pass its own task source, a tick reports how many pending steps active goals have. Zero goals still produces "No tasks are waiting." The default reasoning policy does not turn that count into a model call.

6. **Health warns and continues.** No goals passes. A blocked goal warns. An active goal with no accepted plan warns. The process keeps running.

7. **Schema version is `"6"`.** Versions `"1"` through `"5"` gain the goal tables. A store without the goal methods can still boot. Goal calls then raise `ConfigurationError`. Identity is required before the first write.

## Consequences

An application can keep a next step across restarts without a model, or ask a selected model to draft the steps. Tools that can act on a step shipped in 0.7.0. See [ADR 0007](0007-agents.md). A next action is still not executed unless a granted tool is called.

## Not decided here

Tool permissions, agent adapters, automatic replanning, and embeddings.
