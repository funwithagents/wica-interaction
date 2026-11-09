# The `reachy-sim` extra

**Status:** Done

Implements the settled behavior in [specs/project.md](../specs/project.md) ("Runtime dependencies — a base plus extras", "The `dev` group"): the MuJoCo simulator becomes an extra of its own, `reachy-sim`, mirroring the bridge's `sim` extra under its name, so an install that runs the reachy template as committed (its `backend` is `sim`) selects it by name instead of installing the whole `dev` group. It leaves the `reachy` extra as the robot alone — a deployment on a real robot needs no simulator — and changes no code: the gap was in the packaging and the pages that describe it.

## Scope

- `pyproject.toml` — the `reachy-sim` extra (`reachy-mini-bridge[sim]`), added to `all`; the `dev` group pulls `wica-interaction[reachy-sim]` instead of the raw `reachy-mini-bridge[sim]`
- `uv.lock` — re-resolved for the new extra (no sibling commit moves)
- `specs/project.md` — the `reachy-sim` row of the extras table, the `dev` group paragraph
- `docs/reference/install.md` — the recipes (the template's, in the simulator; the same on a real robot) and the extras table row, which `tests/test_docs.py` checks against `pyproject.toml`
- `README.md`, `docs/getting-started.md`, `docs/guides/reachy-mini.md`, `docs/guides/troubleshooting.md` — every "the simulator needs `uv sync --dev`" becomes the extra's name

## Steps

1. Declare `reachy-sim = ["reachy-mini-bridge[sim]"]` next to `reachy`, add it to `all`, and have `dev` reference it through the self-extra, so the development environment and a consumer install get the simulator the same way.
2. `uv lock` (no `--upgrade`), then `uv sync --dev`.
3. Add the spec row and reword the `dev` group paragraph; add the install reference's row and recipes; replace the dev-group pointers in the README and the guides.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` — `test_docs.py::test_the_install_page_lists_every_extra` is the one that pins the new extra's documentation. A `uv sync --no-dev --extra reachy --extra reachy-sim` install carries `mujoco`; one with `--extra reachy` alone does not.
