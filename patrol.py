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

FRAME SOURCE (since 2026-08-31). Inside the arming window the patrol holds the
outside RTSP stream open (streamer.py) and judges frames ~0.3-1.0s old at
~3/s. The segment-tail path below remains as the fallback and the
outside-window mode. Why: on 08-31 at 02:33 the stray crossed gate-to-flap in
~4s. The segment path sees one DISTINCT instant per ~5s (it re-reads the same
closed-segment tail until the next segment closes) at 0.6-5.6s stale -- the
whole crossing fit inside one hole, and the first orange verdict was a cat
already at the flap, where the welfare gate rightly holds. See
REVIEW-2026-08-31.md.
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
from capture import measure, stream_url
import streamer

CAM = "outside"
SAVE = "/home/david/ocp-watch/patrol"

# Cadence. HOT is the arming window: the cat has crossed the patio in ~4s, so
# the gap between judged frames must be a fraction of that. COLD (daytime) is
# the old 30s poll of the segment buffer -- no reason to decode video all day.
HOT_INTERVAL = float(os.getenv("PATROL_HOT_INTERVAL", "0.33"))
COLD_INTERVAL = float(os.getenv("PATROL_COLD_INTERVAL", "30"))
PREWARM = 900.0        # open the stream this long before the window, seconds
STALE_BLIND = 3.0      # buffer older than this = blind; fall back and say so
STALE_WEDGED = 30.0    # reader thread wedged; abandon it and open a new one


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


def _near_window(now=None):
    """In the arming window, or within PREWARM seconds of it opening."""
    if deter is None:
        return False
    if deter._in_window(now):
        return True
    lt = time.localtime(now or time.time())
    secs = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
    return (deter.HOUR_FROM * 3600 - secs) % 86400 <= PREWARM


class FrameSource:
    """The patrol's view of the camera: a live RTSP buffer when hot, the
    segment tail otherwise -- and never silent about which one it is using.

    States: cold (daytime, segment poll), hot (live buffer, fresh), blind
    (should be hot but the buffer is stale -- fall back to the segment tail
    and SAY SO; a quiet fallback would look like a healthy night and hide that
    we are back to 5s holes).
    """

    def __init__(self):
        self.stream = None
        self.state = "cold"

    def _say(self, state, extra=""):
        if state != self.state:
            self.state = state
            print(f"{time.strftime('%H:%M:%S')} patrol stream {state.upper()}"
                  f"{': ' + extra if extra else ''}", flush=True)

    def _fresh(self):
        age = self.stream.frame_age() if self.stream else None
        return age is not None and age <= STALE_BLIND and \
            bool(self.stream.buf)

    def tick(self):
        """Reconcile the stream with the clock. Call once per cycle."""
        if _near_window():
            if self.stream is None:
                try:
                    self.stream = streamer.Streamer(CAM, stream_url(CAM)).start()
                    print(f"{time.strftime('%H:%M:%S')} patrol opening live "
                          f"stream for the arming window", flush=True)
                except Exception as e:
                    print(f"{time.strftime('%H:%M:%S')} patrol stream open "
                          f"failed ({e}) -- staying on segment tail", flush=True)
            elif (self.stream.frame_age() or 0) > STALE_WEDGED:
                # A wedged reader looks alive and delivers nothing. Abandon it
                # (daemon thread) and open a fresh connection.
                print(f"{time.strftime('%H:%M:%S')} patrol stream WEDGED "
                      f"(no frame for {self.stream.frame_age():.0f}s) -- "
                      f"abandoning reader and reconnecting", flush=True)
                try:
                    self.stream._stop.set()
                except Exception:
                    pass
                try:
                    self.stream = streamer.Streamer(CAM, stream_url(CAM)).start()
                except Exception as e:
                    self.stream = None
                    print(f"  reconnect failed: {e}", flush=True)
        elif self.stream is not None:
            print(f"{time.strftime('%H:%M:%S')} patrol closing live stream "
                  f"(outside the window)", flush=True)
            try:
                self.stream.stop()
            except Exception:
                pass
            self.stream = None
            self._say("cold")

    def frame(self):
        """(frame, age_seconds_or_None). Age None = segment path, ~0.6-5.6s."""
        if self.stream is not None and self._fresh():
            self._say("hot", f"frame age {self.stream.frame_age():.1f}s")
            got = self.stream.latest(1)
            if got:
                return got[0], self.stream.frame_age()
        if self.stream is not None:
            age = self.stream.frame_age()
            self._say("blind", f"buffer {'empty' if age is None else f'{age:.0f}s stale'}"
                      " -- falling back to segment tail")
        return newest_frame(), None

    def grab(self, n):
        """Burst for deter: instant and fresh when hot, segment decode when
        not. deter needs time-ordered frames, newest last, spanning ~2s."""
        if self.stream is not None and self._fresh():
            frames = self.stream.latest(n, span=2.0)
            if frames:
                return frames
        return burst_frames(n)

    def hot(self):
        return self.stream is not None and self._fresh()


def main():
    os.makedirs(SAVE, exist_ok=True)
    print(f"patrol armed {time.strftime('%H:%M:%S')}: YOLO on {CAM}, live "
          f"stream at {HOT_INTERVAL}s in the deterrent window (+{PREWARM/60:.0f}min "
          f"prewarm) / segment tail at {COLD_INTERVAL}s outside, "
          f"detector_available={animal.available()}", flush=True)
    rep = Reporter()
    dlog = ChangeLogger()
    src = FrameSource()
    last_beat = time.time()
    n_frames = n_det = 0
    last_decision = None

    while True:
        try:
            src.tick()
            f, age = src.frame()
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
                              f"{describe(box, verdict, reasoning, info, path)}"
                              f"{f' [frame age {age:.1f}s]' if age is not None else ''}",
                              flush=True)

                    # The deterrent is asked on EVERY orange frame, reported or
                    # not. A single patrol frame is the false-positive
                    # signature, so deter pulls a real burst and votes; a hold
                    # (too_far / at_flap) is re-checked next cycle instead of
                    # after the 120s report gap.
                    if verdict == "orange_cat" and deter is not None:
                        try:
                            decision = deter.consider_and_escalate(
                                src.grab, log=dlog)
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
                s = src.stream.status() if src.stream else None
                extra = (f" stream={src.state} age="
                         f"{'-' if s is None or s['seconds_since_frame'] is None else s['seconds_since_frame']}s"
                         f" reconnects={s['reconnects'] if s else '-'}")
                print(f"{time.strftime('%H:%M:%S')} patrol heartbeat: "
                      f"{n_frames} frames sampled, {n_det} detections "
                      f"({rep.n_suppressed} reports suppressed).{extra} "
                      f"Absence of these lines means the patrol died.", flush=True)
        except Exception as e:
            print(f"{time.strftime('%H:%M:%S')} patrol error: {e}", flush=True)
        # Hot reads are free (in-memory); the blind fallback spawns an ffmpeg
        # per frame, so pace it at the old 2s rather than spinning at 0.33s.
        try:
            time.sleep(HOT_INTERVAL if src.hot() else
                       (2.0 if _near_window() else COLD_INTERVAL))
        except Exception:
            time.sleep(COLD_INTERVAL)


if __name__ == "__main__":
    main()
