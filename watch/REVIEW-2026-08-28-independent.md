# Orange Cat Project — independent review, 2026-08-28

Reviewer: Claude (fresh session on odd-fellow). Scope: `~/projects/ocp`, `~/ocp-watch`,
the working session's memory and notes, live logs, and the three reaction videos.
The goal being judged: **stop the orange stray coming to Dima's house.**

## 1. Bottom line

- **Detection is solved.** YOLO-box + colour-margin finds the stray reliably and
  has rejected every resident and (in the night window) every person. Don't
  spend more effort here.
- **The deterrent has never reached the cat.** Three fires across two nights, all
  on an empty patio, all while the cat was *leaving*. Replaying the reaction videos
  through the detector shows why: the cat is on-camera for 2.5–5.5 s on exit, the
  system's frame-to-sound latency is ~5 s. That is not a tuning problem.
- **The project is aiming at the wrong moment.** Every fire so far was at an exit
  (the cat has already eaten; per Dave, "it is leaving anyway"). The two moments
  that matter are the *approach* (1–30 s of loitering, per Dima) and the
  *feeding* (9–18 min stationary inside). Neither is being targeted today; the
  second is not even on the plan.
- **Whether sound deters this cat at all is unknown**, and the one datum (a
  resident flinched and did not leave; it was still sitting by the gate 30 s
  later in the video) is mildly discouraging. Plan to measure this, not assume it.

My recommended order: (1) fix the two things that keep the system from taking
the shot it already has, (2) move to a continuous low-latency loop, (3) add the
inside-camera deterrent, (4) run a measurement plan for two weeks before buying
water hardware.

## 2. What is solid (no action needed)

- The classifier design: find the animal first, then colour-vs-surround margin.
  The reasoning about why `warm_pct` alone fails (dawn, sun on siding, backlit
  skin) is correct and well evidenced. 30/30 on labelled clips, replayed against
  the archive rather than trusting old verdicts.
- The `>= 2 orange frames` rule as the person backstop: all 9 labelled person
  events scored 0–1. This, not the YOLO person class, is the real safety gate.
- The flap guard as a welfare rule, and the fact it was tested on real frames.
- The 22:00–06:00 arming window for sound.
- Box reliability work: firmware/ACPI fix, hardware watchdog, systemd unit,
  `@reboot` patrol, overnight report. Uptime 2.5 days, 0 hogs.
- Reaction-video capture around every fire. This is what made this review
  possible; keep it.
- The session's own self-assessment (REVIEW-NOTES.md) is honest and accurate. Its
  biggest risk — generalising from n=1 — is real and shows up in the gates below.

## 3. Evidence: what actually happened at the three fires

Replaying YOLO + colour over the reaction videos at 2 fps (frame times from the
burned-in clock; "sound" = the moment HA fetched the file from odd-fellow):

| Fire | Cat visible (outside cam) | What it was doing | Sound | Gap |
|---|---|---|---|---|
| 08-27 03:18 | 03:18:18–03:18:20.5 (by the gate, h≈10%) | leaving; already 20+ s out of the flap | 03:18:27 | ~6 s after last sighting; a **resident** was in shot and flinched |
| 08-28 01:32 | 01:32:20.5–01:32:26.0 (flap → right edge) | leaving; in the flap zone until :23.5 | 01:32:30 | 4 s after it left frame |
| 08-28 03:22 | 03:22:52.5–03:22:55.0 (gate, walking away, h 10→17%) | leaving | 03:22:58 | 3 s after it left frame |

Patrol frames confirm the staleness even after the `-sseof` fix: the burned-in
time on `patrol-032257.jpg` is 03:22:53, logged at 03:22:57 (4 s); on
`patrol-013229.jpg` 01:32:24 vs 01:32:29 (5 s).

Two things this table says beyond "latency":

1. **On exit the cat is a 2–5 s target.** Even a 1 s system would have a single
   shot, at a cat that has already fed. This is the worst possible moment to
   optimise for.
2. **On 08-27 the exit was slow** (out of the flap 03:17:47, at the gate
   03:18:17 — a 30 s window), and it was still missed. That miss was process,
   not latency: the patrol had no HA token, and the flap-hold at 03:17:53 was not
   re-evaluated until 03:18:26. See defect D1.

And the arrival that mattered — 01:25:16, stray mid-patio facing the camera,
the best shot of the night — was held `too_far` by the area gate, and then
(defect D1) not reconsidered for 120 s, during which it went in.

## 4. Defects found (ranked by cost to the goal)

