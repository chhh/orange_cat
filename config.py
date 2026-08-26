"""Runtime config from cat-deterrent.toml.

Secrets (RTSP stream keys, HA long-lived token) stay in .env; this module
holds everything else. Any UPPER_CASE env var of the same name overrides the
file, so existing .env-driven deployments keep working.
"""

import os
import tomllib
from pathlib import Path

_PATH = Path(__file__).parent / "cat-deterrent.toml"

try:
    with open(_PATH, "rb") as fh:
        _C = tomllib.load(fh)
except (FileNotFoundError, tomllib.TOMLDecodeError):
    _C = {}


def _get(section, key, default):
    return os.getenv(key.upper(), _C.get(section, {}).get(key, default))


# Camera / server
HOST = _get("server", "host", "192.168.1.1")
BUFFER_MODE = str(_get("server", "buffer_mode", "segments")).lower()
BG_REFRESH_SECONDS = int(_get("server", "bg_refresh_seconds", "300"))

# Home Assistant (the secret -- the token -- stays in .env)
HA_HOST = _get("ha", "host", "192.168.1.133")
HA_SPEAKER = _get("ha", "speaker", "media_player.nursery_speaker_2")
HA_SSH_HOST = _get("ha", "ssh_host", "ha")

# Sound / deterrent
_RAW_SOUNDS = _get("sound", "sounds", "noise_white.wav")
if isinstance(_RAW_SOUNDS, list):
    ORANGE_SOUNDS = [s.strip() for s in _RAW_SOUNDS if str(s).strip()]
else:
    ORANGE_SOUNDS = [s.strip() for s in str(_RAW_SOUNDS).split(",") if s.strip()]
NEAR_DOOR_MIN_BOTTOM = float(_get("sound", "near_door_min_bottom", "0.85"))
SOUND_ON_ANY_MOTION = str(_get("sound", "on_any_motion", "false")).lower() in \
                      ("1", "true", "yes")
SOUND_MIN_INTERVAL = float(_get("sound", "min_interval", "2"))
SOUND_MAX_INTERVAL = float(_get("sound", "max_interval", "3"))
