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
# outside: the door and the concrete approach. Raised from y > 0.45 to
#          y > 0.30 on 2026-08-17, which costs nothing and recovers animals
#          further from the lens.
#
#          The x < 0.63 edge is deliberate and should stay. It looks obsolete
#          -- it was picked to exclude the 24%-warm wooden gate back when
#          whole frames were scored, and only moving pixels are scored now --
#          but removing it was tested against a day of live events and is
#          clearly wrong: the strip from x=0.63 to 0.80 is the open doorway,
#          which frames the sunlit yard, the swinging gate, and the path
#          people walk. Opening the ROI to x < 0.80 took the day's false
#          ginger verdicts from 1 to 11, including a person in brown boots.
#          The labelled clips cannot see this -- they were all exported around
#          animals -- so it stays a live-log result.
# inside:  the pegboard wall with the flap, plus the floor in front of it.
ROI = {
    "outside": (0.00, 0.30, 0.63, 1.00),
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
# Daylight: how much of the animal reads as ginger. Necessary but not
# sufficient -- see WARM_MARGIN_THRESHOLD below.
#
# Raising this was the obvious response to the dawn false positives and it is
# a dead end, recorded so it is not retried. Per-clip peaks over the 30
# labelled clips:
#
#     orange intruder    25.7 - 96.6
#     residents, possum   0.0 - 22.8   (the top of that range is sunrise)
#
# which looks separable at ~24% until you remember the vote is per frame, not
# per clip. A clip peaking at 25.7% has only a handful of frames up there, so
# a threshold anywhere near the peak leaves nothing to vote with: at 20% the
# harness lost two of the five confirmed intruder clips outright. 8% keeps
# the frames; the surround margin supplies the precision.
WARM_PCT_THRESHOLD = 8.0

# How much more ginger the animal is than the scene immediately around it, in
# percentage points. See `warm_margin` for the measured populations: intruders
# 8.3-48.9, residents and possum at most 2.5. 5.0 sits in that gap. Do not
# raise WARM_PCT_THRESHOLD to compensate for anything here -- that was tried
# and it costs real intruder clips.
#
# This covers one of the two dawn failures and NOT the other. It catches a
# resident *animal* reddened by sunrise, which is what the labelled 06:14:45
# clip is. It does nothing for sunrise falling on an empty patio: live on
# 2026-08-18 the planter and wall scored warm 48-61% at a margin of 27-47pp,
# sailing past this test, and only `bg_corr` rejected them. Neither feature
# is redundant; do not drop one because the other looks sufficient.
WARM_MARGIN_THRESHOLD = 5.0

# Above this, the "motion" is not an object at all -- it is the background
# with the light changed on it. See `bg_correlation`: sunlight measured
# 0.84-0.98, every labelled animal 0.35 or below. 0.70 sits in that gap,
# nearer the animals because a missed frame is cheap (fifteen of them vote)
# while a sunlit patch scoring as a cat is not.
BG_CORR_THRESHOLD = 0.70

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
            "iso_frac": 0.0, "usable": 0, "bg_corr": None,
            "warm_margin": None}

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
                  "usable": int(ISO_MIN <= iso <= ISO_MAX),
                  # Computed here because the two grey images are already in
                  # hand; recomputing them per frame doubled the event cost.
                  "bg_corr": bg_correlation(fg, bgg, mask),
                  "warm_margin": warm_margin(frame, mask, camera)}


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


def bg_correlation(grey, bgg, mask):
    """How much the moving region still looks like the scene behind it.

    Every other feature here asks "what does the moving thing look like". This
    one asks whether it is a *thing* at all -- the question the classifier was
    missing, and the reason it spent 2026-08-17 reporting a ginger cat on an
    empty patio.

    A patch of sunlight does not replace the scene, it rescales it: sunlit
    concrete is the same concrete, same grain in the same places, only
    brighter, so frame and background stay strongly correlated across those
    pixels. An animal *occludes* the scene, substituting its own unrelated
    structure, and the correlation collapses. Measured:

        sunlight on the patio (12 live false positives)  0.84 - 0.98
        cats and possum, 30 labelled clips               -0.03 - 0.35

    Note this is not another of the contrast features that all failed (see the
    dead ends in AGENTS.md). Those measured the animal against itself and so
    normalised away the signal. This measures the animal against the scene it
    is standing in, which is information none of them had.

    Returns Pearson r over the masked pixels, or None where it is undefined --
    an empty mask, or a patch too flat to have structure worth correlating.
    """
    if not mask.any():
        return None
    a, b = grey[mask], bgg[mask]
    # Too few pixels, or a background patch with no texture: r would be noise.
    # Returning None abstains rather than inventing a low correlation, which
    # would read as "definitely an object".
    if a.size < 50 or a.std() < 1e-6 or b.std() < 2.0:
        return None
    return round(float(np.corrcoef(a, b)[0, 1]), 3)


