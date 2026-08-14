"""
badgedump.py -- read the badge's proximity log over USB
=======================================================
Host-side tool. Prints who the badge has been near, for how long, and how
close it ever got.

    python3 tools/badgedump.py
    python3 tools/badgedump.py --tombstone     # just the battery-life figure
    python3 tools/badgedump.py --csv > day.csv

Needs pyserial (or run it with `uv run --with pyserial python ...`).

The badge does the formatting, not this script: it already has the record
codec in lib/badgestats.py, and asking it to print its own report means
there is exactly one implementation of the layout instead of two that can
disagree. This drives the REPL and relays what comes back.

Two things worth knowing about the numbers:

  * The badge has no RTC, so it cannot know what time of day anything
    happened. Records carry a session number and an uptime within it. For the
    *current* session this script can convert to wall-clock times, because it
    knows both the clock and the badge's uptime; earlier sessions stay
    relative, and are shown that way rather than being invented.
  * "Closest" is a raw dBm reading, not a distance. RSSI correlates with
    distance on a good day and moves several dB when someone walks between
    two badges. It is not converted to metres because it cannot be.

Reading is harmless -- nothing is written and nothing is cleared. Interrupting
the running code.py to get at the REPL does stop the badge until you reset it
or press Ctrl-D, and the script does that for you on the way out.
"""
import datetime
import glob
import os
import sys
import time

import serial

PORT = os.environ.get("PORT") or sorted(glob.glob("/dev/cu.usbmodem*"))[0]

# Run on the badge. Two conventions in here matter:
#
#   * one statement per line, and no blank lines inside a block -- paste mode
#     is line based and a blank line ends the block;
#   * every line of real output is prefixed with MARK, because paste mode
#     echoes the source back before running it. Grepping the stream for
#     "NO_LOG" matched the echo of `print('NO_LOG')` and reported an empty log
#     on a badge that had one. The prefix is only ever at the start of a line
#     in genuine output; in the echo it is inside a string, after whitespace.
MARK = "|"
REPORT = """
import sys, time
sys.path.append('/lib')
import badgestats as bs
st = bs.Stats()
loaded = st.load()
M = chr(124)
print(M + 'UPTIME', time.monotonic())
if not loaded:
    print(M + 'NOLOG')
else:
    for line in st.report(limit=%d):
        print(M + 'R ' + line)
    for c in st.top(9999):
        print(M + 'C ' + '%%s,%%s,%%d,%%d,%%d,%%d,%%d,%%s' %% (
            ''.join('%%02x' %% b for b in c.mac), c.handle, c.secs, c.meets,
            c.best_rssi, c.last_session, c.last_secs, c.link))
"""

TOMBSTONE = """
import sys, time
sys.path.append('/lib')
import badgestats as bs
M = chr(124)
got = bs.read_tombstone()
print(M + 'UPTIME', time.monotonic())
if got is None:
    print(M + 'NOLOG')
else:
    print(M + 'TOMB %d %d %d %s' % (got[0], got[1], got[2], bs.hms(got[0])))
"""


def run(src, secs=20.0, idle=3.0):
    p = serial.Serial(PORT, 115200, timeout=0.2)
    p.dtr = True
    time.sleep(0.4)
    p.write(b"\x03")
    time.sleep(0.4)
    p.write(b"\x03")
    time.sleep(0.3)
    p.read(8192)

    p.write(b"\x05")                      # paste mode keeps the indentation
    time.sleep(0.2)
    p.read(8192)
    p.write(src.replace("\n", "\r\n").encode())
    p.write(b"\x04")

    out = []
    deadline = time.time() + secs
    quiet = time.time() + idle
    while time.time() < deadline and time.time() < quiet:
        d = p.read(8192)
        if d:
            out.append(d.decode(errors="replace"))
            quiet = time.time() + idle
    # Hand the badge back to code.py rather than leaving it sitting in the
    # REPL, where it stops being a badge.
    p.write(b"\x04")
    time.sleep(0.5)
    p.close()
    return "".join(out)


def marked(text):
    """Only the badge's real output: lines that start with MARK."""
    return [ln[len(MARK):].rstrip() for ln in text.splitlines()
            if ln.startswith(MARK)]


def main():
    limit = 40
    if "--tombstone" in sys.argv:
        text = run(TOMBSTONE)
        for line in marked(text):
            if line.startswith("TOMB"):
                _, secs, session, label, pretty = line.split(None, 4)
                print("last recorded uptime: %s (%s s), session %s, "
                      "power config %s" % (pretty, secs, session, label))
                return 0
            if line == "NOLOG":
                print("no log in nvm yet -- run the badge with STATS = True")
                return 1
        print("no answer from the badge; is something else holding the port?")
        print(text[-400:])
        return 1

    text = run(REPORT % limit)
    lines = marked(text)
    if any(ln == "NOLOG" for ln in lines):
        print("no log in nvm yet -- run the badge with STATS = True")
        return 1

    uptime = None
    for ln in lines:
        if ln.startswith("UPTIME"):
            try:
                uptime = float(ln.split()[1])
            except (IndexError, ValueError):
                pass

    if "--csv" in sys.argv:
        print("mac,handle,seconds_together,meets,best_rssi,last_session,"
              "last_uptime,linkedin")
        for ln in lines:
            if ln.startswith("C "):
                print(ln[2:])
        return 0

    rows = [ln[2:] for ln in lines if ln.startswith("R ")]
    if not rows:
        print("nothing came back. Raw tail:")
        print(text[-400:])
        return 1
    for r in rows:
        print(r)
    if uptime is not None:
        # Only the current session can be pinned to real times, and only
        # because we know the wall clock and the badge's uptime right now.
        started = datetime.datetime.now() - datetime.timedelta(seconds=uptime)
        print("\nthis session started about %s (badge uptime %.0f s)"
              % (started.strftime("%H:%M"), uptime))
        print("earlier sessions have no wall clock -- the badge has no RTC, so "
              "their times are uptimes within that session")
    return 0


sys.exit(main())
