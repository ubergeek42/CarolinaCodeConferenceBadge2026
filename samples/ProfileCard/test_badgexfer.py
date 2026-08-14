"""
test_badgexfer.py -- self-test for the module transfer protocol
==============================================================
Runs unmodified on CPython and on the badge:

    python3 samples/ProfileCard/test_badgexfer.py
    exec(open("/test_badgexfer.py").read())     # from the badge REPL

The bottom half is the reason this file exists: two `LoopRadio`s on one
`Air`, a real Sender and a real Receiver, and a full transfer of an actual
module -- with a third of the frames thrown away -- driven to completion in
a loop with fake time. ESP-NOW has no loopback, so without this there is no
way to exercise a transfer at all until a second badge exists.
"""

import gc
import sys

sys.path.append("lib")
sys.path.append("/lib")

import badgexfer as bx
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


# ------------------------------------------------------------------
# chunking
# ------------------------------------------------------------------
print("chunking")
eq(bx.n_chunks(1), 1, "one byte is one chunk")
eq(bx.n_chunks(bx.CHUNK), 1, "exactly one chunk")
eq(bx.n_chunks(bx.CHUNK + 1), 2, "one byte over spills")
blob = bytes(range(256)) * 12                     # 3072 bytes
parts = bx.split(blob)
eq(len(parts), bx.n_chunks(len(blob)), "split agrees with n_chunks")
eq(b"".join(parts), blob, "split then join is the identity")
ok(all(len(p) == bx.CHUNK for p in parts[:-1]), "every chunk but the last is full")


# ------------------------------------------------------------------
# wire format
# ------------------------------------------------------------------
print("wire format")
offer = bx.build_offer("syncflash", blob, hops=2, flags=bx.FLAG_DEFLATE)
eq(offer.name, "syncflash", "name kept")
eq(offer.total, len(blob), "total is the blob length")
eq(offer.mod_id, badgemod.mod_id_for("syncflash"), "mod_id matches the runtime's")
round_trip = bx.unpack_offer(bx.pack_offer(offer))
eq(round_trip, offer, "offer survives the wire")
eq(round_trip.name, "syncflash", "and so does the name")
eq(round_trip.hops, 2, "and the hop count")
eq(round_trip.flags, bx.FLAG_DEFLATE, "and the flags")
ok(len(bx.pack_offer(offer)) <= 246, "offer fits in one ESP-NOW body")

eq(bx.unpack_offer(b""), None, "empty body is not an offer")
eq(bx.unpack_offer(b"\x00" * 8), None, "truncated body is not an offer")
bad = bytearray(bx.pack_offer(offer))
bad[4] = 99                                        # chunk count now a lie
eq(bx.unpack_offer(bytes(bad)), None, "a manifest that disagrees with itself is refused")

mid, seq, payload = bx.unpack_data(bx.pack_data(0xBEEF, 7, b"hello"))
eq((mid, seq, payload), (0xBEEF, 7, b"hello"), "data frame survives the wire")
eq(bx.unpack_data(b"\x00"), None, "truncated data frame is refused")
try:
    bx.pack_data(1, 0, bytes(bx.CHUNK + 1))
    ok(False, "oversized chunk should raise")
except ValueError:
    ok(True, "oversized chunk raises rather than being silently truncated")

mid, missing = bx.unpack_req(bx.pack_req(0x1234, [0, 5, 17], 20), 20)
eq(mid, 0x1234, "req carries the mod_id")
eq(missing, [0, 5, 17], "req bitmap round-trips")
eq(bx.unpack_req(bx.pack_req(0x1, [], 8), 8)[1], [], "an empty req is empty")


