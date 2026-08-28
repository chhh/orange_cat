"""Confirm a patrol orange_cat hit with a burst vote, then (optionally) sound.

WHY A SECOND LOOK. The patrol scores ONE frame every 30s. On 2026-08-26 we
labelled every archive event any code called `orange_cat` and found the whole
false-positive population sits at exactly one orange frame:

    real stray  orange votes [1, 1, 1, 4, 7, 8, 9, 9, 9, 10, 11, 12, 13, 16]
    person                   [0, 0, 1, 1, 1, 1, 1, 1, 1]   <- never above 1
    squirrel                 [1, 1, 1, 6, 8]
    resident                 [1, 3]
    light artefact           [1]

Requiring >= 2 orange frames removed 9 of 9 person false positives and the
light artefact, taking precision from 48% to 79%. A single patrol frame IS the
signature that was wrong 15 times out of 15 that day, so firing on one would be
firing on exactly the wrong thing.

So: patrol finds a candidate -> we immediately pull a real burst and vote.

PEOPLE. `animal.best_box` returns None when a person is alone in frame, which
means a person sighting is silently recorded as "no animal" (see the
person-suppression-gap note). We therefore ask the detector for people
DIRECTLY, not via person_overlap, and abstain if any frame in the burst has
one. A person does not become a cat between frames.

Default is DRY RUN: it decides and logs, and plays nothing.
"""
import os
import random
import sys
import time

sys.path.insert(0, "/home/david/projects/ocp")

import animal
import detect

# The cat flap, in frame coordinates (1024x576 outside camera), measured from
# the frame itself rather than guessed. NEVER fire while the animal is at or
# part-way through it: a startled cat halfway through a small opening may bolt
# BACK INSIDE, which is the opposite of the point, or hurt itself in the flap.
# Validated on real frames: the stray crossing the patio at 02:59 on 08-27 had
# box x233-633 (no overlap -> fires), while a cat mid-exit had box x117-259
# (overlap -> stands down).
FLAP_ZONE = (105, 238, 200, 355)
# How many of the NEWEST framed detections decide "is it at the flap right now".
FLAP_RECENT = int(os.getenv("DETER_FLAP_RECENT", "2"))

# Startle falls off with distance -- a drill at the gate is most of a patio
# away from the speaker. Measured on the 02:59 approach (box area as % of
# frame): by the gate 0.4-3.4, mid-patio 6.8-13.0, at the flap 9.6-11.8. The
# band we want is mid-patio: close enough to startle, not yet in the opening.
#
# But that band is only ~2s wide and our sound lands 3-5s after the frames we
# judge. Waiting for the cat to BE in it means firing as it reaches the flap,
# where we must hold. So we also fire on APPROACH -- if it is closing on the
# door, the sound lands about when it arrives in the band.
# Proximity by BOX HEIGHT, not area. Area collapses when the cat faces the
# camera -- on 2026-08-28 01:25 the stray stood mid-patio at 23.1% frame height
# but only 1.4% area, and was held as "too far" while plainly in range.
# Height barely moves with orientation:
#   far by the gate  8.3%   gate 17.5%   mid-patio 29-33%   in the open 43.9%
MIN_HEIGHT_PCT = float(os.getenv("DETER_MIN_HEIGHT", "20.0"))
APPROACH_HEIGHT_PCT = float(os.getenv("DETER_APPROACH_HEIGHT", "12.0"))

BURST = int(os.getenv("DETER_BURST", "6"))
MIN_ORANGE = int(os.getenv("DETER_MIN_ORANGE", "2"))
COOLDOWN = float(os.getenv("DETER_COOLDOWN", "60"))
# Hours during which sound is allowed at all. People were labelled in the
# archive between 09:06 and 21:55 and never once between 22:00 and 06:00,
# so the night window is where a mistake is cheapest.
HOUR_FROM = int(os.getenv("DETER_HOUR_FROM", "22"))
HOUR_TO = int(os.getenv("DETER_HOUR_TO", "6"))
ARMED = os.getenv("DETER_ARM", "0") in ("1", "true", "yes")

# The cooldown has to be SHARED, not per-process. server.py and the patrol are
# both armed and both watch the same patio, so an in-memory cooldown lets one
# cat collect two drills seconds apart -- which over-startles the animal and
# makes it impossible to tell what a single sound does. A file gives every
# process the same clock.
COOLDOWN_FILE = os.getenv("DETER_COOLDOWN_FILE", "/home/david/ocp-watch/.deter-last-fire")


