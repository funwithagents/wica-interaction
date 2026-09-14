# Adopt WICA's output setters and `wica.contrib.gradio` panels

**Status:** Done

Implements the settled behavior in `specs/app.md` ("What the user sees", "Wiring and lifecycle", "No credentials, no robot", "Testing") and `specs/project.md` ("Runtime dependencies"). Catches the app up with two merged WICA improvements: `Wica.init` no longer takes `output_sink` / `output_command` (they are wired afterwards through `wica.set_output_sink` / `wica.set_output_command`), and the three observability panels (Conversation / World state / Prompt) now ship in WICA as reusable `wica.contrib.gradio` presenters + panels behind the `wica[gradio]` extra. The app drops its own `PanelState` reimplementation, places the contrib panels, and recomposes `main()` as the demo's **build → UI → wire → start → launch → close**. It deliberately leaves the ASR panel, the typed-text hatch, the degrade axes and the teardown sequence as they are.

> **Why now.** The repo is already broken against the merged WICA: `uv run pytest` fails at `Wica.init(... output_sink=...)` (`TypeError`) and `uv run pyright` reports the same two kwargs plus `CommandIssued` now requiring `call_id` (in `tests/test_app_ui.py`). This plan is the fix, not just a refactor.

## Scope

- `pyproject.toml` — `wica[anthropic]` → `wica[anthropic,gradio]`; drop the direct `langchain-core` dependency (it was declared only because `app_ui.py` imported `langchain_core.messages` to render the Prompt panel, which now lives in the contrib) — keep it only if a direct import remains after step 3 (`grep -rn langchain_core src tests tests-e2e`).
- `src/reachy_mini_interaction_wica/app.py` — `Wica.init(config.wica, loop=loop)`; `build_system` builds and enters but wires nothing; new `wire(system, transcript)`; `AppSystem` loses `panels`; `_serve(system, blocks)`; `main()` in the build → UI → wire → start order.
- `src/reachy_mini_interaction_wica/app_ui.py` — delete `PanelState`, `_RenderGate`, `_describe_trigger`, `_format_value`, `_flatten_content`, `_render_message`, `tick_world`, `_world_rows`, `_world_html` and their constants; add the `display_entry` hook and the `AppUi` handle; `build_ui(system) -> AppUi` places `conversation_panel` / `world_state_panel` / `prompt_panel` next to the app's ASR panel.
- `tests/test_app.py` — every flow goes through `wire()` with a test-built `TranscriptLog`; prompt/transcript assertions read the contrib presenters' `snapshot()`s; new tests for the sink wiring and the no-agent `wire` no-op.
- `tests/test_app_ui.py` — drop the `PanelState` / `_RenderGate` tests; keep the ASR status-line test; add `display_entry` tests.
- `AGENTS.md` (already updated: the `app_ui.py` map row), `specs/_index.md`, `specs/app.md`, `specs/project.md`, `plans/_index.md` — statuses at the end.

## Steps

1. **Dependency.** In `pyproject.toml` change `"wica[anthropic]"` to `"wica[anthropic,gradio]"` and update its comment (the `gradio` extra is not a provider: it makes `wica.contrib.gradio` importable; core `wica` never imports Gradio). Remove `"langchain-core"` and its comment (re-add only if step 3 leaves a direct import). Run `uv sync --dev` (uv re-locks; commit `uv.lock`). Verify `uv run python -c "import wica.contrib.gradio"` works.

