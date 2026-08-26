# Running the detector on a second machine (zyxx)

Written 2026-08-24, when Dave's laptop had to go to Reed CUS for a fan
replacement (up to 24 h) and the detector would otherwise have gone dark for a
night — which per §1 is exactly when the stray comes.

**zyxx** is the older ThinkPad. Its actual hostname is **`odd-fellow`** and
the login account is **`david`** (not `davidp` as on the laptop); "zyxx" is a
name used only in this document. It was lent to a friend and came back once his
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

## Ray's account: leave it alone

zyxx was rebuilt on 2026-07-01 as Mint (22.3 Cinnamon then; updated again
before 2026-08-25 -- confirm the release on the box). It has two accounts:
**Dave's own admin account**, which is the one the detector runs under, and
**`ray`** (standard).

Dave has his own account here, so nothing in Ray's session needs changing.
Two things to simply *know*, not fix:

- **Auto-login is ON for `ray`**, so a power-on lands in his session, not
  Dave's. Log out and switch accounts. Dave has decided (2026-08-25) not to
  chase the reboot case; a blip that reboots zyxx will take the detector down
  until someone logs in and restarts it, and that is accepted.
- Ray's session swaps Caps and Ctrl (`setxkbmap -option ctrl:swapcaps` in
  Startup Applications), which is startling if you land there unexpectedly.

His Firefox homepage tabs are Gmail and an OHSU patient portal -- his data,
his business; mention it to him if you like, but it is not a setup step.

## Access: expect to do this in person

**SSH to zyxx's Mint 22 install was never established** — during the July
setup password auth kept getting rejected despite a valid password, and the
job was finished by hand in the GUI. So do not plan on configuring this
remotely over SSH.

**Better: install Claude Code on zyxx** and have a session there work through
this document locally -- that removes the need for remote access to do the
setup at all. Either way, sit in front of it once and set up SSH properly
(installing `authorized_keys` via local sudo is the method that works) so
future maintenance of an always-on detector box is not another trip.

## Installing Claude Code on zyxx

zyxx is on a Mint/Ubuntu base with a browser and 16 GB RAM, so it is above the
4 GB / Ubuntu 20.04+ floor. Use the **native installer** -- no Node needed, no
root, and it auto-updates in the background, which matters on a box that is
meant to run unattended for months:

```
curl -fsSL https://claude.ai/install.sh | bash
claude --version          # expect e.g. "2.1.211 (Claude Code)"
claude doctor             # read-only install/settings diagnostics
```

It installs to `~/.local/bin/claude`; if that is not on PATH yet, open a new
terminal or `export PATH="$HOME/.local/bin:$PATH"`.

(The apt repository at `downloads.claude.ai/claude-code/apt` is the alternative
and is fine, but it does not auto-update through Claude Code -- upgrades only
arrive with `sudo apt upgrade`.)

**Log in** by running `claude` and following the browser prompts. Needs a Pro
or Max account -- the same one used on the laptop. Do this in the GUI session,
not over SSH, so the browser callback works.

Then:

```
cd ~/projects/ocp && claude
```

Accept the trust prompt for the directory, and point it at this file.

### What does NOT travel with the install

- **`~/.claude/CLAUDE.md`** -- Dave's global instructions (the `rm -i`/`cp -i`
  alias trap in particular, which hangs non-interactive Bash calls).
- **The project memory** at
  `~/.claude/projects/-home-davidp-projects-ocp/memory/` -- 16 notes carrying
  the ground truth, the retracted discriminators, and the network layout. A
  session on zyxx starts with none of it and will re-propose things that were
  already killed.

`stage-zyxx-bundle.sh` copies both. Do **not** copy `~/.claude.json` -- it
holds credentials and session history; log in fresh on zyxx instead.

Read `AGENTS.md` first on the new box either way; it is the technical state of
record and it does come with the clone.

## Setup