def _last_fire_at():
    try:
        with open(COOLDOWN_FILE) as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return 0.0


def _mark_fired(when=None):
    try:
        with open(COOLDOWN_FILE, "w") as fh:
            fh.write(str(when or time.time()))
    except OSError as exc:
        print(f"  deterrent: could not write cooldown file: {exc}", flush=True)


def _in_window(now=None):
    h = time.localtime(now or time.time()).tm_hour
    return h >= HOUR_FROM or h < HOUR_TO if HOUR_FROM > HOUR_TO else \
        HOUR_FROM <= h < HOUR_TO


def _overlaps(box, zone):
    ax0, ay0, ax1, ay1 = box
    bx0, by0, bx1, by1 = zone
    return not (ax1 < bx0 or ax0 > bx1 or ay1 < by0 or ay0 > by1)


def score_burst(frames):
    """Vote a burst the way server.py does.

    Returns (verdict, tally, people, at_flap) -- at_flap counts frames where
    the animal overlapped the cat flap.
    """
    verdicts, people, at_flap, boxed = [], 0, 0, 0
    flap_seq = []          # per-framed-detection: was it at the flap?
    geom = []              # (x0, area_pct) per framed detection, in time order
    for f in frames:
        if f is None:
            continue
        # Ask about people directly: best_box() hides a lone person by
        # returning None, and that is how a person reaches a cat verdict.
        raw = animal.detect(f, want_person=True)
        if any(d["cls"] == animal.PERSON_CLASS for d in raw):
            people += 1
            verdicts.append("person")
            continue
        box = animal.best_box(f, check_people=True)
        if box is None:
            verdicts.append("no_animal")
            continue
        if (box.get("person_overlap") or 0.0) >= animal.PERSON_OVERLAP:
            people += 1
            verdicts.append("person")
            continue
        boxed += 1
        _x0, _y0, _x1, _y1 = box["box"]
        _h, _w = f.shape[:2]
        geom.append((_x0, 100.0 * (_y1 - _y0) / _h))   # (x0, height % of frame)
        near = _overlaps(box["box"], FLAP_ZONE)
        flap_seq.append(near)
        if near:
            at_flap += 1
        try:
            mask, _ = detect.motion_mask(f, "outside")
            from capture import measure
            stats = measure(f, mask if mask is not None
                            else detect.roi_mask(f.shape, "outside"))
            feats = detect.box_features(f, box["box"])
            box_ir = (detect.box_ir_features(f, box["box"])
                      if stats.get("is_ir") else None)
            v, _, _ = detect.classify_detection(feats, box_ir)
        except Exception:
            v = "unmeasurable"
        verdicts.append(v)
    verdict, _, tally = detect.vote(verdicts)
    return verdict, tally, people, at_flap, boxed, flap_seq, geom


def _flush_print(msg):
    """Default logger. MUST flush: stdout is redirected to a log file, so an
    unflushed print sits in the buffer and the decision is invisible. On
    2026-08-28 the stray was held as too_far and the reason never appeared."""
    print(msg, flush=True)


