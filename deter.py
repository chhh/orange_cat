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
import config
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
# A live escalation ladder refreshes the shared cooldown claim at every sound,
# a few seconds apart. A claim older than this is a dead hand -- a crashed
# thread, or a one-shot fire that already gave up -- and a CLOSE engagement
# may take it over. 09-03 00:53:24: the closing-rule shot ("firing now so the
# sound lands as it arrives") was refused at since=55s while the only ladder
# had crashed; the cat entered in silence. Far engagements never take over:
# repeating an opener at a distant cat is the double-fire the cooldown exists
# to stop.
CLOSE_TAKEOVER = float(os.getenv("DETER_CLOSE_TAKEOVER", "15"))
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

# EXITS ARE NOT TARGETS (N4, REVIEW-2026-08-31). Three of the four fires up to
# 08-31 hit a fed cat leaving: the reward was collected minutes earlier, so
# the sound cannot deter -- it wakes the street at 3am and actively teaches
# the cat that the noise is harmless (habituation is the failure mode that
# ends the acoustic approach). So consider() tracks the VISIT: a chain of
# orange sightings less than VISIT_GAP apart, with the origin fixed by the
# first framed detection of the chain. Origin at the flap = the cat emerged
# from the door = exiting: suppress every fire for the rest of that visit.
# Origin in the open = an approach: nothing changes. A meal longer than
# VISIT_GAP splits entry and exit into separate visits, which is exactly what
# makes the exit recognisable by where it began. Shared through a file for
# the same reason the cooldown is.
VISIT_FILE = os.getenv("DETER_VISIT_FILE", "/home/david/ocp-watch/.deter-visit")
VISIT_GAP = float(os.getenv("DETER_VISIT_GAP", "120"))


def _visit_update(flap_seq, verdict, now=None):
    """Record this sighting; return the visit's origin ('flap' or 'open').

    Only a burst that actually votes orange_cat may touch the state: a
    resident using the flap must not mark a visit that then suppresses a real
    approach seconds later.
    """
    if verdict != "orange_cat" or not flap_seq:
        return None
    import json
    now = time.time() if now is None else now
    origin = None
    try:
        with open(VISIT_FILE) as fh:
            st = json.load(fh)
        if now - float(st.get("last_seen", 0)) <= VISIT_GAP:
            origin = st.get("origin")
    except (OSError, ValueError):
        pass
    if origin not in ("flap", "open"):
        origin = "flap" if flap_seq[0] else "open"
    try:
        with open(VISIT_FILE, "w") as fh:
            json.dump({"last_seen": now, "origin": origin}, fh)
    except OSError:
        pass
    return origin


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


# HALF-STEP FLAP GATE (Dave, 2026-08-31). The original rule held on ANY
# overlap with the flap zone, so a cat standing AT the door -- body outside,
# deciding -- was never fired at, and the "would it back out?" experiment
# never ran. Dave: this cat is not a panicker; fire while it is AT the flap,
# hold only when it is substantially THROUGH it (a startle mid-opening could
# still hurt it or chase it inside). "Through" = the visible box is mostly
# inside the zone; standing at it, most of the body shows outside.
FLAP_COMMIT = float(os.getenv("DETER_FLAP_COMMIT", "0.5"))


def _overlap_frac(box, zone):
    """Fraction of the animal box inside the zone -- how far INTO the flap it
    is, not merely whether it is near it."""
    ax0, ay0, ax1, ay1 = box
    bx0, by0, bx1, by1 = zone
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    return ix * iy / max(1, (ax1 - ax0) * (ay1 - ay0))


def score_burst(frames):
    """Vote a burst the way server.py does.

    Returns (verdict, tally, people, at_flap) -- at_flap counts frames where
    the animal overlapped the cat flap.
    """
    verdicts, people, at_flap, boxed = [], 0, 0, 0
    flap_seq = []          # per-framed-detection: was it at the flap?
    commit_seq = []        # per-framed-detection: fraction of the box IN it
    geom = []              # (x0, area_pct) per framed detection, in time order
    for f in frames:
        if f is None:
            continue
        # Ask about people directly: best_box() hides a lone person by
        # returning None, and that is how a person reaches a cat verdict.
        # ONE detect() call serves both questions -- the second inference per
        # frame was pure duplication and doubled the decision cost.
        raw = animal.detect(f, want_person=True)
        if any(d["cls"] == animal.PERSON_CLASS for d in raw):
            people += 1
            verdicts.append("person")
            continue
        box = animal.best_box_from(raw, f.shape)
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
        commit_seq.append(_overlap_frac(box["box"], FLAP_ZONE))
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
    return verdict, tally, people, at_flap, boxed, flap_seq, commit_seq, geom


def _flush_print(msg):
    """Default logger. MUST flush: stdout is redirected to a log file, so an
    unflushed print sits in the buffer and the decision is invisible. On
    2026-08-28 the stray was held as too_far and the reason never appeared."""
    print(msg, flush=True)