# ------------------------------------------------------------------
# sender: one frame per tick, and a full lap in order
# ------------------------------------------------------------------
print("sender pacing")
sent = []
small = b"abcdefgh" * (bx.CHUNK * 3 // 8)          # exactly 3 chunks of text
off = bx.build_offer("tiny", small)
snd = bx.Sender(lambda k, b: sent.append((k, b)), off, small)

eq(snd.tick(0.0), True, "first tick sends")
eq(snd.tick(0.001), False, "a tick inside the pace window sends nothing")
eq(len(sent), 1, "and really did not send")
eq(sent[0][0], bx.OFFER, "the lap opens with the OFFER")

t = 0.0
for _ in range(4):
    t += bx.PACE
    snd.tick(t)
eq([k for k, _ in sent], [bx.OFFER, bx.DATA, bx.DATA, bx.DATA, bx.OFFER],
   "OFFER, every chunk in order, then OFFER again")
eq(snd.laps, 1, "one lap completed")
eq([bx.unpack_data(b)[1] for k, b in sent if k == bx.DATA], [0, 1, 2],
   "chunks went out in sequence")

# REQ splices to the front of the queue.
eq(snd.on_req(bx.pack_req(off.mod_id, [2], off.chunks)), 1, "req accepted")
eq(snd.on_req(bx.pack_req(0xDEAD, [1], off.chunks)), 0, "another module's req ignored")
t += bx.PACE
snd.tick(t)
eq(bx.unpack_data(sent[-1][1])[1], 2, "the REQ'd chunk jumped the queue")


# ------------------------------------------------------------------
# receiver: consent gates execution, not buffering
# ------------------------------------------------------------------
print("receiver consent")
rx = bx.Receiver()
rx.on_frame(b"\x01" * 6, bx.OFFER, bx.pack_offer(off), -50, now=0.0)
eq(rx.state, bx.OFFERED, "an offer moves us to OFFERED")
eq(rx.offer.name, "tiny", "and names the module")
for i, part in enumerate(bx.split(small)):
    rx.on_frame(b"\x01" * 6, bx.DATA, bx.pack_data(off.mod_id, i, part), -50, now=0.1)
eq(rx.complete, True, "chunks were buffered before any consent")
eq(rx.state, bx.OFFERED, "but the state has not advanced")
eq(rx.take(), None, "and take() refuses to hand over unaccepted code")
eq(rx.accept(), True, "accept")
got = rx.take()
ok(got is not None, "now it hands the module over")
eq(got[1].encode()[:8], small[:8], "and the bytes are the ones that were sent")
eq(rx.state, bx.COMPLETE, "state is COMPLETE")

# Rejection has to stick, or declining just means being asked again.
rx2 = bx.Receiver()
rx2.on_frame(b"\x01" * 6, bx.OFFER, bx.pack_offer(off), -50, now=0.0)
rx2.reject(now=0.0)
eq(rx2.state, bx.IDLE, "rejecting resets")
rx2.on_frame(b"\x01" * 6, bx.OFFER, bx.pack_offer(off), -50, now=1.0)
eq(rx2.state, bx.IDLE, "and the same offer is ignored a second later")
rx2.on_frame(b"\x01" * 6, bx.OFFER, bx.pack_offer(off), -50,
             now=bx.REJECT_SECS + 2.0)
eq(rx2.state, bx.OFFERED, "but not forever -- it may be offered again later")

# A module we already run should not be offered back to us.
rx3 = bx.Receiver(ignore=(off.mod_id,))
rx3.on_frame(b"\x01" * 6, bx.OFFER, bx.pack_offer(off), -50, now=0.0)
eq(rx3.state, bx.IDLE, "a module we already have is not offered")


# ------------------------------------------------------------------
# corruption must fail, not execute
# ------------------------------------------------------------------
print("integrity")
rx4 = bx.Receiver()
rx4.on_frame(b"\x02" * 6, bx.OFFER, bx.pack_offer(off), -50, now=0.0)
rx4.accept()
parts = bx.split(small)
parts[1] = bytes(bx.CHUNK)                         # same length, wrong bytes
for i, part in enumerate(parts):
    rx4.on_frame(b"\x02" * 6, bx.DATA, bx.pack_data(off.mod_id, i, part), -50, now=0.1)
eq(rx4.complete, True, "all chunks present")
eq(rx4.take(), None, "but a bad CRC is not handed to exec")
eq(rx4.state, bx.FAILED, "state is FAILED")
ok("crc" in rx4.error, "and the error says why")

# A short middle chunk would assemble at the wrong offsets, so it is refused
# at arrival rather than discovered by the CRC seconds later.
rx5 = bx.Receiver()
rx5.on_frame(b"\x02" * 6, bx.OFFER, bx.pack_offer(off), -50, now=0.0)
rx5.on_frame(b"\x02" * 6, bx.DATA, bx.pack_data(off.mod_id, 0, b"short"), -50, now=0.1)
eq(rx5.missing, [0, 1, 2], "a short non-final chunk is dropped")

# Deflate: the badge can only decompress, so this is the path a real
# host-built .mod takes.
print("deflate path")
import binascii
import zlib

# A real raw-deflate stream, built by CPython, of the source below. Both
# runtimes inflate this same fixture: the badge cannot build one (its `zlib`
# is decompress-only) but it absolutely must be able to read one, since this
# is the exact shape tools/mkmod.py writes and the air carries.
FIXTURE_SRC = "NAME = 'x'\ndef tick(ctx, now): pass\n" * 20
FIXTURE = binascii.unhexlify(
    "f373f47555b05550af50e74a494d5328c94cced6482ea9d051c8cb2fd7b452284"
    "82c2ee6f21b5533aa6688a80100")
eq(bx.decode_blob(FIXTURE, bx.FLAG_DEFLATE), FIXTURE_SRC,
   "a host-built deflate blob inflates here")
ok(len(FIXTURE) < len(FIXTURE_SRC) // 4, "and it was worth compressing")

# The guard below is a missing *attribute*, not a missing module: the badge
# has `zlib`, just not `compressobj`. An `except ImportError` looked right
# and blew up on hardware -- which is the whole argument for running this
# file in both places.
if hasattr(zlib, "compressobj"):
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = co.compress(FIXTURE_SRC.encode()) + co.flush()
    eq(bx.decode_blob(packed, bx.FLAG_DEFLATE), FIXTURE_SRC,
       "and a freshly compressed one round-trips")
else:
    print("  decompress-only zlib, as expected on the badge")
    ok(not hasattr(zlib, "compress"), "no compressor here, by design")
eq(bx.decode_blob(b"abc", 0), "abc", "an uncompressed blob passes through")


# ------------------------------------------------------------------
# the real thing: a full transfer over a lossy Air
# ------------------------------------------------------------------
print("end-to-end over lossy air")


def read(path):
    for prefix in ("", "/"):
        try:
            with open(prefix + path) as f:
                return f.read()
        except OSError:
            continue
    raise OSError("cannot find " + path)


SOURCE = read("mods/syncflash.py")


def read_bytes(path):
    for prefix in ("", "/"):
        try:
            with open(prefix + path, "rb") as f:
                return f.read()
        except OSError:
            continue
    return None


# Carry the real thing: the deflate blob tools/mkmod.py built, with the flag a
# real OFFER would set.
BLOB = read_bytes("mods/syncflash.mod")
FLAGS = bx.FLAG_DEFLATE
if BLOB is None:                       # no .mod built yet; fall back
    BLOB, FLAGS = SOURCE.encode(), 0
ok(len(BLOB) <= bx.MAX_BLOB, "the blob fits inside the wire cap")
if FLAGS & bx.FLAG_DEFLATE:
    # Both forms fit here, so this is about airtime rather than possibility:
    # compression is what makes a module a handful of frames instead of dozens.
    ok(len(BLOB) < len(SOURCE.encode()) // 2,
       "and compression more than halved it (%d -> %d bytes, %d -> %d chunks)"
       % (len(SOURCE.encode()), len(BLOB),
          bx.n_chunks(len(SOURCE.encode())), bx.n_chunks(len(BLOB))))
gc.collect()
try:
    print("  heap free before transfers:", gc.mem_free())
except AttributeError:
    pass                          # CPython has no mem_free, and does not need it


def run_transfer(drop_every, receivers=1, limit=40.0):
    """Drive a sender and N receivers to completion. Returns (secs, results)."""
    air = bx.Air(drop_every=drop_every)
    tx_radio = air.join(b"\x02\x00\x00\x00\x00\x01")
    offer = bx.build_offer("syncflash", BLOB, flags=FLAGS)
    sender = bx.Sender(tx_radio.send, offer, BLOB)

    ends = []
    for i in range(receivers):
        radio = air.join(bytes((2, 0, 0, 0, 0, 10 + i)))
        ends.append((radio, bx.Receiver(send=radio.send)))

    t = 0.0
    results = [None] * receivers
    while t < limit and any(r is None for r in results):
        sender.tick(t)
        for kind, body in [(k, b) for _m, k, b, _r, _tt in tx_radio.poll()
                           if k == bx.REQ]:
            sender.on_req(body)
        for i, (radio, rx) in enumerate(ends):
            for mac, kind, body, rssi, _tt in radio.poll():
                rx.on_frame(mac, kind, body, rssi, now=t)
            if rx.state == bx.OFFERED:
                rx.accept()                       # stand in for the button
            rx.tick(t)
            if results[i] is None:
                got = rx.take()
                if got is not None:
                    # Compare and discard rather than keeping the source.
                    # Three receivers each holding an inflated 9 KB module is
                    # 27 KB of a ~100 KB heap, and on the badge that was the
                    # difference between this test passing and a MemoryError.
                    offer_got, source_got, _blob = got
                    results[i] = (t, offer_got, len(source_got),
                                  source_got == SOURCE)
                    got = source_got = None
                    gc.collect()
        t += 0.02                                  # a main-loop pass
    return t, results, sender, air


t, results, sender, air = run_transfer(drop_every=0)
ok(results[0] is not None, "lossless transfer completes")
done_t = results[0][0]
eq(results[0][3], True, "the receiver reconstructed the module byte for byte")
eq(results[0][2], len(SOURCE), "at the right length")
eq(results[0][1].name, "syncflash", "with the right name")
ok(done_t <= 2.0, "and took under 2 s of badge time (was %.2fs)" % done_t)
ok(sender.laps <= 2, "in no more than two laps")

del results, sender, air
gc.collect()
t, results, sender, air = run_transfer(drop_every=3)
ok(results[0] is not None, "transfer completes with a third of frames dropped")
eq(results[0][3], True, "still byte for byte")
ok(air.dropped >= air.frames // 4,
   "and a real share of frames was dropped (%d of %d)" % (air.dropped, air.frames))

# Several receivers on one sender is the case the carousel exists for: no
# per-receiver state, so three should finish in about the time one does.
del results, sender, air
gc.collect()
t, results, sender, air = run_transfer(drop_every=4, receivers=3)
ok(all(r is not None for r in results), "three receivers all complete")
ok(all(r[3] for r in results), "all three reconstructed it exactly")
spread = max(r[0] for r in results) - min(r[0] for r in results)
ok(spread < 2.0, "and finished within 2 s of each other (%.2fs)" % spread)

# Tail repair: a receiver that misses one chunk in the last lap should REQ it
# rather than wait a whole extra lap.
reqs = []
air2 = bx.Air()
tx2 = air2.join(b"\x03\x00\x00\x00\x00\x01")
rx_radio = air2.join(b"\x03\x00\x00\x00\x00\x02")
offer2 = bx.build_offer("syncflash", BLOB, flags=FLAGS)
snd2 = bx.Sender(tx2.send, offer2, BLOB)
rcv2 = bx.Receiver(send=lambda k, b: reqs.append((k, b)) or rx_radio.send(k, b))
rcv2.on_frame(tx2.mac, bx.OFFER, bx.pack_offer(offer2), -50, now=0.0)
rcv2.accept()
allparts = bx.split(BLOB)
for i, part in enumerate(allparts):
    if i != 2:
        rcv2.on_frame(tx2.mac, bx.DATA, bx.pack_data(offer2.mod_id, i, part),
                      -50, now=0.1)
eq(rcv2.missing, [2], "one chunk short")
eq(rcv2.tick(0.2), False, "no REQ before a couple of laps have gone by")
lap = (offer2.chunks + 1) * bx.PACE
eq(rcv2.tick(3.0 * lap + 1.0), True, "then a REQ goes out")
eq(bx.unpack_req(reqs[-1][1], offer2.chunks)[1], [2], "naming exactly the gap")
eq(snd2.on_req(reqs[-1][1]), 1, "and the sender queues it")


print("PASSED %d, FAILED %d" % (PASS, FAIL))
