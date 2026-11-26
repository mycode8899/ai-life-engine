# ADR 0008: Plugins and the local API

## Status

Accepted for 0.8.0.

## Context

Phases 1 through 7 can boot, remember, plan, and call a granted tool without a vendor SDK. The remaining planned surface is a way for another package to add a tool, and a way for a local process to read that runtime over HTTP. Both have to stay optional. Importing the library must not scan the working directory or open a port.

The repository already rejects an import of FastAPI. The API therefore has to use the standard library.

## Decision

1. **Plugins load only when asked.** `engine.plugins.install(plugin)` sets up one object. `engine.plugins.load()` reads the entry-point group `ai_life_engine.plugins`. Construction, import, and `start` do not call either method.

2. **The context is narrow.** `setup(context)` may `register_tool`, `register_health_check`, and `subscribe`. It cannot grant a permission, call a tool, or change the lifecycle. A tool added by a plugin stays denied.

3. **A failed plugin does not stop the process.** The failure is an in-memory record. The `plugins` health check warns. Records are not a new database table. Schema version stays at `"7"`.

4. **The HTTP server is local by default.** `serve` binds `127.0.0.1:8765`. Any other host requires `allow_remote`. There is no authentication. A request body cannot grant a permission. `POST /v1/tools/call` uses the same `agents.call` path as the CLI, so denial and the audit row stay consistent.

5. **Release automation builds artifacts and does not publish them.** A tag matching `v*` runs the tests and uploads the wheel and sdist. The package is not on PyPI. The Docker image binds `0.0.0.0` with `--allow-remote` because a container's published port is the isolation boundary the operator chooses.

## Consequences

An application can add a tool from another package without changing lifecycle code, and a local client can read health and call a tool this process already granted. A plugin cannot authorize itself. The API is safe on loopback and unsafe on any other interface.

## Not decided here

Authentication, persistent grants, a shell or filesystem tool, vendor framework packages, and publishing to PyPI.
