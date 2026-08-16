"""Find an IR discriminator that actually separates the two cats.

Ground truth from Dima: every resident cat is black and white; the intruder is
an orange tabby. Under infrared there is no colour at all (warm% is 0.00 on
every clip), so the only thing left is luminance structure.

The naive version -- luminance std over the whole motion mask -- does not work.
Measured against these labels it gives 29.9-63.7 for the tabby and 16.3-72.5
for the black-and-white cats: total overlap, with the single lowest score
belonging to a black-and-white cat.

Two reasons, both fixed here:

  * The mask was not isolating the cat. Whole-frame motion covered 83-95% of
    the frame on half the clips, so the "cat" statistic described the room.
  * The camera re-exposes when a large pale animal fills the view. Mean frame
    luminance swings from 59 to 156 across clips, so a plain background
    subtraction flags every pixel.

So: normalise gain before differencing, then keep only the largest connected
component. Then score several candidate features and see which one actually
splits the labels.

Run:  uv run calibrate.py
"""

import glob
import os
import re

import cv2
import numpy as np

# Ground truth, confirmed by Dima.
LABELS = {
    "00.28.02": "tabby", "00.28.34": "tabby", "00.56.11": "tabby",
    "03.15.52": "tabby", "06.31.42": "tabby", "06.44.16": "tabby",
    "02.33.11": "bw", "02.34.47": "bw", "02.41.32": "bw", "03.59.08": "bw",
}

WORK_W, WORK_H = 960, 540
DIFF_THRESHOLD = 25
MIN_BLOB_PX = 4000


def clip_label(path):
    m = re.search(r"(\d\d\.\d\d\.\d\d) PDT -", os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)[:12]


def read_frames(path, limit=400):
    cap = cv2.VideoCapture(path)
    out = []
    while len(out) < limit:
        ok, f = cap.read()
        if not ok:
            break
        out.append(cv2.resize(f, (WORK_W, WORK_H)))
    cap.release()
    return out


def background(frames):
    """Per-pixel median. Cats move, so the median is mostly empty scene."""
    sub = frames[:: max(1, len(frames) // 30)][:30]
    return np.median(np.stack(sub), axis=0).astype(np.uint8)


def normalise_gain(frame, bg):
    """Scale the frame so its median luminance matches the background's.

    Cancels the camera's auto-exposure response, which otherwise makes every
    pixel look 'changed' the moment a bright animal walks in.
    """
    fg = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bgg = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY).astype(np.float32)
    fm, bm = np.median(fg), np.median(bgg)
    if fm < 1:
        return fg
    return np.clip(fg * (bm / fm), 0, 255)


def cat_mask(frame, bg):
    """Largest connected blob of change, after gain normalisation."""
    fg = normalise_gain(frame, bg)
    bgg = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY).astype(np.float32)
    diff = cv2.absdiff(fg, bgg).astype(np.uint8)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    binary = (diff > DIFF_THRESHOLD).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    n, lab, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if n <= 1:
        return None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[biggest, cv2.CC_STAT_AREA] < MIN_BLOB_PX:
        return None
    # A blob covering most of the frame is an exposure artefact, not a cat.
    if stats[biggest, cv2.CC_STAT_AREA] > 0.55 * WORK_W * WORK_H:
        return None
    return lab == biggest


