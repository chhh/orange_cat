# What is live right now — 2026-09-08 08:05

Read this before changing anything. **Water is armed and will fire tonight
without anyone starting it.** Standing rule: no camera-speaker playback and no
water, tests included, unless Dave has warned Dima first.

## The one thing a fresh session MUST do

**Re-arm the four watches.** They are Monitors and they died with the session
that started them. Everything else on this page survives a reboot on its own;
these do not, and the health watch is what caught both recorders wedged on
09-07 (79 minutes blind, discovered by luck).

    watches/w-patrol.sh   engagements, deterrent decisions, water, errors
    watches/w-health.sh   patrol dead, on battery, tunnel, sound server,
                          detector, stale segments, heartbeat gap
    watches/w-flood.sh    valve stuck open >15s, or HA dark while it was ON
    plus a 06:45 timer to write the night report

Self-test each after arming (they are worthless unarmed and look identical):
append a line containing `OCPWATCH-SELFTEST` to patrol.log, and
`touch ~/ocp-watch/.health-selftest ~/ocp-watch/.flood-selftest`.

## Armed and self-sustaining (no action needed)

| | |
|---|---|
| patrol.py | cron `*/5` re-arms it after any death; **restart ONLY via cron** |
| ocp-detector | systemd, `Restart=always`, `Wants=` (not `Requires=`) the tunnel |
| recorder watchdog | kills any ffmpeg producing nothing for 30s; ~40s recovery |
| deterrent window | 22:00-06:00, enforced in `consider()` |
| valve warming | first sight only — nothing to arm at dusk |
| tunnel | `wg-quick@wg0` enabled at boot |

## What the water does (deter.py, deployed 37910ca)

| situation | sound | water |
|---|---|---|
| orange cat at range | opener + rapid ladder | 3 pulses, ~2.2/3.4/4.6s after the decision |
| at or into the flap | held | ONE sustained 2s barrier |
| exiting, still in the opening | held | held — "not out yet" |
| exiting, clear of the flap (<15% overlap) | held | 3 pulses |
| person in frame | hard stop | hard stop |

Re-arm: water 8s, sound 60s (or 15s for a close engagement). After the cat has
been inside, everything is quiet for 120s of no sighting.

**Three cutoffs, all through Home Assistant.** `fire_water`'s `finally`; HA
`automation.cat_sprayer_failsafe_off` (10s after any on); and the flood watch.
There is NO device-side timer. If HA dies mid-burst nothing can close the
valve remotely — the tap must be shut by hand.

## Night of 09-07/08, the first water night

One engagement 03:43, **no entry**, zero faults inside the window. The water
startled the cat off the patio without touching it; it was still in frame 0.4s
after the siren and vanished inside the 0.33s containing the first pulse.
Report: `reports/Night-2026-09-08-report.html` and
https://claude.ai/code/artifact/e70ab3bb-2701-48a7-b965-2c09efcb8da5

Two approach routes, not one: gate (n=23) and right-hand (n=9). The right-hand
lane is the WETTER one (56% vs 30% cross the wet zone), so pushing the cat off
the gate route works in our favour. See [[the-corridor]].

## Open

- The watches should become systemd user units or cron jobs that notify Dave
  directly, instead of dying with a Claude session. Not yet scoped.
- Aim stays as it is; extending the throw was declined. Confirm coverage the
  first time the cat is actually hit.
- Whether the 1.7s sound lead still earns its place — the cat moved on the
  water, not the siren.

# What is live right now — 2026-09-03 09:00

Read this before changing anything. **The deterrent is armed and will fire
tonight without anyone starting it.** Dave's standing rule: no sound through
the camera speakers — tests included — unless Dave has warned Dima first.

## What changed 09-03 morning (after the tripwire night — 2 entries, 2/2)

