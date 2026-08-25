#!/usr/bin/env bash
# Stage the files zyxx needs that a git clone will NOT bring: they are
# gitignored or root-only. Run this on the laptop AFTER the server is stopped,
# so the background models are final rather than mid-refresh.
# Usage: ./stage-zyxx-bundle.sh [dest]   (default: ~/zyxx-handover)
set -euo pipefail
cd "$(dirname "$0")"
DEST="${1:-$HOME/zyxx-handover}"

if ./run-server.sh status 2>/dev/null | grep -q '"ok":true'; then
  echo "REFUSING: the detector server is still up. Stop it first:" >&2
  echo "  ./run-server.sh stop" >&2
  exit 1
fi

mkdir -p "$DEST/frames"
chmod 700 "$DEST"
install -m 600 .env "$DEST/.env"
install -m 644 frames/bg_inside.npy frames/bg_outside.npy "$DEST/frames/"
[ -f deterrent-hardware.md ] && install -m 644 deterrent-hardware.md "$DEST/"

# Claude Code context that does not come with the repo clone. NOT ~/.claude.json
# -- that holds credentials and session history; log in fresh on zyxx.
MEM="$HOME/.claude/projects/-home-davidp-projects-ocp/memory"
[ -f "$HOME/.claude/CLAUDE.md" ] && install -m 644 "$HOME/.claude/CLAUDE.md" "$DEST/CLAUDE.global.md"
[ -d "$MEM" ] && cp -rf "$MEM" "$DEST/memory"

# WireGuard profile: root-only, so this needs sudo. Confirmed 2026-08-25 to
# live at /etc/wireguard/wg0.conf -- NOT in NetworkManager's system-connections,
# even though NM has the connection imported from it. Same key, same
# 192.168.7.4 -- see the HARD RULE in second-box-zyxx.md: never both machines
# up at once.
sudo install -m 600 -o "$USER" -g "$USER" \
  /etc/wireguard/wg0.conf "$DEST/wg0.conf"

# The tunnel is owned by systemd (wg-quick@wg0), NOT NetworkManager, and the
# reresolve timer was added after the 114-minute outage on 2026-08-24. Both
# units go with it.
sudo install -m 644 -o "$USER" -g "$USER" \
  /etc/systemd/system/wg-reresolve.service \
  /etc/systemd/system/wg-reresolve.timer "$DEST/"

echo
echo "Staged in $DEST:"
ls -la "$DEST" "$DEST/frames"
echo
echo "SECRETS: .env holds the Protect RTSP keys, wg0.nmconnection holds the"
echo "tunnel private key. Move to zyxx directly, then shred this directory."
