"""Independent watcher: sample the outside camera and run the SAME detector
the server uses, bypassing Protect's motion zones and Home Assistant entirely.

Read-only: never writes events.csv, never touches bg_*.npy.
Purpose: distinguish "nothing is happening on that patio" from
"things are happening and the HA -> POST path is broken".
"""
import os, sys, glob, subprocess, time, tempfile
sys.path.insert(0, "/home/david/projects/ocp")
os.chdir("/home/david/projects/ocp")
import cv2, detect, animal
try:
    sys.path.insert(0, "/home/david/ocp-watch")
    import deter
except Exception as _e:          # never let the deterrent break detection
    deter = None
    print(f"deterrent unavailable: {_e}", flush=True)
from capture import measure

CAM = "outside"
SAVE = "/home/david/ocp-watch/patrol"
os.makedirs(SAVE, exist_ok=True)

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


print(f"patrol armed {time.strftime('%H:%M:%S')}: YOLO on {CAM} every 30s, "
      f"detector_available={animal.available()}", flush=True)

# Rate limits are verdict-aware. A resident can nap on that patio for hours and
# would otherwise re-report every 2 min all night. The intruder and people must
# never be suppressed for long.
MIN_GAP = 120.0        # orange_cat / person / unknown
MIN_GAP_BORING = 900.0 # a repeated no_orange -- same animal, still there
HEARTBEAT = 1800.0     # prove liveness twice an hour
last_report = 0.0
last_verdict = None
last_beat = time.time()
n_frames = n_det = n_suppressed = 0

while True:
    try:
        f = newest_frame()
        if f is not None:
            n_frames += 1
            box = animal.best_box(f)
            if box is not None:
                n_det += 1
                # Mirror server.score_frame: classify the box the same way the
                # real detector would, so this is a fallback verdict and not
                # merely "something moved". Read-only -- nothing is recorded.
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

                # Decide whether this one is worth reporting. A PERSON is
                # never "boring": person_overlap wins over the verdict, because
                # classify_detection happily returns no_orange for a person and
                # people are a must-never-fire class. Same for orange_cat.
                gap = time.time() - last_report
                is_person = box.get("person_overlap", 0.0) >= animal.PERSON_OVERLAP
                boring = (verdict == "no_orange"
                          and last_verdict == "no_orange"
                          and not is_person)
                if gap < (MIN_GAP_BORING if boring else MIN_GAP):
                    n_suppressed += 1
                    last_verdict = verdict
                    box = None
            if box is not None:
                last_report = time.time()
                last_verdict = verdict
                _, info = detect.motion_mask(f, CAM)
                ts = time.strftime("%H%M%S")
                path = f"{SAVE}/patrol-{ts}.jpg"
                cv2.imwrite(path, f)
                po = box.get("person_overlap", 0.0)
                kind = "PERSON" if po >= animal.PERSON_OVERLAP else "ANIMAL"
                # An orange_cat verdict alone is NOT trustworthy at dusk:
                # low sun on the grey siding scored 89.7% ginger / 68.1pp on an
                # EMPTY patio at 19:37 and 83.3% at 19:42 (2026-08-25). The
                # detector path never consults bg_corr, so nothing else rejects
                # it. Area separates them -- a cat occupies the frame, a light
                # patch does not:
                #   real ginger 18:47  det_conf 0.91  motion_frac 0.1109
                #   sunlight    19:37  det_conf 0.32  motion_frac 0.0194
                #   sunlight    19:42  det_conf 0.37  motion_frac 0.0039
                # Fitted to 3 points, so LABEL rather than suppress -- never
                # drop a candidate, just stop calling weak ones certain.
                if verdict == "orange_cat":
                    strong = box["conf"] >= 0.5 or info["motion_frac"] >= 0.05
                    flag = (" *** ORANGE CAT ***" if strong else
                            " ~orange-ish LOW-CONFIDENCE (likely sunlight on the"
                            " siding -- LOOK AT THE FRAME before believing it)")
                else:
                    flag = ""
                print(f"{time.strftime('%H:%M:%S')} PATROL {kind}{flag} "
                      f"verdict={verdict} det_conf={box['conf']:.2f} "
                      f"person_overlap={po:.2f} "
                      f"motion_frac={info['motion_frac']:.4f} -> {path} "
                      f"| {reasoning} "
                      f"(if no POST /motion follows within ~1 min, the HA path "
                      f"is the fault)", flush=True)

                # A single patrol frame is the false-positive signature, so
                # never act on it directly: pull a real burst and vote.
                if verdict == "orange_cat" and deter is not None:
                    try:
                        decision = deter.consider_and_escalate(burst_frames)
                        if decision == "fired":
                            deter.capture_reaction(
                                f"fire-{time.strftime('%Y%m%d-%H%M%S')}")
                    except Exception as e:
                        print(f"  deterrent error: {e}", flush=True)
        if time.time() - last_beat >= HEARTBEAT:
            last_beat = time.time()
            print(f"{time.strftime('%H:%M:%S')} patrol heartbeat: "
                  f"{n_frames} frames sampled, {n_det} detections "
                  f"({n_suppressed} suppressed: {MIN_GAP:.0f}s gap, or {MIN_GAP_BORING:.0f}s "
                  f"for a repeated no_orange). "
                  f"Absence of these lines means the patrol died.", flush=True)
    except Exception as e:
        print(f"{time.strftime('%H:%M:%S')} patrol error: {e}", flush=True)
    # Sample DENSELY while the deterrent is live. On 2026-08-27 the stray
    # loitered by the gate for ~8 frames at near-zero motion and then crossed
    # to the flap in about 3 -- a 30s poll misses that entirely (it did). The
    # loiter is the only window wide enough to fire into, and it is exactly
    # what a motion trigger does not see. Outside the window, back off.
    try:
        # 2s, not 5s. The cat crosses the patio in about 3 seconds -- sampling
        # every 5 means we can miss an arrival entirely and only catch it on
        # the way out, which is what happened three times on 2026-08-28. A
        # cycle costs ~0.3s, so 2s is ~15% duty on 8 cores.
        time.sleep(2 if (deter is not None and deter._in_window()) else 30)
    except Exception:
        time.sleep(30)
