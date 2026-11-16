# Personas: the prompt files

The persona is what makes the agent a voice assistant or a small robot: a Markdown file the model reads as its system prompt, with the rules the body needs. This page is about editing those files. What the model then sees and can do is [../reference/agent.md](../reference/agent.md).

## One file per body

| Body | File | Selected by |
|---|---|---|
| the computer | [prompts/computer.md](../../prompts/computer.md) | `wica.agent.system_prompt_file` in the computer config |
| the Reachy Mini | [prompts/reachy.md](../../prompts/reachy.md) | `wica.agent.system_prompt_file` in the reachy config |

The path resolves relative to the config file ([../reference/configuration.md](../reference/configuration.md#wica-the-agent)). A persona that cannot be read is an `Agent:` banner line: the page opens, the agent does not.

## What the files say

Both files share four sections:

- **How you speak.** Everything the person hears goes through `say`; free text is private thinking, never heard. Replies are short and natural to hear aloud: a sentence or two, no bullet points, headings, code blocks or emoji, numbers spelled out the way one says them. The agent may choose not to react.
- **Turns.** One turn at a time: each thing the person says arrives as one complete utterance; reply, then wait. No greeting or sign-off every turn.
- **Dictation.** When the person wants to dictate something longer, call `start_dictation`; its reply says what ends the passage, which is for the model to know, not to announce. An empty passage means nothing came through: say so briefly and offer to try again.
- The computer's file opens as *a friendly, helpful voice assistant*; the robot's as *the voice and mind of a Reachy Mini*, in a spoken, physical interaction.

The robot's file adds three sections of its own:

- **How you move.** `play_emotion` plays a short recorded motion, sparingly and in support of the conversation; the names are listed under "Your emotions" further down. The motors are on: just move, never check first; only if a move reports them off, enable them and play again; rest in `gravity_compensation` or `disabled`. Don't narrate movements. Idle sway and face following happen on their own, with no command for any of it.
- **One body: never collide, act in order.** The action rules below.
- **Always answer out loud.** Acting is not answering: do what was asked, then say what was done.

## What is appended at startup

The file is not the whole prompt. The robot's emotion names, the "Hand signs" section and WICA's runtime primer are appended when the agent is built, in a fixed order, from the config and the code: [../reference/agent.md](../reference/agent.md#the-persona-what-is-appended-at-startup).

## The action rules

WICA's primer tells the model that every Command it issues in one response starts at once and runs concurrently. The robot has one body and one speaker, so the robot persona adds three rules the framework cannot know ([../../specs/reachy-embodiment.md](../../specs/reachy-embodiment.md), "Persona"):

1. **No colliding Commands in one turn.** `say` with `play_emotion` (the emotion plays its own sound over the voice and moves the head while it talks), two emotions, a move with a change of the motors' state, a `cancel_command` with the action that replaces what it cancels. When both are wanted: issue the first, then the next in a later turn, once the first is seen complete. Commands that touch different things may go together.
2. **Order matters.** Dependent actions go in order across turns: re-enable the motors before retrying the move that reported them off, stop one thing before starting the next, say something before playing the emotion that goes with it.
3. **Spoken feedback for every request.** When the person asks to start, stop, change or cancel something, do it, then say what was done once it is seen done. A request never ends in a silent action. This rides on WICA waking the agent when a Command completes.

They are **prompt rules, not guards**: nothing in the code serializes `say` and `play_emotion`, and the tests drive a scripted model that never exercises the model's compliance. A model that keeps colliding is a benchmark finding ([benchmarking.md](benchmarking.md)), and a shared lock is the deferred fix. The hand-sign section's `stop` rule follows the same shape: cancel in one turn, confirm in the next.

## What not to write

Some facts are seeded into the prompt from the config or the code, so the file must not hard-code them, or the two drift:

- **How a dictated passage ends.** It depends on the config's `dictation_default_segmentation_mode` ([dictation.md](dictation.md)), and the engine's `trigger_word` mode runs no timer at all, so a sentence like "delivered when they pause" is wrong against a trigger-word config. `start_dictation`'s reply tells the model the mode in force.
- **The emotion names.** They come from the robot's library at session start and are appended as "Your emotions"; the file only says to use the names listed further down.
- **The hand vocabulary.** The "Hand signs" section is appended only when vision is on, from the same table the classifier's labels map through. A file that described hands would describe them to an agent with no camera.
- **Narrating movements**, or anything about presence, breathing or wobbling: the bridge does those on its own, and a persona that mentions them had the model treating idle aliveness as its job.
- **The users.** The "The users around you" section is appended when the robot has a face detector: what the four users entries say, that a user's number is not a name, and how to follow someone with `start_tracking`. A file that described people would describe them to a robot that sees none.
- **A chatty style.** The replies are heard, not read: markdown, lists and emoji come out of a speaker as noise.

## Editing and language

Edit the file and restart the app; the prompt is composed once, before the agent starts, and lands in the model's cached prefix. For a different persona, copy the file, edit it, and point `system_prompt_file` in your config copy at it.

The committed files are in English. They do not fix the language of the conversation: the model answers in the language the person speaks on its own. What decides which languages are *transcribed* is the speech recognizer's module and model ([../reference/configuration.md](../reference/configuration.md#asr-speech-in)): the templates' `flux-general-en` is an English model, so a conversation in another language starts with a multilingual recognizer model, such as Deepgram's `nova-3` through the `deepgram_v1` module, and a voice that speaks that language. A persona written in another language works the same way.
