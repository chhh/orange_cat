# AGENTS.md — the Orange Cat Problem (ocp)

Orientation for anyone (human or agent) picking this project up. Read this
before touching the code; most of the non-obvious facts below are *not*
recoverable from the source or the git history.

## 1. What we are trying to achieve

A stray **orange tabby** has learned to use the cat door at Dima's house and
eats the resident cats' food, mostly at night. We want to:

1. **Detect** when the animal at the door is the intruder and not one of the
   residents, and notify people (current state).
2. Eventually **deter** it — a sound, a voice recording, or a water spray fired
   *before* it gets through the flap (goal).

Two consequences of the deterrence goal that are easy to get backwards:

- **Precision beats recall.** A false positive sprays one of Dima's own cats,
  which risks teaching it to avoid its own door. Do not tune thresholds toward
  "flag when unsure" the way you would for a notify-only system. Given the
  slow, loitering stray there is time to require two consecutive events to
  agree before firing anything.
- **The outside camera is the one that matters** for deterrence — the verdict
  has to exist while the cat is still on the approach.

## 2. The cats

| Who | Appearance | Role |
|---|---|---|
| Dima's resident cats (several) | all **black and white** | must never be flagged |
| The stray | **orange tabby (ginger)** | target |

That colour split is the whole basis of the classifier:

- **Daylight (colour mode)**: fraction of *moving* pixels that are warm and
  saturated (`warm_pct`). Threshold 8 %.
- **Night (IR mode)**: colour is completely absent (`warm_pct` is 0.00 on every
  night clip). What works is `rel_bright` — mean luminance of the animal divided
  by the mean luminance of the background it stands in front of. Ginger fur
  reflects near-infrared strongly, black fur absorbs it. Calibrated on ten
  labelled clips from the night of 2026-08-16:

  | | rel_bright | cv (secondary) |
  |---|---|---|
  | orange tabby | 1.226 – 1.660 | 0.224 – 0.381 |
  | black & white | 0.716 – 0.915 | 0.428 – 0.645 |

  Threshold `IR_REL_BRIGHT_THRESHOLD = 1.05` (29 % margin). Scored 9/10 clips
  end to end. **The threshold was fitted on the same clips it was tested on;
  fresh nights are the real validation.**

- Dead ends, so nobody re-derives them: every contrast/texture feature
  (luma_std, cv, percentile spread, bright/dark fractions, Otsu separability,
  multi-scale coarseness) overlaps completely between the cats — they all
  normalise away overall brightness, which is the actual signal.

Behaviour notes: the stray is **slow and loiters near the door before going
in**. That is what makes ~1–4 s of latency acceptable and puts it inside the
1.5–25 % of-frame isolation window the classifier needs (a cat barrelling past
the lens is `unmeasurable`).

Lighting note: the **outside camera has patio lights next to it**, so at night
it usually stays in colour with decent quality rather than dropping to IR — the
IR path is mainly needed for the *inside* camera, which is what it was
calibrated on. Verify `is_ir` per event in `frames/events.csv` rather than
assuming.

## 3. Timings and costs measured so far

| What | Measured |
|---|---|
| RTSP connection handshake (cv2 → UniFi Protect) | ~3.1 s |
| Decoding a buffered ~2 s segment on demand | 0.146 s |
| Per-event latency, `BUFFER_MODE=off` (dial out per event) | ~4.0 s, 0 % idle CPU, ~96 MB, no pre-trigger frames |
| Per-event latency, `BUFFER_MODE=decoded` (frames in RAM) | 0.25 s, **24 % idle CPU**, ~295 MB, ~2.7 s history |
| Per-event latency, `BUFFER_MODE=segments` (ffmpeg remux, default) | ~1.0 s, 0 % idle CPU, ~50 MB, ~10–12 s history |
| Rolling 10 s compressed window on disk (`/dev/shm/ocp`) | ~650 KB |
| Snapshot posted by Home Assistant | ~30 KB JPEG |
| Frame grabbed directly from RTSP | ~243 KB, 1024×576 |
| Per-frame confidence on the black-and-white cats | 57–60 % — one frame is a coin flip; verdicts come from a burst of 15 frames voting |
| Camera re-exposure when a pale animal enters | mean frame luminance swings 59 → 156, so gain is normalised before background subtraction |
| Static warm pixels in the outside scene | whole frame 4.38 %; wooden gate 23.97 %; ROI crop 0.52 % |
| WireGuard tunnel overhead (nested in a commercial VPN) | ~3 ms |

