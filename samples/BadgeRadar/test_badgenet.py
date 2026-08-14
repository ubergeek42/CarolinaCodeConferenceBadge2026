"""Self-test for badgenet's hardware-free layers.

Runs unmodified on CPython and on CircuitPython (no unittest module there,
so it is plain asserts plus a tiny counter). Verifying on the badge matters
because CircuitPython differs from CPython in ways this code touches --
notably that dicts are not insertion-ordered, so anything relying on
ordering has to sort explicitly.
"""

import badgenet as bn

_passed = []
_failed = []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    if not cond:
        print("  FAIL:", name)


def raises(name, fn, exc=ValueError):
    try:
        fn()
    except exc:
        check(name, True)
        return
    except Exception as e:
        print("  FAIL:", name, "-> wrong exception", type(e).__name__)
        _failed.append(name)
        return
    print("  FAIL:", name, "-> no exception")
    _failed.append(name)


print("--- codec ---")
f = bn.encode(bn.HELLO, b"hi")
check("frame length", len(f) == 6)
check("magic present", f[0:2] == bn.MAGIC)
check("roundtrip", bn.decode(f) == (bn.HELLO, b"hi"))
check("empty body", bn.decode(bn.encode(bn.ACK)) == (bn.ACK, b""))
check("max body ok", len(bn.encode(bn.HELLO, b"x" * bn.MAX_BODY)) == bn.MAX_FRAME)
raises("over max body raises", lambda: bn.encode(bn.HELLO, b"x" * (bn.MAX_BODY + 1)))
raises("way over raises", lambda: bn.encode(bn.HELLO, b"x" * 1000))
check("MAX_BODY is 246", bn.MAX_BODY == 246)

print("--- decode rejects junk ---")
check("None", bn.decode(None) is None)
check("empty", bn.decode(b"") is None)
check("too short", bn.decode(b"CC\x01") is None)
check("bad magic", bn.decode(b"XX\x01\x01body") is None)
check("bad proto", bn.decode(b"CC\x99\x01body") is None)
check("foreign traffic", bn.decode(b"\x00\x01\x02\x03\x04") is None)
# Any byte string at all must return either None or a valid tuple, never raise.
ok = True
for junk in (b"C", b"CC", b"CC\x01", b"\xff" * 250, bytes(range(20))):
    try:
        r = bn.decode(junk)
        ok = ok and (r is None or (isinstance(r, tuple) and len(r) == 2))
    except Exception:
        ok = False
check("never raises on junk", ok)

print("--- addressed bodies ---")
mac = b"\x01\x02\x03\x04\x05\x06"
b = bn.pack_addressed(mac, b"card")
check("addressed roundtrip", bn.unpack_addressed(b) == (mac, b"card"))
check("addressed no extra", bn.unpack_addressed(bn.pack_addressed(mac)) == (mac, b""))
check("short body -> None", bn.unpack_addressed(b"123") is None)
raises("bad mac len raises", lambda: bn.pack_addressed(b"123"))
check("mac_str", bn.mac_str(mac) == "01:02:03:04:05:06")
check("short_id", bn.short_id(mac) == "0506")

print("--- addressed fits in one frame ---")
card = b"y" * (bn.MAX_BODY - 6)
check("6+240 fits", len(bn.encode(bn.SHAKE, bn.pack_addressed(mac, card))) == bn.MAX_FRAME)
raises("6+241 raises",
       lambda: bn.encode(bn.SHAKE, bn.pack_addressed(mac, b"y" * (bn.MAX_BODY - 5))))

print("--- PeerTable ---")
t = bn.PeerTable(ttl=10.0, smooth=0.5)
a = bytes((2, 0, 0, 0, 0, 1))
c = bytes((2, 0, 0, 0, 0, 2))
t.observe(a, -50, now=100.0, handle="ada")
check("created", len(t) == 1)
check("handle stored", t.peers[a]["handle"] == "ada")
check("rssi initial", t.peers[a]["rssi"] == -50.0)
check("count 1", t.peers[a]["count"] == 1)
t.observe(a, -60, now=101.0)
check("rssi smoothed halfway", abs(t.peers[a]["rssi"] - (-55.0)) < 1e-6)
check("count 2", t.peers[a]["count"] == 2)
check("handle kept when omitted", t.peers[a]["handle"] == "ada")
check("first preserved", t.peers[a]["first"] == 100.0)
check("last advanced", t.peers[a]["last"] == 101.0)

t.observe(c, -70, now=101.0, handle="ken")
near = t.nearby(now=101.0)
check("two live", len(near) == 2)
check("sorted strongest first", near[0]["mac"] == a and near[1]["mac"] == c)
check("closest is strongest", t.closest(now=101.0)["mac"] == a)
check("min_rssi filters", len(t.nearby(now=101.0, min_rssi=-60.0)) == 1)
check("min_rssi can exclude all", len(t.nearby(now=101.0, min_rssi=-10.0)) == 0)
check("closest honours min_rssi", t.closest(now=101.0, min_rssi=-10.0) is None)

check("nothing aged yet", t.age(now=105.0) == [])
check("still two", len(t) == 2)
gone = t.age(now=120.0)
check("both aged out", len(gone) == 2 and len(t) == 0)
check("nearby empty after age", t.nearby(now=120.0) == [])
check("closest None when empty", t.closest(now=120.0) is None)

t2 = bn.PeerTable(ttl=10.0)
t2.observe(a, -50, now=0.0)
check("label falls back to short_id", t2.label(t2.peers[a]) == "0001")
t2.observe(a, -50, now=0.0, handle="ada")
check("label prefers handle", t2.label(t2.peers[a]) == "ada")
# nearby() must exclude stale entries even before age() is called.
check("nearby hides stale", t2.nearby(now=50.0) == [])
check("but table still holds it", len(t2) == 1)

print("--- rssi_to_unit ---")
check("very close -> 0", bn.rssi_to_unit(-20) == 0.0)
check("at near -> 0", bn.rssi_to_unit(-40) == 0.0)
check("at far -> 1", bn.rssi_to_unit(-85) == 1.0)
check("beyond far -> 1", bn.rssi_to_unit(-120) == 1.0)
mid = bn.rssi_to_unit(-62.5)
check("midpoint ~0.5", abs(mid - 0.5) < 0.02)
mono = True
prev = -1.0
for r in range(-30, -100, -5):
    u = bn.rssi_to_unit(r)
    mono = mono and (u >= prev) and 0.0 <= u <= 1.0
    prev = u
check("monotonic and bounded", mono)

print("--- SimRadio ---")
s = bn.SimRadio(handles=("a", "b"), period=0.0, seed=1)
check("sim has a mac", len(s.mac) == 6)
obs = s.poll()
check("sim yields observations", len(obs) > 0)
m, kind, body, rssi, ts = obs[0]
check("sim shape: mac", len(m) == 6)
check("sim shape: kind", kind == bn.HELLO)
check("sim shape: rssi is int", isinstance(rssi, int))
check("sim rssi plausible", -100 < rssi < 0)
check("sim body decodes as handle", body in (b"a", b"b"))
s.send(bn.HELLO, b"x")
raises("sim still enforces size", lambda: s.send(bn.HELLO, b"x" * 300))
# Feeding sim output straight into the table is the real integration path.
tt = bn.PeerTable()
for m, kind, body, rssi, ts in s.poll():
    tt.observe(m, rssi, now=1.0, handle=body.decode())
check("sim feeds PeerTable", len(tt) >= 1)

print()
print("PASSED %d, FAILED %d" % (len(_passed), len(_failed)))
if _failed:
    print("failures:", _failed)
