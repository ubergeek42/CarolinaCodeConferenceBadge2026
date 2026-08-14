# ProfileCard

A digital badge that cycles through a photo card and any number of QR cards, and listens to the badges around it. As shipped: your photo with your handle underneath, a QR to your LinkedIn, and a QR to the web flasher so anyone who likes it can make their own. Press SW1 or SW2 for the next side.

SW3 opens the other half: the badge can **accept a small Python module from a nearby badge over ESP-NOW, run it in the background while still showing your card, and pass it on**. It also keeps a log of which badges you were near and for how long. See [Swarm mode](#swarm-mode) below, and [`mods/README.md`](../../mods/README.md) for writing a module.

## Booting into it without losing the picker

[`flash.py`](../../flash.py) and the [web flasher](../../web/) install this sample under `/samples/ProfileCard/` and put [`autostart.py`](autostart.py) at the top level as `code.py`. That shim runs ProfileCard immediately on power-up, and hands off to the Launcher's picker if any button is held while the badge boots — so the badge is a business card by default and every other sample is still one button away.

It works that way because the picker's memory can't be set from a computer. The Launcher stores its last pick in `microcontroller.nvm`, which is internal flash reachable only from CircuitPython — no host-side flasher can write it, and with nvm unset the Launcher falls back to whichever sample sorts first alphabetically. A shim is the only way to make this one the default without a human picking it once.

The shim deliberately ignores the remembered selection: the badge returns to your card on every reset. Choosing another sample from the picker still runs it, it just doesn't become the new default. For the stock behaviour back, copy `samples/Launcher/code.py` over the top-level `code.py`.

Every side is a pre-rendered BMP in `/img/` — nothing is drawn or encoded on the badge, so switching is instant.

## How to personalize

The whole rotation is one table at the top of `code.py`. Comment a line out to drop that side, add one to extend it — two or five sides work the same as three. Save, and CircuitPython auto-reloads:

```python
SIDES = (
    ("photo", "/img/avatar.bmp", "UBERGEEK42", "",                  0xFFC878),
    ("qr",    "/img/qr.bmp",     "LINKEDIN",   "in/ubergeek42",     0x0A66C2),
    ("qr",    "/img/github.bmp", "GITHUB",     "this badge's code", 0x8250DF),
)

AUTO_FLIP_SECS = 0        # >0 advances on its own after N idle seconds
LEDS_AT_BOOT   = True     # False boots with the NeoPixels dark
```

Each entry is `(style, image, line 1, line 2, accent)`. `"photo"` is a dark card with line 1 in the largest type that fits (line 2 is ignored); `"qr"` is a white card with two small caption lines. The accent tints the LEDs on that side and colors line 1 on a QR card.

The captions are text only — a QR's URL is baked into its bitmap, so a new link means regenerating the BMP (below).

## Controls

| Switch | Action |
|--------|--------|
| SW1 (IO1) / SW2 (IO2) | Next side. In LISTEN, accepts an offered module; in SHARE, picks what to send |
| SW3 (IO43) tap | Cycle mode: NORMAL → LISTEN → SHARE → NORMAL |
| SW3 hold (>1 s) | LEDs on/off |
| SW1+SW2 held at boot | Skip autoloading `/mods` |

The two advance buttons do the same thing on purpose — it doesn't matter which one someone grabs. SW3 carries two jobs because it's the only free button: a tap moves through the modes, a hold is the LED switch. The hold fires the moment it qualifies rather than on release, because a button that does nothing until you let go feels broken.

Note the LEDs are WS2812s, so "off" means every pixel set to black — each chip still idles at a milliamp or so. Set `LEDS_AT_BOOT = False` to come up dark.

If the badge boots through [`autostart.py`](autostart.py), holding *any* button at boot gives you the Launcher instead, which is also a way past a module that misbehaves — nothing in `/mods` runs. The SW1+SW2 hatch is for when ProfileCard is the top-level `code.py` directly.

## Swarm mode

```
NORMAL ──SW3──▶ LISTEN ──SW3──▶ SHARE ──SW3──▶ NORMAL
                  │                │
        SW1/SW2 accepts      SW1/SW2 picks
        what is offered      what to send
```

**LISTEN** shows how many badges are near, and puts up a banner naming any module being offered — who from, how big, how many hops it has travelled, and how much of it has arrived. Nothing runs until you press SW1 or SW2.

**SHARE** broadcasts one of your modules on a loop. Anyone in LISTEN sees the offer. A ~1.6 KB module is 7 frames, about 0.12 s per lap, so it crosses in well under a second.

Chunks are buffered *before* you accept, so accepting is instant instead of costing another lap — consent gates execution, not memory. Accepting also makes you a sender for that module, which is how it spreads hop by hop. Declining sticks for a minute, so "no" doesn't mean "ask me again in 300 ms".

On battery an accepted module is written to `/mods/` and autoloads for good. **On USB it can only run from RAM** — `storage.remount()` refuses while the drive is visible to a computer — and the banner says `in RAM (tethered)` when that happens.

There is no signing and no sandbox, and the design doesn't pretend otherwise. The threat model is "a friend pushes something silly to your badge": the button press is the security model, sizes are capped, a module that crashes or hangs is unloaded automatically, and there are two ways to boot without loading anything.

## The NEARBY side

A fourth side, generated rather than pre-rendered: it lists the badges you have
been near, live neighbours first (strongest signal first), then padded from the
nvm log with people seen earlier in the session. It exists *because* it is
generated — about 3 KB of labels against the ~19 KB a 128×128 image costs, on a
badge with roughly 45 KB free once the radio is up. There was no room for a
fourth image side; there is plenty for a fourth text one.

It says `near`, `close` or `around`, not metres. That is deliberate: the only
distance signal available is RSSI, and `badgenet.rssi_to_unit()`'s calibration
is still an unmeasured guess, so a number would be false precision. Set
`PEERS_SIDE = False` to drop the side.

Badges announce themselves twice: a `HELLO` every second carrying just the
handle, and a fuller `CARD` every fifth beacon carrying handle **and** LinkedIn.
Two kinds rather than one longer message, because badges already in the wild —
and BadgeRadar — decode `HELLO`'s body as a bare handle, and a kind they don't
know is simply ignored. The side shows only the handle; the link is for the log,
so `tools/badgedump.py` can tell you who `ada` actually was.

Both default to what is already printed on your other sides — the handle from
the photo caption, the link from the LinkedIn caption — because the flashers
write `badge_profile.py` and never touch `code.py`. Anything hardcoded would
make every flashed badge introduce itself as whoever committed the line, which
is exactly the bug that shipped for one commit.

**Worth deciding before you wear it.** The handle and link go out as a plain
broadcast, so anyone in range with an ESP32 can log them, not only other badges.
The handle is already printed on the front of the badge, but a machine-readable
link is different in kind. `LINK = None` broadcasts a handle and nothing else.

## The proximity log

With `STATS = True` the badge remembers every badge it hears — how long you were near each other, how many separate times, and the closest you ever got — in `microcontroller.nvm`, which survives a flat battery and, unlike the filesystem, is writable while plugged in. Read it back with:

```sh
python3 tools/badgedump.py            # who you were near, longest first,
                                      # with the LinkedIn they broadcast
python3 tools/badgedump.py --csv      # the same, machine readable
python3 tools/badgedump.py --tombstone   # just how long the last run lasted
```

Two honest limits. The badge has no RTC, so records carry a session number and an uptime, not a time of day; `badgedump.py` can pin the *current* session to the wall clock because it knows both, and says so rather than inventing times for earlier ones. And "closest" is a raw dBm reading — RSSI correlates with distance on a good day and moves several dB when a person steps between two badges, so it is never converted into metres.

## Regenerating the images

Every BMP must be **128×128, 8-bit indexed, uncompressed** — the format `adafruit_imageload` reads and the same format the bundled `CarolinaCodeConference.bmp` uses. The two traps are RLE compression (ImageMagick's default for indexed BMP, which `adafruit_imageload` cannot decode) and bit depths other than 8.

### Photo

Requires ImageMagick. `-compress None` is the important flag:

```sh
magick your_photo.png \
  -resize '128x128^' -gravity center -extent 128x128 \
  -colorspace Gray -normalize -level '12%,90%' -unsharp 0x1+0.7+0 \
  -colors 255 -type Palette -compress None BMP3:img/avatar.bmp
```

`-resize '128x128^' -extent 128x128` centre-crops to a square regardless of the source aspect ratio. The grayscale/normalize/unsharp trio is tuned for high-contrast line art on a small panel; drop `-colorspace Gray` to keep colour.

Verify with `file img/avatar.bmp` — you want `128 x 128 x 8, image size 16384` and **no** mention of compression.

### QR codes

The QRs are generated host-side, so the badge carries no QR library. `adafruit_miniqr` is pure Python and runs fine on desktop CPython:

```sh
curl -LO https://raw.githubusercontent.com/adafruit/Adafruit_CircuitPython_miniQR/main/adafruit_miniqr.py
```

```python
import adafruit_miniqr, struct

CODES = {
    "img/qr.bmp":     "https://linkedin.com/in/ubergeek42",
    "img/github.bmp": "https://github.com/ubergeek42/CarolinaCodeConferenceBadge2026",
}

for path, url in CODES.items():
    qr = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
    qr.add_data(url.encode())
    qr.make()
    n = qr.matrix.width                          # 29 and 33 modules here

    # widest integer module size leaving >=3 modules of quiet zone in 128 px
    scale = max(s for s in range(1, 9) if (n + 6) * s <= 128)
    pad = (128 - n * scale) // 2

    rows = bytearray()
    for y in range(127, -1, -1):                 # BMP rows are bottom-up
        for x in range(128):
            mx, my = (x - pad) // scale, (y - pad) // scale
            dark = 0 <= mx < n and 0 <= my < n and bool(qr.matrix[my, mx])
            rows.append(1 if dark else 0)

    pal = bytes((255, 255, 255, 0)) + bytes((0, 0, 0, 0)) + bytes(4 * 254)
    hdr = b"BM" + struct.pack("<IHHI", 14 + 40 + len(pal) + len(rows), 0, 0, 14 + 40 + len(pal))
    hdr += struct.pack("<IiiHHIIiiII", 40, 128, 128, 1, 8, 0, len(rows), 3780, 3780, 256, 256)
    open(path, "wb").write(hdr + pal + rows)
    print(path, n, "modules at", scale, "px =", round(pad / scale, 1), "modules quiet zone")
```

Writing the BMP by hand rather than going through ImageMagick is deliberate: `magick` insists on emitting a 1-bit bilevel BMP for a two-colour image, and this keeps the file byte-for-byte the same shape as the known-good bundled logo.

**Module size matters more than you'd expect, and it's set by URL length.** The LinkedIn URL fits in 29 modules; the 61-character GitHub URL needs 33. Both land on 3 px/module — an 87 px and a 99 px code, with 6.7 and 4.7 modules of quiet zone against the 4 the spec asks for. At ~0.22 mm per screen pixel that's a 0.66 mm module, well inside what a phone camera resolves at arm's length, but it's why the GitHub side is the tightest of the three. There isn't much headroom left: at 35 modules the quiet zone falls to 3.7, and at 37 the widest fitting module size drops to 2 px, which is where scanning gets unreliable. Shorten the link rather than push past that.

Always verify the finished BMP actually decodes before trusting it — point your phone at it, or on macOS run it through Vision. `magick` handles the indexed BMP that Core Image won't:

```sh
magick img/github.bmp /tmp/verify.png && swift qrdecode.swift /tmp/verify.png
# DECODED[VNBarcodeSymbologyQR]: https://github.com/ubergeek42/CarolinaCodeConferenceBadge2026
```

```swift
// qrdecode.swift
import Foundation
import CoreImage
import Vision

let path = CommandLine.arguments[1]
guard let img = CIImage(contentsOf: URL(fileURLWithPath: path)) else {
    print("could not load"); exit(1)
}
let req = VNDetectBarcodesRequest()
try VNImageRequestHandler(ciImage: img, options: [:]).perform([req])
guard let results = req.results, !results.isEmpty else {
    print("NO BARCODE DETECTED"); exit(1)
}
for r in results {
    print("DECODED[\(r.symbology.rawValue)]: \(r.payloadStringValue ?? "<nil>")")
}
```

## Code design

- **Every scene built once at boot** — `build_scene()` returns a complete `displayio.Group` per side, so advancing is a single `display.root_group = scenes[side]` plus a refresh. No image decoding in the hot path, so the switch is instant. Each 128×128 8-bit bitmap costs ~16 KB of RAM, so the three sides are ~48 KB — comfortable on the ESP32-S3, but it's the number to watch if you keep adding sides.
- **Backlight forced low before the adafruit imports** — the panel powers up bright white and those imports take a couple of seconds cold. Grabbing IO5 and driving it low on line one means you never see a white flash, then it goes high once the first scene is on screen. Same trick the Launcher uses.
- **One table drives the whole rotation** — `build_scene(bmp, bg, y, lines)` covers "full-screen background + 128×128 image + centred text lines" for every side, so the only thing that differs between a photo card and a QR card is the two-branch `for` loop over `SIDES` that fills in the background, image offset, and text. Adding a fourth side is one line of config, not another block of layout code.
- **Any-button edge detection** — `any((not v) and p for v, p in zip(values, prev))` OR-s the press edges of both advance switches, giving one step per press regardless of which button, with no repeat while held. SW3 runs the same edge logic on its own, so holding it doesn't strobe the LEDs.
- **LEDs off costs nothing to maintain** — the toggle blanks the strip once on the press edge; WS2812s latch their last frame, so the off state needs no further `show()` in the loop. The poll interval also backs off from 20 ms to 80 ms when dark, since there's no animation left to keep smooth.
- **QRs sit on a white background** — the BMP carries its own quiet zone, and a white scene background lets that border blend to the screen edge instead of being framed by black, which is what scanners want.
- **Auto-advance shares the button code path** — both call `next_side()`, so there's one place that steps `(side + 1) % len(scenes)`, swaps the scene, refreshes, and resets the idle timer. The modulo is why the rotation doesn't care how many sides you configured.
- **One root group with three slots** — the side, a module's overlay, and the swarm banner. Swapping a side is one assignment, and a module's graphics survive the swap instead of being rebuilt. A *full* repaint measures 87 ms on this panel (not SPI-bound — 74 ms at 24 MHz), so the banner is a 40-row strip rather than a screen takeover, and the display only refreshes when something actually changed.
- **Exactly one radio frame per loop pass** — unpaced ESP-NOW sends saturate the TX queue and then `send()` blocks for up to **205 ms** with no exception and no counter. Paced at 8 ms or more it is a flat 0.6 ms every time. This is the single most important measured constraint in the whole sample.
- **LED writes are rate limited and allowed to fail** — every NeoPixel write allocates from the ESP-IDF heap, which is scarce with WiFi up, and it does run out: an early build died with `espidf.MemoryError` after a few seconds. Writes happen at 30 Hz and a lost frame is counted, not raised.
- **The watchdog is cleared at boot and disarmed on exit** — a RAISE-mode watchdog *survives a soft reload*, so one left armed by a previous run fires during the next boot's image loading and hard faults into safe mode with no output at all. `code.py` clears any inherited watchdog on line one and a `finally` takes its own down.
- **Running out of RAM drops sides rather than dying** — each card is ~19 KB of a ~45 KB budget once the radio is up, so a fourth side is genuinely close to the ceiling. A badge showing two of your three sides is a better failure than one showing the CircuitPython console.
- **The proximity log is one nvm write a minute** — an nvm write costs ~65 ms *per call regardless of length* (one byte and two kilobytes are the same, because the cost is erasing the page), so the whole region is serialised in a single slice assignment, never during a transfer, and a failed write keeps the log in RAM and retries.
- **`SIDES` comes from `/badge_profile.py` when it exists** — so re-running the flasher changes your photo and links without ever touching `code.py`, and your edits here survive a re-flash.
