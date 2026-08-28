# Message to Dima — 2026-08-25 20:45  (found it; it's a one-line fix)

READY TO SEND. Supersedes the 19:10 draft (kept as ASK-DIMA.md.superseded-1910),
whose camera.snapshot theory was WRONG.

---

Found it — and you were right that you didn't touch the motion automation. The
automation is fine. So is the VPN: I'm on the tunnel right now, HA answers me in
12ms, and I can pull both camera streams. Nothing on your side is misconfigured
except one stale IP address.

**The fault, exactly:**

`shell_command.cat_motion_json` posts to **`http://192.168.1.93:8080/motion`**.
That's an address on your LAN — my laptop's DHCP lease from when I was sitting
in your house. I'm at home, so nothing is listening there. HA's own error log
says so in as many words:

    Error running command: `curl -s -X POST http://192.168.1.93:8080/motion`,
    return code: 7

curl exit 7 is "couldn't connect". The automation then reports success, because
`shell_command` failures don't propagate up — which is why the Trace looks clean
and nothing ever pointed at the real problem.

**The fix — change one number in `configuration.yaml`:**

    shell_command:
      cat_motion_json: >
        curl -s -X POST -H "Content-Type: application/json"
        -d '{"camera": "{{ cam }}"}'
        http://192.168.7.4:8080/motion

Two changes from what's there now: `192.168.1.93` -> **`192.168.7.4`** (my
tunnel address, which is stable and doesn't care which of my machines is
running the detector), and actually sending the camera name. The key must be
**`camera`**, not `cam` — `cam` is just the variable your automation passes in.
Without a body it defaults to "outside", which is why outside-only worked
before.

**If you want detection back tonight without editing YAML:** point the
automation's action at `shell_command.cat_motion_img_vpn` instead. I tested that
one from here 20 minutes ago and it reached me and produced a verdict. It's the
weaker path — it sends a single stale snapshot rather than letting me pull my
own burst — so it's a stopgap, not the fix.

**How I verified all of this**, so you don't have to take my word for it: I
called `cat_motion_json` directly through the API and watched nothing arrive;
called `cat_motion_img_vpn` the same way and watched a POST land and score; and
pulled the automation's Trace myself (thank you for the token — that's what made
this diagnosable without you).

## Still worth doing when you have time

An alert when the detector stops answering. Today's outage ran from 17:18 to now
unnoticed, because from my end a broken link and a quiet night look identical.
One alarm on "HA's call to the detector stopped returning OK" would have caught
this in minutes, and would also cover a crashed detector or a dead VPN.

Also: the automation is `mode: single` with silent overflow, so when the cat
trips motion several times in one visit, the extra triggers are dropped.
`mode: queued` would keep them.

And great news about the camera speaker — I'd like to use it for a "would have
sprayed" dry run before we go anywhere near actual water.

(Still open from before: does the stray usually stay inside for roughly ten
minutes? Yesterday evening's visit was under three, but that was early evening
rather than the small hours.)