- **Sounds now come off HA's own disk**: `DETER_SOUND_BASE=
  http://192.168.1.133:8123/local/ocp/ha-staging` in `.env` (Dima staged the
  zip; its top-level folder is why the path ends in ha-staging). Removes the
  ~0.3-0.5s tunnel fetch per sound. The 8081 soundserver on odd-fellow stays
  up as fallback — flip `.env` back to `http://192.168.7.4:8081` if HA 404s.
  **Proof-of-whose-sound-played moved**: soundserver.log access lines no
  longer show our fetches; use patrol.log PLAYED/escalation lines + the HA
  logbook for `media_player` (Dima's talk.py Pi is the other possible
  source). Dima's siren.wav plays by full URL in the close ladder (deployed
  22:05 09-02, first live play 04:21 09-03).
- **Close-engagement cooldown takeover** (`DETER_CLOSE_TAKEOVER`, 15s): a
  close/rapid engagement may take over the 60s shared cooldown when the
  claim is >=15s stale — a live ladder refreshes it every few seconds, so a
  stale claim is a dead hand (09-03 00:53:24 entered in silence on exactly
  this). Far engagements never take over. Validated on the night's frames
  (scratchpad test: stale->fires, fresh->blocked, far->blocked) and by
  evaluate_deter replay of fire-20260903-042136 (no regression).
- **Restart the patrol ONLY via cron or a detached shell.** A patrol started
  from a Claude-session-tracked command dies when the harness reaps the task
  (killed the 02:24 instance last night, and tethered the 08:07 one this
  morning). Kill the patrol and let the */5 cron bring it back.
- ocp-detector restarted 08:07 (Dave's sudo) on the HA-local sound base,
  again 08:23 on the takeover gate, and needs one more for the indoor
  response below.
- **SIREN OPENS as of 09-06 night** (Dima's hypothesis / Dave). A close/urgent
  engagement now opens with a 1.6s cut of the siren's startling ONSET
  (sounds/siren_open.wav, served off the 8081 fallback) fired on first sight,
  then the drill, then catsfight, then dogs. Rationale: the full siren only
  ever played as the ladder's 2nd sound, arriving as the cat was already in
  the flap (09-05/06 both entries); it never had a fair shot at an APPROACHING
  cat. Short so it does not block the speaker like the full 6s siren would.
  DETER_SIREN_OPENS=0 restores the drill opener. Validated: armed-path test
  (opener=siren_open, ladder=[drill,catsfight,dogs]) + evaluate_deter replay
  of the 09-06 entry (fires) and exit (suppressed).
- **URGENT OPENER armed for 09-04 night** (Dave). Any orange engagement now
  opens with the short loud DRILL and the rapid ladder, even when the cat
  reads "far" -- the 09-04 entry showed this cat dashes gate-to-flap in ~6s
  while reading as far (9.7% height), and the old 10s quiet far-opener both
  mismatched it and MONOPOLISED the speaker (_play blocks until a sound ends)
  so nothing louder could follow during entry. DETER_URGENT_OPENER=0 restores
  the graded far growl. Supersedes the far half of the graded-threat design.
  Validated: isolated far-frame test (drill+rapid with flag on, growl with it
  off), and evaluate_deter replay of the 09-04 entry (fires) and exit
  (still suppressed).
- **INDOOR ENTRY RESPONSE armed for 09-03 night** (Dima proposed it 18:18;
  Dave approved). When the escalation's vanished-at-the-flap branch declares
  an entry -- which only happens after a tracked orange engagement, so a
  resident can never trigger it -- wait DETER_INDOOR_DELAY (2s, clears the
  flap), then play the indoor PAIR in order (DETER_INDOOR_SOUNDS):
  dog_growl.wav, then Dima's own voice Poshel-Otsuda.wav (full URL off the
  8081 fallback -- NOT in the HA-staged set), both at DETER_INDOOR_VOLUME
  (0.6 as of 09-05 -- was 0.4; louder test of whether the 09-05 calm was
  about volume; Dima should be warned it is louder) on
  media_player.garage_speaker = the Cat Door INSIDE speaker. Every recognized
  entry also auto-records the INSIDE camera to reactions/inside-<stamp>.mp4
  (DETER_INSIDE_SEG_DIR), so the eviction reaction is never lost to a buffer
  wrap again. The
  8081 soundserver is therefore LOAD-BEARING again for the voice clip. Kill switch: DETER_INDOOR=0 + restart. ENTITY TRAP,
  verified against HA: media_player.nursery_speaker (no _2) is a NEST MINI
  in the family's room -- never a deterrent target; nursery_speaker_2 is
  the outside cat-door speaker, garage_speaker the inside one.

## Night of 08-30/31: FIRST ENTRY under the armed system -- see
`watch/REVIEW-2026-08-31-night.md`. The stray crossed gate-to-flap in ~4s at
02:33; first orange verdict was already at the flap (correct hold); it ate 16
min; the only fire was on the exit. No bug -- the segment-tail patrol sees one
distinct instant per ~5s and the full chain was 3.5-8s. Hence:

## What changed 08-31 (N1 from the review: continuous low-latency loop)

- **patrol.py now holds the outside RTSP stream open in the arming window**
  (`streamer.py`, previously unused). Measured live: frame age 0.12-0.3s (was
  0.6-5.6s), ~5.6 fps buffered, deter's burst grab is instant (was a ~1.3s
  re-decode), classify 0.19s/frame at a 0.33s cadence. Chain is now ~2.1s
  trigger-to-air (was 3.5-8s).
