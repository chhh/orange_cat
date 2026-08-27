# Cat deterrent hardware — options and a recommendation

**Draft for Dima, 2026-08-22**

The detector is now reliably picking out the orange cat — the first real catches
were at 04:01 and 04:11 on the morning of 22 August, both confirmed by eye. So
the next question is what we actually *do* when it turns up.

This note covers: why I think water beats air, four ways to build a water
sprayer, specific products I've checked, which one I'd pick, and two questions I
need answered before anything gets ordered.

Prices are US, checked August 2026. Sources are listed at the end.

---

## Why water, and not a puff of air

Air genuinely does scare cats — the hiss is what makes the commercial pet
deterrents work. The problem is triggering it from our system.

- The canister-based ones use compressed air cartridges. Each is good for
  roughly 80–100 sprays and then needs replacing. That's a running cost and a
  thing to remember.
- Building our own means an air compressor, a small tank, and a valve. More
  parts, more noise, more to go wrong than a garden hose.
- Every off-the-shelf air deterrent fires on its own built-in motion sensor.
  That sensor can't tell your cats from the stray — which is the entire problem
  we just spent the last week solving in software. Using one would throw that away.

Water is free per shot, never runs out, and the mechanism is a single valve.

---

## The four ways to build it

All four do the same job: hold water back, then let it out for a second or two
when Home Assistant says so. They differ in how much assembly is involved, and
in how fast they can react.

| | Setup effort | Cost | Time to get water out | Outside tap needed? |
|---|---|---|---|---|
| **A. Valve wired to a small Wi-Fi switch** | ~30–45 min assembly | $80–110 | Near-instant | Yes |
| **B1. LinkTap G2S hose timer** | Screw it on | $120–140 | Fast, 3 second minimum burst | Yes |
| **B2. SONOFF SWV hose timer** | Screw it on | $25–35 | **~3 seconds just to open** | Yes |
| **C. Bucket and pump** | Plug things in | $60–80 | About half a second | **No** |
| **D. Smart timer feeding a motion sprinkler** | Moderate | $110–150 | Doesn't matter — see below | Yes |

**The short version of what I found:** the easy screw-on options are either slow
(the cheap one) or more expensive than building it properly (the good one).
Details below.

---

### Option A — an electric valve wired to a small Wi-Fi switch

The most controllable version, and after doing the research, still the one I'd
build.

You buy an electric water valve — a brass fitting with an electromagnet in it
that snaps open the instant you send it power. It goes inline on a garden hose.
A small Wi-Fi switch (a **Shelly 1 Gen4**, in a weatherproof box) turns it on and
off, and Home Assistant talks to that switch directly over your own network. No
cloud service in the middle, so it responds in about a tenth of a second.

Two details that make this tidier than it sounds:

- The Shelly 1 Gen4 **can be powered from the same 12 volt supply as the valve**,
  so there's no mains wiring anywhere in the build. It switches up to 10 amps at
  30 volts DC, far more than the valve draws.
- It speaks Wi-Fi, Zigbee **and** Matter, so it'll join whatever you already have
  running without a new hub.

**Shopping list**

| Part | What to get | ~Cost |
|---|---|---|
| The valve | 12 volt DC brass solenoid valve, 3/4" garden hose thread, **"normally closed"** | $15–25 |
| The switch | Shelly 1 Gen4, in a weatherproof box | $20–25 |
| Power | 12 volt plug-in power supply, 1–2 amps (runs both the valve and the Shelly) | $10 |
| Nozzle | Adjustable garden hose nozzle on a stake, or a small irrigation micro-jet | $8–15 |
| Flow control | Small inline needle valve | $8 |
| Manual shutoff | Ordinary ball valve or a splitter with taps, before everything else | $10 |
| Plumbing bits | Hose splitter for the tap, hose, plumber's tape | $15 |

Two bits of jargon worth unpacking:

- **"Normally closed"** means the valve is shut when nothing is powering it.
  That's the safe direction — if the power drops, the network dies, or my
  software crashes, the water stops rather than keeps running. Get this wrong
  and you have a hose that runs all night.
- **The inline needle valve** is just a small hand tap that throttles the flow.
  It lets you turn the spray down to a startle rather than a blast without
  anybody touching the software.

One buying note: most cheap 12 V valves are the "pilot-operated" type, which
need a bit of water pressure behind them to work. That's fine on mains pressure.
If we ever switch to Option C's bucket-and-pump, we'd need a "direct-acting"
valve instead.

Everything on that list is standard garden-irrigation stock.

---

### Option B — a smart hose timer (least work, but read the catch)

These are the battery timers people use for garden watering. Screw it onto the
outside tap, hose on the other end, done — no power supply, no wiring, no box to
mount. Pair it to Home Assistant and we're finished.

**There is a catch, though, and it is a real one.** There are two completely
different mechanisms sold under the same description. Some use an electromagnet
that snaps open in a fraction of a second. Others use a small motor that cranks
a tap open over several seconds. For watering a lawn nobody cares. For us the
slow kind is close to useless. Neither manufacturer publishes which one you're
getting, so I went looking at reviews and teardowns.

Here's what I actually found on the two candidates:

**B1 — LinkTap G2S ($120–140 with its gateway).** This is the good one.

- Its minimum watering time is **3 seconds**, which tells you the valve is the
  fast electromagnet type — a 3-second burst would be pointless on a valve that
  takes 3 seconds to open.
- There's a Home Assistant integration that talks to LinkTap's gateway **over
  your own network with no cloud involved**, and its "start watering" command
  takes a duration in seconds.
- Flow is about 1,500 litres/hour, IP66 weatherproof, 4 AA batteries with a
  claimed 2-year life.
- Downsides: it needs LinkTap's own gateway box (plugged into your router by
  Ethernet), which is most of why it costs what it does. And the local Home
  Assistant integration is written by a community volunteer rather than by
  LinkTap, so it's a bit less guaranteed than an official one.

**B2 — SONOFF SWV ($25–35).** Cheap, Zigbee, works with Home Assistant locally
out of the box. But:

- It's a **motorised ball valve**, and reviewers time it at roughly **3 seconds
  from command to water flowing**. Same again to shut off.
- Specs are otherwise fine: handles 2–39 litres/minute, works from 0.06 MPa
  pressure upward, IP55, 4 AA batteries.

Whether 3 seconds is fatal depends on the cat. You've said the stray takes
anywhere from 1 to 30 seconds to cross to the door, and our software takes about
1.5 seconds to make up its mind. So a 3-second valve puts us at ~4.5 seconds
total — which will catch a loitering cat and miss a determined one. For $25 it
might be worth trying, but I wouldn't build the finished system on it.

**One to avoid: Orbit's B-hyve timers.** They're everywhere and they're cheap,
but every command goes out to the manufacturer's servers and back, which can
take 5–15 seconds. Fine for a watering schedule, useless here.

---

### Option C — a bucket and a pump (the answer if there's no tap out there)

No plumbing at all, and no outside tap needed. A 12 volt pump — the pressurised
sort sold for RV water systems, about $30 — sits in a five-gallon jug with a hose
running to a nozzle.

The neat trick is how you switch it: rather than wiring a relay to the pump, you
plug the pump's power supply into an ordinary smart plug and switch *that*. Turn
the plug on, the pump runs. The only assembly is joining the pump's two wires to
a power-supply lead, which is a screw-terminal job — no soldering.

Two warnings. **Don't substitute a cheap fountain or pond pump** — those give a
dribble, not a spray; you want the pressurised RV/washdown type. And you'll be
refilling the jug, so it's more ongoing bother than a hose.

---

### Option D — let a dumb sprinkler do the aiming

Worth considering because it makes the timing problem vanish.

