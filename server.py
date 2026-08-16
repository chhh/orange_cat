"""Webhook receiver for Home Assistant motion events.

Home Assistant relays UniFi Protect motion detections here as a POST. Two
shapes are accepted, so the automation can be wired either way:

  * multipart/form-data with an "image" file  -- preferred, no extra latency
  * JSON (or an empty body)                   -- we pull a frame over RTSP,
                                                 which costs a few seconds and
                                                 may miss a fast-moving cat

Every event is scored, logged to frames/events.csv, and the frame kept.

Run:  uv run server.py            (listens on 0.0.0.0:8080)
"""

import csv
import os
from datetime import datetime

import cv2
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile
import uvicorn

from capture import grab, measure, stream_url

EVENT_DIR = "frames/events"
CSV_PATH = "frames/events.csv"

# Provisional. Neither number is calibrated against a real cat yet -- see
# classify() for what each one is standing in for.
WARM_PCT_THRESHOLD = 0.5
LUMA_STD_THRESHOLD = 30.0

load_dotenv()
app = FastAPI()


def classify(stats):
    """Provisional verdict. Returns (verdict, confidence, reasoning).

    Daylight: an orange cat puts warm, saturated pixels into a scene that has
    almost none. That test is sound but the threshold is a guess.

    IR: no colour survives, so the stand-in is luminance spread -- a
    black-and-white cat should show two distinct tones where a uniformly
    orange one shows one. This is weak, and measuring it over the whole frame
    (rather than the animal) makes it weaker still. It needs real IR frames of
    both cats before it can be trusted at all.
    """
    if stats["is_ir"]:
        uniform = stats["luma_std"] < LUMA_STD_THRESHOLD
        return (
            "possible_intruder" if uniform else "probably_resident",
            "low",
            f"IR mode, no colour available; luma_std={stats['luma_std']} "
            f"vs threshold {LUMA_STD_THRESHOLD} (UNCALIBRATED)",
        )

    orange = stats["warm_pct"] > WARM_PCT_THRESHOLD
    return (
        "orange_cat" if orange else "no_orange",
        "medium",
        f"colour mode; warm_pct={stats['warm_pct']} "
        f"vs threshold {WARM_PCT_THRESHOLD}",
    )


def record(stamp, path, stats, verdict, confidence, reasoning):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    new_file = not os.path.exists(CSV_PATH)
    fields = ["ts", "file", "verdict", "confidence", "brightness", "saturation",
              "rgb_spread", "is_ir", "warm_pct", "luma_std", "reasoning"]
    with open(CSV_PATH, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new_file:
            writer.writeheader()
        writer.writerow({"ts": stamp, "file": path, "verdict": verdict,
                         "confidence": confidence, "reasoning": reasoning,
                         **stats})


@app.get("/health")
def health():
    return {"ok": True, "host": os.getenv("CAT_HOST", "192.168.1.1")}


@app.post("/motion")
async def motion(request: Request, image: UploadFile | None = None):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    os.makedirs(EVENT_DIR, exist_ok=True)

    if image is not None:
        raw = np.frombuffer(await image.read(), np.uint8)
        frame = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        source = "posted"
    else:
        frame = grab(stream_url())
        source = "rtsp_pull"

    if frame is None:
        print(f"{stamp}  event received but no usable frame ({source})", flush=True)
        return {"ok": False, "error": "no frame", "source": source}

    path = f"{EVENT_DIR}/{stamp}.jpg"
    cv2.imwrite(path, frame)

    stats = measure(frame)
    verdict, confidence, reasoning = classify(stats)
    record(stamp, path, stats, verdict, confidence, reasoning)

    mode = "IR " if stats["is_ir"] else "COL"
    print(f"{stamp}  {mode}  {verdict:18s} ({confidence:6s})  "
          f"warm={stats['warm_pct']:7.3f}%  luma_std={stats['luma_std']:6.2f}  "
          f"src={source}", flush=True)

    return {"ok": True, "verdict": verdict, "confidence": confidence,
            "source": source, "stats": stats, "reasoning": reasoning}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
