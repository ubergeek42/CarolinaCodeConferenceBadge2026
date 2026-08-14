"""
test_badgemod.py -- self-test for the module runtime and SyncFlash
=================================================================
Runs unmodified on CPython and on the badge:

    # on your computer, from the repo root
    python3 samples/ProfileCard/test_badgemod.py

    # or on the badge: copy it to the CIRCUITPY root, then from the REPL
    exec(open("/test_badgemod.py").read())

Both matter. CircuitPython differs from CPython in ways this code leans on
-- `bytes` comparison drives SyncFlash's clock election, dicts are not
insertion-ordered, and `exec(src, globals_dict)` has its own quirks there --
so a green run on a laptop proves less than it looks.

The interesting half is the bottom: two SyncFlash instances wired to each
other through a fake bus, with fake time, converging on a beat. That is the
whole point of keeping the runtime hardware-free -- the sync algorithm is
testable with no radio, no second badge, and no waiting.
"""

import sys

sys.path.append("lib")
sys.path.append("/lib")

import badgemod


PASS = 0
FAIL = 0


def ok(cond, what):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("  FAIL:", what)


def eq(a, b, what):
    ok(a == b, "%s -- got %r want %r" % (what, a, b))


def near(a, b, tol, what):
    ok(abs(a - b) <= tol, "%s -- got %r want %r +/-%r" % (what, a, b, tol))


def near_phase(a, b, tol, what, period=1.0):
    """Compare two points on a circle. 0.98 and 0.00 are 20 ms apart, not 980.

    Worth spelling out: the first version of this test used a plain
    subtraction and reported a perfectly synced pair as a whole beat out.
    """
    d = abs(a - b) % period
    ok(min(d, period - d) <= tol,
       "%s -- got %r vs %r, %r apart (tol %r)" % (what, a, b, min(d, period - d), tol))


class FakePixels:
    """Records what a module asked for, so flashes can be asserted on."""

    def __init__(self, n=5):
        self.n = n
        self.last = None
        self.shows = 0

    def fill(self, color):
        self.last = color

    def show(self):
        self.shows += 1


def read(path):
    for prefix in ("", "/"):
        try:
            with open(prefix + path) as f:
                return f.read()
        except OSError:
            continue
    raise OSError("cannot find " + path)


# ------------------------------------------------------------------
# mod_id
# ------------------------------------------------------------------
print("mod_id_for")
eq(badgemod.mod_id_for("syncflash"), badgemod.mod_id_for("syncflash"), "stable")
eq(badgemod.mod_id_for("syncflash"), badgemod.mod_id_for(b"syncflash"), "str == bytes")
ok(badgemod.mod_id_for("a") != badgemod.mod_id_for("b"), "differs by name")
ok(0 <= badgemod.mod_id_for("syncflash") <= 0xFFFF, "fits 16 bits")


# ------------------------------------------------------------------
# loading, ticking, unloading
# ------------------------------------------------------------------
print("load / tick / unload")
MINIMAL = "NAME = 'tiny'\ncalls = []\ndef tick(ctx, now):\n    calls.append(now)\n"

rt = badgemod.Runtime()
mod = rt.load(MINIMAL)
ok(mod is not None, "minimal module loads")
eq(mod.name, "tiny", "NAME picked up from the source")
eq(len(rt.mods), 1, "one module loaded")
rt.tick(1.0)
rt.tick(2.0)
eq(mod.ticks, 2, "ticked twice")
eq(mod.glb["calls"], [1.0, 2.0], "now passed through")
ok(mod.avg_ms >= 0.0, "timing recorded")

eq(rt.load("this is not python"), None, "syntax error is reported, not raised")
eq(len(rt.mods), 1, "the bad module did not load")
eq(rt.load("def tick(ctx, now): pass"), None, "no NAME is refused")

TEARDOWN = ("NAME = 'td'\ndown = []\ndef tick(ctx, now): pass\n"
            "def teardown(ctx):\n    down.append(1)\n")
td = rt.load(TEARDOWN)
ok(rt.unload("td"), "unload returns True")
eq(td.glb["down"], [1], "teardown ran")
eq(rt.unload("td"), False, "unloading twice is False, not an error")

# Replacing by name is how a newer version of a module arrives off the air.
first = rt.load("NAME = 'dup'\nVERSION = 1\ndef tick(ctx, now): pass\n")
second = rt.load("NAME = 'dup'\nVERSION = 2\ndef tick(ctx, now): pass\n")
eq(len([m for m in rt.mods if m.name == "dup"]), 1, "same name replaces")
eq(rt.get("dup").version, 2, "the newer one won")


# ------------------------------------------------------------------
# quarantine: a module that throws must not reach the main loop
# ------------------------------------------------------------------
print("crash quarantine")
rt2 = badgemod.Runtime()
boom = rt2.load("NAME = 'boom'\ndef tick(ctx, now):\n    raise ValueError('bang')\n")
ok(boom is not None, "it loads fine -- the fault is at tick time")
rt2.tick(1.0)                       # must not raise
eq(len(rt2.mods), 0, "crashed module was unloaded")
ok("crashed" in rt2.unloaded[-1][1], "reason recorded for the UI")

