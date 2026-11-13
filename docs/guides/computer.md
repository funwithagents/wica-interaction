# The computer embodiment

The agent on the machine the app runs on: it listens through a local microphone, speaks through the speakers with a [tts-engine](https://github.com/funwithagents/tts-engine) voice, and, with a webcam, sees the person's hand signs. It has no actions beyond speaking. The design is [specs/computer-embodiment.md](../../specs/computer-embodiment.md), with the microphone processing in [specs/audio-processing.md](../../specs/audio-processing.md).

## What it is

A plain voice assistant. Speech in goes through the speech recognizer, the agent reasons over what was said, and its reply is played through the voice. The persona is [prompts/computer.md](../../prompts/computer.md): a friendly assistant in a spoken conversation that speaks only through `say`, may choose not to react, and takes dictation when asked ([personas.md](personas.md)). Everything is stood up from the computer template, [config.computer.example.json](../../config.computer.example.json), whose sections are in the [configuration reference](../reference/configuration.md#one-file-per-embodiment).

Compared with the robot, the computer has to solve one problem the robot's hardware solves for it: with a live microphone open while the speakers play, the agent would hear its own voice. That is what [echo cancellation](#echo-cancellation) is for.

## Devices

Three devices, each named in its own section of the config file:

| Device | Field | Value |
|---|---|---|
| the microphone | `asr.engine.audio.device` ([reference](../reference/configuration.md#asr-speech-in)) | a device name, an index, or `null` for the system default |
| the speaker | `tts.engine.player.device` ([reference](../reference/configuration.md#tts-the-computers-voice)) | a device name, an index, or `null` for the system default |
| the webcam | `camera.device` ([reference](../reference/configuration.md#camera-the-computers-webcam)) | an OpenCV index (`0` by default), or a path or URL |

To list the audio devices and their indices:

```
uv run python -c "import sounddevice; print(sounddevice.query_devices())"
```

On macOS, the first run asks for microphone access, and camera access when vision is on, for the application the command runs in: your terminal, or your editor. Deny it and the recognizer hears nothing and the webcam fails to open; the latter shows on the Vision line ([the webcam](#the-webcam)). On Linux the microphone and speakers need PortAudio from the system ([install.md](../reference/install.md#system-packages)).

## Echo cancellation

The microphone stays open while the voice plays, so the speaker's output travels through the air back into the microphone, the speech recognizer transcribes it, and it lands as something the person said: the agent reacts to its own voice. Echo cancellation taps what the voice is about to play as a reference signal and subtracts it from the microphone, in a WebRTC audio processor placed between the voice and the speech recognizer. It is on in the committed template and needs the `audio-processing` extra ([install.md](../reference/install.md#extras)).

The fields are `echo_cancellation.enabled` and `echo_cancellation.stream_delay_ms` ([reference](../reference/configuration.md#audio_processing-the-computers-mic-processing)). You rarely need to touch the delay: the reference is captured as the voice plays, ahead of what the microphone hears by the output buffer plus the acoustic path, and the canceller measures that gap itself. The current canceller (WebRTC's AEC3) all but ignores `stream_delay_ms`; the template's `50` does no harm.

When the extra is not installed, or the WebRTC binding failed to build, the app still opens: the banner carries an `Audio processing:` line with the reason, and the microphone goes to the speech recognizer raw ([troubleshooting.md](troubleshooting.md#the-agent-hears-itself)). The speech recognizer must be configured for 16 kHz mono, the default, which is the processor's working rate; another format is reported on the same banner line.

**With headphones, turn it off.** Headphones remove the loop outright, and a canceller with no echo to learn from mutes you instead: until it has heard a few seconds of its own voice come back, it treats anything the microphone picks up while the voice plays as echo and suppresses it, so talking over the agent's first replies does not reach the speech recognizer. On loudspeakers it learns the echo within a second or two and lets your voice through, apart from the first fraction of a second of speaking over it.

Echo cancellation removes the self-hearing loop. It does not add barge-in: talking over the agent is still not supported ([limits.md](limits.md)).

## Noise suppression

The same processor can attenuate steady ambient noise, a fan, a hum, a hiss. It needs no reference signal and works with or without echo cancellation: `noise_suppression.enabled` and `noise_suppression.level`, WebRTC's aggressiveness from `0` (mild) to `3` (max), `2` by default ([reference](../reference/configuration.md#audio_processing-the-computers-mic-processing)). The template leaves it off: suppression can hurt recognition as often as it helps, so enable it per environment and listen to the result in the transcript.

With every feature off, the section can be dropped: the microphone and the speakers run exactly as the engines open them, with nothing in between.

## The webcam

With `vision.enabled` on ([hand-signs.md](hand-signs.md)), the app opens the camera named by `camera.device` through OpenCV and reads it at `vision.fps`: a webcam delivering thirty frames a second into a five-frame-a-second hand stage would be wasted captures, so the source paces itself and nothing downstream runs faster than the configured rate.

A camera that cannot be opened is visible, not fatal. The app opens and the Vision line shows `⚠️ camera: camera open failed: …`; the source keeps retrying at its poll rate, so a camera plugged in later heals the line without a restart. A camera that goes away mid-run is reported and retried the same way ([troubleshooting.md](troubleshooting.md#no-camera-frame-or-no-sign)).

The frame is classified as captured, not mirrored. The classifier does not care which way a hand faces, and only a display would.

## Running it

```
uv run wica-interaction-computer --config config.computer.example.json
```

The template needs three variables in the shell that runs the command: `WICA_ANTHROPIC_API_KEY` for the agent, `DEEPGRAM_API_KEY` for the speech recognizer, `ELEVENLABS_API_KEY` for the voice ([keys](../reference/configuration.md#keys-stay-in-the-environment)). Echo cancellation and hand signs are on as committed; set `vision.enabled` to `false` to run without a camera.

The page opens at the address Gradio prints, titled `WICA Interaction — computer`. On the left, the Conversation: what you said, and each of the agent's reactions with its `🗣️ say` and its private `💭` thinking. On the right, the ASR panel: the live transcript of what you are saying, the recognizer's status and mode, the Stop dictation button, the text box to type a turn instead of speaking it, and the Vision line. A `Show debug panels` checkbox reveals the live World state and every reaction's prompt and timing. If a key is unset or an extra missing, a banner at the top says which part did not start and the rest still runs ([troubleshooting.md](troubleshooting.md#the-banner)).
