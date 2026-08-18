"""Replay the labelled clips through the live detection path.

Every threshold in `detect.py` has at some point been justified by a number in
AGENTS.md, and every one of those numbers was produced by a throwaway script
that no longer exists. This is that script, kept.

It runs `samples/labelled/cats/<label>/*.mp4` through the *same* functions the
server uses -- `detect.motion_mask`, `capture.measure`, `detect.classify`,
`detect.vote` -- and prints a per-clip verdict plus a confusion summary. So a
threshold change can be checked against a night of hand-sorted ground truth
before it goes anywhere near the cat door.

The one thing it cannot reproduce exactly is the *background model*. Live, that
is a long-lived exponential average in `frames/bg_outside.npy`, minutes to
hours old and trained on quiet scenes. Here it is pooled from neighbouring
clips -- see `pooled_backgrounds`, and read that docstring before trusting any
number this prints, because the obvious alternative silently shrinks the very
animals it is supposed to measure. Clips are resized to the live 1024x576
first, because the morphology kernels and pixel thresholds are tuned at that
scale.

Slow illumination drift is still under-represented: the pool is drawn from
clips minutes away, so the dawn sun has moved less here than against a model
that may be an hour stale. Treat dawn false positives seen live as worse than
whatever this reports.

Run:  uv run evaluate.py [--verbose] [--camera outside]
"""

import glob
import os
import re
import shutil
import sys
from collections import Counter

import cv2
import numpy as np

import capture
import detect

CLIP_ROOT = "samples/labelled/cats"
WORK_W, WORK_H = 1024, 576

# Directory name -> what the verdict should be. `orange_cat` is the only
# actionable verdict; for everything else we only care that it is NOT that.
TRUTH = {
    "orange-intruder-cat": "orange_cat",
    "our-cats": "not_orange",
    "possum": "not_orange",
}


def clip_time(path):
    """The '00.39.37' start stamp Protect puts in the exported filename."""
    m = re.search(r"(\d\d\.\d\d\.\d\d) PDT -", os.path.basename(path))
    return m.group(1).replace(".", ":") if m else os.path.basename(path)[:12]


def read_frames(path, limit=300):
    cap = cv2.VideoCapture(path)
    out = []
    while len(out) < limit:
        ok, f = cap.read()
        if not ok:
            break
        out.append(cv2.resize(f, (WORK_W, WORK_H)))
    cap.release()
    return out


