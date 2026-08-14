"""
code.py -- BadgeRadar: Carolina Code Conference sample
=====================================================
Shows which other badges are near you. Every badge broadcasts a HELLO
a couple of times a second over ESP-NOW; this listens, and plots each
badge it hears as a blip whose distance from the centre comes from
signal strength.

Controls
--------
  SW1 (IO1)   -- switch view: RADAR / LIST
  SW2 (IO2)   -- toggle SIM mode (fake neighbours, for testing solo)
  SW3 (IO43)  -- LEDs on/off

SIM mode exists because ESP-NOW has no loopback: a lone badge hears
absolutely nothing, not even itself. With SIM on, badgenet.SimRadio
invents five neighbours that wander in and out of range, so the whole
display, peer table and aging path can be watched with one badge on a
desk. Turn SIM off once there is a second badge in the room.
"""

# ==============================================================
#   >>>  YOUR DETAILS  <<<
# ==============================================================
HANDLE = "ubergeek42"          # broadcast to nearby badges (keep it short)

START_IN_SIM = True            # False to go straight to the real radio
BEACON_HZ    = 2.0             # HELLO broadcasts per second
FORGET_AFTER = 20.0            # seconds unseen before a badge drops off
TX_POWER     = 20.0            # 2.0 = arm's length, 20.0 = across the room
LEDS_AT_BOOT = True
# ==============================================================


# --- backlight off FIRST, before the slow adafruit imports --------
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
import bitmaptools
import adafruit_st7735r
from adafruit_display_text import label

import badgenet as bn


# ------------------------------------------------------------------
# Hardware
# ------------------------------------------------------------------
pixels = neopixel.NeoPixel(board.IO4, 5, brightness=0.2, auto_write=False)
pixels.fill((0, 0, 0)); pixels.show()


def _btn(pin):
    b = digitalio.DigitalInOut(pin)
    b.switch_to_input(pull=digitalio.Pull.UP)
    return b


buttons = (_btn(board.IO1), _btn(board.IO2), _btn(board.IO43))

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
# Scene: one bitmap we repaint for the radar, plus text overlaid on top
# ------------------------------------------------------------------
W, H = 128, 160
CX, CY = 64, 84            # radar centre
R_MAX = 54                 # outermost ring
# Blips never come closer than this, so the strongest signal stays visually
# distinct from the "you are here" marker at the centre.
R_MIN = 16

BG, RING, NEAR, MID, FAR, ME = 0, 1, 2, 3, 4, 5

canvas = displayio.Bitmap(W, H, 6)
pal = displayio.Palette(6)
pal[BG]   = 0x000008
pal[RING] = 0x004430
pal[NEAR] = 0x00FF66
pal[MID]  = 0xFFCC00
pal[FAR]  = 0xFF4444
pal[ME]   = 0x00CCFF

scene = displayio.Group()
scene.append(displayio.TileGrid(canvas, pixel_shader=pal))

title = label.Label(terminalio.FONT, text="BADGE RADAR", color=0x00CCFF)
title.anchor_point = (0.5, 0.0)
title.anchored_position = (64, 2)
scene.append(title)

count_lbl = label.Label(terminalio.FONT, text="", color=0xFFFFFF)
count_lbl.anchor_point = (0.5, 0.0)
count_lbl.anchored_position = (64, 13)
scene.append(count_lbl)

# A fixed pool of labels. Allocating per frame would fragment the heap,
# so we reuse these and blank the ones we don't need.
MAX_LABELS = 6
blip_labels = []
for _ in range(MAX_LABELS):
    lbl = label.Label(terminalio.FONT, text="", color=0xC0C0C0)
    lbl.anchor_point = (0.5, 0.5)
    lbl.anchored_position = (-20, -20)
    scene.append(lbl)
    blip_labels.append(lbl)

foot1 = label.Label(terminalio.FONT, text="", color=0x707070)
foot1.anchor_point = (0.0, 1.0)
foot1.anchored_position = (3, 150)
scene.append(foot1)

foot2 = label.Label(terminalio.FONT, text="", color=0x707070)
foot2.anchor_point = (0.0, 1.0)
foot2.anchored_position = (3, 159)
scene.append(foot2)

display.root_group = scene


def clear():
    bitmaptools.fill_region(canvas, 0, 0, W, H, BG)


def blip_color(unit):
    if unit < 0.34:
        return NEAR
    if unit < 0.67:
        return MID
    return FAR


def dot(x, y, color, size=1):
    """Filled square blip, clipped to the canvas."""
    x0 = max(0, x - size)
    y0 = max(0, y - size)
    x1 = min(W, x + size + 1)
    y1 = min(H, y + size + 1)
    if x1 > x0 and y1 > y0:
        bitmaptools.fill_region(canvas, x0, y0, x1, y1, color)


def angle_for(mac):
    """A stable bearing per badge, so a blip doesn't jump around.

    Derived from the MAC rather than kept in a dict: no state to age out,
    and the same badge always lands in the same direction.
    """
    h = (mac[3] * 7 + mac[4] * 31 + mac[5] * 127) % 360
    return h * math.pi / 180.0


