# What is live right now — 2026-08-28 08:20

Read this before changing anything. **The deterrent is armed and will fire
tonight without anyone starting it.**

## Running, and surviving this session

    patrol        pid 716011, DETER_ARM=1, sampling every 2s inside 22:00-06:00
                  started detached with setsid -- it is NOT tied to any session
    ocp-detector  systemd, started 22:09 on 08-27, deterrent hooked in
    soundserver   `python -m http.server 8081` in ~/projects/ocp/sounds/,
                  pid 574231 -- HA fetches the sounds FROM here over the tunnel
    wg-quick@wg0  the tunnel

    crontab:  @reboot  start-patrol.sh          (patrol returns after a reboot)
              50 6 * * * overnight-report.sh    (writes overnight-report.txt)

`.env` contains `DETER_ARM=1`, so **restarting the detector re-arms it**. There
is no "off" that survives a restart except editing that line.

## Dies when the session ends

The five monitor watches (detector verdicts, ACPI hogs, AC mains, patrol
liveness, POST delivery). Nothing then alerts on a fire, a freeze precursor, or
delivery going down. The system keeps running; only the watching stops.

## HOW TO DISARM — do this before working on it near 22:00

    # stop the deterrent but keep detection running
    sed -i 's/^DETER_ARM=1/DETER_ARM=0/' ~/projects/ocp/.env
    sudo systemctl restart ocp-detector
    pkill -f "python /home/david/ocp-watch/patrol.py"      # cron returns it at reboot only

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

Sounds play at a neighbour's house at 3am. That is agreed with Dima for the
stray, and only inside 22:00-06:00. It is not agreed for testing — do test
plays in daylight, announced.

## Known-broken, not ours to fix

`shell_command.cat_motion_rpi` hangs exactly 60s on Dima's powered-down Pi and
starves his `mode: single` automation, so `server.py` receives almost nothing.
The patrol is unaffected. One line on his side: `--max-time 5`. See
`memory/rpi-command-blocks-delivery.md`.

## Uncommitted and single-copy

Everything in `~/ocp-watch/` — `deter.py`, `patrol.py`, the watch scripts, all
three reaction videos — has never been in git. `~/ocp-backups/` holds tarballs
from 08-25 and 08-26, which predate the deterrent entirely.
