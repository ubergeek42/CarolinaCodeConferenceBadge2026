"""
badgenet.py -- shared badge-to-badge ESP-NOW plumbing
=====================================================
First-party module (not part of the Adafruit bundle -- see lib/NOTICES.md
for the third-party libraries). Imported by the badge-to-badge samples so
they don't each re-derive the radio setup and its several sharp edges.

Three layers, deliberately separable:

  encode/decode   pure frame codec, no hardware
  PeerTable       pure proximity bookkeeping, no hardware
  Radio/SimRadio  interchangeable transports

The split is not decoration. ESP-NOW has no loopback -- a badge cannot
hear its own broadcast -- so with a single badge the radio can only be
smoke-tested. Keeping the codec and the peer table free of hardware means
everything except the final RF hop is testable on one badge (or on a
laptop), and SimRadio lets a sample's whole UI be built and watched with
no second device in the room.

Everything below was verified against CircuitPython 10.2.1 on the CCC
2026 badge (ESP32-S3). Notes marked GOTCHA are behaviours confirmed on
hardware that the docs get wrong or omit.
"""

import time

# Protocol constants. CHANNEL must match on every badge that wants to
# talk -- there is no wifi.radio.channel property to negotiate at runtime,
# so it is a hard-coded convention, not something discoverable.
CHANNEL = 6

MAGIC = b"CC"          # conference WiFi is busy; ignore everything not ours
PROTO = 1

# Message kinds. Samples are free to add their own above 0x20.
HELLO = 0x01           # "I am here" beacon / handshake solicitation
SHAKE = 0x02           # "I pick you" -- addressed, carries a card
ACK   = 0x03           # "I picked you too" -- addressed, carries a card
CARD  = 0x04           # "here are my details" -- handle + optional link

# CARD is a separate kind rather than a longer HELLO on purpose. HELLO's body
# is a bare handle, and badges already in the wild -- plus BadgeRadar -- decode
# it as exactly that, so widening it would make every existing receiver display
# a mangled name. A new kind is ignored by anything that does not know it,
# which is what forward compatibility looks like on a broadcast channel with
# no version negotiation.
CARD_SEP = b"\x1f"     # unit separator: never appears in a handle or a URL


def pack_card(handle, link=""):
    """handle [+ link] for a CARD body."""
    body = handle.encode() if isinstance(handle, str) else bytes(handle)
    if link:
        body += CARD_SEP + (link.encode() if isinstance(link, str) else bytes(link))
    return body[:MAX_BODY]


def unpack_card(body):
    """(handle, link) from a CARD body. Never raises; garbage gives ("", "").

    Undecodable bytes are a normal input on a shared channel, and a badge whose
    name arrives corrupted should still be counted as present rather than
    dropped -- so this returns empties and lets the caller fall back to the MAC.
    """
    try:
        text = bytes(body).decode()
    except Exception:
        return "", ""
    handle, _, link = text.partition(CARD_SEP.decode())
    return handle, link

BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"

_HDR = 4                       # MAGIC(2) + PROTO(1) + kind(1)
MAX_FRAME = 250                # ESP_NOW_MAX_DATA_LEN
MAX_BODY = MAX_FRAME - _HDR    # 246 usable bytes per packet


# ------------------------------------------------------------------
# Frame codec -- pure functions, no hardware, safe to unit test anywhere
# ------------------------------------------------------------------
def encode(kind, body=b""):
    """Frame a message. Raises ValueError if it would not fit one packet.

    GOTCHA: this length check is the only thing standing between you and
    silent data loss. CircuitPython does NOT validate send() length -- on
    hardware, a 1000-byte broadcast raises nothing, increments no counter,
    and simply does not arrive intact. Never bypass encode().
    """
    if len(body) > MAX_BODY:
        raise ValueError("body %d > %d bytes" % (len(body), MAX_BODY))
    return MAGIC + bytes((PROTO, kind)) + bytes(body)


def decode(frame):
    """(kind, body) for a well-formed frame of ours, else None.

    Returning None rather than raising is deliberate: unknown traffic is
    the normal case on a shared channel, not an error.
    """
    if frame is None or len(frame) < _HDR:
        return None
    if frame[0:2] != MAGIC or frame[2] != PROTO:
        return None
    return frame[3], frame[_HDR:]


