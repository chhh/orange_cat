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
from datetime import datetime

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, UploadFile
import uvicorn

import detect
from capture import CAMERAS, grab_burst, measure, stream_url

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


@app.get("/health")
def health():
    return {"ok": True, "host": os.getenv("CAT_HOST", "192.168.1.1"),
            "cameras": list(CAMERAS)}


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
        # Pull our own burst: full resolution, and enough frames to vote on.
        frames = grab_burst(stream_url(camera))
        source = "rtsp_burst"

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
