#!/bin/bash
# Overnight report. Runs from system cron, independent of any Claude session.
# Output: /home/david/ocp-watch/overnight-report.txt (overwritten each run)
exec > /home/david/ocp-watch/overnight-report.txt 2>&1
cd /home/david/projects/ocp || exit 1
SINCE="${1:-21:00}"

echo "OVERNIGHT REPORT  $(date '+%Y-%m-%d %H:%M')   (window: since $SINCE yesterday)"
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
echo "-- HA DELIVERY (the open blocker) -------------------------------"
p=$(/bin/grep -c "POST /motion" frames/server.log); p=${p:-0}
echo "POST /motion total in current server.log: $p"
last=$(/bin/grep -n "POST /motion" frames/server.log | tail -1 | cut -d: -f1)
if [ -n "$last" ]; then echo "last POST context:"; sed -n "$((last-3)),${last}p" frames/server.log | sed 's/^/    /'; fi
echo "newest event row:"; tail -1 frames/events.csv | cut -c1-160 | sed 's/^/    /'
echo
echo "-- DETERRENT ----------------------------------------------------"
fires=$(/bin/grep -c "deterrent: PLAYED" /home/david/ocp-watch/patrol.log /home/david/ocp-watch/exitwatch.log 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')
echo "sounds played: $fires"
/bin/grep -hE "deterrent: (PLAYED|WOULD HAVE|animal AT THE FLAP|PERSON in|playback FAILED)" \
  /home/david/ocp-watch/patrol.log /home/david/ocp-watch/exitwatch.log 2>/dev/null | tail -12 | sed 's/^/    /'
echo "reaction videos:"
ls -lh /home/david/ocp-watch/reactions/fire-*.mp4 2>/dev/null | awk '{print "    ",$9,$5}' || echo "     none"
echo "WATCH ANY FIRE VIDEO: the open question is whether the cat visibly reacts."
echo
echo "-- PATROL: WHAT VISITED -----------------------------------------"
hits=$(/bin/grep -c "PATROL ANIMAL" /home/david/ocp-watch/patrol.log); hits=${hits:-0}
echo "total PATROL ANIMAL lines in log (all time): $hits"
echo
echo "ORANGE CAT hits (LOOK AT EVERY FRAME -- dawn light fakes these):"
/bin/grep "ORANGE CAT" /home/david/ocp-watch/patrol.log | tail -20 | sed 's/^/    /'
echo
echo "other animals (residents etc), last 20:"
/bin/grep "PATROL ANIMAL" /home/david/ocp-watch/patrol.log | /bin/grep -v "ORANGE CAT" | tail -20 | sed 's/^/    /'
echo
echo "-- PATROL LIVENESS (gaps = it died) -----------------------------"
/bin/grep -E "heartbeat|patrol armed|patrol error" /home/david/ocp-watch/patrol.log | tail -12 | sed 's/^/    /'
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
echo "Full context for today's session: /home/david/projects/ocp/next-prompt8.26.txt"