def pack_addressed(mac, extra=b""):
    """Body for a message aimed at one badge: 6-byte target + payload.

    Addressing lives in the payload, not the radio. ESP-NOW caps the peer
    list at 20 entries (confirmed on hardware), so registering a peer per
    badge you meet would fail after 19 people. Staying on broadcast and
    filtering in Python scales to a whole conference.
    """
    if len(mac) != 6:
        raise ValueError("mac must be 6 bytes")
    return bytes(mac) + bytes(extra)


def unpack_addressed(body):
    """(target_mac, extra) -- inverse of pack_addressed."""
    if len(body) < 6:
        return None
    return bytes(body[0:6]), body[6:]


def mac_str(mac):
    return ":".join("%02x" % b for b in mac)


def short_id(mac):
    """Last two octets, for display when a badge has no handle yet."""
    return "%02X%02X" % (mac[4], mac[5])


# ------------------------------------------------------------------
# Peer table -- pure bookkeeping, no hardware
# ------------------------------------------------------------------
class PeerTable:
    """Who is nearby, how close, and for how long.

    RSSI is smoothed because raw dBm jumps several points packet to packet
    (bodies, hands and antenna orientation all move it). An unsmoothed
    value makes any distance threshold chatter.
    """

    def __init__(self, ttl=20.0, smooth=0.35):
        self.ttl = ttl
        self.smooth = smooth
        self.peers = {}

    def observe(self, mac, rssi, now=None, handle=None):
        mac = bytes(mac)
        now = time.monotonic() if now is None else now
        p = self.peers.get(mac)
        if p is None:
            p = {
                "mac": mac,
                "handle": handle or "",
                "rssi": float(rssi),
                "first": now,
                "last": now,
                "count": 0,
            }
            self.peers[mac] = p
        else:
            # Exponential moving average toward the new reading.
            p["rssi"] += self.smooth * (rssi - p["rssi"])
            if handle:
                p["handle"] = handle
        p["last"] = now
        p["count"] += 1
        return p

    def age(self, now=None):
        """Forget peers unseen for ttl seconds. Returns the dropped macs."""
        now = time.monotonic() if now is None else now
        gone = [m for m, p in self.peers.items() if now - p["last"] > self.ttl]
        for m in gone:
            del self.peers[m]
        return gone

    def nearby(self, now=None, min_rssi=None):
        """Live peers, strongest signal first."""
        now = time.monotonic() if now is None else now
        out = [p for p in self.peers.values() if now - p["last"] <= self.ttl]
        if min_rssi is not None:
            out = [p for p in out if p["rssi"] >= min_rssi]
        out.sort(key=lambda p: -p["rssi"])
        return out

    def closest(self, now=None, min_rssi=None):
        live = self.nearby(now, min_rssi)
        return live[0] if live else None

    def label(self, p):
        return p["handle"] or short_id(p["mac"])

    def __len__(self):
        return len(self.peers)


def rssi_to_unit(rssi, near=-40.0, far=-85.0):
    """Map dBm to 0.0 (in your face) .. 1.0 (about to vanish).

    The near/far calibration is a guess until measured against a real
    second badge -- RSSI is not a distance sensor, it is a
    signal-strength reading that correlates with distance on a good day.
    """
    if rssi >= near:
        return 0.0
    if rssi <= far:
        return 1.0
    return (near - rssi) / (near - far)


