# Drop `mute` and hold a sign for `hold_s` before it counts

**Status:** Done

Implements the settled behavior in `specs/gesture-input.md` ("The reduction: label → sign, per hand" — `mute` unmapped; "The hold filter: a value must be visible for `hold_s`") and the new `vision.hold_s` key in `specs/configuration.md`. Delivers a smaller vocabulary and a per-hand, time-based, strictly-consecutive hold before any change (a sign going away included, `stop` included) reaches the World. Deliberately leaves out any cross-hand logic (two-hand counting is a separate, later design) and any majority-vote or per-sign hold.

## Scope

- `src/wica_interaction/gesture_input.py` — remove `mute` from `SIGNS` and the persona text; add `HoldFilter` (pure, per hand); the watcher runs each slot's value through its filter and delivers only what clears it; `hold_s` kwarg (default 0.5, must be `>= 0`), echoed in `status()`.
- `src/wica_interaction/config.py` — `VisionConfig.hold_s` (default 0.5, number `>= 0`), an accepted `vision` key.
- `src/wica_interaction/system.py` — pass `cfg.vision.hold_s` to `GestureInput`.
- `tests/test_gesture_input.py` — `HoldFilter` unit tests with explicit times; real-clock hold tests through `GestureInput`; existing tests run at `hold_s=0`; vocabulary test drops `mute`.
- `tests/test_config.py` — `hold_s` carried, defaulted, and validated.
- `tests/test_system.py` — `hold_s` reaches the gesture input; vision tests run at `hold_s=0`.
- `tests-e2e/test_gesture_input_live.py` — wait past the default hold before reading deliveries.
- `config.*.example.json`, `README.md`, `specs/*` samples — the new key; `mute` gone from every vocabulary listing.

## Steps

1. Spec: `mute` out of the table and the persona; the hold-filter section; `hold_s` in the API, `status()`, app integration and testing; open question 2 resolved. `configuration.md`: the key. Done alongside this plan.
2. `config.py`: `hold_s` in `_VISION_KEYS`, the dataclass, `from_dict` (number, `>= 0`, stored as float).
3. `gesture_input.py`: `HoldFilter` with `value` (last delivered) and `observe(value, now) -> bool`; `GestureInput(hold_s=0.5)` builds one per slot, validates `hold_s >= 0`, and `_watch` stamps `time.monotonic()` once per result, observes every slot, and delivers each slot whose filter returned `True`. `_signs` stays the delivered values (`status()["signs"]`). Drop `mute` from `SIGNS` and `_HAND_SIGNS_TEXT`.
4. `system.py`: forward `hold_s`.
5. Tests as scoped; the example configs and docs.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` all clean. Mark this plan `Done` (here and in [_index.md](_index.md)) and the gesture-input spec back to `Implemented` only once all pass.
