"""Replay the patrol + deterrent decision logic over recorded video.

The detector has evaluate.py; the deterrent had nothing, so every gate was
fitted to the last live cat and tested on the next one. This runs the SAME
code the patrol runs -- patrol.classify_frame and deter.consider -- over a
reaction clip, at a simulated cadence and frame staleness, and reports when it
would have fired and WHERE THE CAT WAS when the sound would have landed.

    uv run evaluate_deter.py ~/ocp-watch/reactions/fire-20260828-013231.mp4
    uv run evaluate_deter.py CLIP --stale 4 --legacy-gap 120   # as run on 08-28
    uv run evaluate_deter.py CLIP --stale 0.7                  # decoded ring buffer

Nothing here plays a sound: DETER_ARM is forced off and the deterrent returns
"would_fire". The cooldown file is redirected to a temp path.

Truth is the detector itself run on every frame at full rate ("where the cat
was" = the animal box in the frame nearest the sound landing time). That is
good enough for aiming questions; it is not a substitute for looking at the
frame when the verdict is in doubt.
"""
import argparse
import glob
import os
import subprocess
import sys
import tempfile
import time

os.environ["DETER_ARM"] = "0"
os.environ["DETER_COOLDOWN_FILE"] = os.path.join(tempfile.gettempdir(),
                                                  "evaluate-deter-cooldown")
os.environ["DETER_VISIT_FILE"] = os.path.join(tempfile.gettempdir(),
                                               "evaluate-deter-visit")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2

import animal
import detect
import deter
import patrol

FPS = 4  # decode rate; also the burst rate the live patrol uses

# Burned-in clock at t=0 of each clip, read off the frames by eye. The clip
# name carries the FIRE time, not the start; pre-roll is 4-5 segments of ~5s.
CLIP_START = {
    "fire-20260827-031828": "03:18:03",
    "fire-20260828-013231": "01:32:07",
    "fire-20260828-032259": "03:22:35",
}
SOUND_AT = {  # when HA fetched the WAV, from soundserver.log / exitwatch.log
    "fire-20260827-031828": "03:18:27",
    "fire-20260828-013231": "01:32:30",
    "fire-20260828-032259": "03:22:58",
}


def hms(t):
    t = t % 86400
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:04.1f}"


def parse_hms(s):
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def decode(path, dir_fps=1.5):
    """Frames at FPS from a video, or the frames of a saved event burst dir.

    A burst dir (frames/events/<cam>-<stamp>/NN-verdict-pxN.jpg) holds 15
    frames spread across ~10s of segment buffer, i.e. ~1.5 fps. Each is
    repeated to fill FPS so the simulated cadence and staleness still apply;
    resolution in time is then only ~0.7s, which is fine for "where was it".
    """
    if os.path.isdir(path):
        out = []
        rep = max(1, int(round(FPS / dir_fps)))
        for f in sorted(glob.glob(os.path.join(path, "[0-9][0-9]-*.jpg"))):
            img = cv2.imread(f)
            if img is not None:
                out.extend([img] * rep)
        return out
    d = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-i", path,
                    "-vf", f"fps={FPS}", "-q:v", "3", f"{d}/f%05d.jpg"],
                   check=True)
    frames = []
    for f in sorted(glob.glob(f"{d}/*.jpg")):
        img = cv2.imread(f)
        if img is not None:
            frames.append(img)
    subprocess.run(["rm", "-rf", d])
    return frames


def truth(frames):
    """Per frame: (box, height%, verdict) for the best animal box, or None."""
    out = []
    for f in frames:
        dets = [d for d in animal.detect(f, want_person=True) if d["cls"] != 0]
        if not dets:
            out.append(None)
            continue
        b = dets[0]["box"]
        h = 100.0 * (b[3] - b[1]) / f.shape[0]
        feats = detect.box_features(f, b)
        v, _, _ = detect.classify_detection(feats, None)
        out.append({"box": b, "h": h, "verdict": v, "conf": dets[0]["conf"],
                    "flap": deter._overlaps(b, deter.FLAP_ZONE)})
    return out


def where(tr, i):
    if i < 0 or i >= len(tr) or tr[i] is None:
        return "no animal in frame"
    t = tr[i]
    b = t["box"]
    return (f"{t['verdict']} h={t['h']:.0f}% x={b[0]}-{b[2]}"
            f"{' AT FLAP' if t['flap'] else ''}")


