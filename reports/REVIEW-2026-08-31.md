# Orange Cat Project — night review, 2026-08-31

Reviewer: Claude (fresh session, ocp_review). Scope: last night's logs
(`patrol.log`, `soundserver.log`, `overnight-report.txt`), the entry burst
`frames/events/outside-20260831-023323-013/`, the patrol frames, the live code
(`patrol.py`, `deter.py`), and the state left by the 08-28 review.

## 1. Bottom line

- **Last night was the first confirmed entry since the deterrent started
  landing.** The stray entered at 02:33, ate for ~16 minutes, and left. The
  only sounds it heard were on the way out.
- **Nothing malfunctioned.** Every fix from the 08-28 review that was applied
  worked: the deterrent was consulted immediately (D1 fix), the flap welfare
  gate held twice — both times correctly — the escalation judged fresh frames,
  the patrol stayed alive all night, and HA delivery worked. The system
  executed its design faithfully.
- **The design lost to a 4-second crossing.** The cat appeared at the gate at
  02:33:21, was mid-patio at 02:33:23, and at the flap by 02:33:25. The
  system's first orange verdict came from a frame stamped 02:33:25 — cat
  already at the flap, where the (correct) welfare rule forbids firing. There
  was never a legal moment to fire before the cat was inside.
- **The cat's behaviour has changed, and that is probably our doing.** On
  08-28/29 it loitered on approaches — and three fires landed, zero entries.
  On 08-29/30, one fire at the gate and it left. Last night it sprinted
  straight through the one zone where sound could reach it. n=1, but the
  direction is exactly the habituation/adaptation the 08-28 review warned
  about: we are training a faster cat.
- **R2 — the continuous low-latency loop — is still not built.** It has been
  item #1 on the "Next" list since 08-29. Last night is the failure mode it
  exists to prevent. It is no longer an improvement; it is the difference
  between a working system and a decorative one.

## 2. Timeline of the failure (all times 2026-08-31, from burned-in clocks)

    02:33:19-21  stray enters the patio at the gate (right edge; burst frame 08)
    02:33:23     mid-patio, crossing left toward the door (burst frame 11 —
                 the best and only real shot of the night)
    02:33:25     at the flap (patrol-023328.jpg, logged 02:33:28)
    02:33:29     deterrent consulted → at_flap 6/6 → stands down (CORRECT)
    ~02:33:30    inside. Indoor sound is ruled out; nothing can fire.
    02:33:23     server.py got the HA POST too; by decision time: at_flap
                 3 of 4 → stands down (CORRECT, same reason)
    02:49:47     exits through the flap (patrol-024950.jpg)
    02:49:57     patrol fires — DRILL lands ~10s after the exit began
    02:50:01     escalation sound 2 (bark); cat still visible (orange=3)
    ~02:50:05    gone. Escalation stops on target-gone (correct behaviour).

Net effect: the cat got a free meal and two loud noises on its way home.

## 3. Why no sound could fire in time — the arithmetic

**a. The patrol only sees a distinct instant every ~5 seconds.** The segment
buffer is a ring of 5.0s `-c copy` segments; the open segment has no moov atom
and is unreadable, so `newest_frame()` reads the tail of the newest *closed*
segment. The patrol cycles every ~2.3s, but until a new segment closes it
re-reads the *same* instant. Distinct sampled instants last night were ~5s
apart (…02:33:20, 02:33:25…). The entire gate→flap crossing (4s) fit inside
one hole. This is D2 from the 08-28 review, unchanged.

**b. Frame age is 0.6–5.6s on top of that.** The 02:33:25 frame was processed
at 02:33:28.

**c. The decision + sound chain adds ~2.5–4s.** deter re-decodes a burst
(~1.3s), then HA fetches the WAV over the tunnel and spawns ffmpeg to the
camera talkback (~1.3–2s). Measured on the exit fire: triggering frames
stamped ~02:49:50–53, sound in the air ~02:49:57.

**Total trigger-to-air today: ~3.5–8s against a 4s crossing.** Even a
perfectly timed detection at the gate (02:33:21) would have put sound in the
air at ~02:33:25+ — with the cat already at the flap, where the welfare gate
(rightly) refuses. With the current architecture there is *no* gate tuning,
threshold change, or cadence tweak that beats this cat. It is physics.

**d. The height gates assume a loiterer.** At first sight at the gate the cat
is borderline `too_far` (MIN_HEIGHT 20%); two seconds later it is `at_flap`.
For a fast crosser there is no state in between long enough to hit. The gates
were fitted to the loitering cat of 08-27/28, which no longer shows up.

## 4. What went right (keep all of it)

- D1 fix: the deterrent was consulted on the first orange frame, not 120s
  later. The at_flap hold was immediate and correct.