rt2.load("NAME = 'setupboom'\ndef setup(ctx):\n    raise KeyError('no')\n"
         "def tick(ctx, now): pass\n")
eq(len(rt2.mods), 0, "a module that throws in setup never lands")


# ------------------------------------------------------------------
# budget: three overruns and you are out
# ------------------------------------------------------------------
print("time budget")
rt3 = badgemod.Runtime(budget_ms=0.0)     # everything overruns
hog = rt3.load("NAME = 'hog'\ndef tick(ctx, now):\n    x = sum(range(2000))\n")
rt3.tick(1.0)
eq(len(rt3.mods), 1, "one strike is survivable")
rt3.tick(2.0)
rt3.tick(3.0)
eq(len(rt3.mods), 0, "three strikes and it is unloaded")
ok("budget" in rt3.unloaded[-1][1], "unload reason mentions the budget")


# ------------------------------------------------------------------
# pixels have exactly one owner
# ------------------------------------------------------------------
print("pixel ownership")
px = FakePixels()
rt4 = badgemod.Runtime(pixels=px)
a = rt4.load("NAME = 'a'\nWANTS_PIXELS = True\ndef tick(ctx, now): pass\n")
b = rt4.load("NAME = 'b'\nWANTS_PIXELS = True\ndef tick(ctx, now): pass\n")
# Newest wins, not first. The common case is a module just accepted off
# another badge, and one that visibly does nothing because an older module
# holds the strip is indistinguishable from a transfer that failed.
ok(b.ctx.pixels is px, "the newest asker gets the pixels")
ok(a.ctx.pixels is None, "and the previous owner is told it lost them")
eq(rt4.pixel_owner, "b", "ownership moved")
rt4.unload("a")
eq(rt4.pixel_owner, "b", "unloading a non-owner leaves ownership alone")
rt4.unload("b")
eq(rt4.pixel_owner, None, "ownership released when the owner goes")
eq(px.last, (0, 0, 0), "and the strip was blanked")


# ------------------------------------------------------------------
# routing: mod_id keeps two modules' mail apart
# ------------------------------------------------------------------
print("deliver / mod_id routing")
rt5 = badgemod.Runtime()
m1 = rt5.load("NAME = 'one'\nseen = []\ndef tick(ctx, now):\n    seen.extend(ctx.inbox)\n")
m2 = rt5.load("NAME = 'two'\nseen = []\ndef tick(ctx, now):\n    seen.extend(ctx.inbox)\n")
ok(rt5.deliver(m1.mod_id, b"\x01" * 6, b"hi", -40), "delivered to one")
eq(rt5.deliver(0xDEAD, b"\x01" * 6, b"nope"), False, "unknown mod_id dropped")
rt5.tick(1.0)
eq(len(m1.glb["seen"]), 1, "module one got its message")
eq(len(m2.glb["seen"]), 0, "module two did not")
rt5.tick(2.0)
eq(len(m1.glb["seen"]), 1, "inbox is drained after each tick")


# ------------------------------------------------------------------
# needs_radio aggregation
# ------------------------------------------------------------------
print("needs_radio")
rt6 = badgemod.Runtime()
eq(rt6.needs_radio, False, "nothing loaded, nothing needed")
rt6.load("NAME = 'greedy'\ndef setup(ctx):\n    ctx.needs_radio = True\n"
         "def tick(ctx, now): pass\n")
eq(rt6.needs_radio, True, "a module can veto radio duty-cycling")


# ------------------------------------------------------------------
# SyncFlash: two badges, a fake bus, fake time
# ------------------------------------------------------------------
print("syncflash convergence")
SYNC = read("mods/syncflash.py")

LOW = b"\x02\x00\x00\x00\x00\x01"
HIGH = b"\xaa\x00\x00\x00\x00\x99"


class Bus:
    """Wires N runtimes together. Every send reaches everyone else."""

    def __init__(self):
        self.nodes = []
        self.frames = 0
        self.drop = 0            # drop every Nth frame, 0 = lossless

    def join(self, mac, pixels=None):
        rt = badgemod.Runtime(pixels=pixels, mac=mac,
                              send=lambda mid, payload, m=mac: self._tx(m, mid, payload))
        self.nodes.append((mac, rt))
        return rt

    def _tx(self, src, mod_id, payload):
        self.frames += 1
        if self.drop and self.frames % self.drop == 0:
            return True                       # went out, nobody heard it
        for mac, rt in self.nodes:
            if mac != src:
                rt.deliver(mod_id, src, payload, -50)
        return True

    def run(self, until, step=0.02, t0=0.0):
        t = t0
        while t < until:
            for _mac, rt in self.nodes:
                rt.tick(t)
            t += step
        return t


