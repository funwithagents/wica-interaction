# Catch up with the bridge: `motion.tracking` is an object

**Status:** Done

Implements [specs/reachy-embodiment.md](../specs/reachy-embodiment.md) ("Configuration") against reachy-mini-bridge `dcb55de`, the commit that brings the sim props and the sim robot's audio devices the end-to-end tier needs. Since the bridge's tracking posture (mirroring, approach, personal space), `motion.tracking` is an object, `{"enabled": true}`, and the old boolean is a `ConfigError`. The template, the tests and the docs follow; the posture's own settings stay at the bridge's defaults.

## Scope

- `uv.lock` — reachy-mini-bridge `46536ee` → `dcb55de`, nothing else.
- `config.reachy.example.json` — `"tracking": { "enabled": true }`.
- `tests/reachy/test_config.py` — the tracking tests write and read the object; a new test pins that a boolean `tracking` is rejected at load with the bridge's hint.
- `specs/reachy-embodiment.md` — the `motion` line, the template excerpt and the testing line name the object.
- `docs/reference/configuration.md`, `docs/guides/reachy-mini.md`, `docs/guides/troubleshooting.md` — the template excerpt, the `motion` row, the load errors, the tracking guide, and a troubleshooting entry for a config still using the boolean.

## Steps

1. `uv lock --upgrade-package reachy-mini-bridge`, `uv sync --dev`; the fast tier shows the three tests and the template the bump breaks.
2. Write `tracking` as an object in the template and the tests; add the boolean-rejection test.
3. Update the spec and the docs as listed in Scope.

## Verification

- `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`, all passing.
- CI green on the pull request.
