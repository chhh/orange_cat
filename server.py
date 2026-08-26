"""Webhook receiver for Home Assistant motion events.

Home Assistant relays UniFi Protect motion detections here as a POST. Two
shapes are accepted:

  * JSON naming a camera  -- PREFERRED. We then pull our own burst of frames
                             straight off RTSP: full resolution, and enough of
                             them to vote. Costs a second or two of latency.
  * multipart/form-data with an "image" -- one frame, whatever resolution HA
                             chose to send. Lower latency, much weaker signal.

The burst matters. Per-frame confidence on the black-and-white cats is only
57-60%, so a single image is close to a coin flip; the clip-level accuracy
measured during calibration came from many frames voting. HA's snapshots also
arrived at ~30KB against ~243KB for a direct RTSP grab.

Either shape may name a camera ("inside" / "outside"); default is outside.

Scoring happens on the pixels that changed against a background model, inside
the camera's region of interest -- see detect.py for why the whole frame is
useless here. Frames come from whichever buffer BUFFER_MODE selects; see the
comment on that constant below, and segments.py for the default.

Run:  uv run server.py            (listens on 0.0.0.0:8080)
"""

import csv
import os
import random
import threading
import time
from collections import deque
from datetime import datetime

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, UploadFile
import uvicorn

import detect
import segments
import streamer
from capture import CAMERAS, grab, grab_burst, measure, stream_url
from talk import play
import config

EVENT_DIR = "frames/events"
CSV_PATH = "frames/events.csv"

# In-memory tail of motion receipts: what arrived and how it was processed.
RECENT_EVENTS = deque(maxlen=20)

load_dotenv()
app = FastAPI()


FIELDS = ["ts", "camera", "file", "verdict", "confidence", "brightness",
          "saturation", "rgb_spread", "is_ir", "warm_pct", "luma_std",
          "px", "motion_px", "motion_frac", "iso_frac", "usable",
          "roi_px", "cx", "cy", "bbox_w", "bbox_h", "bbox_bottom",
          "rel_bright", "cv", "frames", "votes", "width",
          "height", "source", "reasoning"]


def record(row):
    """Append an event, keeping the header honest about the columns.

    Appending wider rows under an older header produced a file that could not
    be parsed by its own header -- 11 columns declared, 25 written. When the
    schema changes, the old file is set aside rather than silently corrupted.
    """
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)

    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="") as fh:
            existing = next(csv.reader(fh), [])
        if existing and existing != FIELDS:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            archived = f"{CSV_PATH}.{stamp}.bak"
            os.rename(CSV_PATH, archived)
            print(f"  events.csv schema changed ({len(existing)} -> "
                  f"{len(FIELDS)} columns); previous file kept as {archived}",
                  flush=True)

    new_file = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


BURST = 15

# How frames are on hand when a notification arrives. Measured on two cameras:
#
#   segments  0% idle CPU, ~50MB, ~1.0s per event, ~10s of history  (default)
#             ffmpeg remuxes RTSP to a rolling window without ever decoding;
#             we decode only the seconds that matter, when asked.
#   decoded  24% idle CPU, 295MB, 0.25s per event, ~2.7s of history
#             frames kept decoded in memory. Fastest, but continuously
#             decompresses video nobody looks at.
#   off       0% idle CPU,  96MB, ~4.0s per event, no pre-trigger frames
#             dial out on each event and pay the 3.1s RTSP handshake.
BUFFER_MODE = config.BUFFER_MODE

# --- Sound playback config (cat-deterrent.toml) ---------------------------
# Comma-separated list; one is picked at random per trigger. Each must exist
# in HA's config/www/sounds/.
ORANGE_SOUNDS = config.ORANGE_SOUNDS

# Position gate: only fire once the animal's bbox bottom is at least this
# fraction of frame height (1.0 = touching the bottom edge, at the door).
# 0 disables the gate entirely.
NEAR_DOOR_MIN_BOTTOM = config.NEAR_DOOR_MIN_BOTTOM

# Bypass: fire the sound on ANY motion event with frames, whatever the
# verdict -- for testing with your own feet.
SOUND_ON_ANY_MOTION = config.SOUND_ON_ANY_MOTION

# While the target stays in view, keep firing a random sound every
# random.uniform(min, max) seconds instead of once per motion event.
SOUND_MIN_INTERVAL = config.SOUND_MIN_INTERVAL
SOUND_MAX_INTERVAL = config.SOUND_MAX_INTERVAL
SOUND_MAX_DURATION = config.SOUND_MAX_DURATION

_deterring = {}  # camera -> threading.Event; set when its deterrent loop stops


