"""Independent watcher: sample the outside camera and run the SAME detector
the server uses, bypassing Protect's motion zones and Home Assistant entirely.

Read-only on the detector side: never writes events.csv, never touches
bg_*.npy. It is also the process that ARMS THE DETERRENT at night -- see
deter.py for the gates.

Two rate limits live here and they must stay separate:

  * REPORTING is rate-limited (a resident napping on the patio would otherwise
    log every 2s all night).
  * THE DETERRENT IS NOT. Until 2026-08-28 the report limit sat in front of the
    deterrent call, so after one orange report deter.consider() was not asked
    again for 120s. A `too_far` or `at_flap` hold was therefore a one-shot no:
    the 01:25 hold that night was never re-checked while the cat walked to the
    door and went in. The deterrent now sees every orange frame; its own
    shared cooldown prevents double fires.

The per-frame logic is in classify_frame() so evaluate_deter.py can replay it
over recorded video with the same code the live loop runs.
"""
import os, sys, glob, subprocess, time, tempfile
sys.path.insert(0, "/home/david/projects/ocp")
os.chdir("/home/david/projects/ocp")
import cv2, detect, animal
try:
    import deter
except Exception as _e:          # never let the deterrent break detection
    deter = None
    print(f"deterrent unavailable: {_e}", flush=True)
from capture import measure

CAM = "outside"
SAVE = "/home/david/ocp-watch/patrol"


def burst_frames(n):
    """The n FRESHEST frames available, newest last.

    Latency is what decides whether we hit the cat or the empty patio it left.
    Measured 2026-08-27: the newest segment that ffmpeg can actually read has
    just closed, so its final frame is ~1s old, and its first is ~6s old. A
    direct RTSP grab is WORSE -- 3.6-4.0s just to collect 15 frames.

    So: take the newest readable segment, decode it densely, and return its
    TAIL. That puts every frame we judge on within a couple of seconds of now,
    and it keeps them in time order so deter's "is it at the flap RIGHT NOW"
    check reads the genuinely newest detections.
    """
    segs = sorted(glob.glob(f"/dev/shm/ocp/{CAM}/*.mp4"),
                  key=os.path.getmtime, reverse=True)
    for s in segs:
        d = tempfile.mkdtemp()
        r = subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-i", s,
                            "-vf", "fps=4", "-q:v", "2",
                            os.path.join(d, "f%03d.jpg")],
                           capture_output=True)
        out = []
        if r.returncode == 0:
            for f in sorted(glob.glob(os.path.join(d, "*.jpg"))):
                img = cv2.imread(f)
                if img is not None:
                    out.append(img)
        subprocess.run(["rm", "-rf", d])
        if len(out) >= n:
            return out[-n:]          # the tail = the freshest
        if out:
            return out               # short segment; take what there is
    return []


def newest_frame():
    """The FRESHEST frame available, not the oldest.

    `-frames:v 1` returns the FIRST frame of the segment. Segments are ~5s and
    the newest readable one has only just closed, so its first frame is ~6s old
    -- and that is what detection was running on. Measured 2026-08-28: the
    patrol logged a detection at 01:32:29 from a frame stamped 01:32:21, eight
    seconds stale, and the deterrent then fired three seconds after the cat had
    walked out of frame. `-sseof -0.6` seeks to the end of the segment instead.
    """
    segs = sorted(glob.glob(f"/dev/shm/ocp/{CAM}/*.mp4"),
                  key=os.path.getmtime, reverse=True)
    for s in segs:
        out = tempfile.mktemp(suffix=".jpg")
        r = subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y",
                            "-sseof", "-0.6", "-i", s,
                            "-frames:v", "1", "-q:v", "2", out],
                           capture_output=True)
        if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            # some segments will not seek; fall back to the first frame
            r = subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-i", s,
                                "-frames:v", "1", "-q:v", "2", out],
                               capture_output=True)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            f = cv2.imread(out); os.unlink(out)
            if f is not None:
                return f
    return None


def classify_frame(f):
    """One frame -> (box, verdict, reasoning, info); box is None if no animal.

    Mirrors server.score_frame: classify the detector box the way the real
    detector would, so this is a fallback verdict and not merely "something
    moved". Read-only -- nothing is recorded.
    """
    box = animal.best_box(f)
    if box is None:
        return None, None, None, None
    verdict = reasoning = "?"
    try:
        mask, minfo = detect.motion_mask(f, CAM)
        stats = measure(f, mask if mask is not None
                        else detect.roi_mask(f.shape, CAM))
        feats = detect.box_features(f, box["box"])
        box_ir = (detect.box_ir_features(f, box["box"])
                  if stats.get("is_ir") else None)
        verdict, _, reasoning = detect.classify_detection(feats, box_ir)
    except Exception as e:
        reasoning = f"classify failed: {e}"
    _, info = detect.motion_mask(f, CAM)
    return box, verdict, reasoning, info


# Rate limits are verdict-aware. A resident can nap on that patio for hours and
# would otherwise re-report every 2 min all night. The intruder and people must
# never be suppressed for long. THESE GATE LOGGING ONLY -- see module docstring.
MIN_GAP = 120.0        # orange_cat / person / unknown
MIN_GAP_BORING = 900.0 # a repeated no_orange -- same animal, still there
HEARTBEAT = 1800.0     # prove liveness twice an hour


