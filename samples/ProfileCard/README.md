# ProfileCard

A digital badge that cycles through a photo card and any number of QR cards. As shipped: your photo with your handle underneath, a QR to your LinkedIn, and a QR to this repo. Press SW1 or SW2 for the next side; SW3 toggles the LEDs off to save battery. Copy this sample's `code.py` over the top-level `code.py` to run it (or pick it from the Launcher menu).

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
| SW1 (IO1) | Next side |
| SW2 (IO2) | Next side |
| SW3 (IO43) | LEDs on/off |

The two advance buttons do the same thing on purpose — it doesn't matter which one someone grabs. SW3 is the battery switch: the NeoPixels are the biggest current draw on the board, so killing them is the one lever this sample has for stretching a CR123A. Note they're WS2812s, so "off" means every pixel set to black — each chip still idles at a milliamp or so, and the display backlight keeps running either way. Set `LEDS_AT_BOOT = False` to come up dark.

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
- **LEDs deliberately dim** (`brightness=0.15`) — this sample is meant to run all day off the CR123A, and the NeoPixels are the biggest draw on the board. The per-side accent doubles as the tint: warm white on the photo, LinkedIn blue, GitHub purple.