def consider(grab_frames, log=_flush_print):
    """Called when a detector thinks it saw an orange cat.

    `grab_frames(n)` must return n fresh frames. Returns the string decision.
    """
    frames = [x for x in (grab_frames(BURST) or []) if x is not None]
    if not frames:
        log("  deterrent: no burst frames available -- standing down")
        return "no_frames"

    verdict, tally, people, at_flap, boxed, flap_seq, geom = score_burst(frames)
    orange = tally.get("orange_cat", 0)
    detail = (f"burst={len(frames)} orange={orange} "
              f"people_frames={people} at_flap={at_flap} tally={tally}")

    if people:
        log(f"  deterrent: PERSON in {people} of {len(frames)} burst frames "
            f"-- standing down. {detail}")
        return "person"
    # WHERE THE CAT IS NOW is what matters -- the sound lands in the future,
    # so a burst-wide majority is the wrong test. On the 02:59 arrival the cat
    # was at the flap in the last 2 frames of 13; a majority test said "fire"
    # while it was standing at the door. Judge on the most recent detections.
    recent = flap_seq[-FLAP_RECENT:] if flap_seq else []
    if recent and any(recent):
        log(f"  deterrent: cat at the flap in the latest {len(recent)} "
            f"detection(s) -- standing down; it must not be startled into or "
            f"out of the opening. {detail}")
        return "at_flap"
    if False:
        log(f"  deterrent: animal AT THE FLAP in {at_flap} of {boxed} framed "
            f"detections -- standing down. A cat part-way through could bolt "
            f"back inside. {detail}")
        return "at_flap"
    if geom:
        height = geom[-1][1]
        # "Closing" must not assume a direction. x0 falling only detects an
        # approach from the RIGHT (the gate), which is how it came on 08-27 --
        # a sample of one. Growing area means approaching from ANY angle, and
        # on that same approach area ran 0.6 -> 13.0%, a far stronger signal
        # than the 749 -> 233 x-shift. Either counts.
        _x_closing = len(geom) >= 2 and geom[-1][0] < geom[0][0] - 40
        # Height can grow because the cat TURNS SIDE-ON, not because it comes
        # closer. On 2026-08-28 03:22 that fired at a cat walking AWAY toward
        # the gate (x 796->865, height +40%). The flap is on the LEFT, so a
        # genuine approach never increases x. Growth only counts if the cat is
        # not retreating.
        _x_retreating = len(geom) >= 2 and geom[-1][0] > geom[0][0] + 25
        _grew = (len(geom) >= 2 and geom[0][1] > 0
                 and height >= 1.4 * geom[0][1] and not _x_retreating)
        closing = (_x_closing or _grew) and height >= APPROACH_HEIGHT_PCT
        if height < MIN_HEIGHT_PCT and not closing:
            log(f"  deterrent: too far for a useful startle "
                f"(height {height:.1f}% < {MIN_HEIGHT_PCT}%, not closing) -- holding. {detail}")
            return "too_far"
        if closing and height < MIN_HEIGHT_PCT:
            log(f"  deterrent: cat is CLOSING on the door "
                f"(x {geom[0][0]}->{geom[-1][0]}, height {height:.1f}%) -- firing now so "
                f"the sound lands as it arrives. {detail}")

    if orange < MIN_ORANGE:
        log(f"  deterrent: only {orange} orange frame(s), need {MIN_ORANGE} "
            f"-- standing down (this is the false-positive signature). {detail}")
        return "too_few"
    if not _in_window():
        log(f"  deterrent: confirmed orange cat but outside the "
            f"{HOUR_FROM:02d}:00-{HOUR_TO:02d}:00 window -- not sounding. {detail}")
        return "out_of_hours"
    since = time.time() - _last_fire_at()
    if since < COOLDOWN:
        log(f"  deterrent: confirmed, but another process fired {since:.0f}s ago "
            f"({COOLDOWN:.0f}s shared cooldown). {detail}")
        return "cooldown"

    if not ARMED:
        log(f"  deterrent: WOULD HAVE PLAYED a sound now (dry run). {detail}")
        return "would_fire"

    sound = SOUND_SEQUENCE[0]
    # Claim the cooldown BEFORE playing, so a second process racing us on the
    # same cat backs off rather than doubling up while we are mid-request.
    _mark_fired()
    try:
        _play(sound, log)
        log(f"  deterrent: PLAYED {sound}. {detail}")
        return "fired"
    except Exception as exc:
        log(f"  deterrent: playback FAILED ({exc}). {detail}")
        return "failed"


# --- reaction capture -----------------------------------------------------
# The recorder keeps a rolling ~30s of 5-second segments in /dev/shm, wrapping
# over 6 files. So the moments BEFORE a sound are already on disk when it
# fires -- we just have to copy them out before the wrap overwrites them, then
# keep collecting as new ones land. Result: one clip spanning approach,
# sound, and reaction.

import glob
import shutil
import subprocess

SEG_DIR = os.getenv("DETER_SEG_DIR", "/dev/shm/ocp/outside")
REACT_DIR = os.getenv("DETER_REACT_DIR", "/home/david/ocp-watch/reactions")
REACT_AFTER = float(os.getenv("DETER_REACT_AFTER", "50"))


def _segments():
    """Current buffer contents as (mtime, path), oldest first."""
    out = []
    for p in glob.glob(os.path.join(SEG_DIR, "*.mp4")):
        try:
            out.append((os.path.getmtime(p), p))
        except OSError:
            pass
    return sorted(out)