class Reporter:
    """Decides whether a detection is worth a log line and a saved frame."""

    def __init__(self):
        self.last_report = 0.0
        self.last_verdict = None
        self.n_suppressed = 0

    def should_report(self, verdict, is_person, now):
        gap = now - self.last_report
        boring = (verdict == "no_orange"
                  and self.last_verdict == "no_orange"
                  and not is_person)
        self.last_verdict = verdict
        if gap < (MIN_GAP_BORING if boring else MIN_GAP):
            self.n_suppressed += 1
            return False
        self.last_report = now
        return True


def describe(box, verdict, reasoning, info, path):
    po = box.get("person_overlap", 0.0)
    kind = "PERSON" if po >= animal.PERSON_OVERLAP else "ANIMAL"
    # An orange_cat verdict alone is NOT trustworthy at dusk: low sun on the
    # grey siding scored 89.7% ginger / 68.1pp on an EMPTY patio at 19:37 and
    # 83.3% at 19:42 (2026-08-25). Area separates them -- a cat occupies the
    # frame, a light patch does not. Fitted to 3 points, so LABEL rather than
    # suppress. The "sunlight" wording only makes sense in daylight; at night a
    # weak box is a distant cat (22:10 on 08-25 was real).
    if verdict == "orange_cat":
        strong = box["conf"] >= 0.5 or info["motion_frac"] >= 0.05
        night = deter is not None and deter._in_window()
        if strong:
            flag = " *** ORANGE CAT ***"
        elif night:
            flag = (" ~orange-ish LOW-CONFIDENCE (weak box at night: probably"
                    " distant -- LOOK AT THE FRAME)")
        else:
            flag = (" ~orange-ish LOW-CONFIDENCE (likely sunlight on the"
                    " siding -- LOOK AT THE FRAME before believing it)")
    else:
        flag = ""
    return (f"PATROL {kind}{flag} verdict={verdict} det_conf={box['conf']:.2f} "
            f"person_overlap={po:.2f} motion_frac={info['motion_frac']:.4f} "
            f"-> {path} | {reasoning} "
            f"(if no POST /motion follows within ~1 min, the HA path is the fault)")


class ChangeLogger:
    """deter logs a line per gate per call; at 2s cadence a hold would repeat
    every cycle. Print a gate's reasoning only when it differs from the last
    thing printed, so the log reads as a sequence of decisions."""

    def __init__(self):
        self.last = None

    def __call__(self, msg):
        key = msg.split(" -- ")[0].split(". burst=")[0]
        if key != self.last:
            self.last = key
            print(msg, flush=True)


def main():
    os.makedirs(SAVE, exist_ok=True)
    print(f"patrol armed {time.strftime('%H:%M:%S')}: YOLO on {CAM}, 2s in the "
          f"deterrent window / 30s outside, detector_available={animal.available()}",
          flush=True)
    rep = Reporter()
    dlog = ChangeLogger()
    last_beat = time.time()
    n_frames = n_det = 0
    last_decision = None

    while True:
        try:
            f = newest_frame()
            if f is not None:
                n_frames += 1
                box, verdict, reasoning, info = classify_frame(f)
                if box is not None:
                    n_det += 1
                    is_person = box.get("person_overlap", 0.0) >= animal.PERSON_OVERLAP
                    if rep.should_report(verdict, is_person, time.time()):
                        ts = time.strftime("%H%M%S")
                        path = f"{SAVE}/patrol-{ts}.jpg"
                        cv2.imwrite(path, f)
                        print(f"{time.strftime('%H:%M:%S')} "
                              f"{describe(box, verdict, reasoning, info, path)}",
                              flush=True)

                    # The deterrent is asked on EVERY orange frame, reported or
                    # not. A single patrol frame is the false-positive
                    # signature, so deter pulls a real burst and votes; a hold
                    # (too_far / at_flap) is re-checked next cycle, 2s later,
                    # instead of after the 120s report gap.
                    if verdict == "orange_cat" and deter is not None:
                        try:
                            decision = deter.consider_and_escalate(
                                burst_frames, log=dlog)
                            if decision != last_decision:
                                print(f"{time.strftime('%H:%M:%S')} deterrent "
                                      f"decision: {decision}", flush=True)
                            last_decision = decision
                            if decision == "fired":
                                deter.capture_reaction(
                                    f"fire-{time.strftime('%Y%m%d-%H%M%S')}")
                        except Exception as e:
                            print(f"  deterrent error: {e}", flush=True)
                else:
                    last_decision = None
                    dlog.last = None
            if time.time() - last_beat >= HEARTBEAT:
                last_beat = time.time()
                print(f"{time.strftime('%H:%M:%S')} patrol heartbeat: "
                      f"{n_frames} frames sampled, {n_det} detections "
                      f"({rep.n_suppressed} reports suppressed). "
                      f"Absence of these lines means the patrol died.", flush=True)
        except Exception as e:
            print(f"{time.strftime('%H:%M:%S')} patrol error: {e}", flush=True)
        # 2s inside the window: the cat crosses the patio in about 3 seconds.
        # A cycle costs ~0.3s, plus ~0.5s when the deterrent is consulted.
        try:
            time.sleep(2 if (deter is not None and deter._in_window()) else 30)
        except Exception:
            time.sleep(30)


if __name__ == "__main__":
    main()
