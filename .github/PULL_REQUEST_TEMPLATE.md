## Summary

Describe the behavior change.

## Phase

- [ ] Phase 1 only, or name the later phase this change belongs to
- [ ] Tests cover the new behavior
- [ ] Docs and the changelog match the public API
- [ ] No provider SDK, shell tool, or secret was added
- [ ] No local database or data directory is committed

## Verification

```bash
python -m ruff check src tests examples
python -m ruff format --check src tests examples
python -m pytest
```
