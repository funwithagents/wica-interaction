# CI: the static gate and the fast tier

**Status:** In progress

Implements [specs/ci.md](../specs/ci.md): one GitHub Actions workflow running lint, format and type checks on Linux and the fast tier on Linux, macOS and Windows, on every pull request and push to `main`. Leaves out the live tier's jobs, which come with the end-to-end work ([analysis/202611091830_e2e-full-pipeline.md](../analysis/202611091830_e2e-full-pipeline.md), §14).

## Scope

- `.github/workflows/ci.yml` — new: the `check` and `fast-tier` jobs.
- `specs/ci.md` — new: the CI design; `specs/_index.md` gains its row.
- `specs/testing.md` — the "CI wiring" open question is answered by the new spec; the "today all testing is local" line goes.
- `specs/project.md` — the repository shape names `.github/workflows/` and `analysis/`.
- `specs/reachy-embodiment.md`, `tests/reachy/test_config.py` — the template test loads the one committed Reachy template. It named a French template that was never committed, so the committed tree failed its own fast tier.
- `AGENTS.md` — the top-level layout names `.github/` and `analysis/`.
- `analysis/202611091830_e2e-full-pipeline.md` — new: the end-to-end analysis this CI design comes from.
- `plans/_index.md` — this plan's row.

## Steps

1. Drop the uncommitted French template from `test_the_committed_templates_load` (now `test_the_committed_template_loads`) and from the Reachy spec's testing line.
2. Write `specs/ci.md` (Stable) and add its row to `specs/_index.md`.
3. Write `.github/workflows/ci.yml`: triggers on `pull_request`, `push` to `main` and `workflow_dispatch`; one run per ref; the Linux system packages in one `APT_PACKAGES` variable; `check` on `ubuntu-24.04`; `fast-tier` on `ubuntu-24.04`, `macos-15` and `windows-2025` with `fail-fast: false`; `uv sync --locked` with the uv cache keyed on `uv.lock`; a timeout per job.
4. Update `specs/testing.md`, `specs/project.md` and `AGENTS.md` as listed in Scope.
5. Run the local gate, push the branch, open the pull request and read every job's log. Fix what a platform reveals: a missing system package, a wheel that does not exist, a test that assumes a POSIX path or a fast clock.
6. Once every job is green, mark this plan `Done` and the spec `Implemented`, here and in both indexes.

## Verification

- Locally: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`, all passing.
- On GitHub: the four jobs green on the pull request — `check`, and `fast-tier` on Linux, macOS and Windows.