def consider(grab_frames, log=_flush_print, live_track=None):
    """Called when a detector thinks it saw an orange cat.

    `grab_frames(n)` must return n fresh frames. Returns the string decision.

    `live_track` (optional): newest-last [(verdict, box, frame_height_px)]
    from the caller's OWN per-frame classifications over the last ~1.5s. At
    the patrol's 0.33s cadence, two consecutive live orange verdicts are the
    same two-distinct-frames protection the burst re-vote provides -- but in
    0.7s instead of ~5. On the 09-01 breach the burst vote scored 0 orange on
    a small fresh cat and spent five of its nine visible seconds confirming;
    the live track is how the first glimpse becomes the engagement.
    """
    # FIRST SIGHT: pull the valve into fast-poll now, while we spend the next
    # second scoring the burst. Cold it answers in ~2s, warm in ~0.29s, and
    # this is the only place we get that second for free.
    if WATER_ENABLED and ARMED:
        water_warm()

    frames = [x for x in (grab_frames(BURST) or []) if x is not None]
    if not frames:
        log("  deterrent: no burst frames available -- standing down")
        return "no_frames"

    verdict, tally, people, at_flap, boxed, flap_seq, commit_seq, geom = \
        score_burst(frames)
    orange = tally.get("orange_cat", 0)
    live = live_track or []
    live_orange = sum(1 for v, _, _ in live if v == "orange_cat")
    # The caller's live boxes are FRESHER than any burst frame: append them so
    # the flap and range checks judge the cat where it is now, and so a burst
    # that could not box a small cat still gets geometry.
    for v, box, fh in live:
        if box is None or not fh:
            continue
        boxed += 1
        near = _overlaps(box, FLAP_ZONE)
        flap_seq.append(near)
        commit_seq.append(_overlap_frac(box, FLAP_ZONE))
        geom.append((box[0], 100.0 * (box[3] - box[1]) / fh))
        if near:
            at_flap += 1
    confirmed_orange = orange >= MIN_ORANGE or live_orange >= MIN_ORANGE
    detail = (f"burst={len(frames)} orange={orange} live_orange={live_orange} "
              f"people_frames={people} at_flap={at_flap} tally={tally}")
    origin = _visit_update(
        flap_seq, "orange_cat" if confirmed_orange else verdict)

    if people:
        note = ""
        try:
            import cv2 as _cv2
            d = os.path.join(PERSON_DIR,
                             time.strftime("standdown-%Y%m%d-%H%M%S"))
            os.makedirs(d, exist_ok=True)
            for i, f in enumerate(frames):
                _cv2.imwrite(os.path.join(d, f"{i:02d}.jpg"), f)
            note = f" Burst saved to {d}."
        except Exception:
            pass
        log(f"  deterrent: PERSON in {people} of {len(frames)} burst frames "
            f"-- standing down.{note} {detail}")
        return "person"
    # WHERE THE CAT IS NOW is what matters -- the sound lands in the future,
    # so a burst-wide majority is the wrong test. Judge the most recent
    # detections. Since 08-31 (half-step, Dave's call): a cat merely AT the
    # flap is a legitimate target -- the hope is that it backs out and runs --
    # and only a cat substantially THROUGH the opening holds the fire.
    recent = commit_seq[-FLAP_RECENT:] if commit_seq else []
    if recent and any(c >= FLAP_COMMIT for c in recent):
        # WATER DOES NOT HOLD HERE (Dave, 09-07). The sound hold stands -- a
        # startle mid-opening can make a cat thrash against the flap -- but a
        # soaking delivered AS it enters is aversive contingent on the act
        # itself, which is cleaner conditioning than withholding and letting
        # it eat. On 08-31 we held fire here and it ate for 16 minutes. "Even
        # if the cat slips in the house, it will not be happy."
        # ...but NOT on the way out. A cat leaving through the flap is
        # geometrically "into the flap" too, and this branch sits ABOVE the
        # exiting check below -- so without this guard the barrier spray would
        # fire on every exit, at a cat that has already eaten. Exits are the
        # one case where firing teaches only that the deterrent is harmless.
        if confirmed_orange and _in_window() and origin != "flap":
            if _fire_water_async(log, delay=0.0, why="cat INTO the flap",
                                 sustain=WATER_FLAP_SUSTAIN):
                log(f"  deterrent: cat is INTO the flap (max "
                    f"{max(recent):.0%} of box in the zone) -- holding the "
                    f"SOUND, firing WATER. {detail}")
                return "in_flap_water"
        log(f"  deterrent: cat is INTO the flap in the latest {len(recent)} "
            f"detection(s) (max {max(recent):.0%} of box in the zone) -- "
            f"standing down; a startle mid-opening could hurt it. {detail}")
        return "in_flap"
    if False:
        log(f"  deterrent: animal AT THE FLAP in {at_flap} of {boxed} framed "
            f"detections -- standing down. A cat part-way through could bolt "
            f"back inside. {detail}")
        return "at_flap"
    if origin == "flap":
        log(f"  deterrent: EXITING -- this visit was first seen at the flap, "
            f"so the cat came OUT of the door with the meal already eaten. A "
            f"sound now cannot deter and only teaches it the noise is "
            f"harmless -- standing down. {detail}")
        return "exiting"
    opener, volume = SOUND_SEQUENCE[0], 1.0
    # Default ladder: continue past the drill. Close engagements override
    # below with the harshest-first rapid ladder.
    ladder, rapid = SOUND_SEQUENCE[1:], False
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
        far = height < MIN_HEIGHT_PCT and not closing
        if far and not URGENT_OPENER:
            # N2 (2026-08-31): a far cat is no longer a hold. On 08-31 the
            # stray crossed the patio in ~4s; "too far" spent the only shot
            # waiting for proof it was coming toward the door it always comes
            # toward. Fire the graded opener on first sight instead; the
            # ladder starts AT the drill.
            opener, volume = _far_opener(), FAR_VOLUME
            ladder = SOUND_SEQUENCE[:]
        else:
            # Close engagement OR urgent-opener mode (09-04): a race, not a
            # negotiation (09-01 breach: entry 3s after the drill). Fire the
            # startle immediately, then escalate ~1s apart.
            rapid = True
            if SIREN_OPENS:
                # Siren onset opens (09-06, Dima's hypothesis); drill is now
                # the first repeat, then harshest, then dogs.
                opener, volume = SIREN_OPEN, 1.0
                ladder = ([SOUND_SEQUENCE[0]] + SOUND_SEQUENCE[-1:]
                          + SOUND_SEQUENCE[1:-1])
            else:
                # Short loud drill first (opener, set above at vol 1.0), then
                # full siren, then harshest repeat.
                ladder = [SIREN] + SOUND_SEQUENCE[-1:] + SOUND_SEQUENCE[1:-1]
            if far:
                log(f"  deterrent: URGENT opener -- cat reads far "
                    f"(height {height:.1f}%) but this cat dashes to the flap in "
                    f"~6s; opening now, not with a slow growl. {detail}")
            elif closing and height < MIN_HEIGHT_PCT:
                log(f"  deterrent: cat is CLOSING on the door "
                    f"(x {geom[0][0]}->{geom[-1][0]}, height {height:.1f}%) -- firing now so "
                    f"the sound lands as it arrives. {detail}")

    if not confirmed_orange:
        log(f"  deterrent: only {orange} burst / {live_orange} live orange "
            f"frame(s), need {MIN_ORANGE} of either -- standing down (one "
            f"frame is the false-positive signature). {detail}")
        return "too_few"
    if not _in_window():
        log(f"  deterrent: confirmed orange cat but outside the "
            f"{HOUR_FROM:02d}:00-{HOUR_TO:02d}:00 window -- not sounding. {detail}")
        return "out_of_hours"
    since = time.time() - _last_fire_at()
    if since < COOLDOWN:
        if rapid and since >= CLOSE_TAKEOVER:
            log(f"  deterrent: shared cooldown is STALE ({since:.0f}s since the "
                f"last sound; a live ladder refreshes every few seconds) -- "
                f"close engagement takes it over. {detail}")
        else:
            log(f"  deterrent: confirmed, but another process fired {since:.0f}s ago "
                f"({COOLDOWN:.0f}s shared cooldown). {detail}")
            return "cooldown"

    if not ARMED:
        log(f"  deterrent: WOULD HAVE PLAYED a sound now (dry run). {detail}")
        return "would_fire"

    # Claim the cooldown BEFORE playing, so a second process racing us on the
    # same cat backs off rather than doubling up while we are mid-request.
    _mark_fired()
    _FIRE_PLAN["ladder"] = ladder
    _FIRE_PLAN["rapid"] = rapid
    # Water starts FIRST because it must land LAST: the thread sleeps
    # WATER_LEAD while _play blocks on ~1.2s of speaker latency, so the sound
    # arrives first and the water ~0.5s behind it. Starting it after _play
    # would put it a full sound-length late; starting it inline would put it
    # a second early. Own thread, so it cannot queue behind playback.
    watered = _fire_water_async(log, delay=WATER_LEAD, why="engagement")
    try:
        _play(opener, log, volume=volume)
        graded = (f" at volume {volume:.1f} (far opener; height "
                  f"{geom[-1][1]:.1f}%)" if volume < 1.0 and geom else
                  (" (close engagement: rapid ladder)" if rapid else ""))
        log(f"  deterrent: PLAYED {opener}{graded}"
            f"{' + WATER' if watered else ''}. {detail}")
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
INSIDE_SEG_DIR = os.getenv("DETER_INSIDE_SEG_DIR", "/dev/shm/ocp/inside")
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