- The stream opens at 21:45 (15 min prewarm), closes at 06:00; daytime is the
  old 30s segment poll. If the buffer goes stale the patrol logs
  `patrol stream BLIND` and falls back to the segment tail -- worst case IS
  the old behaviour, but say-so is in the log. A wedged RTSP read (socket
  timeout now set) is abandoned and reconnected, logged `WEDGED`.
- Replayed 08-31 02:33 entry at the new latency (`evaluate_deter.py
  frames/events/outside-20260831-023323-013/ --stale 0.7 --interval 0.5`):
  fires on the closing rule ~1.7s after gate appearance, sound lands
  mid-patio (h=31%, not at flap) -- the shot that did not exist last night.
  Exit replay: still holds while in the flap, fires only clear of it.
- Gates are UNCHANGED in this step. Next per the review: N2 (fire on first
  sight at the gate), N3 (sound chain <1.5s), N4 (stop exit fires).

## Night of 08-28/29: three visits, three on-target fires -- see memory
`night-0829-sound-does-not-deter` and `watch/REVIEW-2026-08-28-independent.md`.

## What changed 08-28 (independent review + fixes, commits 4273da1, f912148, 94c57d5)

- `~/ocp-watch/deter.py`, `patrol.py`, `start-patrol.sh`, `overnight-report.sh`,
  `LIVE-STATE.md` are now **symlinks into `~/projects/ocp`** (`deter.py`,
  `patrol.py`, `watch/`). Edit them in the repo; commit; the live paths do not
  change.
- **patrol.py no longer gates the deterrent behind the 120s report limit.**
  Every orange frame in the window asks `deter.consider()` (2s cadence); a
  `too_far` / `at_flap` hold is re-checked 2s later instead of 120s later.
  Reporting is still rate-limited. Log format is unchanged for the greps.
- **`evaluate_deter.py`** replays patrol + deterrent over a reaction clip or a
  saved burst dir (`frames/events/outside-<stamp>/`) at a chosen frame age
  and cadence, with sound forced off. Run it before changing any gate:

      uv run evaluate_deter.py ~/ocp-watch/reactions/fire-*.mp4
      uv run evaluate_deter.py frames/events/outside-20260827-025947-470/ --stale 0.7

  Baseline today: the 02:59 approach (08-27) fires mid-patio once the report
  gap is out of the way, even at 4s frame age; all three recorded exits are
  still misses or marginal at any latency -- exits are not the target.
