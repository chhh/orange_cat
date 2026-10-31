#!/usr/bin/env bash
# disable-ocp.sh -- take the whole OCP stack down on rpi-vpn, and keep it down.
#
# This is stronger than `ocpctl stop`:
#   ocpctl stop   leaves the units enabled (they return on boot) and leaves
#                 david's crontab alone; only the patrol is held back by the
#                 ~/ocp-watch/.stopped marker.
#   this script   removes the crontab lines too and disables the units, so
#                 nothing returns on boot and cron cannot resurrect the patrol.
#                 Use it when the camera is off or the Pi is being serviced.
#
# Usage, from a checkout of this repo (needs root):
#   sudo watch/pi/disable-ocp.sh            # stop + disable everything
#   sudo watch/pi/disable-ocp.sh restore    # put crontab + units back
#
# Before removing anything the crontab is copied to
# /var/backups/ocp-crontab-<timestamp>.bak, and only the start-patrol.sh
# lines are removed -- david's report/marker/prune jobs keep running.
set -uo pipefail

DAVID_USER=david
DAVID_HOME=/home/david
BACKUP_GLOB="/var/backups/ocp-crontab-*.bak"
UNITS="ocp-detector.service ocp-soundserver.service"
STOPPED="$DAVID_HOME/ocp-watch/.stopped"

say() { printf '\n== %s\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root:  sudo $0 ${1:-}"
id "$DAVID_USER" >/dev/null 2>&1 || die "user $DAVID_USER does not exist"
RUNTIME="/run/user/$(id -u "$DAVID_USER")"

# Run something as david while still talking to david's systemd --user bus.
as_david() { sudo -u "$DAVID_USER" "$@"; }
david_systemctl() { sudo -u "$DAVID_USER" env XDG_RUNTIME_DIR="$RUNTIME" systemctl --user "$@"; }

running() {
    ps -u "$DAVID_USER" -o pid=,cmd= \
      | grep -E 'server\.py|patrol\.py|http\.server 8081|rtsp_transport tcp' \
      | grep -v grep
}

verify_down() {
    echo "units:"
    # unquoted on purpose: $UNITS is a list of two unit names
    david_systemctl is-enabled $UNITS 2>&1 | sed 's/^/  /'
    david_systemctl is-active $UNITS 2>&1 | sed 's/^/  /'
    echo "processes:"
    local p
    if p=$(running) && [ -n "$p" ]; then
        echo "$p" | sed 's/^/  /'
        echo "  ^^ STILL RUNNING"
    else
        echo "  none (good)"
    fi
    echo "ports:"
    curl -sf -m 3 http://127.0.0.1:8080/health >/dev/null \
      && echo "  8080 STILL UP" || echo "  8080 down (good)"
    curl -sf -m 3 http://127.0.0.1:8081/ >/dev/null \
      && echo "  8081 STILL UP" || echo "  8081 down (good)"
}

do_disable() {
    say "0. what is running now"
    running | sed 's/^/  /' || true

    say "1. back up david's crontab, strip the start-patrol.sh lines"
    # Back up only a crontab that still has the patrol line: running this
    # twice must not overwrite the good backup with the already-stripped
    # version, or `restore` would restore a crontab with no patrol in it.
    local stamp backup cur
    cur=$(mktemp)
    if as_david crontab -l >"$cur" 2>/dev/null; then
        if grep -q 'ocp-watch/start-patrol\.sh' "$cur"; then
            stamp=$(date +%Y%m%d-%H%M%S)
            backup="/var/backups/ocp-crontab-$stamp.bak"
            cp "$cur" "$backup"
            chmod 600 "$backup"
            echo "backup: $backup"
            grep -v 'ocp-watch/start-patrol\.sh' "$cur" | as_david crontab -
        else
            echo "no start-patrol.sh line present; leaving existing backups alone"
        fi
        echo "crontab now:"
        as_david crontab -l | sed 's/^/  /'
    else
        echo "(david has no crontab; nothing to strip)"
    fi
    rm -f "$cur"

    say "2. kill the patrol supervisor"
    pkill -u "$DAVID_USER" -f 'ocp-watch/patrol\.py'
    pkill -u "$DAVID_USER" -f 'ocp-watch/start-patrol\.sh'
    sleep 1

    say "3. stop and disable the systemd --user units"
    david_systemctl disable --now $UNITS
    # a unit killed while active stays in "failed"; clear that so `status`
    # reads disabled/inactive rather than disabled/failed
    david_systemctl reset-failed $UNITS

    say "4. kill anything the units did not reap"
    pkill -u "$DAVID_USER" -f 'projects/ocp/\.venv/bin/python server\.py'
    pkill -u "$DAVID_USER" -f 'uv run --frozen server\.py'
    pkill -u "$DAVID_USER" -f 'projects/ocp/\.venv/bin/python -m http\.server 8081'
    pkill -u "$DAVID_USER" -f 'rtsp_transport tcp'
    sleep 1

    say "5. verify"
    verify_down

    cat <<EOF

stopped and disabled. Re-enable with:
  sudo $0 restore
(restores the newest crontab backup, enables both units, removes .stopped;
 the patrol then returns from cron within 5 minutes on its own -- do not
 start it from a login or agent session, it would die with that session.)
EOF
}

do_restore() {
    say "1. restore david's crontab"
    # newest backup that actually contains the patrol line (a later run of
    # `disable` may have left a stripped crontab as the newest file)
    local f backup=""
    for f in $(ls -1t $BACKUP_GLOB 2>/dev/null); do
        if grep -q 'ocp-watch/start-patrol\.sh' "$f"; then backup=$f; break; fi
    done
    if [ -n "$backup" ]; then
        as_david crontab "$backup"
        echo "restored from: $backup"
        as_david crontab -l | sed 's/^/  /'
    else
        die "no $BACKUP_GLOB found; install watch/pi/crontab.txt by hand"
    fi

    say "2. clear the ocpctl stop marker"
    as_david rm -f "$STOPPED"

    say "3. enable and start the units"
    david_systemctl enable --now $UNITS

    say "4. verify (the patrol itself arrives from cron within 5 min)"
    david_systemctl is-enabled $UNITS 2>&1 | sed 's/^/  /'
    david_systemctl is-active $UNITS 2>&1 | sed 's/^/  /'
    curl -sf -m 5 http://127.0.0.1:8080/health | head -c 300; echo
    echo "patrol: $(pgrep -u "$DAVID_USER" -f 'ocp-watch/patrol\.py' >/dev/null && echo running || echo 'not yet -- cron starts it within 5 min')"
}

case "${1:-disable}" in
    disable) do_disable ;;
    restore|enable) do_restore ;;
    *) echo "usage: sudo $0 [disable|restore]" >&2; exit 2 ;;
esac
