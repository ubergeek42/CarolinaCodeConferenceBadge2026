"""
badgexfer.py -- moving a module from one badge to another over ESP-NOW
=====================================================================
First-party module (not part of the Adafruit bundle -- see lib/NOTICES.md).
Sits on top of `badgenet`'s codec and transports and carries a blob of
bytes -- in practice a compressed Python module -- from one badge to any
number of others.

Kept out of `badgenet` on purpose: BadgeRadar wants a radio and a peer
table, and should not have to carry a code-transfer protocol it never uses.

Why a carousel instead of a conversation
----------------------------------------
Broadcast ESP-NOW gives **no delivery feedback whatsoever** -- confirmed on
this hardware, `send_success` and `send_failure` both sit at 0 forever. A
request/response protocol has nothing to build on, and there may be five
receivers anyway.

So the sender does not negotiate. It loops:

    OFFER, chunk 0, chunk 1, ... chunk N, OFFER, chunk 0, ...

one frame per main-loop pass, for as long as sharing is switched on. A
receiver joins at any point in the lap, collects chunks in whatever order
they arrive, and is done when its bitmap fills. Loss costs it another lap
rather than a retransmission dance, one sender feeds a whole room with no
per-receiver state, and everything stays on broadcast -- which also dodges
the 20-peer cap that makes per-badge registration a dead end.

`REQ` exists only as tail repair: a receiver still short of a few chunks
after a couple of laps names them in a bitmap and the sender splices those
to the front of the next lap. It is an optimisation, not the mechanism, and
the transfer completes without it.

Pacing is the one hard rule
---------------------------
Measured on this badge: **unpaced sends saturate the TX queue and then
`send()` blocks for up to 205 ms**, with no exception and no counter. Paced
at 8 ms or more it is a flat 0.6 ms every time. `Sender.tick()` therefore
emits at most one frame per call and refuses to go faster than `PACE`. Do
not "optimise" this into a loop.

Compression lives on the host
-----------------------------
`zlib` on the badge is decompress-only, so a badge can never compress. It
does not need to: `tools/mkmod.py` builds a `.mod` blob host-side with raw
deflate (0.37 of source, measured), and a badge relaying a module forwards
the exact bytes it received along with the CRC it was given. A module
authored locally with no `.mod` beside it is shared uncompressed -- still
only about a second per lap -- so nothing is blocked on a compressor that
cannot exist.
"""

import gc
import time

import binascii

import badgenet as bn

# Message kinds, in the range badgenet reserves for samples (>= 0x20).
OFFER = 0x20
DATA = 0x21
REQ = 0x22
MODMSG = 0x30           # module-to-module traffic, routed by mod_id

# 240 leaves room for badgenet's 4-byte frame header plus our 3-byte DATA
# header inside the 250-byte ESP-NOW limit, with a little slack.
CHUNK = 240
MAX_CHUNKS = 34         # seq is one byte, but this is the real cap: 8 KB
MAX_BLOB = CHUNK * MAX_CHUNKS

# Minimum gap between frames. 8 ms is where the measured stalls stop; 15 ms
# is deliberately slacker than that, because the receiving badge also has to
# service a display that can hold its loop for 87 ms.
PACE = 0.015

# How long a rejected module stays rejected. Without this, declining an
# offer just means being asked again 300 ms later, forever.
REJECT_SECS = 60.0

FLAG_DEFLATE = 0x01     # payload is raw deflate (wbits=-15)

# Receiver states
IDLE, OFFERED, RECEIVING, COMPLETE, FAILED = 0, 1, 2, 3, 4
STATE_NAMES = ("IDLE", "OFFERED", "RECEIVING", "COMPLETE", "FAILED")


def crc32(data):
    return binascii.crc32(data) & 0xFFFFFFFF


# ------------------------------------------------------------------
# Wire format -- pure functions, no hardware, safe to test anywhere
# ------------------------------------------------------------------
class Offer:
    """A manifest: everything a receiver needs before the bytes arrive."""

    def __init__(self, mod_id, name, total, chunks, crc, hops=0, flags=0):
        self.mod_id = mod_id
        self.name = name
        self.total = total
        self.chunks = chunks
        self.crc = crc
        self.hops = hops
        self.flags = flags

    def __eq__(self, other):
        return (isinstance(other, Offer) and self.mod_id == other.mod_id
                and self.crc == other.crc and self.total == other.total)

    def __repr__(self):
        return "<Offer %s %dB in %d chunks crc%08x hops%d%s>" % (
            self.name, self.total, self.chunks, self.crc, self.hops,
            " deflate" if self.flags & FLAG_DEFLATE else "")


