# CI cache keyed on the source builds

**Status:** In progress

Implements the settled behavior in `specs/ci.md` ("The workflow", the "Caches" bullet): the uv cache is keyed on the locked entries of the sdist-only packages instead of the whole `uv.lock`, so a lock change that does not touch them — a sibling bump — no longer rebuilds `aec-audio-processing` (about two minutes on Linux and macOS) on every job. It leaves out publishing wheels for `aec-audio-processing` upstream, which would remove the build for users too.

## Scope

- `.github/workflows/ci.yml` — a `SOURCE_BUILDS` pattern in `env`; in both jobs, a step writing the matching `uv.lock` entries to `.ci-source-builds`, and `setup-uv`'s `cache-dependency-glob` pointed at it
- `specs/ci.md` — the "Caches" bullet, and the echo-cancellation line now naming macOS (`Updated` until this plan is `Done`)

## Steps

1. Add `SOURCE_BUILDS: '^name = "(aec-audio-processing|pygobject|pycairo)"'` to the workflow's `env`, with the reason in its comment.
2. Before `setup-uv` in `check` and `fast-tier`, a `bash` step (Windows included) running `grep -A3 -E "$SOURCE_BUILDS" uv.lock > .ci-source-builds`.
3. Set `cache-dependency-glob: .ci-source-builds` in both `setup-uv` steps.

## Verification

- Local: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`.
- On GitHub: the first run on the branch misses (new key) and saves; a re-run hits, with the sync step down to seconds on every job. After merge, a lock-only change (a sibling bump) still restores the cache.

Mark this plan `Done` (here and in [_index.md](_index.md)), and `specs/ci.md` back to `Implemented`, once a run shows the hit.