def features(frame, mask, bg):
    """Candidate discriminators, computed over the cat's pixels only."""
    grey = normalise_gain(frame, bg).astype(np.uint8)
    px = grey[mask].astype(np.float32)
    if px.size < MIN_BLOB_PX:
        return None

    mean, std = float(px.mean()), float(px.std())
    lo, hi = np.percentile(px, [5, 95])

    # What the scene looks like behind the animal, same pixels, no cat.
    bg_grey = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    bg_behind = float(bg_grey[mask].astype(np.float32).mean())

    # Self-normalised, so overall exposure cancels out.
    norm = px / max(mean, 1.0)
    bright = float((norm > 1.35).mean())
    dark = float((norm < 0.65).mean())

    # Otsu separability: how cleanly the pixels split into two populations.
    # A black-and-white cat should split well; a tabby should not.
    hist = cv2.calcHist([grey], [0], mask.astype(np.uint8), [64], [0, 256])
    hist = (hist.ravel() / max(hist.sum(), 1)).astype(np.float64)
    idx = np.arange(64, dtype=np.float64)
    total_mu = float((hist * idx).sum())
    w0 = np.cumsum(hist)
    mu0 = np.cumsum(hist * idx)
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (total_mu * w0 - mu0) ** 2 / (w0 * (1 - w0))
    between = np.nan_to_num(between)
    variance = float((hist * (idx - total_mu) ** 2).sum())
    separability = float(between.max() / variance) if variance > 0 else 0.0

    # Spatial scale of the markings -- the point the histogram features miss.
    # Black-and-white patches are large and contiguous, so their contrast
    # survives heavy blurring. Tabby stripes are fine and average away. The
    # blur radius scales with the animal's apparent size so distance does not
    # decide the answer.
    span = max(8, int(np.sqrt(mask.sum()) / 6))
    k = span * 2 + 1
    coarse = cv2.GaussianBlur(grey, (k, k), 0)
    coarse_px = coarse[mask].astype(np.float32)
    coarse_std = float(coarse_px.std())

    return {
        "px": int(px.size),
        "luma_std": round(std, 1),
        "cv": round(std / max(mean, 1), 3),
        "p95_p5": round(float(hi - lo), 1),
        "bright_frac": round(bright, 3),
        "dark_frac": round(dark, 3),
        "two_tone": round(min(bright, dark), 3),
        "otsu_sep": round(separability, 3),
        "coarse_cv": round(coarse_std / max(mean, 1), 3),
        "coarse_ratio": round(coarse_std / max(std, 1), 3),
        # Dave's observation: the tabby simply looks brighter under IR. Every
        # feature above normalises the mean away, so none of them could see it.
        # Comparing the animal against the background it is standing in front
        # of cancels exposure without discarding the level.
        "cat_mean": round(mean, 1),
        "rel_bright": round(mean / max(bg_behind, 1.0), 3),
        "contrast_bg": round((mean - bg_behind) / max(bg_behind, 1.0), 3),
    }


# The camera sits at the door, so most motion frames are extreme close-ups
# where the animal fills the view: exposure blows out, no background is left
# visible, and the mask stops meaning anything. Only frames where the cat is
# a modest fraction of the scene can be measured against its surroundings.
ISO_MIN = 0.015
ISO_MAX = 0.25


def best_frame(path, collect=False):
    frames = read_frames(path)
    if not frames:
        return None
    bg = background(frames)
    total = WORK_W * WORK_H
    usable = []
    for f in frames:
        m = cat_mask(f, bg)
        if m is None:
            continue
        frac = m.sum() / total
        if not (ISO_MIN <= frac <= ISO_MAX):
            continue
        feat = features(f, m, bg)
        if feat is None:
            continue
        usable.append(feat)
    if not usable:
        return None
    if collect:
        return usable
    # Median across usable frames rather than one arbitrary frame.
    out = {}
    for k in usable[0]:
        out[k] = round(float(np.median([u[k] for u in usable])), 3)
    out["n_frames"] = len(usable)
    return out


def main():
    rows = []
    for path in sorted(glob.glob("samples/videos/*.mp4")):
        name = clip_label(path)
        label = LABELS.get(name, "?")
        feat = best_frame(path)
        if feat is None:
            print(f"  {name}  {label:5s}  no usable cat blob")
            continue
        rows.append((name, label, feat))

    keys = ["n_frames", "cat_mean", "rel_bright", "contrast_bg", "cv", "coarse_cv"]
    header = f"{'clip':>10} {'label':>6} {'px':>7} " + " ".join(f"{k:>11}" for k in keys)
    print(header)
    print("-" * len(header))
    for name, label, f in sorted(rows, key=lambda r: r[1]):
        print(f"{name:>10} {label:>6} {f['px']:>7} " +
              " ".join(f"{f[k]:>11}" for k in keys))

    print("\nseparation by feature (want the two ranges not to overlap):")
    for k in keys:
        tab = [f[k] for _, l, f in rows if l == "tabby"]
        bw = [f[k] for _, l, f in rows if l == "bw"]
        if not tab or not bw:
            continue
        gap = min(bw) - max(tab)
        gap2 = min(tab) - max(bw)
        verdict = "CLEAN" if gap > 0 or gap2 > 0 else "overlaps"
        print(f"  {k:>12}  tabby {min(tab):8.3f}-{max(tab):<8.3f}  "
              f"bw {min(bw):8.3f}-{max(bw):<8.3f}   {verdict}")


if __name__ == "__main__":
    main()