**D1 — `patrol.py` suppresses the deterrent for 120 s after any orange report.**
`MIN_GAP = 120` is applied before the deterrent call: after one `orange_cat`
report, `box = None` for 120 s, and `deter.consider_and_escalate` is never
reached. So a `too_far` or `at_flap` hold is not re-checked for two minutes —
the 2 s "dense sampling" buys the deterrent nothing after its first look. This
is the single most damaging bug in the current code: it is exactly what
converted the 01:25 hold into a missed entry, and what left the 08-27 flap-hold
un-revisited for 33 s. Fix: rate-limit *logging*, never the deterrent; call
`deter.consider` on every orange frame in the window (its own 60 s cooldown
already prevents double fires).

**D2 — ~4 s stale frames are structural to the segment buffer.** `-c copy`
segments cut on keyframes (~5 s real length) and an open segment has no moov
atom, so the freshest readable frame is 1–5 s old, plus 1.3 s to deliver the
drill. The session's diagnosis is right; the fix (`BUFFER_MODE=decoded` /
`streamer.py`, or simply a held-open `cv2.VideoCapture`) is already in the repo
and untried. See R2.

**D3 — Two half-detectors, neither of which sees the arrival.** `server.py`
fires only on HA motion POSTs, and on 08-28 the POST log shows *no* events
during the 01:25–01:32 or 03:22 visits (POSTs at 01:37 and 04:06 only, both
`no_animal`). The patrol is the detector that actually works, but it is a
setsid'd script, polls, and had four code edits between 01:29 and 03:26 while
the cat was visiting. The HA→POST path has failed three separate ways in two
weeks (dead LAN IP, Pi hang, timeouts). Detection should not depend on it.

**D4 — Every aiming gate is fitted to n=1 or n=2.** `MIN_HEIGHT_PCT=20`,
`APPROACH_HEIGHT_PCT=12`, "grew 1.4×", "x retreat 25 px", the CLOSING rule. The
03:22 fire *was* one of these misfiring (height grew because the cat turned
side-on). They were patched at 03:26 with another n=1 rule. There is no offline
harness for `deter.consider`; each patch is tested on the next live cat.

**D5 — Blocking work inside an `async def` handler.** `server.py:/motion` runs
15× YOLO, then `deter.consider_and_escalate` (12 more inferences, then a
10 s-timeout HTTP call to HA) synchronously inside the event loop. `/health` and
other POSTs stall meanwhile. Not proven, but a plausible contributor to the "HA
timed out after 60 s" lines seen around the visits. Fix: make the handler a
plain `def` (FastAPI runs it in a threadpool) or `run_in_executor`.

