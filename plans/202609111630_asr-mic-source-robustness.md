# ASR mic-source robustness

**Status:** Done

> **As built — deviation from step 3.** Rather than having the bridge read its engine's audio source
> behind an `isinstance` guard (`ASREngine` keeps it in the private `_audio_source`, so that would be
> the same sibling-internals coupling the analysis flags), `AsrBridge` takes the same `RobotMicSource`
> through an optional `mic=` kwarg — the app builds one `mic` and hands it to both the engine and the
> bridge. Sizing: `_MIC_QUEUE_MAXSIZE = 200` (~2 s of the bridge's 10 ms / 160-frame capture blocks),
> overridable via a `maxsize=` kwarg (tests use 4). Also: `test_robot_mic_source_stop_cancels_pump` no
> longer peeks at `source._task` (it counts the fake stream's open iterators instead — an analysis nit),
> and `test_stream_end_is_recorded_and_a_restart_clears_it` pins the stream-end + reset path.

Implements the settled mic-source behavior in `specs/asr-bridge.md` ("Mic source: the robot's
echo-cancelled stream", "Status for the UI"). Fixes the silent mic-pump death (bug 4) and the two
mic-queue nits from `specs/_analysis.md` — the unbounded queue and the double-`start()` task leak — so
a stalled or ended robot stream is observable (logged + surfaced in ASR status) instead of masquerading
as "running", and memory stays bounded under a slow consumer. It touches only `RobotMicSource` and
`AsrBridge.status()`; the bridge's segment→World rule and dictation commands are unchanged.

## Scope

- `src/reachy_mini_interaction_wica/asr_bridge.py`:
  - `RobotMicSource` — bound the queue (drop-oldest), wrap the pump loop so a raise/normal-end is
    logged and recorded as a `capture_error`/stopped state, and cancel any live pump task before
    spawning a new one in `start()` (double-start safety). Expose a cheap read of the capture state.
  - `AsrBridge.status()` — fold the source's capture state into the dict the UI renders, so a dead
    pump shows up rather than `running: True` forever.
- `src/reachy_mini_interaction_wica/app_ui.py` — surface the capture-error/stalled state in
  `tick_asr`'s status line.
- `tests/test_asr_bridge.py` — tests for: a pump whose stream raises records the error and stops;
  the queue drops oldest under backpressure; a second `start()` cancels the first task.
- `specs/asr-bridge.md` — document the bounded drop-oldest queue, the pump-failure surfacing, and the
  double-start guard; `tests/test_app.py` unaffected.

## Steps

1. **Bounded, drop-oldest queue.** Give `RobotMicSource` a queue built with a `maxsize` (a module
   constant sized for a small backlog of ~16 kHz mono int16 audio, e.g. a couple of seconds' worth of
   chunks — state the figure in a comment). In `_pump`, when `put_nowait` would raise `QueueFull`,
   discard the oldest item (`get_nowait()`) and retry, so stale audio is dropped rather than growing
   memory without bound (real-time audio semantics: old audio is useless). Reset the queue with the
   same `maxsize` on each `start()`.

2. **Pump failure is observable (bug 4).** Add a private `self._error: str | None = None` (and reset
   it to `None` in `start()`). Wrap the `async for` in `_pump` so that a raised exception is caught,
   logged via `logger.exception`, and stored in `self._error`; a normal end of the async iterator
   (stream closed) is also recorded (a distinct sentinel message like `"mic stream ended"`). Add a
   `logger = logging.getLogger(__name__)` to the module. Expose the state, e.g. a
   `capture_error` property returning `self._error`. (Prefer surfacing over a done-callback: the
   error must be readable by `status()` on the app/UI thread.)

3. **`status()` reports capture health.** Extend `AsrBridge.status()` to include the source's capture
   state. Since `AsrBridge` only holds the `ASREngine` (which holds the source), thread the state
   through: give `RobotMicSource` its `capture_error` accessor and have the bridge read it **only when
   its engine's audio source is a `RobotMicSource`** (guard with `isinstance`, since the bridge is
   also constructed over fakes in tests). Add a `"capture_error": str | None` key (default `None`) to
   the returned dict. Keep `running`/`dictating`/`segmentation_mode` as-is.

4. **Double-start safety (nit).** At the top of `RobotMicSource.start()`, if `self._task` is not
   `None`, cancel it before creating the fresh queue/task, so a `start()` without an intervening
   `stop()` cannot orphan the previous pump task (the field is currently overwritten).

5. **UI surfacing.** In `app_ui.tick_asr`, when `status()["capture_error"]` is set, append it to the
   status markdown (e.g. `· **capture:** <error>`) so a user sees that capture stalled even though the
   engine still reports running. No behavior change when it is `None`.

6. **Spec update.** In `specs/asr-bridge.md`, extend the "Mic source" section to state the queue is
   bounded and drop-oldest, that a pump failure or stream-end is logged and surfaced through
   `status()` (and `start()` cancels any live task first), and add `capture_error` to the `status()`
   shape in "Status for the UI". The spec is `Implemented`; set it to `Updated` when editing and back
   to `Implemented` once this plan is `Done`.

## Verification

- New `tests/test_asr_bridge.py` cases (driven against `FakeMicApi`):
  - `test_pump_error_is_recorded_and_surfaced`: a `FakeMicApi.audio_input` that raises after a chunk
    leaves `source.capture_error` set; a bridge built over an engine using that source reports the
    error via `status()["capture_error"]`.
  - `test_queue_drops_oldest_under_backpressure`: fill past `maxsize` without draining and assert the
    queue never exceeds `maxsize` and retains the newest chunks (oldest dropped).
  - `test_second_start_cancels_the_first_pump`: `start()` twice without `stop()`; the first task is
    cancelled and only the second drains.
- `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass. Mark `Done` and flip
  `specs/asr-bridge.md` back to `Implemented` only once all three pass.
