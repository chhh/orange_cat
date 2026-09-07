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


def self_host(target=None):
    """The address a service on `target` (default HA) would see us arrive from.

    odd-fellow moves between two houses that BOTH use 192.168.1.0/24, so its
    address is 192.168.7.4 over the tunnel from Dave's and a DHCP lease on
    Dima's LAN when on-site. HA has to fetch sounds from us by URL, so a
    hardcoded address is wrong in one of the two places -- and on 2026-09-07
    that cost the siren opener and Dima's voice clip.

    The routing table already knows the answer: ask it which source address it
    would use for a packet to HA. connect() on a UDP socket sends nothing, so
    this is free and works with the tunnel up or down.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target or HA_HOST, 8123))
        return s.getsockname()[0]
    except OSError:
        return "192.168.7.4"      # tunnel address: the historical default
    finally:
        s.close()

HA_SPEAKER = _get("ha", "speaker", "media_player.nursery_speaker_2")
HA_SSH_HOST = _get("ha", "ssh_host", "ha")

# Where HA fetches sounds we serve ourselves (the 8081 soundserver). Resolved
# per-process at import, which is enough: moving house means a restart anyway.
SELF_SOUND_BASE = os.getenv("DETER_SELF_SOUND_BASE") or f"http://{self_host()}:8081"

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
SOUND_MAX_DURATION = float(_get("sound", "max_duration", "30"))