def draw_radar(peers, now):
    clear()
    for frac in (0.34, 0.67, 1.0):
        bitmaptools.draw_circle(canvas, CX, CY, int(R_MAX * frac), RING)
    # Crosshair through the centre, broken so it reads as a reticle.
    bitmaptools.draw_line(canvas, CX - R_MAX, CY, CX - R_MAX + 8, CY, RING)
    bitmaptools.draw_line(canvas, CX + R_MAX - 8, CY, CX + R_MAX, CY, RING)
    bitmaptools.draw_line(canvas, CX, CY - R_MAX, CX, CY - R_MAX + 8, RING)
    bitmaptools.draw_line(canvas, CX, CY + R_MAX - 8, CX, CY + R_MAX, RING)
    dot(CX, CY, ME, 1)

    for i, p in enumerate(peers[:MAX_LABELS]):
        unit = bn.rssi_to_unit(p["rssi"])
        r = R_MIN + unit * (R_MAX - R_MIN)
        a = angle_for(p["mac"])
        x = int(CX + r * math.cos(a))
        y = int(CY + r * math.sin(a))
        dot(x, y, blip_color(unit), 2)
        lbl = blip_labels[i]
        lbl.text = table.label(p)
        lbl.color = 0xFFFFFF if unit < 0.34 else 0xA0A0A0
        # Nudge the caption toward the centre so it stays on screen.
        lbl.anchored_position = (min(max(x, 16), W - 16), y - 9 if y > 20 else y + 9)
    for i in range(len(peers), MAX_LABELS):
        blip_labels[i].text = ""


def draw_list(peers, now):
    clear()
    for i in range(MAX_LABELS):
        blip_labels[i].text = ""
    y = 30
    for i, p in enumerate(peers[:MAX_LABELS]):
        unit = bn.rssi_to_unit(p["rssi"])
        # Signal bar: longer is closer.
        w = int((1.0 - unit) * 96)
        bitmaptools.fill_region(canvas, 14, y + 8, 14 + max(w, 1), y + 11,
                                blip_color(unit))
        lbl = blip_labels[i]
        lbl.text = "%-10s %4ddBm" % (table.label(p)[:10], int(p["rssi"]))
        lbl.color = 0xFFFFFF if unit < 0.34 else 0xA0A0A0
        lbl.anchor_point = (0.0, 0.0)
        lbl.anchored_position = (14, y)
        y += 19


# ------------------------------------------------------------------
# Radio -- SimRadio and Radio are interchangeable
# ------------------------------------------------------------------
table = bn.PeerTable(ttl=FORGET_AFTER)
sim = START_IN_SIM
radio = None
seen_ever = set()


def open_radio(use_sim):
    global radio
    if radio is not None:
        radio.deinit()
        radio = None
    if use_sim:
        return bn.SimRadio()
    return bn.Radio(channel=bn.CHANNEL, buffer_size=2048, tx_power=TX_POWER)


radio = open_radio(sim)
HELLO_BODY = HANDLE.encode()[:bn.MAX_BODY]

leds_on = LEDS_AT_BOOT
view = 0                      # 0 = radar, 1 = list
prev = [b.value for b in buttons]
last_beacon = 0.0
last_draw = 0.0

display.refresh()
bl.value = True

print("BadgeRadar up. mode=%s channel=%d handle=%s"
      % ("SIM" if sim else "LIVE", bn.CHANNEL, HANDLE))
if not sim:
    print("  mac=%s tx_power=%s dBm" % (bn.mac_str(radio.mac), radio.tx_power))


# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------
while True:
    now = time.monotonic()

    values = [b.value for b in buttons]
    pressed = [(not v) and p for v, p in zip(values, prev)]
    prev = values

    if pressed[0]:
        view = 1 - view
        for lbl in blip_labels:                    # views anchor differently
            lbl.text = ""
            lbl.anchor_point = (0.5, 0.5) if view == 0 else (0.0, 0.0)
        print("view:", "RADAR" if view == 0 else "LIST")
        last_draw = 0.0
    if pressed[1]:
        sim = not sim
        table = bn.PeerTable(ttl=FORGET_AFTER)
        try:
            radio = open_radio(sim)
            print("mode:", "SIM" if sim else "LIVE")
        except Exception as ex:
            print("radio switch failed:", type(ex).__name__, ex)
            sim = True
            radio = open_radio(True)
        last_draw = 0.0
    if pressed[2]:
        leds_on = not leds_on
        if not leds_on:
            pixels.fill((0, 0, 0)); pixels.show()
        print("leds:", "on" if leds_on else "off")

    # --- radio ---
    if now - last_beacon > 1.0 / BEACON_HZ:
        try:
            radio.send(bn.HELLO, HELLO_BODY)
        except Exception as ex:
            print("send failed:", type(ex).__name__, ex)
        last_beacon = now

    for mac, kind, body, rssi, _t in radio.poll():
        if kind != bn.HELLO:
            continue
        handle = ""
        try:
            handle = body.decode()[:12]
        except Exception:
            pass                                   # garbled handle: keep the id
        table.observe(mac, rssi, now=now, handle=handle)
        seen_ever.add(mac)

    table.age(now)

    # --- display, throttled: a full repaint is ~40 KB over SPI ---
    if now - last_draw > 0.15:
        peers = table.nearby(now)
        if view == 0:
            draw_radar(peers, now)
        else:
            draw_list(peers, now)
        count_lbl.text = "%d near / %d seen" % (len(peers), len(seen_ever))
        foot1.text = "%s  ch%d" % ("SIM" if sim else "LIVE", bn.CHANNEL)
        foot2.text = "S1:view S2:sim S3:led"
        display.refresh()
        last_draw = now

    # --- LEDs: a proximity meter for the closest badge ---
    if leds_on:
        closest = table.closest(now)
        if closest is None:
            idle = 0.12 + 0.10 * ((math.sin(now * 1.2) + 1) / 2)
            pixels.fill((0, 0, int(60 * idle)))
        else:
            unit = bn.rssi_to_unit(closest["rssi"])
            lit = max(1, int(round((1.0 - unit) * 5)))
            for i in range(5):
                if i < lit:
                    pixels[i] = (int(255 * unit), int(255 * (1 - unit)), 40)
                else:
                    pixels[i] = (0, 0, 0)
        pixels.show()

    time.sleep(0.02)
