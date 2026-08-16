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
useless here.

Run:  uv run server.py            (listens on 0.0.0.0:8080)
"""

import csv
import os
import threading
from datetime import datetime

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, UploadFile
import uvicorn

import detect
import streamer
from capture import CAMERAS, grab, grab_burst, measure, stream_url

EVENT_DIR = "frames/events"
CSV_PATH = "frames/events.csv"

load_dotenv()
app = FastAPI()


def record(row):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    new_file = not os.path.exists(CSV_PATH)
    fields = ["ts", "camera", "file", "verdict", "confidence", "brightness",
              "saturation", "rgb_spread", "is_ir", "warm_pct", "luma_std",
              "px", "motion_px", "motion_frac", "iso_frac", "usable",
              "roi_px", "rel_bright", "cv", "frames", "votes", "width",
              "height", "source", "reasoning"]
    with open(CSV_PATH, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


BURST = 15

# Holding RTSP connections open costs ~24% of a CPU for two streams, and buys
# 0.25s events instead of 4.0s. That is waste for notifications and essential
# for a deterrent, so it is a choice rather than a default.
#
#   RING_BUFFER=0  connect on demand   -- ~4.0s, idle when nothing happens
#   RING_BUFFER=1  hold connections    -- ~0.25s, plus pre-trigger frames
RING_BUFFER = os.getenv("RING_BUFFER", "0") == "1"


@app.get("/health")
def health():
    return {"ok": True, "host": os.getenv("CAT_HOST", "192.168.1.1"),
            "cameras": list(CAMERAS), "ring_buffer": RING_BUFFER,
            "streams": streamer.all_status()}


@app.on_event("startup")
def _warm_streams():
    """Open both connections at boot so no event pays the handshake."""
    if not RING_BUFFER:
        print("  ring buffer off -- connecting on demand (set RING_BUFFER=1 "
              "for sub-second events)", flush=True)
        return
    for cam in CAMERAS:
        try:
            streamer.get(cam, stream_url(cam))
            print(f"  streaming {cam}", flush=True)
        except Exception as exc:
            print(f"  could not start {cam}: {exc}", flush=True)


# Without the ring buffer nothing else feeds the background model, and a stale
# model scores every event against yesterday's light. One frame per camera
# every few minutes is enough and costs almost nothing.
BG_REFRESH_SECONDS = int(os.getenv("BG_REFRESH_SECONDS", "300"))
_stop_refresh = threading.Event()


def _refresh_backgrounds():
    while not _stop_refresh.wait(BG_REFRESH_SECONDS):
        for cam in CAMERAS:
            try:
                frame = grab(stream_url(cam))
                if frame is None:
                    continue
                mask, info = detect.motion_mask(frame, cam)
                # Only learn from a quiet scene, so a loitering cat cannot
                # gradually become part of the scenery.
                if mask is None or info["motion_frac"] < 0.002:
                    detect.update_background(cam, frame)
            except Exception as exc:
                print(f"  background refresh failed for {cam}: {exc}",
                      flush=True)


@app.on_event("startup")
def _start_refresh():
    if RING_BUFFER:
        return  # the streamer threads already do this
    threading.Thread(target=_refresh_backgrounds, daemon=True,
                     name="bg-refresh").start()
    print(f"  background refresh every {BG_REFRESH_SECONDS}s", flush=True)


@app.on_event("shutdown")
def _close_streams():
    _stop_refresh.set()
    streamer.stop_all()


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
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    os.makedirs(EVENT_DIR, exist_ok=True)

    if image is not None:
        raw = np.frombuffer(await image.read(), np.uint8)
        one = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        frames = [one] if one is not None else []
        source = "posted"
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
        if RING_BUFFER:
            # Full resolution, frames from before the trigger, no handshake.
            frames = streamer.get(camera, url).snapshot(BURST)
            source = "ring_buffer"
        if not frames:
            # Buffer off, not warm yet, or the stream dropped.
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