Sources for these numbers: `server.py` (`BUFFER_MODE` comment), `segments.py`,
`streamer.py`, `detect.py`, `calibrate.py`.

## 4. Topology — what triggers what

```
Dima's house (LAN 192.168.1.0/24)
┌──────────────────────────────────────────────────────────────┐
│ UniFi Protect on the UDM (192.168.1.1)                        │
│   cameras: "inside" (pegboard wall + flap), "outside" (door,  │
│   concrete approach, patio lights)                            │
│   RTSP :7447  RTSPS :7441  — stream keys in .env              │
│         │ motion event (binary_sensor)   │ RTSP video          │
│         ▼                                │                     │
│ Home Assistant (Raspberry Pi 192.168.1.133)                   │
│   automation: on motion → REST POST to the detector           │
│   (JSON {"camera": "..."} preferred; multipart snapshot ok)   │
│         │                                │                     │
│ WireGuard server on the UDM (UDP 51820, tunnel 192.168.7.0/24)│
└─────────┼────────────────────────────────┼─────────────────────┘
          │  tunnel                        │  tunnel (CAT_HOST=192.168.7.1)
          ▼                                ▼
Dave's machine asset14201 (192.168.7.4 on wg0)
  server.py  :8080  POST /motion, GET /health
    ├─ segments.py  ffmpeg -c copy RTSP → rolling 2 s×6 segments in /dev/shm/ocp
    ├─ streamer.py  (optional) decoded ring buffer, one thread per camera
    ├─ capture.py   RTSP grab/grab_burst, per-frame measure(); stand-alone sampler
    └─ detect.py    ROI crop → background model (frames/bg_<cam>.npy)
                    → motion mask → colour or IR features → classify → vote
  writes  frames/events/<cam>-<ts>.jpg (+ -posted.jpg), frames/events.csv
  returns JSON verdict to HA  →  HA notifies people (see §7)
```

Event flow, step by step:

1. Protect detects motion → HA `binary_sensor.<camera>_motion` turns on.
2. HA automation POSTs to `http://192.168.7.4:8080/motion`.
3. `server.py` collects a burst of 15 frames: from the segment buffer if warm
   (includes ~10 s of *pre-trigger* footage, which is where the well-isolated
   approach frames are), else the decoded ring buffer, else a cold RTSP grab.
   A posted snapshot is treated as a *trigger*, not as the evidence — it is
   saved byte-for-byte and scored too, but the buffer frames dominate.
4. Every frame: motion mask against the background model inside the ROI →
   `orange_cat` / `probably_resident` / `no_orange` / `no_animal` /
   `unmeasurable` / `no_background`.
5. Only decisive verdicts vote; the winner and its confidence are returned
   and logged. The best frame is kept in `frames/events/`.
6. A background-refresh thread blends a quiet frame into the model every
   `BG_REFRESH_SECONDS` (300) so the model tracks the light.

Verdict semantics for downstream automations: `orange_cat` = act;
`probably_resident` / `no_orange` = a resident, do nothing; everything else =
abstain.

### Networking gotchas (they will bite again)

- **Both houses use `192.168.1.0/24`.** The remote `/24` can never go into
  WireGuard `AllowedIPs`; the UDM's LAN address `192.168.1.1` is unroutable from
  Dave's side. Use the UDM's tunnel-side address `192.168.7.1` for RTSP
  (`CAT_HOST=192.168.7.1`). HA at `192.168.1.133` is routed as a `/32`.
- **On-site at Dima's the tunnel cannot work** (client would dial its own WAN
  address; the UDM does not hairpin UDP). There: stop `wg-quick@wg0`, join the
  main SSID `Internetz_5GHz` (not the guest one), and use
  `CAT_HOST=192.168.1.1`.
- UniFi's exported client config includes `DNS = 192.168.7.1` (breaks
  `wg-quick` without `resolvconf`) and `AllowedIPs = 0.0.0.0/0` (full tunnel).
  Strip the first, narrow the second, every time it is re-exported. Use
  `systemctl restart wg-quick@wg0`, not `wg-quick up`.
- Anything touching the cameras, the UDM, or HA needs Dima; Dave cannot change
  that hardware remotely.

## 5. Running the server code

### Where it can run

