# Re-arm after reboot — updated 2026-08-25 20:50

The detector (`ocp-detector.service`) and the tunnel (`wg-quick@wg0`) are
systemd units and come back on their own. The patrol now comes back too — an
`@reboot` crontab job was installed 20:31 (`crontab -l` to verify). **The five
watches still do not** — they die with the Claude session.

## FIRST: what state are we in?

    date; uptime -p
    cat /sys/class/power_supply/AC/online        # 1 = mains. MUST be 1.
    uname -r; cat /sys/class/dmi/id/bios_version # expect N2WET52W (1.42)
    systemctl is-active ocp-detector wg-quick@wg0
    ps -eo pid,args | /bin/grep "[p]atrol.py"    # bracket avoids self-match
    journalctl -b -k | /bin/grep -c "acpi_os_execute_deferred hogged"

**Never check for the patrol with `pgrep -f patrol.py`** -- it matches the
shell running the check and reports a false ALIVE. Use the bracket form.

## The open situation, in priority order

1. **HA delivery: root cause FOUND, fix is Dima's to apply.**
   `shell_command.cat_motion_json` curls `http://192.168.1.93:8080/motion` --
   Dave's DHCP lease on Dima's LAN, dead from home. HA's own error log says
   `return code: 7`. The automation is CORRECT and its Trace reads clean,
   because shell_command failures don't propagate. Fix and full evidence in
   `ASK-DIMA.md` (rewritten 20:45; the old snapshot theory was wrong and is
   kept at `ASK-DIMA.md.superseded-1910`). **Not yet sent.**
   Stopgap that needs no YAML edit: point the automation at
   `shell_command.cat_motion_img_vpn`, which is verified to reach us.
   Until one of those happens the patrol is the ONLY detection.
2. **Firmware update looks like it worked** -- see below. Keep the box on mains.
3. **The hardware watchdog is ARMED** as of 20:41 -- but it does NOT survive a
   reboot yet. See below.

## Firmware verdict (updated 08-25 evening)

BIOS 0.1.6 -> **0.1.42**, EC 0.1.4 -> **0.1.15**, ME -> 225.77.2497.
Load test at 20:35: `evaluate.py`, 283s of sustained 8-core load ->
**hogged 0 -> 0**. On old firmware the same kind of load went 4->35, and one
boot went 4->131 in 31 seconds before freezing. gpe16 itself still ticks
(~1.2/s under load) -- the EC storm remains, but it no longer hogs the
workqueue. Say "the hog is gone", not "ACPI is fixed".

**Untested:** the AC-transition trigger (unplugging preceded the 19:17 freeze by
4 min). It needs a human to pull the plug. **Do not test it until the watchdog
is armed** -- as of 20:41 it is, so this is now safe to try while uptime holds.

## Watchdog: ARMED, and now persistent across reboots

Armed 2026-08-25 20:41 via `sudo modprobe iTCO_wdt`. Made permanent 08-26 09:23:

    echo iTCO_wdt | sudo tee -a /etc/initramfs-tools/modules
    sudo update-initramfs -u

Verified by inspection: `lsinitramfs /boot/initrd.img-7.0.0-28-generic` now
contains `kernel/drivers/watchdog/iTCO_wdt.ko.zst`. This matters because
systemd claims the watchdog EARLY -- if the module only loads later,
`RuntimeWatchdogSec=60` finds no device and nothing is armed. The initramfs
guarantees it is present first.

**NOT yet proven by an actual reboot.** After the next natural reboot, confirm:

    journalctl -b | /bin/grep "Using hardware watchdog"   # must appear at BOOT
    cat /sys/class/watchdog/watchdog0/timeleft            # 60 pinned = being pet

If that line is absent, the initramfs route did not take; fall back to
`sudo modprobe iTCO_wdt` and investigate. Loading the module is NOT the same as
arming it -- a `timeleft` counting down to 0 means nobody is petting it.

With this in place the recovery chain closes with no human: lockup -> hardware
reset in ~60s -> detector and tunnel return as systemd units -> patrol returns
via the `@reboot` cron job. "A freeze costs two minutes" is now TRUE.

## Restart the patrol (now automatic, but if you need it by hand)

    /home/david/ocp-watch/start-patrol.sh      # idempotent, sleeps 30s first

## Five watches to re-arm (all die with the Claude session)

Scripts are written and self-tested; re-arm by pointing Monitor at each:

    scratchpad/w1-verdicts.sh   detector verdicts, !! warnings, relearns, restarts,
                                + 30-min summary carrying the verdict MIX
    scratchpad/w2-acpi.sh       ACPI hog RATE (spike = +5 or more in 30s)
    scratchpad/w3-ac.sh         AC mains transitions
    scratchpad/w4-patrol.sh     patrol lines + process liveness + 45-min beat gap
    scratchpad/w5-post.sh       POST silence, 4h

Each honours a `selftest-N` trigger file so the notification path can be proved
without waiting for a real event. All five were verified at 20:32.

Traps: use `/bin/grep`, never `grep` (ugrep shim); `grep -c` prints 0 AND exits
1, so `|| echo 0` yields "0\n0" -- use `n=${n:-0}`.

## Verified working — do not re-derive

- `evaluate.py` **30/30** at 20:35 (5/5 intruder, 24/24 residents, 1/1 possum),
  after the alpha fix. Rollback still at `server.py.before-alpha-fix.bak`.
- Full chain re-proven 20:28: HA -> POST -> RTSP burst -> verdict.
- Dima's credentials in `.env` (video keys + HA token) match what he sent;
  nothing was rotated.
- Intruder caught 18:47 on BOTH paths -- colour outside (70.1% warm, 43.6pp) and
  IR inside (rel_bright 1.48). Frames in `event-1847/`.

## Watch out for

A single-frame `*** ORANGE CAT ***` from the patrol at low `det_conf` may be
SUNLIGHT -- 19:37 and 19:42 both scored 83-90% ginger on an empty patio at
det_conf 0.32/0.37. ALWAYS look at the frame. See [[detector-path-has-no-bg-corr]].

## Repo state -- and the real exposure

Working tree is **clean**; everything (`server.py`, `AGENTS.md`,
`second-box-zyxx.md`, `HANDOFF-2026-08-25.md`) is committed in `4d49bc0`. The
older "Uncommitted" note in this file was stale.

**`origin` is a LOCAL BUNDLE** (`/home/david/ocp.bundle`, 12:41 today), not
GitHub. The handover was done by bundle, and a bundle carries commits but NOT
remote config -- so the GitHub URL never came across, and neither did any
credential: no `gh`, no SSH key, no `known_hosts` entry for github.com. odd-fellow
therefore cannot fetch or push even if given the URL.

We are **3 commits ahead, 0 behind**. Nothing is missing from upstream; the
exposure runs the other way -- those 3 commits and all of `~/ocp-watch` exist
ONLY on this box, which locked up twice today.

Local backups taken 20:44 (`~/ocp-backups/`, mode 600):

    ocp-<ts>.bundle              all refs
    ocp-worktree-<ts>.tar.gz     working tree (no .venv/frames/.git)
    ocp-watch-<ts>.tar.gz        patrol, REARM, ASK-DIMA, event frames

To restore GitHub, Dave needs to supply the remote URL and a credential:

    git remote add github <url>          # or: git remote set-url origin <url>
    git push github roi-background-detection
