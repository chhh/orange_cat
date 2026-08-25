"""Play a sound through a UniFi Protect camera speaker via Home Assistant.

HA already holds Protect credentials and exposes each camera speaker as a
media_player entity. This script just calls HA's REST API.

Env:
  HA_LONG_LIVED_TOKEN  required -- HA profile -> Security -> long-lived token
  HA_HOST              default 192.168.1.133
  HA_SPEAKER           default media_player.nursery_speaker_2

Usage:  uv run talk.py <sound-file>

<sound-file> must exist in HA's config/www/sounds/ (copy it there first).
"""

import json
import os
import sys
import urllib.request


def main():
    token = os.getenv("HA_LONG_LIVED_TOKEN")
    if not token:
        raise SystemExit("Set HA_LONG_LIVED_TOKEN (HA profile -> Security).")

    host = os.getenv("HA_HOST", "192.168.1.133")
    speaker = os.getenv("HA_SPEAKER", "media_player.nursery_speaker_2")

    if len(sys.argv) != 2:
        raise SystemExit("Usage: uv run talk.py <sound-file>")

    name = os.path.basename(sys.argv[1])
    content_id = f"http://{host}:8123/local/sounds/{name}"
    payload = {
        "entity_id": speaker,
        "media_content_id": content_id,
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


if __name__ == "__main__":
    main()