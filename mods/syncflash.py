"""
syncflash.py -- badges standing together pulse as one
=====================================================
A badgemod module. Every badge running it flashes its NeoPixels on a one
second beat; badges within earshot converge on a shared beat and a shared
colour, so a knot of people talking in a hallway drifts into sync on its own
and reads, from across the room, as one organism. Walk away and your badge
goes back to its own beat.

How the beat is agreed
----------------------
Lowest MAC in earshot wins. Each badge broadcasts, twice a second, the MAC
of whichever badge it currently believes is the clock, how many hops away
that badge is, and where the beat currently sits. Hearing a lower MAC than
your own clock, you adopt it; hearing the same clock from someone closer to
it than you are, you tighten up to them. That makes sync spread through a
crowd transitively -- you sync to people who can hear the clock even when
you can't -- and the hop count is what stops two badges from endlessly
nudging each other in a circle.

No round-trip correction, deliberately. One-way airtime is 0.6 ms
(measured), so the error budget is dominated by main-loop jitter: about
20 ms of a 1000 ms period, or 2%. That is invisible to the eye, and paying
for a handshake to fix 2% would cost more air than the beat itself.

The colour comes from the clock's MAC, which is a free and rather nice
side effect: two synced groups in the same room flash in different colours,
so you can see the groups.
"""

NAME = "syncflash"
VERSION = 1
WANTS_PIXELS = True

PERIOD = 1.0            # seconds per beat
BEACON_HZ = 2.0         # how often we tell others about the beat
CLOCK_TTL = 5.0         # silence from our clock before we go it alone
DECAY = 0.45            # fraction of the period the flash takes to fade
FLOOR = 0.04            # never fully dark, so the badge still reads as alive

import struct


def _hue_rgb(h):
    """h in 0..1 -> a saturated (r, g, b). Small enough to inline here.

    Deliberately not importing a colour helper: a module travels over the
    air as source, so every import is a dependency the receiving badge has
    to already have.
    """
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    q = int(255 * (1 - f))
    t = int(255 * f)
    return ((255, t, 0), (q, 255, 0), (0, 255, t), (0, q, 255),
            (t, 0, 255), (255, 0, q))[i]


def setup(ctx):
    st = ctx.state
    st["clock"] = bytes(ctx.mac)      # we are our own clock until told otherwise
    st["hops"] = 0
    st["offset"] = 0.0                # phase = (now + offset) % PERIOD
    st["heard"] = 0.0                 # last time we heard a better clock
    st["beacon"] = 0.0
    st["group_size"] = 0
    st["seen"] = {}                   # mac -> last heard, for the group count
    st["pip"] = None
    ctx.needs_radio = True            # sync only works with the radio listening
    ctx.log("beat is mine until someone lower turns up")


def _adopt(ctx, clock, hops, phase_ms, now):
    st = ctx.state
    # Line our phase up with theirs: they are at phase_ms into the beat, so
    # our offset is whatever makes our clock read the same.
    st["clock"] = clock
    st["hops"] = hops + 1
    st["offset"] = (phase_ms / 1000.0 - now) % PERIOD
    st["heard"] = now


def tick(ctx, now):
    st = ctx.state
    mine = bytes(ctx.mac)

    # --- listen ---
    for mac, payload, _rssi in ctx.inbox:
        if len(payload) != 9:
            continue                                  # not ours / truncated
        clock = bytes(payload[0:6])
        hops = payload[6]
        phase_ms = struct.unpack(">H", payload[7:9])[0]
        if phase_ms >= PERIOD * 1000:
            continue                                  # nonsense; ignore
        st["seen"][bytes(mac)] = now
        if clock < st["clock"]:
            _adopt(ctx, clock, hops, phase_ms, now)
            ctx.log("following", ":".join("%02x" % b for b in clock))
        elif clock == st["clock"] and clock != mine and hops + 1 <= st["hops"]:
            # Same clock, and this badge is strictly closer to it than we are
            # (adopting would leave us at hops + 1, no worse than we are now).
            # Two things happen here, and both matter:
            #   * we re-sync, which corrects the drift between two badges'
            #     independent monotonic clocks -- they are not the same clock
            #     and never will be;
            #   * `heard` is refreshed, which is how we know the clock is still
            #     reachable. Only ever syncing to someone *strictly* closer
            #     meant our own upstream's beacons never counted as contact,
            #     so a happily-synced badge dropped back to its own beat every
            #     CLOCK_TTL seconds. `<=` rather than `<` is the whole fix.
            # It still cannot loop: syncing to someone further from the clock
            # than us is what would do that, and that is what this excludes.
            _adopt(ctx, clock, hops, phase_ms, now)

    # A clock we can no longer hear is not a clock. Falling back to our own
    # MAC rather than keeping a stale beat is what lets a group that splits
    # in two re-form as two groups instead of drifting apart silently.
    if st["clock"] != mine and now - st["heard"] > CLOCK_TTL:
        st["clock"] = mine
        st["hops"] = 0
        ctx.log("lost the beat, back on my own")

    # --- talk ---
    if now - st["beacon"] >= 1.0 / BEACON_HZ:
        st["beacon"] = now
        phase = (now + st["offset"]) % PERIOD
        ctx.send(st["clock"] + bytes((st["hops"],))
                 + struct.pack(">H", int(phase * 1000)))

    # --- flash ---
    phase = (now + st["offset"]) % PERIOD
    if phase < DECAY:
        lvl = FLOOR + (1.0 - FLOOR) * (1.0 - phase / DECAY)
    else:
        lvl = FLOOR
    if ctx.pixels is not None:
        clock = st["clock"]
        r, g, b = _hue_rgb(((clock[4] << 8 | clock[5]) % 360) / 360.0)
        ctx.pixels.fill((int(r * lvl), int(g * lvl), int(b * lvl)))
        ctx.pixels.show()

    # --- the pip: how many badges are beating with us ---
    live = [m for m, t in st["seen"].items() if now - t < CLOCK_TTL]
    for m in list(st["seen"]):
        if now - st["seen"][m] > CLOCK_TTL * 2:
            del st["seen"][m]
    if len(live) != st["group_size"]:
        st["group_size"] = len(live)
        _pip(ctx, len(live))


def _pip(ctx, n):
    """A two-character badge in the corner: how big the synced group is.

    Only touched when the number changes -- a label whose text is reassigned
    every frame is a full-width dirty rectangle every frame, and on this
    display that is 17 ms we do not need to spend.
    """
    st = ctx.state
    if ctx.group is None:
        return
    if st["pip"] is None:
        import terminalio
        from adafruit_display_text import label
        st["pip"] = label.Label(terminalio.FONT, text="", color=0x00FFCC,
                                background_color=0x000000)
        st["pip"].anchor_point = (0.0, 0.0)
        st["pip"].anchored_position = (2, 2)
        ctx.group.append(st["pip"])
    st["pip"].text = ("*%d" % n) if n else ""
    ctx.dirty = True


def teardown(ctx):
    if ctx.pixels is not None:
        ctx.pixels.fill((0, 0, 0))
        ctx.pixels.show()
