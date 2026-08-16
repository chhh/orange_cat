"""Region of interest, background model, and classification.

The core problem with scoring a whole frame is that the outdoor scene is full
of permanently orange things. Measured on frame_outside.jpg (1024x576):

    whole frame          4.38% warm-and-saturated
    wooden gate         23.97%   <- almost all of it, and nowhere near the door
    tan planter          2.13%
    open concrete        0.89%
    cat door + surround  0.48%

So we do two things: crop to the part of the scene the cat actually uses, and
score only pixels that changed against a background model. Static warm objects
then contribute nothing, and what is left is the animal.

That also rescues the IR path. Luminance spread across a whole room describes
the room; luminance spread across the moving pixels describes the cat -- and a
black-and-white cat should be two-toned where a ginger one is uniform.
"""

import os

import cv2
import numpy as np

BG_DIR = "frames"

# (x0, y0, x1, y1) as fractions of width/height.
#
# outside: door plus the concrete approach, gate excluded. Chosen empirically --
#          this crop holds background warm pixels to 0.52% versus 4.38% for the
#          full frame, while keeping 645x317 px of usable area.
# inside:  the pegboard wall with the flap, plus the floor in front of it.
ROI = {
    "outside": (0.00, 0.45, 0.63, 1.00),
    "inside": (0.35, 0.30, 1.00, 1.00),
}

# How fast the background forgets. Low enough that a cat sitting still for a
# few frames does not get absorbed into it.
BG_ALPHA = 0.05

# A changed pixel must differ by at least this much (0-255, greyscale).
DIFF_THRESHOLD = 25

# Ignore motion smaller than this fraction of the ROI -- leaves, rain, noise.
MIN_MOTION_FRACTION = 0.004

# --- Verdict thresholds -------------------------------------------------
# Both are still UNCALIBRATED: no frame containing a known cat has been scored
# yet. They are deliberately biased toward flagging, because this system alerts
# rather than gates -- a false positive is a wasted notification, a false
# negative is a stolen meal, and neither locks a cat outside.
WARM_PCT_THRESHOLD = 8.0   # of the moving pixels, how much reads as ginger
LUMA_STD_THRESHOLD = 28.0  # below this the animal looks one-toned in IR


def roi_box(shape, camera):
    h, w = shape[:2]
    x0, y0, x1, y1 = ROI.get(camera, (0.0, 0.0, 1.0, 1.0))
    return (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))


def roi_mask(shape, camera):
    """Boolean mask that is True inside the camera's region of interest."""
    h, w = shape[:2]
    x0, y0, x1, y1 = roi_box(shape, camera)
    mask = np.zeros((h, w), dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


def _bg_path(camera):
    return os.path.join(BG_DIR, f"bg_{camera}.npy")


def load_background(camera):
    path = _bg_path(camera)
    if not os.path.exists(path):
        return None
    try:
        return np.load(path)
    except (ValueError, OSError):
        return None


def update_background(camera, frame, alpha=BG_ALPHA):
    """Blend a frame into the running background model.

    Called from the periodic capture loop, which mostly sees an empty scene --
    which is what we want it trained on. Resets if the resolution changes.
    """
    os.makedirs(BG_DIR, exist_ok=True)
    cur = frame.astype(np.float32)
    bg = load_background(camera)
    if bg is None or bg.shape != cur.shape:
        bg = cur
    else:
        cv2.accumulateWeighted(cur, bg, alpha)
    np.save(_bg_path(camera), bg)
    return bg


def motion_mask(frame, camera):
    """Pixels that changed against the background, within the ROI.

    Returns (mask, info). mask is None when there is no background yet.
    """
    bg = load_background(camera)
    roi = roi_mask(frame.shape, camera)
    roi_px = int(roi.sum())

    if bg is None or bg.shape != frame.shape:
        return None, {"reason": "no background model yet", "roi_px": roi_px,
                      "motion_px": 0, "motion_frac": 0.0}

    diff = cv2.absdiff(frame.astype(np.float32), bg).astype(np.uint8)
    grey = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    grey = cv2.GaussianBlur(grey, (5, 5), 0)
    changed = grey > DIFF_THRESHOLD

    # Close small holes so an animal reads as one region rather than speckle.
    kernel = np.ones((5, 5), np.uint8)
    changed = cv2.morphologyEx(changed.astype(np.uint8), cv2.MORPH_CLOSE,
                               kernel).astype(bool)

    mask = changed & roi
    motion_px = int(mask.sum())
    frac = motion_px / roi_px if roi_px else 0.0
    return mask, {"reason": "", "roi_px": roi_px, "motion_px": motion_px,
                  "motion_frac": round(frac, 5)}


def classify(stats, info):
    """Return (verdict, confidence, reasoning).

    Verdicts: no_background / no_animal / orange_cat / no_orange /
              possible_intruder / probably_resident
    """
    if info.get("reason"):
        return "no_background", "none", info["reason"]

    if info["motion_frac"] < MIN_MOTION_FRACTION:
        return ("no_animal", "medium",
                f"only {info['motion_px']} px changed "
                f"({info['motion_frac'] * 100:.2f}% of ROI), below "
                f"{MIN_MOTION_FRACTION * 100:.2f}% floor")

    if stats["is_ir"]:
        uniform = stats["luma_std"] < LUMA_STD_THRESHOLD
        return (
            "possible_intruder" if uniform else "probably_resident",
            "low",
            f"IR mode, no colour; luma_std={stats['luma_std']} vs "
            f"{LUMA_STD_THRESHOLD} over {info['motion_px']} moving px "
            f"(UNCALIBRATED)",
        )

    orange = stats["warm_pct"] > WARM_PCT_THRESHOLD
    return (
        "orange_cat" if orange else "no_orange",
        "medium",
        f"colour mode; {stats['warm_pct']}% of {info['motion_px']} moving px "
        f"read as ginger, threshold {WARM_PCT_THRESHOLD}%",
    )