def pack_offer(offer):
    name = offer.name.encode() if isinstance(offer.name, str) else offer.name
    return (bytes((offer.mod_id >> 8, offer.mod_id & 0xFF,
                   offer.total >> 8, offer.total & 0xFF,
                   offer.chunks,
                   (offer.crc >> 24) & 0xFF, (offer.crc >> 16) & 0xFF,
                   (offer.crc >> 8) & 0xFF, offer.crc & 0xFF,
                   offer.hops, offer.flags))
            + name[:24])


def unpack_offer(body):
    """Offer, or None if the frame is malformed.

    None rather than an exception: this decodes bytes off a shared radio
    channel, where garbage is a normal input.
    """
    if body is None or len(body) < 11:
        return None
    mod_id = (body[0] << 8) | body[1]
    total = (body[2] << 8) | body[3]
    chunks = body[4]
    crc = (body[5] << 24) | (body[6] << 16) | (body[7] << 8) | body[8]
    hops = body[9]
    flags = body[10]
    try:
        name = bytes(body[11:]).decode()
    except Exception:
        return None
    if not 0 < chunks <= MAX_CHUNKS or not 0 < total <= MAX_BLOB:
        return None
    if chunks != n_chunks(total):
        return None                      # manifest disagrees with itself
    return Offer(mod_id, name, total, chunks, crc, hops, flags)


def pack_data(mod_id, seq, payload):
    if len(payload) > CHUNK:
        raise ValueError("chunk %d > %d" % (len(payload), CHUNK))
    return bytes((mod_id >> 8, mod_id & 0xFF, seq)) + bytes(payload)


def unpack_data(body):
    if body is None or len(body) < 4:
        return None
    return (body[0] << 8) | body[1], body[2], bytes(body[3:])


