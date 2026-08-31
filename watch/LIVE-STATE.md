# What is live right now — 2026-08-31 09:45

Read this before changing anything. **The deterrent is armed and will fire
tonight without anyone starting it.** Dave's standing rule: no sound through
the camera speakers — tests included — unless Dave has warned Dima first.

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

    patrol        pid 2658847 (restarted 09:52 08-31: live-stream loop + N4),
                  DETER_ARM=1, HA token in env; live RTSP at 0.33s cadence
                  21:45-06:00, segment poll at 30s otherwise; cron restarts it
    ocp-detector  systemd, restarted 09:56 08-31 with the N4 deter. HA path
                  delivers in ~7s since Dima disabled cat_motion_rpi on 08-28.
    soundserver   `python -m http.server 8081` in ~/projects/ocp/sounds/,
                  pid 574231 -- HA fetches the sounds FROM here over the tunnel
    wg-quick@wg0  the tunnel

`.env` contains `DETER_ARM=1`, so **restarting the detector re-arms it**. There
is no "off" that survives a restart except editing that line.

## Not running

The five monitor watches died with the previous session. Nothing alerts on a
fire, a freeze precursor, or delivery going down. The system keeps running;
only the watching stops. Re-arm from `REARM.md` if wanted.

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

It plays DRILL_boost3 then escalates through dog bark, growl, bark — up to 6
sounds over 30s, stopping the moment the cat leaves, reaches the flap, or a
person appears. It writes a 75s video to `~/ocp-watch/reactions/fire-*.mp4`.
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

## Next (from REVIEW-2026-08-31, in order; N1+N4 done 08-31)

1. N2: fire on first sight in the open (NOT a gate zone -- see correction).
2. N3: sound chain under ~1.5s (stage WAVs in HA config/www).
3. N5: acceptance bar in evaluate_deter (sound in air <=2.5s after gate
   appearance on the 023323 burst) before changing any gate.
4. N6: ask Dima about food-at-night; keep the visits/entries scorecard.
5. D6 person-suppression fix before any water hardware.
