#!/usr/bin/env bash
# Copy this repo to a Pi over ssh and nothing else.
#
# Usage:  ./copy-to-pi.sh "ssh rpi-vpn"
#
# The argument is the ssh connection as you would type it. It runs exactly
# once, so a password prompt (or host-key confirm) appears once on your tty.
# If the host needs flags (port, user), put them in ~/.ssh/config under the
# alias instead of on the command line.
set -euo pipefail

ssh_cmd="${1:?usage: $0 \"ssh <host>\"}"

# rsync wants the remote as host:path and takes the shell separately; strip a
# leading "ssh " to reuse the rest as that host.
target="${ssh_cmd#ssh }"

cd "$(dirname "$0")"

rsync -av \
  -e ssh \
  --exclude '.venv' \
  --exclude '.git' \
  --exclude 'frames' \
  --exclude '__pycache__' \
  --exclude '*.jpg' \
  --exclude 'samples' \
  --exclude '.DS_Store' \
  . "$target:~/orange-cat/"

echo "copied to $target:~/orange-cat/"
echo "next, on the Pi:  cd ~/orange-cat && ./run-server.sh start"