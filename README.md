# orange-cat

## Running on the Raspberry Pi (`rpi-vpn`, 192.168.1.142)

Since 2026-10-03 the detector, patrol and deterrent are installed on Dima's
Raspberry Pi 5 (16 GB, Debian 13, 64-bit) instead of Dave's laptop
`odd-fellow`. What the system does and why is in `AGENTS.md`; what is armed
right now is in `watch/LIVE-STATE.md`. This section is the operating manual.

### What Home Assistant has to provide

The Pi talks to HA at `192.168.1.133:8123` with the long-lived token in
`.env`. HA needs:

| What | Detail |
|---|---|
| Motion push | Automation **"Cat door outside motion -> detector"** must call `shell_command.cat_motion_rpi`, and that command must POST to the Pi: `curl -s -X POST http://192.168.1.142:8080/motion` (no body needed -- the camera defaults to `outside`, and the detector pulls its own frames). Optionally add `--connect-timeout 5` so a powered-down Pi cannot hang the automation for 60 s; do **not** use `--max-time` below ~20, the Pi takes about 9 s to answer. The older `cat_motion_dave_vpn` action (odd-fellow) stays **disabled**. Set up this way 2026-10-03 and verified end to end with a leg in front of the camera (20:04). |
| Outside speaker | `media_player.nursery_speaker_2` (the cat-door camera outside). |
| Inside speaker | `media_player.garage_speaker` (the cat-door camera inside). **Never** `media_player.nursery_speaker` without the `_2` -- that is a Nest Mini in a bedroom. |
| Water valve | `switch.cat_sprayer` (Zigbee). |
| Valve failsafe | `automation.cat_sprayer_failsafe_off` must stay **enabled**: it closes the valve 10 s after any open, and is the only cutoff that does not depend on the Pi. |
| Staged sounds | The sound files under `config/www/ocp/ha-staging/`, served at `http://192.168.1.133:8123/local/ocp/ha-staging/<file>`. |
| Reach the Pi on 8081 | Two clips (`siren_open.wav`, `Poshel-Otsuda.wav`) are not staged; HA fetches them from `http://192.168.1.142:8081/`. |

The patrol pulls its own video from the cameras (`rtsp://192.168.7.1:7447`),
so the deterrent keeps working if the motion push is broken. Without the push
only the detector's event log (`frames/events.csv`) goes quiet.

Two things on the network side, not in HA: a **DHCP reservation** for the Pi
at `192.168.1.142` (both the HA command above and the sound URLs depend on
that address), and no HA updates or restarts between 22:00 and 06:00 -- every
speaker and the valve go through HA, so the deterrent is inert while it is down.

### Where things are on the Pi

Everything runs as the unprivileged user `david` (key-only login, no sudo,
"linger" enabled so its services start at boot). From odd-fellow:
`ssh rpi-ocp`. Nothing is installed system-wide and nothing lives in Dima's
`chhh` home.

| Path | What |
|---|---|
| `~/projects/ocp/` | this repository (branch `roi-background-detection`) and its `.venv` |
| `~/projects/ocp/.env` | camera stream keys, HA token, `DETER_ARM`, `DETER_WATER`. Mode 600, not in git. |
| `~/projects/ocp/models/yolov8n.onnx` | detector weights, not in git (`AGENTS.md` says how to regenerate) |
| `~/projects/ocp/frames/` | `server.log`, `events.csv`, `events/` (saved frames), background models |
| `~/ocp-watch/` | `patrol.log`, `soundserver.log`, `reactions/` (video of each fire), `person-evidence/`, and symlinks to `patrol.py`, `deter.py` and the scripts in `watch/` |
| `~/.config/systemd/user/` | `ocp-detector.service` (port 8080) and `ocp-soundserver.service` (port 8081); sources in `watch/pi/` |
| `crontab -l` | starts the patrol at boot and every 5 minutes if it is not running; night marker 21:00; report 06:50; prune 07:10. Source: `watch/pi/crontab.txt` |
| `/dev/shm/ocp/` | rolling ~10 s of video per camera, in RAM (never touches the SD card) |
| `~/.local/bin/ocpctl` | symlink to `watch/pi/ocpctl` -- start / stop / status |
| `~/ocp.git` | bare repository that receives `git push pi` from odd-fellow |
| `~/ocp-replay/` | clips from the 2026-10-03 validation; safe to delete |

