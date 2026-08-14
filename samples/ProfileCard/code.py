"""
code.py -- ProfileCard: Carolina Code Conference sample
=======================================================
A three-sided badge. SW1 or SW2 cycles through your photo plus
handle, a QR to your LinkedIn, and a QR to this badge's source.

Controls
--------
  SW1 (IO1) / SW2 (IO2)  -- next side (photo -> LinkedIn -> GitHub)
  SW3 (IO43)             -- LEDs on/off. The NeoPixels are the
                            biggest current draw on the board, so
                            turning them off stretches the CR123A.

All three images are pre-rendered BMPs in /img/ -- see README.md for
the one-liner that converts your photo and the script that builds the
QR codes.
"""

# ==============================================================
#   >>>  YOUR DETAILS  <<<
#   Edit these, save the file, and CircuitPython auto-reloads.
#   The printed badge already carries your real name, so the photo
#   side just shows your handle.
#
#   SIDES is the whole rotation, in order. Comment a line out to drop
#   that side; add one to extend it. Two or five sides work the same
#   as three.
#
#     style   "photo" -- dark card, one line of large type below the
#                        image (line 2 is ignored)
#             "qr"    -- white card, two small caption lines
#     accent  tints the LEDs on that side, and colours line 1 on a
#             "qr" card
#
#   The captions are text only -- a QR's URL is baked into its bitmap,
#   so regenerate the BMP (see README.md) if a link changes.
#
#   flash.py writes a badge_profile.py holding your own SIDES; if that
#   file exists it wins, so re-flashing never touches this code and
#   deleting it drops you back to the table below.
# ==============================================================
try:
    from badge_profile import SIDES
except ImportError:
    SIDES = (
        ("photo", "/img/avatar.bmp", "UBERGEEK42", "",                  0xFFC878),
        ("qr",    "/img/qr.bmp",     "LINKEDIN",   "in/ubergeek42",     0x0A66C2),
        ("qr",    "/img/github.bmp", "GITHUB",     "this badge's code", 0x8250DF),
    )

# Seconds of no button press before the badge advances on its own.
# Set to 0 for button-only cycling.
AUTO_FLIP_SECS = 0

# Whether the NeoPixels start lit. SW3 toggles them either way --
# set this False if you want the badge to boot in low-power mode.
LEDS_AT_BOOT = True
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


flip_buttons = (_btn(board.IO1), _btn(board.IO2))
led_button = _btn(board.IO43)

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


# Build every side listed in SIDES. A "photo" card is dark with the
# handle in the largest type that fits; a "qr" card is white so the
# bitmap's own quiet zone blends to the screen edge, which is what
# scanners want. Everything else about the two is the same, which is
# why one table drives both.
scenes = []
tints = []
names = []

for style, bmp, line1, line2, accent in SIDES:
    if style == "photo":
        lines = ((line1, choose_scale(line1), 0xFFFFFF, 146),)
        scenes.append(build_scene(bmp, 0x000000, 4, lines))
    else:
        lines = ((line1, 1, accent, 143),)
        if line2:
            lines += ((line2, 1, 0x303030, 154),)
        scenes.append(build_scene(bmp, 0xFFFFFF, 6, lines))
    tints.append(((accent >> 16) & 0xFF, (accent >> 8) & 0xFF, accent & 0xFF))
    names.append(line1)

# Commenting out every side leaves nothing to show -- say so plainly
# rather than dying on an index error three lines later.
if not scenes:
    raise ValueError("SIDES is empty -- enable at least one side")


# ------------------------------------------------------------------
# Boot
# ------------------------------------------------------------------
side = 0
display.root_group = scenes[side]
display.refresh()
bl.value = True

prev = [b.value for b in flip_buttons]
led_prev = led_button.value
last_flip = time.monotonic()
leds_on = LEDS_AT_BOOT

print("ProfileCard: %d sides -- %s" % (len(scenes), " -> ".join(names)))
print("  SW1/SW2 next side, SW3 toggles LEDs (now %s); auto-advance %s"
      % ("on" if leds_on else "off",
         "off" if AUTO_FLIP_SECS <= 0 else "%ds" % AUTO_FLIP_SECS))


def next_side():
    global side, last_flip
    side = (side + 1) % len(scenes)
    display.root_group = scenes[side]
    display.refresh()
    last_flip = time.monotonic()
    print("side:", names[side])


# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------
while True:
    now = time.monotonic()

    # Advance on the press edge of either front button. OR-ing the two
    # edges means it doesn't matter which one an attendee grabs.
    values = [btn.value for btn in flip_buttons]
    pressed = any((not v) and p for v, p in zip(values, prev))
    prev = values

    if pressed:
        next_side()
        time.sleep(0.15)                              # debounce the release
        prev = [btn.value for btn in flip_buttons]
    elif AUTO_FLIP_SECS > 0 and now - last_flip >= AUTO_FLIP_SECS:
        next_side()

    # SW3 toggles the LEDs. Blanking them once on the edge is enough --
    # the strip holds its last frame, so the off state costs no further
    # bus traffic and no further current beyond the pixels' own idle.
    led_val = led_button.value
    if (not led_val) and led_prev:
        leds_on = not leds_on
        if not leds_on:
            pixels.fill((0, 0, 0))
            pixels.show()
        print("leds:", "on" if leds_on else "off")
        time.sleep(0.15)                              # debounce the release
        led_val = led_button.value
    led_prev = led_val

    if leds_on:
        # Slow breathe so the badge reads as "alive" without eating the
        # battery or distracting from the QR.
        lvl = 0.25 + 0.75 * ((math.sin(now * 1.4) + 1) / 2)
        r, g, b = tints[side]
        pixels.fill((int(r * lvl), int(g * lvl), int(b * lvl)))
        pixels.show()

    # Poll fast enough to feel instant while lit; back off when the LEDs
    # are out, since there's no animation left to keep smooth.
    time.sleep(0.02 if leds_on else 0.08)
