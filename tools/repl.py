"""
repl.py -- run a snippet or a file on the badge and print what it says
======================================================================
Host-side tool, not badge code. Iterating on badge behaviour without this
is guesswork: every measured number in the badge-to-badge design came out
of it.

    python3 tools/repl.py probe.py            # run a file on the badge
    python3 tools/repl.py -c "import gc; print(gc.mem_free())"
    python3 tools/repl.py probe.py --fresh    # soft-reset first
    python3 tools/repl.py probe.py --reload    # hand the badge back to code.py
    python3 tools/repl.py slow.py --secs=90 --idle=15   # something genuinely slow

Needs pyserial. If you'd rather not install it:

    uv run --with pyserial python tools/repl.py ...

Three details make this work where a naive `cat > /dev/cu.usbmodem*` does
not, and all three cost real debugging time to find:

  * DTR must be asserted or the port opens and stays mute -- the ESP32-S3's
    native USB needs it (see docs/SERIAL_CONSOLE.md).
  * Multi-line code must go through paste mode (Ctrl-E .. Ctrl-D). Typed
    line by line, the REPL auto-indents and every block comes out mangled.
  * Reads need an idle timeout, not a fixed one. Badge code goes silent for
    seconds at a stretch (a paced radio burst, a battery of refreshes), so
    "stop when it stops talking" has to mean several seconds of quiet.
"""
import glob
import os
import sys
import time

import serial

PORT = os.environ.get("PORT") or sorted(glob.glob("/dev/cu.usbmodem*"))[0]


def opt(name, default):
    """Value of --name=N, or default."""
    for a in sys.argv[1:]:
        if a.startswith("--%s=" % name):
            return float(a.split("=", 1)[1])
    return default


# Both bounded, and bounded tightly. A snippet that has gone quiet for three
# seconds is finished or wedged, and either way there is nothing to gain by
# waiting: the badge either answers immediately or needs a reset. Raise these
# with --secs= / --idle= for something genuinely slow, like a paced radio burst.
SECS = opt("secs", 25.0)
IDLE = opt("idle", 3.0)


def drain(p, secs, idle=IDLE):
    """Read until `idle` seconds of silence, or `secs` overall."""
    out = []
    deadline = time.time() + secs
    quiet = time.time() + idle
    while time.time() < deadline and time.time() < quiet:
        d = p.read(8192)
        if d:
            out.append(d.decode(errors="replace"))
            quiet = time.time() + idle
    return "".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args and args[0] == "-c":
        src = args[1]
    else:
        src = open(args[0]).read()

    p = serial.Serial(PORT, 115200, timeout=0.2)
    p.dtr = True
    time.sleep(0.4)
    p.write(b"\x03")              # Ctrl-C: interrupt whatever code.py is doing
    time.sleep(0.4)
    p.write(b"\x03")
    drain(p, 0.6)

    if "--fresh" in sys.argv:
        # Soft reset so the heap, the pins and the radio start from a known
        # state, then break back in before code.py gets far. Worth doing
        # whenever a previous run left hardware claimed.
        p.write(b"\x04")
        time.sleep(0.3)
        p.write(b"\x03")
        time.sleep(0.5)
        p.write(b"\x03")
        drain(p, 1.0, idle=0.6)

    p.write(b"\x05")              # Ctrl-E: paste mode
    time.sleep(0.2)
    drain(p, 0.3)
    p.write(src.replace("\n", "\r\n").encode())
    p.write(b"\x04")              # Ctrl-D: run the pasted block
    print(drain(p, SECS), end="")

    if "--reload" in sys.argv:
        p.write(b"\x04")
        drain(p, 2)
    p.close()


main()
