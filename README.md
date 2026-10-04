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
| Motion push | Automation **"Cat door outside motion -> detector"** must call `shell_command.cat_motion_rpi`, and that command must POST to the Pi: `curl -s --max-time 5 -X POST http://192.168.1.142:8080/motion`. The older `cat_motion_dave_vpn` action (odd-fellow's tunnel address) should be disabled, or it stalls every event once that laptop is off. |
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
| `~/ocp.git` | bare repository that receives `git push pi` from odd-fellow |
| `~/ocp-replay/` | clips from the 2026-10-03 validation; safe to delete |

Dima's older copy in `/home/chhh/orange-cat` must stay stopped: it has no
person gate and would compete for port 8080 and the same speaker.

### Maintenance

Is it alive?

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

The arg is a filename that already lives in `config/www/sounds/`.

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