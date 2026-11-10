"""The person's voice, as a process of its own (specs/e2e.md, "The person"): a phonon engine
playing to the cable's playback end, speaking each line it reads on stdin and answering `done`
once it has been played.

A process of its own because, on macOS, a process that plays into the cable stalls its own capture
of the cable — the app's capture, when the person shares the app's process — whatever the order
and the rate. The person is not inside the app either.

    python voice.py <device> <voice>
"""

from __future__ import annotations

import asyncio
import sys

from tts_engine.config import TTSEngineConfig
from tts_engine.engine import TTSEngine


async def main(device: str, voice: str) -> None:
    config = TTSEngineConfig.from_dict(
        {"module": {"type": "phonon", "voice": voice}, "player": {"device": device}}
    )
    async with TTSEngine(config) as engine:
        print("ready", flush=True)
        for line in sys.stdin:
            await engine.say(line.strip())
            print("done", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