# Saturation floor for "this pixel is ginger".
#
# 90 is right for a motion mask and wrong for a detector box, and the
# difference is not about the cat -- it is about what else is in the
# selection. A motion mask is half wall, so the test has to be strict enough
# that wall cannot pass it. A detector box is nearly all animal, so it can
# afford a floor low enough to survive dim light at range, which is exactly
# where the old one failed: the stray measured ~3% ginger at S>90 on the
# night it raided twice, against 29-55% at S>40.
SAT_MIN_MASK = 90
SAT_MIN_BOX = 40


def warm_mask(img, sat_min=SAT_MIN_MASK):
    """Pixels that read as ginger: warm hue, saturated, not in shadow.

    Hue wraps at both ends of the red range. Lives here rather than in
    `capture.measure` because the surround comparison needs the same
    predicate, and two copies of it would drift apart.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return ((h <= 22) | (h >= 170)) & (s > sat_min) & (v > 50)


def surround_mask(mask, camera, shape, thickness=25):
    """A band of scene hugging the animal, for same-frame comparison.

    `warm_pct` on its own is a measurement of the light as much as of the cat.
    At sunrise the whole patio goes orange for twenty minutes, and a
    black-and-white resident standing in it reads 10-17% ginger -- with the
    wall behind it reading much the same. A ginger cat is orange *against* its
    surroundings; a resident at dawn is orange *along with* them.

    Comparing against the stored background will not do this, because the
    background model is minutes old and was built under different light --
    that is the whole problem. The comparison has to happen inside the one
    frame, so this returns the ring of scene immediately around the animal.
    """
    band = cv2.dilate(mask.astype(np.uint8),
                      np.ones((thickness, thickness), np.uint8))
    ring = band.astype(bool) & ~mask & roi_mask(shape, camera)
    return ring


def warm_margin(frame, mask, camera):
    """Ginger on the animal, minus ginger on the scene right beside it.

    This is the measurement that finally separates a ginger cat from a
    resident standing in orange sunrise light, which raw `warm_pct` cannot do
    at any threshold -- the two populations overlap (residents reached 22.8%
    at dawn, one intruder only 25.7%). Against the local surround they come
    apart cleanly. Measured over the 30 labelled clips, per-clip 75th
    percentile:

        orange intruder      8.3 - 48.9
        residents, possum   -0.1 -  2.5   (2.5 is the dawn false positive)

    Returns None when there is no ring worth measuring -- an animal at the
    frame edge, or one that fills its surroundings.
    """
    ring = surround_mask(mask, camera, frame.shape)
    if ring.sum() < 200 or not mask.any():
        return None
    warm = warm_mask(frame)
    return round(float(warm[mask].mean() - warm[ring].mean()) * 100, 3)


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

    # Is it an object, or is it the light moving? This has to come before both
    # the colour and the IR test, because both of them describe whatever the
    # mask contains without ever asking whether it is a thing.
    corr = info.get("bg_corr")
    if corr is not None and corr > BG_CORR_THRESHOLD:
        return ("no_animal", "high",
                f"moving region still matches the background behind it "
                f"(r={corr} > {BG_CORR_THRESHOLD}) -- changing light, "
                "not an object")

    # An animal too close to the lens blots out the background and blows the
    # exposure; one too far away is a handful of pixels. Neither can be
    # scored. This applied to the IR path only, which is why a 1500 px scrap
    # of sunlit doorway could still be declared a ginger cat in broad
    # daylight -- twelve times on 2026-08-17.
    if not info.get("usable"):
        return ("unmeasurable", "none",
                f"animal fills {info['iso_frac'] * 100:.2f}% of frame "
                f"(usable range {ISO_MIN * 100:.1f}-{ISO_MAX * 100:.0f}%) "
                "-- too close or too small to compare against its background")

    if stats["is_ir"]:
        # Colour is gone; go on how brightly the animal returns infrared.
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

    # Two conditions, because either one alone is known to fail. Absolute
    # warmth alone calls a resident ginger at sunrise; the margin alone would
    # trust a couple of stray warm pixels that happen to beat a grey surround.
    margin = info.get("warm_margin")
    warm_enough = stats["warm_pct"] > WARM_PCT_THRESHOLD
    stands_out = margin is not None and margin > WARM_MARGIN_THRESHOLD
    reason = (f"colour mode; {stats['warm_pct']}% of {info['motion_px']} "
              f"moving px read as ginger (threshold {WARM_PCT_THRESHOLD}%), "
              f"margin over surround "
              f"{'n/a' if margin is None else f'{margin}pp'} "
              f"(threshold {WARM_MARGIN_THRESHOLD}pp)")

    if warm_enough and stands_out:
        return "orange_cat", "medium", reason
    # Warm but no warmer than its surroundings: the light is orange, not the
    # animal. This is the dawn case, and it is the one that would otherwise
    # spray a resident.
    if warm_enough and margin is not None:
        return "no_orange", "medium", reason + " -- lit warm, not ginger"
    return "no_orange", "medium", reason


# --- Detector path -------------------------------------------------------
# Thresholds for scoring inside an animal detector's box rather than a motion
# mask. Set from measurement in `evaluate.py --detector`; see the numbers
# recorded beside each one.

def box_features(frame, box, pad=30):
    """Ginger inside the animal's box, and how much it beats its surroundings.

    The margin is the same idea as `warm_margin` on a motion mask and exists
    for the same reason: at sunrise the light reddens everything, so absolute
    warmth cannot tell a ginger cat from a black-and-white one standing in
    orange light. Here the comparison is the band of scene just outside the
    box, which is the closest available sample of "what the light is doing
    right now" without another model to keep current.
    """
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None

    warm = warm_mask(frame, SAT_MIN_BOX)
    inside = warm[y0:y1, x0:x1]

    ring = np.zeros((h, w), bool)
    ring[max(0, y0 - pad):min(h, y1 + pad),
         max(0, x0 - pad):min(w, x1 + pad)] = True
    ring[y0:y1, x0:x1] = False

    warm_pct = float(inside.mean()) * 100
    margin = (warm_pct - float(warm[ring].mean()) * 100
              if ring.sum() > 200 else None)
    return {"warm_pct": round(warm_pct, 3),
            "warm_margin": None if margin is None else round(margin, 3),
            "box_frac": round((x1 - x0) * (y1 - y0) / float(w * h), 5)}


# Measured over 5 labelled intruder clips, 3 confirmed live raids, 24 resident
# clips and the possum, all scored inside a detector box:
#
#                        warm%        margin
#   intruder          30.7 - 57.1   30.1 - 54.7
#   residents          0.0 - 30.4   -6.8 - 14.7
#   possum                    1.3          0.5
#
# The margin is what separates them -- a 2x gap, 14.7 to 30.1 -- and warm% on
# its own does NOT: a resident at sunrise reached 30.4 against the intruder's
# floor of 30.7. Both are required anyway, because the margin is a difference
# of two small numbers when the box is tiny and warm% keeps that honest.
BOX_WARM_THRESHOLD = 25.0
BOX_MARGIN_THRESHOLD = 20.0


def classify_detection(feats):
    """Verdict for one frame in which an animal was detected.

    Returns (verdict, confidence, reasoning). `feats` is `box_features`
    output; None means the box was unusable and the frame abstains.
    """
    if feats is None:
        return "unmeasurable", "none", "detector box too small to measure"

    warm, margin = feats["warm_pct"], feats["warm_margin"]
    if margin is None:
        return ("unmeasurable", "none",
                f"{warm}% ginger in box but no surround to compare against "
                "-- animal at the frame edge")

    reason = (f"detector box; {warm}% ginger (threshold "
              f"{BOX_WARM_THRESHOLD}%), {margin}pp over surround "
              f"(threshold {BOX_MARGIN_THRESHOLD}pp)")
    if warm > BOX_WARM_THRESHOLD and margin > BOX_MARGIN_THRESHOLD:
        return "orange_cat", "medium", reason
    return "no_orange", "medium", reason
