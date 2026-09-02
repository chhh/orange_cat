#!/bin/bash
# Restart the independent YOLO patrol after a reboot.
#
# Why this exists: ocp-detector.service and wg-quick@wg0 are systemd units and
# come back on their own, but the patrol is only setsid'd from whatever session
# started it. If odd-fellow reboots -- which the iTCO_wdt watchdog will make it
# do within ~60s of a hard lockup -- the patrol dies and does NOT return.
# That matters most exactly when it matters: if the HA delivery path is broken,
# the patrol is the ONLY detection channel.
#
# Safe to run repeatedly: it exits immediately if a patrol is already alive.
#
# Install with:
#   ( crontab -l 2>/dev/null | grep -v start-patrol.sh; \
#     echo "@reboot /home/david/ocp-watch/start-patrol.sh" ) | crontab -
# Verify with: crontab -l

# The lock, not the pgrep, is the real duplicate guard. The pgrep alone has a
# ~30s race: during the settle sleep below this process is just "bash
# start-patrol.sh" -- invisible to the pattern -- so a manual run and the
# */5 cron restart can both pass the check and arm twice (which is exactly
# what happened 2026-09-02 10:15: two patrols, two deter loops). The lock fd
# is inherited across exec/setsid into patrol.py and is held for its whole
# lifetime, so it releases automatically when the patrol dies.
exec 9>>/home/david/ocp-watch/start-patrol.lock
flock -n 9 || exit 0

# Belt and suspenders: also yield to a patrol started outside this script
# (which holds no lock).
pgrep -f "uv run /home/david/ocp-watch/patrol.py" >/dev/null && exit 0

cd /home/david/projects/ocp || exit 1
export PATH="/home/david/.local/bin:$PATH"

# Let wg-quick@wg0 and ocp-detector settle first: the patrol reads the segment
# buffers in /dev/shm/ocp, which do not exist until the recorders are running.
sleep 30

# Deterrent: armed, night window only, drill sound. See deter.py for the
# gates and the ground truth they were fitted to.
export DETER_ARM=1

# The deterrent needs HA_LONG_LIVED_TOKEN to reach the speaker. Without this
# the patrol arms, decides correctly, and then fails at the last step with
# "HA_LONG_LIVED_TOKEN not set" -- which is exactly what happened at 03:18 on
# 2026-08-27, while a separate process fired successfully.
set -a; . /home/david/projects/ocp/.env; set +a

exec setsid nohup uv run /home/david/ocp-watch/patrol.py \
     >> /home/david/ocp-watch/patrol.log 2>&1 < /dev/null