def capture_reaction(tag, after=None, log=_flush_print, seg_dir=None):
    """Save a clip spanning the seconds before and after a deterrent fire.

    `seg_dir` chooses which camera's rolling buffer to record (default the
    outside cat-door camera); pass INSIDE_SEG_DIR to record the inside camera,
    e.g. the cat's reaction to the indoor entry response.

    ffmpeg is writing into these files continuously, so a segment must only be
    copied once it has STOPPED changing -- copying mid-write yields a truncated
    file, and concat then silently drops most of it (a 70s capture came out as
    25s of video before this was fixed). We therefore watch (mtime, size) and
    take a segment only when it has been stable across two polls.
    """
    after = REACT_AFTER if after is None else after
    seg_dir = SEG_DIR if seg_dir is None else seg_dir
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
        for p in glob.glob(os.path.join(seg_dir, "*.mp4")):
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
# The 8081 fallback server runs on THIS box, whose address differs by house
# (see config.self_host). Never hardcode it again.
_SELF = config.SELF_SOUND_BASE

SOUND_BASE = os.getenv("DETER_SOUND_BASE", _SELF)

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
# catsfight (screened by Dave 08-31) is the final rung: the loudest
# conspecific alarm, kept rare.
SOUND_SEQUENCE = [s.strip() for s in os.getenv(
    "DETER_SOUNDS",
    "DRILL_boost3.wav,dog_bark_big.wav,dog_growl.wav,dog_bark.wav,catsfight.mp3"
).split(",") if s.strip()]