You buy a motion-activated garden sprinkler — the **Orbit 62100 Yard Enforcer**
is the standard one (heat-and-motion sensor covering 120° out to 40 feet, a
35-foot sprinkler head, 4 AA batteries good for around 7,500 firings, and a
night-only mode). On its own it's no use to us: it fires at anything warm that
moves, so it would soak your cats as happily as the stray.

But put a smart valve *upstream* of it, controlling its water supply. Now the
detector doesn't fire the spray — it just opens the water for about ten seconds
when it's confident the orange cat is there. Inside that window the sprinkler's
own sensor does the firing, from a foot away, with perfect timing and aim.

Why it's appealing: the smart valve is allowed to be slow and cheap, because it
no longer has to hit a one-second window. The slow $25 SONOFF would be perfectly
adequate here. And you skip all the fiddling with where to aim.

Why it might not be worth it: one more thing to buy, and the sprinkler's sensor
will click away dry all night every time one of your cats walks past. Harmless,
but it'll chew batteries.

---

## What I'd recommend

**Option A.** Doing the homework actually strengthened the case for building it
properly rather than buying convenience:

- The cheap screw-on timer is slow (~3 seconds to open).
- The fast screw-on timer costs $120–140 — *more* than building Option A — and
  still has a 3-second minimum burst.
- Option A is about 30–45 minutes of work, needs no plumbing skill and no mains
  wiring, and gives a valve that opens instantly, no batteries to die at four in
  the morning in February, and a hardware-level safety cutoff the others don't
  have (see below).

**If you'd genuinely rather not assemble anything**, LinkTap G2S is the one to
buy. It's the only screw-on option I found that is both fast and controllable
without the cloud. Pay the $130 and skip the build.

**If there's no outside tap**, Option C, and the choice is made for us.

**Cheap experiment worth considering:** buy the $25 SONOFF first and see whether
3 seconds is fast enough in practice. If it is, we're done for $25. If it isn't,
we've learned something real and it becomes the upstream valve for Option D.

---

## Three things I want built in from the start

**1. A hard time limit that lives in the hardware, not in my software.**

The Shelly has a built-in auto-off timer: you tell the device itself "never stay
on longer than a second or two," and it enforces that on its own. It also
accepts a flip-back timer attached to each individual command. So if my detector
crashes with the water on, or the VPN drops halfway through, the valve shuts
regardless. Nothing on my end has to still be working.

This is the single most important safety feature in the build. A hose running
unattended onto your patio at 4 a.m. is the thing that ends this project. The
screw-on timers in Option B have watering schedules rather than a true
independent cutoff — that's their real downside, more than the speed.

**2. Aim across the approach, about a metre short of the flap — not at the flap.**

Keeps water off the door, the camera lens and the patio light. It also means
that if we get it wrong and spray one of your cats, we've given it a wetting
rather than blocked its way home. Worth remembering that wet concrete on a
freezing night is a slip hazard, right where the cats walk.

**3. A plan for winter.** The hose will need draining and disconnecting in the
cold months. Realistically this is a warm-season deterrent unless we run a line
from indoors.

---

## Two questions before anything gets ordered

**Is there an outside tap within hose reach of the patio?** This decides between
Options A/B/D and Option C, so it's the first thing I need to know.

**Which Raspberry Pi is the spare one?** This matters more than the sprayer does,
and I have real numbers now. The image-recognition step takes about 30
milliseconds per frame on my laptop. Published benchmarks for the same class of
model:

| | Per frame | Our 16-frame decision |
|---|---|---|
| My laptop (now) | ~30 ms | ~0.5 s |
| Pi 5, the way we run it today | ~150 ms | ~2.4 s |
| Pi 5, with a faster export (see below) | ~85 ms | ~1.4 s |
| Pi 4 | roughly double the Pi 5 | ~3–5 s |

Two useful findings in there. First, a Pi 5 is fine, and a Pi 4 is workable but
eats most of the head start we just gained. Second, **there's a free speedup
available**: we currently run the model in a format called ONNX, and switching
to one called NCNN nearly doubles the speed on Pi hardware for the same model
and the same accuracy. That's a software change on my side, no cost.

