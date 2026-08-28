# Orientation for an independent review — written 2026-08-28

Written by the session that did the work, for whoever reviews it. The intent is
to save you reconstruction time and to point at the weak parts directly rather
than let you find them one by one.

## Where everything is

| what | where | size |
|---|---|---|
| detector code | `~/projects/ocp/` — `server.py`, `detect.py`, `animal.py`, `capture.py`, `segments.py`, `evaluate.py` | ~2.1k lines |
| deterrent + patrol | `~/ocp-watch/deter.py`, `patrol.py` | ~720 lines |
| memory (38 notes) | `~/.claude/projects/-home-david-projects-ocp/memory/`, index in `MEMORY.md` | |
| this session's transcript | `~/.claude/projects/-home-david-projects-ocp/b5e39f69-*.jsonl` | 31 MB |
| event archive from the laptop | `~/laptop-archive/frames/` — 942 events, 08-19..08-25 | 3.4 GB |
| ground truth (Dave-labelled) | `~/laptop-archive/ground-truth-2026-08-26.csv` | 31 events |
| replay of all 855 bursts | `~/laptop-archive/replay-current-code-2026-08-26.json` | |
| reaction videos | `~/ocp-watch/reactions/fire-*.mp4` | 3 clips |
| backups | `~/ocp-backups/` | bundle + tarballs |
| upstream | `github.com/chhh/orange_cat`, branch `roi-background-detection` @ `40e64d8` | |

Uncommitted: the sound layer (`config.py`, `cat-deterrent.toml`, `sounds/`),
`server.py` deterrent hook, and the `~/ocp-watch/` scripts, which have never
been in git at all.

## Traps that will mislead you if nobody says so

- **`frames/server.log` and `patrol.log` contain synthetic test lines** I
  injected to self-test the watches: `SELFTEST`, `NOT-REAL`, `OCP-STALE-PROBE`,
  `OCP-WARN-PROBE`, `OCP-DETER-PROBE`, and fake `orange_cat` verdicts with
  `src=SELFTEST-...`. Two fake `posted image ... 31658 bytes` lines too. Do not
  count these as detections.
- **`events.csv` has only 23 rows on this box** and starts 2026-08-25 15:35.
  The real history is in `~/laptop-archive/`. Neither is a superset.
- **`is_ir` is measured on the detected REGION, not the frame.** A cat in a dark
  corner trips it while the scene is in colour. Happened once in 28 events.
- **HA logbook timestamps are UTC.** Comparing them to local times has bitten me.
- **`no_animal` does not mean "nothing there".** People produce `no_animal`,
  because `best_box` skips the person class.

## Where I would look hardest

1. **Every threshold is fitted to one or two observations.** `MIN_HEIGHT_PCT=20`,
   `APPROACH_HEIGHT_PCT=12`, the 1.4x growth test, the 25px retreat test,
   `FLAP_ZONE`, `MIN_ORANGE=2` — all from a handful of events on 08-26..08-28,
   several from a single approach. `MIN_ORANGE` is the best-supported (31
   labelled events, 9 people, all scoring 0-1 orange) and even that is n=9 for
   the class that matters.
2. **The deterrent has never worked.** Three fires, all landing on an empty
   patio 3s after the cat left. Detection is reliable; aiming is not. See
   `memory/aiming-the-deterrent.md`.
3. **Latency is the unfixed constraint** — ~5s from frame to sound against a cat
   gone in ~3s. The architectural answer (`BUFFER_MODE=decoded`, the `streamer`
   module already in `server.py`) has not been tried.
4. **I have been wrong repeatedly, in a consistent way**: generalising from a
   single observation. Visit duration "~5 minutes" (it is 2-20, Dave corrected
   me); a `box_frac` person-gate that close-up residents would have broken;
   predicting dawn false positives would recur daily (they did not); "zero false
   fires" when one had startled a resident. Treat my inferences with more
   suspicion than my frame-reading, which has held up.
5. **The person gate is much weaker than it looks** — it detects a person in
   only 5 of 9 labelled person events. What actually blocks people is the
   2-orange-frame rule.
6. **`~/ocp-watch/` is not in git.** `deter.py`, `patrol.py`, the watch scripts
   and every reaction video exist on this laptop only.

## Things that are solid

- Firmware fix for the ACPI lockups: both triggers exercised, 58h uptime, 0 hogs.
- The ground truth and the replay methodology — replaying the archive through
  *current* code rather than trusting archived verdicts was Dave's correction
  and it is the right method.
- `evaluate.py` 30/30 on the labelled clip set.
- The diagnosis chain on HA delivery: three separate faults found and proven
  from HA's own logs, most recently `cat_motion_rpi` hanging exactly 60,073ms
  on a powered-down host.

## Open question the whole project rests on

Nobody has yet put a sound in front of this cat while it was there to hear it.
Whether sound deters it is unknown. The one datum is a resident flinching at the
drill and not leaving.
