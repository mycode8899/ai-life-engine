# Security policy

## Supported versions

| Version | Status |
| --- | --- |
| 0.8.x | Current line. It has not been published to PyPI. |
| 0.7.x | Superseded by 0.8.x. It was not published to PyPI. |
| 0.6.x | Superseded by 0.7.x. It was not published to PyPI. |
| 0.5.x | Superseded by 0.6.x. It was not published to PyPI. |
| 0.4.x | Superseded by 0.5.x. It was not published to PyPI. |
| 0.3.x | Superseded by 0.4.x. It was not published to PyPI. |
| 0.2.x | Superseded by 0.3.x. It was not published to PyPI. |
| 0.1.x | Superseded by 0.2.x. It was not published to PyPI. |

## Reporting

Report a vulnerability privately to the maintainers. After the repository is public, use its private security advisory flow. Do not open a public issue for an unfixed vulnerability, and do not include exploit payloads in a public place.

Please include the version, the phase of the project you exercised, and a data directory that contains no real secrets.

## What 0.8.0 enforces

- The core does not spawn a shell. It opens a network connection only when the application selects `HttpChatProvider` or calls `serve`.
- The HTTP API defaults to `127.0.0.1`. Any other host is refused unless `allow_remote` is set. There is no authentication. Do not expose it on a shared network.
- A plugin can register a tool, a health check, or an event handler. It cannot grant a permission. A tool it registers stays denied until the application calls `allow`.
- Plugin records are not written to the database. A failed plugin does not stop the process.
- Metadata rejects keys that look like tokens, passwords, or API keys.
- The data directory is created with mode `0700` and the database file with mode `0600` where the platform honors those modes.
- The filesystem root cannot be used as a data directory.
- Lifecycle changes are written with the audit row and checkpoint in one transaction.
- A second process cannot claim a runtime whose recorded pid is still alive.
- Audit text is forced onto one printable line.
- List APIs reject limits outside 1..1000.
- Health checks cannot call lifecycle methods.
- There is no unrestricted file or shell tool.
- The default heartbeat policy never requests reasoning. A reasoner runs only when an application policy asks and only for `reason_timeout` seconds (default 5, maximum 120).
- Heartbeat intervals below 0.01 seconds are rejected. Consecutive tick failures stop the heartbeat after `heartbeat_max_failures` (default 5) without stopping the process.
- Tick text is forced onto one printable line. Tick history keeps at most 50 rows.
- Event types and sources are short lowercase names. Payloads are string maps and reject secret-looking keys.
- At most 100 subscriptions and 100 pending events are accepted. Finished events keep the newest 100 rows. Pending rows are not trimmed away.
- A handler runs on a worker thread for at most `event_handler_timeout` seconds (default 1, range 0.05..30). A timeout or exception marks that event failed and does not stop the process.
- Subscriptions are not stored. Restarting the process drops handlers. It does not drop the event log.
- Memory keys are short lowercase names. Values, summaries, and fact content are single-line printable text of at most 500 characters. Payloads reject secret-looking keys.
- Working memory holds at most 32 keys. Semantic memory holds at most 200 facts. Episodes keep the newest 200 rows. A full store rejects a new key instead of deleting one silently.
- Search reads stored text. It does not send that text to a model.
- No model provider is selected until the application calls `brain.use`. The library does not read API keys from the environment.
- `HttpChatProvider` sends `api_key` only when the caller passes one. Base URLs must be `http` or `https` and must not embed credentials.
- Model calls time out after `model_timeout` seconds (default 30, range 0.05..120). Usage rows keep provider, model, token counts, and a clipped error. They do not keep the prompt.
- The CLI `complete` command reads `AI_LIFE_ENGINE_API_KEY` only when `--provider openai-compatible` is selected.
- Goals, plans, steps, and reflections are stored text. Do not put secrets in them. `next_action` returns that text and does not run it. `propose` calls a model only when the application calls it.
- Every tool is denied until `allow` grants its permission in this process. Grants are not written to the database. Built-in tools do not open a shell, read files, or call the network. A tool-call row stores a clipped result, so do not pass a secret as an argument.

## What 0.8.0 does not enforce yet

The HTTP API does not authenticate callers. `--allow-remote` and the Docker command bind beyond loopback. Anyone who can reach that port can read state and call a tool this process already granted.

There is no human approval step, rate limit, or per-tool timeout. Do not treat a grant as safe for a tool that has external side effects.

A recycled pid can look alive and block a restart. This version will not force that lock open.

A custom reasoner is application code. The core does not sandbox it beyond the timeout and the rule that it must not call lifecycle methods. Do not point a reasoner at a shell or a network client and treat the heartbeat as authorization.

An event handler is application code under the same limit. The timeout does not cancel work the handler already started in another thread. Do not treat a subscription as permission to read secrets out of the payload or to call a shell.

SQLite files are only as private as the directory permissions and the account that runs the process. Do not put secrets in metadata or in the database by other means.

## Shutdown

`stop()` and the context manager record a clean shutdown. `run()` handles SIGINT and, on POSIX, SIGTERM. A kill that cannot be caught leaves the record active so the next start can recover it. The destructor does not rewrite that record into a fake clean stop.
