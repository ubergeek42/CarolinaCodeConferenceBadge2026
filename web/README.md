# Web flasher

A static page that provisions a badge from the browser: type a GitHub username,
pick the CIRCUITPY drive, done. No install, no toolchain, nothing uploaded
anywhere — every byte is produced locally and written straight to the drive.

It is the same pipeline as [`flash.py`](../flash.py), and the QR half is
verified byte-identical to it (see [Testing](#testing)).

## Why not WebUSB

The obvious answer is wrong, so it's worth writing down.

| Approach | Verdict |
|---|---|
| **WebUSB** | **Can't work.** The badge presents USB Mass Storage and CDC serial, both claimed by the OS kernel driver. WebUSB can only talk to interfaces nothing else has claimed. |
| **Web Serial → REPL** | **Can't work.** Writing files through the REPL fails: CircuitPython cannot write to its own filesystem while USB mass storage is enumerated (`RuntimeError: Cannot remount path when visible via USB`, confirmed on this badge). |
| **File System Access API** | **Works.** The host is always allowed to write to CIRCUITPY — it's CircuitPython that gets locked out. `showDirectoryPicker()` hands the page a directory handle and the writes are ordinary file writes. |

Browser support is the cost: `showDirectoryPicker` is Chrome/Edge desktop only.
Firefox and Safari get the **Download .zip** button instead — unzip onto the
drive, keeping the folder structure — which is the same tradeoff the ESP web
flashers live with.

## Layout

```
index.html   form, preview, and the copy that explains what's happening
app.js       orchestration: fetch avatar, build payload, write to the drive
qr.js        QR encoder, ported from tools/adafruit_miniqr.py (MIT)
bmp.js       128x128 8-bit indexed BMP writer
font.js      terminalio's ter-u12n glyphs, so the preview matches the panel
zip.js       store-only ZIP for the fallback path
```

The page fetches `../samples/ProfileCard/code.py` and `../lib/*.mpy` from the
same origin, so it always ships whatever the repo currently holds.

## Things that bit, and why the code looks like this

- **The avatar URL matters.** `github.com/<user>.png` 302s through a response
  with no `Access-Control-Allow-Origin`, so `fetch` is blocked. The page uses
  `avatars.githubusercontent.com/<login>`, which sends `ACAO: *` and resolves
  by username. The tradeoff: an unknown user returns an identicon rather than a
  404, so the preview is the validation.
- **`api.github.com` is avoided deliberately.** It would give a real 404 for a
  bad username, but it's rate limited to 60 requests/hour *per IP* — one shared
  conference NAT would exhaust that after 60 attendees.
- **`.nojekyll` at the repo root is required.** GitHub Pages runs Jekyll by
  default, which excludes files beginning with an underscore — that would
  silently 404 every `__init__.mpy` in `lib/`, and the badge would boot to an
  ImportError.
- **The BMP writer is hand-rolled** for the same reason as in `flash.py`: image
  encoders pick bit depth from colour count, and a 1-bit or 4-bit BMP fails to
  load on the badge. This always emits 8-bit with a full 256-entry palette.
- **The QR version selector adds 12 bits** for the mode nibble and length byte.
  miniqr's own auto-select omits them, so a URL landing exactly on a version
  boundary (54 characters) overflows instead of stepping up. Both this and
  `flash.py` compute it the corrected way, which is what keeps them identical.

## It won't overwrite your work

The badge is yours to hack, so the flasher never destroys a file it didn't
create. A file is only written if one of these holds:

1. it's byte-identical to what we'd write anyway (nothing happens),
2. `.badge_flash.json` on the badge records that we wrote it last time, or
3. it's an unmodified file from this repo — the Launcher that ships as
   `code.py`, or the sample itself.

Anything else is left alone and named in the notes, with a *Replace my edits*
checkbox to override. So a stock badge flashes cleanly, a re-flash updates its
own files, and a badge whose owner has been editing `code.py` keeps their work.

`flash.py` scans the whole repo for case 3. The page can only fetch paths it
knows, so it checks the Launcher and ProfileCard; a badge running some *other*
untouched sample is conservatively preserved rather than replaced.

## Testing

The parts that can be checked without a human are checked:

```sh
# QR + BMP output is byte-identical to flash.py's, across edge cases
node /tmp/qrtest.mjs <urls...>      # then cmp against flash.py's output

# every printable glyph matches the BDF the firmware uses
# (95/95, zero mismatches)

# the ZIP is a real ZIP
unzip -t badge-you.zip

# the whole page, in a real browser, no clicking required
python3 -m http.server 8765
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --virtual-time-budget=15000 --screenshot=/tmp/shot.png \
  "http://localhost:8765/web/?gh=ubergeek42&li=in/ubergeek42&repo=1"
```

The `?gh=…&li=…&handle=…&repo=1` parameters prefill the form and build the
preview immediately. That makes the page shareable as a ready-made link, and it
is what lets the pipeline be exercised headlessly.

**Not covered by any of that:** the `showDirectoryPicker` write itself, which
needs a real user gesture and a real drive. Everything up to and including the
bytes handed to it is verified; the write loop is not.

## Deploying

Settings → Pages → deploy from `main`, root. The page then lives at
`/web/`, and `.nojekyll` keeps `lib/` intact.
