# What is live right now — 2026-08-28 09:05

Read this before changing anything. **The deterrent is armed and will fire
tonight without anyone starting it.** Dave's standing rule: no sound through
the camera speakers — tests included — unless Dave has warned Dima first.

## What changed today (independent review + fixes, commits 4273da1, f912148)

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

    patrol        pid 886096 (started 08:57:58), DETER_ARM=1, HA token in env,
                  sampling every 2s inside 22:00-06:00; cron restarts it if it dies
    ocp-detector  systemd, started 22:09 on 08-27, deterrent hooked in
                  (server.py path unchanged today; it rarely sees the stray --
                  Dima's Pi hang starves the HA automation)
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

## Next (from the review, in order)

1. R2: one continuous loop on a held-open RTSP stream (frame age ~0.7s).
2. R3: fire on arrival in the open. (Inside-camera sound is ruled out -- family sleeps nearby.)
3. D6 person-suppression fix before any water hardware.
