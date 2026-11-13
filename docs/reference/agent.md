# The agent's surface

What the model sees and can do: the World entries it reads, the Commands it can call, the text its persona is given at startup, and how each shows in the page's transcript. This is the page for whoever edits a persona ([../guides/personas.md](../guides/personas.md)) or reads a transcript. The design is [specs/app.md](../../specs/app.md) ("World, Commands, persona") and the embodiment specs it links.

WICA's model is that you do not thread a conversation: you maintain **World state**, and every reasoning step renders the relevant state into the prompt. This app keeps that World deliberately tiny.

## The World

| Entry | Registered | Holds | Rendered in the prompt as |
|---|---|---|---|
| `speech_input` | always | what was just heard: a closed speech segment, or a typed line. Written verbatim, an empty passage included | `You hear someone say: "<text>"`; a passage that closed a dictation: `Dictation is over. You heard this complete dictated passage: "<text>"` |
| `hand_gesture_1` … `hand_gesture_<max_hands>` | only when `vision.enabled` is `true` ([configuration.md](configuration.md#vision-hand-signs)) | the gesture just detected on hand `n`: one of `stop`, `like`, `dislike`, `ok`, `one`, `two`, `three`, `four`, `five`, or `None` | `Hand gesture detected on hand {n}: "{sign}".` |
| `user_event` | robot only, with a face detector ([../guides/reachy-mini.md](../guides/reachy-mini.md#the-users-in-front-of-the-robot)) | what just happened to the robot's users, one line per event: a user came into view, or left it. Events a moment apart are written together | `A user came into view: user 3, about 0.7 m away, in front of you.` · `User 3 left your view.` · at start-up, `A user is in view as you start: user 3, …` |
| `users_in_view` | robot only, with a face detector | who is in view now, and where. Omitted while detection is not running | `In view: user 3 (about 0.7 m away, in front of you), user 5 (about 1.5 m away, to your left; out of your sight right now).` · `Nobody is in view.` |
| `personal_space` | robot only, with a face detector; judged only while the head follows someone, omitted otherwise | which user, if any, is very close, inside the personal space; for 2 s after a change, what just changed | `User 3 just came into your personal space: they are very close to you (about 0.3 m away).` · `User 3 just stepped back out of your personal space; nobody is in it now.` · then, settled, `User 3 is in your personal space, …` · `Nobody is in your personal space.` |
| `tracked_user` | robot only, with a face detector | whom the head follows, or that it follows nobody | `Your head is following user 3, as you chose.` · `Your head is following user 3.` · `You chose to follow user 3, who is not in view; your head is waiting for them.` · `You are not following anyone with your head.` |
| `agent:command:<call_id>` | automatic, one per Command in flight | the Command's state: `running`, then `complete`, `failed` or `cancelled`, with its result | WICA's own rendering of a tracked action |

`speech_input` triggers a reasoning step on every write, the same text twice included. A hand entry triggers only when a sign is **shown** (`None → stop`, or a direct switch `two → three`); a hand relaxing updates the entry silently. Hand numbers are slots in the order the hands appeared, not left and right ([../guides/hand-signs.md](../guides/hand-signs.md)). `user_event` triggers on every write; a user who was not seen leaving (not looked at for 20 s, or detection stopping) changes only `users_in_view`. `personal_space` triggers when who is inside changes: someone coming in, stepping back out, another taking their place; not when tracking starts or stops. `users_in_view` and `tracked_user` never trigger. A user's number is a track, not an identity: the same person coming back after a break gets a new one. No other embodiment state is mirrored into the World: the robot's motors, its head's pose, the camera frame itself are not entries; the users' state entries are the exception, there so the agent knows whom it may follow in the step where it decides. The robot's entries hold objects (`UserChange`, `UserInView`, `PersonalSpace`, `TrackedUser`): the **World state** panel shows those, the prompt the wording above.

## Inputs are transient

The inputs are **one-shot perceptions**, not standing state: the person *said* something, a hand *showed* a sign, a person *came into view*. Two World mechanisms make each observed once and then gone:

- **A TTL on each entry.** `speech_input` and `user_event` reset to `None` **2 s** after the write that set them, each hand entry **1 s** after its sign landed. The reset never triggers a step; the step the input triggered observes it well within the window.
- **`None` renders nothing.** An entry holding `None` is omitted from the prompt entirely, envelope included. There is no "the person has not said anything" line and no "hand 1 shows no sign" line: a step carries a `speech_input` block only in the step the utterance triggered, and a hand block only while that hand's sign is live.

History keeps what was observed, as each step's own snapshot, so the model still sees earlier utterances and signs in context, just not as current. Two consequences are accepted by design: an utterance whose trigger was dropped because a step was in flight is lost unless a step observes it within 2 s, and a sign the person keeps holding reads as no sign after 1 s, until the hand changes ([../guides/limits.md](../guides/limits.md)).

## The Commands

Every Command is non-raising: it returns a readable string, success or failure, so the model always learns what happened. The output Command, `say`, is the body's voice: with it set, the model's free text becomes private reasoning and everything the person hears goes through `say`.

**Both bodies:**

| Command | What the model reads | Replies |
|---|---|---|
| `say(text)` | computer: *"Speak `text` aloud to the user through the machine's speakers. This is your voice: whatever you want the user to hear, say it here. Returns "OK" when the utterance finished playing."* Robot: *"Speak `text` aloud to the person, out of the robot's speaker. …"* | `"OK"` once the utterance has finished playing, so the Command stays `running` for the real duration of the speech; `"TTS error: text must not be empty"` for an empty string; `"TTS error: <why>"` when the voice failed to build or to synthesize |
| `start_dictation(segmentation_mode=None)` | *"Switch speech capture to dictation mode for one longer passage. … The reply tells you what ends the passage (a pause, or a word the user says). Dictation ends automatically after that one passage."* | `"Dictation started (<mode> mode): <how it ends>"`, naming the config's trigger words in `trigger_word` mode; `"Could not start dictation: <why>"` when the engine refuses (not running, already dictating, unknown mode). Only registered when the speech recognizer built ([../guides/dictation.md](../guides/dictation.md)) |
| `stop_dictation()` | revert to conversation without waiting for the passage to close | a readable string either way; the page's **Stop dictation** button calls the same thing |
| `noop()` | WICA's own: do not react this step | |
| `cancel_command(call_id)` | WICA's own: cancel a running Command by the id of its World entry | a cancelled `say` stops the speech; a cancelled move stops being commanded |

**The robot adds** ([specs/reachy-embodiment.md](../../specs/reachy-embodiment.md), "`RobotControl`"):

| Command | What the model reads | Replies |
|---|---|---|
| `play_emotion(name)` | play a short recorded motion; when the emotion library was read at startup, the description lists the valid names | a short confirmation; `"Can't do that: the motors are off. Call set_motors_state('enabled') first."` when the motors are not enabled; `"play_emotion failed: <why>"` followed by `Valid names: …` for an unknown name |
| `get_motors_state()` | report `enabled`, `disabled` or `gravity_compensation`; the description says to assume enabled and read back only after a move reported them off | the state |
| `set_motors_state(state)` | set `"enabled"`, `"disabled"` or `"gravity_compensation"`; the description says to call it only to recover after a move reported the motors off, or to rest | the state read back; `"set_motors_state failed: <why>"` for an unknown state |
| `start_tracking(user=None)` | with a face detector: follow a user with the head, to show whom the robot pays attention to; a number from `users_in_view` follows that user only, none follows whoever is in front | `"OK — your head now follows user <n>."` or `"… whoever is in front of you."`; `"Can't follow user <n>: they are not in view."` when they left meanwhile |
| `stop_tracking()` | with a face detector: stop following; the head goes back to its idle movement | `"OK — your head no longer follows anyone."` |

The computer registers no action Command: it has nothing to act with beyond speaking. There is no `list_emotions`: the names are given to the model up front, in the persona and in the tool description. Presence, breathing and wobbling are the bridge's, not the agent's; no Command touches them. Following a person is the agent's choice of *whom*; where the head aims while it follows is the bridge's. The tracking pair collides with nothing: it may go in one turn with `say` or an emotion.

## The persona: what is appended at startup

The system prompt the model reads is composed once, before the agent starts, in this order:

1. **The persona file** named by `wica.agent.system_prompt_file` ([configuration.md](configuration.md#wica-the-agent)), verbatim: `prompts/computer.md` or `prompts/reachy.md`.
2. **The body's addendum.** The robot appends a "Your emotions" section naming every emotion its library holds, read once from the bridge when the session opened, then, with a face detector, a "The users around you" section: what the four users entries say, that a number is never said aloud, that arrivals, departures and someone in the personal space may be met or let pass, and how to follow someone with `start_tracking`. The computer appends nothing. When the library could not be read, the emotions section is absent and the banner says `Robot: emotions unavailable`.
3. **The "Hand signs" section**, only when `vision.enabled` is `true` and the body is up. It says a camera detects hand gestures, names the nine signs, explains that a sign state says a gesture was *just detected*, not by whom, and disappears a moment later, that hand numbers are not left and right, and gives the one instruction the vocabulary carries: on `stop`, cancel every running Command and start nothing new, then confirm out loud in the next turn. What `like`, `ok` or a count means is left to the agent on purpose.
4. **WICA's runtime primer**, appended by the framework: how to read the observations, that Commands run asynchronously and all the calls of one response run at once, that the person is addressed through the output Command, and that `noop` is allowed.

Two and three are seeded from the config and the code rather than written in the file, so a persona never describes hands it cannot see, users it cannot see or emotions it cannot play, and the same file serves with and without a camera. The dictation reply plays the same role for how a passage ends: the mode is the config's, so `start_dictation`'s reply tells the model, not the file ([../guides/personas.md](../guides/personas.md#what-not-to-write)).

## What the transcript shows

The **Conversation** panel is WICA's transcript component, with one app-owned hook that names things the way a person reads them. The same hook labels the **Reaction** panel, so transcript items and reaction labels agree.

| In the prompt | In the transcript |
|---|---|
| a `speech_input` write | an input item `🗣️ "<text>"` |
| a `hand_gesture_<n>` sign | an input item `✋ hand <n>: <sign>` |
| a `user_event` | an input item `👤 <the event>` |
| a `personal_space` change | an input item `📏 <who is in the personal space>` |
| a reaction whose prompt carried `users_in_view` or `tracked_user` | labelled `👥 <who is in view>` or `🎯 <whom the head follows>` in the Reaction panel |
| a reasoning step | a collapsible **💬 reaction** holding the step's outputs, labelled by channel |
| a `say` call | **🗣️ say**, with the spoken text as the body: what the person heard |
| the model's free text | **💭 output sink**: private reasoning, never heard |
| any other Command | **🦾** with its name and arguments |
| `noop` | **🚫 noop** |

Every Command item shows its **execution state live**: a spinner while it runs, then ✅ complete, ❌ failed or ⏹ cancelled, with its result and duration. A `say` is therefore visibly in flight for the real duration of the speech, which is how the one-step-at-a-time rule reads on the page: a new utterance that arrives while a reaction is still spinning is the one that gets dropped ([../guides/limits.md](../guides/limits.md#one-reasoning-step-at-a-time)). The **World state** and **Reaction** panels, behind the **Show debug panels** toggle, show the raw entries and the exact prompt of every step.
