"""Doubles more than one test module needs (specs/testing.md, "Fakes are shared, not duplicated").

Nothing here imports a robot SDK or an audio engine: the lifecycle is tested once over
``FakeEmbodiment``, and each embodiment's own fakes live under its ``tests/<embodiment>/`` folder.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
from asr_engine.audio import AudioSource
from vision_modules import Frame
from wica import Command, Content, TextPart
from wica.config import WicaConfig

from wica_interaction.embodiment import Deliver, Input, InputEntry
from wica_interaction.gesture_input import CameraFeed


def scripted_wica_config(
    script: list[dict], *, persona: str = "You are a test double."
) -> WicaConfig:
    """A WICA config running the `fake` provider over *script* — no key, no network, no delay."""
    return WicaConfig.from_dict(
        {
            "agent": {
                "provider": "fake",
                "model": "scripted",
                "system_prompt": persona,
                "model_kwargs": {"delay_s": 0, "script": script},
            }
        }
    )


class FakeASREngine:
    """The slice of `ASREngine` that `AsrBridge` and `build_system` touch — construction needs no key.

    `AsrBridge.__init__` assigns `on_speech_segment`; `start`/`stop` and the `status()`/`dictating`/
    `segmentation_mode` accessors back the bridge's own surface. `audio_source` is recorded (the
    engine is normally built over the embodiment's mic). Pass `log` (shared with `FakeEmbodiment`)
    to record teardown steps in order. `raise_on_start` / `raise_on_stop` make the corresponding call
    raise; `hang_on_stop` makes `stop` never return (a wedged provider).
    """

    def __init__(
        self,
        *,
        audio_source: AudioSource | None = None,
        log: list[str] | None = None,
        raise_on_start: str | None = None,
        raise_on_stop: str | None = None,
        hang_on_stop: bool = False,
    ) -> None:
        self.on_speech_segment: Any = None
        self.audio_source = audio_source
        self.dictating = False
        self.segmentation_mode = "utterance"
        self.started = False
        self._log = log
        self._raise_on_start = raise_on_start
        self._raise_on_stop = raise_on_stop
        self._hang_on_stop = hang_on_stop

    async def start(self) -> None:
        if self._raise_on_start is not None:
            raise RuntimeError(self._raise_on_start)
        self.started = True

    async def stop(self) -> None:
        if self._log is not None:
            self._log.append("asr.stop")
        if self._raise_on_stop is not None:
            raise RuntimeError(self._raise_on_stop)
        if self._hang_on_stop:
            await asyncio.Event().wait()
        self.started = False

    def status(self) -> dict[str, Any]:
        return {"running": self.started}


def asr_factory(
    engine: FakeASREngine, calls: list[AudioSource | None] | None = None
) -> Callable[[Any, AudioSource | None], Any]:
    """The `asr_engine_factory` seam of `build_system`, handing back *engine*.

    It records the source the lifecycle built the engine over — on `engine.audio_source`, and in
    *calls* when given (so a test can also assert the factory was never called)."""

    def factory(_config: Any, audio_source: AudioSource | None) -> Any:
        engine.audio_source = audio_source
        if calls is not None:
            calls.append(audio_source)
        return engine

    return factory


class ScriptedCameraFeed:
    """A `CameraFeed` over one settable frame — the camera every vision test runs on.

    The shape the gesture input samples (specs/gesture-input.md, "Frames come from the
    embodiment"): `open()` starts a thread republishing `frame` under a fresh `frame_id` every
    `1/fps` (a `None` frame publishes nothing, so a test can starve the feed), `close()` stops it,
    `latest()` / `published_count` are what it published; `frame` and `capture_error` are plain
    attributes a test sets mid-run, `opens` / `closes` count the lifecycle calls, `fps` is the rate
    the body was asked for.
    """

    def __init__(self, frame: np.ndarray | None = None, *, fps: float = 100.0) -> None:
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self.frame = frame
        self.fps = fps
        self.capture_error: str | None = None
        self.opens = 0
        self.closes = 0
        self._interval = 1.0 / fps
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._count = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def open(self) -> None:
        self.opens += 1
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.closes += 1
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        self._thread = None

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    @property
    def published_count(self) -> int:
        with self._lock:
            return self._count

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            frame = self.frame
            if frame is None:
                continue
            with self._lock:
                self._count += 1
                self._latest = Frame(self._count, time.monotonic(), frame)


class ScriptedCameraSource:
    """A `CameraSource` over one settable frame — what the `StreamFeed` tests wrap.

    Obeys the three rules such a source does (specs/gesture-input.md, "`StreamFeed`"): it paces
    itself to `fps`, a `None` frame is *not* the end of the stream (it waits an interval and tries
    again), and only `close()` makes `read()` return `None`. `frame` and `capture_error` are plain
    attributes a test sets mid-run.
    """

    def __init__(self, frame: np.ndarray | None = None, *, fps: float = 100.0) -> None:
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self.frame = frame
        self.capture_error: str | None = None
        self.opens = 0
        self.closes = 0
        self.reads = 0
        self._interval = 1.0 / fps
        self._closed = threading.Event()
        self._closed.set()  # not readable until the provider opens it

    def open(self) -> None:
        self.opens += 1
        self._closed.clear()

    def read(self) -> np.ndarray | None:
        while not self._closed.is_set():
            self._closed.wait(self._interval)
            if self._closed.is_set():
                break
            self.reads += 1
            frame = self.frame
            if frame is not None:
                return frame
        return None

    def close(self) -> None:
        self.closes += 1
        self._closed.set()

    def fps(self) -> float:
        return 1.0 / self._interval


def _rendered(value: Any, previous: Any) -> Content:
    """A scripted entry's prompt line: what it holds, prefixed so a test finds it; nothing for None."""
    return [] if value is None else [TextPart(f"Seen: {value}")]


class ScriptedInput:
    """An embodiment's `Input` a test drives by hand (specs/embodiment.md, "Testing").

    It declares *entries* (by default `seen`, a triggering one-shot string labelled `👀 <value>` in
    the transcript, and `around`, a standing one that never triggers), records `start` / `stop` in
    `log` (shared with the other fakes, to pin the order) and keeps the `deliver` the lifecycle
    handed it, so the test plays the producer: `deliver("seen", "a cat")`. `raise_on_start` makes
    `start` raise.
    """

    def __init__(
        self,
        entries: Sequence[InputEntry] | None = None,
        *,
        log: list[str] | None = None,
        raise_on_start: str | None = None,
    ) -> None:
        self._entries = (
            tuple(entries)
            if entries is not None
            else (
                InputEntry(
                    "seen",
                    str,
                    serialize_fn=_rendered,
                    triggers_llm_call=True,
                    display_fn=lambda value: f"👀 {value}",
                ),
                InputEntry("around", str, serialize_fn=_rendered),
            )
        )
        self.deliver: Deliver | None = None
        self.started = 0
        self.stopped = 0
        self.loop: asyncio.AbstractEventLoop | None = None
        self._log = log
        self._raise_on_start = raise_on_start

    @property
    def entries(self) -> Sequence[InputEntry]:
        return self._entries

    async def start(self, deliver: Deliver) -> None:
        self.started += 1
        if self._raise_on_start is not None:
            raise RuntimeError(self._raise_on_start)
        self.loop = asyncio.get_running_loop()
        self.deliver = deliver

    async def stop(self) -> None:
        self.stopped += 1
        if self._log is not None:
            self._log.append("input.stop")


class FakeEmbodiment:
    """The whole `Embodiment` protocol, recording what the lifecycle asked of it.

    Stands in for any body in `tests/test_system.py` (specs/embodiment.md, "Testing"): it imports no
    robot SDK and no audio engine, so the lifecycle is tested once, over a body that only records.
    `inputs` are the body's own perceptions (a `ScriptedInput`, played by the test);
    `raise_on_enter` makes `enter()` raise (the "no embodiment" degrade); `missing` are the
    per-component degrade lines a real `enter()` would have gathered; `log` (shared with
    `FakeASREngine`) records teardown steps in order.
    """

    display_name = "fake body"

    def __init__(
        self,
        *,
        voice: bool = True,
        mic: AudioSource | None = None,
        mic_error: str | None = None,
        frames: np.ndarray | None = None,
        commands: Sequence[Command | Callable[..., Any]] | None = None,
        inputs: Sequence[Input] = (),
        prompt_addendum: str = "",
        missing: Sequence[str] = (),
        raise_on_enter: str | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.said: list[str] = []
        self.waved = 0
        self.entered = False
        self.exited = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.cameras: list[ScriptedCameraFeed] = []
        self.missing: list[str] = []
        self._voice = voice
        self._mic = mic
        self._mic_error = mic_error
        self._frames = frames
        # None ⇒ this body's one action Command, `wave` (registered like any other; tests that want
        # a body with nothing to act with pass `commands=()`).
        self._commands: Sequence[Command | Callable[..., Any]] = (
            commands if commands is not None else (self.wave,)
        )
        self._inputs = tuple(inputs)
        self.inputs_asked = 0
        self._prompt_addendum = prompt_addendum
        self._missing = tuple(missing)
        self._raise_on_enter = raise_on_enter
        self._log = log

    async def enter(self) -> None:
        if self._raise_on_enter is not None:
            raise RuntimeError(self._raise_on_enter)
        self.loop = asyncio.get_running_loop()
        self.entered = True
        self.missing = list(self._missing)

    async def exit(self) -> None:
        if self._log is not None:
            self._log.append("embodiment.exit")
        self.exited = True

    @property
    def voice(self) -> Callable[[str], Any] | None:
        return self.say if self._voice else None

    async def say(self, text: str) -> str:
        """Speak `text` aloud to the person."""
        self.said.append(text)
        return "OK"

    async def wave(self) -> str:
        """Wave at the person."""
        self.waved += 1
        return "OK — waved."

    @property
    def mic(self) -> AudioSource | None:
        return self._mic

    @property
    def mic_error(self) -> str | None:
        return self._mic_error

    def camera(self, *, fps: float) -> CameraFeed:
        feed = ScriptedCameraFeed(self._frames, fps=fps)
        self.cameras.append(feed)
        return feed

    def commands(self) -> Sequence[Command | Callable[..., Any]]:
        return self._commands

    def inputs(self) -> Sequence[Input]:
        self.inputs_asked += 1
        return self._inputs

    @property
    def prompt_addendum(self) -> str:
        return self._prompt_addendum
