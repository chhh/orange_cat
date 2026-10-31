#!/bin/bash
# Overnight report. Runs from system cron, independent of any Claude session.
# Output: /home/david/ocp-watch/overnight-report.txt (overwritten each run)
#
# WINDOW SCOPING: a 21:00 cron (night-window-marker.sh) stamps
# "NIGHT WINDOW START" into patrol.log and server.log. Everything below reads
# only lines AFTER the last marker, so the report describes the night it is
# reporting on -- before this, all-time greps put previous nights' deterrent
# lines into the morning read (2026-08-31: three stale lines in a one-fire
# night).
exec > /home/david/ocp-watch/overnight-report.txt 2>&1
cd /home/david/projects/ocp || exit 1

PLOG=/home/david/ocp-watch/patrol.log
SLOG=frames/server.log

# Lines after the last night-window marker (whole file if no marker yet).
window() {
  tac "$1" 2>/dev/null | awk '/NIGHT WINDOW START/{exit} {print}' | tac
}
WP=$(mktemp); window "$PLOG" > "$WP"
WS=$(mktemp); window "$SLOG" > "$WS"
trap 'rm -f "$WP" "$WS"' EXIT

echo "OVERNIGHT REPORT  $(date '+%Y-%m-%d %H:%M')   (window: since the last 21:00 marker)"
echo "================================================================"
echo
echo "-- BOX HEALTH ---------------------------------------------------"
echo "uptime      : $(uptime -p)   (booted $(uptime -s))"
echo "AC mains    : $(cat /sys/class/power_supply/AC/online)   (1 = good)"
n=$(journalctl -b -k | /bin/grep -c "acpi_os_execute_deferred hogged"); n=${n:-0}
echo "ACPI hogs   : $n  (0 = firmware fix holding)"
if [ -e /sys/class/watchdog/watchdog0/timeleft ]; then
  echo "watchdog    : timeleft=$(cat /sys/class/watchdog/watchdog0/timeleft) (60 pinned = armed)"
else
  echo "watchdog    : *** NOT LOADED -- box rebooted; run: sudo modprobe iTCO_wdt ***"
fi
echo "detector    : $(systemctl is-active ocp-detector)    tunnel: $(systemctl is-active wg-quick@wg0)"
if ps -eo args | /bin/grep -q "[p]atrol.py"; then echo "patrol      : alive"; else echo "patrol      : *** DEAD ***"; fi
echo
echo "-- SCORECARD (the metric: are we deterring, or feeding?) --------"
fired=$(/bin/grep -c "deterrent decision: fired" "$WP")
exiting=$(/bin/grep -c "deterrent decision: exiting" "$WP")
inflap=$(/bin/grep -c "deterrent decision: in_flap" "$WP")
person=$(/bin/grep -c "deterrent decision: person" "$WP")
toofar=$(/bin/grep -c "deterrent decision: too_far" "$WP")
orange=$(/bin/grep -c "ORANGE CAT" "$WP")
echo "    pre-entry fires        : $fired"
echo "    exit visits seen       : $exiting  (each implies AN ENTRY happened -- a meal we did not stop)"
echo "    in-flap holds          : $inflap"
echo "    person stand-downs     : $person"
echo "    too-far holds          : $toofar"
echo "    orange sightings logged: $orange"
echo "    (decision lines are deduped per state change; counts are episodes,"
echo "     not frames. Group visits by eye from the timeline below; video is truth.)"
echo
echo "-- STREAM (the N1 loop: hot = live frames, blind = old segment path) --"
/bin/grep -E "patrol stream|patrol opening|patrol closing" "$WP" | tail -12 | sed 's/^/    /'
echo
echo "-- DETERRENT (this window only) ---------------------------------"
/bin/grep -hE "deterrent: (PLAYED|WOULD HAVE|EXITING|cat is INTO the flap|PERSON in|playback FAILED|CLOSING)|deterrent decision:|escalation:|reaction: saved" "$WP" "$WS" | tail -30 | sed 's/^/    /'
echo "reaction videos (all):"
ls -lh /home/david/ocp-watch/reactions/fire-*.mp4 2>/dev/null | awk '{print "    ",$9,$5}' | tail -8 || echo "     none"
echo "WATCH ANY FIRE VIDEO: the open question is whether the cat visibly reacts."
echo
echo "-- HA DELIVERY --------------------------------------------------"
p=$(/bin/grep -c "POST /motion" "$WS"); p=${p:-0}
echo "POST /motion this window: $p   (all-time in current server.log: $(/bin/grep -c 'POST /motion' $SLOG))"
last=$(/bin/grep -n "POST /motion" "$WS" | tail -1 | cut -d: -f1)
if [ -n "$last" ]; then echo "last POST context:"; sed -n "$((last-3)),${last}p" "$WS" | sed 's/^/    /'; fi
echo "newest event row:"; tail -1 frames/events.csv | cut -c1-160 | sed 's/^/    /'
echo
echo "-- PATROL: WHAT VISITED (this window only) ----------------------"
echo "ORANGE CAT hits (LOOK AT EVERY FRAME -- dawn light fakes these):"
/bin/grep "ORANGE CAT" "$WP" | sed 's/^/    /'
[ "$orange" -eq 0 ] && echo "    (none this window)"
echo
echo "other animals (residents etc), this window:"
/bin/grep "PATROL ANIMAL" "$WP" | /bin/grep -v "ORANGE CAT" | tail -20 | sed 's/^/    /'
echo
echo "-- PATROL LIVENESS (gaps = it died) -----------------------------"
/bin/grep -E "heartbeat|patrol armed|patrol error" "$WP" | tail -12 | sed 's/^/    /'
echo
echo "-- COLOUR vs IR on tonight's frames ------------------------------"
echo "(colour = ginger discriminator works; IR = ~0% ginger regardless)"
/home/david/projects/ocp/.venv/bin/python - <<'PY'
import glob, os, cv2, numpy as np
fs=sorted(glob.glob("/home/david/ocp-watch/patrol/patrol-*.jpg"), key=os.path.getmtime)[-25:]
for p in fs:
    f=cv2.imread(p)
    if f is None: continue
    b,g,r=cv2.split(f.astype(np.float32))
    s=float(np.mean(np.abs(r-g))+np.mean(np.abs(g-b)))
    print(f"    {os.path.basename(p):28s} spread={s:6.2f}  {'IR  <-- ginger unusable' if s<3 else 'COLOUR'}")
if not fs: print("    (no patrol frames saved)")
PY
echo
echo "-- REBOOTS OVERNIGHT --------------------------------------------"
last reboot 2>/dev/null | head -4 | sed 's/^/    /'
echo
echo "State and next steps: /home/david/projects/ocp/watch/LIVE-STATE.md"
