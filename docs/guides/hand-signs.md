# Hand signs

The agent's second perception, after speech: the body's camera runs through the [vision-modules](https://github.com/funwithagents/vision-modules) hand pipeline, a hand detector and a gesture classifier, and each hand's gesture is reduced to one sign of a small vocabulary the agent can react to. The design is [specs/gesture-input.md](../../specs/gesture-input.md).

## What the agent sees

One entry per hand, `hand_gesture_1` up to `hand_gesture_<max_hands>`, holding the sign that hand just showed. Showing a sign wakes the agent; a hand relaxing or leaving does not. The sign is a one-shot observation: it is cleared a second after it lands and gone from every later prompt, so it reads as what the person just did, not as something they are still holding ([agent.md](../reference/agent.md)).

The persona section describing this is appended at startup, only when vision is on, and it describes the perception, not the reactions: the agent is told a camera detects hand gestures (not whose hand made them), which signs exist, and that hand numbers follow the order the hands appeared in, not left and right. It is not told what `like`, `ok` or a count means, nor whether to respond; it decides from the conversation. The one exception is `stop`: when it is detected on any hand, the agent cancels everything it is doing, then confirms out loud in the next turn.

## The vocabulary

The classifier reports one of HaGRID's classes per hand; the app reduces them to signs a person means:

| Classifier label | Sign |
|---|---|
| `fist`, `stop`, `stop_inverted` | `stop` |
| `like` | `like` |
| `dislike` | `dislike` |
| `ok` | `ok` |
| `one` | `one` |
| `two_up`, `two_up_inverted`, `peace`, `peace_inverted`, `rock` | `two` |
| `three`, `three2` | `three` |
| `four` | `four` |
| `palm` | `five` |

Anything else, `call` and `mute` among them, a label below the confidence threshold, or no hand, is no sign. Two hands are reported separately: `three` and `two` are two entries, not a `five`.

## Enabling it

Have a `vision` section in the config ([reference](../reference/configuration.md#vision-hand-signs)), `{}` being enough, with the `vision` extra installed ([extras](../reference/install.md#extras)); both default configs ship it. The section left out opens no camera, loads no model and tells the agent nothing about hands. On the computer the hand signs read the webcam of the `camera` section, which must be there too. A config still holding the old `enabled` field is refused: remove the field, or the whole section to turn hand signs off.

The first run downloads the models, a MediaPipe hand bundle and a gesture classifier of about 340 MB, into the Hugging Face cache ([first-run downloads](../reference/install.md#first-run-downloads)); until they are loaded the Vision line shows no label. The models load on the first frame, so a download that fails, with no network, is reported on the Vision line rather than crashing the app.

Which camera is the body's: the computer's webcam, named by `camera.device` ([computer.md](computer.md#the-webcam)); on the robot, its own camera, the same feed the bridge's face detector samples ([reachy-mini.md](reachy-mini.md#the-users-in-front-of-the-robot)). Hand signs are not available on Linux arm64 or an Intel Mac, where MediaPipe ships no wheel ([platforms](../reference/install.md#platforms)).

## Tuning

| Field | Default | Effect |
|---|---|---|
| `threshold` | `0.55` | the confidence a label needs to count; below it the hand reads as no sign and the Vision line shows `below threshold` |
| `fps` | `10` | the rate of the hand stage and the classifier, and of the webcam poll on the computer; the robot's camera runs at the daemon's own rate and the stages sample it |
| `max_hands` | `2` | how many hands are detected and classified, and how many entries the agent has |
| `hold_s` | `0.3` | how long a sign must be seen, unbroken, before it is delivered; `0` delivers on the first frame |
| `release_hold_s` | `0.5` | how long a hand must show no sign, unbroken, before its sign counts as gone |
| `device` | `null` | the classifier's torch device; `null` picks `mps`, then `cuda`, then `cpu` |

Two hands by default because `max_hands` is also the detector's tracking capacity: with one, the detector locks onto the first hand it sees, and a person whose relaxed hand was tracked first can sign with the other and never be seen. The price is up to one more classifier pass per frame; if the classifier's achieved rate on the Vision line falls well below the target on a slow machine, `max_hands: 1` is the fallback.

The hold is what keeps a one-frame flicker of the classifier, between `stop` and `five`, or across the threshold, from waking the agent. It applies to every change, and the clock restarts on any dissenting frame. A sign appearing, or replacing another, waits `hold_s`: that is the delay a person feels, so it is the short one. A sign going away waits `release_hold_s`, which never delays a reaction (a hand relaxing does not wake the agent) and only has to outlast a dip below the threshold, so that the same sign coming back is not delivered twice.

With the stages, the hold and the agent's coalescing window of about 200 ms, a `stop` acts a little over half a second after it is shown. If that is felt, lower `hold_s`; if signs then arrive that nobody showed, raise it again. If the classifier's achieved rate on the Vision line falls well below the target, the machine cannot keep up with `fps`: lower it, since a rate the classifier cannot hold only costs CPU.

## The Vision line

At the top of the Vision section, beside the ASR one, refreshed with it. Off, it reads `Hand signs are off (the config has no Hand signs section).` On, the rates first, then a few lines under a **Hand signs** subheading:

```
- camera: 9.8 fps
- hand stage: 9.8 fps (target 10)
- classifier: 9.5 fps (target 10)

Hand signs
Status: running · up to 2 hands

| hand | seen                       | sign |
| #1   | like (0.91)                | like |
| #2   | below threshold (0.40)     | —    |
```

| Field | Meaning |
|---|---|
| `camera:` | the rate the camera actually delivered frames at: the daemon's on the robot, the paced webcam's on the computer |
| `hand stage:`, `classifier:` | each stage's achieved rate next to the configured target; a starved classifier shows here |
| `— fps` | no rate yet: the first sampling window has not passed |
| `Status:` | whether the pipeline runs, and how many hands it follows at most (`max_hands`) |
| `seen` | what the detector currently publishes in that hand slot, with its validated label and raw score; `below threshold (0.40)` for a hand whose top class missed the threshold, `no crop` for a hand at the frame's edge, `no hand` with none |
| `sign` | the last sign delivered for that hand slot, `—` for none |

Two lines appear on trouble, each on its own, after the table: `⚠️ camera:` with the feed's own failure (a webcam that cannot be opened), or the stall line `no camera frame for 3 s` when frames stop arriving from any camera, cleared the moment one lands; and `⚠️ error:` with the last exception a stage recorded, a model that failed to download or load among them ([troubleshooting.md](troubleshooting.md#no-camera-frame-or-no-sign)).

## Snapshots

When vision is on, the Vision section carries a `Snapshot folder` box, prefilled with a `snapshots` folder under the directory the app was started from, and two buttons, for checking what the pipeline actually sees:

| Button | Writes |
|---|---|
| `Save detector snapshot` | the full camera frame the hand stage last detected on, as `snapshot_HandStage_<timestamp>.jpg` |
| `Save classifier snapshot` | the hand crop or crops the classifier last classified, as `snapshot_GestureClassifier_<timestamp>_<i>.jpg`, one per hand |

The status line under the buttons reports `Saved …` with the path, or a `⚠️` line: nothing consumed yet before the first frame, `no crop to save` when no hand was in the last classified frame, or the OS error for an unwritable folder. Nothing raises.

## Limits

- **Hands are slots, not identities.** Hand 1 is the first hand the detector published and stays that hand while it is in view, but when hand 1 leaves and hand 2 stays, the remaining hand becomes hand 1 and its sign moves with it. The agent is told the numbers are not left and right.
- **A sign shown while the model is reasoning is lost.** The agent handles one reasoning step at a time; a sign shown while it speaks does wake it, since speaking is a command, not a step, but one shown mid-reasoning is dropped. Lower the hand and show it again ([limits.md](limits.md)).
- **A held sign expires.** A sign is cleared one second after it lands and is not refreshed while the hand keeps holding it; it reads as no sign until the hand changes.
- **The page shows the camera, not what the models saw.** The page's **camera**, beside the Conversation, shows the camera's picture, mirrored like a selfie; the snapshots are the way to see the exact frame and hand crops each stage consumed.
