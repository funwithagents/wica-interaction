"""The person in front of the app (specs/e2e.md, "The person"): a voice played into the cable the
app listens on, and a hand shown to its camera.

The voice is a tts-engine phonon engine of its own, on a loop of its own, playing to the cable's
playback end. What the camera sees is a *stage*: on the computer, the image file the webcam reads.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Protocol

from tts_engine.config import TTSEngineConfig
from tts_engine.engine import TTSEngine

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NO_HAND = FIXTURES / "no_hand.jpg"

# The person's phonon voice, not the app's (`Micah`), so a transcript always tells them apart.
PERSON_VOICE = "Harper"

# Each photo and the sign the app delivers for it (specs/e2e.md, "The hand photos").
HAND_PHOTOS: dict[str, str] = {
    "hand_stop": "stop",
    "hand_fist": "stop",
    "hand_palm": "five",
    "hand_one": "one",
    "hand_three2": "three",
}


class Stage(Protocol):
    def show(self, photo: str) -> None: ...
    def hide(self) -> None: ...


class FileCamera:
    """The computer's camera as one image file: `camera.device` names it, and the webcam source
    reads it again at every poll (an image file is a one-frame stream it reopens). Changing what the
    camera sees is replacing the file — atomically, so a read never sees half an image."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.hide()

    def _put(self, source: Path) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=self.path.suffix)
        os.close(fd)
        shutil.copyfile(source, tmp)
        os.replace(tmp, self.path)

    def show(self, photo: str) -> None:
        self._put(FIXTURES / f"{photo}.jpg")

    def hide(self) -> None:
        self._put(NO_HAND)


class Person:
    def __init__(self, voice_device: str, stage: Stage) -> None:
        self._stage = stage
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        config = TTSEngineConfig.from_dict(
            {
                "module": {"type": "phonon", "voice": PERSON_VOICE},
                "player": {"device": voice_device},
            }
        )
        self._engine = self._run(self._build(config))

    async def _build(self, config: TTSEngineConfig) -> TTSEngine:
        return TTSEngine(config)

    def _run[T](self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def say(self, text: str) -> None:
        """Speak *text* into the cable; returns once it has been played."""
        self._run(self._engine.say(text))

    def show(self, photo: str) -> None:
        """Hold up the hand of *photo* (a key of `HAND_PHOTOS`)."""
        self._stage.show(photo)

    def hide(self) -> None:
        """Take the hand away."""
        self._stage.hide()

    def close(self) -> None:
        try:
            self._run(self._engine.close())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(5.0)
