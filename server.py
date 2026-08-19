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

import collections
import csv
import os
import threading
import time
from datetime import datetime

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, UploadFile
import uvicorn

import animal
import detect
import segments
import streamer
from capture import CAMERAS, grab, grab_burst, measure, stream_url

EVENT_DIR = "frames/events"
CSV_PATH = "frames/events.csv"

load_dotenv()
app = FastAPI()


FIELDS = ["ts", "camera", "file", "verdict", "confidence", "brightness",
          "saturation", "rgb_spread", "is_ir", "warm_pct", "luma_std",
          "px", "motion_px", "motion_frac", "iso_frac", "usable",
          "roi_px", "bg_corr", "warm_margin", "det_conf", "box_frac",
          "rel_bright", "cv",
          "frames", "votes", "width", "height", "source", "reasoning"]


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


# Keep every frame that was scored, not just the winner, under
# frames/events/<camera>-<stamp>/NN.jpg.
#
# Added 2026-08-18 at Dima's request, and it immediately earned itself: the
# single "best" frame the server kept was hiding the fact that the buffer was
# serving footage a minute stale. One frame per event cannot show that; the
# burst can. Costs ~1.5 MB per event, so it is opt-out.
SAVE_BURST = os.getenv("SAVE_BURST", "1") not in ("0", "false", "no")

# A warm buffer is not the same thing as a CURRENT buffer, and conflating the
# two cost a whole night of detections on 2026-08-17/18. The ffmpeg recorder
# restarted 40 times over the VPN that night; after a restart the rolling
# window refills from wherever the stream resumes, and events were being
# scored against footage ~70 s old. Ten animals -- cats and two skunks,
# confirmed by Dima against Protect's own clips -- were each scored as an
# empty patio, because by the time we looked at the "buffered" frames the
# animal had not arrived in them yet.
#
# So: if the newest complete segment is older than this, the buffer is not
# describing now. Dial out for fresh frames instead and pay the ~4 s.
MAX_BUFFER_AGE = float(os.getenv("MAX_BUFFER_AGE", "20"))

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
BUFFER_MODE = os.getenv("BUFFER_MODE", "segments").lower()


@app.get("/health")
def health():
    return {"ok": True, "host": os.getenv("CAT_HOST", "192.168.1.1"),
            "cameras": list(CAMERAS), "buffer_mode": BUFFER_MODE,
            "buffers": (segments.all_status() if BUFFER_MODE == "segments"
                        else streamer.all_status()),
            "warnings": list(_warnings)}


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
BG_REFRESH_SECONDS = int(os.getenv("BG_REFRESH_SECONDS", "300"))
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
                    # alpha=1.0: REPLACE the model, do not blend into it.
                    # The default 0.05 moves it 5% toward the current frame,
                    # which at one check per BG_REFRESH_SECONDS needs ~45
                    # cycles -- nearly four hours -- to catch up. The guard
                    # then re-fires every 20 minutes forever while every
                    # event in between is scored against a model that is
                    # still wrong. Seen on 2026-08-18: the log filled with
                    # "treating the model as stale and relearning" all night
                    # while events reported a steady ~88000 px of "motion"
                    # that was really the wall and the planter under changed
                    # light. A model declared stale is wrong, so throw it
                    # away rather than averaging the wrong answer in.
                    detect.update_background(cam, frame, alpha=1.0)
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
    streamer.stop_all()
    segments.stop_all()


# Find the animal first, then ask what colour it is. The motion path is kept
# underneath: it still feeds the background model, still fills the diagnostic
# columns in events.csv, and still decides if the model file is missing.
USE_DETECTOR = os.getenv("USE_DETECTOR", "1") not in ("0", "false", "no")


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

    if USE_DETECTOR and animal.available():
        found = animal.best_box(frame)
        if found is None:
            # Abstain rather than vote. On a real raid the detector fired on
            # as few as 1 of 15 frames, so treating "no detection" as
            # evidence of absence would drown every true positive in its own
            # burst.
            return ("no_animal", "no animal detected in frame",
                    stats, {**info, "det_conf": None}, ir_feat)
        feats = detect.box_features(frame, found["box"])
        verdict, _, reasoning = detect.classify_detection(feats)
        info = {**info, "det_conf": found["conf"],
                "box_frac": (feats or {}).get("box_frac"),
                "warm_margin": (feats or {}).get("warm_margin")}
        if feats:
            stats = {**stats, "warm_pct": feats["warm_pct"]}
        return verdict, reasoning, stats, info, ir_feat

    verdict, _, reasoning = detect.classify(stats, info, ir_feat)
    return verdict, reasoning, stats, info, ir_feat


# Every failure this project has had was SILENT and looked exactly like a
# quiet night: stale buffer frames (2026-08-17), a "relearn" that only nudged
# the model 5% (2026-08-18), an ROI that cropped out the animals. In each case
# the verdict column read clean while the detector was blind. Nothing can tell
# "the patio was empty" from "I was looking at the wrong minute" -- so the
# server has to watch its own vital signs and say when they look wrong.
_recent = collections.deque(maxlen=12)
_warnings = []


