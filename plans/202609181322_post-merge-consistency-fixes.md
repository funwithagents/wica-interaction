# Post-merge consistency fixes: one voice contract, a dictation reply that says how it ends, a tested mic hand-off

**Status:** Done

Implements the `Updated` design of [specs/embodiment.md](../specs/embodiment.md), [specs/computer-embodiment.md](../specs/computer-embodiment.md), [specs/reachy-embodiment.md](../specs/reachy-embodiment.md), [specs/asr-bridge.md](../specs/asr-bridge.md), [specs/app.md](../specs/app.md) and [specs/audio-processing.md](../specs/audio-processing.md), after a whole-repo consistency review of the [wica-interaction merge](202609172311_wica-interaction-merge.md). It delivers the six inconsistencies that review found between the specs and the code (and between the two embodiments), plus the small stale-text items. It adds no feature and leaves every open question of the specs open.

## What the review found (the why of each step)

1. **"No voice" meant two different things.** The robot keeps `say` wired and answers `"TTS error: …"`; the computer dropped the output Command (`voice is None`), leaving a persona that says "speak only through `say`" with no such tool, and a banner/README sentence ("with no voice, `say` reports a TTS error") that was false there. Also, `ComputerEmbodiment.enter()` caught only `ConfigError`/`ImportError` from the voice build, so any other constructor failure took the whole body (mic included) down — against "degradation is per component".
2. **The robot persona and the robot example config disagreed on how a dictated passage ends.** The config selects `trigger_word`, whose segmenter has *no* timer (a passage closes only on a trigger word); `prompts/reachy.md` says "delivered when they pause". Conversation (`utterance` mode) is unaffected — only a dictated passage never closed as the person was told it would.
3. **The mic hand-off was untested.** Every lifecycle test injected a prebuilt engine, so `ASREngine(cfg, audio_source=embodiment.mic)` never ran under test and the one "test" of it asserted the fake's own attribute. An injected engine also built a bridge with no body, against the spec.
4. **`audio_processing` silently ignored unknown keys** (a typo'd `echo_cancelation` ⇒ echo cancellation off, no error), unlike every other app-owned section.
5. **A raise in `build_ui`/`wire` leaked the entered body** — only `_start`/`launch` sat inside the `try` whose `finally` runs `_shutdown`.
6. **The page named itself wrongly**: app.md described titles the code no longer has; with no body the heading read "embodied in a WICA" and the launched body's name was lost; the "offline development hints are the embodiment's" sentence had no implementation.

## Scope

- `src/wica_interaction/computer/embodiment.py` — `voice` always `say`; `say` returns `"TTS error: <why>"` with no engine; the voice build catches any `Exception`; `WebcamSource` open/close race closed under its lock
- `src/wica_interaction/computer/config.py` — `audio_processing` rejects unknown keys at its three levels and non-object sub-sections
- `src/wica_interaction/asr_bridge.py` — `trigger_words=` parameter; `start_dictation`'s reply says how the passage ends
- `src/wica_interaction/system.py` — `asr_engine_factory=` seam replacing `asr_engine=`; the bridge gets the config's trigger words; `AppSystem.display_name`; `_serve(system)` owns build_ui → wire → start → launch inside the one `try/finally`
- `src/wica_interaction/app_ui.py` — title/heading from `system.display_name`, neutral without one
- `src/wica_interaction/reachy/embodiment.py` — a failed connection carries the offline-development hint (non-`fake` backends)
- `src/wica_interaction/__init__.py`, `config.py`, `reachy/robot_control.py` — stale docstrings
- `prompts/computer.md`, `prompts/reachy.md` — the dictation paragraph defers to `start_dictation`'s reply
- `tests/fakes.py`, `tests/test_system.py`, `tests/test_asr_bridge.py`, `tests/test_app_ui.py`, `tests/computer/test_embodiment.py`, `tests/computer/test_audio_processing.py`, `tests/reachy/test_embodiment.py` — below
- `specs/*.md` (the six above, plus wording in `gesture-input.md`), `README.md`, `plans/_plan-template.md` (stale package path)

## Steps

1. **Specs first.** Edit the six specs to the design below; fix the stale wording in gesture-input.md and testing.md (editorial). The specs and the code land in one change, so the six go `Implemented` → `Updated` → `Implemented` within it and end at `Implemented` (step 9).
2. **One voice contract (computer).** `ComputerEmbodiment.enter()` wraps the engine build in `except Exception`, keeping the message as `self._voice_error` and the `TTS: …` line in `missing`. `voice` returns `self.say` always; `say` returns `f"TTS error: {self._voice_error}"` when there are no tools. Tests: a failed build (a `ConfigError`, an `ImportError`, **and** an arbitrary `RuntimeError`) leaves the body up, `voice` set, `say("hi")` returning the TTS error naming the cause.
3. **Dictation reply.** `AsrBridge(engine, *, on_final_segment, mic_error=None, trigger_words=())`. After a successful `engine.start_dictation`, the reply is `"Dictation started (<mode> mode): <how it ends>"` — `timeout`: delivered once the person stops speaking for a few seconds; `trigger_word`: delivered when they say one of `<words>`, a pause does not end it; `utterance`: delivered after their next sentence. The reply is for the model's own understanding: nothing asks it to announce the mechanics (an extra spoken turn before every dictation would break the flow). `build_system` passes `config.asr_engine.segmentation.trigger_words`. Both personas' dictation paragraph drops "(delivered when they pause)" / "they paused" for "`start_dictation`'s reply tells you what will end the passage — for you to know, not to announce". Tests: one per mode on the reply.
4. **Factory seam.** `build_system(config, embodiment, *, asr_engine_factory: AsrEngineFactory = _build_asr_engine, gesture_models=None)`, `AsrEngineFactory = Callable[[ASREngineConfig, AudioSource | None], ASREngine]`; the factory is called only with a body, inside the existing `except (ValueError, ImportError)`. `tests/fakes.py` gains `asr_factory(engine)` (records `engine.audio_source`, returns the engine). `test_the_mic_is_what_the_asr_engine_reads` asserts `fake.audio_source is mic`; the no-body test now injects a factory and asserts it was never called.
5. **Strict `audio_processing`.** Unknown keys in the section or either sub-section raise `ValueError` naming them; a sub-section that is present but not an object (`false`, `[]`) raises too (`null`/absent still means defaults). Tests for each.
6. **Teardown covers the whole serve.** `_serve(system)`: `try: ui = build_ui(system); wire(system, ui.transcript); _start(system); ui.blocks.launch() finally: _shutdown(system)`; `run_app` ends with `_serve(system)`. Tests: a raising `build_ui` and a raising `wire` each still exit the body and stop the loop; the existing start-failure test goes through the same path.
7. **The page's name.** `AppSystem.display_name: str | None` — the launched embodiment's, kept even when `enter()` raised; `None` only for a config that failed to load. `build_ui`: title `WICA Interaction — <name>` / heading `Talk to a WICA agent embodied in a <name>`, or `WICA Interaction` / `Talk to a WICA agent` without one. `ReachyEmbodiment.enter()` re-raises a failed `__aenter__` as `RuntimeError(f"{exc} (offline development: …)")` from the original when the backend is not `fake`. Tests for the three title cases and the hint.
8. **Small items.** `WebcamSource`: `_opened` flips under `_lock`, and an open that completes after `close()` is closed again (test: an opener whose `open()` closes the source mid-call). Stale docstrings (`__init__.py`, `VisionConfig`, `robot_control.py`'s spec references). `plans/_plan-template.md`'s package path.
9. **Docs + statuses.** README's degraded-start sentence; the six specs → `Implemented`; this plan → `Done`, here and in the index.

## Verification

`uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, `uv run pytest` — all green; `grep -rn "asr_engine=cast(ASREngine" tests` prints nothing (the old seam is gone); `grep -rn "when they pause" prompts` prints nothing.
