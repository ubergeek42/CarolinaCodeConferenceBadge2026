"""
mkmod.py -- pack a module into the blob a badge broadcasts
=========================================================
Host-side tool. The badge's `zlib` is decompress-only, so a badge can never
build one of these; it only ever forwards bytes it was handed. This is where
they come from.

    python3 tools/mkmod.py mods/syncflash.py
    python3 tools/mkmod.py mods/*.py --install      # also copy to CIRCUITPY

A `.mod` file is *just the raw deflate stream* -- no container, no header.
Everything else a receiver needs (name, length, chunk count, CRC32) is
computed from the blob and travels in the OFFER frame, so there is no second
format to keep in sync with the protocol. The badge's own `.py` copy sits
beside it for autoloading; the `.mod` is what goes on the air.

It also lints the module, because a broken one is much cheaper to catch here
than after it has been pushed to a room full of badges.
"""
import glob
import os
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import badgexfer as bx           # noqa: E402
import badgemod                  # noqa: E402

CIRCUITPY = "/Volumes/CIRCUITPY"


def lint(path, source):
    """Complaints about a module, worst first. Empty means it is shippable."""
    problems = []
    glb = {"__name__": "lint"}
    try:
        exec(compile(source, path, "exec"), glb)
    except Exception as ex:
        return ["does not even import: %s: %s" % (type(ex).__name__, ex)]

    name = glb.get("NAME")
    if not name:
        problems.append("no NAME -- the runtime will refuse it")
    else:
        stem = os.path.basename(path)[:-3]
        if name != stem:
            problems.append("NAME is %r but the file is %r; autoload and the "
                            "air would disagree about what this is"
                            % (name, stem))
    if not callable(glb.get("tick")):
        problems.append("no tick(ctx, now) -- nothing would ever run")
    for hook in ("setup", "teardown"):
        fn = glb.get(hook)
        if fn is not None and not callable(fn):
            problems.append("%s is defined but not callable" % hook)
    if "while True" in source:
        problems.append("contains `while True` -- a module that does not "
                        "return is a module the watchdog has to kill")
    if "time.sleep" in source:
        problems.append("calls time.sleep -- that blocks the whole badge, "
                        "including its radio; use `now` instead")
    return problems


def build(path, install=False):
    source = open(path).read()
    name = os.path.basename(path)[:-3]

    problems = lint(path, source)
    for p in problems:
        print("  ! %s" % p)
    if any("does not even import" in p or "no tick" in p or "no NAME" in p
           for p in problems):
        print("  refusing to pack %s" % path)
        return False

    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    blob = co.compress(source.encode()) + co.flush()

    # Prove it inflates with exactly the call the badge will make. A blob that
    # only this machine can read would fail on a badge, silently, after a
    # perfectly successful-looking transfer.
    if bx.decode_blob(blob, bx.FLAG_DEFLATE) != source:
        print("  ! blob does not round-trip; not writing")
        return False

    if len(blob) > bx.MAX_BLOB:
        print("  ! %d B compressed is over the %d B cap (%d chunks max)"
              % (len(blob), bx.MAX_BLOB, bx.MAX_CHUNKS))
        return False

    out = path[:-3] + ".mod"
    with open(out, "wb") as f:
        f.write(blob)

    offer = bx.build_offer(name, blob, flags=bx.FLAG_DEFLATE)
    chunks = offer.chunks
    print("  %-14s %5d B source -> %5d B blob (%.2f), %2d chunks, "
          "%.2f s per lap, mod_id 0x%04X, crc %08x"
          % (name, len(source), len(blob), len(blob) / len(source), chunks,
             (chunks + 1) * bx.PACE, badgemod.mod_id_for(name), offer.crc))

    if install:
        dest = os.path.join(CIRCUITPY, "mods")
        if not os.path.isdir(CIRCUITPY):
            print("  ! %s is not mounted; skipped install" % CIRCUITPY)
        else:
            os.makedirs(dest, exist_ok=True)
            for f in (path, out):
                # Bytes only, deliberately not shutil.copy: copying metadata
                # onto a FAT volume makes macOS write an AppleDouble sidecar
                # (`._syncflash.py`), which ends in .py and which autoload
                # would then try to compile. Write the content and nothing else.
                target = os.path.join(dest, os.path.basename(f))
                with open(f, "rb") as src, open(target, "wb") as dst:
                    dst.write(src.read())
                    dst.flush()
                    os.fsync(dst.fileno())
                # macOS attaches a provenance xattr to files it creates, and
                # FAT has nowhere to put one, so it lands as a 4 KB
                # `._name` sidecar next to every single copy. Sweep it.
                sidecar = os.path.join(dest, "._" + os.path.basename(f))
                if os.path.exists(sidecar):
                    os.remove(sidecar)
            if hasattr(os, "sync"):
                os.sync()            # the volume is mounted async
            print("  installed to %s" % dest)
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    install = "--install" in sys.argv
    paths = []
    for a in args:
        paths.extend(sorted(glob.glob(a)) or [a])
    if not paths:
        print(__doc__)
        return 2
    failed = 0
    for path in paths:
        if not path.endswith(".py"):
            continue
        print(path)
        if not build(path, install=install):
            failed += 1
    return 1 if failed else 0


sys.exit(main())
