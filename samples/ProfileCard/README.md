# ProfileCard

A two-sided digital badge. One side shows your photo with your handle underneath; the other shows a QR code pointing at your LinkedIn. Press SW1 or SW2 to flip; SW3 toggles the LEDs off to save battery. Copy this sample's `code.py` over the top-level `code.py` to run it (or pick it from the Launcher menu).

Both sides are pre-rendered BMPs in `/img/` — nothing is drawn or encoded on the badge, so a flip is instant.

## How to personalize

Edit the block at the top of `code.py`, save, and CircuitPython auto-reloads:

```python
HANDLE   = "UBERGEEK42"
LINKEDIN = "in/ubergeek42"

PHOTO_BMP = "/img/avatar.bmp"
QR_BMP    = "/img/qr.bmp"

AUTO_FLIP_SECS = 0        # >0 flips on its own after N idle seconds
LEDS_AT_BOOT   = True     # False boots with the NeoPixels dark
```

`LINKEDIN` is only the caption text under the QR — it does not affect what the QR encodes. If your URL changes you have to regenerate `qr.bmp` (below).

## Controls

| Switch | Action |
|--------|--------|
| SW1 (IO1) | Flip to the other side |
| SW2 (IO2) | Flip to the other side |
| SW3 (IO43) | LEDs on/off |

The two flip buttons do the same thing on purpose — it doesn't matter which one someone grabs. SW3 is the battery switch: the NeoPixels are the biggest current draw on the board, so killing them is the one lever this sample has for stretching a CR123A. Note they're WS2812s, so "off" means every pixel set to black — each chip still idles at a milliamp or so, and the display backlight keeps running either way. Set `LEDS_AT_BOOT = False` to come up dark.

## Regenerating the images

Both BMPs must be **128×128, 8-bit indexed, uncompressed** — the format `adafruit_imageload` reads and the same format the bundled `CarolinaCodeConference.bmp` uses. The two traps are RLE compression (ImageMagick's default for indexed BMP, which `adafruit_imageload` cannot decode) and bit depths other than 8.

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

### QR code

The QR is generated host-side, so the badge carries no QR library. `adafruit_miniqr` is pure Python and runs fine on desktop CPython:

```sh
curl -LO https://raw.githubusercontent.com/adafruit/Adafruit_CircuitPython_miniQR/main/adafruit_miniqr.py
```

```python
import adafruit_miniqr, struct

URL = "https://linkedin.com/in/ubergeek42"
qr = adafruit_miniqr.QRCode(error_correct=adafruit_miniqr.L)
qr.add_data(URL.encode())
qr.make()
n = qr.matrix.width                              # 29 modules for this URL

# widest integer module size leaving >=3 modules of quiet zone in 128 px
scale = max(s for s in range(1, 9) if (n + 6) * s <= 128)
pad = (128 - n * scale) // 2

rows = bytearray()
for y in range(127, -1, -1):                     # BMP rows are bottom-up
    for x in range(128):
        mx, my = (x - pad) // scale, (y - pad) // scale
        dark = 0 <= mx < n and 0 <= my < n and bool(qr.matrix[my, mx])
        rows.append(1 if dark else 0)

pal = bytes((255, 255, 255, 0)) + bytes((0, 0, 0, 0)) + bytes(4 * 254)
hdr = b"BM" + struct.pack("<IHHI", 14 + 40 + len(pal) + len(rows), 0, 0, 14 + 40 + len(pal))
hdr += struct.pack("<IiiHHIIiiII", 40, 128, 128, 1, 8, 0, len(rows), 3780, 3780, 256, 256)
open("img/qr.bmp", "wb").write(hdr + pal + rows)
```

Writing the BMP by hand rather than going through ImageMagick is deliberate: `magick` insists on emitting a 1-bit bilevel BMP for a two-colour image, and this keeps the file byte-for-byte the same shape as the known-good bundled logo.

**Module size matters more than you'd expect.** This URL needs 29 modules. The next scale up (4 px/module, 116 px wide) would leave only 1.5 modules of quiet zone instead of the 4 the spec calls for, so the script settles on 3 px/module — an 87 px code with a comfortable 20 px white border. At ~0.22 mm per screen pixel that's a 0.66 mm module, well inside what a phone camera resolves at arm's length. Shortening the URL is the only way to get bigger modules: dropping to 25 modules (≤32 bytes of payload) would allow 4 px/module.

Always verify the finished BMP actually decodes before trusting it — point your phone at it, or on macOS run it through Vision.

## Code design

- **Both scenes built once at boot** — `build_scene()` returns a complete `displayio.Group` per side, so flipping is a single `display.root_group = scenes[side]` plus a refresh. No image decoding in the hot path, so the flip is instant. Two 128×128 8-bit bitmaps cost ~32 KB of RAM, which the ESP32-S3 has to spare.
- **Backlight forced low before the adafruit imports** — the panel powers up bright white and those imports take a couple of seconds cold. Grabbing IO5 and driving it low on line one means you never see a white flash, then it goes high once the first scene is on screen. Same trick the Launcher uses.
- **`build_scene(bmp, bg, y, lines)` is shared by both sides** — one function covers "full-screen background + 128×128 image + centred text lines", which is why the two sides are ~6 lines of configuration each instead of two parallel blocks of layout code.
- **Any-button edge detection** — `any((not v) and p for v, p in zip(values, prev))` OR-s the press edges of both flip switches, giving one flip per press regardless of which button, with no repeat while held. SW3 runs the same edge logic on its own, so holding it doesn't strobe the LEDs.
- **LEDs off costs nothing to maintain** — the toggle blanks the strip once on the press edge; WS2812s latch their last frame, so the off state needs no further `show()` in the loop. The poll interval also backs off from 20 ms to 80 ms when dark, since there's no animation left to keep smooth.
- **The QR sits on a white background** — the BMP carries its own quiet zone, and a white scene background lets that border blend to the screen edge instead of being framed by black, which is what scanners want.
- **Auto-flip shares the button code path** — both call `flip()`, so there's one place that swaps the scene, refreshes, and resets the idle timer.
- **LEDs deliberately dim** (`brightness=0.15`) — this sample is meant to run all day off the CR123A, and the NeoPixels are the biggest draw on the board. The tint changes per side: warm white for the photo, LinkedIn blue for the QR.