1. **Prerequisites.** zyxx was on Mint 22.3 (Ubuntu 24.04 base, Python 3.12)
   and has since been updated, so do not assume the version — the repo needs
   `>= 3.12`, confirm with `python3 -V` and `lsb_release -d`. Hardware is an i7-10510U with ~16 GB RAM, comfortably above
   what `segments` mode needs.
   ```
   sudo apt install ffmpeg
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. **Repo.** Clone it, then copy `.env` **out of band** — it is gitignored and
   holds the Protect RTSP stream keys. It will not arrive with the clone, and
   the server fails confusingly without it.
3. **WireGuard.** The profile is **`/etc/wireguard/wg0.conf`** (confirmed
   2026-08-25). The original draft said
   `/etc/NetworkManager/system-connections/wg0.nmconnection` -- that file does
   not exist, even though NM does have the connection imported from the conf
   (id `wg0`, uuid `524b70b7-bc41-48ae-a00f-b21356d93209`, autoconnect `no`).
   **The tunnel is owned by systemd, not NetworkManager.** `nmcli` shows a
   `wg0` connection, but that is NM adopting an interface `wg-quick@wg0`
   already brought up (it is `enabled` and `active`, autoconnect `no`).
   `wg-quick up wg0` and `systemctl start wg-quick@wg0` conflict -- the second
   fails with ``wg-quick: `wg0' already exists``. Use systemd throughout.

   The laptop also runs a **`wg-reresolve.timer`** (every 30 s, calling
   `reresolve-dns.sh wg0`), added after the tunnel died silently for 114
   minutes on 2026-08-24 and recorded zero events. It ships in the bundle.

   `stage-zyxx-bundle.sh` picks up the conf and both units. On zyxx:
   ```
   sudo apt install wireguard-tools
   sudo install -m 600 -o root -g root wg0.conf /etc/wireguard/wg0.conf
   sudo install -m 644 -o root -g root wg-reresolve.service wg-reresolve.timer \
     /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now wg-reresolve.timer
   sudo systemctl start wg-quick@wg0        # start, NOT enable -- see below
   ```
   The reresolve script path
   (`/usr/share/doc/wireguard-tools/examples/reresolve-dns/reresolve-dns.sh`)
   comes from the `wireguard-tools` package, so install that first or the
   timer fails every 30 s.

   **`start`, not `enable --now`, for `wg-quick@wg0`.** The laptop's copy IS
   enabled, so if both boot with it enabled they fight over `192.168.7.4`.
   Once zyxx is confirmed as the permanent detector, `sudo systemctl disable
   wg-quick@wg0` on the laptop and enable it on zyxx -- one or the other,
   never both.

   Copying the working conf also dodges the two UniFi export footguns (the
   `DNS =` line that makes `wg-quick` abort with `resolvconf: command not
   found`, and `AllowedIPs = 0.0.0.0/0`); they are already fixed in this file.
   Do not re-export from the UDM.

   Verify with `ip -brief addr show wg0` → `192.168.7.4/32`, then confirm the
   tunnel actually carries traffic:
   ```
   curl -sv rtsp://192.168.7.1:7447 --max-time 3   # expect a connection, not a timeout
   ```
4. **Background models.** Copy `frames/bg_inside.npy` and `frames/bg_outside.npy`
   across so the detector does not start cold. They are gitignored, so they
   also will not arrive with the clone. Without them the first events return
   `no_background` until the refresh thread builds a model.
5. **Disable suspend.** Do this even if you intend to leave the lid open:
   idle suspend and the power button reach the same place, and anyone else in
   the house can close a lid. A suspend does not merely pause the detector --
   on 08-23 two of them left ffmpeg alive with dead RTSP sessions that ten
   supervisor restarts could not clear, while `/health` still read healthy via
   the cold-RTSP fallback. Silent, and worse than a planned outage.
   ```
   sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
   ```
   Also set "When the lid is closed: do nothing" in Mint's Power Management.
