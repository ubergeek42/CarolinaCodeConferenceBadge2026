"""
badgestats.py -- who you were near, kept in nvm
===============================================
First-party module (not part of the Adafruit bundle -- see lib/NOTICES.md).
`badgenet.PeerTable` knows who is nearby *now*; this remembers it across
resets and flat batteries, so at the end of a conference the badge can say
who you spent the day standing next to.

Where it lives, and why nvm rather than a file
----------------------------------------------
`microcontroller.nvm` is 8192 bytes and -- this is the deciding property --
it is writable **while the badge is plugged into USB**, which the filesystem
is not (`storage.remount()` refuses whenever the drive is visible to a
host). A log that only accumulated on battery would miss every minute spent
at a desk.

    nvm[0:64]     reserved for the Launcher, which stores its last pick at
                  byte 0 as a length-prefixed name. Not ours to touch.
    nvm[64:80]    header: magic, version, count, uptime tombstone, sessions
    nvm[80:8192]  338 records of 24 bytes

One write, once a minute
------------------------
Measured on this badge: an nvm write costs **~65 ms regardless of length** --
one byte and two kilobytes cost the same, because the cost is erasing the
page, not moving the bytes. So this keeps everything in RAM and serialises
the whole region into a single slice assignment at most once a minute. Doing
it per observation would have spent 65 ms of every second on bookkeeping.

What it cannot do
-----------------
There is no RTC on this board, so the badge has no idea what time it is.
Records carry a session number and an uptime, not a wall clock: "session 3,
1400 seconds in", not "14:20". `tools/badgedump.py` can convert the *current*
session to real times, because when you plug in it knows both the wall clock
and the badge's uptime; earlier sessions stay relative. Pretending otherwise
would mean inventing timestamps.

And the distances are qualitative. `badgenet.rssi_to_unit()`'s calibration
is still an unmeasured guess, so `best_rssi` is stored raw, in dBm, and
described as "closest" rather than converted into metres it cannot support.
"""

import gc
import time

MAGIC = b"CCST"
VERSION = 2

LAUNCHER_RESERVED = 64          # nvm[0:64] belongs to the Launcher
HEADER_AT = 64
HEADER_LEN = 16
RECORDS_AT = HEADER_AT + HEADER_LEN
RECORD_LEN = 48

# Handles and LinkedIn paths are truncated to fit the record. Widening the
# record from 24 to 48 bytes to hold a link drops capacity from 338 contacts to
# 169, which is still more people than anyone talks to at a conference -- and a
# name you can act on later beats twice as many you can't.
HANDLE_LEN = 12
LINK_LEN = 20     # 6 + 12 + 10 fixed + 20 = RECORD_LEN

# A gap longer than this makes the next sighting a separate encounter rather
# than a continuation, so "3 meets over 40 minutes" stays distinguishable
# from "one 40-minute conversation".
ENCOUNTER_GAP = 90.0

# Longest gap that still accrues shared time. Without a cap, a badge seen at
# 10:00 and again at 16:00 would be credited with six hours of company.
ACCRUE_CAP = 30.0

FLUSH_SECS = 60.0
MAX_SECS = 0xFFFF               # ~18 hours per contact, then it saturates


def _nvm():
    import microcontroller
    return microcontroller.nvm


def capacity(size=8192):
    return (size - RECORDS_AT) // RECORD_LEN


class Contact:
    __slots__ = ("mac", "handle", "link", "secs", "meets", "best_rssi",
                 "first_session", "last_session", "last_secs", "flags", "_seen")

    def __init__(self, mac, handle="", secs=0, meets=1, best_rssi=-127,
                 first_session=0, last_session=0, last_secs=0, flags=0,
                 link=""):
        self.mac = bytes(mac)
        self.handle = handle
        self.link = link
        self.secs = secs
        self.meets = meets
        self.best_rssi = best_rssi
        self.first_session = first_session
        self.last_session = last_session
        self.last_secs = last_secs
        self.flags = flags
        self._seen = None            # monotonic time of the last observation

    @property
    def label(self):
        return self.handle or "%02X%02X" % (self.mac[4], self.mac[5])

    def __repr__(self):
        return "<%s %ds %d meets best %ddBm>" % (
            self.label, self.secs, self.meets, self.best_rssi)


def _fixed(text, n):
    raw = text.encode()[:n] if isinstance(text, str) else bytes(text)[:n]
    return raw + bytes(n - len(raw))


def pack_contact(c):
    return (c.mac + _fixed(c.handle, HANDLE_LEN)
            + bytes((min(c.secs, MAX_SECS) >> 8, min(c.secs, MAX_SECS) & 0xFF,
                     min(c.meets, 255),
                     c.best_rssi if c.best_rssi >= 0 else c.best_rssi + 256,
                     c.first_session & 0xFF, c.last_session & 0xFF,
                     (c.last_secs >> 8) & 0xFF, c.last_secs & 0xFF,
                     c.flags, 0))
            + _fixed(c.link, LINK_LEN))