**D6 — Person gate is weaker than it looks** (session's own finding, unfixed):
YOLO sees the person in only 5 of 9 labelled person events, and a lone person is
recorded as `no_animal` so the burst-level rule never fires. The 2-orange-frame
rule is doing the safety work. Acceptable for sound in the 22–06 window; **must
be fixed before water.** The fix is designed (`person-suppression-gap` memory)
and not applied.

**D7 — `~/ocp-watch/` (deter.py, patrol.py, watches, reaction videos) is not
in git**, and the repo's deterrent changes are uncommitted. The live system's
logic exists on one laptop that has locked up twice. `deter.py` is a symlink
from the repo into `~/ocp-watch`.

**D8 — Minor.** `score_burst` runs YOLO twice per frame (`detect` then
`best_box`); dead `if False:` block in `consider`; `deter._play` bypasses
`talk.play` and duplicates it; `SOUND_SEQUENCE` env default disagrees with
`cat-deterrent.toml` (`sounds = ["DRILL_boost3.wav"]` is ignored by the live
path); `FLAP_ZONE` is hard-coded pixels (fine until the camera is bumped).

## 5. Recommendations

### R1 — Take the shots you already have (hours, no new architecture)

1. Fix D1: never let the report rate-limit gate the deterrent.
2. Make the hold outcomes *stateful*: after `too_far` / `at_flap`, re-evaluate
   every cycle for 60 s and fire the instant the gate clears. Today a hold is a
   one-shot no.
3. Commit everything (D7), run the patrol as a systemd unit, and **freeze code
   during the arming window.** Change gates in daylight, replay them (R4), arm at
   22:00. Four live edits between 01:29 and 03:26 cost at least one shot on 08-28.

### R2 — One continuous low-latency loop (the architectural fix; 1–2 days)

Replace patrol + segment-decode + HA-trigger with a single process that holds
the outside RTSP open (`streamer.py` already does this), runs YOLO on every
Nth frame (~31 ms/frame; 5 fps is ~15% of one core), and keeps a short track
history (position, height, direction, time-in-zone). Decide from the *track*,
not a 6-frame burst. Expected frame age: 0.3–1 s (Protect's own RTSP delay) vs
4 s today. Keep `server.py`/HA only for notifications and as a second opinion.

While there: measure the sound path. 1.3 s is HA fetching the WAV over the
tunnel and spawning ffmpeg to the camera's talkback port. Try (a) staging the
files in HA's `config/www` so the fetch is local, (b) a shorter file with an
instant onset, (c) pre-warming — HA's Protect `media_player` accepts a new
`play_media` while idle; time it from the logbook. If the whole chain lands
under ~1.5 s, the approach becomes a real target.

### R3 — Aim at the two moments that matter, not the exit

**Approach.** Dima says the stray takes 1–30 s to cross; the 08-27 02:59 entry
and 01:25 sighting both show loitering mid-patio. With R1+R2 this becomes a
multi-second window. Fire on *arrival in the open* (height ≥ ~20%, not in flap
zone, moving toward the door or stationary), not on a fitted "closing" rule.

**Inside, at the food.** The stray is inside for 9–18 min and stationary at the
bowl. Latency is irrelevant there. The inside camera has a speaker
(`media_player.garage_speaker`, "Cat Door Inside Speaker") and a calibrated IR
discriminator (`BOX_IR_REL_BRIGHT`, `BOX_IR_CV`; orange 1.40–1.67 vs tuxedo
0.78–1.42 on 9 events — small sample, so validate first). Aversive stimulus
*at the reward site* is also the conditioning that actually changes behaviour;
a startle on the way out is not. Two welfare gates carry over: never fire while
the cat is in the flap, and never fire when a resident is also in frame (the
residents live there and cannot avoid it). Direction of flight: from the bowl
the nearest exit is the flap, i.e. out. This needs Dima's agreement — it is
sound inside his house at night.

### R4 — Build the replay harness before touching another threshold

The reaction videos, patrol frames and event bursts are a labelled corpus of
approaches and exits. Write `evaluate_deter.py` that runs the decision logic
over them at real timestamps and prints *when it would have fired and where the
cat was*. Every gate change gets run through it. This is the same discipline
the detector already has (`evaluate.py`) and it is why the detector works.

### R5 — Decide whether sound works before buying water hardware

Run two weeks with R1–R3 and record, per visit: approach seen (y/n), fired
(y/n), cat's reaction (flee / flinch / ignore, from video), and whether it
entered anyway. Define success up front — e.g. "fired on ≥70% of approaches;
entered on <30% of fired approaches; visits/night falling week over week."
Expect habituation: cats stop responding to a repeated harmless sound within
days, especially with food on the other side. Vary sounds, keep them rare
(escalate only while the cat stays), and treat "flinched but stayed" as a
failure, not a partial success.

If sound does not move the numbers, the water build (Option A, Shelly + 12 V
NC solenoid, hardware auto-off) is well researched and I agree with it. Do
D6 (person gate) first, and aim across the approach as the hardware note
already says.

### R6 — One non-camera question for Dima (once, then drop it)

The cat comes for food. If the bowls can be empty at night (timed feeder for
the residents at dusk/dawn, or bowls moved away from the door), the reward
disappears and every deterrent above works better or becomes unnecessary. This
is not the microchip route, which is settled. If Dima has already said no,
ignore this.

## 6. Smaller things worth doing

- `server.py`: make `/motion` a sync `def` (D5); add the detector-path blindness
  check the notes flag (N consecutive events with zero YOLO boxes on both cams).
- Apply the person-suppression fix (D6) and replay it against the archive.
- Log the *burned-in frame time* alongside wall clock on every deterrent
  decision so staleness is visible without opening a frame.
- `patrol.py` "likely sunlight on the siding" label is wrong at night (22:10 case);
  make it conditional on daylight.
- Push to GitHub from the laptop; `origin` is still a local bundle.
- Prune `frames/events` on a schedule (~200 MB/night).

## 7. Questions I could not answer from here

1. Has Dima seen the stray react to any sound in person? (Dave saw one flinch.)
2. Is Dima's Pi deterrent definitely off every night? Two systems on one speaker
   destroy the evidence about what a single sound does.
3. Can the cameras' GOP/keyframe interval be shortened in Protect? (Only matters
   if R2 is not done.)
4. Is an inside-camera sound at night acceptable to Dima's household (R3)?
5. Can the food be unavailable at night (R6)?