class SimClock:
    """Give deter a cooldown that runs on simulated time."""

    def __init__(self):
        self.now = 0.0
        self.last_fire = -1e9

    def install(self):
        deter._last_fire_at = lambda: time.time() - (self.now - self.last_fire)
        deter._mark_fired = lambda when=None: setattr(self, "last_fire", self.now)
        deter._in_window = lambda now=None: True
        # Wrap the ORIGINAL, not whatever a previous clip's install left here.
        if not hasattr(deter, "_visit_update_orig"):
            deter._visit_update_orig = deter._visit_update
        deter._visit_update = (lambda fs, v, now=None:
                               deter._visit_update_orig(fs, v, now=self.now))


def run(path, stale, interval, sound_latency, legacy_gap, quiet=False):
    # Each clip is its own visit: stale state from the previous clip would
    # mark an approach as an exit (or vice versa).
    try:
        os.unlink(os.environ["DETER_VISIT_FILE"])
    except OSError:
        pass
    name = os.path.splitext(os.path.basename(path.rstrip("/")))[0]
    t0 = parse_hms(CLIP_START.get(name, "00:00:00"))
    frames = decode(path)
    tr = truth(frames)
    dur = len(frames) / FPS

    seen = [i for i, t in enumerate(tr) if t and t["verdict"] == "orange_cat"]
    print(f"\n== {name}: {dur:.0f}s, orange cat in truth "
          f"{hms(t0 + seen[0] / FPS) if seen else '-'} .. "
          f"{hms(t0 + seen[-1] / FPS) if seen else '-'}"
          f"{'  (actual sound ' + SOUND_AT[name] + ')' if name in SOUND_AT else ''}")
    print(f"   config: frame age {stale}s, patrol every {interval}s, "
          f"sound lands +{sound_latency}s, "
          f"{'report gap ' + str(legacy_gap) + 's gates the deterrent' if legacy_gap else 'deterrent asked every orange frame'}")

    clock = SimClock()
    clock.install()
    rep = patrol.Reporter()
    fires, last = [], None
    t = stale
    while t < dur:
        clock.now = t
        i = int((t - stale) * FPS)
        f = frames[i]
        box, verdict, reasoning, info = patrol.classify_frame(f)
        decision = None
        if box is not None:
            is_person = box.get("person_overlap", 0.0) >= animal.PERSON_OVERLAP
            reported = rep.should_report(verdict, is_person, t) if legacy_gap else True
            if verdict == "orange_cat" and (reported or not legacy_gap):
                def grab(n, i=i):
                    return [frames[j] for j in range(max(0, i - n + 1), i + 1)]
                msgs = []
                decision = deter.consider(grab, log=msgs.append)
                if decision == "would_fire":
                    land = t + sound_latency
                    li = int(land * FPS)
                    fires.append((t, land, where(tr, li)))
                    clock.last_fire = t
                if decision != last and not quiet:
                    gate = msgs[-1].strip().split(". burst=")[0] if msgs else ""
                    print(f"   {hms(t0 + t)}  sees frame {hms(t0 + t - stale)}: "
                          f"{decision:10s} {gate[:110]}")
        last = decision
        t += interval

    if fires:
        for t, land, w in fires:
            print(f"   >> WOULD FIRE at {hms(t0 + t)}, sound lands {hms(t0 + land)}: "
                  f"cat is {w}")
    else:
        print("   >> no fire")
    return fires


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--stale", type=float, default=4.0,
                    help="age of the frame the patrol sees (s). ~4 today; ~0.7 with a decoded buffer")
    ap.add_argument("--interval", type=float, default=2.0, help="patrol cadence (s)")
    ap.add_argument("--sound-latency", type=float, default=1.3,
                    help="decision -> sound at the speaker (s); 1.3 measured for the drill")
    ap.add_argument("--legacy-gap", type=float, default=0.0,
                    help="emulate the pre-08-28 patrol: only ask the deterrent when a report is allowed")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    for c in a.clips:
        run(c, a.stale, a.interval, a.sound_latency, a.legacy_gap, a.quiet)


if __name__ == "__main__":
    main()
