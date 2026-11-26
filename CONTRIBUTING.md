# Contributing

AI Life Engine is developer infrastructure. Changes should stay model-agnostic, keep the core small, and ship with tests.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m ruff check src tests examples
python -m ruff format --check src tests examples
python -m pytest
```

Python 3.12 or newer is required. The CI workflow runs the same checks on Linux, macOS, and Windows for 3.12 and 3.13.

## What to work on

Phases 1 through 8 are implemented. `engine.heartbeat.wake()` is still only a signal to the scheduler. The default reasoning policy does not call a selected model. A heartbeat tick does not call a tool. Plugins load only when asked, and they cannot grant a permission. Do not add a shell tool or import a vendor agent framework from `ai_life_engine`. Do not add authentication by importing a web framework. The local API stays on the standard library.

Do not add a provider SDK, a web framework, or a database driver to the default install. Optional integrations get an extra in `pyproject.toml` only after their tests pass. Empty extras are not accepted.

Do not import OpenAI, Anthropic, Ollama, LangGraph, CrewAI, AutoGen, FastAPI, Redis, or a PostgreSQL client from `ai_life_engine` until that integration is a reviewed phase of its own.

## Pull requests

- Describe the behavior change and the tests that cover it.
- Keep public API changes compatible, or call out the break. 0.8.0 adds `engine.plugins`, the local HTTP API, and CLI commands. Adding a field is easier to absorb than renaming one.
- Do not commit secrets, local database files, or `.ai-life-engine/` directories.
- Update `docs/api_reference.md` and `CHANGELOG.md` when the public behavior changes.
- Prefer the `StateStore` and health-check boundaries over a new concrete dependency.

## Reporting issues

Use the bug and feature templates. Include the Python version, operating system, and a data directory that contains no private data. A failing test is the most useful report.

Security reports follow [SECURITY.md](SECURITY.md). Do not file a public issue for an unfixed vulnerability.

## License

By submitting a contribution you agree it is licensed under the Apache License 2.0, the license of this repository.
