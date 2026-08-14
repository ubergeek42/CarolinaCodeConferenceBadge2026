#!/usr/bin/env python3
"""
flash.py -- turn a stock CCC 2026 badge into your ProfileCard, in one command.

    python3 flash.py --github ubergeek42 --linkedin in/ubergeek42

Fetches your GitHub avatar, generates QR codes for your links, converts
everything to the 128x128 8-bit indexed BMP the badge can actually load,
and copies the whole payload -- images, libraries, code -- to a plugged-in
badge. Nothing in this repo is modified; generated assets go to a staging
directory so your own img/ stays yours.

Requires ImageMagick (`brew install imagemagick`) to decode the avatar.
Everything else is the Python standard library plus the vendored, MIT
licensed tools/adafruit_miniqr.py.
"""

import argparse
import glob
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))

SIZE = 128                      # every badge image is 128x128
CAPTION_MAX = 20                # 6 px glyphs in a 128 px panel, minus margin

# The minimum set of libraries ProfileCard actually imports. A stock badge
# already ships every one of these, so normally none get copied -- they are
# listed as a safety net for a badge whose lib/ has been wiped or replaced.
# Anything already present with identical bytes is skipped.
#
# ProfileCard draws its captions with terminalio.FONT, which is frozen into the
# firmware, so adafruit_bitmap_font is deliberately absent.
LIB_FILES = (
    "neopixel.mpy",
    "adafruit_pixelbuf.mpy",                       # required by neopixel
    "adafruit_st7735r.mpy",
    "adafruit_display_text/__init__.mpy",
    "adafruit_display_text/label.mpy",
    "adafruit_imageload/__init__.mpy",
    "adafruit_imageload/displayio_types.mpy",
    "adafruit_imageload/bmp/__init__.mpy",
    "adafruit_imageload/bmp/indexed.mpy",
    "adafruit_imageload/bmp/truecolor.mpy",
    "adafruit_imageload/bmp/negative_height_check.mpy",
)


# ------------------------------------------------------------------
# Finding the badge
# ------------------------------------------------------------------
def find_badge(explicit=None):
    """Locate a mounted CircuitPython drive.

    Gates on boot_out.txt rather than the volume name: the name is
    user-changeable, and the file tells us what we actually found.
    """
    if explicit:
        candidates = [explicit]
    elif sys.platform == "darwin":
        candidates = glob.glob("/Volumes/*")
    elif sys.platform.startswith("linux"):
        user = os.environ.get("USER", "*")
        candidates = (glob.glob("/media/%s/*" % user)
                      + glob.glob("/run/media/%s/*" % user)
                      + glob.glob("/media/*"))
    elif sys.platform == "win32":
        candidates = ["%s:\\" % chr(c) for c in range(ord("A"), ord("Z") + 1)]
    else:
        candidates = []

    found = []
    for path in candidates:
        boot_out = os.path.join(path, "boot_out.txt")
        if os.path.isfile(boot_out):
            try:
                with open(boot_out) as f:
                    found.append((path, f.readline().strip()))
            except OSError:
                continue
    return found


# ------------------------------------------------------------------
# Images
# ------------------------------------------------------------------
def write_bmp8(path, pixels, palette):
    """Write a 128x128 8-bit indexed uncompressed BMP.

    This is the one format adafruit_imageload reliably reads on this badge,
    and writing it by hand is deliberate: ImageMagick's BMP encoder picks its
    bit depth from the colour count and no flag overrides it, so a two-colour
    QR comes out 1-bit and a flat photo can come out 4-bit. Both fail to load.

    pixels is 128*128 palette indices, top row first.
    """
    if len(pixels) != SIZE * SIZE:
        raise ValueError("expected %d pixels, got %d" % (SIZE * SIZE, len(pixels)))

    pal = bytearray()
    for i in range(256):
        r, g, b = palette[i] if i < len(palette) else (0, 0, 0)
        pal += bytes((b, g, r, 0))                 # BMP palettes are BGRA

    rows = bytearray()
    for y in range(SIZE - 1, -1, -1):              # BMP rows are bottom-up
        rows += bytes(pixels[y * SIZE:(y + 1) * SIZE])

    size = 14 + 40 + len(pal) + len(rows)
    hdr = b"BM" + struct.pack("<IHHI", size, 0, 0, 14 + 40 + len(pal))
    hdr += struct.pack("<IiiHHIIiiII", 40, SIZE, SIZE, 1, 8, 0,
                       len(rows), 3780, 3780, 256, 256)
    with open(path, "wb") as f:
        f.write(hdr + pal + rows)