def _self_check(camera, info, roi_px):
    """Flag states that mean the detector is not seeing, whatever it reported.

    These are checks on the SHAPE of recent measurements, not on verdicts --
    a verdict cannot reveal that it was computed against the wrong minute.
    """
    _recent.append((camera, int(info.get("motion_px") or 0)))
    mine = [px for c, px in _recent if c == camera]
    out = []

    # A near-constant, large motion area across events is not motion. Real
    # animals differ frame to frame; a wrong background model does not.
    if len(mine) >= 5 and roi_px:
        last = mine[-5:]
        hi, lo = max(last), min(last)
        if hi > 0.02 * roi_px and hi - lo <= 0.05 * hi:
            out.append(f"motion_px pinned near {hi} across 5 events -- "
                       "background model is probably wrong, not the scene")

    # Nothing changing over many events, when events keep arriving, means
    # something upstream is feeding us the wrong frames.
    if (not (USE_DETECTOR and animal.available())
            and len(mine) >= 8 and all(p == 0 for p in mine[-8:])):
        out.append("8 consecutive events with zero motion pixels -- "
                   "detector may be blind (check buffer freshness and ROI)")

    # Under the detector path the equivalent blindness is the model failing
    # to load, which would silently drop every event back to abstaining.
    if USE_DETECTOR and not animal.available():
        out.append(f"animal detector unavailable ({animal.MODEL_PATH}) -- "
                   "falling back to the motion path")

    # A model that has not been written recently is not tracking the light.
    try:
        age = time.time() - os.path.getmtime(detect._bg_path(camera))
        if age > 3 * BG_REFRESH_SECONDS:
            out.append(f"background model for {camera} is {age / 60:.0f} min "
                       "old -- refresh thread may be stuck")
    except OSError:
        out.append(f"no background model on disk for {camera}")

    for w in out:
        print(f"  !! {camera}: {w}", flush=True)
    _warnings[:] = [f"{camera}: {w}" for w in out]
    return out


def _buffer_is_current(camera, age):
    """Is the buffered video recent enough to describe the event?"""
    if age is None or age <= MAX_BUFFER_AGE:
        return True
    print(f"  {camera}: buffer is {age:.0f}s stale (limit "
          f"{MAX_BUFFER_AGE:.0f}s) -- ignoring it and grabbing fresh frames",
          flush=True)
    return False


def _save_burst(frames, scored, camera, stamp):
    """Write every scored frame, in order, with its own verdict in the name.

    The order is the order they were voted on, so a burst that turns out to be
    stale, gappy, or duplicated is visible at a glance in the file listing --
    which is the failure this exists to catch. The per-frame verdict is in the
    filename so a directory listing already tells you where the classifier
    disagreed with itself, without opening anything.
    """
    d = f"{EVENT_DIR}/{camera}-{stamp}"
    try:
        os.makedirs(d, exist_ok=True)
        for i, (frame, s) in enumerate(zip(frames, scored)):
            if frame is None:
                continue
            px = s[3].get("motion_px", 0)
            cv2.imwrite(f"{d}/{i:02d}-{s[0]}-px{px}.jpg", frame)
    except OSError as exc:
        print(f"  could not save burst: {exc}", flush=True)


@app.post("/motion")
async def motion(request: Request, image: UploadFile | None = None,
                 camera: str = Form("outside")):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    os.makedirs(EVENT_DIR, exist_ok=True)

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
                age = (buffered.get(camera, stream_url(camera)).age()
                       if BUFFER_MODE == "segments" else None)
                if extra and _buffer_is_current(camera, age):
                    # Posted frame may differ in size from the stream; scoring
                    # mixes fine, but keep them separate for the record.
                    frames = extra + [f for f in frames
                                      if f is not None
                                      and f.shape == extra[0].shape]
                    source = f"posted+{BUFFER_MODE}"
                elif extra:
                    fresh = grab_burst(stream_url(camera))
                    if fresh:
                        frames = fresh
                        source = "posted+rtsp_fresh"
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
            buf = segments.get(camera, url)
            if _buffer_is_current(camera, buf.age()):
                frames = buf.frames(BURST)
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
        return {"ok": False, "error": "no frame", "camera": camera,
                "source": source}

    scored = [score_frame(f, camera) for f in frames]

    if SAVE_BURST:
        _save_burst(frames, scored, camera, stamp)

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
    _self_check(camera, info, info.get("roi_px") or 0)
    record(row)

    mode = "IR " if stats["is_ir"] else "COL"
    print(f"{stamp}  {camera:7s} {mode}  {verdict:18s} "
          f"({confidence * 100:3.0f}% of {len(frames)} frames)  "
          f"{keep.shape[1]}x{keep.shape[0]}  "
          f"votes={row['votes']}  src={source}", flush=True)

    return {"ok": True, "camera": camera, "verdict": verdict,
            "confidence": round(confidence, 3), "frames": len(frames),
            "votes": tally, "source": source, "stats": stats,
            "motion": info, "ir": ir_feat, "reasoning": reasoning}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
