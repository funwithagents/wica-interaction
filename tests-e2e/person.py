"""The person in front of the app (specs/e2e.md, "The person"): a voice played into the cable the
app listens on, and a hand shown to its camera.

The voice is a tts-engine phonon engine in the room, a process of its own (`room.py`) playing into
the cable's playback end, which also plays the app's echo back when the run has an echo device.
What the camera sees is a *stage*: on the computer, the image file the webcam reads; on the robot,
the props of the sim's scene.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NO_HAND = FIXTURES / "no_hand.jpg"
ROOM = Path(__file__).resolve().parent / "room.py"

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


class SceneStage:
    """The robot's camera stage: the hand props of the sim's scene (`hand_1` … in `HAND_PHOTOS`'
    order, declared by the conftest), one spawned straight ahead of the head to show a hand, the
    scene cleared to take it away."""

    # 0.45 m ahead at head height: the middle of the band where the spike read every sign.
    HAND_POS = (0.45, 0.0, 0.20)

    def __init__(self, scene: Any) -> None:
        self._scene = scene
        self._names = {photo: f"hand_{n}" for n, photo in enumerate(HAND_PHOTOS, 1)}

    def show(self, photo: str) -> None:
        self._scene.clear()
        self._scene.spawn(self.HAND_POS, name=self._names[photo])

    def hide(self) -> None:
        self._scene.clear()


class Person:
    """The person: a voice in the room (`room.py`), started on the first `say` — with the app's echo
    when *echo_device* is given — and a hand shown on the *stage*. `said` keeps every line spoken,
    for the echo check."""

    def __init__(
        self, voice_device: str, stage: Stage, echo_device: str | None = None
    ) -> None:
        self._device = voice_device
        self._stage = stage
        self._echo = echo_device
        self._voice: subprocess.Popen[str] | None = None
        self.said: list[str] = []

    def _started(self) -> subprocess.Popen[str]:
        if self._voice is None:
            echo = [self._echo] if self._echo is not None else []
            self._voice = subprocess.Popen(
                [sys.executable, str(ROOM), self._device, PERSON_VOICE, *echo],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            self._expect("ready")
        return self._voice

    def _expect(self, word: str) -> None:
        assert self._voice is not None and self._voice.stdout is not None
        line = self._voice.stdout.readline().strip()
        if line != word:
            raise RuntimeError(
                f"the room answered {line!r}, not {word!r} "
                f"(exit code {self._voice.poll()})"
            )

    def say(self, text: str) -> None:
        """Speak *text* into the cable; returns once it has been played."""
        voice = self._started()
        assert voice.stdin is not None
        self.said.append(text)
        voice.stdin.write(" ".join(text.split()) + "\n")
        voice.stdin.flush()
        self._expect("done")

    def show(self, photo: str) -> None:
        """Hold up the hand of *photo* (a key of `HAND_PHOTOS`)."""
        self._stage.show(photo)

    def hide(self) -> None:
        """Take the hand away."""
        self._stage.hide()

    def close(self) -> None:
        if self._voice is None:
            return
        if self._voice.stdin is not None:
            self._voice.stdin.close()
        try:
            self._voice.wait(10.0)
        except subprocess.TimeoutExpired:
            self._voice.kill()
