"""
test_badgestats.py -- self-test for the nvm proximity log
========================================================
Runs unmodified on CPython and on the badge:

    python3 samples/ProfileCard/test_badgestats.py
    exec(open("/test_badgestats.py").read())     # from the badge REPL

Everything runs against a `bytearray` standing in for nvm, so the real 8 KB
is never touched and no test can scribble on the Launcher's saved pick at
nvm[0:64]. That substitution is the only thing that makes this testable at
all -- a real nvm write costs 65 ms, so a few hundred assertions against
hardware would take half a minute and wear the flash for no reason.
"""

import sys

sys.path.append("lib")
sys.path.append("/lib")

import badgestats as bs


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


def fake_nvm(size=8192):
    return bytearray(size)


# ------------------------------------------------------------------
# layout
# ------------------------------------------------------------------
print("layout")
eq(bs.capacity(8192), 169, "169 contacts fit in 8 KB at 48 bytes each")
ok(bs.RECORDS_AT >= bs.LAUNCHER_RESERVED,
   "records start past the Launcher's reserved bytes")
eq(bs.HEADER_AT, 64, "header sits right after the reserved region")


# ------------------------------------------------------------------
# record codec
# ------------------------------------------------------------------
print("record codec")
c = bs.Contact(b"\x02\x00\x00\x00\xab\xcd", "ada", secs=3600, meets=4,
               best_rssi=-42, first_session=1, last_session=3, last_secs=1234,
               link="in/ada")
raw = bs.pack_contact(c)
eq(len(raw), bs.RECORD_LEN, "a record is exactly RECORD_LEN")
back = bs.unpack_contact(raw)
eq(back.mac, c.mac, "mac survives")
eq(back.handle, "ada", "handle survives")
eq(back.link, "in/ada", "and so does the link")
eq(back.secs, 3600, "seconds survive")
eq(back.meets, 4, "meets survive")
eq(back.best_rssi, -42, "negative rssi survives the round trip")
eq(back.last_session, 3, "session survives")
eq(back.last_secs, 1234, "uptime survives")

# Saturation rather than wraparound: an 18-hour contact must not read as 3s.
big = bs.Contact(b"\x01" * 6, "x" * 20, secs=999999, meets=999)
back = bs.unpack_contact(bs.pack_contact(big))
eq(back.secs, bs.MAX_SECS, "seconds saturate instead of wrapping")
eq(back.meets, 255, "meets saturate too")
eq(len(back.handle), bs.HANDLE_LEN, "an over-long handle is truncated, not refused")

eq(bs.unpack_contact(bytes(bs.RECORD_LEN)), None, "an all-zero slot is empty")
eq(bs.unpack_contact(b"\xff" * bs.RECORD_LEN), None, "an erased slot is empty")
eq(bs.unpack_contact(b"\x00" * 5), None, "a short slot is not a record")

eq(bs.unpack_contact(bs.pack_contact(bs.Contact(b"\x02\x00\x00\x00\x00\x09"))).label,
   "0009", "no handle falls back to the last two MAC octets")


# ------------------------------------------------------------------
# accrual: time together, and what must not be counted
# ------------------------------------------------------------------
print("accrual")
st = bs.Stats(nvm=fake_nvm())
st.reset()
MAC = b"\x02\x00\x00\x00\x00\x01"
st.observe(MAC, -50, now=0.0, handle="ada")
eq(len(st.contacts), 1, "first sighting creates a contact")
eq(st.contacts[MAC].secs, 0, "with no time together yet")
eq(st.contacts[MAC].meets, 1, "and one meeting")

for t in range(1, 31):
    st.observe(MAC, -50, now=float(t))
eq(st.contacts[MAC].secs, 30, "30 s of one-second sightings is 30 s together")
eq(st.contacts[MAC].meets, 1, "still one continuous meeting")

# A long silence is a new encounter, and the gap is NOT company.
st.observe(MAC, -50, now=30.0 + bs.ENCOUNTER_GAP + 10)
eq(st.contacts[MAC].secs, 30, "the gap added no shared time")
eq(st.contacts[MAC].meets, 2, "and counted as a second meeting")

# A moderate gap accrues, but only up to the cap.
st2 = bs.Stats(nvm=fake_nvm())
st2.reset()
st2.observe(MAC, -50, now=0.0)
st2.observe(MAC, -50, now=60.0)         # inside ENCOUNTER_GAP, over ACCRUE_CAP
eq(st2.contacts[MAC].secs, int(bs.ACCRUE_CAP),
   "a 60 s gap credits at most ACCRUE_CAP seconds")
eq(st2.contacts[MAC].meets, 1, "and is still the same meeting")

# Closest approach is a maximum, not the latest reading.
st2.observe(MAC, -30, now=61.0)
st2.observe(MAC, -80, now=62.0)
eq(st2.contacts[MAC].best_rssi, -30, "best_rssi keeps the closest approach")

# A handle learned later fills in a blank, but does not overwrite a known one.
st2.observe(MAC, -50, now=63.0, handle="grace", link="in/grace")
eq(st2.contacts[MAC].handle, "grace", "a late handle is adopted")
eq(st2.contacts[MAC].link, "in/grace", "and a late link too")
st2.observe(MAC, -50, now=64.0, handle="other", link="in/other")
eq(st2.contacts[MAC].handle, "grace", "but not replaced once known")
eq(st2.contacts[MAC].link, "in/grace", "and neither is the link")


