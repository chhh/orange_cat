"""Webhook receiver for Home Assistant motion events.

Home Assistant relays UniFi Protect motion detections here as a POST. Two
shapes are accepted, so the automation can be wired either way:

  * multipart/form-data with an "image" file  -- preferred, no extra latency
  * JSON (or an empty body)                   -- we pull a frame over RTSP,
                                                 which costs a few seconds and
                                                 may miss a fast-moving cat

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
from capture import CAMERAS, grab, measure, stream_url

EVENT_DIR = "frames/events"
CSV_PATH = "frames/events.csv"

load_dotenv()
app = FastAPI()


def record(row):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    new_file = not os.path.exists(CSV_PATH)
    fields = ["ts", "camera", "file", "verdict", "confidence", "brightness",
              "saturation", "rgb_spread", "is_ir", "warm_pct", "luma_std",
              "px", "motion_px", "motion_frac", "roi_px", "source", "reasoning"]
    with open(CSV_PATH, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


@app.get("/health")
def health():
    return {"ok": True, "host": os.getenv("CAT_HOST", "192.168.1.1"),
            "cameras": list(CAMERAS)}


@app.post("/motion")
async def motion(request: Request, image: UploadFile | None = None,
                 camera: str = Form("outside")):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    os.makedirs(EVENT_DIR, exist_ok=True)

    if image is not None:
        raw = np.frombuffer(await image.read(), np.uint8)
        frame = cv2.imdecode(raw, cv2.IMREAD_COLOR)
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
        frame = grab(stream_url(camera))
        source = "rtsp_pull"

    if camera not in CAMERAS:
        camera = "outside"

    if frame is None:
        print(f"{stamp}  {camera}: event but no usable frame ({source})",
              flush=True)
        return {"ok": False, "error": "no frame", "camera": camera,
                "source": source}

    path = f"{EVENT_DIR}/{camera}-{stamp}.jpg"
    cv2.imwrite(path, frame)

    mask, info = detect.motion_mask(frame, camera)
    if mask is None:
        # Bootstrap: nothing to compare against yet. Seed the model so the
        # next event has one, and decline to guess about this frame.
        detect.update_background(camera, frame)
        stats = measure(frame, detect.roi_mask(frame.shape, camera))
        verdict, confidence, reasoning = detect.classify(stats, info)
    else:
        stats = measure(frame, mask)
        verdict, confidence, reasoning = detect.classify(stats, info)

    row = {"ts": stamp, "camera": camera, "file": path, "verdict": verdict,
           "confidence": confidence, "source": source, "reasoning": reasoning,
           **stats, **{k: v for k, v in info.items() if k != "reason"}}
    record(row)

    mode = "IR " if stats["is_ir"] else "COL"
    print(f"{stamp}  {camera:7s} {mode}  {verdict:18s} ({confidence:6s})  "
          f"warm={stats['warm_pct']:7.3f}%  luma_std={stats['luma_std']:6.2f}  "
          f"motion={info['motion_px']:6d}px  src={source}", flush=True)

    return {"ok": True, "camera": camera, "verdict": verdict,
            "confidence": confidence, "source": source, "stats": stats,
            "motion": info, "reasoning": reasoning}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
