# ADR 0007: Tools are granted, adapters only choose

Date: 2026-11-26

Accepted for 0.7.0.

## Context

Goals can name a next step, and a model can be selected. Phase 7 needs a way to run a small piece of work, to swap the component that chooses that work, and to refuse the call unless the process allows it. Vendor agent frameworks are large optional dependencies. The core must not import them. A shell tool would make the runtime an automation product. This project is not that.

## Decision

1. **The facade is `engine.agents`.** `register` adds a tool with a name, a description, a permission, and `call(argument)`. Built-in tools `echo`, `note`, and `finish` are registered at startup. Registration does not grant them.

2. **Permissions are process-local.** `allow` and `revoke` change a set held on the handle. The set is not written to SQLite. A new process starts with an empty set. `call` runs the tool only when its permission is in the set. A denied call is stored and raises `AgentError` with code `denied`. The tool function is not entered.

3. **Adapters choose. The runtime runs.** `use` selects an object with `name` and `decide(request)`. `EchoAgent` and `ScriptedAgent` are built in. `step` shows the adapter the next-action text and the names of tools that are both registered and granted. The adapter returns a tool name and an argument, or None. The runtime then uses the same `call` path, so a decision cannot skip the permission check. The heartbeat does not call `step`.

4. **Built-in tools stay narrow.** `echo` returns the argument. `note` appends an episode. `finish` marks the current next action succeeded. None of them opens a shell, reads the filesystem, or contacts the network. A custom tool is an ordinary object. The application registers it.

5. **The log keeps the outcome, not the argument.** Schema version moves to `"7"`. Versions `"1"` through `"6"` gain `tool_calls`. Each row stores the agent name, tool, permission, decision, status, and a clipped detail. The newest 200 rows are kept. Do not pass a secret as an argument: `echo` puts that text into the detail.

6. **Health warns and continues.** No calls passes. A denied or failed latest call warns. The process keeps running.

7. **Identity is required to call.** `register`, `allow`, and `use` work before `initialize`. `call` and `step` need an identity so the row has an engine id. A store without the tool-call methods can still boot. `call` then raises `ConfigurationError` before the tool runs.

## Consequences

An application can swap adapters and add tools without rewriting lifecycle code. Nothing runs until that process grants a permission. Framework packages and a plugin loader remain later work.

## Not decided here

A shell or filesystem tool, streaming tool output, persistent grants, and entry-point discovery.