def capture_reaction(tag, after=None, log=_flush_print):
    """Save a clip spanning the seconds before and after a deterrent fire.

    ffmpeg is writing into these files continuously, so a segment must only be
    copied once it has STOPPED changing -- copying mid-write yields a truncated
    file, and concat then silently drops most of it (a 70s capture came out as
    25s of video before this was fixed). We therefore watch (mtime, size) and
    take a segment only when it has been stable across two polls.
    """
    after = REACT_AFTER if after is None else after
    work = os.path.join(REACT_DIR, tag)
    os.makedirs(work, exist_ok=True)

    def playable(path):
        """An mp4 written by the segment muxer has no moov atom until it is
        closed, so an in-progress segment is simply unreadable. ffprobe is the
        only honest test -- stability of mtime is not enough."""
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True)
        try:
            return r.returncode == 0 and float(r.stdout.strip()) > 0.5
        except ValueError:
            return False

    def stamp():
        out = {}
        for p in glob.glob(os.path.join(SEG_DIR, "*.mp4")):
            try:
                st = os.stat(p)
                out[p] = (st.st_mtime, st.st_size)
            except OSError:
                pass
        return out

    taken = {}          # path -> the (mtime,size) we copied
    kept = []           # (mtime, dst) so we can order the clip by real time
    prev = stamp()

    def harvest(now):
        for p, sig in now.items():
            if prev.get(p) == sig and taken.get(p) != sig and sig[1] > 0:
                dst = os.path.join(work, f"{len(kept):03d}.mp4")
                try:
                    shutil.copy2(p, dst)
                except OSError:
                    continue
                taken[p] = sig
                if playable(dst):
                    kept.append((sig[0], dst))
                else:
                    os.unlink(dst)      # still being written -- no moov atom

    harvest(prev)       # pre-roll: whatever is already complete in the buffer
    pre = len(kept)
    log(f"  reaction: {pre} pre-roll segment(s) (~{pre * 5}s before the sound)")

    deadline = time.time() + after
    while time.time() < deadline:
        time.sleep(1.0)
        now = stamp()
        harvest(now)
        prev = now

    if not kept:
        log("  reaction: nothing captured")
        return None

    kept.sort()
    listing = os.path.join(work, "list.txt")
    with open(listing, "w") as fh:
        for _, p in kept:
            fh.write(f"file '{p}'\n")
    out = os.path.join(REACT_DIR, f"{tag}.mp4")
    r = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", listing,
         "-c", "copy", "-avoid_negative_ts", "make_zero", out],
        capture_output=True)
    if r.returncode == 0 and os.path.exists(out):
        dur = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", out],
            capture_output=True, text=True).stdout.strip()
        shutil.rmtree(work, ignore_errors=True)
        log(f"  reaction: saved {out} ({len(kept)} segments, {dur}s)")
        return out
    log(f"  reaction: concat failed -- raw segments kept in {work}")
    return work


# --- escalation ------------------------------------------------------------
# Dima's design (his `main` branch): one sound is often not enough -- on
# 2026-08-27 a resident flinched at the drill and did not leave. So keep going
# WHILE THE TARGET IS STILL THERE, rather than firing a fixed volley.
#
# A fixed 3-sound barrage was considered and rejected: our latency means sounds
# 2 and 3 would often play to an empty patio (exactly what happened at 03:18),
# and residents -- who cannot avoid their own cat door -- would take the full
# volley. Escalation gives a cat that leaves ONE sound and a cat that ignores
# it several.
#
# Every gate is re-applied on every repeat. In particular the flap guard: a cat
# that retreats toward the door must not be blasted while part-way through it.

import threading

ESC_MIN = float(os.getenv("DETER_ESC_MIN", "0"))       # 0 -> take from config
ESC_MAX = float(os.getenv("DETER_ESC_MAX", "0"))
ESC_MAX_DURATION = float(os.getenv("DETER_ESC_DURATION", "0"))
ESC_MAX_SOUNDS = int(os.getenv("DETER_ESC_MAX_SOUNDS", "6"))