- crontab: `*/5 * * * * start-patrol.sh` restarts a dead patrol (the script
  exits immediately if one is alive). `@reboot` and the 06:50 report remain.
- Review and recommendations: `watch/REVIEW-2026-08-28-independent.md`.

## Running, and surviving this session

    patrol        restarted 09-02 with the full stack (first-glimpse, rapid
                  ladder, threaded captures, 25s play timeout); DETER_ARM=1;
                  live RTSP 0.33s cadence 21:45-06:00; cron restarts it
    ocp-detector  systemd, restarted 09-02 (Dave's sudo) on the same deter.
                  HA path delivers in ~7s.
    soundserver   `python -m http.server 8081` in ~/projects/ocp/sounds/,
                  pid 574231 -- HA fetches the sounds FROM here over the tunnel
    wg-quick@wg0  the tunnel

`.env` contains `DETER_ARM=1`, so **restarting the detector re-arms it**. There
is no "off" that survives a restart except editing that line.

## Not running

The 08-31 Claude session armed a patrol.log watch (fires/exits/BLIND/WEDGED/
errors) and a 22:05 went-hot check — but they die with that session. Nothing
durable alerts on a fire or on delivery going down. Re-arm from `REARM.md`.

## HOW TO DISARM — do this before working on it near 22:00

    # stop the deterrent but keep detection running
    sed -i 's/^DETER_ARM=1/DETER_ARM=0/' ~/projects/ocp/.env
    sudo systemctl restart ocp-detector
    pkill -f "[p]ython /home/david/ocp-watch/patrol.py"   # bracket: never match yourself
    # AND comment out the */5 cron line, or cron brings the armed patrol back in 5 min

    # or abort a firing sequence already in progress
    touch /home/david/ocp-watch/.deter-abort

    # or stop sounds reaching the speaker at all
    kill 574231            # the sound server; HA then 404s and plays nothing

Narrowing the window is gentler than disarming: `DETER_HOUR_FROM` /
`DETER_HOUR_TO` in the environment.

## If it fires

Far first sight: angrycat_full or guarddogs_far (nightly rotation) at
volume 0.6. Close: DRILL_boost3 at 1.0. Escalation while it stays: drill,
dog_bark_big, dog_growl, dog_bark, catsfight (8s) — up to 6 sounds over 30s,
stopping the moment the cat leaves, gets INTO the flap opening, or a person
appears. It writes a 75s video to `~/ocp-watch/reactions/fire-*.mp4`.
Feed every new fire video to `evaluate_deter.py` the next morning.

Sounds play at a neighbour's house at 3am. That is agreed with Dima for the
stray, and only inside 22:00-06:00. It is not agreed for testing.

## Known-broken, not ours to fix

`shell_command.cat_motion_rpi` hangs exactly 60s on Dima's powered-down Pi and
starves his `mode: single` automation, so `server.py` receives almost nothing.
The patrol is unaffected. One line on his side: `--max-time 5`. See
`memory/rpi-command-blocks-delivery.md`.

## What changed 08-31, second pass (N4: exits are not targets)

- `deter.consider` now tracks the VISIT (shared file `.deter-visit`, 120s
  chain gap): a visit whose FIRST framed detection is at the flap means the
  cat came out of the door -- it returns `exiting` and never fires. A visit
  first seen in the open is an approach; nothing changed there. Only
  orange-voted bursts touch the state, so residents cannot poison it.
- Replayed: 08-31 entry still fires mid-patio; 08-31 and 08-28 exits are now
  "no fire"; the 08-29 approach (the fire that turned the cat away) still
  fires. Patrol restarted 09:52 on this code.
- ocp-detector restarted 09:56 08-31 (Dave ran the sudo) -- both processes
  now run the N4 deter.
- Correction from Dave: last night the stray did NOT come in from the gate --
  it came in from the side, quickly. N2 must fire on first sight anywhere in
  the open, not on a gate zone.

## What changed 08-31, third pass (half-step flap gate, Dave's call)

- A cat standing AT the flap (body outside) is now a target for the first
  sound and for escalation repeats -- the back-out-and-run experiment. Only
  >=50% of the visible box inside the flap zone (`DETER_FLAP_COMMIT`) holds
  fire, logged `in_flap`. Exits still never fire (visit-origin rule; replays
  confirm both recorded exits stay silent). Commit 3b7faa0.
- Patrol restarted 10:29, ocp-detector 10:37 (Dave's sudo) -- both on this.
- Sound screening: 13 candidates (freesound previews) for a distance-graded
  threat sequence are with Dave -- audition page (artifact
  "Deterrent Sound Auditions"), originals in ~/ocp-watch/sound-candidates/.
  Dave is keeping Dima informed. On verdicts: wire keepers + N2 (far first
  sight -> menacing opener, closing -> startle sequence); check HA
  volume_set on the speaker for distance-scaled volume.

## What changed 08-31, fourth pass (hygiene sweep, commit 956e4ab)

- Watchdogs for tonight (Claude-session-bound: die with the session): a
  patrol.log monitor (fires/exits/BLIND/WEDGED/errors) and a 22:05 went-hot
  check.
- Overnight report scopes to a NIGHT WINDOW START marker (21:00 cron stamps
  both logs); opens with a scorecard (pre-entry fires / exit visits = missed
  entries / holds); shows stream state. 07:10 cron prunes frames/events >14d.
- One YOLO pass per deterrent frame (animal.best_box_from) -- decision cost
  halved; replay decision-identical.
- D6 FIXED: server.score_frame reports a lone person as "person"; the burst
  rule now works as documented. Found live: 20260830-123217-002 (person,
  12:32) had scored orange_cat 1.0; now suppresses to person. Stray bursts
  unchanged. Patrol restarted 15:34 on all of this; detector restart pending
  Dave's sudo.
- Speaker media_player.nursery_speaker_2 SUPPORTS volume_set (now at 1.0) --
  distance-scaled volume is available for the graded sequence.

## What changed 08-31, fifth pass (N2 + graded sequence; Dave's verdicts)

- Sounds screened by Dave (two audition rounds on the artifact page). Kept:
  angrycat_full, catsfight, guarddogs_far (new, mastered to drill loudness),
  plus the existing drill + three dogs (dogs re-leveled +0.3-0.6dB).
  Rejected: Hshh, Poshel-Otsuda (was 13dB under-leveled all along), siren,
  noise bursts, and all other freesound candidates. Sources kept in
  ~/ocp-watch/sound-candidates/.
- **N2 live: no more too_far hold.** A far first sight (height < 20%, not
  closing) fires the GRADED opener immediately: angrycat_full /
  guarddogs_far rotating nightly, at volume 0.6 (speaker volume_set per
  play, always explicit). Close/closing: DRILL_boost3 at 1.0 as before.
  Escalation ladder: drill, dog_bark_big, dog_growl, dog_bark, catsfight
  (last rung); after a far opener the ladder starts AT the drill
  (seq_offset -1).
- N5 acceptance bar met on replay: 08-31 entry fires with sound in air 2.5s
  after first appearance (sim 0.7s stale; ~2.0s at live 0.2s), landing
  mid-patio h=30%. 08-29 approach fires 3s earlier (far opener at the
  gate). Exits still silent. Patrol restarted 17:06 on this; detector
  restart pending Dave's sudo.

## What changed 09-01 (recommendations from the night report)

- Night 08-31/09-01: 2 pre-entry engagements, 1 repel (01:52), 1 breach
  (01:58 -- sprinted through drill+bark into the flap, ate 11 min), silent
  exit 02:09. Report: `~/projects/ocp_review/Night-2026-09-01-report.html`
  (artifact "The Last Meter").
- **R1 first-glimpse engagement**: patrol passes its last ~1.5s of live
  classifications to deter; two consecutive live orange frames satisfy the
  2-frame FP rule without waiting for the burst re-vote (which scored 0 on
  the small fresh cat at 01:58:20 and cost 5 of 9 visible seconds). Live
  boxes also feed the flap/range checks as the newest evidence.
- **R2 close-range rapid ladder**: a close engagement escalates at ~1s (was
  2-3s), harshest repeat first: drill opener -> catsfight -> big bark ->
  growl -> bark. Far engagements keep the graded spacing.
- **R3**: the three new sounds now ship as MP3s (200-350KB, were 1-1.6MB
  WAVs -- an untested tunnel-fetch risk at the moment of fire). Staging kit
  for Dima: ha-staging.zip (Drive link in the night report); when he stages,
  set DETER_SOUND_BASE=http://192.168.1.133:8123/local/ocp and restart both.
- **R5 evidence**: silent reaction capture (threaded) on the first
  exiting/in_flap of a visit; person stand-downs save their burst to
  ~/ocp-watch/person-evidence/standdown-*/; escalation logs "VANISHED AT THE
  FLAP -- likely went INSIDE" and marks the visit origin=flap so a quick
  re-emergence reads as the exit it is.
- **R7 TRIPWIRE (Dave, 09-01): if the cat gets inside on 2 of the next 5
  armed nights despite the above, the water build starts.** Count from the
  night of 09-01/02. Scorecard "exit visits seen" = entries.
- Still open: R4 (physical obstacle to lengthen the last meter -- Dima's
  patio, his call), NVR pull for the 02:09:21 person frame, and whether the
  five 2am sounds were audible in the house.

## What changed 09-02 (night fixes after Two Entries night)

- Night 09-01/02 ("The Guest House" report, ~/projects/ocp_review/
  Night-2026-09-02-report.html): 2 entries by the stray (04:37 dive-through;
  04:51 walked in moments after a drill), ~9-min meal, clean silent exit
  05:01 (first silent-capture video). TRIPWIRE: 1 entry-night of 2 used,
  night 1 of 5.
- Fired-path reaction capture is now THREADED: the blocking version blinded
  the patrol ~75s per fire and hid both unseen flap transitions on 09-02.
- _play play_media timeout 10s -> 25s: the call blocks until playback ends,
  so long files were logged "failed (timed out)" while playing -- and the
  false failure killed the rest of the ladder both times.
- catsfight.mp3 trimmed to 8s (was 17s of 4am screaming).
- Close-range trend to watch: drill response decayed from flees-patio
  (08-30) to dives-through (01:58) to steps-aside-and-enters (04:51).
- Open: Dima's staging (ha-staging.zip; flip DETER_SOUND_BASE=
  http://192.168.1.133:8123/local/ocp + restart both when placed), the
  00:33 nightly ~30s stream stall (ask Dima what runs then), R4 obstacle,
  audibility question.

## Next (in order)

1. Dima stages ha-staging.zip -> flip DETER_SOUND_BASE, restart both.
2. R4 with Dima: an obstacle arc in front of the flap (+2-3s of approach).
3. N6 scorecard: count visits / entries / pre-entry fires nightly against
   the 2-of-5 tripwire.

## Settled questions (do not re-raise)

- **Food at night: STAYS OUT (Dima, via Dave 2026-08-31).** The resident
  cats eat overnight, and that is what keeps them from waking Dima too
  early -- which is also the actual harm when the stray eats it: the
  residents go hungry and wake him. So the reward cannot be removed; the
  deterrent must win BEFORE entry. (Closes R6 from the 08-28 review and
  N6's open question from 08-31.)
- Microchip flap route: settled earlier, not pursued.
- Inside-camera sound: ruled out 08-28, family sleeps nearby.
