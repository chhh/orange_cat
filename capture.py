"""Camera access and per-frame measurement.

Two cameras: "inside" and "outside". Keys come from CAT_VIDEO_KEY_INSIDE and
CAT_VIDEO_KEY_OUTSIDE; the host is CAT_HOST, which differs by location (see
README-ish note in main.py).

Run:  uv run capture.py [interval_seconds] [duration_minutes] [camera]
"""

import csv
import os
import sys
import time
from datetime import datetime

import cv2
import numpy as np
from dotenv import load_dotenv

import detect

CAMERAS = ("inside", "outside")
FRAME_DIR = "frames"
CSV_PATH = "frames/stats.csv"


def stream_url(camera="outside"):
    if camera not in CAMERAS:
        raise ValueError(f"camera must be one of {CAMERAS}, got {camera!r}")
    var = f"CAT_VIDEO_KEY_{camera.upper()}"
    key = os.getenv(var)
    if not key:
        raise RuntimeError(f"Env var {var} not set. Add it to .env (gitignored).")
    host = os.getenv("CAT_HOST", "192.168.1.1")
    return f"rtsp://{host}:7447/{key}?enableSrtp"


def grab(url, flush=5):
    """Open the stream, read a few frames, return the last one.

    The first frame off an RTSP connection is whatever was sitting in the
    buffer, which can be seconds stale -- no good for catching an animal
    mid-transit. Reading several and keeping the last gets something current.
    (Idea from Dima's version.)

    Reconnecting per call rather than holding the capture open is slower but
    survives overnight runs; a long-held RTSP handle tends to wedge.
    """
    cap = cv2.VideoCapture(url)
    try:
        if not cap.isOpened():
            return None
        frame = None
        for _ in range(max(1, flush)):
            ok, f = cap.read()
            if ok and f is not None:
                frame = f
        return frame
    finally:
        cap.release()


def grab_burst(url, count=15, stride=4, flush=5):
    """Collect several frames spread over a couple of seconds.

    A single frame is a poor basis for a verdict: per-frame confidence on the
    black-and-white cats runs 57-60%, so one snapshot is close to a coin flip.
    Clip-level accuracy came from many frames voting, and this is how the live
    path gets the same thing.

    The stream delivers ~30fps, so consecutive frames are nearly identical --
    taking every `stride`-th spreads the sample over real movement instead.
    """
    cap = cv2.VideoCapture(url)
    out = []
    try:
        if not cap.isOpened():
            return out
        for _ in range(flush):  # discard the stale buffered frames
            cap.read()
        wanted = count * stride
        for i in range(wanted):
            ok, f = cap.read()
            if not ok or f is None:
                break
            if i % stride == 0:
                out.append(f)
            if len(out) >= count:
                break
        return out
    finally:
        cap.release()


def measure(img, mask=None):
    """Per-frame statistics.

    Day/night mode is judged on the whole frame -- it is a property of the
    camera, not of whatever is moving. The colour and luminance signals are
    measured only inside `mask` when one is given, so that a static wooden
    gate or a tan planter cannot contribute to the verdict.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    b, g, r = (img[:, :, i].astype(np.int16) for i in range(3))
    stack = np.stack([b, g, r])
    rgb_spread = float(np.mean(stack.max(axis=0) - stack.min(axis=0)))

    # Warm + saturated: the orange-cat signal. Shared with the surround
    # comparison in detect, so the two cannot drift apart.
    warm = detect.warm_mask(img)

    if mask is None:
        sel = np.ones(img.shape[:2], dtype=bool)
    else:
        sel = mask.astype(bool)

    px = int(sel.sum())
    if px == 0:
        # Nothing selected: report frame-level mode, zero signal.
        return {"brightness": round(float(v.mean()), 2),
                "saturation": 0.0, "rgb_spread": round(rgb_spread, 2),
                "is_ir": int(rgb_spread < 8), "warm_pct": 0.0,
                "luma_std": 0.0, "px": 0}

    return {
        "brightness": round(float(v[sel].mean()), 2),
        "saturation": round(float(s[sel].mean()), 2),
        "rgb_spread": round(rgb_spread, 2),
        # Below ~8 there is no usable colour left; the camera is in IR.
        "is_ir": int(rgb_spread < 8),
        "warm_pct": round(float(warm[sel].mean()) * 100, 4),
        "luma_std": round(float(v[sel].std()), 2),
        "px": px,
    }


def main():
    load_dotenv()
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 0  # 0 = forever
    camera = sys.argv[3] if len(sys.argv) > 3 else "outside"

    os.makedirs(FRAME_DIR, exist_ok=True)
    url = stream_url(camera)
    deadline = time.time() + duration * 60 if duration else None

    new_file = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as fh:
        fields = ["ts", "camera", "file", "brightness", "saturation",
                  "rgb_spread", "is_ir", "warm_pct", "luma_std", "px"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new_file:
            writer.writeheader()

        while deadline is None or time.time() < deadline:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            frame = grab(url)

            if frame is None:
                print(f"{stamp}  no frame ({camera})", flush=True)
                time.sleep(interval)
                continue

            name = f"{FRAME_DIR}/{camera}-{stamp}.jpg"
            cv2.imwrite(name, frame)

            # Feed the background model. Periodic sampling mostly catches an
            # empty scene, which is exactly what we want it trained on.
            detect.update_background(camera, frame)

            row = {"ts": stamp, "camera": camera, "file": name,
                   **measure(frame, detect.roi_mask(frame.shape, camera))}
            writer.writerow(row)
            fh.flush()

            mode = "IR " if row["is_ir"] else "COL"
            print(f"{stamp}  {camera:7s} {mode}  bright={row['brightness']:6.1f}  "
                  f"spread={row['rgb_spread']:6.1f}  "
                  f"warm={row['warm_pct']:7.3f}%", flush=True)

            time.sleep(interval)


if __name__ == "__main__":
    main()