def pack_req(mod_id, missing, chunks):
    """Bitmap of wanted chunks. Bit i set == "please resend chunk i"."""
    n = (chunks + 7) // 8
    bits = bytearray(n)
    for seq in missing:
        if 0 <= seq < chunks:
            bits[seq // 8] |= 1 << (seq % 8)
    return bytes((mod_id >> 8, mod_id & 0xFF)) + bytes(bits)


def unpack_req(body, chunks=None):
    if body is None or len(body) < 3:
        return None
    mod_id = (body[0] << 8) | body[1]
    bits = body[2:]
    limit = chunks if chunks is not None else len(bits) * 8
    return mod_id, [i for i in range(min(limit, len(bits) * 8))
                    if bits[i // 8] & (1 << (i % 8))]


def n_chunks(total):
    return (total + CHUNK - 1) // CHUNK


def split(blob, size=CHUNK):
    return [bytes(blob[i:i + size]) for i in range(0, len(blob), size)]


def decode_blob(blob, flags):
    """Blob -> module source. Raises on corrupt deflate, which is the point.

    `wbits=-15` is not optional: this build's `zlib.decompress` cannot
    autodetect a stream, and bare gzip fails with `ValueError: -3`.
    """
    if flags & FLAG_DEFLATE:
        import zlib
        return zlib.decompress(blob, -15).decode()
    return bytes(blob).decode()


def build_offer(name, blob, hops=0, flags=0, mod_id=None):
    """Manifest for a blob you are about to share."""
    if len(blob) > MAX_BLOB:
        raise ValueError("blob %d > %d bytes" % (len(blob), MAX_BLOB))
    if mod_id is None:
        import badgemod
        mod_id = badgemod.mod_id_for(name)
    return Offer(mod_id, name, len(blob), n_chunks(len(blob)), crc32(blob),
                 hops, flags)


# ------------------------------------------------------------------
# Sender -- one frame per tick, forever, until switched off
# ------------------------------------------------------------------
class Sender:
    """Broadcasts a blob round and round. Stateless with respect to receivers.

    `send` is any callable taking (kind, body) -- `badgenet.Radio.send`, or a
    test double.
    """

    def __init__(self, send, offer, blob, pace=PACE):
        self.send = send
        self.offer = offer
        self.chunks = split(blob)
        self.pace = pace
        self.pos = -1               # -1 means "OFFER next"
        self.laps = 0
        self.frames = 0
        self.priority = []          # seqs asked for by REQ, sent next
        self._next = 0.0
        self._offer_body = pack_offer(offer)

    def tick(self, now):
        """Emit at most one frame. Returns True if it sent something.

        One frame per call is the entire pacing strategy, and it is not
        negotiable -- see the module docstring.
        """
        if now < self._next:
            return False
        self._next = now + self.pace
        if self.priority:
            seq = self.priority.pop(0)
            self.send(DATA, pack_data(self.offer.mod_id, seq, self.chunks[seq]))
        elif self.pos < 0:
            self.send(OFFER, self._offer_body)
            self.pos = 0
        else:
            seq = self.pos
            self.send(DATA, pack_data(self.offer.mod_id, seq, self.chunks[seq]))
            self.pos += 1
            if self.pos >= len(self.chunks):
                self.pos = -1
                self.laps += 1
        self.frames += 1
        return True

    def on_req(self, body):
        """Splice REQ'd chunks to the front of the queue. Ignores other mods."""
        got = unpack_req(body, self.offer.chunks)
        if got is None or got[0] != self.offer.mod_id:
            return 0
        added = 0
        for seq in got[1]:
            if seq < len(self.chunks) and seq not in self.priority:
                self.priority.append(seq)
                added += 1
        return added

    @property
    def lap_secs(self):
        return (len(self.chunks) + 1) * self.pace


# ------------------------------------------------------------------
# Receiver -- buffers without consent, executes only with it
# ------------------------------------------------------------------
class Receiver:
    """Collects one module at a time.

    Deliberate: chunks are buffered as soon as they are heard, *before* the
    human has accepted anything. Consent gates execution, not memory -- and
    buffering early means accepting is instant instead of costing another
    full lap. The bytes are inert until `take()` hands them to the runtime.

    One offer at a time. If two badges are sharing different modules at
    once, the second is ignored until the first resolves, because a UI that
    asks two questions at once on a 128x160 screen is a worse answer than
    making the second sender wait a lap.
    """

    def __init__(self, send=None, ignore=None, max_blob=MAX_BLOB):
        self.send = send
        self.state = IDLE
        self.offer = None
        self.src_mac = None
        self.rssi = 0
        self.have = []              # parallel to chunks: bytes or None
        self.max_blob = max_blob
        self.started = 0.0
        self.last_frame = 0.0
        self.laps_seen = 0
        self.frames = 0
        self.dupes = 0
        self.rejected = {}          # mod_id -> when the rejection expires
        self.error = None
        # mod_id -> crc32 of the copy we already hold. Keyed on the *bytes*, not
        # just the name: a newer build of a module you already run has the same
        # mod_id and must still be able to reach you, or a fix can never
        # propagate past the first badge.
        if ignore is None:
            self.mine = {}
        elif isinstance(ignore, dict):
            self.mine = dict(ignore)
        else:
            self.mine = {mod_id: None for mod_id in ignore}   # any version
        # The last offer we turned down without asking, and why. Exposed so the
        # UI can say so: an offer that vanishes silently is indistinguishable
        # from a radio that is not working, which is exactly the bug this
        # attribute exists to prevent.
        self.declined = None        # (name, reason, when)
        self._last_req = 0.0

    # -- inbound ----------------------------------------------------------
    def on_frame(self, mac, kind, body, rssi=0, now=None):
        now = time.monotonic() if now is None else now
        if kind == OFFER:
            self._on_offer(mac, body, rssi, now)
        elif kind == DATA:
            self._on_data(mac, body, now)

    def _on_offer(self, mac, body, rssi, now):
        offer = unpack_offer(body)
        if offer is None:
            return
        if offer.total > self.max_blob:
            self.declined = (offer.name, "too big", now)
            return
        if offer.mod_id in self.mine:
            known = self.mine[offer.mod_id]
            if known is None or known == offer.crc:
                self.declined = (offer.name, "have it", now)
                return
            # Same module, different bytes: a newer build. Let it through.
        exp = self.rejected.get(offer.mod_id)
        if exp is not None:
            if now < exp:
                self.declined = (offer.name, "declined", now)
                return
            del self.rejected[offer.mod_id]
        if self.offer is None:
            self._begin(mac, offer, rssi, now)
        elif offer == self.offer:
            self.laps_seen += 1
            self.rssi = rssi
            self.last_frame = now
        elif offer.mod_id == self.offer.mod_id and self.state in (OFFERED, IDLE):
            # Same module, different bytes -- the sender changed it mid-share.
            # Start over rather than mixing two versions into one blob.
            self._begin(mac, offer, rssi, now)

    def _begin(self, mac, offer, rssi, now):
        self.offer = offer
        self.src_mac = bytes(mac)
        self.rssi = rssi
        self.have = [None] * offer.chunks
        self.state = OFFERED
        self.started = now
        self.last_frame = now
        self.laps_seen = 0
        self.frames = 0
        self.dupes = 0
        self.error = None

    def _on_data(self, mac, body, now):
        got = unpack_data(body)
        if got is None or self.offer is None:
            return
        mod_id, seq, payload = got
        if mod_id != self.offer.mod_id or seq >= self.offer.chunks:
            return
        self.frames += 1
        self.last_frame = now
        if self.have[seq] is not None:
            self.dupes += 1
            return
        # Every chunk but the last must be full: a short middle chunk means
        # the blob would assemble at the wrong offsets and only fail at the
        # CRC, several seconds later.
        if seq < self.offer.chunks - 1 and len(payload) != CHUNK:
            return
        self.have[seq] = payload

    # -- outbound ---------------------------------------------------------
    def tick(self, now):
        """Send tail-repair REQs when a lap has gone by and gaps remain."""
        if self.offer is None or self.send is None or self.complete:
            return False
        missing = self.missing
        if not missing:
            return False
        # Only after two laps' worth of silence on those chunks, and never
        # more than once a lap: a REQ storm from five receivers at once would
        # cost more air than another lap of the carousel.
        lap = (self.offer.chunks + 1) * PACE
        if now - self._last_req < max(2.0 * lap, 1.0):
            return False
        if now - self.started < 2.0 * lap:
            return False
        self._last_req = now
        self.send(REQ, pack_req(self.offer.mod_id, missing[:64], self.offer.chunks))
        return True

    # -- state ------------------------------------------------------------
    @property
    def missing(self):
        return [i for i, c in enumerate(self.have) if c is None]

    @property
    def complete(self):
        return self.offer is not None and not self.missing

    @property
    def progress(self):
        if self.offer is None:
            return 0.0
        got = self.offer.chunks - len(self.missing)
        return got / self.offer.chunks

    def accept(self):
        if self.state == OFFERED:
            self.state = RECEIVING
            return True
        return False

    def reject(self, now=None):
        now = time.monotonic() if now is None else now
        if self.offer is not None:
            self.rejected[self.offer.mod_id] = now + REJECT_SECS
        self.reset()

    def reset(self):
        self.state = IDLE
        self.offer = None
        self.src_mac = None
        self.have = []
        self.error = None

    def take(self):
        """(offer, source, blob) once accepted and complete, else None.

        Verifies the CRC and decompresses. A blob that fails either is
        dropped with `state = FAILED` and `error` set, rather than handed to
        `exec` -- corrupt source is how a transfer bug becomes a crash loop.
        """
        if self.state != RECEIVING or not self.complete:
            return None
        # Collect first. Assembling needs one contiguous allocation the size
        # of the whole module, and the chunks it is built from have been
        # arriving one at a time for the last second or two -- exactly the
        # pattern that leaves a heap too fragmented to satisfy it. Found by
        # a MemoryError on hardware where 100 KB was nominally free.
        gc.collect()
        blob = b"".join(self.have)
        if len(blob) != self.offer.total:
            return self._fail("length %d != %d" % (len(blob), self.offer.total))
        if crc32(blob) != self.offer.crc:
            return self._fail("crc mismatch")
        try:
            source = decode_blob(blob, self.offer.flags)
        except Exception as ex:
            return self._fail("inflate: %s %s" % (type(ex).__name__, ex))
        offer = self.offer
        self.state = COMPLETE
        self.mine[offer.mod_id] = offer.crc
        return offer, source, blob

    def _fail(self, why):
        self.error = why
        self.state = FAILED
        return None

    @property
    def state_name(self):
        return STATE_NAMES[self.state]


# ------------------------------------------------------------------
# LoopRadio -- two protocol ends in one process, with optional loss
# ------------------------------------------------------------------
class LoopRadio:
    """A fake radio that delivers to its peers, so a whole transfer can be
    driven with no hardware at all.

    `badgenet.SimRadio` invents neighbours but cannot carry a conversation;
    ESP-NOW has no loopback, so a lone badge hears nothing, not even itself.
    This closes that gap: N of these joined to one `Air` reproduce a room,
    including packet loss, which is the failure mode that matters most since
    broadcast gives no delivery feedback.
    """

    def __init__(self, air, mac):
        self.air = air
        self.mac = bytes(mac)
        self.channel = bn.CHANNEL
        self.tx_power = 20.0
        self.dropped = 0
        self.recoveries = 0
        self.inbox = []

    def send(self, kind, body=b""):
        bn.encode(kind, body)                 # enforce the real size cap
        self.air.deliver(self, kind, body)

    def poll(self):
        out, self.inbox = self.inbox, []
        return out

    def set_tx_power(self, dbm):
        self.tx_power = dbm
        return dbm

    def deinit(self):
        pass


class Air:
    """The shared medium. Drops every `drop_every`-th frame if asked."""

    def __init__(self, drop_every=0, rssi=-55):
        self.radios = []
        self.drop_every = drop_every
        self.rssi = rssi
        self.frames = 0
        self.dropped = 0

    def join(self, mac):
        r = LoopRadio(self, mac)
        self.radios.append(r)
        return r

    def deliver(self, src, kind, body):
        self.frames += 1
        if self.drop_every and self.frames % self.drop_every == 0:
            self.dropped += 1
            return
        for r in self.radios:
            if r is not src:
                r.inbox.append((src.mac, kind, bytes(body), self.rssi,
                                int(self.frames)))
