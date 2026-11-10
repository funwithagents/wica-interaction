# The room holds its echo's delay

**Status:** Todo

Implements the settled behavior in `specs/e2e.md` ("The room", "The echo's delay holds"): the end-to-end room keeps the app's echo at its 50 ms delay for the whole run, against the drift between its recording and playback streams and a late recording block. It leaves out drift on purpose (a room variant for two devices), which the spec defers.

## Scope

- `tests-e2e/room.py` — the `Mixer` holds its echo backlog at the delay: a running average of the backlog, a one-sample resampled correction per block while it is more than 0.5 ms off, a cut or pad when it is more than 20 ms off; the old cut-back past 150 ms (`ECHO_SLACK_S`) goes; the room's log counts the hard corrections.
- `specs/e2e.md` — back to `Implemented` once done.

## Steps

1. In `Mixer`, replace the backlog cap in `heard` with a hold in `block`: track the average backlog (weight 0.01 per block, about a second), adjusted by each correction the room makes itself so it never chases its own steps; per block take `n + 1`, `n − 1` or `n` echo samples by the average's error against the delay (±24 samples at 48 kHz) and resample them to `n` (linear); beyond ±960 samples, drop the oldest or pad silence to the delay at once and count it.
2. The room's log: the `rec` rows' last column becomes the count of hard corrections so far (it was "cut"); the module docstring says so.
3. Check offline: drive the `Mixer` with a recording stream 400 ppm faster than its playback, and with a 90 ms late block, and read the delay of what it plays back (cross-correlation): it holds within a millisecond, and the late block is corrected at once.
4. One CI run: the room's log shows the backlog flat at the delay, and the echo's delay measured on the app's side (`analysis/echo_bench.py track`) no longer creeps.

## Verification

`uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`; the offline check of step 3; and one manual CI run (`gh workflow run ci.yml --ref <branch>`) whose computer evidence shows the delay held. Mark this plan `Done` (here and in [_index.md](_index.md)) and `specs/e2e.md` back to `Implemented` once all pass.
