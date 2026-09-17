---
code:
  - src/wica_interaction/embodiment.py
  - src/wica_interaction/system.py
tests:
  - tests/test_system.py
  - tests/fakes.py
---

# Embodiment

**Status:** Implemented

## Purpose

The app is one program that can be **embodied** in different things: a computer (a microphone, speakers, optionally a webcam), a Reachy Mini robot, later a Nao. Everything WICA-side is the same whatever the body — the `speech_input` entry and its funnel, the dictation commands, the hand-sign entries, the transcript and observability panels, the build → UI → wire → start → close lifecycle. What varies is only **where audio comes in and goes out, what the agent can act with, and what it sees through**. This spec names that variation: the `Embodiment` protocol the shared lifecycle in [app.md](app.md) is written against, and the two implementations, [computer-embodiment.md](computer-embodiment.md) and [reachy-embodiment.md](reachy-embodiment.md).

The word is deliberate: the persona already says "you are the voice and mind of a Reachy Mini". An embodiment is the body the agent has in a given deployment — not a *backend* (the bridge uses that word for `real`/`sim`/`fake`), not a *device* (used for audio, camera, and torch devices throughout the configs), not a *setup* (static configuration; this object is live).

## The protocol

```python
# src/wica_interaction/embodiment.py
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from asr_engine.audio import AudioSource
from wica import Command

from wica_interaction.gesture_input import CameraSource

Voice = Callable[[str], Awaitable[str]]  # the `say` output Command


class Embodiment(Protocol):
    display_name: str  # "computer", "Reachy Mini": the page title, the banner lines
    missing: list[str]  # degrade lines gathered while entering (one per component)

    async def enter(
        self,
    ) -> None: ...  # connect / build, on the app loop; a raise ⇒ no embodiment
    async def exit(
        self,
    ) -> None: ...  # release, on the app loop, after ASR and vision stopped

    @property
    def voice(self) -> Voice | None: ...  # the output Command; None ⇒ none is set

    @property
    def mic(
        self,
    ) -> AudioSource | None: ...  # None ⇒ the ASR engine opens its default device

    @property
    def mic_error(
        self,
    ) -> str | None: ...  # why capture died on its own; None while healthy

    def camera(
        self, *, fps: float
    ) -> CameraSource: ...  # a paced FrameSource + capture_error

    def commands(self) -> Sequence[Command | Callable[..., Any]]: ...  # action Commands

    @property
    def prompt_addendum(
        self,
    ) -> str: ...  # persona text appended at startup ("" for none)
```

A `typing.Protocol`, not a base class: pyright checks every real embodiment structurally at the call `build_system(config, ReachyEmbodiment(...))`, so an implementation that drifts from this surface is a type error in its own package, and the lifecycle tests drive `build_system` with a `FakeEmbodiment` that imports neither a robot SDK nor an audio engine.

## Member by member: who calls what, and when

Each member is touched at exactly one point of the lifecycle ([app.md](app.md), "Wiring and lifecycle"); the shared code never asks an embodiment what kind it is.

| Member | Called by, and when | Computer | Reachy Mini |
|---|---|---|---|
| `display_name` | `build_ui` (page title); `build_system` (the banner line when `enter` raises) | `"computer"` | `"Reachy Mini"` |
| `enter()` | `build_system`, first, scheduled onto the app loop and awaited. **A raise means "no embodiment"**: the agent still builds, typed text still drives it, nothing that needs a body is wired | builds the audio-processing coordinator and the `TTSEngine`, recording their failures in `missing` | `ReachyMiniApi.__aenter__` (daemon, connection, media session), then the emotion-names fetch, then the mic source |
| `missing` | `build_system`, right after `enter`, merged into the banner list | `TTS: …`, `Audio processing: …` | `TTS: …`, `Robot: emotions unavailable: …` |
| `prompt_addendum` | `build_system`, before `Wica.init`: appended to the resolved persona (the same seam the hand-signs section uses — [app.md](app.md), build step 4) | `""` | the "Your emotions" section |
| `mic` | `build_system`, as `ASREngine(cfg.asr_engine, audio_source=…)`. `None` means the engine opens its default local capture from the ASR config's `audio.device` | the coordinator's processed source, or `None` when no processing feature is on | `RobotMicSource` |
| `mic_error` | the ASR panel's timer, through `AsrBridge.status()["capture_error"]` | always `None` | `RobotMicSource.capture_error` |
| `camera(fps=…)` | `build_system`, only when `vision.enabled`, handed to `GestureInput` | a paced `WebcamSource` on the configured OpenCV device | `RobotCameraSource` polling the bridge on the loop |
| `voice` | `wire`, as `wica.set_output_command(voice)`. `None` means no output Command is set — the model's free text is then its voice, as today | `say` over `TTSTools(engine)`, or `None` when the TTS engine failed to build | `say` over `api.say` |
| `commands()` | `wire`, each item `register_command`ed after the dictation commands | `()` | `play_emotion`, `get_motors_state`, `set_motors_state` |
| `exit()` | `_shutdown`, scheduled onto the loop after the agent, vision and ASR are stopped, before the loop stops | nothing to release | `ReachyMiniApi.__aexit__` |

