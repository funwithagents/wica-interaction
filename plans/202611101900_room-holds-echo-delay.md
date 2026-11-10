# The room holds its echo's delay

**Status:** In progress

Implements the settled behavior in `specs/e2e.md` ("The room", "The echo's delay holds"): the end-to-end room keeps the app's echo at its 50 ms delay for the whole run, against the drift between its recording and playback streams and a late recording block. It leaves out drift on purpose (a room variant for two devices), which the spec defers.

## Scope

- `tests-e2e/room.py` — the `Mixer` holds its echo backlog at the delay: a running average of the backlog, a one-sample resampled correction per block while it is more than 0.5 ms off, a cut or pad when it is more than 20 ms off; the old cut-back past 150 ms (`ECHO_SLACK_S`) goes; the room's log counts the hard corrections.
- `specs/e2e.md` — back to `Implemented` once done.

## Steps

1. In `Mixer`, replace the backlog cap in `heard` with a hold in `block`: `heard` takes the block's ADC time and `block` the DAC time of its first sample, so the echo's delay is measured from recording to playback (the backlog alone moves with the streams' phase). Lock the delay held after 100 blocks; track it with two averages (weights 0.01 and 0.03 per block), each adjusted by the room's own corrections so they never chase them; per block take `n + 1`, `n − 1` or `n` echo samples by the slow average's error (±24 samples at 48 kHz) and resample them to `n` (linear); when the fast average is more than 960 samples over, cut the whole excess as measured now and count it. Never pad a shortfall at once.
2. The room's log: the `rec` rows' last column becomes the count of hard corrections so far (it was "cut"); the module docstring says so.
3. Check offline: drive the `Mixer` with a recording stream 400 ppm faster than its playback, and with a 90 ms late block, and read the delay of what it plays back (cross-correlation): it holds within a millisecond, and the late block is corrected at once.
4. One CI run: the room's log shows the backlog flat at the delay, and the echo's delay measured on the app's side (`analysis/echo_bench.py track`) no longer creeps.

## Verification

`uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest`; the offline check of step 3; and one manual CI run (`gh workflow run ci.yml --ref <branch>`) whose computer evidence shows the delay held. Mark this plan `Done` (here and in [_index.md](_index.md)) and `specs/e2e.md` back to `Implemented` once all pass.