def _play_sound(sound):
    t = time.time()
    try:
        play(sound)
        print(f"  play_media roundtrip {1000 * (time.time() - t):.0f} ms "
              f"({sound})", flush=True)
    except Exception as exc:
        print(f"  sound playback failed ({sound}): {exc}", flush=True)


def _fresh_frame(camera):
    url = stream_url(camera)
    if BUFFER_MODE == "segments":
        return segments.get(camera, url).latest_frame()
    if BUFFER_MODE == "decoded":
        snap = streamer.get(camera, url).snapshot(1)
        return snap[0] if snap else None
    return grab(url)


def _deter_loop(camera, stop):
    """Repeatedly play a random sound while the target remains in view.

    Bounded by SOUND_MAX_DURATION: a stale background model makes an empty
    scene read as permanently busy, which would otherwise spam forever (seen
    on 2026-08-25 night test: 15% of the ROI 'moving' with nobody there).
    """
    started = time.time()
    try:
        while not stop.wait(random.uniform(SOUND_MIN_INTERVAL,
                                           SOUND_MAX_INTERVAL)):
            if time.time() - started > SOUND_MAX_DURATION:
                print(f"  deterrent: max duration reached, stopping ({camera})",
                      flush=True)
                break
            try:
                frame = _fresh_frame(camera)
                if frame is None:
                    print(f"  deterrent: no frame, stopping loop ({camera})",
                          flush=True)
                    break
                verdict, _, _, info, _ = score_frame(frame, camera)
            except Exception as exc:
                print(f"  deterrent loop error ({camera}): {exc}", flush=True)
                break

            if SOUND_ON_ANY_MOTION:
                present = info.get("motion_frac", 0.0) >= detect.MIN_MOTION_FRACTION
            else:
                present = verdict == "orange_cat"

            print(f"  deterrent: motion_frac={info.get('motion_frac', 0)} "
                  f"verdict={verdict} present={present}", flush=True)

            if not present:
                print(f"  target left view, stopping deterrent ({camera})",
                      flush=True)
                break

            sound = random.choice(ORANGE_SOUNDS)
            print(f"  deterrent: playing {sound} ({camera})", flush=True)
            _play_sound(sound)
    finally:
        stop.set()  # mark finished so the next event starts a fresh loop


@app.get("/health")
def health():
    return {"ok": True, "host": config.HOST,
            "cameras": list(CAMERAS), "buffer_mode": BUFFER_MODE,
            "buffers": (segments.all_status() if BUFFER_MODE == "segments"
                        else streamer.all_status())}


@app.get("/events")
def recent_events():
    """Last few motion receipts and their processing status."""
    return {
        "recent": list(RECENT_EVENTS),
        "last_orange_cat": _last_orange_cat(),
    }


def _last_orange_cat():
    if not os.path.exists(CSV_PATH):
        return None
    try:
        with open(CSV_PATH, newline="") as fh:
            last = None
            for row in csv.DictReader(fh):
                if row.get("verdict") == "orange_cat":
                    last = row["ts"]
            return last
    except OSError:
        return None


@app.get("/config")
def show_config():
    """The resolved config the server is running with (no secrets)."""
    return {
        "host": config.HOST,
        "buffer_mode": BUFFER_MODE,
        "bg_refresh_seconds": BG_REFRESH_SECONDS,
        "ha": {"host": config.HA_HOST, "speaker": config.HA_SPEAKER,
               "ssh_host": config.HA_SSH_HOST},
        "sound": {
            "sounds": ORANGE_SOUNDS,
            "near_door_min_bottom": NEAR_DOOR_MIN_BOTTOM,
            "on_any_motion": SOUND_ON_ANY_MOTION,
            "min_interval": SOUND_MIN_INTERVAL,
            "max_interval": SOUND_MAX_INTERVAL,
            "max_duration": SOUND_MAX_DURATION,
        },
    }


@app.on_event("startup")
def _warm_buffers():
    """Start whichever buffer the mode calls for, so events find frames ready."""
    if BUFFER_MODE == "off":
        print("  buffering off -- dialling out per event (~4s)", flush=True)
        return
    module = segments if BUFFER_MODE == "segments" else streamer
    for cam in CAMERAS:
        try:
            module.get(cam, stream_url(cam))
            print(f"  buffering {cam} ({BUFFER_MODE})", flush=True)
        except Exception as exc:
            print(f"  could not buffer {cam}: {exc}", flush=True)


# Without the ring buffer nothing else feeds the background model, and a stale
# model scores every event against yesterday's light. One frame per camera
# every few minutes is enough and costs almost nothing.
BG_REFRESH_SECONDS = config.BG_REFRESH_SECONDS
_stop_refresh = threading.Event()


