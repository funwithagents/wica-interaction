# What v1 does not do

The boundaries of the app as it stands, so a behaviour you meet is read as a known limit rather than a bug. Most follow from WICA's own v1 model, which has its own page: [what WICA v1 does not do yet](https://github.com/funwithagents/wica-framework/blob/main/docs/guides/limits.md). The design records each as a deferral in the spec it belongs to.

## One reasoning step at a time

The agent handles one model call at a time. A trigger that arrives while a call is in flight is **dropped, not queued**: an utterance that closes, or a sign that is shown, while the model is still thinking is lost. The person says it again, or lowers the hand and shows the sign again, and the next write triggers.

A running `say` is a **Command**, not a step: once the model has answered and the voice is playing, the agent is free again, so speech or a sign during playback does wake it. The transcript makes the difference visible: a reaction still spinning is the window in which input is dropped; a 🗣️ say item still spinning is not ([../reference/agent.md](../reference/agent.md#what-the-transcript-shows)).

Because inputs are transient ([../reference/agent.md](../reference/agent.md#inputs-are-transient)), a dropped utterance is gone after two seconds rather than answered late by the next unrelated step.

## No barge-in

Two things are often meant by barge-in. **Echo** is handled, per body: the robot's XVF3800 cancels the robot's own voice in firmware, the computer's echo canceller subtracts the voice's playback from the mic ([computer.md](computer.md#echo-cancellation)). So the mic does not hear the agent, and the agent does not answer itself.

**Talking over the agent** is not handled. Speech that closes while `say` is playing starts a new step with the running `say` visible in the World, and nothing cancels the first: both play, or the model decides. What does work is an explicit stop: the `stop` hand sign, or a spoken "stop", wakes the agent, which cancels the running Command with `cancel_command` and confirms in the next turn, as the persona and the hand-sign section instruct ([hand-signs.md](hand-signs.md)). That is a reaction, with a reaction's latency, not an interruption.

## No streamed speech

`say` plays a complete utterance: the model's reply is synthesized and spoken as one Command, which stays `running` until playback ends. There is no word-by-word reveal on the page and no speaking of a reply while it is still being generated.

## No persistence

The World starts empty each run, and so does the history: the agent remembers nothing from a previous session. Within a session the history is WICA's only memory, unbounded by default and bounded by `history_reactions` or `history_budget` when the config sets them ([../reference/configuration.md](../reference/configuration.md#wica-the-agent)); anything older than the bound is gone for the model. State that must outlive it has no home in v1.

## Hand slots, not identities

`hand_gesture_1` and `hand_gesture_2` are **slots**, numbered in the order the hands appeared in view, not left and right. When hand 1 leaves while hand 2 stays, the remaining hand becomes hand 1: on the page and in the prompt, hand 1 takes hand 2's sign and hand 2 reads no sign. The agent is told the numbers are not left and right and nothing more. Two hands are two entries: `three` and `two` are not a `five`, unless the model decides so.

A sign the person keeps holding is delivered once, when it is first held long enough, and reads as no sign after a second until the hand changes ([hand-signs.md](hand-signs.md#tuning)).

## No manual movement

The agent has no gaze pose, head or body command: it can play a recorded emotion, set its motors' state and, when the robot sees people, choose whom its head follows, nothing else ([../reference/agent.md](../reference/agent.md#the-commands)). Where the head aims while it follows someone, breathing between actions and swaying with the voice are the bridge's motion loop, configured in the `motion` block ([../reference/configuration.md](../reference/configuration.md#reachy-the-robot)) and invisible to the model. Following is framed as whom the robot pays attention to, deliberately: when tracking was an on/off switch for the agent, the model treated idle aliveness as its own job.

## A user is a number, not someone

The robot tells the agent about *a user* in view under a number, and nothing more: it does not recognise anyone. The same person coming back after a break, or hidden for more than two seconds in view, gets a new number, and the agent is told a user left and another came into view. Every arrival, departure and change of who is in the personal space wakes the agent: in a busy place the robot comments on people coming and going more than a person would. Who is in view is not shown on the page, only in the World panel ([agent.md](../reference/agent.md#the-world)).

## No per-body panel

The page is the same for every body. It shows the transcript, the ASR line with the live speech and the typed-text box, the Vision line, and the debug panels; it does not show the robot's connection, its motor state, who it sees or is looking at (the World panel does), or whether the computer's echo cancellation is on or degraded. What there is: the **banner** at the top for anything that could not start, and the `⚠️` tails of the ASR and Vision lines for a mic or a camera that died ([troubleshooting.md](troubleshooting.md)). A panel owned by the embodiment is an open question of the design, not a plan.