If the spare turns out to be a Pi 3, that's too slow and we should talk. If you
wanted to throw hardware at it, the Raspberry Pi AI HAT+ is $70 for the 13-TOPS
version and removes the problem entirely, but I don't think we'll need it.

---

## One thing the sprayer changes about the software

The plan up to now was to require **two events in a row to agree** before acting
— cheap insurance against spraying one of your cats by mistake.

That rule can't survive an actual sprayer. The two confirmed catches were at
04:01 and 04:11 — ten minutes apart. Waiting for a second event means either
spraying an empty patio ten minutes late, or never firing at all.

So the safety check has to move to being *within a single visit*: pool the frames
from one visit and require them to agree with each other. That's already on my
list — you're the one who spotted that a single visit often spans several
separate events. I want it working before anything is connected to water.

---

## Suggested order of doing things

**Phase 0 — free, can start now.** Point the Home Assistant automation at a
"would have sprayed" log entry, or at the camera's own speaker if that model has
one. Run it a week. We get real timings and a track record on a live firing
path, with zero risk of soaking a resident cat.

**Phase 1 — buy and install**, with the manual shutoff closed so no water can
reach it. Confirm the whole chain fires dry, end to end. **While you're there,
time the valve with a stopwatch** — command to water — because that number isn't
in any datasheet and it decides whether the setup is good enough.

**Phase 2 — turn the water on**, ideally on a few nights when you're around to
watch the first firings.

---

## Worth knowing: somebody has already built this

A project called **CatoCam / CatoZap** does almost exactly what we're doing — a
Raspberry Pi watching an RTSP camera stream, running the same family of
image-recognition model, firing 12 volt solenoid water jets when it sees a cat
loitering. Their build uses 1/4" tubing and a small transistor board to let the
Pi's pins drive the valves.

Two things to take from it. It confirms the approach is sound and the parts
choice is conventional. But they publish **no data on whether it actually
deterred the cats** — which is a reminder that "the sprayer fired" and "the cat
stopped coming" are different claims, and we should plan to measure the second
one rather than assume it.

---

## Sources

- SONOFF SWV specs (flow 2–39 L/min, 0.06–0.8 MPa, IP55): <https://help.sonoff.tech/docs/swv-bsp-nh>
- SONOFF SWV reviews and ~3-second opening: <https://smarthomescene.com/reviews/sonoff-zigbee-smart-water-valve-swv-review/> and <https://www.cnx-software.com/2024/07/17/sonoff-swv-review-zigbee-smart-water-valve-ewelink-home-assistant/>
- SONOFF SWV in Home Assistant: <https://www.zigbee2mqtt.io/devices/SWV.html>
- LinkTap G2S review, 3-second minimum and pricing: <https://www.irrigation-guide.com/linktap-g2s-review.html>
- LinkTap local (no-cloud) Home Assistant integration: <https://github.com/sh00t2kill/linktap_local_http_component>
- Shelly 1 Gen4 (12 V DC power option, dry contact, Wi-Fi/Zigbee/Matter): <https://us.shelly.com/products/shelly-1-gen4>
- Shelly device-side auto-off and flip-back timers: <https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch/>
- Orbit 62100 Yard Enforcer specs: <https://www.orbitonline.com/products/yard-enforcer-motion-activated-sprinkler>
- Raspberry Pi model speeds: <https://docs.ultralytics.com/guides/raspberry-pi> and <https://www.seeedstudio.com/blog/2023/09/28/raspberry-pi-5-vs-pi-4-ai-performance-cpu-benchmark-how-much-leap-forward/>
- Raspberry Pi AI HAT+ pricing: <https://www.raspberrypi.com/news/raspberry-pi-ai-hat/>
- CatoCam / CatoZap prior art: <https://github.com/jones139/catocam>