# Dima's siren (his suggestion, 2026-09-02). Already sitting on the HA box
# under /homeassistant/www, so it is the one sound with ZERO tunnel fetch --
# HA serves it to the speaker off its own disk. A full URL here bypasses
# SOUND_BASE (see _play). Close ladder only: the last meter is where sound
# is losing, and novel + harsh is the counter Dima asked to try.
SIREN = os.getenv(
    "DETER_SIREN",
    "http://192.168.1.133:8123/local/sounds/archived/siren.wav")

# SIREN AS OPENER (2026-09-06, Dima's hypothesis / Dave). Dima built the siren
# to be very startling and believes it should deter -- but it has only ever
# played as the ladder's SECOND sound, ~3s after the drill, arriving as the
# cat is already in the flap (drill playback + the rapid re-check eat the gap;
# 09-05/06 both entries: siren logged at the same second as VANISHED AT FLAP).
# So the siren has never had a fair shot at an APPROACHING cat. This opens a
# close/urgent engagement with the siren's startling ONSET instead of the
# drill, fired the instant the cat is seen. It is a 1.6s cut of the siren
# (siren_open.wav, served off the 8081 fallback) -- short so it does not
# block the speaker the way the full 6s siren would. The drill becomes the
# first repeat. Set DETER_SIREN_OPENS=0 to restore the drill opener.
SIREN_OPENS = os.getenv("DETER_SIREN_OPENS", "1") == "1"
SIREN_OPEN = os.getenv("DETER_SIREN_OPEN", f"{_SELF}/siren_open.wav")

# GRADED THREAT (Dave's design, 2026-08-31). A drill blast from a not-loud
# speaker at a cat most of a patio away is not credible; a sustained growl is
# -- a real predator announces itself at a distance and escalates as range
# closes. So a FAR first sight opens with a menacing growl at reduced volume
# (quiet reads as "nearby animal", and spares the neighbours), and the drill
# startle waits until the cat is close. The two openers rotate NIGHTLY, not
# per fire: habituation is fought across nights, while within one night a
# consistent voice reads as one animal.
FAR_VOLUME = float(os.getenv("DETER_FAR_VOLUME", "0.6"))
FAR_OPENERS = [s.strip() for s in os.getenv(
    "DETER_FAR_OPENERS", "angrycat_full.mp3,guarddogs_far.mp3"
).split(",") if s.strip()]

# URGENT OPENER (2026-09-04, Dave). The graded design opened a FAR cat with a
# long, quiet growl (guarddogs_far is 10s, angrycat 12.5s) so a distant animal
# gets a credible warning rather than a startle. But this cat does not linger
# at range -- it dashes gate-to-wall-to-flap in ~6s, and on 09-04 it read as
# "far" (9.7% height, small at the frame edge) the whole way in. The 10s far
# opener then MONOPOLISED the speaker -- _play blocks until a sound finishes --
# so no louder sound could follow during the entire entry, and the one sound
# was a quiet growl the cat ignored. With this on, a "far" read opens with the
# short, loud drill and the rapid ladder instead, so the response is sharp
# immediately and can escalate inside the 6s window. Set to 0 to restore the
# graded far opener. (Sounds are inaudible indoors -- Dima 09-03 -- so "loud"
# costs the family nothing.) Supersedes the far half of [[graded-threat]].
URGENT_OPENER = os.getenv("DETER_URGENT_OPENER", "1") == "1"


def _far_opener():
    return FAR_OPENERS[time.localtime().tm_yday % len(FAR_OPENERS)]