2. **`app.py` — build without wiring.**
   - Remove the `PanelState` import and the `panels` field of `AppSystem` (and from `_degraded_system`); update the module and class docstrings: the build order is now robot → voice → **wica → bridge**, the output channel is wired in `wire`, and the UI owns the presenters.
   - Replace the `Wica.init(...)` call with `Wica.init(config.wica, loop=loop)` (same three `except` clauses, unchanged messages).
   - Keep `wica.world.register("speech_input", ...)` in `build_system` (a World-schema operation, like the demo's `register_world`). Remove from `build_system` the `register_command` calls and the three `wica.on_agent_*.subscribe(panels.*)` lines.
   - Add, next to `_speech_input`, nothing new — the person-facing `display_entry` hook lives in `app_ui.py` (step 3); a one-line comment on `_speech_input` points at it.
   - Add:
     ```python
     def wire(system: AppSystem, transcript: TranscriptLog) -> None:
         """Wire the output channel and the Commands onto the agent, before start(). Gradio-free."""
         wica = system.wica
         if wica is None:
             return  # no agent: nothing to wire into (the banner already says why)
         wica.set_output_sink(transcript.output_sink)
         if system.voice is not None:
             wica.set_output_command(system.voice.say)
         if system.bridge is not None:
             wica.register_command(system.bridge.start_dictation)
             wica.register_command(system.bridge.stop_dictation)
         if system.api is not None:
             controls = RobotControl(system.api)
             for command in (
                 controls.list_emotions,
                 controls.play_emotion,
                 controls.start_head_tracking,
                 controls.stop_head_tracking,
                 controls.get_motors_state,
                 controls.set_motors_state,
             ):
                 wica.register_command(command)
     ```
     (`from wica.contrib.gradio import TranscriptLog` — a runtime import is fine: the extra is a hard dependency now.)
   - `_serve(system, blocks)`: `try: _start(system); blocks.launch()` / `finally: _shutdown(system)` — the page is now built *before* start, so it is passed in rather than built inside.
   - `main()`: after `system = build_system(config)` (or `_degraded_system`), `ui = build_ui(system)`, `wire(system, ui.transcript)`, `_serve(system, ui.blocks)`.

3. **`app_ui.py` — place the contrib panels.**
   - Imports: drop `queue`, `threading`, `datetime`, `langchain_core.messages` and `CommandIssued` (keep `html` — the banner still escapes with it); add `from dataclasses import dataclass` and `from wica.contrib.gradio import EntryDisplay, PromptLog, TranscriptLog, conversation_panel, prompt_panel, world_state_panel`; keep `CommandExecution` and `WorldEntry` from `wica`.
   - Delete `PanelState`, `_RenderGate`, `_describe_trigger`, `_format_value`, `_flatten_content`, `_render_message`, `tick_world`, `_world_rows`, `_world_html`, `_OUTPUT_COMMAND_NAME`, `_NOOP_COMMAND_NAME`, `_NO_PROMPT_YET`, `_WORLD_*`. Keep `_NO_LIVE_SPEECH` and `tick_asr` unchanged.
   - Add the hook (spec "What the user sees", "The `display_entry` hook"):
     ```python
     def display_entry(entry: WorldEntry) -> EntryDisplay | None:
         value = entry.current.value
         if entry.key == "speech_input":
             return EntryDisplay(f'🗣️ "{value}"')
         if isinstance(value, CommandExecution) and value.name == "say":
             return EntryDisplay("🗣️ say", str(value.args.get("text", "")))
         return None
     ```
   - Add `@dataclass(frozen=True) class AppUi: blocks: gr.Blocks; transcript: TranscriptLog`.
   - `build_ui(system) -> AppUi`: build `transcript = TranscriptLog(system.wica, display_entry)` and `prompts = PromptLog(system.wica, display_entry)` *before* the `gr.Blocks` context; inside it keep the banner, intro text, ASR panel, typed-text hatch and the "Show debug panels" checkbox exactly as today, but: `conversation_panel(transcript)` replaces the `gr.Chatbot` + `conv_timer`; inside the debug column, `world_state_panel(system.wica.world)` when `system.wica is not None` else `gr.Markdown("_No agent — there is no World to show._")`, then `prompt_panel(prompts)`; the toggle's handler becomes `lambda show: gr.update(visible=show)` on the debug column only (no timer output, no gate — the panels own their timers). Update the intro Markdown to mention live Command state (spinner → ✅/❌/⏹ with result and duration). Return `AppUi(blocks=demo, transcript=transcript)`. Update the module docstring accordingly (presentation layer; presenters owned here and handed back for wiring; no `langchain_core` import).

4. **`tests/test_app.py`.**
   - Add a helper `_wire(system) -> tuple[TranscriptLog, PromptLog]` that builds both presenters over `system.wica` with `display_entry` (imported from `app_ui`) and calls `wire(system, transcript)`; every existing flow calls it between `build_system` and `_start`.
   - `_prompt_captured(system)` → takes the `PromptLog` and returns `log.snapshot().count > 0`; the empty-typed-text assertion reads `[m["content"] for m in transcript.snapshot().conversation if m["role"] == "user"]`.
   - New `test_free_text_lands_in_the_transcript_sink`: script `[{"text": "thinking out loud"}, noop]`; after a typed turn, wait until the transcript snapshot holds an item titled `💭 output sink` with that content — this is the only test that proves `set_output_sink` was wired.
   - New `test_wire_without_agent_is_a_noop`: a `_degraded_system("x")` (or a system whose `Wica.init` failed) — `wire(system, TranscriptLog(None, display_entry))` returns without raising.
   - `test_robot_action_command_is_registered_and_routes_to_the_robot` now proves `wire` registered the actions; keep its assertion, rename its docstring.
   - `test_shutdown_skips_never_started_bridge` and the teardown tests need no `wire` (nothing they assert depends on it) — leave them wire-free to keep proving `build_system` alone enters the robot.

5. **`tests/test_app_ui.py`.** Remove the `PanelState`/`_RenderGate` tests and the `langchain_core` / `CommandIssued` imports. Add `test_display_entry_labels_the_app_entries`: `speech_input` → `EntryDisplay('🗣️ "hi"')`; a `say` `CommandExecution` (`WorldEntry` with `key="agent:command:c1"`, value `CommandExecution(name="say", args={"text": "hi there"}, state="running")`) → `EntryDisplay("🗣️ say", "hi there")`; an `agent:command:c2` `play_emotion` execution and an unknown key → `None`. Keep `test_asr_status_line_flags_a_dead_mic_stream`, dropping the `panels=` field from its `AppSystem` construction.

6. **Docs + statuses.** Re-check the AGENTS.md map row (done) and `README.md` (done). Flip `specs/app.md` and `specs/project.md` back to `Implemented` (file + `specs/_index.md`) and this plan to `Done` (file + `plans/_index.md`).

## Verification

- `uv sync --dev` succeeds and `uv.lock` records the `gradio` extra for `wica`.
- `uv run ruff check .`, `uv run ruff format .` (commit its rewrites), `uv run pyright` — clean (in particular the two `Wica.init` kwarg errors and the `call_id` error are gone).
- `uv run pytest` — all green, including: typed text and a closed segment still reach the robot speaker via `say` (proves `set_output_command`), a robot action still reaches the robot (proves `wire` registers commands), the new sink test (proves `set_output_sink`), the no-agent `wire` no-op, the `display_entry` cases, and the unchanged degrade/teardown tests.
- Manual smoke (optional, `reachy.backend = "fake"` + a real agent key): the Conversation shows the `say` item with a spinner then ✅ and its duration; the debug toggle reveals the World table and the prompt history; the ASR panel and typed text behave as before.
- Mark this plan `Done` (here and in [_index.md](_index.md)) only once all pass.