Read the "called by" column top to bottom and it is the lifecycle: construct the embodiment from its own config in the entry point (no I/O in the constructor), `enter` it, seed the prompt, build ASR over its mic, build vision over its camera, wire its voice and commands, run, `exit` it.

**`voice` is the model-facing description of the voice.** The embodiment's `say` coroutine is registered directly as the output Command, so its name, signature and docstring are what the model reads — "out of the robot's speaker" versus "through the machine's speakers" lives with the embodiment that owns it. The contract is the same for both: non-raising, `"OK"` on success, a readable `"TTS error: …"` otherwise, awaiting real playback so the Command stays `running` for the duration of the speech.

**Degradation is per component, never per embodiment.** `enter()` raises only when the body itself is unavailable (the robot cannot be connected). Anything narrower — the voice's key unset, an optional dependency absent, the emotions library unreadable — is recorded as one line in `missing` and the corresponding member degrades (`voice` is `None`, `commands()` still registers, the addendum is empty, …), so the rest of the body stays up. The banner shows `missing` verbatim.

## Not in the protocol

- **Config loading.** Each embodiment's package parses its own sections and constructs the embodiment from them ([configuration.md](configuration.md)); the shared code only ever sees the shared `AppConfig` (`wica`, `asr`, `vision`).
- **World entries.** `speech_input` and `hand_gesture_<n>` are the app's ([app.md](app.md), [gesture-input.md](gesture-input.md)); an embodiment reaches the World only through the Commands it registers.
- **UI.** The page is shared ([app.md](app.md), "What the user sees"). If an embodiment ever needs a panel of its own — a robot's motor state, a battery line — that is a new member to add then, not a hook reserved now (open question 1).
- **The entry point.** Which embodiment runs is decided by the command you launch (`wica-interaction-computer`, `wica-interaction-reachy`), not by a config key: each entry point imports only its own package, so a computer-only install never imports the robot SDK ([project.md](project.md), "Extras"). A reachy config handed to the computer command fails on its unknown `reachy` section — a clearer error than a discriminator could give.

## Testing

`tests/fakes.py` holds a `FakeEmbodiment` that records what the lifecycle asked of it: an `entered`/`exited` flag and an ordered `log` shared with the fake ASR engine (to pin teardown order), a `said` list behind its `voice`, an optional injected `mic` (an `AudioSource` fake) and `mic_error`, a scripted `camera` (frames for the gesture tests), `commands` to register, a `prompt_addendum`, `missing` lines, and `raise_on_enter`. The lifecycle tests in `tests/test_system.py` drive `build_system(config, FakeEmbodiment(...))`, then `wire`, then `_start`, against WICA's scripted `fake` provider, and assert ([app.md](app.md), "Testing"):

- the scripted `say` reaches `embodiment.said` (the voice is set as the output Command), and with `voice=None` no output Command is set;
- each of `commands()` is registered and routes back to the fake when the model calls it;
- `prompt_addendum` appears verbatim in the first step's prompt;
- `mic` is what the ASR engine was built over (the fake engine records its `audio_source`), and `mic_error` reaches `bridge.status()["capture_error"]`;
- `raise_on_enter` degrades to a banner line naming `display_name`, no voice, no commands, no ASR bridge, no gesture input — and typed text still reaches the agent;
- `missing` lines from `enter` reach `system.missing`;
- teardown order: `wica.close`, `gesture.stop`, `asr.stop`, `embodiment.exit`, then the loop — and `exit` runs even when a step before it raises.

Each embodiment's own tests ([computer-embodiment.md](computer-embodiment.md), [reachy-embodiment.md](reachy-embodiment.md)) pin that it satisfies each member's contract against its own fakes; no test builds a real body.

## Open questions

1. **Per-embodiment UI.** A status surface owned by the embodiment (robot connection and motor state; the computer's audio-processing on/degraded state) would be a `panel(...)`-style member the shared page places. Deferred until one is wanted — the [app.md](app.md) banner and the ASR/Vision lines cover today's needs.
2. **A timeout on `enter`.** The robot's enter can block for the bridge's `daemon.startup_timeout` (45 s) before failing; the app adds no timeout of its own ([reachy-embodiment.md](reachy-embodiment.md)). Whether the protocol should carry one, or leave it to each implementation, is deferred.
