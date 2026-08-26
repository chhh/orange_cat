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
| A possum | grey-brown | also visits; must not be flagged (scores 0.00 %) |

That colour split is the whole basis of the classifier:

- **Daylight (colour mode)**: fraction of *moving* pixels that are warm and
  saturated (`warm_pct`, threshold 8 %) **and** `warm_margin` — the same
  measurement on the animal minus the same measurement on the ring of scene
  immediately around it, threshold 5 pp. Both must pass. `warm_pct` alone
  cannot work: at sunrise the light reddens a black-and-white cat to 10–23 %,
  which overlaps the intruder's own range (25.7 % at its lowest). Against the
  local surround the two separate cleanly — intruders 8.3–48.9 pp, every
  resident and the possum ≤ 2.5 pp.
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

Lighting note, now measured rather than assumed (42 samples, 21:09 → 06:49 on
2026-08-16/17): the **outside camera never drops to IR**. `rgb_spread` held
9.3–10.5 all night, never near the cutoff of 8, because of the patio light. So:

- **Outside camera → colour path only.** `rel_bright` separates *nothing* there
  (orange 0.72–0.77 vs residents 0.34–2.13, fully overlapping). Do not apply
  the IR thresholds to it.
- **Inside camera → IR path only.** It sits in IR even at 13:30, because the
  room has no daylight.

### Validated on labelled outside-camera clips (2026-08-17)

Dima exported and hand-sorted a night's clips into `our-cats/`,
`orange-intruder-cat/` and `possum/` (see §8). Scoring `warm_pct` over the
motion mask:

| | n | warm % |
|---|---|---|
| orange intruder | 4 | **22.1 – 99.0** |
| possum | 1 | **0.00** |
| our cats, night and full daylight | 21 | **0.00 – 3.6** |
| our cats, ~20 min dawn transition | 3 | 6.9, 32.7, **88.9** ← false positives |

The 8 % threshold separates these cleanly outside the dawn window. This is the
first validation on data the thresholds were *not* fitted to.

**Superseded on 2026-08-17** — `uv run evaluate.py` replays all 30 clips
through the live code and now scores **30/30, no misses and no false alarms**
(it was 28/30, the two dawn clips being the failures). See §9.

**First live night, 2026-08-17/18.** 44 overnight events, **no false
positives**. Dima confirms the stray did not visit, so this validates
*precision only* — the new code has still never seen the intruder, and recall
is untested live. Two results worth keeping:

- The dawn false positive reproduced and was **correctly rejected**: 05:56–06:10
  scored `warm` 48–61 % with a surround margin of 27–47 pp on frames containing
  no animal (verified by eye — sunrise on the tan planter). `warm_margin` sailed
  straight past it; **only `bg_corr` caught it**. The two dawn failure modes are
  different mechanisms and need different features — see the comment on
  `WARM_MARGIN_THRESHOLD`.
- For 8.5 hours the detector reported *zero* motion pixels, which looks broken
  and is not. Frame-wide 5 000–10 000 px were changing while **0–3** fell inside
  the ROI: wind in the bamboo and the doorway, both outside the crop. Protect
  triggers on them; the ROI correctly ignores them. Before debugging a silent
  night, check where the changed pixels are, not how many.

A subtlety that cost time: the ginger test needs `S > 90`, and the outside
scene's *background* saturation ceiling at night is only 60–70 — which looks
fatal but is not. The cat itself reaches `satP99 = 136`. Measure saturation on
the animal, not the scene.

## 3. Timings and costs measured so far