def _refresh_frame(cam):
    """A recent frame for background learning, as cheaply as the mode allows."""
    if BUFFER_MODE == "segments":
        # Already on hand -- no connection, no handshake.
        frame = segments.get(cam, stream_url(cam)).latest_frame()
        if frame is not None:
            return frame
    return grab(stream_url(cam))


# Refusing to learn from a busy scene protects the model from absorbing a
# loitering cat -- but it also deadlocks: once the model is stale, every frame
# looks busy, so it never qualifies to update, so it stays stale forever. That
# happened overnight on 2026-08-16: a daylight model was still in place at
# 04:00, flagging 23% of every frame as motion.
#
# A cat does not sit at the door for half an hour. Sustained high motion means
# the model is wrong, not that the scene is busy, so after this many
# consecutive busy checks we relearn anyway.
BG_STALE_AFTER = 4
_busy_streak = {}


def _refresh_backgrounds():
    while not _stop_refresh.wait(BG_REFRESH_SECONDS):
        for cam in CAMERAS:
            try:
                frame = _refresh_frame(cam)
                if frame is None:
                    continue
                mask, info = detect.motion_mask(frame, cam)
                quiet = mask is None or info["motion_frac"] < 0.002
                streak = _busy_streak.get(cam, 0)

                if quiet:
                    detect.update_background(cam, frame)
                    _busy_streak[cam] = 0
                elif streak + 1 >= BG_STALE_AFTER:
                    print(f"  {cam}: {streak + 1} busy checks in a row "
                          f"({info['motion_frac'] * 100:.1f}% motion) -- "
                          f"treating the model as stale and relearning",
                          flush=True)
                    detect.update_background(cam, frame)
                    _busy_streak[cam] = 0
                else:
                    _busy_streak[cam] = streak + 1
            except Exception as exc:
                print(f"  background refresh failed for {cam}: {exc}",
                      flush=True)


@app.on_event("startup")
def _start_refresh():
    if BUFFER_MODE == "decoded":
        return  # the streamer threads already do this
    threading.Thread(target=_refresh_backgrounds, daemon=True,
                     name="bg-refresh").start()
    print(f"  background refresh every {BG_REFRESH_SECONDS}s", flush=True)


@app.on_event("shutdown")
def _close_buffers():
    _stop_refresh.set()
    for ev in _deterring.values():
        ev.set()
    streamer.stop_all()
    segments.stop_all()


def score_frame(frame, camera):
    """Score one frame. Returns (verdict, reasoning, stats, info, ir)."""
    mask, info = detect.motion_mask(frame, camera)
    ir_feat = None
    if mask is None:
        # Bootstrap: nothing to compare against yet. Seed the model so the
        # next event has one, and decline to guess about this frame.
        detect.update_background(camera, frame)
        stats = measure(frame, detect.roi_mask(frame.shape, camera))
    else:
        stats = measure(frame, mask)
        if stats["is_ir"]:
            ir_feat = detect.ir_features(frame, mask, camera)
    verdict, _, reasoning = detect.classify(stats, info, ir_feat)
    return verdict, reasoning, stats, info, ir_feat