def median_background(frames):
    """Per-pixel median over a spread of frames: mostly empty scene."""
    sub = frames[:: max(1, len(frames) // 30)][:30]
    return np.median(np.stack(sub), axis=0).astype(np.float32)


def clip_minutes(path):
    """Clip start as minutes into the night, so 21:00 -> 06:00 is contiguous."""
    m = re.search(r"(\d\d)\.(\d\d)\.(\d\d) PDT -", os.path.basename(path))
    if not m:
        return 0.0
    hh, mm, ss = (int(x) for x in m.groups())
    mins = hh * 60 + mm + ss / 60.0
    return mins + 24 * 60 if hh < 12 else mins  # small hours follow the evening


def pooled_backgrounds(clips, neighbours=9):
    """An animal-free background per clip, taken from its neighbours in time.

    A clip's own median is the obvious background and it is *wrong* for exactly
    the clips that matter most. A 10-second export of a cat that sits near the
    door is a cat in nearly every frame, so the median contains the cat, the
    difference against it is a thin outline, and the animal measures as a
    fraction of its true size. Clip 00:40:25 -- a confirmed intruder -- scored
    iso 0.003 that way, indistinguishable from a patch of moving sunlight, and
    that is an artefact of this harness rather than anything the live path sees.
    Live, the model is an average of quiet scenes minutes old.

    So: one frame from each clip, and for any given clip take the per-pixel
    median of the frames belonging to its nearest neighbours in time. The
    camera is fixed, the neighbours share its lighting to within a few minutes,
    and a median over nine of them removes whichever animals happen to be in
    shot. Falls back to the clip's own median if there is nothing to pool.
    """
    first = {}
    for path in clips:
        cap = cv2.VideoCapture(path)
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            first[path] = cv2.resize(frame, (WORK_W, WORK_H))

    out = {}
    for path in clips:
        others = sorted((p for p in first if p != path),
                        key=lambda p: abs(clip_minutes(p) - clip_minutes(path)))
        pool = [first[p] for p in others[:neighbours]]
        if len(pool) >= 3:
            out[path] = np.median(np.stack(pool), axis=0).astype(np.float32)
    return out


def score_clip(path, camera, bg_dir, pooled=None):
    """Return (verdict, confidence, tally, diagnostics) for one clip.

    Writes the clip's background where `detect` expects to find it, so the real
    `motion_mask` / `classify` run unmodified.
    """
    frames = read_frames(path)
    if not frames:
        return None, 0.0, {}, {}

    bg = (pooled or {}).get(path)
    if bg is None:
        bg = median_background(frames)
    np.save(os.path.join(bg_dir, f"bg_{camera}.npy"), bg)
    detect._bg_cache.pop(camera, None)

    verdicts, warm, isos, rels, corrs, rings = [], [], [], [], [], []
    # Skip the first frames: they dominate the median, so the animal is
    # partly baked into its own background there.
    for frame in frames[2:]:
        mask, info = detect.motion_mask(frame, camera)
        if mask is None:
            verdicts.append("no_background")
            continue
        stats = capture.measure(frame, mask)
        ir = detect.ir_features(frame, mask, camera) if stats["is_ir"] else None
        verdict, _, _ = detect.classify(stats, info, ir)
        verdicts.append(verdict)
        if info["motion_frac"] >= detect.MIN_MOTION_FRACTION:
            warm.append(stats["warm_pct"])
            isos.append(info["iso_frac"])
            if info.get("bg_corr") is not None:
                corrs.append(info["bg_corr"])
            ring = detect.surround_mask(mask, camera, frame.shape)
            if ring.sum() > 200:
                rings.append(stats["warm_pct"]
                             - capture.measure(frame, ring)["warm_pct"])
            if ir:
                rels.append(ir["rel_bright"])

    winner, conf, tally = detect.vote(verdicts)
    diag = {
        "frames": len(verdicts),
        "warm_max": max(warm) if warm else 0.0,
        "iso_med": float(np.median(isos)) if isos else 0.0,
        "iso_max": max(isos) if isos else 0.0,
        "rel_med": float(np.median(rels)) if rels else 0.0,
        "corr_med": float(np.median(corrs)) if corrs else float("nan"),
        "corr_p90": float(np.percentile(corrs, 90)) if corrs else float("nan"),
        "ring_max": max(rings) if rings else float("nan"),
        "ring_p75": float(np.percentile(rings, 75)) if rings else float("nan"),
        "abstain": sum(1 for v in verdicts if v not in detect.DECISIVE),
    }
    return winner, conf, tally, diag


def apply_overrides(argv):
    """`--set WARM_PCT_THRESHOLD=8` -- poke a constant in `detect` for one run.

    Attribution is the point. A change set of four edits that scores 30/30
    tells you nothing about which of the four earned it, or whether one of
    them is quietly costing you a clip that another one covers up. This lets
    each be switched off on its own without editing the module.
    """
    for i, arg in enumerate(argv):
        if arg != "--set":
            continue
        name, _, value = argv[i + 1].partition("=")
        if not hasattr(detect, name):
            raise SystemExit(f"detect has no attribute {name!r}")
        current = getattr(detect, name)
        setattr(detect, name, type(current)(value) if
                isinstance(current, (int, float)) and not isinstance(current, bool)
                else value)
        print(f"override: detect.{name} = {getattr(detect, name)!r}")


def main():
    verbose = "--verbose" in sys.argv
    camera = "outside"
    if "--camera" in sys.argv:
        camera = sys.argv[sys.argv.index("--camera") + 1]
    if "--roi" in sys.argv:
        detect.ROI["outside"] = tuple(
            float(x) for x in sys.argv[sys.argv.index("--roi") + 1].split(","))
        print(f"ROI outside = {detect.ROI['outside']}")
    apply_overrides(sys.argv)

    # Per-process, because two replays running at once would otherwise stamp
    # on each other's background file and np.load would read a half-written
    # array. (It does happen: comparing before/after invites parallel runs.)
    bg_dir = os.path.join("frames", f"eval-bg-{os.getpid()}")
    os.makedirs(bg_dir, exist_ok=True)
    # Point detect at a scratch background so a replay can never corrupt the
    # live model the server is using right now.
    real_bg_dir, detect.BG_DIR = detect.BG_DIR, bg_dir

    try:
        counts = Counter()
        misses, false_alarms = [], []

        # Pool across every label: an animal-free background needs neighbours
        # in time, and it does not matter which folder they were sorted into.
        all_clips = sorted(glob.glob(os.path.join(CLIP_ROOT, "*", "*.mp4")))
        pooled = pooled_backgrounds(all_clips)
        print(f"pooled backgrounds for {len(pooled)}/{len(all_clips)} clips")

        for label, expected in TRUTH.items():
            clips = sorted(glob.glob(os.path.join(CLIP_ROOT, label, "*.mp4")))
            if not clips:
                continue
            print(f"\n{label}  ({len(clips)} clips, expect {expected})")
            print(f"  {'time':<9} {'verdict':<18} {'conf':>5} "
                  f"{'warm%':>7} {'isoMed':>7} {'isoMax':>7} {'corr':>6} {'ringP75':>7}  votes")
            for path in clips:
                winner, conf, tally, diag = score_clip(path, camera, bg_dir,
                                                       pooled)
                shown = winner or "abstain"
                actionable = winner == "orange_cat"
                ok = actionable == (expected == "orange_cat")
                counts[(label, "hit" if ok else "miss")] += 1
                if not ok:
                    (misses if expected == "orange_cat"
                     else false_alarms).append(clip_time(path))
                flag = "   " if ok else " <-"
                print(f"  {clip_time(path):<9} {shown:<18} {conf:>5.2f} "
                      f"{diag.get('warm_max', 0):>7.2f} "
                      f"{diag.get('iso_med', 0):>7.4f} "
                      f"{diag.get('iso_max', 0):>7.4f} "
                      f"{diag.get('corr_med', float('nan')):>6.3f} "
                      f"{diag.get('ring_p75', float('nan')):>7.2f}"
                      f"{flag} {tally}")
                if verbose:
                    print(f"            {diag}")

        print("\n--- summary " + "-" * 50)
        for label in TRUTH:
            hit = counts[(label, "hit")]
            tot = hit + counts[(label, "miss")]
            if tot:
                print(f"  {label:<22} {hit}/{tot} correct")
        if misses:
            print(f"  MISSED intruder clips:  {', '.join(misses)}")
        if false_alarms:
            print(f"  FALSE ALARMS on:        {', '.join(false_alarms)}")
        if not misses and not false_alarms:
            print("  no misses, no false alarms")
    finally:
        detect.BG_DIR = real_bg_dir
        detect._bg_cache.pop(camera, None)
        shutil.rmtree(bg_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
