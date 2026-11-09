# Dictation

How speech reaches the agent, and how to speak a longer passage without it being chopped into a turn per sentence. The design is [specs/asr-bridge.md](../../specs/asr-bridge.md).

## Conversation and dictation

The speech recognizer closes a **segment** of speech and the app writes it to the agent as one utterance, which triggers one reasoning step. While a segment is still open, its growing text streams into the ASR panel's `Live speech (interim)` box and nothing reaches the agent: partial speech is visible but inert, and only a closed segment wakes the agent, exactly once, as one complete utterance.

In **conversation**, the default, a segment is one spoken sentence: you talk in short turns and the agent replies to each. In **dictation**, a segment is one longer passage, a message, a note, a paragraph, delivered to the agent whole. Dictation is entered by the **agent**, on request: when you say you want to dictate something, it calls its `start_dictation` command, and the recognizer switches mode for exactly one passage, then reverts to conversation on its own. Speech still flows the same way during dictation; the only difference is how long the recognizer waits before closing the segment. The agent is still fed and still replies.

The ASR panel's status line shows the current mode: `ASR: running · mode: conversation (utterance)` or `mode: dictation (timeout)`.

## How a passage ends

What closes a dictated passage is the speech recognizer's `dictation_default_segmentation_mode` ([reference](../reference/configuration.md#asr-speech-in)):

| Mode | The passage is delivered when | Fields |
|---|---|---|
| `timeout` | you stop speaking for a few seconds (the computer template) | `segmentation.end_of_speech_timeout_s`, 5 s by default; `initial_silence_timeout_s`, 10 s, when nothing was said at all |
| `trigger_word` | you say one of the trigger words; a pause does **not** end it (the reachy template) | `segmentation.trigger_words`; by default `submit`, `enter`, `validate`, `send`, `confirm`, `go` and their French equivalents, matched as case-insensitive substrings of a sentence |

The reply `start_dictation` returns says which it is, `Dictation started (timeout mode): …` or `(trigger_word mode): …`, naming the configured words. That reply is for the model, not for you: neither it nor the persona asks the agent to announce how the passage ends, since you chose the mode and an extra spoken turn before every dictation would break the flow. It is there so the model's picture matches the config, for what it answers if asked and what it makes of the passage it then receives.

The persona files say nothing about how a passage ends, for the same reason: the mode is a config field, and a prompt that said "when you pause" against a `trigger_word` config would describe a passage that never closes that way ([personas.md](personas.md)).

## The stop button

`Stop dictation`, in the ASR panel, is the manual hatch out of dictation without waiting for the passage to close. It is enabled only while a dictation is in progress; pressing it reverts the recognizer to conversation mode. The agent has the same command, `stop_dictation`, but with a passage that ends on its own it rarely needs it.

## The empty passage

A `timeout` passage can close with nothing in it: you asked to dictate, then said nothing for `initial_silence_timeout_s`. The recognizer still closes the segment, and the app writes it as an empty utterance, so the agent can react to you saying nothing. Both personas tell the model what that means: the passage ended empty, say briefly that nothing came through, and offer to try again, without re-entering dictation on its own ([agent.md](../reference/agent.md)).

A `trigger_word` passage runs no timer, so it never closes on silence: it ends when you say a trigger word, or when you press the stop button.

## Typed text

The text box in the ASR panel is the keyboard way in. Submitting a line, with Enter or the `Send` button, writes it to the agent exactly as a closed segment would, so it flows through the same reason and reply path and shows in the Conversation as something you said. It needs neither the speech recognizer nor its key: with only the agent's key set, the app opens with an `ASR:` banner line and the text box is the way to talk to it ([troubleshooting.md](troubleshooting.md#the-banner)).

One difference from speech: an empty or whitespace-only typed line is ignored, while an empty spoken passage is delivered. Typed text shares the agent's one-step-at-a-time rule with speech: a line sent while a step is in flight is dropped, not queued ([limits.md](limits.md)).
