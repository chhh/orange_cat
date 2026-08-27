"""Replay every labelled clip and known event through the DETECTOR path.

Companion to `evaluate.py`, which exercises the older motion-mask path. Same
discipline applies: measure before changing a threshold, and read
`validating-detector-changes` in memory first.

One design point this harness exists to check. A frame with no detection
ABSTAINS -- it does not vote "not orange". The detector finds the animal in as
few as 1 of 15 frames on a real raid (01:09 on 2026-08-19), so counting
no-detection frames as evidence of absence would drown every true positive in
its own burst. The cost is that a single spurious detection can carry a
verdict, which is why both colour thresholds have to clear a 2x gap.

Run:  uv run evaluate_detector.py [--verbose]
"""

import csv
import glob
import os
import re
import sys
from collections import Counter

import cv2
import numpy as np

import animal
import detect

CLIP_ROOT = "samples/labelled/cats"
WORK_W, WORK_H = 1024, 576
TRUTH = {"orange-intruder-cat": "orange_cat",
         "our-cats": "not_orange",
         "possum": "not_orange"}

# Confirmed by Dima against Protect's own clips.
LIVE_RAIDS = ["20260819-010914", "20260819-044737", "20260819-045711"]


def clip_time(path):
    m = re.search(r"(\d\d\.\d\d\.\d\d) PDT -", os.path.basename(path))
    return m.group(1).replace(".", ":") if m else os.path.basename(path)[:12]


def clip_frames(path, limit=15):
    cap = cv2.VideoCapture(path)
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(cv2.resize(f, (WORK_W, WORK_H)))
    cap.release()
    if not out:
        return []
    step = max(1, len(out) // limit)
    return out[::step][:limit]


def score_frames(frames):
    """(verdict, confidence, tally, diagnostics) for a burst."""
    verdicts, warms, margins, dets = [], [], [], 0
    for f in frames:
        if f is None:
            continue
        d = animal.best_box(f)
        if d is None:
            verdicts.append("no_animal")   # abstains: not in DECISIVE
            continue
        dets += 1
        feats = detect.box_features(f, d["box"])
        v, _, _ = detect.classify_detection(feats)
        verdicts.append(v)
        if feats:
            warms.append(feats["warm_pct"])
            if feats["warm_margin"] is not None:
                margins.append(feats["warm_margin"])
    winner, conf, tally = detect.vote(verdicts)
    return winner, conf, tally, {
        "det": dets, "n": len(frames),
        "warm": float(np.median(warms)) if warms else float("nan"),
        "margin": float(np.median(margins)) if margins else float("nan"),
    }


def event_frames(ts):
    d = glob.glob(f"frames/events/outside-{ts}*/")
    if d:
        return [cv2.imread(p) for p in sorted(glob.glob(d[0] + "*.jpg"))]
    f = glob.glob(f"frames/events/outside-{ts}*.jpg")
    return [cv2.imread(p) for p in f if "posted" not in p]


def main():
    if not animal.available():
        raise SystemExit(f"no detector model ({animal.MODEL_PATH})")
    misses, false_alarms = [], []
    counts = Counter()

    for label, expected in TRUTH.items():
        clips = sorted(glob.glob(os.path.join(CLIP_ROOT, label, "*.mp4")))
        if not clips:
            continue
        print(f"\n{label}  ({len(clips)} clips, expect {expected})")
        print(f"  {'time':<9} {'verdict':<14} {'conf':>5} {'det':>6} "
              f"{'warm%':>7} {'margin':>7}  votes")
        for p in clips:
            w, c, t, d = score_frames(clip_frames(p))
            shown = w or "abstain"
            ok = (w == "orange_cat") == (expected == "orange_cat")
            counts[(label, "hit" if ok else "miss")] += 1
            if not ok:
                (misses if expected == "orange_cat"
                 else false_alarms).append(clip_time(p))
            print(f"  {clip_time(p):<9} {shown:<14} {c:>5.2f} "
                  f"{d['det']:>3}/{d['n']:<2} {d['warm']:>7.1f} "
                  f"{d['margin']:>7.1f}{'   ' if ok else ' <-'} {t}")

    print("\nconfirmed live raids (2026-08-19) — all logged no_animal by the "
          "old path")
    for ts in LIVE_RAIDS:
        frames = [f for f in event_frames(ts) if f is not None]
        if not frames:
            print(f"  {ts[9:]}  no frames on disk")
            continue
        w, c, t, d = score_frames(frames)
        hit = w == "orange_cat"
        counts[("live-raids", "hit" if hit else "miss")] += 1
        if not hit:
            misses.append(ts[9:])
        print(f"  {ts[9:]}     {w or 'abstain':<14} {c:>5.2f} "
              f"{d['det']:>3}/{d['n']:<2} {d['warm']:>7.1f} "
              f"{d['margin']:>7.1f}{'   ' if hit else ' <-'} {t}")

    print("\n--- summary " + "-" * 46)
    for label in list(TRUTH) + ["live-raids"]:
        hit = counts[(label, "hit")]
        tot = hit + counts[(label, "miss")]
        if tot:
            print(f"  {label:<22} {hit}/{tot} correct")
    if misses:
        print(f"  MISSED:       {', '.join(misses)}")
    if false_alarms:
        print(f"  FALSE ALARMS: {', '.join(false_alarms)}")
    if not misses and not false_alarms:
        print("  no misses, no false alarms")


if __name__ == "__main__":
    main()