- The flap welfare gate held on entry AND released correctly on exit (fired
  only once the latest detections were clear of the flap).
- The escalation fix (94c57d5): fresh frames, stopped at "target gone" after
  2 sounds. No repeat of the 08-29 frozen-burst violation.
- Shared cooldown, reaction capture, overnight report, patrol liveness
  (heartbeats every 30 min all night), HA delivery ~7s: all worked.
- No false fires, no person events, no sounds outside the window.

## 5. Recommendations, in order

### N1 — Build R2 this week. Everything else is secondary.

One process holding the outside RTSP open (`streamer.py` already exists,
unused), YOLO on every ~250–500ms frame, a rolling track (position, height,
direction, time-in-frame). Frame age drops from 0.6–5.6s to ~0.3–1.0s and
every frame is seen — no more 5s holes. This was R2 on 08-28; last night is
the miss it predicted.

### N2 — Fire on first sight at the gate, not on "close enough".

The gate is a choke point: the cat must pass it, outside, at the start of the
crossing (burst frame 08 is the template). New rule: an orange track that
*begins* at the gate edge moving in → fire immediately. Drop `too_far` for
this case; a startle that lands mid-patio 2s later is the whole game, and the
alternative shot does not exist. Keep the flap and person gates untouched.

### N3 — Get trigger-to-air under ~1.5s.

Stage the WAVs in HA's `config/www` so the fetch is local instead of over the
tunnel; use the shortest-onset file first; measure the play_media→audible gap
from the HA logbook. Budget after N1+N2+N3: detect at gate +0.5s, decide
+0.3s, sound +1.0s ≈ **1.8s — sound lands mid-patio on a 4s crossing.** Tight
but real, and it is the only budget that closes.

### N4 — Stop firing on exits. Tonight, before anything else.

Three of the last four fires were at a fed cat leaving. An exit fire cannot
deter (the reward was already collected 16 minutes earlier), it wakes the
neighbourhood at 3am for nothing, and every harmless sound the cat hears
speeds habituation — it is anti-training. Make exit detections
capture-and-log only. This is a small patch to `deter.consider` (suppress
fire when the track/flap_seq shows the cat *emerging* from the flap) and it
can ship today, independent of R2.

### N5 — Acceptance test before re-arming the new loop.

Add last night to the corpus: the entry burst `outside-20260831-023323-013`
and `fire-20260831-024957.mp4`. The bar in `evaluate_deter.py`: replaying the
02:33 entry, the new loop must have sound in the air ≤2.5s after frame 08
(gate appearance). If the replay can't do it, the live loop won't either —
don't re-arm until it passes.

### N6 — The strategy question is now live: sound is being outrun.

Scorecard since fires began: 08-28/29 — 3 approaches, 3 fires landed, 0
entries. 08-29/30 — 1 approach, 1 fire, 0 entries. 08-30/31 — 1 approach, 0
pre-entry fires, **1 entry**. The cat is adapting faster than the system is
improving. Two non-latency moves matter more than any code:

1. **Food at night (old R6, still unanswered).** The cat spent 16 minutes at
   the bowl. That bowl is the entire reason it comes. Empty bowls 22:00–06:00
   beats every deterrent on this list. Ask Dima once, concretely.
2. **Keep the R5 scorecard honest.** Visits/night, entries per approach,
   pre-entry fires per approach. If N1–N3 don't produce pre-entry fires
   within a week of shipping, sound has lost the race and the water build
   (with D6 person-gate fixed first) is the next conversation — aimed at the
   approach, per the hardware note.

## 6. Small findings (for the record)

- `patrol.py` consults deter only when the single patrol frame scores
  `orange_cat`; the 02:49:50 exit frame scored `no_orange` (cat half-through
  the flap, 11.8% ginger) so the (useless) exit response was delayed a few
  more seconds. Moot once N4 lands; worth knowing that a half-occluded cat
  can score under the ginger floor.
- The overnight report's "DETERRENT" section greps all-time server.log lines
  into a since-21:00 report — three of the lines shown last night are from
  previous nights. Scope the grep to the window; it cost real confusion this
  morning.
- The `soundserver.log` GETs from 192.168.7.4 (16:33, 21:32 on 08-30) are
  local fetches on odd-fellow, not camera playback — no warned-sound rule
  violation. Camera playback is always a GET from 192.168.1.133 (HA).
- `frames/events` pruning (08-28 review §6) still not scheduled.

## 7. One question for Dave

The 08-28 review asked: can the food be unavailable at night? It is now the
highest-leverage open item on the project. If the answer is "asked, Dima said
no", record that in LIVE-STATE so reviews stop re-raising it.
