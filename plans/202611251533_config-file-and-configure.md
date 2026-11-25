# Running from a config file, and the `configure` command

**Status:** In progress

Implements [specs/setup.md](../specs/setup.md) ("Three launches"), with the entry point of [specs/app.md](../specs/app.md) ("Wiring and lifecycle", step 0), the autostart from a file of [specs/session.md](../specs/session.md) ("Autostart") and the flags of [specs/project.md](../specs/project.md) ("Entry points"). Both apps take `--config FILE` and `--env PATH` alongside `--folder`, and a `configure` subcommand serves the Setup and Secrets tabs with no session. It builds on [202611241957_sessions-and-setup-page.md](202611241957_sessions-and-setup-page.md) and on app-setup's config file source, still on its `config-file` branch. It leaves out a bare app (the apps keep one key per section) and an example config file beside the default configs.

## Scope

- `pyproject.toml`, `uv.lock` — app-setup on its `config-file` branch, back to `main` once that branch is merged
- `src/wica_interaction/system.py` — `run_app`: `add_config_arguments` / `open_source`, a missing file as `parser.error`, `ensure_persona` for a folder only; `CONFIGURE` and `_configure` (app-setup's `config_page` over a writable `ConfigFile` or the folder)
- `tests/test_system.py` — the launches from a file, `--env`, a missing file, `--folder` with `--config`, `configure` over a file and over the default folder
- `tests/computer/test_app.py`, `tests/reachy/test_app.py` — the default config run as a file, its persona next to it; `config_page` built over a file and over the folder
- `specs/setup.md`, `specs/app.md`, `specs/session.md`, `specs/project.md`, `specs/configuration.md`, `specs/computer-embodiment.md`, `specs/reachy-embodiment.md` — the three launches
- `README.md`, `docs/index.md`, `docs/getting-started.md`, `docs/reference/configuration.md` ("Three launches"), `docs/guides/computer.md`, `docs/guides/reachy-mini.md`, `docs/guides/personas.md`, `docs/guides/troubleshooting.md` — the launches and where the `.env` is
- `AGENTS.md` — the project map's `system.py` row

## Steps

1. **app-setup's branch.** `rev = "config-file"` with a comment, `uv lock --upgrade-package app-setup`, `uv sync --all-groups`. The existing tests pass `AppModel`'s source positionally, so the `folder` → `source` rename needs no change.
2. **The launch from a file.** `run_app` parses with `add_config_arguments(parser, app)`, `--host`, `--port`, `add_start_arguments`; `open_source(options, app)`, a `ConfigFileError` becoming `parser.error`; the `AppModel` over the source, locked by the session; `ensure_persona(model.path)` only for a `ConfigFolder`; `starts_at_launch` decides as before (from a file: yes, unless `--no-start`).
3. **`configure`.** A first argument `configure` runs `_configure(app, ensure_persona, rest)`: its own parser (`--folder DIR` | `--config FILE`, `--env`, `--host`, `--port`), a writable `ConfigFile` for `--config` (a missing file a `parser.error`), else the `ConfigFolder` and its persona; `config_page(AppModel(app, source)).launch(...)`. The plain launch's help names the subcommand in its epilog.
4. **Tests** as Scope, the page and `config_page` replaced by recorders in `tests/test_system.py`; the real `config_page` built per app.
5. **Specs and docs** as Scope. The configuration reference owns the launches ("Three launches"); the other pages link there.
6. **By hand, both apps.** `--config` on a copy of the folder's `config.json` with the persona next to it: the session starts, the Setup tab read-only; `--config` on a missing file stops with the message; `configure` over the folder and over a file, Save written back to the right one, the Secrets tab saving to the `.env` next to it (or to `--env`).

## Verification

- `uv run ruff check .`, `uv run ruff format src tests tests-e2e`, `uv run pyright`, `uv run pytest`.
- The by-hand run of step 6.
- Before merging: app-setup back on `rev = "main"` once its config file branch is merged; then this plan `Done`, with the plans it builds on.
