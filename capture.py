"""Sample the camera on an interval and log per-frame statistics.

Purpose is to collect real data across the day/night boundary: we need to know
what the scene looks like once the camera drops into IR, and what the warm
saturated background level actually is outdoors.

Run:  uv run capture.py [interval_seconds] [duration_minutes]
"""

import cv2
import csv
import os
import sys
import time
from datetime import datetime

import numpy as np
from dotenv import load_dotenv

FRAME_DIR = "frames"
CSV_PATH = "frames/stats.csv"


def stream_url():
    api_key = os.getenv("RTSP_CAT_KEY")
    if not api_key:
        raise RuntimeError("Env var RTSP_CAT_KEY not set. Add it to .env.")
    host = os.getenv("CAT_HOST", "192.168.1.1")
    return f"rtsp://{host}:7447/{api_key}?enableSrtp"


def grab(url):
    """Open the stream, take one frame, close it again.

    Reconnecting each time is slower than holding the capture open, but it
    survives overnight runs -- a long-held RTSP handle tends to wedge.
    """
    cap = cv2.VideoCapture(url)
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def measure(img):
    """Per-frame statistics. rgb_spread is the day/night discriminator."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    b, g, r = (img[:, :, i].astype(np.int16) for i in range(3))
    stack = np.stack([b, g, r])
    rgb_spread = float(np.mean(stack.max(axis=0) - stack.min(axis=0)))

    # Warm + saturated: the orange-cat signal. Hue wraps, so both ends.
    warm = ((h <= 22) | (h >= 170)) & (s > 90) & (v > 50)

    return {
        "brightness": round(float(v.mean()), 2),
        "saturation": round(float(s.mean()), 2),
        "rgb_spread": round(rgb_spread, 2),
        # Below ~8 there is no usable colour left; the camera is in IR.
        "is_ir": int(rgb_spread < 8),
        "warm_pct": round(float(warm.mean()) * 100, 4),
        "luma_std": round(float(v.std()), 2),
    }


def main():
    load_dotenv()
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 0  # 0 = forever

    os.makedirs(FRAME_DIR, exist_ok=True)
    url = stream_url()
    deadline = time.time() + duration * 60 if duration else None

    new_file = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as fh:
        fields = ["ts", "file", "brightness", "saturation", "rgb_spread",
                  "is_ir", "warm_pct", "luma_std"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new_file:
            writer.writeheader()

        while deadline is None or time.time() < deadline:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            frame = grab(url)

            if frame is None:
                print(f"{stamp}  no frame (stream unavailable)", flush=True)
                time.sleep(interval)
                continue

            name = f"{FRAME_DIR}/{stamp}.jpg"
            cv2.imwrite(name, frame)

            row = {"ts": stamp, "file": name, **measure(frame)}
            writer.writerow(row)
            fh.flush()

            mode = "IR " if row["is_ir"] else "COL"
            print(f"{stamp}  {mode}  bright={row['brightness']:6.1f}  "
                  f"spread={row['rgb_spread']:6.1f}  "
                  f"warm={row['warm_pct']:7.3f}%", flush=True)

            time.sleep(interval)


if __name__ == "__main__":
    main()
