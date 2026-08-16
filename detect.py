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

# The camera sits at the door, so many motion frames are extreme close-ups in
# which the animal fills the view. There is then no visible background to
# compare it against and the exposure has blown out, so the frame cannot be
# scored at all. Measured on real clips, only frames in this range are usable.
ISO_MIN = 0.015
ISO_MAX = 0.25

# --- Verdict thresholds -------------------------------------------------
WARM_PCT_THRESHOLD = 8.0   # daylight: how much of the animal reads as ginger

# IR discriminator, calibrated against 9 labelled clips from 2026-08-16.
# Colour is entirely absent at night, but the orange tabby reflects infrared
# far more strongly than the background it stands against, while black fur
# absorbs it. Measured over frames where the cat was properly isolated:
#
#     orange tabby     rel_bright 1.226 - 1.660
#     black and white  rel_bright 0.716 - 0.915
#
# a 29% margin, far wider than any contrast measure (cv managed 12%, and
# whole-frame luminance spread separated nothing at all). Threshold sits below
# the midpoint because this system alerts rather than gates: a false alarm
# costs a notification, a miss costs a meal, and neither locks a cat outside.
IR_REL_BRIGHT_THRESHOLD = 1.05

# Secondary, same calibration: tabby 0.224-0.381, b&w 0.428-0.645. Used only
# to qualify confidence, since its margin is narrower.
IR_CV_THRESHOLD = 0.42


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


# The model is several megabytes and gets read twice per frame while scoring a
# burst -- hundreds of megabytes of pointless disk reads per event. Keep it in
# memory and reload only when the file on disk actually changes.
_bg_cache = {}


def load_background(camera):
    path = _bg_path(camera)
    if not os.path.exists(path):
        _bg_cache.pop(camera, None)
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None

    cached = _bg_cache.get(camera)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        bg = np.load(path)
    except (ValueError, OSError):
        return None
    _bg_cache[camera] = (mtime, bg)
    return bg


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
    try:
        _bg_cache[camera] = (os.path.getmtime(_bg_path(camera)), bg)
    except OSError:
        pass
    return bg


def normalise_gain(frame, bg):
    """Scale a frame so its median luminance matches the background's.

    The camera re-exposes when a large pale animal walks in -- mean frame
    luminance was seen swinging from 59 to 156 across real clips. Without this
    correction a plain subtraction marks every pixel as changed, and the mask
    describes the room instead of the cat.
    """
    fg = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bgg = cv2.cvtColor(bg.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    fm, bm = float(np.median(fg)), float(np.median(bgg))
    if fm < 1:
        return fg
    return np.clip(fg * (bm / fm), 0, 255)


def motion_mask(frame, camera):
    """The animal: largest connected region of change inside the ROI.

    Returns (mask, info). mask is None when there is no background yet.
    """
    bg = load_background(camera)
    roi = roi_mask(frame.shape, camera)
    roi_px = int(roi.sum())
    base = {"roi_px": roi_px, "motion_px": 0, "motion_frac": 0.0,
            "iso_frac": 0.0, "usable": 0}

    if bg is None or bg.shape != frame.shape:
        return None, {**base, "reason": "no background model yet"}

    fg = normalise_gain(frame, bg)
    bgg = cv2.cvtColor(bg.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    diff = cv2.absdiff(fg, bgg).astype(np.uint8)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    binary = ((diff > DIFF_THRESHOLD) & roi).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    n, labelled, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if n <= 1:
        return binary.astype(bool), {**base, "reason": ""}

    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = labelled == biggest
    motion_px = int(mask.sum())
    frac = motion_px / roi_px if roi_px else 0.0
    iso = motion_px / float(frame.shape[0] * frame.shape[1])

    return mask, {"reason": "", "roi_px": roi_px, "motion_px": motion_px,
                  "motion_frac": round(frac, 5), "iso_frac": round(iso, 5),
                  "usable": int(ISO_MIN <= iso <= ISO_MAX)}


def ir_features(frame, mask, camera):
    """Brightness of the animal relative to the background behind it."""
    bg = load_background(camera)
    if bg is None or bg.shape != frame.shape or not mask.any():
        return None
    grey = normalise_gain(frame, bg)
    bgg = cv2.cvtColor(bg.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    cat = grey[mask]
    behind = bgg[mask]
    mean = float(cat.mean())
    return {
        "rel_bright": round(mean / max(float(behind.mean()), 1.0), 3),
        "cv": round(float(cat.std()) / max(mean, 1.0), 3),
    }


DECISIVE = ("orange_cat", "probably_resident", "no_orange")


def vote(verdicts):
    """Combine per-frame verdicts into one answer.

    Only decisive frames count. Frames where the animal was too close to
    measure, or where nothing moved, are abstentions rather than evidence --
    counting them would let a cat pressed against the lens outvote the frames
    that could actually be scored.
    """
    tally = {}
    for v in verdicts:
        if v in DECISIVE:
            tally[v] = tally.get(v, 0) + 1

    total = sum(tally.values())
    if not total:
        return None, 0.0, tally

    winner = max(tally, key=tally.get)
    return winner, tally[winner] / total, tally


def classify(stats, info, ir=None):
    """Return (verdict, confidence, reasoning).

    Verdicts: no_background / no_animal / unmeasurable /
              orange_cat / no_orange / probably_resident
    """
    if info.get("reason"):
        return "no_background", "none", info["reason"]

    if info["motion_frac"] < MIN_MOTION_FRACTION:
        return ("no_animal", "medium",
                f"only {info['motion_px']} px changed "
                f"({info['motion_frac'] * 100:.2f}% of ROI), below "
                f"{MIN_MOTION_FRACTION * 100:.2f}% floor")

    if stats["is_ir"]:
        # Colour is gone; go on how brightly the animal returns infrared.
        if not info.get("usable"):
            return ("unmeasurable", "none",
                    f"animal fills {info['iso_frac'] * 100:.1f}% of frame "
                    f"(usable range {ISO_MIN * 100:.1f}-{ISO_MAX * 100:.0f}%) "
                    "-- too close to compare against its background")
        if ir is None:
            return "unmeasurable", "none", "IR features unavailable"

        bright = ir["rel_bright"] > IR_REL_BRIGHT_THRESHOLD
        agrees = (ir["cv"] < IR_CV_THRESHOLD) == bright
        return (
            "orange_cat" if bright else "probably_resident",
            "medium" if agrees else "low",
            f"IR mode; rel_bright={ir['rel_bright']} vs "
            f"{IR_REL_BRIGHT_THRESHOLD} (tabby 1.23-1.66, b&w 0.72-0.92), "
            f"cv={ir['cv']} {'agrees' if agrees else 'DISAGREES'}",
        )

    orange = stats["warm_pct"] > WARM_PCT_THRESHOLD
    return (
        "orange_cat" if orange else "no_orange",
        "medium",
        f"colour mode; {stats['warm_pct']}% of {info['motion_px']} moving px "
        f"read as ginger, threshold {WARM_PCT_THRESHOLD}%",
    )
