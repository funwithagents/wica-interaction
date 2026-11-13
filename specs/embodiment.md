---
code:
  - src/wica_interaction/embodiment.py
  - src/wica_interaction/system.py
tests:
  - tests/test_system.py
  - tests/fakes.py
---

# Embodiment

**Status:** Updated

## Purpose

The app is one program that can be **embodied** in different things: a computer (a microphone, speakers, optionally a webcam), a Reachy Mini robot, later a Nao. Everything WICA-side is the same whatever the body — the `speech_input` entry and its funnel, the dictation commands, the hand-sign entries, the transcript and observability panels, the build → UI → wire → start → close lifecycle. What varies is only **where audio comes in and goes out, what the agent can act with, what it sees through, and what the body itself perceives** (the robot's people in view). This spec names that variation: the `Embodiment` protocol the shared lifecycle in [app.md](app.md) is written against, and the two implementations, [computer-embodiment.md](computer-embodiment.md) and [reachy-embodiment.md](reachy-embodiment.md).

The word is deliberate: the persona already says "you are the voice and mind of a Reachy Mini". An embodiment is the body the agent has in a given deployment — not a *backend* (the bridge uses that word for `real`/`sim`/`fake`), not a *device* (used for audio, camera, and torch devices throughout the configs), not a *setup* (static configuration; this object is live).

## The protocol

```python
# src/wica_interaction/embodiment.py
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from asr_engine.audio import AudioSource
from wica import Command, Content

from wica_interaction.gesture_input import CameraFeed

Voice = Callable[[str], Awaitable[str]]  # the `say` output Command
Deliver = Callable[[str, Any], None]  # writes one of an input's entries (key, value)


@dataclass(frozen=True)
class InputEntry:  # one World entry an input writes: its registration, and its transcript label
    key: str
    type: type
    serialize_fn: Callable[[Any, Any], Content]
    triggers_llm_call: bool = False
    trigger_condition_fn: Callable[[Any, Any], bool] | None = None
    ttl: timedelta | None = None
    display_fn: Callable[[Any], str] | None = (
        None  # the transcript's label; None ⇒ the default
    )


class Input(Protocol):  # a producer of perceptions, and the entries it writes
    @property
    def entries(self) -> Sequence[InputEntry]: ...
    async def start(
        self, deliver: Deliver
    ) -> None: ...  # on the app loop, the agent running
    async def stop(self) -> None: ...  # on the app loop, before the body exits


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
    ) -> CameraFeed: ...  # a latest-value frame slot + open/close + capture_error

    def commands(self) -> Sequence[Command | Callable[..., Any]]: ...  # action Commands

    def inputs(self) -> Sequence[Input]: ...  # the body's own perceptions ((): none)

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
| `display_name` | `build_system`, which keeps it on `AppSystem.display_name` — also when `enter` raises, so `build_ui` still names the page after the body that was launched — and uses it for the banner line of a failed `enter` | `"computer"` | `"Reachy Mini"` |
| `enter()` | `build_system`, first, scheduled onto the app loop and awaited. **A raise means "no embodiment"**: the agent still builds, typed text still drives it, nothing that needs a body is wired | builds the audio-processing coordinator and the `TTSEngine`, recording their failures in `missing` | `ReachyMiniBridge.start()` (daemon, connection, media session, camera feed, motion loop), then the emotion-names fetch, then the mic source |
| `missing` | `build_system`, right after `enter`, merged into the banner list | `TTS: …`, `Audio processing: …` | `TTS: …`, `Robot: emotions unavailable: …` |
| `prompt_addendum` | `build_system`, before `Wica.init`: appended to the resolved persona (the same seam the hand-signs section uses — [app.md](app.md), build step 4) | `""` | the "Your emotions" section |
| `mic` | `build_system`, as `ASREngine(cfg.asr_engine, audio_source=…)`. `None` means the engine opens its default local capture from the ASR config's `audio.device` | the coordinator's processed source, or `None` when no processing feature is on | `RobotMicSource` |
| `mic_error` | the ASR panel's timer, through `AsrBridge.status()["capture_error"]` | always `None` | `RobotMicSource.capture_error` |
| `camera(fps=…)` | `build_system`, only when `vision.enabled`, handed to `GestureInput` as the hand stage's upstream ([gesture-input.md](gesture-input.md), "Frames come from the embodiment"). `fps` is the rate the body should deliver frames at *if it can pace them*; a body whose camera runs at its own rate ignores it (the stages pace themselves) | a `StreamFeed` over a `WebcamSource` paced at `fps` on the configured OpenCV device | `RobotCameraFeed`: the bridge's `camera` feed (`bridge.camera`) (the session's one camera reader, at the daemon's rate) behind no-op `open`/`close` |
| `voice` | `wire`, as `wica.set_output_command(voice)`. `None` is for a body with **no speaker at all** (no output Command is set — the model's free text is then its voice); a body whose voice merely *failed to build* keeps its `say` wired (below) | `say` over `TTSTools(engine)`; with no engine it answers `"TTS error: <why>"` | `say` over `bridge.say`; with no synthesizer it answers `"TTS error: <why>"` |
| `commands()` | `wire`, each item `register_command`ed after the dictation commands | `()` | `play_emotion`, `get_motors_state`, `set_motors_state` |
| `inputs()` | `build_system`, right after `speech_input` is registered, only with a body and an agent: each `Input`'s entries are registered, its producer handed a guarded `deliver`. Each started by `_start` after the agent, each stopped by `_shutdown` right after the agent closes | `()` | the people in view ([reachy-embodiment.md](reachy-embodiment.md), "People: `PeopleInput`"), when a face detector is configured |
| `exit()` | `_shutdown`, scheduled onto the loop after the agent, the inputs, vision and ASR are stopped, before the loop stops | nothing to release | `ReachyMiniBridge.stop()` |

Read the "called by" column top to bottom and it is the lifecycle: construct the embodiment from its own config in the entry point (no I/O in the constructor), `enter` it, seed the prompt, register its inputs, build ASR over its mic, build vision over its camera, wire its voice and commands, run, `exit` it.

**`inputs()` is the mirror of `commands()`.** A Command is the agent acting through the body; an input is the body telling the agent what it perceived. In both directions the body *describes* and the shared code *connects*: `commands()` returns callables that `wire` registers with `register_command`, and `inputs()` returns producers with the World entries they write, which `build_system` registers with `world.register` — the body never sees the agent's registry, nor the World. It is the pattern speech and hand signs already follow: `AsrBridge` and `GestureInput` call a callback, and `system.py` owns the entries, the guard and the order ([app.md](app.md), "Wiring and lifecycle"). An input is:

- **Its entries** (`InputEntry`): everything `world.register` takes — key, type, `serialize_fn` (what the prompt says), whether and when it triggers a step, its TTL — plus a `display_fn`, how the transcript names it (`None`: the contrib's default). The wording belongs to the body that perceives, so it lives with it.
- **Its producer**: `start(deliver)` and `stop()`, both coroutines run on the app loop. `deliver(key, value)` writes one of *its own* entries — a key it did not declare is logged and dropped — guarded on a running agent, like every input of the app. The producer starts after the agent (`world.update` raises before), so it may deliver at once — what it already perceives — and stops before the body exits, while whatever it reads from is still live.
- **No failure of its own takes the app down.** An entry the World refuses (a key already registered) drops that input, with a `<display_name>: …` banner line; a `start()` that raises is logged and the input stays silent; a `stop()` that raises or hangs is one isolated, time-bounded teardown step.

**`voice` is the model-facing description of the voice.** The embodiment's `say` coroutine is registered directly as the output Command, so its name, signature and docstring are what the model reads — "out of the robot's speaker" versus "through the machine's speakers" lives with the embodiment that owns it. The contract is the same for both: non-raising, `"OK"` on success, a readable `"TTS error: …"` otherwise, awaiting real playback so the Command stays `running` for the duration of the speech.

**A voice that failed to build stays wired.** Both personas say "speak only through `say`", and WICA's primer tells the model its free text is private *because* an output Command is set. Dropping the Command when the TTS engine cannot be built would leave the persona naming a tool that does not exist, and the model with no way to learn why nothing was heard. So both embodiments return their `say` from `voice` whatever happened to the engine, and `say` answers `"TTS error: <the build error>"` — the failure is in the banner for the person and in the Command result for the model. `voice` is `None` only for a body that has no speaker to begin with (none today; `FakeEmbodiment(voice=False)` pins that path).

**Degradation is per component, never per embodiment.** `enter()` raises only when the body itself is unavailable (the robot cannot be connected). Anything narrower — the voice's key unset, an optional dependency absent, the emotions library unreadable — is recorded as one line in `missing` and the corresponding member degrades (`say` answers with the TTS error, `commands()` still registers, the addendum is empty, …), so the rest of the body stays up. An `enter()` therefore catches **whatever** a component's build raises, not a shortlist of exception types: a provider constructor failing in an unforeseen way must cost that component, never the mic and the camera with it. The banner shows `missing` verbatim.

## Not in the protocol

- **Config loading.** Each embodiment's package parses its own sections and constructs the embodiment from them ([configuration.md](configuration.md)); the shared code only ever sees the shared `AppConfig` (`wica`, `asr`, `vision`).
- **The World.** `speech_input` and `hand_gesture_<n>` are the app's ([app.md](app.md), [gesture-input.md](gesture-input.md)). An embodiment adds entries of its own only through `inputs()`, described and delivered, never written directly: registration, the running guard and the order stay the lifecycle's.
- **UI.** The page is shared ([app.md](app.md), "What the user sees"). If an embodiment ever needs a panel of its own — a robot's motor state, a battery line — that is a new member to add then, not a hook reserved now (open question 1).
- **The entry point.** Which embodiment runs is decided by the command you launch (`wica-interaction-computer`, `wica-interaction-reachy`), not by a config key: each entry point imports only its own package, so a computer-only install never imports the robot SDK ([project.md](project.md), "Extras"). A reachy config handed to the computer command fails on its unknown `reachy` section — a clearer error than a discriminator could give.

## Testing

`tests/fakes.py` holds a `FakeEmbodiment` that records what the lifecycle asked of it: an `entered`/`exited` flag and an ordered `log` shared with the fake ASR engine (to pin teardown order), a `said` list behind its `voice`, an optional injected `mic` (an `AudioSource` fake) and `mic_error`, a scripted `camera` (a feed of frames for the gesture tests), `commands` to register, `inputs` (a `ScriptedInput`: entries of its own, a producer recording its start and stop and handing the test its `deliver`), a `prompt_addendum`, `missing` lines, and `raise_on_enter`. The lifecycle tests in `tests/test_system.py` drive `build_system(config, FakeEmbodiment(...))`, then `wire`, then `_start`, against WICA's scripted `fake` provider, and assert ([app.md](app.md), "Testing"):

- the scripted `say` reaches `embodiment.said` (the voice is set as the output Command), and with `voice=None` no output Command is set;
- each of `commands()` is registered and routes back to the fake when the model calls it;
- `prompt_addendum` appears verbatim in the first step's prompt;
- `mic` is what the ASR engine was built over — asserted on the **engine's** side: the lifecycle builds the engine through its `asr_engine_factory` seam ([app.md](app.md), build step 6), so the fake factory records the `audio_source` it was handed (asserting the fake body's own `mic` would prove nothing) — and `mic_error` reaches `bridge.status()["capture_error"]`;
- `raise_on_enter` degrades to a banner line naming `display_name` (kept on `AppSystem.display_name`), no voice, no commands, no ASR bridge — the engine factory is never called — no gesture input, and typed text still reaches the agent;
- each of `inputs()` is registered and started once the agent runs: a value its producer delivers lands in the World, renders in the next step's prompt through its `serialize_fn`, wakes the agent only as its trigger rule says, and shows in the transcript through its `display_fn`; a key it did not declare is dropped; a delivery before start or after teardown is dropped, not raised; an entry the World refuses (a key twice) is a banner line, the rest of the app up; a `start()` that raises leaves the app running; no input is registered or started without a body or without an agent;
- `missing` lines from `enter` reach `system.missing`;
- teardown order: `wica.close`, the inputs' `stop`, `gesture.stop`, `asr.stop`, `embodiment.exit`, then the loop — and `exit` runs even when a step before it raises, including a raise while building the page or wiring (the body is entered before either).

Each embodiment's own tests ([computer-embodiment.md](computer-embodiment.md), [reachy-embodiment.md](reachy-embodiment.md)) pin that it satisfies each member's contract against its own fakes; no test builds a real body.

## Open questions

1. **Per-embodiment UI.** A status surface owned by the embodiment (robot connection and motor state; the computer's audio-processing on/degraded state) would be a `panel(...)`-style member the shared page places. Deferred until one is wanted — the [app.md](app.md) banner and the ASR/Vision lines cover today's needs.
2. **A timeout on `enter`.** The robot's enter can block for the bridge's `daemon.startup_timeout` (45 s) before failing; the app adds no timeout of its own ([reachy-embodiment.md](reachy-embodiment.md)). Whether the protocol should carry one, or leave it to each implementation, is deferred.