# INDOOR ENTRY RESPONSE (Dima's suggestion, 2026-09-03 18:18: "you can also
# try playing a sound at the cat as it enters through the flap. Just don't
# play the siren on the inside camera"). Fired ONLY from the escalation's
# vanished-at-the-flap branch, which by construction follows a tracked
# orange engagement -- never on inside motion alone; the residents use that
# flap too. The delay lets the cat clear the flap first (the in_flap welfare
# rule stands: no startle mid-opening).
#
# ENTITY TRAP, verified 2026-09-03 against HA states -- three near-identical
# names, one of which must NEVER play a deterrent:
#   media_player.nursery_speaker_2  = Cat Door OUTSIDE speaker (config.HA_SPEAKER)
#   media_player.garage_speaker     = Cat Door INSIDE speaker  (this one)
#   media_player.nursery_speaker    = a NEST MINI in the family's room. Never.
INDOOR_ENABLED = os.getenv("DETER_INDOOR", "1") == "1"
INDOOR_SPEAKER = os.getenv("DETER_INDOOR_SPEAKER", "media_player.garage_speaker")
# Played in order, one gap apart: an animal threat, then the strongest
# startle on record -- a human voice (Dima's own, 08-29: the window "HEY" is
# the only thing that has ever emptied the patio instantly), and a voice is
# at its most credible coming from INSIDE the house. The voice clip is not
# in the HA-staged set, so it plays by full URL off the odd-fellow fallback
# server; indoors there is no race, so the tunnel fetch costs nothing.
INDOOR_SOUNDS = [s.strip() for s in os.getenv(
    "DETER_INDOOR_SOUNDS",
    f"dog_growl.wav,{_SELF}/Poshel-Otsuda.wav"
).split(",") if s.strip()]
INDOOR_VOLUME = float(os.getenv("DETER_INDOOR_VOLUME", "0.6"))
INDOOR_DELAY = float(os.getenv("DETER_INDOOR_DELAY", "2.0"))
INDOOR_GAP = float(os.getenv("DETER_INDOOR_GAP", "1.0"))


# --- water ------------------------------------------------------------------
# The 12V-solenoid plan became a SONOFF SWV-ZFU (Zigbee ball valve) that Dima
# wired up 2026-09-07. Measured that afternoon at his house:
#
#   warm (pinged 1s earlier)   0.29s command -> acknowledged
#   30s idle                   0.66s
#   cold (30+ min)             1.7-2.0s
#
# It is a battery EndDevice, so commands queue at its parent until it polls.
# Hence the keepalive below: without it the first shot of the night is ~2s
# late, which is a third of this cat's gate-to-flap dash.
WATER_ENABLED = os.getenv("DETER_WATER", "0") == "1"
WATER_ENTITY = os.getenv("DETER_WATER_ENTITY", "switch.cat_sprayer")

# Rapid bursts, not one stream (Dima, 09-07): a pulsed stimulus is harder to
# habituate to and reads as something REACTING to the cat rather than a fixed
# feature of the patio. Half-second spacing is the floor -- each transition
# costs one ~0.29s command.
#
# Measured 09-07 with the keepalive running: EVERY command costs ~0.4s, so a
# 3-pulse burst is 7 commands = ~2.8s before any sleep of ours. The command
# latency IS the pulse width; the sleeps below only trim it. Generous sleeps
# (0.4/0.35) stretched the burst to 6.1s wall time -- longer than the cat's
# whole gate-to-flap dash, so the last pulses hit an empty patio.
WATER_PULSES = int(os.getenv("DETER_WATER_PULSES", "3"))
WATER_PULSE = float(os.getenv("DETER_WATER_PULSE", "0.15"))
WATER_GAP = float(os.getenv("DETER_WATER_GAP", "0.1"))

# AT THE FLAP THE SHAPE CHANGES (Dima's question, 09-07). The burst above is
# shaped for STARTLE at range -- three onsets, because a cat reacts to onset,
# not duration (the same finding that made the 0.85s drill beat longer sounds).
# At the door startle is the wrong goal: a startled cat at the flap may go
# FORWARD, which is the failure we keep losing to. What is wanted there is a
# BARRIER -- a wall of water it must cross, with no gaps between pulses to slip
# through. So the flap gets ONE sustained spray instead.
#
# Triggered by GEOMETRY, not by a stopwatch: the burst's three pulses land on a
# fixed clock (~2.2/3.4/4.6s after the decision) which only lines up with the
# door if the cat runs its ~6s dash -- and this one also loiters. The flap
# branches already know where the cat is; they now pass sustain=.
#
# Water through an open flap lands in Dima's GARAGE, not the house (Dave,
# 09-07), which is why sustained is acceptable here.
WATER_FLAP_SUSTAIN = float(os.getenv("DETER_WATER_FLAP_SUSTAIN", "2.0"))