# Sounds are served from odd-fellow, not from HA's config/www. media_content_id
# is just a URL HA fetches, and HA can reach us over the tunnel -- verified
# 2026-08-27 21:48 (HA hit our server from 192.168.1.133 and the speaker went
# playing -> idle). This means the detector owns its own sounds instead of
# depending on files in someone else's config directory.
SOUND_BASE = os.getenv("DETER_SOUND_BASE", "http://192.168.7.4:8081")

# A kill switch. The escalation runs in a daemon thread and would otherwise
# play its full sequence no matter what -- so "I am watching it" would mean
# nothing. Touch this file and the loop stops before the next sound.
ABORT_FILE = os.getenv("DETER_ABORT_FILE", "/home/david/ocp-watch/.deter-abort")


def _aborted():
    if os.path.exists(ABORT_FILE):
        try:
            os.unlink(ABORT_FILE)
        except OSError:
            pass
        return True
    return False

# ORDERED, not random. The drill is the sharpest onset -- best for the initial
# startle. The dog sounds carry a threat a cat has evolved to care about, which
# a power tool does not. So: startle, then escalate in KIND, not just in
# repetition. A resident flinched at the drill on 08-27 and did not leave.
SOUND_SEQUENCE = [s.strip() for s in os.getenv(
    "DETER_SOUNDS",
    "DRILL_boost3.wav,dog_bark_big.wav,dog_growl.wav,dog_bark.wav"
).split(",") if s.strip()]


def _play(name, log=print):
    """Play one sound, by URL, through the camera speaker."""
    import json as _json, urllib.request, os as _os
    token = _os.getenv("HA_LONG_LIVED_TOKEN")
    if not token:
        raise RuntimeError("HA_LONG_LIVED_TOKEN not set")
    import config
    payload = {"entity_id": config.HA_SPEAKER,
               "media_content_id": f"{SOUND_BASE}/{name}",
               "media_content_type": "music"}
    req = urllib.request.Request(
        f"http://{config.HA_HOST}:8123/api/services/media_player/play_media",
        data=_json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10):
        pass


def _esc_params():
    import config
    return (ESC_MIN or config.SOUND_MIN_INTERVAL,
            ESC_MAX or config.SOUND_MAX_INTERVAL,
            ESC_MAX_DURATION or config.SOUND_MAX_DURATION)


def escalate(grab_frames, log=_flush_print, already_played=1):
    """Keep sounding while the cat stays, re-checking every gate each time."""
    import config
    import talk
    lo, hi, max_dur = _esc_params()
    started = time.time()
    played = already_played

    while played < ESC_MAX_SOUNDS:
        time.sleep(random.uniform(lo, hi))
        if _aborted():
            log(f"  escalation: ABORTED by hand after {played} sound(s)")
            return played
        if time.time() - started > max_dur:
            log(f"  escalation: {max_dur:.0f}s limit reached after "
                f"{played} sound(s) -- stopping")
            return played

        frames = [x for x in (grab_frames(BURST) or []) if x is not None]
        if not frames:
            log(f"  escalation: no frames -- stopping after {played}")
            return played

        verdict, tally, people, at_flap, boxed, flap_seq, geom = score_burst(frames)
        orange = tally.get("orange_cat", 0)

        if people:
            log(f"  escalation: PERSON appeared -- stopping after {played}")
            return played
        if boxed and at_flap * 2 > boxed:
            log(f"  escalation: cat moved to the flap ({at_flap}/{boxed}) -- "
                f"stopping after {played}, it must not be driven back inside")
            return played
        if orange < MIN_ORANGE:
            log(f"  escalation: target gone (orange={orange}) -- "
                f"stopping after {played} sound(s)")
            return played

        sound = SOUND_SEQUENCE[played % len(SOUND_SEQUENCE)]
        _mark_fired()          # keep the shared cooldown claimed
        try:
            _play(sound, log)
            played += 1
            log(f"  escalation: sound {played} -- {sound} (still present, "
                f"orange={orange})")
        except Exception as exc:
            log(f"  escalation: playback failed ({exc}) -- stopping")
            return played

    log(f"  escalation: reached the {ESC_MAX_SOUNDS}-sound cap")
    return played


def consider_and_escalate(grab_frames, log=_flush_print):
    """consider(), and if it fired, keep escalating in the background."""
    decision = consider(grab_frames, log=log)
    if decision == "fired":
        threading.Thread(target=escalate, args=(grab_frames,),
                         kwargs={"log": log}, daemon=True).start()
    return decision
