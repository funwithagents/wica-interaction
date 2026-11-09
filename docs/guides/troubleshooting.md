# Troubleshooting

The checks for each symptom, in the order to make them, each pointing at the page that owns the explanation. The app's own signals come first: the **banner** at the top of the page, the **ASR** status line, the **Vision** line, and the result of each Command in the transcript (a ❌ item shows the reply the model got, a `TTS error: …` among them). For a full trace, run with WICA's logs on: the app configures Python logging, and `logging.getLogger("wica")` at `DEBUG` shows every trigger, step, Command and cancellation.

## The banner

The page opens even when something could not be built, with one line per failure. What each prefix means, and what to do:

| Line | Cause | Fix |
|---|---|---|
| `Agent: environment variable 'X' is not set` | the agent's key variable is unset in the shell that ran the command | export it and restart; a non-interactive shell does not read your `~/.zshrc` |
| `Agent: …` (any other message) | WICA rejected the config, or the persona file could not be read, or the provider's extra is not installed (`ImportError` naming it) | the message is WICA's: an unknown `agent` key, a bad `system_prompt_file` path (relative to the config file), `uv sync --extra wica-<provider>` ([../reference/configuration.md](../reference/configuration.md#wica-the-agent)) |
| `ASR: …` | the speech recognizer could not be built: its key variable unset, a bad `module`, or its extra absent | export the key, check `asr.engine.module`, `uv sync --extra asr-deepgram`; meanwhile the text box drives the agent ([../reference/configuration.md](../reference/configuration.md#asr-speech-in)) |
| `TTS: …` | the voice could not be built: its key unset, its `tts-<type>` extra absent, a local model that failed to load, or (robot) no `reachy.tts` block | fix the `tts` block or install the extra. The body is up and `say` stays wired: the model gets `TTS error: …` as the Command's reply, so you see in the transcript what it tried to say ([../guides/computer.md](../guides/computer.md), [../guides/reachy-mini.md](../guides/reachy-mini.md#the-voice)) |
| `Audio processing: …` | the computer's echo canceller could not be built: `aec-audio-processing` or `samplerate` absent (`uv sync --extra audio-processing`, a C++ toolchain on Linux), or the ASR audio format is not 16 kHz mono linear16 | the mic goes straight to the recognizer, so the agent may hear itself until it is fixed ([../guides/computer.md](../guides/computer.md#echo-cancellation)) |
| `Reachy Mini: …` | the robot could not be connected: no daemon reachable, a simulator that did not come up in time, the robot not on USB, a bridge or upstream error | the line carries the original message plus the offline hint: `reachy.backend` `"fake"` with no `daemon` block, or `"sim"`. See [The robot does not start](#the-robot-does-not-start). With no body the agent still reasons over typed text, but has no voice and no actions |
| `Robot: emotions unavailable: …` | the robot is up, but the recorded-moves library could not be read (no network on first run) | the prompt carries no emotion names and `play_emotion` keeps its plain description; connect once so the library downloads ([../reference/install.md](../reference/install.md#first-run-downloads)) |
| `Vision: …` | the hand-sign input could not be constructed | a config or programming error at build time; a model that fails to load after start shows on the Vision line instead ([../guides/hand-signs.md](../guides/hand-signs.md#the-vision-line)) |
| a message with no prefix, and no agent at all | the config file itself did not load: missing, not JSON, a required section absent, a section the command does not accept, a field an engine rejected | the message names the file and the field ([../reference/configuration.md](../reference/configuration.md#what-a-loader-rejects)). A reachy file handed to the computer command fails on `reachy` |

## Nothing happens after I speak

- **Does the ASR line say `running`?** `stopped`, or `ASR unavailable`, means the recognizer was not built: see the banner. A `⚠️ capture:` tail means the engine runs but its microphone stream died (`mic stream failed: …` on the robot): restart the app.
- **Do your words appear in the live speech box?** If not, the recognizer hears nothing: the wrong device in `asr.engine.audio.device` (the computer; `null` is the system default), no microphone permission for the terminal (macOS), the robot's mic on another media session. With a device listed and still nothing, check the level with another application.
- **They appear and clear, but no 🗣️ shows on the left.** The utterance never closed into a final segment, or it did while the agent was busy. A trigger arriving while a reasoning step is in flight is dropped, not queued ([limits.md](limits.md#one-reasoning-step-at-a-time)): wait for the previous reaction to finish and say it again.
- **A 🗣️ shows, a reaction opens, but there is no 🗣️ say.** Open the reaction: a **🚫 noop** means the model chose not to answer (ask directly); a 💭 item alone means it wrote free text instead of calling `say`, which the persona forbids and which a weaker model does anyway ([personas.md](personas.md)); a ❌ say shows the `TTS error: …` the voice returned.
- **The say runs but you hear nothing.** The computer: the speaker in `tts.engine.player.device`, the system volume, a voice whose `gain_db` is too low. The robot: the robot's speaker and the media session; on the `fake` backend nothing is heard by design.
- **Typed text works, speech does not.** Then the agent is fine and the problem is upstream of it: the microphone, the recognizer's key, or echo cancellation cleaning too much (a `stream_delay_ms` far from the real latency can eat speech, [computer.md](computer.md#echo-cancellation)).

## The agent hears itself

The transcript shows the agent's own sentences coming back as 🗣️ input, and it answers them.

- **On the computer**, echo cancellation must be on: `audio_processing.echo_cancellation.enabled`, the `audio-processing` extra installed, and no `Audio processing:` banner line. Tune `stream_delay_ms` to your audio path's latency ([computer.md](computer.md#echo-cancellation)). Headphones remove the loop outright.
- **On the robot**, the XVF3800 cancels the echo in hardware only when the voice plays through the robot's own media session, which is how the app plays it. A `tts.engine.player.device` has no place in a reachy config (the loader rejects it); the robot's volume still matters at the extremes.

## No camera frame, or no sign

- **Is vision on?** `_Vision off_` on the Vision line means `vision.enabled` is false, or there is no body. With it on, the line reads `running` and the hand-stage and classifier rates climb once the models are loaded ([hand-signs.md](hand-signs.md#the-vision-line)).
- **`⚠️ error:` on the line.** A model failed to load: no network on the first run (the download is a few hundred megabytes), or the `vision` extra is not installed. Fix it and restart; the app itself keeps running.
- **`⚠️ camera: no camera frame for 3 s`.** The camera publishes nothing. The computer: the wrong `camera.device`, no camera permission for the terminal (macOS), the webcam held by another application; a `camera open failed: …` tail says which, and the source retries so plugging the camera in heals it. The robot: a headless simulator on macOS has no camera (no GL context without a window), a daemon that has not delivered its first frame yet, a Lite whose camera is held by another process; the bridge's [troubleshooting page](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/guides/troubleshooting.md) has the robot's camera checks.
- **Hands are seen, no sign is delivered.** The label is `below threshold`: lower `vision.threshold`, or improve the light. The sign flickers between two classes: the hold filter needs it unbroken for `hold_s`; hold it steadier, or lower `hold_s`. `no crop` means the hand is at the frame's edge.
- **A sign is delivered and the agent does nothing.** Only `stop` carries an instruction; the agent decides what the others mean from context. A sign shown while the model is still reasoning is lost ([limits.md](limits.md#one-reasoning-step-at-a-time)).
- **Hand 1 took hand 2's sign.** Hands are slots in order of appearance, and the remaining hand shifts to slot 1 when the other leaves ([hand-signs.md](hand-signs.md#limits)).

## The robot does not start

The banner reads `Reachy Mini: …`. The message is the bridge's or upstream's, with the offline hint appended.

- **The simulator never becomes ready.** `DaemonError` after `startup_timeout` (45 s): the `sim` extra is not installed (`uv sync --dev`), the viewer needs an unlocked GUI session (and `mjpython` on macOS, which MuJoCo's wheel provides), or on Linux the GStreamer webrtc plugin is missing. The bridge's [running-daemons guide](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/guides/running-daemons.md) and [Linux guide](https://github.com/funwithagents/reachy-mini-bridge/blob/main/docs/guides/linux.md) cover each.
- **The robot is not found on USB.** The hardware daemon needs the serial port, which the bridge auto-detects; check the cable and that no other daemon or application holds the robot. One media daemon per machine: stop a simulator before plugging the robot.
- **`daemon.spawn` on the `fake` backend.** The loader rejects it: delete the `daemon` block with the fake ([../reference/configuration.md](../reference/configuration.md#reachy-the-robot)).
- **`motion.tracking` with no detector.** A bridge `ConfigError` at load: name `face_detection.detector` (`"yunet"`), or turn tracking off.
- **`motion.tracking` is an object, not a boolean.** A config written before the bridge's tracking posture says `"tracking": true`; write `"tracking": { "enabled": true }`.
- **The viewer opens but the page says the body is absent.** The daemon answered too late: raise `startup_timeout`, or start the app again while the daemon the bridge spawned is still up (`spawn: "auto"` reuses a daemon already serving at the address).

## No voice, or a quiet one

- **`TTS:` in the banner.** The voice did not build: see the banner table. The robot with no `reachy.tts` block has no voice at all.
- **`say` items fail with `TTS error: …`.** The reply names the cause: an empty text (the model called `say` with nothing), a provider error, a key rejected, a local model that failed to load after start.
- **The first `say` takes long.** A local voice downloads its weights on first use ([../reference/install.md](../reference/install.md#first-run-downloads)); a hosted one needs the network.
- **One voice is much louder or quieter than another.** Each voice speaks at its own level; `gain_db` in the engine block levels it, and tts-engine ships a tool to find the value ([tts-engine's configuration page](https://github.com/funwithagents/tts-engine/blob/main/docs/configuration.md#output-gain)).
- **On the robot, the head does not sway while it speaks.** `motion.wobbling` is off, or paused for an emotion.

## The model misbehaves

- **It plays an emotion and talks at the same time, and the sound collides.** The no-collision rule is a prompt rule, not a guard; a model that ignores it needs a stronger one, or a better model ([personas.md](personas.md#the-action-rules)). Compare models on exactly this with the [benchmark](benchmarking.md).
- **It checks the motors before every move.** The persona and the tool descriptions tell it not to; some models do anyway. It costs a turn, nothing else.
- **It answers a stop sign with a cancel and nothing spoken.** The persona asks for the confirmation in the next turn, after the cancellation is observed; a model that stays silent breaks "Always answer out loud" ([personas.md](personas.md#the-action-rules)).
- **It replies to an utterance from two turns ago.** An input is cleared two seconds after it lands and omitted from later prompts; a model answering it later is reading its history, not a current input ([../reference/agent.md](../reference/agent.md#inputs-are-transient)).
- **Gemini is slow on every turn.** It thinks by default: `model_kwargs: {"thinking_level": "low"}` ([WICA's configuration reference](https://github.com/funwithagents/wica-framework/blob/main/docs/reference/configuration.md#providers)).

## The page

- **Typing raises `World is not running`.** The agent was not built (see the banner) or the app is shutting down.
- **The World panel shows a note instead of a table.** No agent: the banner says why.
- **Closing.** Stop the command with Ctrl-C: the app closes the agent, stops vision and the recognizer, exits the body (the robot goes to sleep, a daemon the bridge spawned is stopped) and ends. A step that hangs is given five seconds and skipped, so a stuck device cannot strand the process.