def fetch_avatar(user):
    """Download a GitHub avatar as raw bytes.

    The .png in the URL is a lie -- GitHub serves whatever was uploaded, and
    most avatars come back JPEG. We never look at the bytes ourselves, so it
    doesn't matter; ImageMagick decodes whatever it is. The 404 on this URL
    is also our username validation, so use this form rather than the
    avatars.githubusercontent.com one, which happily returns an identicon for
    a username that doesn't exist.

    size= is a cap, not a resize: GitHub never upscales, so a small avatar
    comes back small and we resize locally regardless.
    """
    url = "https://github.com/%s.png?size=256" % user
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SystemExit("no such GitHub user: %s" % user)
        raise SystemExit("fetching %s failed: %s" % (url, e))
    except urllib.error.URLError as e:
        raise SystemExit("fetching %s failed: %s" % (url, e.reason))


def avatar_to_bmp(data, path, keep_color=False):
    """Decode with ImageMagick, write the BMP ourselves.

    magick only ever hands us raw pixels on stdout; the file on disk is
    written by write_bmp8 so the format is exactly what the badge wants.
    """
    if shutil.which("magick") is None:
        raise SystemExit(
            "ImageMagick not found -- install it with `brew install imagemagick`\n"
            "(it decodes the avatar; everything else here is stdlib)")

    argv = ["magick", "-", "-resize", "%dx%d^" % (SIZE, SIZE),
            "-gravity", "center", "-extent", "%dx%d" % (SIZE, SIZE)]
    if not keep_color:
        # Tuned for a small, low-contrast panel: normalize spreads the
        # histogram, the level clips the muddy ends, unsharp puts edges back
        # after the downscale.
        argv += ["-colorspace", "Gray", "-normalize",
                 "-level", "12%,90%", "-unsharp", "0x1+0.7+0"]
    else:
        argv += ["-colorspace", "Gray"]            # colour needs a palette pass
    argv += ["-depth", "8", "GRAY:-"]

    p = subprocess.run(argv, input=data, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise SystemExit("magick failed: %s" % p.stderr.decode(errors="replace").strip())
    gray = p.stdout
    if len(gray) != SIZE * SIZE:
        raise SystemExit("magick returned %d bytes, expected %d"
                         % (len(gray), SIZE * SIZE))

    write_bmp8(path, gray, [(i, i, i) for i in range(256)])


def qr_to_bmp(url, path):
    """Render a QR for url into a 128x128 BMP. Returns (modules, px_per_module).

    Generated here rather than on the badge so the badge needs no QR library.
    """
    import adafruit_miniqr

    # Pick the QR version ourselves. miniqr's own auto-select compares the
    # payload against the block capacity in bytes and forgets the 12-bit
    # mode-and-length header, so a URL that lands exactly on a version
    # boundary (54 characters, say) overflows with a traceback instead of
    # stepping up a version. Byte mode on versions 1-9 always spends 4 bits on
    # the mode and 8 on the length, hence the + 12.
    payload = url.encode()
    need_bits = 12 + len(payload) * 8
    qr_type = None
    for t in range(1, 10):
        capacity = sum(b["data"] for b in adafruit_miniqr._get_rs_blocks(
            t, adafruit_miniqr.L))
        if capacity * 8 >= need_bits:
            qr_type = t
            break
    if qr_type is None:
        raise SystemExit("URL too long to encode (%d chars): %s\nShorten the link."
                         % (len(url), url))

    qr = adafruit_miniqr.QRCode(qr_type=qr_type, error_correct=adafruit_miniqr.L)
    qr.add_data(payload)
    qr.make()
    n = qr.matrix.width

    # Widest module size that still leaves the 4 modules of quiet zone the
    # spec asks for. Getting this wrong is the difference between a code that
    # scans across a room and one that never scans at all.
    fits = [s for s in range(1, 9) if (n + 8) * s <= SIZE]
    if not fits:
        raise SystemExit(
            "URL too long for a 128 px code (%d modules): %s\nShorten the link."
            % (n, url))
    scale = max(fits)
    pad = (SIZE - n * scale) // 2

    pixels = bytearray(SIZE * SIZE)
    for y in range(SIZE):
        my = (y - pad) // scale
        for x in range(SIZE):
            mx = (x - pad) // scale
            if 0 <= mx < n and 0 <= my < n and qr.matrix[my, mx]:
                pixels[y * SIZE + x] = 1

    write_bmp8(path, pixels, [(255, 255, 255), (0, 0, 0)])
    return n, scale


# ------------------------------------------------------------------
# Profile
# ------------------------------------------------------------------
def normalize_linkedin(value):
    """Accept 'foo', 'in/foo' or a full URL -> (url, caption)."""
    v = value.strip().rstrip("/")
    for prefix in ("https://", "http://", "www.", "linkedin.com/", "www.linkedin.com/"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    v = v.lstrip("/")
    if v.startswith("linkedin.com/"):
        v = v[len("linkedin.com/"):]
    if not v.startswith("in/"):
        v = "in/" + v
    return "https://linkedin.com/%s" % v, v


def caption(text, what):
    if len(text) > CAPTION_MAX:
        print("  ! %s caption %r is %d chars, trimming to %d (it would run off "
              "the panel)" % (what, text, len(text), CAPTION_MAX))
        return text[:CAPTION_MAX]
    return text


def profile_source(sides):
    lines = [
        '"""badge_profile.py -- written by flash.py. Edit freely; it is just data.',
        "",
        "ProfileCard imports SIDES from here when present, so re-running flash.py",
        "never rewrites your code.py.",
        '"""',
        "",
        "SIDES = (",
    ]
    for style, img, l1, l2, accent in sides:
        lines.append('    ("%s", "%s", "%s", "%s", 0x%06X),'
                     % (style, img, l1, l2, accent))
    lines.append(")")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------
# Copying
# ------------------------------------------------------------------
def copy_to(src, dest):
    """Copy one file if it differs. Returns bytes written, or None if skipped.

    Skipping identical files matters more than it looks: a stock badge already
    carries every library in LIB_FILES, so on the normal path this reduces the
    job to your photo, your QR codes and two small text files. It also makes
    re-running flash.py a no-op instead of 78 KB of pointless flash wear.

    The fsync is not optional -- the badge volume is mounted async, so without
    it a file can still be sitting in the host's cache when someone unplugs.
    """
    with open(src, "rb") as f:
        data = f.read()
    if os.path.isfile(dest):
        with open(dest, "rb") as f:
            if f.read() == data:
                return None
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return len(data)


# ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Provision a CCC 2026 badge as your ProfileCard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n"
               "  python3 flash.py --github ubergeek42 --linkedin in/ubergeek42")
    ap.add_argument("--github", required=True, metavar="USER",
                    help="GitHub username: avatar source and QR target")
    ap.add_argument("--linkedin", metavar="NAME",
                    help="LinkedIn vanity name, 'in/name' or full URL")
    ap.add_argument("--handle", metavar="TEXT",
                    help="text under your photo (default: GitHub username)")
    ap.add_argument("--repo", action="store_true",
                    help="add a side linking to this badge's source repo")
    ap.add_argument("--color", action="store_true",
                    help="keep the avatar in colour (default: grayscale)")
    ap.add_argument("--drive", metavar="PATH",
                    help="badge mount point (default: autodetect)")
    ap.add_argument("--stage", metavar="DIR",
                    help="where to build assets (default: a temp dir)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build everything, copy nothing")
    args = ap.parse_args()

    stage = args.stage or tempfile.mkdtemp(prefix="badge-")
    os.makedirs(os.path.join(stage, "img"), exist_ok=True)

    # --- find the badge first: failing after a network round trip is rude ---
    drive = None
    if not args.dry_run:
        found = find_badge(args.drive)
        if not found:
            raise SystemExit(
                "no CircuitPython badge found.\n"
                "Plug it in and check it mounts (CIRCUITPY), or pass --drive PATH.\n"
                "Use --dry-run to build the assets without a badge.")
        if len(found) > 1:
            print("several CircuitPython drives found:")
            for path, desc in found:
                print("   %s -- %s" % (path, desc))
            raise SystemExit("pass --drive PATH to pick one")
        drive, desc = found[0]
        print("badge: %s" % drive)
        print("       %s" % desc)

    # --- assets ---
    print("\nbuilding assets in %s" % stage)

    handle = caption((args.handle or args.github).upper(), "handle")
    avatar_bmp = os.path.join(stage, "img", "avatar.bmp")
    print("  avatar: github.com/%s" % args.github)
    avatar_to_bmp(fetch_avatar(args.github), avatar_bmp, keep_color=args.color)

    sides = [("photo", "/img/avatar.bmp", handle, "", 0xFFC878)]

    if args.linkedin:
        url, cap = normalize_linkedin(args.linkedin)
        n, s = qr_to_bmp(url, os.path.join(stage, "img", "qr.bmp"))
        print("  qr:     %s  (%d modules at %d px)" % (url, n, s))
        sides.append(("qr", "/img/qr.bmp", "LINKEDIN",
                      caption(cap, "linkedin"), 0x0A66C2))

    gh_url = "https://github.com/%s" % args.github
    n, s = qr_to_bmp(gh_url, os.path.join(stage, "img", "github.bmp"))
    print("  qr:     %s  (%d modules at %d px)" % (gh_url, n, s))
    sides.append(("qr", "/img/github.bmp", "GITHUB",
                  caption(args.github, "github"), 0x8250DF))

    if args.repo:
        repo_url = "https://github.com/ubergeek42/CarolinaCodeConferenceBadge2026"
        n, s = qr_to_bmp(repo_url, os.path.join(stage, "img", "repo.bmp"))
        print("  qr:     %s  (%d modules at %d px)" % (repo_url, n, s))
        sides.append(("qr", "/img/repo.bmp", "BADGE CODE",
                      "make your own", 0x2DA44E))

    with open(os.path.join(stage, "badge_profile.py"), "w") as f:
        f.write(profile_source(sides))

    # --- payload ---
    payload = [("samples/ProfileCard/code.py", "code.py"),
               (os.path.join(stage, "badge_profile.py"), "badge_profile.py")]
    for style, img, _, _, _ in sides:
        name = img.lstrip("/")
        payload.append((os.path.join(stage, name), name))
    for name in LIB_FILES:
        payload.append((os.path.join(HERE, "lib", name), "lib/" + name))

    missing = [s for s, _ in payload if not os.path.isfile(
        s if os.path.isabs(s) else os.path.join(HERE, s))]
    if missing:
        raise SystemExit("missing payload files:\n  " + "\n  ".join(missing))

    if args.dry_run:
        total = sum(os.path.getsize(s if os.path.isabs(s) else os.path.join(HERE, s))
                    for s, _ in payload)
        print("\ndry run -- would copy %d files (%.1f KB) to a badge"
              % (len(payload), total / 1024.0))
        print("assets left in %s" % stage)
        return

    print("\nwriting to %s" % drive)
    total = 0
    written = 0
    skipped = []
    for src, rel in payload:
        src = src if os.path.isabs(src) else os.path.join(HERE, src)
        n = copy_to(src, os.path.join(drive, rel))
        if n is None:
            skipped.append(rel)
        else:
            total += n
            written += 1
            print("  %s" % rel)
    if hasattr(os, "sync"):
        os.sync()

    if skipped:
        libs = [s for s in skipped if s.startswith("lib/")]
        other = [s for s in skipped if not s.startswith("lib/")]
        parts = []
        if libs:
            parts.append("%d libraries already on the badge" % len(libs))
        if other:
            parts.append("%d unchanged" % len(other))
        print("  (skipped %s)" % ", ".join(parts))

    if written:
        print("\ndone -- %d files, %.1f KB. The badge reloads on its own."
              % (written, total / 1024.0))
    else:
        print("\nalready up to date -- nothing needed writing.")
    print("\nSW1/SW2 step through the sides, SW3 toggles the LEDs.")
    print("Your details are in badge_profile.py on the badge -- edit and save,")
    print("CircuitPython reloads instantly.")


if __name__ == "__main__":
    main()
