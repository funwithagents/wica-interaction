# UI rendering efficiency & thread-safety

**Status:** Done

> **As built.** Beyond the steps: showing the debug panels also pushes a fresh World render (the
> `_RenderGate` is shared by every browser session, so without that push a newly opened tab could show
> a stale World until the next change), and `tests/test_app_ui.py` also covers the mic-source plan's
> `capture_error` in the ASR status line. `specs/app.md` stays `Updated` past this plan only because
> the test-hygiene plan edits it next.

Implements the settled panel behavior in `specs/app.md` ("What the user sees") more efficiently,
fixing the four UI "Bad patterns" from `specs/_analysis.md`: the World panel re-renders five times a
second with no change detection (even while hidden), `tick_conversation` rebuilds a signature over the
whole transcript each tick (unbounded work as a session grows), the debug timers keep firing while the
debug panels are toggled off, and `PanelState`'s change-tracking fields are mutated from Gradio worker
threads without stating or guarding the single-listener assumption. Behavior (what shows) is
unchanged; only how often/how much the views recompute and push changes.

## Scope

- `src/reachy_mini_interaction_wica/app_ui.py`:
  - a small reusable `_RenderGate` (returns whether a rendered value differs from the last) used to
    gate the World HTML push;
  - `tick_conversation` — replace the whole-transcript signature with an O(1) change check (append-only
    transcript ⇒ length is a sufficient change signal), and guard the `_last_*` bookkeeping with the
    existing `_state_lock`, with a comment stating the concurrency assumption;
  - `build_ui` — gate the World push through a `_RenderGate`; pause the World (debug) timer when the
    debug panels are hidden and resume it when shown (via the `show_debug` toggle), so a hidden panel
    does no periodic work.
- `tests/test_app_ui.py` — **new** file: functional tests for `_RenderGate` and
  `PanelState.tick_conversation`'s change detection (no Gradio browser needed).
- `specs/app.md` — note the change-detection + hidden-panel-pause behavior and the single-listener
  concurrency assumption for `PanelState`.
- `specs/app.md` frontmatter `tests:` — add `tests/test_app_ui.py`.
- `AGENTS.md` — no map change (no new module); leave as-is.

## Steps

1. **`_RenderGate` helper.** Add a tiny stateful class to `app_ui.py`:

   ```python
   class _RenderGate:
       """Returns True only when the rendered value differs from the last one it accepted."""

       def __init__(self) -> None:
           self._last: object = object()  # sentinel: first call always changes

       def changed(self, value: object) -> bool:
           if value == self._last:
               return False
           self._last = value
           return True
   ```

2. **Gate the World push.** In `build_ui`, create `world_gate = _RenderGate()` and replace the
   `world_timer.tick(lambda: tick_world(system), …)` callback with one that computes
   `html_now = tick_world(system)` and returns `html_now` only when `world_gate.changed(html_now)`,
   else `gr.update()` (no-op). This stops the World HTML from being re-sent every 0.2s when nothing
   changed, matching how `tick_conversation` already diffs before pushing the chatbot.

3. **Pause the World timer when hidden.** Start `world_timer = gr.Timer(0.2, active=False)` (debug
   panels are hidden by default). Extend the `show_debug.change` handler to drive both the panel
   visibility **and** the timer's `active` state (add `world_timer` to its outputs, returning
   `gr.update(visible=show)` and `gr.update(active=show)`). The ASR and conversation timers stay
   always-on (ASR is main surface; the conversation timer also drives the transcript). Note in a
   comment that the Prompt view is refreshed by `tick_conversation` (shared with the chatbot) and so
   is not separately paused — it already only pushes on a new step.

4. **O(1) conversation change check.** In `tick_conversation`, drop the
   `conv_sig = tuple(str(m.get("content", "")) for m in conversation)` full-transcript signature.
   Because the transcript is append-only (existing entries are never mutated — reaction children are
   appended, the group container keeps its empty content), `len(self._conversation)` is a sufficient
   change signal. Track `self._last_conv_len` and push the chatbot value only when the length grew.
   Remove the now-unused `_last_conv_sig` field.

5. **State the concurrency assumption / guard it.** Move the reads and writes of `_last_conv_len` and
   `_last_shown_count` inside the `with self._state_lock:` block (they are already computed from
   locked state), and add a one-line comment that these timer callbacks run under Gradio's default
   per-listener concurrency of one, and the lock makes the check-and-set safe regardless. Add
   `self._last_conv_len = 0` in `__init__` (replacing `_last_conv_sig`).

6. **Spec update.** In `specs/app.md` "What the user sees", add that the World view only re-renders on
   change and its timer pauses while the debug panels are hidden, and that `PanelState`'s
   change-tracking is guarded by its lock under the single-listener assumption. Add
   `tests/test_app_ui.py` to the app spec frontmatter `tests:` list. app.md is `Implemented`; set to
   `Updated` on the edit, back to `Implemented` once `Done`. (`tests/test_project_map.py` will then
   see the new test path exists — keep the frontmatter honest.)

## Verification

- New `tests/test_app_ui.py`:
  - `test_render_gate_only_reports_real_changes`: first value changes; an identical value does not; a
    new value does again.
  - `test_tick_conversation_pushes_only_on_new_events`: a fresh `PanelState`; drive `on_trigger` /
    `on_prompt` to enqueue events, call `tick_conversation` and assert the chatbot output is the
    conversation list on the tick that drained new events, and a `gr.update()` no-op on an immediately
    following tick with no new events. (Assert on the observable return, not private fields.)
  - `test_tick_conversation_snaps_prompt_selector_to_newest`: after an `on_prompt`, the selector
    update carries the new choice and selects the newest index; a following no-op tick does not
    re-push it.
- `uv run ruff check .`, `uv run pyright`, `uv run pytest` all pass (including
  `tests/test_project_map.py` with the updated frontmatter). Mark `Done` and flip `specs/app.md` back
  to `Implemented` only once all pass.
