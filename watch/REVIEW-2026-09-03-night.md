# Night review — 2026-09-02/03: two entries, and the cooldown that allowed them

Written 05:05 by the overnight Claude session, from live logs as it happened.

## Timeline

| time | event |
|---|---|
| 21:45 | patrol stream hot on schedule |
| ~22:00–22:05 | patrol died silently (no traceback); cron restarted it at 22:05. Plausibly the DNN thread bug below, crashing natively. |
| 00:28–00:31 | Dima-side outage (UDM unreachable; our internet fine). Tunnel healed itself; the two segment-recorder ffmpegs hung on the dead TCP reads and were SIGKILLed at 00:33 — segments.py's supervisor respawned them cleanly. |
| 00:52:13 | ENGAGEMENT 1: angrycat_full fired (far opener, h=11.8%) |
| 00:52:26 | second process fired; 60s shared cooldown starts |
| 00:52:30 | escalation sound 2: DRILL_boost3 |
| ~00:52:5x | **escalate thread CRASHED** (cv2.dnn shape assertion) — ladder dead at 2 sounds |
| 00:53:24 | closing-rule fire ("firing now so the sound lands as it arrives") **BLOCKED: cooldown at 55–59s** |
| 00:53:28 | **ENTRY 1** (burst frame 13 shows it mid-flap). Flap-hold rules behaved correctly once it was committed. |
| 02:22:33 | exit after 89 min; correct exit hold; silent visit video saved |
| 02:24 | patrol restarted with the thread-safety fix (see below) |
| 04:21:33 | ENGAGEMENT 2: first orange verdict; gate-to-flap ~10s |
| 04:21:36 | DRILL fired mid-approach (close engagement, rapid ladder) |
| 04:21:41+ | repeats blocked by the shared cooldown (at_flap=8 within 5s) |
| 04:21:45 | escalation sound 2 (siren); then correctly stopped: "target VANISHED AT THE FLAP — likely went INSIDE". **ENTRY 2.** |
| 04:59:30 | exit after 38 min; correct exit hold |

## The one finding that matters

**The 60-second shared cooldown disarms the system at exactly the moment the
cat commits to the door — both entries tonight happened inside that window.**
The sequence is always: opener fires on first sight → cat keeps coming → the
close-range/closing-rule fire, the one aimed at the commit moment, is refused
because the opener was <60s ago. The cooldown exists to stop double-firing
between the patrol and detector processes, but it is scoped per-*everything*
rather than per-situation: a repeat of the same far sound and the
land-as-it-arrives close shot are treated identically.

Options to evaluate BY REPLAY (evaluate_deter.py over tonight's two bursts —
frames/events/outside-20260903-005325-438/ has the full entry sequence):

1. Exempt the closing-rule fire from the shared cooldown (or drop its
   holdoff to ~10–15s). The double-fire risk it guards against is two openers,
   not an opener plus a committed-approach shot.
2. Per-process cooldown only for the *same* trigger class.
3. Whether an exempted close shot at 00:53:24 / 04:21:41 would actually have
   landed pre-flap at measured latency (~2.1s chain).

## Also fixed tonight

- **cv2.dnn.Net is not thread-safe** — deter's escalate thread scoring a burst
  concurrently with the patrol loop corrupted the shared net (shape
  assertions; killed the 00:52 ladder at sound 2; likely also the 22:05
  silent death). Fixed: lock around setInput/forward, commit 4f3fb56;
  validated at 04:21 (ladder ran clean).
- **start-patrol.sh duplicate race** — flock guard, commit c3b2d79 (from
  yesterday's double-patrol incident).

## Open items

- segments.py ffmpeg command needs a socket timeout (`-rw_timeout`) so a
  network blip can't hang the recorders again (tonight needed a manual kill).
- HA's :8123 web port refused TCP all night after its ~00:30 reboot while its
  automations and media fetches worked — don't use that port as a health probe.
- Sound is not preventing entries (2-sound rapid ladders both times); watch
  for habituation. Exit-side behaviour is exemplary — every hold correct.
- Tripwire: **2/2 entry-nights used.**

## Evidence

reactions/fire-20260903-005226.mp4 (entry 1 engagement, 75s),
reactions/visit-20260903-022231.mp4 (exit 1),
reactions/fire-20260903-042136.mp4 (entry 2 engagement),
reactions/visit-20260903-045929.mp4 (exit 2),
frames/events/outside-20260903-005211-739.jpg, outside-20260903-005325-438/.
