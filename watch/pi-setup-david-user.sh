#!/bin/bash
# One-time root setup on Dima's Pi (rpi-vpn) for the OCP migration.
# Run as:   sudo bash ~/ocp-bench/pi-setup-david-user.sh
#
# It does exactly three things, and is safe to run twice:
#   1. creates a login `david` (no password -- key-only, and NOT in sudo)
#   2. installs odd-fellow's public key so Dave's laptop can ssh in as david
#   3. enables "linger" so david's services start at boot with nobody logged in
#
# It installs no packages and touches nothing under /home/chhh, the VPN, or
# ~/orange-cat. The hardware watchdog is already on (RuntimeWatchdogSec=1min),
# so it is left alone.
#
# To undo everything:   sudo loginctl disable-linger david && sudo userdel -r david
set -euo pipefail

USER_NAME=david
PUBKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOI0GC4JxjelB+r7tInLnWZ+yGj4mYKtlPmpE2CZGXmx dave@odd-fellow'

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo." >&2
    exit 1
fi

if id "$USER_NAME" >/dev/null 2>&1; then
    echo "user $USER_NAME already exists -- leaving the account as it is"
else
    /usr/sbin/useradd --create-home --shell /bin/bash "$USER_NAME"
    echo "created user $USER_NAME"
fi

HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.ssh"
AUTH="$HOME_DIR/.ssh/authorized_keys"
touch "$AUTH"
grep -qxF "$PUBKEY" "$AUTH" || echo "$PUBKEY" >> "$AUTH"
chown "$USER_NAME:$USER_NAME" "$AUTH"
chmod 600 "$AUTH"
echo "key installed in $AUTH"

loginctl enable-linger "$USER_NAME"
echo "linger enabled"

echo
echo "--- result ---"
id "$USER_NAME"
ls -ld "$HOME_DIR"
loginctl show-user "$USER_NAME" -p Linger 2>/dev/null || true
echo "Done. Tell Dave it ran; he tests with:  ssh david@192.168.1.142"
