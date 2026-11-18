# Faster hand signs

**Status:** Done

Implements the settled behavior in `specs/gesture-input.md` ("The reduction", "The hold filter", "`StreamFeed`"), `specs/configuration.md` ("The `vision` section") and `specs/computer-embodiment.md` (the webcam's pacing): a sign reaches the World in about half the time. Leaves out the two sampling waits inside `vision-modules`' stages (gesture-input.md, open question 7) and a `stop` that bypasses coalescing (open question 6).

## Scope

- `src/wica_interaction/gesture_input.py` — the watcher polls the classifier's slot every 10 ms instead of at `fps`; `HoldFilter` takes a second hold for a release; `GestureInput` gains `release_hold_s`, echoes it in `status()`, and defaults to 10 fps and a 0.3 s sign hold
- `src/wica_interaction/config.py` — `VisionConfig.release_hold_s` (default 0.5, validated `>= 0`); defaults `fps` 10, `hold_s` 0.3
- `src/wica_interaction/system.py` — passes `release_hold_s` to `GestureInput`
- `src/wica_interaction/computer/embodiment.py` — `WebcamSource.read()` waits before it reads the device, not after
- `config.computer.example.json`, `config.reachy.example.json`, `tests-e2e/configs/*.json` — the new defaults, written out
- `tests/test_gesture_input.py`, `tests/test_config.py`, `tests/test_system.py`, `tests/computer/test_embodiment.py` — functional tests for the above
- `docs/reference/configuration.md`, `docs/guides/hand-signs.md`, `docs/guides/troubleshooting.md`, `docs/guides/computer.md` — the new field, the defaults, the tuning notes
- `specs/gesture-input.md`, `specs/configuration.md`, `specs/computer-embodiment.md`, `specs/app.md`, `specs/_index.md` — the design

## Steps

1. Watcher: replace the per-`fps` wait with a fixed `_WATCH_INTERVAL_S` (10 ms).
2. Hold: `HoldFilter(hold_s, release_hold_s=None)` picks the hold by the candidate (`None` → the release's); thread `release_hold_s` through `GestureInput`, `VisionConfig` and `build_system`.
3. Webcam: keep the time the next poll is due; `read()` waits for it, then reads and returns at once; `open()` resets it.
4. Defaults: `fps` 10, `hold_s` 0.3, `release_hold_s` 0.5, in code, templates, e2e configs, docs and specs.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest`. New tests: the two holds on `HoldFilter` alone (a sign, a switch, a release, a dip between the two holds), the status echoing both, a result delivered inside the period with stages at 1 fps, the webcam's first read immediate and its second fresh, the config's new field and defaults. The live tier (`tests-e2e`) was not run for this plan.