| Location | Pros | Cons |
|---|---|---|
| **Dave's machine over WireGuard** (current) | full dev environment, easy iteration | ~3 ms tunnel + Dave's uptime; unusable when Dave is physically at Dima's |
| **A box on Dima's LAN** (a spare laptop / mini PC / the HA Pi) | lowest latency, no VPN dependency, always on | Pi 4 is marginal for `decoded` mode; `segments` mode is fine (0 % idle CPU, decode only on events); needs ffmpeg + Python 3.12 |
| Inside HA as an add-on / container | co-located with HA | HA OS containerisation makes RTSP + `/dev/shm` fiddlier; not attempted |

Nothing in the code cares which; only `CAT_HOST` and the HA automation URL
change.

### Prerequisites

- Python ≥ 3.12 and [`uv`](https://docs.astral.sh/uv/):
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- `ffmpeg` on `PATH` (needed for the default `segments` buffer):
  `sudo apt install ffmpeg`
- Python deps are pinned in `uv.lock` (`fastapi`, `uvicorn`, `opencv-python`,
  `python-dotenv`, `python-multipart`); `uv run …` installs them into `.venv`
  automatically.
- A `.env` file (gitignored) in the repo root:

  ```
  CAT_VIDEO_KEY_INSIDE=<Protect RTSP stream key for the inside camera>
  CAT_VIDEO_KEY_OUTSIDE=<Protect RTSP stream key for the outside camera>
  CAT_HOST=192.168.7.1        # 192.168.1.1 when on Dima's LAN
  # optional
  BUFFER_MODE=segments        # segments | decoded | off
  BG_REFRESH_SECONDS=300
  ```

  Stream keys come from UniFi Protect → camera → Settings → Advanced → RTSP
  (enable a stream; the key is the last path component of the URL).

### Commands

```
uv run server.py                  # webhook receiver on 0.0.0.0:8080
curl localhost:8080/health        # buffers warm? which host/cameras?
curl -X POST localhost:8080/motion -H 'content-type: application/json' \
     -d '{"camera": "outside"}'   # fake an event
uv run capture.py 60 0 outside    # sample a frame/minute forever, feed the bg model
uv run calibrate.py               # re-run the IR calibration on samples/videos
uv run main.py                    # one-off snapshot from each camera
```

Bootstrap: the first event per camera at a given resolution has no background
model and returns `no_background`; the model seeds itself and later events work.
Running `capture.py` for a while first avoids that.

For anything long-running use a systemd unit or `tmux`; the RTSP handles are
reconnected by the recorder threads on failure.

## 6. Setting up Home Assistant

HA already exists (Pi at `192.168.1.133`, at Dima's). To recreate or extend it:

1. **Install** Home Assistant OS on the Pi (Raspberry Pi Imager → Other
   specific-purpose OS → Home assistants → Home Assistant) or run the container
   image `ghcr.io/home-assistant/home-assistant:stable`. Onboard at
   `http://<pi>:8123`.
2. **UniFi Protect integration**: Settings → Devices & services → Add →
   *UniFi Protect*. Point it at the UDM (`192.168.1.1`, port 443) using a
   **local** Protect user (create one in Protect → Users, role *Viewer* is
   enough for events; *Admin* if you also want HA to control cameras). It
   creates `binary_sensor.<camera>_motion` (and object-detection sensors) plus
   `camera.<camera>_high` entities.
3. **RESTful command** to reach the detector — in `configuration.yaml`:

   ```yaml
   rest_command:
     cat_detector:
       url: "http://192.168.7.4:8080/motion"
       method: POST
       content_type: "application/json"
       payload: '{"camera": "{{ camera }}"}'
       timeout: 15
   ```

   (Over the tunnel `192.168.7.4` is Dave's peer; on Dima's LAN it is
   whatever box runs `server.py`. Verified: HA can reach the tunnel address
   without any UDM firewall rule.)
4. **Automation** (Settings → Automations → new, YAML mode):

   ```yaml
   alias: Cat door – score motion
   mode: queued
   trigger:
     - platform: state
       entity_id: binary_sensor.cat_door_outside_motion
       to: "on"
       variables: {camera: outside}
     - platform: state
       entity_id: binary_sensor.cat_door_inside_motion
       to: "on"
       variables: {camera: inside}
   action:
     - service: rest_command.cat_detector
       data: {camera: "{{ camera }}"}
       response_variable: verdict
     - condition: template
       value_template: "{{ verdict.content.verdict == 'orange_cat' }}"
     - service: notify.cat_watchers        # see §7
       data:
         title: "Orange cat at the {{ camera }} door"
         message: >
           {{ (verdict.content.confidence * 100) | round }}% of
           {{ verdict.content.frames }} frames — {{ verdict.content.reasoning }}
   ```

   Adjust entity ids to whatever the integration named the cameras (Developer
   tools → States, filter `motion`). Sending JSON is preferred over the older
   multipart-snapshot automation (`camera.snapshot` → `rest_command` with the
   file) — the snapshot is only ~30 KB and one frame.
5. **Restart HA** (or *Developer tools → YAML → Reload* for rest_command /
   automations) and fire a test with *Run actions* on the automation.

Debugging: HA → Settings → System → Logs for `rest_command` errors;
`frames/events.csv` and the server's stdout on the detector side.

## 7. Notifying several people instead of one

Today the automation calls a single `notify.mobile_app_<phone>` service. Two
ways to fan out:

**A. Notify group (recommended)** — one service name, many targets, edit in one
place. `configuration.yaml`:

```yaml
notify:
  - name: cat_watchers
    platform: group
    services:
      - service: mobile_app_dimas_phone       # HA companion app
      - service: mobile_app_daves_phone
      - service: telegram                     # any other notify.* platform
        data:
          target: 123456789
```

Then call `notify.cat_watchers` everywhere. Each entry is the part after
`notify.` of an existing service. Reload with *Developer tools → YAML →
Notify* (or restart).

**B. Parallel actions in the automation** — no config change, but repeated in
every automation:

```yaml
action:
  - parallel:
      - service: notify.mobile_app_dimas_phone
        data: {message: "Orange cat!"}
      - service: notify.mobile_app_daves_phone
        data: {message: "Orange cat!"}
```

Prerequisites per target: the HA Companion app installed and logged in on each
phone (that is what creates `notify.mobile_app_<device_name>`); for external
people, an integration such as Telegram, Pushover, or SMTP. Attaching the
event image is possible if HA can fetch it — e.g. serve `frames/events/` via
`python -m http.server` and pass `data: {image: "http://…/outside-<ts>.jpg"}`.

## 8. Resources to download

| Resource | Where | Notes |
|---|---|---|
| Labelled sample clips (`samples/videos/*.mp4`, `samples/sheets/*.jpg`) | Exported from UniFi Protect at Dima's (Protect → Timeline → Export) or the shared `cat-videos.zip` (~43 MB, ask Dave/Dima) | unzip into `samples/`; both paths are gitignored. Ground-truth labels are hard-coded in `calibrate.py` (`LABELS`, keyed by clip start time). |
| Background models `frames/bg_<camera>.npy` | generated locally by `capture.py` / `server.py` | not shared; regenerate on each host and resolution |
| `ffmpeg` | `apt install ffmpeg` / <https://ffmpeg.org/download.html> | needed for `segments` mode |
| `uv` | <https://docs.astral.sh/uv/> | Python + deps |
| WireGuard client config | exported from Dima's UDM (Network → VPN → WireGuard → client) | see §4 for the two edits it always needs; lives at `/etc/wireguard/wg0.conf` |
| Protect RTSP stream keys | UDM Protect UI, per camera | go in `.env` |
| Home Assistant | <https://www.home-assistant.io/installation/> | §6 |

## 9. Repository map

| File | Purpose |
|---|---|
| `server.py` | FastAPI webhook receiver; orchestrates buffers, scoring, voting, logging |
| `detect.py` | ROI, background model, gain normalisation, motion mask, IR features, `classify`, `vote` |
| `capture.py` | RTSP helpers (`grab`, `grab_burst`), `measure`, stand-alone sampler that trains the background |
| `segments.py` | ffmpeg remux ring buffer in `/dev/shm/ocp` (default) |
| `streamer.py` | decoded in-memory ring buffer (`BUFFER_MODE=decoded`) |
| `calibrate.py` | IR discriminator calibration against `samples/videos` |
| `main.py` | minimal "grab one frame from each camera" sanity check |
| `frames/` (gitignored) | `events/`, `events.csv`, `stats.csv`, `bg_*.npy`, sampled frames |
| `samples/`, `cat-videos.zip` (gitignored) | labelled clips and contact sheets |

Conventions: plain scripts, run with `uv run`; comments explain *why* a number
is what it is, with the measurement that produced it — keep doing that when you
change a threshold.
