"""syncflash -- nearby badges walk the same colour cycle together.

Kept deliberately terse: this file *is* the wire payload, so every comment
costs airtime on every hop. The reasoning lives in mods/README.md.
"""

NAME = "syncflash"
VERSION = 1
WANTS_PIXELS = True

PERIOD = 12.0           # seconds per colour rotation; the synced quantity
BREATHS = 4             # breaths per rotation
BEACON_HZ = 2.0
CLOCK_TTL = 5.0         # silence from our clock before we go it alone
FLOOR = 0.35            # dimmest point, so colour stays visible
LED_HZ = 30             # LED writes/sec; higher churns the ESP-IDF heap

import struct


def _breath(p):
    tri = 1.0 - abs(2.0 * p - 1.0)
    return tri * tri * (3.0 - 2.0 * tri)            # smoothstep


def _hue_rgb(h):
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    q = int(255 * (1 - f))
    t = int(255 * f)
    return ((255, t, 0), (q, 255, 0), (0, 255, t), (0, q, 255),
            (t, 0, 255), (255, 0, q))[i]


def _tint(clock):
    return (((clock[4] << 8) | clock[5]) % 997) / 997.0


def setup(ctx):
    st = ctx.state
    st["clock"] = bytes(ctx.mac)
    st["hops"] = 0
    st["offset"] = 0.0
    st["heard"] = 0.0
    st["beacon"] = 0.0
    st["lit"] = 0.0
    st["group"] = 0
    st["seen"] = {}
    st["pip"] = None
    st["tint"] = _tint(st["clock"])
    ctx.needs_radio = True
    ctx.log("on my own until someone lower turns up")


def _adopt(ctx, clock, hops, phase_ms, now):
    st = ctx.state
    st["clock"] = clock
    st["hops"] = hops + 1
    st["offset"] = (phase_ms / 1000.0 - now) % PERIOD
    st["tint"] = _tint(clock)
    st["heard"] = now


def tick(ctx, now):
    st = ctx.state
    mine = bytes(ctx.mac)

    for mac, payload, _rssi in ctx.inbox:
        if len(payload) != 9:
            continue
        clock = bytes(payload[0:6])
        hops = payload[6]
        phase_ms = struct.unpack(">H", payload[7:9])[0]
        if phase_ms >= PERIOD * 1000:
            continue
        st["seen"][bytes(mac)] = now
        if clock < st["clock"]:
            _adopt(ctx, clock, hops, phase_ms, now)
            ctx.log("following", ":".join("%02x" % b for b in clock))
        elif clock == st["clock"] and clock != mine and hops + 1 <= st["hops"]:
            _adopt(ctx, clock, hops, phase_ms, now)   # <= not <: also liveness

    if st["clock"] != mine and now - st["heard"] > CLOCK_TTL:
        st["clock"] = mine
        st["hops"] = 0
        st["tint"] = _tint(mine)
        ctx.log("lost the group, back on my own")

    if now - st["beacon"] >= 1.0 / BEACON_HZ:
        st["beacon"] = now
        phase = (now + st["offset"]) % PERIOD
        ctx.send(st["clock"] + bytes((st["hops"],))
                 + struct.pack(">H", int(phase * 1000)))

    if ctx.pixels is not None and now - st["lit"] >= 1.0 / LED_HZ:
        st["lit"] = now
        frac = ((now + st["offset"]) % PERIOD) / PERIOD
        lvl = FLOOR + (1.0 - FLOOR) * _breath((frac * BREATHS) % 1.0)
        r, g, b = _hue_rgb((frac + st["tint"]) % 1.0)
        ctx.pixels.fill((int(r * lvl), int(g * lvl), int(b * lvl)))
        ctx.show()                                    # tolerates a lost frame

    live = [m for m, t in st["seen"].items() if now - t < CLOCK_TTL]
    for m in list(st["seen"]):
        if now - st["seen"][m] > CLOCK_TTL * 2:
            del st["seen"][m]
    if len(live) != st["group"]:
        st["group"] = len(live)
        _pip(ctx, len(live))


def _pip(ctx, n):
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
        ctx.show()
    if ctx.led_drops:
        ctx.log("dropped %d LED frames to memory pressure" % ctx.led_drops)
