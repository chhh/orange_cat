"""Play a sound through a UniFi Protect camera speaker via Home Assistant.

HA already holds Protect credentials and exposes each camera speaker as a
media_player entity. This script just calls HA's REST API.

Env:
  HA_LONG_LIVED_TOKEN  required -- HA profile -> Security -> long-lived token
  HA_HOST              default 192.168.1.133
  HA_SPEAKER           default media_player.nursery_speaker_2
  HA_SSH_HOST          optional ssh alias (default ha) -- used to list HA files;
                       falls back to listing the local sounds/ dir

Usage:
  uv run talk.py               list available sounds
  uv run talk.py <sound-file>  play it

<sound-file> must exist in HA's config/www/sounds/ (copy it there first).
"""

import json
import os
import subprocess
import sys
import urllib.request


def list_sounds():
    host = os.getenv("HA_SSH_HOST", "ha")
    try:
        out = subprocess.run(
            ["ssh", host, "ls", "/config/www/sounds"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return sorted(f for f in out.stdout.split() if f.lower().endswith(".wav"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # No ssh to HA -- list the repo's own sounds/ dir instead.
    local_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
    try:
        return sorted(f for f in os.listdir(local_dir) if f.lower().endswith(".wav"))
    except FileNotFoundError:
        return []


def play(name, token=None, host=None, speaker=None):
    """Play a sound file that lives in HA's config/www/sounds/."""
    token = token or os.getenv("HA_LONG_LIVED_TOKEN")
    if not token:
        raise RuntimeError("HA_LONG_LIVED_TOKEN not set")
    host = host or os.getenv("HA_HOST", "192.168.1.133")
    speaker = speaker or os.getenv("HA_SPEAKER", "media_player.nursery_speaker_2")

    name = os.path.basename(name)
    payload = {
        "entity_id": speaker,
        "media_content_id": f"http://{host}:8123/local/sounds/{name}",
        "media_content_type": "music",
    }
    req = urllib.request.Request(
        f"http://{host}:8123/api/services/media_player/play_media",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"Playing {name} on {speaker} ...", flush=True)
        print(resp.status, resp.read().decode())


def main():
    if not os.getenv("HA_LONG_LIVED_TOKEN"):
        raise SystemExit("Set HA_LONG_LIVED_TOKEN (HA profile -> Security).")

    if len(sys.argv) != 2:
        sounds = list_sounds()
        print("Available sounds:")
        for name in sounds:
            print(f"  {name}")
        print("\nUsage: uv run talk.py <sound-file>")
        raise SystemExit(0)

    play(sys.argv[1])


if __name__ == "__main__":
    main()