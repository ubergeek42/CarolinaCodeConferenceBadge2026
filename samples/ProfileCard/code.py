"""
code.py -- ProfileCard: Carolina Code Conference sample
=======================================================
A two-sided badge. One side is your photo plus your handle, the
other is a QR code pointing at your LinkedIn. Press any of the
three buttons to flip.

Controls
--------
  SW1 (IO1) / SW2 (IO2) / SW3 (IO43)  -- flip to the other side

Both images are pre-rendered BMPs in /img/ -- see README.md for the
one-liner that converts your own photo and regenerates the QR.
"""

# ==============================================================
#   >>>  YOUR DETAILS  <<<
#   Edit these, save the file, and CircuitPython auto-reloads.
#   The printed badge already carries your real name, so the photo
#   side just shows your handle.
#
#   The QR image itself is /img/qr.bmp -- changing LINKEDIN here
#   only changes the caption text, not the code. Regenerate the
#   BMP (see README.md) if your URL changes.
# ==============================================================
HANDLE   = "UBERGEEK42"
LINKEDIN = "in/ubergeek42"

PHOTO_BMP = "/img/avatar.bmp"
QR_BMP    = "/img/qr.bmp"

# Seconds of no button press before the badge flips on its own.
# Set to 0 for button-only flipping.
AUTO_FLIP_SECS = 0
# ==============================================================


# --- backlight off FIRST, before the slow adafruit imports --------
# The panel powers up bright white and the imports below take a
# couple of seconds on a cold boot. Drive IO5 low up front so the
# screen stays dark until we have something to show.
import board
import digitalio
bl = digitalio.DigitalInOut(board.IO5)
bl.direction = digitalio.Direction.OUTPUT
bl.value = False

import math
import time
import busio
import displayio
import fourwire
import neopixel
import terminalio
import adafruit_st7735r
import adafruit_imageload
from adafruit_display_text import label


# ------------------------------------------------------------------
# Hardware setup
# ------------------------------------------------------------------
# Low brightness on purpose -- this sample is meant to run all day
# off the CR123A, and the LEDs are the biggest draw on the board.
pixels = neopixel.NeoPixel(board.IO4, 5, brightness=0.15, auto_write=False)
pixels.fill((0, 0, 0)); pixels.show()


def _btn(pin):
    b = digitalio.DigitalInOut(pin)
    b.switch_to_input(pull=digitalio.Pull.UP)
    return b


buttons = (_btn(board.IO1), _btn(board.IO2), _btn(board.IO43))

# Font chip shares the SPI bus -- deselect it so it stays quiet.
font_cs = digitalio.DigitalInOut(board.IO9)
font_cs.direction = digitalio.Direction.OUTPUT
font_cs.value = True

displayio.release_displays()
spi = busio.SPI(clock=board.IO12, MOSI=board.IO11)
display_bus = fourwire.FourWire(
    spi, command=board.IO6, chip_select=board.IO10, reset=board.IO7,
    baudrate=8_000_000,
)
display = adafruit_st7735r.ST7735R(
    display_bus, width=128, height=160, rotation=0, bgr=True,
    auto_refresh=False,
)


# ------------------------------------------------------------------
# Scenes
#
# The screen is 128x160 portrait; both BMPs are 128x128. Each scene
# is a full-screen background plus the image near the top, leaving a
# band at the bottom for text. Building both scenes up front means a
# flip is just a root_group swap -- no image decoding mid-flip.
# ------------------------------------------------------------------
def solid_bg(color):
    bmp = displayio.Bitmap(128, 160, 1)
    pal = displayio.Palette(1); pal[0] = color
    return displayio.TileGrid(bmp, pixel_shader=pal)


def choose_scale(text, max_px=124):
    """Biggest scale (4..1) that keeps the text within max_px.

    terminalio glyphs are 6 px wide, so a scale-N string of L chars
    occupies L * 6 * N pixels.
    """
    for s in (4, 3, 2, 1):
        if len(text) * 6 * s <= max_px:
            return s
    return 1


def build_scene(bmp_path, bg_color, image_y, lines):
    """Background + 128x128 image at image_y + centred text lines.

    lines is a sequence of (text, scale, color, y) tuples.
    """
    scene = displayio.Group()
    scene.append(solid_bg(bg_color))

    bitmap, palette = adafruit_imageload.load(
        bmp_path, bitmap=displayio.Bitmap, palette=displayio.Palette
    )
    tile = displayio.TileGrid(bitmap, pixel_shader=palette)
    tile.y = image_y
    scene.append(tile)

    for text, scale, color, y in lines:
        lbl = label.Label(terminalio.FONT, text=text, scale=scale, color=color)
        lbl.anchor_point = (0.5, 0.5)
        lbl.anchored_position = (64, y)
        scene.append(lbl)

    return scene


# Side A -- photo on black, handle centred in the band below it.
photo_scene = build_scene(
    PHOTO_BMP, 0x000000, 4,
    (
        (HANDLE, choose_scale(HANDLE), 0xFFFFFF, 146),
    ),
)

# Side B -- QR on white. The BMP already carries its own white quiet
# zone, so a white background lets it blend edge to edge.
qr_scene = build_scene(
    QR_BMP, 0xFFFFFF, 6,
    (
        ("LINKEDIN", 1, 0x0A66C2, 143),
        (LINKEDIN,   1, 0x303030, 154),
    ),
)

scenes = (photo_scene, qr_scene)
# Per-side LED tint: warm white for the photo, LinkedIn blue for the QR.
tints = ((255, 200, 120), (10, 102, 194))


# ------------------------------------------------------------------
# Boot
# ------------------------------------------------------------------
side = 0
display.root_group = scenes[side]
display.refresh()
bl.value = True

prev = [b.value for b in buttons]
last_flip = time.monotonic()

print("ProfileCard: %s / %s (handle scale=%d)"
      % (HANDLE, LINKEDIN, choose_scale(HANDLE)))
print("  press any button to flip; auto-flip %s"
      % ("off" if AUTO_FLIP_SECS <= 0 else "%ds" % AUTO_FLIP_SECS))


def flip():
    global side, last_flip
    side = 1 - side
    display.root_group = scenes[side]
    display.refresh()
    last_flip = time.monotonic()
    print("side:", "QR" if side else "PHOTO")


# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------
while True:
    now = time.monotonic()

    # Any button, on the press edge. Reading all three and OR-ing the
    # edges means it doesn't matter which one an attendee grabs.
    values = [btn.value for btn in buttons]
    pressed = any((not v) and p for v, p in zip(values, prev))
    prev = values

    if pressed:
        flip()
        time.sleep(0.15)                              # debounce the release
        prev = [btn.value for btn in buttons]
    elif AUTO_FLIP_SECS > 0 and now - last_flip >= AUTO_FLIP_SECS:
        flip()

    # Slow breathe so the badge reads as "alive" without eating the
    # battery or distracting from the QR.
    lvl = 0.25 + 0.75 * ((math.sin(now * 1.4) + 1) / 2)
    r, g, b = tints[side]
    pixels.fill((int(r * lvl), int(g * lvl), int(b * lvl)))
    pixels.show()

    time.sleep(0.02)