@app.post("/motion")
async def motion(request: Request, image: UploadFile | None = None,
                 camera: str = Form("outside")):
    t0 = time.time()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    os.makedirs(EVENT_DIR, exist_ok=True)
    entry = {"ts": stamp, "camera": camera, "status": "received"}
    RECENT_EVENTS.append(entry)

    if image is not None:
        # A posted snapshot is one frame at whatever resolution HA chose --
        # the weakest input this classifier can be given. Treat it as a
        # trigger, not as the evidence: if a buffer is available it holds more
        # frames, at full resolution, including some from before the trigger.
        # The posted frame is still scored, so nothing is lost either way.
        blob = await image.read()
        # Keep exactly what arrived, byte for byte. Useful for confirming
        # which camera an automation is really pointed at, and for spotting
        # truncated or re-encoded snapshots.
        posted_path = f"{EVENT_DIR}/{camera}-{stamp}-posted.jpg"
        try:
            with open(posted_path, "wb") as fh:
                fh.write(blob)
        except OSError as exc:
            print(f"  could not save posted image: {exc}", flush=True)
        print(f"{stamp}  posted image from {getattr(image, 'filename', '?')}"
              f" -> {len(blob)} bytes, saved {posted_path}", flush=True)

        raw = np.frombuffer(blob, np.uint8)
        one = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        frames = [one] if one is not None else []
        source = "posted"
        if BUFFER_MODE != "off":
            try:
                buffered = (segments if BUFFER_MODE == "segments"
                            else streamer)
                extra = (buffered.get(camera, stream_url(camera)).frames(BURST)
                         if BUFFER_MODE == "segments"
                         else buffered.get(camera,
                                           stream_url(camera)).snapshot(BURST))
                if extra:
                    # Posted frame may differ in size from the stream; scoring
                    # mixes fine, but keep them separate for the record.
                    frames = extra + [f for f in frames
                                      if f is not None
                                      and f.shape == extra[0].shape]
                    source = f"posted+{BUFFER_MODE}"
            except Exception as exc:
                print(f"  buffer unavailable, using posted frame only: {exc}",
                      flush=True)
    else:
        # No file part, so the camera name (if any) is in the JSON body.
        try:
            body = await request.json()
            if isinstance(body, dict):
                camera = body.get("camera", camera)
        except Exception:
            pass
        if camera not in CAMERAS:
            camera = "outside"
        url = stream_url(camera)
        frames, source = [], "rtsp_cold"
        if BUFFER_MODE == "segments":
            # Decode the seconds we already have; includes pre-trigger frames.
            frames = segments.get(camera, url).frames(BURST)
            source = "segments"
        elif BUFFER_MODE == "decoded":
            frames = streamer.get(camera, url).snapshot(BURST)
            source = "ring_buffer"
        if not frames:
            # Buffering off, not warm yet, or the stream dropped.
            frames = grab_burst(url)
            source = "rtsp_cold"

    if camera not in CAMERAS:
        camera = "outside"

    if not frames:
        print(f"{stamp}  {camera}: event but no usable frame ({source})",
              flush=True)
        entry.update(camera=camera, status="no_frame")
        return {"ok": False, "error": "no frame", "camera": camera,
                "source": source}

    scored = [score_frame(f, camera) for f in frames]
    verdict, confidence, tally = detect.vote([s[0] for s in scored])

    if verdict is None:
        # Nothing decisive -- report the most common abstention instead.
        counts = {}
        for s in scored:
            counts[s[0]] = counts.get(s[0], 0) + 1
        verdict = max(counts, key=counts.get)
        confidence = 0.0
        tally = counts

    # Keep the frame that best represents the winning verdict.
    best = max((s for s in scored if s[0] == verdict),
               key=lambda s: s[3]["motion_px"], default=scored[0])
    _, reasoning, stats, info, ir_feat = best
    keep = frames[scored.index(best)]

    path = f"{EVENT_DIR}/{camera}-{stamp}.jpg"
    cv2.imwrite(path, keep)

    row = {"ts": stamp, "camera": camera, "file": path, "verdict": verdict,
           "confidence": round(confidence, 3), "source": source,
           "reasoning": reasoning, "frames": len(frames),
           "votes": ";".join(f"{k}={v}" for k, v in sorted(tally.items())),
           "width": keep.shape[1], "height": keep.shape[0],
           **stats, **{k: v for k, v in info.items() if k != "reason"},
           **(ir_feat or {})}
    record(row)
    entry.update(camera=camera, status="processed", verdict=verdict,
                 confidence=round(confidence, 3),
                 motion_frac=info.get("motion_frac"))

    mode = "IR " if stats["is_ir"] else "COL"
    print(f"{stamp}  {camera:7s} {mode}  {verdict:18s} "
          f"({confidence * 100:3.0f}% of {len(frames)} frames)  "
          f"{keep.shape[1]}x{keep.shape[0]}  "
          f"votes={row['votes']}  src={source}", flush=True)

    if SOUND_ON_ANY_MOTION:
        fire, why = True, "bypass (any motion)"
    else:
        near_door = any(s[3].get("bbox_bottom", 0.0) >= NEAR_DOOR_MIN_BOTTOM
                        for s in scored)
        fire = verdict == "orange_cat" and near_door
        why = "orange_cat near door" if fire else ""

    sound = random.choice(ORANGE_SOUNDS) if fire else None
    sound_lat_ms = (time.time() - t0) * 1000
    if sound:
        print(f"{stamp}  -> playing {sound} ({why})  "
              f"[req->fire {sound_lat_ms:.0f} ms]", flush=True)
        threading.Thread(target=_play_sound, args=(sound,),
                         daemon=True, name="orange-sound").start()
        running = _deterring.get(camera)
        if running is None or running.is_set():
            stop = threading.Event()
            _deterring[camera] = stop
            threading.Thread(target=_deter_loop, args=(camera, stop),
                             daemon=True, name=f"deter-{camera}").start()

    return {"ok": True, "camera": camera, "verdict": verdict,
            "confidence": round(confidence, 3), "frames": len(frames),
            "votes": tally, "source": source, "stats": stats,
            "motion": info, "ir": ir_feat, "reasoning": reasoning,
            "sound": sound, "sound_latency_ms": round(sound_lat_ms, 1)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