# Sound must LEAD the water, so the sound becomes a signal that PREDICTS it --
# the speaker reaches the whole patio, the water only reaches where it is
# aimed. Simultaneous pairing conditions poorly and water-first conditions
# backwards. The two channels have opposite latencies (~1.2s for HA speaker
# playback vs ~0.29s for a warm valve), so firing both on the same line would
# land the water a second BEFORE the sound. This delay is measured from the
# moment the play command is issued.
WATER_LEAD = float(os.getenv("DETER_WATER_LEAD", "1.7"))

# Keepalive. Every 30s inside the night window holds ~0.66s; going to 10s all
# night buys only 0.14s for triple the traffic. The win is switching to ~1s
# the moment anything is seen, which reaches 0.29s within a couple of pings.
WATER_KEEPALIVE = float(os.getenv("DETER_WATER_KEEPALIVE", "30"))
WATER_FAST = float(os.getenv("DETER_WATER_FAST", "1.0"))
WATER_FAST_FOR = float(os.getenv("DETER_WATER_FAST_FOR", "20"))

# consider() can run every ~0.33s during an engagement. Without this the
# valve would cycle dozens of times per visit -- motor wear on a ball
# valve, and the burst stops reading as a discrete event.
WATER_COOLDOWN = float(os.getenv("DETER_WATER_COOLDOWN", "8"))

_water_fast_until = 0.0
_water_last = 0.0
# water_warm() must interrupt the keepalive's sleep. A bare sleep(30)
# means a first-sight warm does nothing for up to 30s -- precisely the
# seconds it exists to buy (found by test, 09-07).
_water_wake = threading.Event()
# Set while a burst is in flight. The keepalive pings with turn_off, which
# is idempotent ONLY while the valve is meant to be shut -- mid-fire it
# slams the valve closed. Cost a 2.0s sustained spray after 0.6s in
# testing, 09-07, and was probably truncating every pulse too.
_water_busy = threading.Event()


