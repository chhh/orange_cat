# Running the detector on a second machine (zyxx)

Written 2026-08-24, when Dave's laptop had to go to Reed CUS for a fan
replacement (up to 24 h) and the detector would otherwise have gone dark for a
night — which per §1 is exactly when the stray comes.

**zyxx** is the older ThinkPad. It was lent to a friend and came back once his
desktop was installed at his apartment, so it is free. Goal: make it the
permanent home for the detector, so Dave's daily-driver laptop stops being a
single point of failure for Dima's cat door.

Per AGENTS.md §5 a box on Dima's own LAN would be better still (no VPN
dependency, lowest latency). zyxx is second-best: it keeps the WireGuard hop
but is always on and never leaves the house. Take it as the pragmatic move,
not the ideal one.

## The WireGuard shortcut — and its one hard rule

Normally a second machine means generating a new peer on **Dima's UDM** and
editing the Home Assistant automation to point at the new tunnel address.
Neither is necessary here.

Dave's laptop is `192.168.7.4/32` on `wg0`, a NetworkManager profile, and HA
POSTs to that literal address. Because the two machines are **never up at the
same time** (the laptop is switched off at the repair bench), zyxx can simply
carry a copy of the same profile and come up as `192.168.7.4`. Nothing on
Dima's side changes: no new UDM peer, no HA automation edit, no `CAT_HOST`
change.

> **HARD RULE: never have both machines on the tunnel at once.** Same key,
> same tunnel IP — the UDM tracks one endpoint per peer and the two will fight
> over it, with symptoms that look like a flaky network rather than a
> misconfiguration. Before starting the server on one, confirm the other's
> `wg0` is down.

If zyxx ever becomes a genuinely permanent second detector running *alongside*
the laptop, stop using this shortcut and cut a proper second peer
(`192.168.7.5`) on the UDM, then repoint the HA automation.

## Before anything else: undo Ray's accessibility setup

zyxx was rebuilt on 2026-07-01 as **Mint 22.3 Cinnamon** and lent to Ray. It
has two accounts: Dave's admin account, and **`ray`** (standard). Ray's
session was deliberately configured in ways that are fine for a lent laptop
and wrong for an always-on box sitting in Dave's house:

- **Auto-login is ON for `ray`.** The machine boots straight into his session.
- **Screen lock is disabled** (so he could never get trapped at a lock screen).
- **Firefox auto-starts** with `mail.google.com` and
  `mychartweb.ohsu.edu/MyChart/` as its homepage tabs, zoom 150 %.

Together that means a power-on lands anyone in front of Ray's Gmail and his
OHSU patient portal with no lock screen in the way. Fix before anything else:

1. Disable auto-login (Login Window → Users), or switch it to Dave's account.
2. Re-enable the screen lock.
3. Sign Ray out of Gmail and MyChart in Firefox and clear the saved sessions,
   or simply remove the `ray` account once he confirms he needs nothing off it
   — he has his own desktop now.

Note Ray's session also swaps Caps↔Ctrl via `setxkbmap -option ctrl:swapcaps`
in Startup Applications, which is startling if you are not expecting it.

## Access: expect to do this in person

**SSH to zyxx's Mint 22 install was never established** — during the July
setup password auth kept getting rejected despite a valid password, and the
job was finished by hand in the GUI. So do not plan on configuring this
remotely. Sit in front of it, and while you are there set up SSH properly
(installing `authorized_keys` via local sudo is the method that works) so
future maintenance of an always-on detector box is not another trip.

## Setup

1. **Undo Ray's setup** (above): auto-login, screen lock, his Firefox sessions.
2. **Prerequisites.** zyxx runs Mint 22.3 (Ubuntu 24.04 base), which ships
   Python 3.12, so the `>= 3.12` requirement is already met — confirm with
   `python3 -V`. Hardware is an i7-10510U with ~16 GB RAM, comfortably above
   what `segments` mode needs.
   ```
   sudo apt install ffmpeg
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. **Repo.** Clone it, then copy `.env` **out of band** — it is gitignored and
   holds the Protect RTSP stream keys. It will not arrive with the clone, and
   the server fails confusingly without it.
4. **WireGuard.** Copy the `wg0` profile from the laptop's
   `/etc/NetworkManager/system-connections/` (root-only, needs sudo) and
   import it on zyxx. Verify with `ip -brief addr show wg0` → `192.168.7.4/32`,
   then confirm the tunnel actually carries traffic:
   ```
   curl -sv rtsp://192.168.7.1:7447 --max-time 3   # expect a connection, not a timeout
   ```
5. **Background models.** Copy `frames/bg_inside.npy` and `frames/bg_outside.npy`
   across so the detector does not start cold. They are gitignored, so they
   also will not arrive with the clone. Without them the first events return
   `no_background` until the refresh thread builds a model.
6. **Disable suspend.** A laptop lid-closes into suspend and the detector dies
   silently — worse than a planned outage, because nobody gets an alert saying
   alerts stopped.
   ```
   sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
   ```
   Also set "When the lid is closed: do nothing" in Mint's Power Management.
7. **Start it.** `./run-server.sh start`, then `./run-server.sh status`.

## Validation — do not skip

Starting cleanly is not evidence that it works. The tunnel, the stream keys,
and the HA automation are all separately capable of failing silently.

- `./run-server.sh status` returns healthy.
- **Wait for a real motion event** and confirm a new row appears in
  `frames/events.csv` with a sane verdict and a non-zero frame count. This is
  the only check that exercises HA → tunnel → server → RTSP end to end.
- Ideally catch one daylight event and one night (IR) event; §2 shows they run
  through completely different feature paths.

Only after a real event lands should the laptop be handed over.

## Turning the laptop off cleanly

On the laptop, before it goes to CUS:

```
cd ~/projects/ocp && ./run-server.sh stop     # also kills the ffmpeg recorders
sudo nmcli con down wg0                       # release the tunnel IP for zyxx
```

`~` is eCryptfs-encrypted, so with the machine logged out the data is
protected at rest while it is on the repair bench.

## Tell Dima

Whatever the outcome, Dima should know when detection is down and when it is
back. A silent gap looks identical to a working system that saw nothing.