# ------------------------------------------------------------------
# Transports
# ------------------------------------------------------------------
class Radio:
    """ESP-NOW broadcast transport, with the known sharp edges handled."""

    def __init__(self, channel=CHANNEL, buffer_size=2048, tx_power=None):
        import wifi
        import espnow

        self._espnow = espnow
        self._wifi = wifi
        self._buffer_size = buffer_size
        self.channel = channel

        # GOTCHA: there is no wifi.radio.channel property. Bouncing a
        # throwaway AP is the only way to pin the channel, and it must
        # happen BEFORE ESPNow() is constructed.
        wifi.radio.start_ap(" ", "", channel=channel, max_connections=0)
        wifi.radio.stop_ap()

        self.mac = bytes(wifi.radio.mac_address)
        self._open()

        # GOTCHA: constructing ESPNow() re-enables the radio and resets
        # tx_power to 20 dBm, so power must be set afterwards.
        if tx_power is not None:
            self.set_tx_power(tx_power)

        self.recoveries = 0

    def _open(self):
        try:
            self.e = self._espnow.ESPNow(buffer_size=self._buffer_size)
        except RuntimeError:
            # ESPNow is a singleton. A bare "RuntimeError: Already running"
            # is a genuinely baffling thing to hit from the REPL or after a
            # sample exited without cleaning up, so say what to do about it.
            raise RuntimeError(
                "ESP-NOW is already running -- something else holds the "
                "singleton. Call .deinit() on it, or reset the board."
            )
        self.bc = self._espnow.Peer(mac=BROADCAST_MAC, channel=self.channel)
        self.e.peers.append(self.bc)

    def set_tx_power(self, dbm):
        """Set transmit power; returns what the radio actually accepted.

        GOTCHA: valid range is 2.0-20.0 dBm and out-of-range writes are
        silently ignored -- no exception, value unchanged. Always use the
        returned value rather than assuming the write landed.
        """
        self._wifi.radio.tx_power = dbm
        return self._wifi.radio.tx_power

    @property
    def tx_power(self):
        return self._wifi.radio.tx_power

    def send(self, kind, body=b""):
        """Broadcast a framed message.

        GOTCHA: the peer argument is mandatory. Bare e.send(msg) raises
        IDFError 0x3069 even with a broadcast peer registered.

        There is no delivery feedback: broadcast frames are never ACKed,
        so send_success/send_failure both stay at 0 forever. Treat every
        send as fire-and-forget and make the protocol tolerate loss.
        """
        self.e.send(encode(kind, body), self.bc)

    def poll(self):
        """Drain the RX buffer -> [(mac, kind, body, rssi, t_ms), ...]."""
        out = []
        while self.e:
            try:
                p = self.e.read()
            except ValueError:
                # Open CircuitPython bug: the RX ring buffer is filled from
                # the WiFi task on another core and is not multicore-safe.
                # Once it garbles, read() raises forever until reopened.
                self._recover()
                break
            if p is None:
                break
            d = decode(p.msg)
            if d is None:
                continue                      # not ours; ignore quietly
            out.append((bytes(p.mac), d[0], d[1], p.rssi, p.time))
        return out

    def _recover(self):
        self.recoveries += 1
        try:
            self.e.deinit()
        except Exception:
            pass
        self._open()

    @property
    def dropped(self):
        """Ring-buffer overflows. Non-zero means the loop is too slow.

        The default buffer_size of 526 holds only two full packets, and a
        displayio refresh can easily stall the loop past that.
        """
        return self.e.read_failure

    def deinit(self):
        try:
            self.e.deinit()
        except Exception:
            pass


class SimRadio:
    """Fake transport that invents neighbours.

    ESP-NOW has no loopback, so a lone badge receives absolutely nothing.
    This stands in for the radio so a sample's peer table, aging, display
    and persistence can all be exercised and watched on one badge. Same
    interface as Radio, so a sample swaps between them on one line.
    """

    def __init__(self, handles=("ada", "grace", "ken", "bjarne", "linus"),
                 period=0.4, seed=None):
        import random
        self._random = random
        if seed is not None:
            random.seed(seed)
        self.mac = b"\x02SIMME"
        self.channel = CHANNEL
        self.recoveries = 0
        self.dropped = 0
        self.tx_power = 20.0
        self._period = period
        self._next = 0.0
        # Each fake badge gets its own RSSI random walk, so some drift out
        # of range and get aged out while others stay close.
        self._sims = []
        for i, h in enumerate(handles):
            self._sims.append({
                "mac": bytes((0x02, 0x00, 0x00, 0x00, 0x00, i + 1)),
                "handle": h,
                "rssi": -50.0 - i * 7,
                "vel": 0.0,
            })

    def set_tx_power(self, dbm):
        self.tx_power = dbm
        return self.tx_power

    def send(self, kind, body=b""):
        encode(kind, body)                    # still enforce the size cap
        return None

    def poll(self):
        now = time.monotonic()
        if now < self._next:
            return []
        self._next = now + self._period
        out = []
        for s in self._sims:
            # Momentum-biased walk: smoother and more lifelike than
            # independent jitter, and it actually crosses the ttl boundary.
            s["vel"] += self._random.uniform(-1.2, 1.2)
            s["vel"] = max(-3.0, min(3.0, s["vel"]))
            s["rssi"] = max(-95.0, min(-30.0, s["rssi"] + s["vel"]))
            if s["rssi"] < -90.0:
                continue                      # too far away to be heard
            body = s["handle"].encode()
            out.append((s["mac"], HELLO, body, int(s["rssi"]), int(now * 1000)))
        return out

    def deinit(self):
        pass