def _water_svc(service, timeout=20):
    """One switch service call. turn_off is idempotent and cannot wet anything."""
    import json as _json, urllib.request, os as _os
    import config
    token = _os.getenv("HA_LONG_LIVED_TOKEN")
    if not token:
        raise RuntimeError("HA_LONG_LIVED_TOKEN not set")
    req = urllib.request.Request(
        f"http://{config.HA_HOST}:8123/api/services/switch/{service}",
        data=_json.dumps({"entity_id": WATER_ENTITY}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout):
        pass
    return time.monotonic() - t


def water_warm(seconds=None):
    """Pull the valve into fast-poll -- call on FIRST SIGHT, not before firing."""
    global _water_fast_until
    _water_fast_until = max(_water_fast_until,
                            time.time() + (seconds or WATER_FAST_FOR))
    _water_wake.set()          # cut the keepalive's sleep short


def _water_keepalive_loop():
    while True:
        fast = time.time() < _water_fast_until
        try:
            if (_in_window() or fast) and not _water_busy.is_set():
                _water_svc("turn_off", timeout=25)
        except Exception:
            pass          # a missed ping costs latency, never correctness
        gap = WATER_FAST if time.time() < _water_fast_until else WATER_KEEPALIVE
        _water_wake.wait(gap)
        _water_wake.clear()


def fire_water(log=_flush_print, delay=0.0, why="", sustain=None):
    """Pulse the valve, or hold it open for `sustain` seconds at the flap.

    ALWAYS closes it, whatever happens on the way. Runs on its own thread:
    _play() blocks until playback ends (that is what monopolised the speaker
    through an entire entry on 09-04), and water must never queue behind it.
    """
    _water_busy.set()          # hold off the keepalive; see _water_busy
    try:
        if delay:
            time.sleep(delay)
        if sustain:
            # One continuous barrier, not a startle. Bounded well under the
            # 10s HA failsafe and the >15s stuck-open watch.
            _water_svc("turn_on")
            time.sleep(sustain)
            _water_svc("turn_off")
            log(f"  water: FIRED sustained {sustain:.1f}s"
                f"{(' -- ' + why) if why else ''}")
            return
        for i in range(WATER_PULSES):
            if _aborted():
                log(f"  water: ABORTED by hand after {i} pulse(s)")
                break
            _water_svc("turn_on")
            time.sleep(WATER_PULSE)
            _water_svc("turn_off")
            if i < WATER_PULSES - 1:
                time.sleep(WATER_GAP)
        log(f"  water: FIRED {WATER_PULSES} x {WATER_PULSE:.2f}s"
            f"{(' -- ' + why) if why else ''}")
    except Exception as exc:
        log(f"  water: FAILED ({exc})")
    finally:
        # Belt and braces. automation.cat_sprayer_failsafe_off on Dima's HA
        # closes it 10s after any on -- this is the faster of the two, and the
        # valve has NO device-side timer (no ZHA quirk applied). Clear _busy
        # only AFTER the close, so the keepalive cannot resume mid-shutdown.
        try:
            _water_svc("turn_off")
        except Exception as exc:
            log(f"  water: FINAL CLOSE FAILED ({exc}) -- valve may be OPEN")
        finally:
            _water_busy.clear()


def _fire_water_async(log=_flush_print, delay=0.0, why="", sustain=None):
    global _water_last
    if not (WATER_ENABLED and ARMED):
        return False
    since = time.time() - _water_last
    if since < WATER_COOLDOWN:
        log(f"  water: held ({since:.1f}s since the last burst, "
            f"{WATER_COOLDOWN:.0f}s valve cooldown)")
        return False
    _water_last = time.time()
    threading.Thread(target=fire_water,
                     kwargs={"log": log, "delay": delay, "why": why,
                             "sustain": sustain},
                     daemon=True).start()
    return True


# Started at import so the patrol gets it for free. Gated on ARMED so replays
# (evaluate_deter forces DETER_ARM=0) and dry runs never touch Dima's valve.
if WATER_ENABLED and ARMED:
    threading.Thread(target=_water_keepalive_loop, daemon=True).start()


# How consider() tells the escalation thread what to play next. `ladder` is
# the ordered list of repeats AFTER the opener; `rapid` compresses the 2-3s
# escalation interval to ~1s. CLOSE-RANGE DOCTRINE (09-01): on the breach at
# 01:58 the cat entered 3s after the drill, having heard one follow-up at a
# polite interval. A close engagement is a race, so it gets the harshest
# repeat first (catsfight) and ~1s spacing; a far engagement keeps the
# graded, spaced ladder -- menace, then startle, then dogs.
_FIRE_PLAN = {"ladder": None, "rapid": False}

# Where a person stand-down saves its evidence. The 02:09:21 stand-down was
# judged on six in-memory frames and discarded; whether it was a real person
# or YOLO misreading the cat is now unknowable. This gate is rare and
# safety-critical -- keep what it saw.
PERSON_DIR = os.getenv("DETER_PERSON_DIR", "/home/david/ocp-watch/person-evidence")


def _play(name, log=print, volume=1.0, entity=None):
    """Play one sound, by URL, through the camera speaker.

    `entity` overrides the target speaker (default config.HA_SPEAKER, the
    OUTSIDE cat-door camera). The only sanctioned override is INDOOR_SPEAKER
    -- see the entity-trap comment above its definition.

    `volume` is set explicitly on EVERY play (the speaker supports
    volume_set; verified 2026-08-31): the far opener plays reduced, the
    startle sounds full, and stateless per-call setting means a crash between
    sounds can never leave the speaker stuck quiet for the next fire.
    """
    import json as _json, urllib.request, os as _os
    token = _os.getenv("HA_LONG_LIVED_TOKEN")
    if not token:
        raise RuntimeError("HA_LONG_LIVED_TOKEN not set")
    import config

    def _svc(service, payload, timeout=10):
        req = urllib.request.Request(
            f"http://{config.HA_HOST}:8123/api/services/media_player/{service}",
            data=_json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout):
            pass

    target = entity or config.HA_SPEAKER
    try:
        _svc("volume_set", {"entity_id": target,
                            "volume_level": max(0.0, min(1.0, volume))})
    except Exception as exc:
        # A failed volume call must not silence the fire itself.
        log(f"  deterrent: volume_set failed ({exc}) -- playing anyway")
    # play_media BLOCKS until playback finishes (measured 09-02: both 17s
    # screams were logged "failed (timed out)" at the old 10s timeout while
    # audibly playing -- and the false failure killed the rest of the
    # ladder). 25s covers the longest file with margin; blocking through a
    # sound is fine, nothing else should be playing over it anyway.
    _svc("play_media", {"entity_id": target,
                        "media_content_id": (name if name.startswith("http")
                                             else f"{SOUND_BASE}/{name}"),
                        "media_content_type": "music"}, timeout=25)


def _esc_params():
    import config
    return (ESC_MIN or config.SOUND_MIN_INTERVAL,
            ESC_MAX or config.SOUND_MAX_INTERVAL,
            ESC_MAX_DURATION or config.SOUND_MAX_DURATION)


def escalate(grab_frames, log=_flush_print, already_played=1, ladder=None,
             rapid=False):
    """Keep sounding while the cat stays, re-checking every gate each time.

    `ladder` is the ordered list of repeats after the opener (defaults to the
    sequence past the drill). `rapid` compresses the interval to ~1s -- the
    close-range doctrine: a cat a body-length from the door decides in the
    next two seconds, not the next six.
    """
    import config
    import talk
    lo, hi, max_dur = _esc_params()
    started = time.time()
    played = already_played
    ladder = list(ladder if ladder is not None else SOUND_SEQUENCE[1:])
    last_near_flap = False

    while played < ESC_MAX_SOUNDS:
        time.sleep(random.uniform(0.8, 1.2) if rapid
                   else random.uniform(lo, hi))
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

        verdict, tally, people, at_flap, boxed, flap_seq, commit_seq, geom = \
            score_burst(frames)
        orange = tally.get("orange_cat", 0)

        if people:
            log(f"  escalation: PERSON appeared -- stopping after {played}")
            return played
        # Half-step (08-31): keep sounding at a cat standing AT the flap --
        # that is the back-out-and-run experiment -- and stop only once it is
        # substantially THROUGH the opening.
        recent_commit = commit_seq[-FLAP_RECENT:] if commit_seq else []
        if recent_commit and any(c >= FLAP_COMMIT for c in recent_commit):
            watered = _fire_water_async(log, delay=0.0,
                                        why="cat INTO the flap (escalation)",
                                        sustain=WATER_FLAP_SUSTAIN)
            log(f"  escalation: cat is INTO the flap "
                f"(max {max(recent_commit):.0%} of box in the zone) -- "
                f"stopping the ladder after {played}, no startle mid-opening"
                f"{'; WATER fired' if watered else ''}")
            return played
        if orange < MIN_ORANGE:
            if last_near_flap:
                # It did not walk off camera -- it vanished at the door. On
                # 09-01 this exact moment was logged "target gone" while the
                # cat was starting an 11-minute meal. Say what happened, and
                # mark the visit so a re-emergence inside the chain gap is
                # treated as the exit it is.
                log(f"  escalation: target VANISHED AT THE FLAP -- likely "
                    f"went INSIDE, not away. Stopping after {played} sound(s)")
                try:
                    import json as _json
                    with open(VISIT_FILE, "w") as fh:
                        _json.dump({"last_seen": time.time(),
                                    "origin": "flap"}, fh)
                except OSError:
                    pass
                # Record the INSIDE camera on every recognized entry (09-05),
                # independent of whether a sound plays -- the eviction footage
                # (09-05: the cat sat calm through the growl and voice) was
                # otherwise only saved by a lucky manual grab before the buffer
                # wrapped. Threaded so it never blocks the indoor sounds below.
                threading.Thread(
                    target=capture_reaction,
                    args=(f"inside-{time.strftime('%Y%m%d-%H%M%S')}",),
                    kwargs={"seg_dir": INSIDE_SEG_DIR, "log": log},
                    daemon=True).start()
                # Indoor entry response (Dima, 09-03): meet it on the other
                # side. This branch only runs after a tracked orange
                # engagement, so a resident coming through its own door can
                # never trigger it. Delay lets it clear the flap first.
                if INDOOR_ENABLED:
                    time.sleep(INDOOR_DELAY)
                    for i, snd in enumerate(INDOOR_SOUNDS):
                        if _aborted():
                            break
                        if i:
                            time.sleep(INDOOR_GAP)
                        try:
                            _play(snd, log, volume=INDOOR_VOLUME,
                                  entity=INDOOR_SPEAKER)
                            log(f"  indoor: PLAYED {snd} at volume "
                                f"{INDOOR_VOLUME:.1f} on the INSIDE speaker "
                                f"({INDOOR_SPEAKER})")
                        except Exception as exc:
                            log(f"  indoor: playback FAILED ({exc}) -- "
                                f"stopping the indoor pair")
                            break
            else:
                log(f"  escalation: target gone (orange={orange}) -- "
                    f"stopping after {played} sound(s)")
            return played
        last_near_flap = bool(flap_seq) and flap_seq[-1]

        idx = played - already_played
        if idx >= len(ladder):
            log(f"  escalation: ladder exhausted after {played} sound(s)")
            return played
        sound = ladder[idx]
        _mark_fired()          # keep the shared cooldown claimed
        try:
            _play(sound, log)
            played += 1
            log(f"  escalation: sound {played} -- {sound} (still present, "
                f"orange={orange}{', rapid' if rapid else ''})")
        except Exception as exc:
            log(f"  escalation: playback failed ({exc}) -- stopping")
            return played

    log(f"  escalation: reached the {ESC_MAX_SOUNDS}-sound cap")
    return played


def consider_and_escalate(grab_frames, log=_flush_print, escalate_grab=None,
                          live_track=None):
    """consider(), and if it fired, keep escalating in the background.

    `escalate_grab` MUST return genuinely fresh frames on every call. The
    decision to fire may be made on a burst that was already in hand (server.py
    passes the frames from the motion POST), but the repeat loop asks "is the
    cat STILL there, and NOT in the flap" every 2-3s, and a grabber that hands
    back the same frames answers "yes, and no" forever. That happened on
    2026-08-29 at 02:56: sounds 2-6 played at a cat the patrol could see in
    the flap zone. Defaults to `grab_frames` for callers whose grabber is
    already live (the patrol).
    """
    decision = consider(grab_frames, log=log, live_track=live_track)
    if decision == "fired":
        threading.Thread(target=escalate,
                         args=(escalate_grab or grab_frames,),
                         kwargs={"log": log,
                                 "ladder": _FIRE_PLAN["ladder"],
                                 "rapid": _FIRE_PLAN["rapid"]},
                         daemon=True).start()
    return decision