| What | Measured |
|---|---|
| RTSP connection handshake (cv2 → UniFi Protect) | ~3.1 s |
| Decoding a buffered ~2 s segment on demand | 0.146 s |
| Per-event latency, `BUFFER_MODE=off` (dial out per event) | ~4.0 s, 0 % idle CPU, ~96 MB, no pre-trigger frames |
| Per-event latency, `BUFFER_MODE=decoded` (frames in RAM) | 0.25 s, **24 % idle CPU**, ~295 MB, ~2.7 s history |
| Per-event latency, `BUFFER_MODE=segments` (ffmpeg remux, default) | **0.28–0.52 s** measured over the tunnel, ~1 % idle CPU, ~250 MB total incl. 2 ffmpeg, ~10–12 s history |
| RTSP-over-VPN stability | recorders restarted **33–40 times** overnight; the supervisor recovers each time, but the buffer has gaps |
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
- HA currently POSTs to **both** `192.168.1.224` (Dave on Dima's LAN) and
  `192.168.7.4` (Dave over the tunnel). Exactly one works at a time depending
  on where Dave is, and that is deliberate — a single hostname cannot cover
  both. Use short curl timeouts so the dead one does not stall the automation.
  The real fix is moving the server onto a box at Dima's (§5).

### Detection gotchas (found the hard way)

- **The background model can deadlock.** It refuses to learn from a busy scene
  so a loitering cat is not absorbed — but a *stale* model makes every frame
  look busy, so it never updates, so it stays stale forever. It ran a whole
  night with a daylight model, flagging 23 % of every frame as motion and
  making all 19 real events uninformative. Everything looks healthy while this
  happens. Fixed by relearning after `BG_STALE_AFTER` consecutive busy checks;
  if verdicts look strange, check `motion_px` for a suspiciously constant value.
- **`detect.ROI`: widen in `y`, never in `x`.** `y > 0.45` → `y > 0.30` is
  free and recovers animals further from the lens. Removing the `x < 0.63`
  edge looks equally justified — it was picked to exclude the 24 %-warm gate
  back when whole frames were scored, and only motion pixels are scored now —
  and it is **wrong**. The strip `x = 0.63…0.80` is the open doorway: sunlit
  yard, a gate that swings, and the path people walk. Replaying a day of live
  events took false ginger verdicts from **1 to 11** when the ROI opened to
  `x < 0.80`, one of them a person in brown boots. The labelled clips score
  30/30 at *every* ROI tried, so they cannot see this — it is a live-log
  result only, and the earlier "lost nothing" note came from the clips alone.
- **Moving sunlight reads as a ginger cat — and it is not only a dawn
  problem.** On 2026-08-17 the live detector returned `orange_cat` **twelve
  times between 11:49 and 13:05** on a patio with no animal on it, at 0.73–1.0
  confidence. Two causes, both now fixed:
  1. The isolation window (`ISO_MIN`/`ISO_MAX`) was applied on the IR path
     only, so in colour mode a 1 500 px scrap of sunlight was scored as if it
     were an animal. It now gates both paths.
  2. The old ROI's own right edge sliced through the sunlit doorway,
     manufacturing exactly such a scrap — eleven of the twelve blobs sat at
     `x = 0.60…0.63`, pinned against the crop boundary. If verdicts look
     strange, plot where the blob is; a blob that hugs an ROI edge is an
     artefact of the crop, not a thing in the world.
- **`bg_corr` answers "is it an object at all?"** Sunlight does not replace
  the scene, it rescales it, so frame and background stay correlated across
  the moving pixels (measured 0.84–0.98). An animal occludes the scene and the
  correlation collapses (−0.03…0.35 across all 30 labelled clips). Threshold
  0.70; above it the frame abstains. This is *not* another of the failed
  contrast features — those measured the animal against itself, which
  normalises the signal away; this measures it against the scene behind it.
  Expect it to fire for a few minutes after a restart, when the model is
  genuinely stale — that is correct behaviour, and safer than the old failure,
  which was to score confidently against a stale model.
- **A small COCO detector (NanoDet) is poor on IR close-ups** — 0–13 % hit rate
  on the indoor night clips, but **87 %** on the one clip with a well-framed
  cat at a distance. It fails on exactly the frames background subtraction also
  fails on, so it does not rescue them. It is a plausible fit for the *dawn*
  problem above, where the scene is daylit and the animal distant. Note
  `ultralytics` pulls ~2 GB of CUDA wheels; prefer an ONNX export under
  `cv2.dnn`, and note OpenCV 5 needs `cv2.dnn.ENGINE_CLASSIC` plus explicit
  output names for older models.

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
./run-server.sh start             # detached; survives closing the terminal
./run-server.sh status            # health + buffer state
./run-server.sh stop              # server + its ffmpeg recorders
uv run server.py                  # foreground, for debugging
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

For anything long-running use `./run-server.sh start`, which `setsid`s the
process into its own session so it outlives the terminal or agent session that
launched it (logs to `frames/server.log`).
The RTSP handles are reconnected by the recorder threads on failure.

**On odd-fellow this is no longer how the server is started.** Since
2026-08-25 it runs as `ocp-detector.service` (`Restart=always`, survives
reboot, appends to the same `frames/server.log`). Use
`systemctl start|stop|restart ocp-detector` there; `./run-server.sh start`
would add a second server competing for port 8080 and the same segment
buffers. `./run-server.sh status` is still fine -- it only reads `/health`.

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

## 6b. Notification classes (added 2026-08-19 at Dima's request)

`/motion` returns a `class` field so Home Assistant does not need to know our
verdict vocabulary or which verdicts are decisive:

| class | headline | meaning |
|---|---|---|
| `intruder` | Orange cat at the door | `orange_cat` at ≥ 0.6 confidence |
| `not_orange` | An animal, not the orange cat | `no_orange` at ≥ 0.6 confidence |
| `unsure` | An animal, but could not tell which | something was there; frames disagreed or could not be scored |
| `none` | Nothing seen | no animal detected in any frame |

**`not_orange` is not "a resident".** Nothing here identifies Dima's cats
positively — the class also covers the possum and the skunks. Do not word a
notification as if it did.

The `unsure` class earns its place immediately: the 04:47:37 raid, a 1–1 split
on two detections, reports `unsure` instead of a confidently wrong
`not_orange`.

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

### Detector-first front end (2026-08-19) — current design

The motion-mask front end was replaced. It failed on three of three confirmed
raids, and its failures were structural rather than tuning: it needs a
background model that tracks the light, an ROI narrow enough to exclude the
gate, and an animal big enough in frame. `animal.py` runs a YOLOv8n ONNX
export under `cv2.dnn` and needs none of those.

Measured over 5 labelled intruder clips, 24 resident clips, the possum, 3
confirmed live raids, and the 14 known daytime false positives:

| | old motion path | detector path |
|---|---|---|
| labelled clips | 30/30 | 30/30 |
| confirmed live raids | **0/3** | **2/3** |
| sunlight false positives | 12 fired | **0 detected** |
| person false positives | 2 fired | **0** (person class excluded) |

Colour is scored inside the box at `SAT_MIN_BOX = 40`, not the mask's 90 — a
box is nearly all animal where a mask was half wall, so the floor can be low
enough to survive dim light at range. The discriminator is the **margin** over
the surrounding band, not absolute warmth: intruder 30.1–54.7 pp against every
resident ≤ 14.7 pp, while warm % alone overlaps (a resident at sunrise hit
30.4 % against the intruder's 30.7 % floor).

Two things to know before touching it:

- **A frame with no detection ABSTAINS, it does not vote "not orange".** The
  detector fired on as few as 1 of 15 frames on a real raid, so counting
  no-detection frames as absence would drown every true positive.
- **Species labels are worthless** — one burst returned cat, then dog, then
  cow for the same animal. The box is the signal; colour does the ID.

The remaining miss is 04:47:37, a 1–1 split on only 2 detections. It is the
same visit as 04:57:11, which fires at confidence 1.00, so the raid is still
caught. Not worth tuning to a single event.

The model is gitignored. Regenerate with:
`pip install ultralytics && yolo export model=yolov8n.pt format=onnx imgsz=640 opset=12`
then put `yolov8n.onnx` in `models/`. Validate with `uv run evaluate_detector.py`.
Set `USE_DETECTOR=0` to fall back to the motion path.

### Night of 2026-08-18/19 — the intruder was seen and missed

Plumbing was clean all night (buffers fresh, 0 recorder restarts, background
current, no stale-buffer fallbacks), so the 2026-08-17 buffer fix held. 16
events, no false positives, and **the orange tabby appeared at 04:57:11 and
was logged `no_animal`, motion_px 0.**

Replaying its 15 saved burst frames shows **three** gates, each of which
blocks it on its own — so fixing any one of them changes nothing:

| Gate | Value | Threshold |
|---|---|---|
| ROI `x < 0.63` | cat at x≈0.72–0.83 | cropped out entirely |
| `ISO_MIN` | `iso = 0.0103` | 0.015 → 11/15 frames `unmeasurable` |
| colour test | `warm_pct` 3.9 %, margin 3.47 pp | 8 % / 5 pp → votes `no_orange` 7–3 |

The third is the deep one and it is new information: **at night and at
distance the ginger signal itself collapses.** The `S > 90` term in
`warm_mask` is what fails — dim light at range desaturates the fur. §2's note
that "the cat itself reaches satP99 = 136" was measured on a *near* animal
under the patio light; it does not hold across the patio. Any fix has to
address all three together, and the colour threshold cannot simply be lowered
without re-running §9 against the false positives it was raised to stop.

## 9. Validating a threshold change (`evaluate.py`)

`uv run evaluate.py` replays Dima's 30 hand-sorted clips through the *live*
functions — `motion_mask`, `measure`, `classify`, `vote` — and prints a
per-clip verdict plus a confusion summary. Run it before and after touching
anything in `detect.py`. Current state: **30/30, no misses, no false alarms**.

```
uv run evaluate.py                              # the current thresholds
uv run evaluate.py --set WARM_PCT_THRESHOLD=8   # override one constant
uv run evaluate.py --roi 0,0.45,0.63,1          # try a different crop
```

Use `--set` for **ablations**, one change switched off at a time. A change set
that scores 30/30 tells you nothing about which edit earned it; on 2026-08-17
ablation showed that of four changes only `warm_margin` moved the clip score
at all, and that the ROI change was silently *costing* precision in a way the
clips could not show.

Two traps, both of which produced confidently wrong numbers before being
caught:

- **Never use a clip's own median as its background.** A 10 s export of a cat
  near the door is a cat in most frames, so the median contains the cat, and
  the animal then measures at a fraction of its true size — a confirmed
  intruder scored `iso 0.003`, indistinguishable from a sunlit scrap. The
  harness pools backgrounds from neighbouring clips instead
  (`pooled_backgrounds`). This one artefact changed clip verdicts and
  confidences across the board (one clip went 0.71 → 1.00).
- **The clips cannot see false positives that happen when no animal is
  present.** They were exported *around* animals, so sunlight-on-empty-patio
  and people-walking-past do not appear in them at all. That failure mode
  lives only in `frames/events.csv` and `frames/events/*.jpg`. To test against
  it, rebuild each event's background from the median of neighbouring events
  in time (±40 min) — scoring a midday frame against tonight's model reports
  blobs ten times too big and is worthless.

Known remaining weakness: **a person wearing red or orange inside the ROI can
score `orange_cat`.** There is no notion of shape or species anywhere in the
pipeline. It needs a burst of frames to agree, so it is not frequent, but it
is the most likely false positive now that the sunlight ones are gone, and it
is the thing to fix before any deterrent is armed.
