# The end-to-end tier, part 4: the room and the conversation

**Status:** In progress

Implements [specs/e2e.md](../specs/e2e.md) ("The room", "The conversation") and the echo device of [specs/ci.md](../specs/ci.md): the room process that plays the person's voice and, given a virtual speaker, the app's echo into the cable; the computer's echo cancellation following that echo; and one conversation per embodiment, a test class of steps on one running app, which takes over the separate interrupt and dictation tests and is where the echo is checked. The focused scenarios 1–3 stay. The real-room run and a robot echo are left out.

## Scope

- `tests-e2e/room.py` — replaces `voice.py`: the person's phonon voice and, given an echo device, the app's speaker recorded and played back 50 ms later at −10 dB, mixed into the cable for the whole run.
- `tests-e2e/person.py` — `Person` drives the room: starts it with the echo device, `say` as before.
- `tests-e2e/support.py` — the echo device's resolution; per-step checks (`failed_checks` from a start time); the echo check.
- `tests-e2e/conftest.py` — `Devices.echo`; the computer's echo cancellation filled in from it; the class-scoped conversation fixtures; the conversation's evidence.
- `tests-e2e/scenarios.py` — the focused scenarios 1–3; the conversation's steps (the question, the sign, the emotion, the spoken and the shown stop, the note with its follow-up question, the echo check), each starting from a settled agent.
- `tests-e2e/test_computer.py`, `tests-e2e/test_reachy.py` — the focused scenarios and a `TestConversation` class each; the separate interrupt, emotion and dictation tests go.
- `.github/workflows/ci.yml` — the echo device (`wica_echo`, over `wica_speaker.monitor`) in both e2e jobs; the robot's job stops naming its mic.
- `.github/workflows/ci.yml` — the two e2e jobs on pushes to `main` and by hand only, no longer on pull requests (each run pays the LLM and the ASR); a missing key, device or sim capability always fails them.
- `specs/e2e.md`, `specs/ci.md` (`Updated`, back to `Implemented` once CI is green), `specs/_index.md`, `plans/_index.md` — the design and the statuses.

## Steps

1. Write the room: one output stream into the person-voice device at 48 kHz; a phonon engine with a capturing sink, its lines resampled and queued; given an echo device, an input stream from it whose blocks enter a 50 ms delay line at −10 dB; the output callback mixes the two. `ready` once both streams run, `done` once a line's last sample has been played.
2. Resolve the echo device: `WICA_E2E_ECHO_DEVICE`, else the speaker when it is also an input device, else none. The computer's config gets `audio_processing.echo_cancellation.enabled` from it.
3. The robot: the named cable and no echo; the room is told not to play its voice back.
4. The conversation: a class-scoped fixture building the system as the function-scoped one does; steps marked with `TestRun.note`; each step settles the agent and ends a leftover dictation first; checks over the step's own window; the echo check last.
5. On the Mac (BlackHole 2ch, no echo): both conversations and the focused scenarios. If BlackHole 16ch is installed, the computer with the echo.
6. CI: the echo device. Push, read both jobs, fix what they show.

## Verification

- `uv run pytest tests-e2e -rs` on the Mac: the focused scenarios and both conversations pass, apart from the occasional misheard sentence a re-run clears.
- CI green on both e2e jobs, the computer's with the echo on and its canceller.
- Lint, format, types, the fast tier.

## What CI showed

- **The robot:** `webrtcdsp`, the daemon's software canceller, is not in Ubuntu 24.04 (it left `gstreamer1.0-plugins-bad` after 22.04), so the robot's echo was dropped: decided with the maintainer to keep it simple, the echo is tested on the computer only.
- **The computer:** with the room's echo on, its canceller did not remove it on Linux: the app's own sentences came back as speech inputs, the note step lost its passage to them, and the echo check failed. The same canceller removes a 50 ms echo offline; the guess is a path several PulseAudio hops long, hundreds of milliseconds. To measure on CI next.