A second checkout of this repository lives in `/home/chhh/orange-cat` (Dima's
account) and must stay stopped: only one detector may own port 8080 and the
same speaker. It is a working copy, not the deployed one -- the deployed copy
is `/home/david/projects/ocp`.

### Start, stop, status: `ocpctl`

The whole stack -- detector, sound server and patrol -- is called **OCP** and
is controlled with one command. From Dima's own account (`chhh`), prefix it
with `sudo -iu david`:

```bash
sudo -iu david ocpctl status    # what is running, armed flags, cameras, last log line
sudo -iu david ocpctl stop      # stop everything; nothing can fire; stays stopped
sudo -iu david ocpctl start     # start again (the patrol follows within ~5 min)
sudo -iu david ocpctl restart
```

`ocpctl` is not a service: it is a short bash script in the repo
(`watch/pi/ocpctl`), made available as a command by a symlink at
`/home/david/.local/bin/ocpctl` (that directory is on david's `PATH`). It
runs only when someone types it, and simply calls `systemctl --user`, `pkill`
and `curl` for you. To remove just the command: `rm ~/.local/bin/ocpctl` as
david; nothing else depends on it.

`stop` survives cron: it leaves `~/ocp-watch/.stopped`, which
`start-patrol.sh` obeys, until `start` removes it. It does **not** survive a
reboot for the two services, which come back on boot by design -- but the
patrol, the only piece that fires, stays down while the marker exists.

### Stop completely, and start again by hand

Use this when the stack must not come back at all -- for example while the
camera that drives the detections is switched off, before maintenance, or
while another host is armed. It is `ocpctl stop` plus two extra steps: the
units are **disabled** (so a reboot does not start them) and the patrol is
**removed from cron** (so nothing resurrects it).

The script does all of it and backs up the crontab first:

```bash
# from a checkout, as chhh (it needs root)
sudo watch/pi/disable-ocp.sh            # stop + disable; prints what it changed
sudo watch/pi/disable-ocp.sh restore    # put crontab and units back
```

By hand, the same full stop:

```bash
# 1. take the patrol out of cron (keep a copy first; file-based so sudo
#    still has the terminal for its password prompt)
sudo -u david crontab -l > /tmp/david-crontab.bak
grep -v 'ocp-watch/start-patrol.sh' /tmp/david-crontab.bak > /tmp/david-crontab.new
sudo -u david crontab /tmp/david-crontab.new

# 2. kill the running patrol
sudo pkill -u david -f 'ocp-watch/patrol\.py'

# 3. stop both units and stop them starting at boot
sudo -u david env XDG_RUNTIME_DIR=/run/user/$(id -u david) \
  systemctl --user disable --now ocp-detector ocp-soundserver

# 4. confirm: no processes, both ports closed
ps -u david -o pid,cmd | grep -E 'server\.py|patrol\.py|rtsp_transport'
curl -s -m 3 localhost:8080/health || echo 'detector down'
curl -s -m 3 -o /dev/null localhost:8081 || echo 'sound server down'
```

By hand, the way back. The patrol is deliberately *not* started here: it
comes from cron within five minutes, and a copy started from a login or agent
session dies with that session.

```bash
# 1. clear the marker ocpctl stop leaves behind (harmless if absent)
sudo -u david rm -f /home/david/ocp-watch/.stopped

# 2. restore the crontab -- either the backup made above, or the canonical one
sudo -u david crontab /home/david/projects/ocp/watch/pi/crontab.txt

# 3. enable the units and start them now
sudo -u david env XDG_RUNTIME_DIR=/run/user/$(id -u david) \
  systemctl --user enable --now ocp-detector ocp-soundserver

# 4. wait for cron, then check everything
sudo -iu david ocpctl status      # patrol should appear within ~5 min
```

Watch out for two things: `.stopped` overrides a restored crontab, so a
`crontab` install alone will not bring the patrol back while it exists; and a
disabled unit stays disabled across reboots until step 3 above is run --
`ocpctl start` only starts it for the current boot.

### Maintenance

Is it alive? `ocpctl status`, or by hand:

```bash
systemctl --user status ocp-detector ocp-soundserver
curl -s localhost:8080/health          # both cameras: alive, segments fresh
tail -n 5 ~/ocp-watch/patrol.log       # a heartbeat line every 30 minutes
grep -E '^DETER_(ARM|WATER)=' ~/projects/ocp/.env
```

Arm or disarm. **Exactly one machine may be armed.** If odd-fellow is ever
switched back on with `DETER_ARM=1`, two hosts fire at the same cat.

```bash
# edit DETER_ARM / DETER_WATER in ~/projects/ocp/.env (1 = live, 0 = dry run), then:
systemctl --user restart ocp-detector
pkill -f '[p]atrol.py'                 # cron restarts it within 5 minutes
```

`start-patrol.sh` sets `DETER_ARM=1` and then reads `.env`, so a missing
`DETER_ARM` line means **armed**. To disarm, set it to `0`; do not delete it.
Never start the patrol by hand from a login or agent session -- it dies with
the session. Kill it and let cron bring it back. To stop a firing sequence
already in progress: `touch ~/ocp-watch/.deter-abort`.

Update the code (from odd-fellow, then on the Pi):

```bash
git push pi roi-background-detection
ssh rpi-ocp 'cd ~/projects/ocp && git pull && ~/.local/bin/uv sync --frozen \
  && systemctl --user restart ocp-detector && pkill -f "[p]atrol.py"'
```

Routine care:

- **Disk.** The Pi runs from a 32 GB SD card. `frames/events/` is pruned
  after 14 days by cron; `patrol.log`, `server.log` and `reactions/` are not
  rotated. Check `df -h /` and `du -sh ~/ocp-watch ~/projects/ocp/frames`
  about once a month.
- **Reboot or power cut.** The two services and the patrol are meant to come
  back by themselves. This has not yet been tested with a real reboot -- after
  the first one, run the four "is it alive" commands above.
- **Address change.** Each process works out the Pi's own address once, at
  start. If the Pi's IP ever changes, restart the detector and the patrol, or
  the two clips served from port 8081 silently stop playing.
- **After any HA restart or update**, check that `switch.cat_sprayer` is not a
  stale "restored" entry: the valve was dead for two nights in September while
  HA reported it as `off`.
- **Heat and noise.** The patrol runs the detector about three times a second
  from 21:45 to 06:00, so the fan runs then. Measured 62-65 C under a harder
  load than that, with no throttling.
- **Clock.** The firing window is 22:00-06:00 local time, so the Pi needs the
  right timezone and working NTP (`timedatectl`).

To remove it all: `crontab -r`, `systemctl --user disable --now ocp-detector
ocp-soundserver`, and then, as Dima,
`sudo loginctl disable-linger david && sudo userdel -r david`.

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

## Deterrent: what fires, and when

`server.py` no longer decides to fire anything itself. The `/motion` webhook
and the patrol (`patrol.py`) both hand a fresh burst to
`deter.consider_and_escalate()`, which re-scores it and fires only when
several frames agree on an orange cat at the flap and **no person** appears
anywhere in the burst. A cat whose visit started at the flap (an exit) is
never fired at for the rest of that visit, and a shared cooldown file stops
the server and the patrol double-firing at the same animal. Escalation, the
valve sequence and the reaction clip (`~/ocp-watch/reactions/`) also live in
`deter.py`.

What is allowed to fire is gated in `.env`, not in `cat-deterrent.toml`:

| Variable | Meaning |
|---|---|
| `DETER_ARM=1` | sound may play. `start-patrol.sh` exports `DETER_ARM=1` *before* sourcing `.env`, so an explicit `DETER_ARM=0` is what keeps a host disarmed; never delete the line. |
| `DETER_WATER=1` | `switch.cat_sprayer` may be pulsed (`DETER_WATER_ENTITY` overrides the entity). |
| `DETER_SOUNDS` | comma-separated escalation ladder, default `DRILL_boost3.wav,dog_bark_big.wav,dog_growl.wav,dog_bark.wav,catsfight.mp3`. |
| `DETER_HOUR_FROM` / `DETER_HOUR_TO` | hours in which sound is allowed at all, default `22` and `6`. |

**Exactly one machine may be armed.** How to arm and disarm: `watch/REARM.md`;
what is armed right now: `watch/LIVE-STATE.md`.

`[sound]` in `cat-deterrent.toml` only supplies the repeat timings now; the
`sounds` key there is legacy, the live ladder is `DETER_SOUNDS`:

```toml
[sound]
min_interval = 2     # seconds between sounds while the target stays in view
max_interval = 3
max_duration = 30    # hard stop for the escalation loop
```

`watch/LIVE-STATE.md` records what was measured in the field; that is the
place to look before changing any of these numbers.

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