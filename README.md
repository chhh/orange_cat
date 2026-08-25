# orange-cat

## Play a sound through a camera speaker

Home Assistant holds the UniFi Protect connection and exposes each camera with a
speaker as a `media_player` entity.  `talk.py` triggers playback through that
entity over HA's REST API — no direct Protect credentials needed.

### One-time setup

1. Copy the sound into HA so it can serve it over HTTP:
   - put it in `config/www/sounds/` (e.g. `DRILL.WAV`)
   - it's then reachable at `http://<HA-IP>:8123/local/sounds/DRILL.WAV`

2. Create a long-lived token: HA profile → Security → long-lived access token.

3. In repo `.env` (or export in shell):
   ```
   HA_LONG_LIVED_TOKEN=<token>
   HA_HOST=192.168.1.133          # default
   HA_SPEAKER=media_player.nursery_speaker_2   # default
   ```

### Play

```bash
uv run talk.py DRILL.WAV
```

The arg is a filename that already lives in `config/www/sounds/`. Filenames
are case-sensitive (`DRILL.WAV` != `DRILL.wav`).

### Play (curl)

Same call, raw:

```bash
curl -X POST http://192.168.1.133:8123/api/services/media_player/play_media \
  -H "Authorization: Bearer $HA_LONG_LIVED_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id":"media_player.nursery_speaker_2",
       "media_content_id":"http://192.168.1.133:8123/local/sounds/DRILL.WAV",
       "media_content_type":"music"}'
```

### Louder

Two ways:

**1. Raise the entity volume** (0.0–1.0):

```bash
curl -X POST http://192.168.1.133:8123/api/services/media_player/volume_set \
  -H "Authorization: Bearer $HA_LONG_LIVED_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id":"media_player.nursery_speaker_2","volume_level":1.0}'
```

**2. Boost the file itself** (gain, clips if pushed too far):

```bash
ffmpeg -i DRILL.WAV -filter:a "volume=3.0" DRILL_LOUD.WAV
```

Copy the louder file to `config/www/sounds/`, then play it the same way.