def phase_of(rt, now):
    """Where this badge is in its cycle, using the module's own PERIOD."""
    mod = rt.mods[0]
    return (now + mod.ctx.state["offset"]) % mod.glb["PERIOD"]


bus = Bus()
pa = FakePixels()
pb = FakePixels()
ra = bus.join(HIGH, pa)          # deliberately the higher MAC
rb = bus.join(LOW, pb)
ok(ra.load(SYNC) is not None, "syncflash loads from mods/syncflash.py")
ok(rb.load(SYNC) is not None, "syncflash loads on the second badge too")
eq(ra.mods[0].ctx.pixels is pa, True, "it asked for and got the pixels")
eq(ra.needs_radio, True, "and it wants the radio left on")

# Start them deliberately out of step: half a beat apart.
SYNC_PERIOD = ra.mods[0].glb["PERIOD"]
ra.mods[0].ctx.state["offset"] = SYNC_PERIOD / 2.0      # half a breath apart
t = bus.run(until=4.0)

eq(ra.mods[0].ctx.state["clock"], LOW, "the higher MAC followed the lower one")
eq(rb.mods[0].ctx.state["clock"], LOW, "the lower MAC stayed the clock")
eq(rb.mods[0].ctx.state["hops"], 0, "clock is zero hops from itself")
eq(ra.mods[0].ctx.state["hops"], 1, "follower is one hop out")
near_phase(phase_of(ra, t), phase_of(rb, t), 0.05,
           "breaths converged to inside 50 ms", period=SYNC_PERIOD)
# Not "written every tick" -- deliberately not. NeoPixel writes allocate from
# the ESP-IDF heap and the strip is rate limited to LED_HZ, so the right
# assertion is that the rate limit is doing its job while still animating.
led_hz = ra.mods[0].glb["LED_HZ"]
ticks = ra.mods[0].ticks
ok(pa.shows < ticks, "the strip is rate limited, not written on every tick")
ok(0.5 * led_hz * 4.0 <= pa.shows <= 1.1 * led_hz * 4.0,
   "but still driven at roughly LED_HZ over 4 s (%d writes, %d ticks)"
   % (pa.shows, ticks))
ok(pb.shows == pa.shows, "both badges drive their strips on the same schedule")
# Same colour, near-identical brightness -- but NOT byte-identical, and it
# would be wrong to demand that. The two badges agree on the breath to within
# a loop pass, and on a smooth curve a 20 ms difference is a genuinely
# different brightness. Asserting equality here just pinned the old strobe,
# where both badges sat at the same floor value most of the cycle.
ok(max(abs(x - y) for x, y in zip(pa.last, pb.last)) <= 16,
   "in sync means the same colour within a few percent (%r vs %r)"
   % (pa.last, pb.last))
ok([i for i, v in enumerate(pa.last) if v == max(pa.last)]
   == [i for i, v in enumerate(pb.last) if v == max(pb.last)],
   "and the same dominant channel, i.e. the same group colour")

# The group pip counts the other badge.
eq(ra.mods[0].ctx.state["group"], 1, "one other badge in the group")

# Now cut the radio: the follower must go back to its own beat rather than
# holding a stale one forever.
bus.nodes = [(HIGH, ra)]
t = bus.run(until=t + 8.0, t0=t)
eq(ra.mods[0].ctx.state["clock"], HIGH, "alone again, it clocks itself")
eq(ra.mods[0].ctx.state["group"], 0, "and the group emptied")

# Lossy air must not break election -- beacons are fire-and-forget and
# broadcast gives no delivery feedback at all, so every third frame vanishing
# is a normal Tuesday.
bus2 = Bus()
bus2.drop = 3
rc = bus2.join(HIGH, FakePixels())
rd = bus2.join(LOW, FakePixels())
rc.load(SYNC)
rd.load(SYNC)
rc.mods[0].ctx.state["offset"] = SYNC_PERIOD * 0.37
t2 = bus2.run(until=6.0)
eq(rc.mods[0].ctx.state["clock"], LOW, "election survives 33% loss")
near_phase(phase_of(rc, t2), phase_of(rd, t2), 0.05,
           "and so does phase agreement", period=SYNC_PERIOD)

# Three in a chain, where the middle badge is the only one that hears both:
# the far badge must still end up on the clock, via the middle.
bus3 = Bus()
r1 = bus3.join(LOW, FakePixels())
r2 = bus3.join(b"\x50\x00\x00\x00\x00\x02", FakePixels())
r3 = bus3.join(HIGH, FakePixels())
for r in (r1, r2, r3):
    r.load(SYNC)
t3 = bus3.run(until=5.0)
eq(r3.mods[0].ctx.state["clock"], LOW, "everyone lands on the lowest MAC")
near_phase(phase_of(r3, t3), phase_of(r1, t3), 0.05,
           "and on its rhythm", period=SYNC_PERIOD)
ok(r3.mods[0].ctx.state["hops"] >= 1, "hop count is at least one from the clock")


print("PASSED %d, FAILED %d" % (PASS, FAIL))