6. **Start it.** On odd-fellow the detector is a systemd unit as of
   2026-08-25 (see "It froze on day one" below), so use
   `sudo systemctl start ocp-detector`, then `./run-server.sh status`
   for the health read. **Do not use `./run-server.sh start` there** --
   it knows nothing about the unit and will launch a second, competing
   server against the same buffers and the same port. `start` is still
   the right command on a host that has no unit installed.

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
cd ~/projects/ocp && ./run-server.sh stop        # also kills the ffmpeg recorders
sudo systemctl stop wg-quick@wg0                 # release 192.168.7.4 for zyxx
sudo systemctl disable wg-quick@wg0              # and do not grab it back on boot
```

`~` is eCryptfs-encrypted, so with the machine logged out the data is
protected at rest while it is on the repair bench.

## Tell Dima

Whatever the outcome, Dima should know when detection is down and when it is
back. A silent gap looks identical to a working system that saw nothing.


## What actually happened, 2026-08-25

Executed between 12:10 and 12:57. Downtime was **about four minutes**
(12:53 server stop -> 12:57 server start on odd-fellow).

Corrections this run forced, beyond the WireGuard ownership fix above:

- **`models/yolov8n.onnx` (13 MB) is gitignored** and was missing from every
  earlier version of this document. Without it the detector cannot run at all.
  It is a fourth out-of-band artifact alongside `.env`, the two `bg_*.npy`, and
  the tunnel config.
- **Neither machine ran sshd.** The plan assumed a USB shuttle; installing
  `openssh-server` on odd-fellow and adding the laptop's key turned the whole
  transfer into `scp` over the LAN and let the laptop drive the setup. Do this
  first next time -- it is the single biggest time saver here.
- **The laptop's own `~/.ssh/id_ed25519` is passphrase-protected** and
  gnome-keyring refused to sign for a non-interactive session
  (`agent refused operation`). A dedicated passphrase-less key
  (`~/.ssh/ocp_handover`, comment `ocp-handover-to-zyxx`) was generated for
  this. **Revoke it when the handover is over**: `rm ~/.ssh/ocp_handover*` on
  the laptop and drop that line from `~/.ssh/authorized_keys` on odd-fellow.
- **`git` was not installed** on odd-fellow. Neither was anything else beyond
  ffmpeg/wireguard-tools.
- **`sudo` cannot be driven over a non-interactive SSH session.** Every root
  step has to be a script the human runs at the box's own terminal. Two were
  used here, `root-setup-1.sh` (git + suspend masking) and `root-setup-2.sh`
  (tunnel config, units, `wg-quick` start), both staged by `scp` first.
- **Do not clone from GitHub.** `origin/roi-background-detection` was **13
  commits behind** local at handover time -- a clone would have missed both
  commits that caught the cat. A `git bundle create ocp.bundle --all` carried
  the full history instead.

Pre-flight that proved the detector before it saw a live frame: `samples/labelled`
(107 MB, gitignored) was copied across and `uv run evaluate.py` returned
**30/30 on odd-fellow** -- 5/5 orange-intruder, 24/24 our-cats, 1/1 possum, no
misses and no false alarms, matching the laptop's baseline exactly. Worth
repeating on any future host: it isolates detector correctness from the tunnel
and RTSP paths, which are the only things left to fail after it passes.

## It froze on day one — read this before trusting the box

**2026-08-25 13:07, about an hour after setup, odd-fellow locked up hard.** No
cursor, no console, off the network (ARP `FAILED`). The journal for that boot
ends mid-second at 13:07:04 with no panic, no thermal trip, no OOM, no crash
dump — the signature of a hard lockup, not overheating. Idle temps were 43 °C
against a 100 °C critical, and a power cycle recovered it immediately.

The only signal that was worsening beforehand:

    acpi_os_execute_deferred hogged CPU for >10000us
    4 -> 5 -> 7 -> 11 -> 19 -> 35 -> 67 -> 131 (12:28) ... 259 (12:52)

Check it with:

```
journalctl -b -k | /bin/grep -c "acpi_os_execute_deferred hogged"
```

A climbing count is the only early warning available. Also note `thermald`
**cannot run on this platform** ("Unsupported cpu model or platform"), and the
kernel is `7.0.0-28-generic`, very new for Mint 22.3 — a kernel regression is
as plausible as anything else here.

### What was added in response (`root-setup-3.sh`)

1. **`ocp-detector.service`** — the detector is a real systemd unit now
   (`Restart=always`, `User=david`, `Requires=wg-quick@wg0.service`). **It
   survives reboot.** Every earlier note in this project saying "not a systemd
   unit, dies on reboot" is stale for this box.
2. **`wg-quick@wg0` enabled here**, and stopped *and disabled* on the laptop.
   The hard rule still stands — if the laptop returns to service, disable one
   before the other comes up.
3. **Hardware watchdog** — `iTCO_wdt` loaded and persisted in
   `/etc/modules-load.d/watchdog.conf`, `RuntimeWatchdogSec=60`. systemd
   confirms `Using hardware watchdog 'iTCO_wdt' ... timeout of 1min`. A hard
   lockup reboots the box in ~60 s.

A freeze now costs about two minutes instead of a night.

### What is still unprotected

**Nothing outside odd-fellow can tell you it has stopped.** The watchdog covers
a lockup, but not a wedged detector on a live box. The durable fix is an alert
on Dima's Home Assistant when its POSTs stop getting a 200 — one alarm covering
a frozen box, a dead tunnel and a stopped server. It belongs on the list of
questions for Dima.