# ------------------------------------------------------------------
# persistence: one write, and it survives a reboot
# ------------------------------------------------------------------
print("persistence")
nvm = fake_nvm()
st3 = bs.Stats(nvm=nvm)
st3.reset()
st3.begin_session()
eq(st3.session, 1, "first session is 1")
for i in range(5):
    mac = bytes((2, 0, 0, 0, 0, i + 1))
    st3.observe(mac, -40 - i, now=0.0, handle="p%d" % i)
    for t in range(1, 10 + i):
        st3.observe(mac, -40 - i, now=float(t))

eq(st3.flush(now=100.0, allow=False), False, "flush can be vetoed mid-transfer")
eq(st3.writes, 0, "and really did not write")
eq(st3.flush(now=100.0, force=True), True, "a forced flush writes")
eq(st3.writes, 1, "exactly one write for the whole log")
ok(nvm[0:64] == bytearray(64), "the Launcher's region was left alone")

reloaded = bs.Stats(nvm=nvm)
eq(reloaded.load(), True, "the header is recognised after a reboot")
eq(reloaded.session, 1, "session survives")
eq(reloaded.tomb_secs, 100, "the tombstone recorded the uptime")
eq(len(reloaded.contacts), 5, "all five contacts came back")
eq(reloaded.top(1)[0].handle, "p4", "and the longest company is still on top")
eq(reloaded.begin_session(), 2, "the next boot is session 2")

# Rate limiting: the expensive write happens once a minute, not once a loop.
st3.dirty = True
eq(st3.flush(now=101.0), False, "a second flush a second later is refused")
eq(st3.flush(now=100.0 + bs.FLUSH_SECS + 1), True, "a minute later it goes")

# Garbage nvm must not be read as data.
junk = fake_nvm()
for i in range(len(junk)):
    junk[i] = (i * 7) & 0xFF
st4 = bs.Stats(nvm=junk)
eq(st4.load(), False, "nvm without our magic is not our log")
eq(len(st4.contacts), 0, "and nothing was invented from it")

# A wrong version is also a fresh start rather than a misparse.
nvm2 = fake_nvm()
st5 = bs.Stats(nvm=nvm2)
st5.reset()
st5.observe(MAC, -50, now=0.0)
st5.flush(now=1.0, force=True)
nvm2[bs.HEADER_AT + 4] = 99
eq(bs.Stats(nvm=nvm2).load(), False, "a future version is refused, not guessed at")


# ------------------------------------------------------------------
# eviction: the log fills up at a conference, and must fill sensibly
# ------------------------------------------------------------------
print("eviction")
small_nvm = fake_nvm(bs.RECORDS_AT + bs.RECORD_LEN * 3)     # room for three
st6 = bs.Stats(nvm=small_nvm)
st6.reset()
eq(st6.cap, 3, "capacity follows the nvm size")
for i in range(6):
    mac = bytes((2, 0, 0, 0, 0, i + 1))
    st6.observe(mac, -50, now=0.0, handle="c%d" % i)
    # Later contacts get progressively more time together.
    for t in range(1, 2 + i * 3):
        st6.observe(mac, -50, now=float(t))
st6.flush(now=10.0, force=True)
kept = bs.Stats(nvm=small_nvm)
kept.load()
eq(len(kept.contacts), 3, "only three records were persisted")
eq(sorted(c.handle for c in kept.contacts.values()), ["c3", "c4", "c5"],
   "and they are the three you spent longest with, not an arbitrary three")
ok(st6.evicted > 0, "eviction was counted rather than silent")


# ------------------------------------------------------------------
# tombstone: reading a dead battery's last words
# ------------------------------------------------------------------
print("tombstone")
nvm3 = fake_nvm()
st7 = bs.Stats(nvm=nvm3)
st7.reset()
st7.begin_session()
st7.tomb_label = 3
st7.flush(now=27000.0, force=True)          # 7.5 hours in, then the cell died
got = bs.read_tombstone(nvm3)
eq(got[0], 27000, "the last uptime is readable without parsing the whole log")
eq(got[1], 1, "along with the session")
eq(got[2], 3, "and which power configuration was being timed")
eq(bs.read_tombstone(fake_nvm()), None, "blank nvm has no tombstone")
eq(bs.hms(27000), "7h30m", "and it formats for humans")
eq(bs.hms(45), "45s", "seconds")
eq(bs.hms(605), "10m05s", "minutes")


# ------------------------------------------------------------------
# reporting
# ------------------------------------------------------------------
print("reporting")
lines = reloaded.report(limit=2)
ok(lines[0].startswith("BADGESTATS"), "report has a header")
eq(len(lines), 2 + 2 + 1, "header, columns, two rows, and an 'and N more'")
ok("more" in lines[-1], "the truncation is stated, not silent")
ok("met" in bs.Stats(nvm=fake_nvm()).summary() or
   "no badges" in bs.Stats(nvm=fake_nvm()).summary(), "empty summary is sane")
ok(reloaded.summary().startswith("5 met"), "summary counts contacts")


# ------------------------------------------------------------------
# the CARD wire form -- handle plus an optional link
# ------------------------------------------------------------------
print("card codec")
import badgenet as bn

eq(bn.unpack_card(bn.pack_card("ada", "in/ada")), ("ada", "in/ada"),
   "handle and link round-trip")
eq(bn.unpack_card(bn.pack_card("ada")), ("ada", ""), "a link is optional")
eq(bn.unpack_card(b""), ("", ""), "an empty body is not an error")
eq(bn.unpack_card(b"\xff\xfe"), ("", ""),
   "undecodable bytes give empties rather than raising")
ok(len(bn.pack_card("x" * 300, "y" * 300)) <= bn.MAX_BODY,
   "an absurd card is truncated to one frame, not silently oversized")
ok(bn.CARD != bn.HELLO,
   "CARD is its own kind, so badges that predate it just ignore it")


print("PASSED %d, FAILED %d" % (PASS, FAIL))