def unpack_contact(raw):
    """Contact, or None for an empty or nonsense slot."""
    if len(raw) < RECORD_LEN:
        return None
    mac = bytes(raw[0:6])
    if mac == b"\x00" * 6 or mac == b"\xff" * 6:
        return None                              # empty slot

    def text(at, n):
        try:
            return bytes(raw[at:at + n]).rstrip(b"\x00").decode()
        except Exception:
            return ""                            # garbled; keep the record

    base = 6 + HANDLE_LEN
    rssi = raw[base + 3] - 256 if raw[base + 3] > 127 else raw[base + 3]
    return Contact(mac, text(6, HANDLE_LEN),
                   secs=(raw[base] << 8) | raw[base + 1],
                   meets=raw[base + 2] or 1,
                   best_rssi=rssi,
                   first_session=raw[base + 4],
                   last_session=raw[base + 5],
                   last_secs=(raw[base + 6] << 8) | raw[base + 7],
                   flags=raw[base + 8],
                   link=text(base + 10, LINK_LEN))


class Stats:
    """The in-RAM view of the log, plus the one write that persists it."""

    def __init__(self, nvm=None, session_secs=None):
        self.nvm = _nvm() if nvm is None else nvm
        self.cap = capacity(len(self.nvm))
        self.contacts = {}
        self.session = 0
        self.tomb_secs = 0          # uptime at the last flush -- the tombstone
        self.tomb_label = 0         # which power configuration was being timed
        self.dirty = False
        self.writes = 0
        self.failed_writes = 0
        self.evicted = 0
        self.last_flush = None      # set on the first flush() call, not at boot
        self._loaded = False

    # -- durability -------------------------------------------------------
    def load(self):
        """Read nvm. A missing or wrong-version header starts fresh."""
        head = bytes(self.nvm[HEADER_AT:HEADER_AT + HEADER_LEN])
        if head[0:4] != MAGIC or head[4] != VERSION:
            self.reset()
            return False
        count = (head[6] << 8) | head[7]
        self.tomb_secs = (head[8] << 24) | (head[9] << 16) | (head[10] << 8) | head[11]
        self.session = ((head[12] << 8) | head[13]) & 0xFFFF
        self.tomb_label = head[14]
        self.contacts = {}
        for i in range(min(count, self.cap)):
            at = RECORDS_AT + i * RECORD_LEN
            c = unpack_contact(bytes(self.nvm[at:at + RECORD_LEN]))
            if c is not None:
                self.contacts[c.mac] = c
        self._loaded = True
        return True

    def reset(self):
        self.contacts = {}
        self.session = 0
        self.tomb_secs = 0
        self.tomb_label = 0
        self._loaded = True
        self.dirty = True

    def begin_session(self):
        """Bump the session counter. Call once at boot, after load()."""
        if not self._loaded:
            self.load()
        self.session = (self.session + 1) & 0xFFFF
        self.dirty = True
        return self.session

    def flush(self, now=None, force=False, allow=True):
        """Serialise everything into nvm in a single write. Returns True if written.

        `allow` is the "not right now" lever: the caller passes False while a
        transfer is streaming, because 65 ms of nvm erase in the middle of a
        carousel is several dropped frames.
        """
        now = time.monotonic() if now is None else now
        if not allow:
            return False
        if self.last_flush is None:
            # First call sets the clock rather than writing. `time.monotonic()`
            # counts from power-on, not from when this program started, so a
            # badge that has been sitting at the REPL for five minutes would
            # otherwise satisfy `now - 0.0 >= FLUSH_SECS` on its very first
            # loop pass and take a 65 ms nvm write during boot.
            self.last_flush = now
            if not force:
                return False
        if not force and (not self.dirty or now - self.last_flush < FLUSH_SECS):
            return False

        self.tomb_secs = int(now)
        ordered = self._ordered()
        count = len(ordered)
        head = bytearray(HEADER_LEN)
        head[0:4] = MAGIC
        head[4] = VERSION
        head[5] = 0
        head[6] = (count >> 8) & 0xFF
        head[7] = count & 0xFF
        head[8] = (self.tomb_secs >> 24) & 0xFF
        head[9] = (self.tomb_secs >> 16) & 0xFF
        head[10] = (self.tomb_secs >> 8) & 0xFF
        head[11] = self.tomb_secs & 0xFF
        head[12] = (self.session >> 8) & 0xFF
        head[13] = self.session & 0xFF
        head[14] = self.tomb_label
        head[15] = 0

        buf = bytearray(head)
        for c in ordered:
            buf += pack_contact(c)

        # One assignment. The 65 ms cost is per call, not per byte, so
        # splitting this into "just the dirty records" would be strictly
        # slower as well as more code.
        #
        # Both the collect and the try matter, and both were learned on
        # hardware. Writing a slice of nvm allocates a buffer the size of the
        # *whole* 8 KB region internally, whatever the slice length -- so on a
        # badge with the radio up and three images loaded, this is one of the
        # largest single allocations the program ever asks for, and it does
        # fail. When it failed unguarded it killed the main loop, and the badge
        # stopped responding to buttons. Losing a minute of the log is nothing;
        # losing the badge is not.
        gc.collect()
        try:
            self.nvm[HEADER_AT:HEADER_AT + len(buf)] = buf
        except (MemoryError, OSError) as ex:
            self.failed_writes += 1
            print("[badgestats] nvm write failed (%s: %s); keeping the log in "
                  "RAM and retrying next time" % (type(ex).__name__, ex))
            self.last_flush = now          # don't retry every single pass
            return False
        self.writes += 1
        self.dirty = False
        self.last_flush = now
        return True

    def _ordered(self):
        """Contacts to persist, most significant first, truncated to capacity.

        Sorted rather than dict-ordered on purpose: CircuitPython dicts are
        not insertion-ordered, so without this the record that gets dropped
        when the log fills would be arbitrary. Longest company wins.
        """
        out = list(self.contacts.values())
        out.sort(key=lambda c: -c.secs)
        if len(out) > self.cap:
            self.evicted += len(out) - self.cap
            out = out[:self.cap]
        return out

    # -- observing --------------------------------------------------------
    def observe(self, mac, rssi, now=None, handle="", link=""):
        """Record a sighting. Cheap: no nvm traffic, just arithmetic."""
        now = time.monotonic() if now is None else now
        mac = bytes(mac)
        c = self.contacts.get(mac)
        if c is None:
            c = Contact(mac, handle[:HANDLE_LEN], secs=0, meets=1,
                        best_rssi=int(rssi), first_session=self.session,
                        last_session=self.session, last_secs=int(now),
                        link=link[:LINK_LEN])
            c._seen = now
            self.contacts[mac] = c
            self.dirty = True
            return c

        gap = now - c._seen if c._seen is not None else None
        if gap is None or gap > ENCOUNTER_GAP:
            c.meets = min(c.meets + 1, 255)
        elif gap > 0:
            # Only credit time actually spent in each other's company, and
            # only up to ACCRUE_CAP per step, so a missed hour is not billed
            # as an hour together.
            c.secs = min(c.secs + int(min(gap, ACCRUE_CAP)), MAX_SECS)
        if int(rssi) > c.best_rssi:
            c.best_rssi = int(rssi)
        if handle and not c.handle:
            c.handle = handle[:HANDLE_LEN]
        if link and not c.link:
            # First one wins, like the handle: a later garbled frame must not
            # overwrite a link that already arrived intact.
            c.link = link[:LINK_LEN]
        c.last_session = self.session
        c.last_secs = int(now) & 0xFFFF
        c._seen = now
        self.dirty = True
        return c

    def observe_table(self, table, now=None):
        """Fold a whole `badgenet.PeerTable` in at once."""
        now = time.monotonic() if now is None else now
        for p in table.nearby(now):
            self.observe(p["mac"], p["rssi"], now=now, handle=p.get("handle", ""))

    # -- reading ----------------------------------------------------------
    def top(self, n=5):
        out = list(self.contacts.values())
        out.sort(key=lambda c: -c.secs)
        return out[:n]

    @property
    def total_secs(self):
        return sum(c.secs for c in self.contacts.values())

    def summary(self):
        """One short line for the badge's own screen."""
        n = len(self.contacts)
        if not n:
            return "no badges met yet"
        best = self.top(1)[0]
        return "%d met / %s %s" % (n, best.label, hms(best.secs))

    def report(self, limit=20):
        """Lines for a serial dump. `tools/badgedump.py` prints these."""
        lines = ["BADGESTATS v%d  session %d  uptime %s  %d contacts"
                 % (VERSION, self.session, hms(self.tomb_secs), len(self.contacts)),
                 "%-12s %8s %5s %8s %-11s %s"
                 % ("who", "together", "meets", "closest", "last seen", "linkedin")]
        for c in self.top(limit):
            lines.append("%-12s %8s %5d %6ddBm s%-3d+%-6s %s"
                         % (c.label[:12], hms(c.secs), c.meets, c.best_rssi,
                            c.last_session, hms(c.last_secs), c.link))
        if len(self.contacts) > limit:
            lines.append("... and %d more" % (len(self.contacts) - limit))
        return lines


def hms(secs):
    secs = int(secs)
    if secs < 60:
        return "%ds" % secs
    if secs < 3600:
        return "%dm%02ds" % (secs // 60, secs % 60)
    return "%dh%02dm" % (secs // 3600, (secs % 3600) // 60)


def read_tombstone(nvm=None):
    """Uptime at the last flush, in seconds, or None if nvm holds no log.

    This is how a battery-life run is read out: let the badge run until it
    dies, plug it in, and ask what the last thing it managed to write was.
    """
    nvm = _nvm() if nvm is None else nvm
    head = bytes(nvm[HEADER_AT:HEADER_AT + HEADER_LEN])
    if head[0:4] != MAGIC:
        return None
    return ((head[8] << 24) | (head[9] << 16) | (head[10] << 8) | head[11],
            ((head[12] << 8) | head[13]), head[14])
