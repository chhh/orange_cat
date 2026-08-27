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

3. Put secrets in `.env` (gitignored):
   ```
   HA_LONG_LIVED_TOKEN=<token>
   CAT_VIDEO_KEY_INSIDE=<key>
   CAT_VIDEO_KEY_OUTSIDE=<key>
   ```

4. Non-secret settings live in `cat-deterrent.toml` (comments allowed):
   `[server]` host/buffer_mode, `[ha]` host/speaker, `[sound]` everything
   below.

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

## Auto-play on detection

`server.py` fires a sound through the camera speaker on detection. Config
comes from `cat-deterrent.toml` (`[sound]` section); secrets stay in `.env`:

```toml
[sound]
sounds = ["noise_white.wav", "siren.wav"]  # random pick per trigger
near_door_min_bottom = 0.85  # bbox bottom fraction; 0 = gate off
on_any_motion = false        # foot-test bypass
min_interval = 2             # repeat window (s) while target in view
max_interval = 3
```

When it fires, the `/motion` JSON carries `"sound": "<file>"`; the server log
prints `-> playing <file> (<why>)`.

## Animal detector (YOLOv8n ONNX)

`animal.py` runs a YOLOv8n model under `cv2.dnn` to find the animal before
the colour classifier scores it. The model file is **not in the repo**
(`models/` is gitignored) — install it once:

```bash
mkdir -p models
# one-time, on a dev machine -- ultralytics pulls ~2 GB of torch/CUDA wheels
pip install ultralytics
yolo export model=yolov8n.pt format=onnx imgsz=640 opset=12
mv yolov8n.onnx models/
```

Custom path via env: `ANIMAL_MODEL=/path/to/yolov8n.onnx` (default
`models/yolov8n.onnx`).

Validate:

```bash
uv run evaluate_detector.py
# expect: 5/5 intruder, 24/24 our-cats, 1/1 possum
```

Without the model the server **falls back to the motion-mask path**
(`animal.available()` is False) and logs a warning in `/health` — detection
degrades but still runs.