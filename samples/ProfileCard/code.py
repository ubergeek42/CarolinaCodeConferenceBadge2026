"""
code.py -- ProfileCard: a business card that catches code
=========================================================
A multi-sided digital business card that also listens to the badges around
it. SW1 or SW2 cycles through your sides; SW3 opens the door to the swarm,
where the badge can accept a small Python module from a nearby badge, run it
in the background, and pass it on.

Controls
--------
  SW1 (IO1) / SW2 (IO2)  -- next side. In LISTEN, accepts an offered
                            module; in SHARE, picks what to share.
  SW3 (IO43) tap         -- NORMAL -> LISTEN -> SHARE -> NORMAL
  SW3 hold (>1 s)        -- LEDs on/off. The NeoPixels are one of the
                            bigger draws on the board.
  SW1+SW2 held at boot   -- skip autoloading everything in /mods; the way
                            back if a module wedges the loop.

Three things run at once, and none may block the others: the card is on
screen, any received module gets a `tick()` every pass, and the radio keeps
listening. There is no asyncio and no threading in this build, so "at once"
means cooperative and time-budgeted -- see lib/badgemod.py.

What the measurements made us do
--------------------------------
Everything unusual in here traces to something measured on this hardware:

  * A full-screen repaint costs 87 ms, so sides are built once at boot and
    a flip is a single group swap. The swarm UI is a bottom banner rather
    than a screen takeover, because a banner dirties 40 rows, not 160.
  * Unpaced ESP-NOW sends make `send()` block for up to 205 ms. So exactly
    one frame goes out per loop pass, always.
  * An nvm write costs 65 ms whatever its size, so the proximity log is
    serialised whole, once a minute, and never during a transfer.
  * `storage.remount()` fails while USB is connected, so a module accepted
    at a desk runs from RAM; on battery it persists to /mods and autoloads
    for good. The banner says which happened.
  * A NeoPixel write allocates ESP-IDF heap, which is scarce with WiFi up and
    really does run out, so LED writes are rate limited and every one of them
    tolerates failing.
"""

# ==============================================================
#   >>>  YOUR DETAILS  <<<
#   Edit these, save the file, and CircuitPython auto-reloads.
#
#   SIDES is the whole rotation, in order. Comment a line out to drop
#   that side; add one to extend it.
#
#     style   "photo" -- dark card, one line of large type below the
#                        image (line 2 is ignored)
#             "qr"    -- white card, two small caption lines
#     accent  tints the LEDs on that side, and colours line 1 on a
#             "qr" card
# ==============================================================
SIDES = (
    ("photo", "/img/avatar.bmp", "UBERGEEK42",  "",              0xFFC878),
    ("qr",    "/img/qr.bmp",     "LINKEDIN",    "in/ubergeek42", 0x0A66C2),
    ("qr",    "/img/repo.bmp",   "MAKE YOUR OWN", "flash a badge", 0x2DA44E),
)

# A badge provisioned by flash.py or the web flasher gets its own details in
# /badge_profile.py, which wins over the table above. That is what lets a
# re-flash change your photo and links without ever rewriting this file -- so
# the edits you make here survive, and so does your profile.
try:
    from badge_profile import SIDES
except ImportError:
    pass                            # not flashed; the table above is it

HANDLE = "ubergeek42"       # what nearby badges see (8 chars are logged)

# Seconds of no button press before the badge advances on its own.
AUTO_FLIP_SECS = 0

# --- radio ----------------------------------------------------
# The radio is the single biggest load on the board, and leaving it listening
# is what costs the badge roughly two thirds of its battery life -- about 6
# hours instead of 18. That is the trade being made on purpose: listening is
# what makes the proximity log and module sharing possible at all, and the
# badge has a physical power switch for when it should be off.
RADIO = True
TX_POWER = 20.0             # 2.0 = arm's length, 20.0 = across the room
BEACON_SECS = 1.0           # how often we say who we are

# --- power ----------------------------------------------------
LEDS_AT_BOOT = True
LED_BRIGHTNESS = 0.2        # bright enough for the colour cycle to read across
                            # a lit room. This is the biggest discretionary
                            # draw on the board and it is spent deliberately:
                            # the LEDs are the part people actually notice
LED_HZ = 30                 # LED updates per second. Not per loop pass: every
                            # NeoPixel write allocates ESP-IDF heap, which is
                            # scarce with WiFi up, and 30 Hz already exceeds
                            # what the eye resolves on five pixels
# The screen stays on. Blanking it after a minute saves roughly an hour and a
# half, and it was the wrong trade: a business card that goes dark is a business
# card you have to poke before you can show it to someone. The badge has a
# physical power switch for when it should be off.
BACKLIGHT_IDLE_SECS = 0     # seconds untouched before blanking; 0 = never

# --- log ------------------------------------------------------
STATS = True                # remember who you were near, in nvm
POWER_LABEL = 1             # tag for the battery tombstone; bump it whenever
                            # you change the settings above, so a runtime
                            # measurement can't be attributed to the wrong one

# --- modules --------------------------------------------------
AUTOLOAD = True             # run whatever is already in /mods at boot
# ==============================================================


# --- backlight off FIRST, before the slow adafruit imports --------
# The panel powers up bright white and the imports below take a couple of
# seconds on a cold boot. Drive IO5 low up front so the screen stays dark
# until we have something to show.
import board
import digitalio
bl = digitalio.DigitalInOut(board.IO5)
bl.direction = digitalio.Direction.OUTPUT
bl.value = False

# --- disarm any watchdog we inherited, before the slow part of boot -------
# A watchdog in RAISE mode SURVIVES A SOFT RELOAD. If the previous run armed
# one and stopped feeding it -- Ctrl-C into the REPL, an exception, saving a
# new code.py -- it is still counting down while this boot does its slow
# imports and image loading, and when it fires in the middle of displayio
# setup the board does not raise, it hard faults into safe mode with no
# output at all. That cost an hour to find. Clear it first, arm ours later.
try:
    from microcontroller import watchdog as _inherited_wd
    _inherited_wd.mode = None
except Exception:
    pass

import gc
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

import badgenet as bn
import badgemod
import badgexfer as bx
import badgestats as bstats


# ------------------------------------------------------------------
# Hardware
# ------------------------------------------------------------------
pixels = neopixel.NeoPixel(board.IO4, 5, brightness=LED_BRIGHTNESS,
                           auto_write=False)
pixels.fill((0, 0, 0)); pixels.show()


def _btn(pin):
    b = digitalio.DigitalInOut(pin)
    b.switch_to_input(pull=digitalio.Pull.UP)
    return b


flip_buttons = (_btn(board.IO1), _btn(board.IO2))
mode_button = _btn(board.IO43)

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
# Scenes -- one per side, built once
# ------------------------------------------------------------------
def solid_bg(color, w=128, h=160):
    bmp = displayio.Bitmap(w, h, 1)
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
    """Background + 128x128 image at image_y + centred text lines."""
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


scenes = []
tints = []
names = []
for style, bmp, line1, line2, accent in SIDES:
    # Each side costs about 19 KB -- a 128x128 image is 16 KB of it -- out of
    # roughly 150 KB. Four or five sides plus the radio is genuinely close to
    # the ceiling, so run out of memory by dropping sides and saying so,
    # rather than by dying at boot with a traceback nobody will see. The badge
    # showing two of your three sides is a far better failure than a badge
    # showing the CircuitPython console.
    try:
        if style == "photo":
            lines = ((line1, choose_scale(line1), 0xFFFFFF, 146),)
            scenes.append(build_scene(bmp, 0x000000, 4, lines))
        else:
            lines = ((line1, 1, accent, 143),)
            if line2:
                lines += ((line2, 1, 0x303030, 154),)
            scenes.append(build_scene(bmp, 0xFFFFFF, 6, lines))
    except MemoryError:
        gc.collect()
        print("out of memory building side %r -- dropping it and any after."
              " Fewer SIDES, or turn RADIO off, to get them all." % line1)
        break
    except OSError as ex:
        print("side %r: cannot read %s (%s) -- skipped" % (line1, bmp, ex))
        continue
    tints.append(((accent >> 16) & 0xFF, (accent >> 8) & 0xFF, accent & 0xFF))
    names.append(line1)

if not scenes:
    raise ValueError("no sides could be built -- check SIDES and /img")


# ------------------------------------------------------------------
# The screen: side, then module overlay, then banner
#
# One root group with three slots, rather than three self-contained scenes.
# Swapping a side is then a single assignment, and a module's graphics and
# the swarm banner survive the swap instead of being rebuilt -- which
# matters because rebuilding means a full repaint, and a full repaint is
# 87 ms of the loop.
# ------------------------------------------------------------------
BANNER_H = 40
BANNER_Y = 160 - BANNER_H

root = displayio.Group()
root.append(scenes[0])

mod_overlay = displayio.Group()
root.append(mod_overlay)

banner = displayio.Group()
banner.append(solid_bg(0x101018, 128, BANNER_H))
banner[0].y = BANNER_Y
banner_lines = []
for i in range(3):
    _lbl = label.Label(terminalio.FONT, text="", color=0xFFFFFF)
    _lbl.anchor_point = (0.0, 0.0)
    _lbl.anchored_position = (4, BANNER_Y + 5 + i * 12)
    banner.append(_lbl)
    banner_lines.append(_lbl)
banner.hidden = True
root.append(banner)

display.root_group = root


def set_banner(l1="", l2="", l3="", c1=0xFFFFFF):
    """Update the banner; return True if anything actually changed.

    The return value is the whole point. Assigning a label's text marks its
    box dirty even when the new text is identical, so a caller that refreshed
    unconditionally would spend ~20 ms per frame redrawing the same pixels
    and starve the radio to do it.
    """
    changed = False
    for lbl, text in zip(banner_lines, (l1, l2, l3)):
        if lbl.text != text:
            lbl.text = text
            changed = True
    if banner_lines[0].color != c1:
        banner_lines[0].color = c1
        changed = True
    want_hidden = not (l1 or l2 or l3)
    if banner.hidden != want_hidden:
        banner.hidden = want_hidden
        changed = True
    return changed


# ------------------------------------------------------------------
# Radio, peers, log
# ------------------------------------------------------------------
radio = None
if RADIO:
    try:
        # Collect first: the WiFi stack wants roughly 35 KB in one piece, and
        # this runs right after three 16 KB images were decoded into the heap.
        gc.collect()
        radio = bn.Radio(channel=bn.CHANNEL, buffer_size=8192,
                         tx_power=TX_POWER)
    except Exception as ex:
        # A badge with no radio is still a business card. Say so and carry
        # on, rather than dropping an attendee into the REPL.
        print("radio unavailable: %s %s" % (type(ex).__name__, ex))

my_mac = radio.mac if radio else b"\x00\x00\x00\x00\x00\x00"
peers = bn.PeerTable(ttl=25.0)

stats = None
if STATS:
    stats = bstats.Stats()
    stats.load()
    stats.tomb_label = POWER_LABEL
    stats.begin_session()


def radio_send(kind, body=b""):
    if radio is None:
        return False
    try:
        radio.send(kind, body)
        return True
    except Exception as ex:
        print("send failed: %s %s" % (type(ex).__name__, ex))
        return False


def mod_send(mod_id, payload):
    """What a module's ctx.send() ends up calling."""
    return radio_send(bx.MODMSG,
                      bytes((mod_id >> 8, mod_id & 0xFF)) + bytes(payload))


# ------------------------------------------------------------------
# Module runtime
# ------------------------------------------------------------------
runtime = badgemod.Runtime(overlay=mod_overlay, pixels=pixels, peers=peers,
                           send=mod_send, mac=my_mac)

# The escape hatch: both front buttons held at boot skips autoload.
skip_autoload = not (flip_buttons[0].value or flip_buttons[1].value)

if AUTOLOAD and not skip_autoload:
    runtime.autoload()


def shareables():
    """[(name, blob, flags)] we could offer -- .mod preferred, .py otherwise.

    A `.mod` is a host-built deflate stream, forwarded verbatim. A bare `.py`
    shares fine too, just uncompressed and over more frames, because the
    badge has no compressor and never will.
    """
    import os
    out = []
    try:
        files = sorted(os.listdir("/mods"))
    except OSError:
        return out
    stems = sorted({f[:-4] for f in files
                    if f.endswith(".mod") and not f.startswith(".")}
                   | {f[:-3] for f in files
                      if f.endswith(".py") and not f.startswith(".")})
    for stem in stems:
        try:
            with open("/mods/%s.mod" % stem, "rb") as f:
                out.append((stem, f.read(), bx.FLAG_DEFLATE))
                continue
        except OSError:
            pass
        try:
            with open("/mods/%s.py" % stem, "rb") as f:
                out.append((stem, f.read(), 0))
        except OSError:
            pass
    return out


# Modules caught this session, kept in RAM so they can still be relayed when
# the badge is tethered and could not write them to disk.
caught = []


def offerable():
    return shareables() + [(n, b, f) for n, b, f, _h in caught]


# ------------------------------------------------------------------
# Swarm state
# ------------------------------------------------------------------
NORMAL, LISTEN, SHARE = 0, 1, 2
MODE_NAMES = ("NORMAL", "LISTEN", "SHARE")

mode = NORMAL
receiver = bx.Receiver(send=radio_send,
                       ignore=[m.mod_id for m in runtime.mods])
sender = None
share_pick = 0
last_result = ""


def start_share(index):
    """Begin broadcasting one of our modules, or stop if there is nothing."""
    global sender, share_pick
    items = offerable()
    if not items:
        sender = None
        return None
    share_pick = index % len(items)
    name, blob, flags = items[share_pick]
    if len(blob) > bx.MAX_BLOB:
        print("%s is %d B, over the %d B cap" % (name, len(blob), bx.MAX_BLOB))
        sender = None
        return None
    # Anything we caught ourselves goes out with its hop count incremented,
    # so a receiver can see how far it has travelled.
    hops = 0
    for cname, _b, _f, chops in caught:
        if cname == name:
            hops = chops
    offer = bx.build_offer(name, blob, hops=hops, flags=flags)
    sender = bx.Sender(radio_send, offer, blob)
    print("sharing %s: %d B, %d chunks, %.2f s per lap"
          % (name, offer.total, offer.chunks, sender.lap_secs))
    return sender


def accept_offer(now):
    """Take the buffered module, run it, and keep it if we are allowed to."""
    global last_result
    receiver.accept()
    got = receiver.take()
    if got is None:
        if receiver.state == bx.FAILED:
            last_result = "%s failed: %s" % (
                receiver.offer.name if receiver.offer else "?", receiver.error)
        return False                    # still arriving; try again next pass
    offer, source, blob = got
    mod = runtime.load(source, name=offer.name)
    if mod is None:
        last_result = "%s would not run" % offer.name
        receiver.reset()
        return False
    kept = runtime.save(offer.name, source, blob)
    caught.append((offer.name, blob, offer.flags, offer.hops + 1))
    receiver.mine.add(offer.mod_id)
    last_result = "%s %s" % (offer.name, "kept" if kept else "in RAM (tethered)")
    print("accepted %s from %s, %d hops out -- %s"
          % (offer.name, bn.short_id(receiver.src_mac or b"\x00" * 6),
             offer.hops, "saved to /mods" if kept else "RAM only, USB attached"))
    receiver.reset()
    return True


def set_mode(new):
    global mode, sender
    if new == mode:
        return
    if mode == SHARE:
        sender = None
    mode = new
    if mode == SHARE:
        start_share(share_pick)
    elif mode == NORMAL:
        receiver.reset()
    print("mode:", MODE_NAMES[mode])


def next_side():
    global side, last_flip, need_draw
    side = (side + 1) % len(scenes)
    root[0] = scenes[side]
    last_flip = time.monotonic()
    need_draw = True


# ------------------------------------------------------------------
# Boot
# ------------------------------------------------------------------
side = 0
display.refresh()
bl.value = True

leds_on = LEDS_AT_BOOT
backlight_on = True
last_flip = time.monotonic()
last_touch = time.monotonic()
last_beacon = 0.0
last_draw = 0.0
last_lit = 0.0
led_drops = 0
need_draw = True
beacon_body = HANDLE.encode()[:bn.MAX_BODY]

runtime.arm()

print("ProfileCard: %d sides -- %s" % (len(scenes), " -> ".join(names)))
print("  radio %s  mods %s  stats %s"
      % ("%s ch%d tx%.0f" % (bn.mac_str(my_mac), bn.CHANNEL, radio.tx_power)
         if radio else "OFF",
         ",".join(m.name for m in runtime.mods) or "none",
         "on" if stats else "off"))
if skip_autoload:
    print("  SW1+SW2 were held at boot: /mods was NOT autoloaded")
if stats is not None and stats.tomb_secs:
    print("  previous session reached %s of uptime (power config %d)"
          % (bstats.hms(stats.tomb_secs), stats.tomb_label))
    print("  " + stats.summary())
print("  SW3 tap = mode, SW3 hold = LEDs, SW1/SW2 = side")
gc.collect()
print("  free RAM: %d" % gc.mem_free())


# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------
LONG_PRESS = 1.0

flip_prev = [b.value for b in flip_buttons]
mode_prev = mode_button.value
mode_down_at = 0.0
mode_handled = True          # ignore a button already held at boot

# The `finally` is not decoration. Leaving this loop -- Ctrl-C, an unexpected
# exception, a reload after saving the file -- leaves a RAISE-mode watchdog
# counting down with nobody feeding it, and it then fires into whatever runs
# next (the REPL, or the next boot's display setup, which hard faults). Any
# exit from the loop has to take the watchdog with it.
try:
    while True:
        now = time.monotonic()
        runtime.feed()

        # --- buttons ---
        # SW1/SW2 act on the press edge, so a flip feels instant. SW3 has to
        # wait for the release to tell a tap from a hold.
        flip_now = [b.value for b in flip_buttons]
        flip_edge = any((not v) and p for v, p in zip(flip_now, flip_prev))
        flip_prev = flip_now

        mode_now = mode_button.value
        mode_tap = False
        if (not mode_now) and mode_prev:
            mode_down_at = now
            mode_handled = False
        elif (not mode_now) and not mode_handled and now - mode_down_at >= LONG_PRESS:
            # Fire the hold the moment it qualifies rather than on release: a
            # button that does nothing until you let go feels broken.
            leds_on = not leds_on
            if not leds_on:
                pixels.fill((0, 0, 0)); pixels.show()
            print("leds:", "on" if leds_on else "off")
            mode_handled = True
        elif mode_now and not mode_prev and not mode_handled:
            mode_tap = True
            mode_handled = True
        mode_prev = mode_now

        if flip_edge or mode_tap:
            last_touch = now
            if not backlight_on:
                bl.value = True
                backlight_on = True
                need_draw = True

        if mode_tap:
            set_mode((mode + 1) % 3)
            need_draw = True

        if flip_edge:
            if mode == LISTEN and receiver.state == bx.OFFERED:
                accept_offer(now)
            elif mode == SHARE:
                start_share(share_pick + 1)
            else:
                next_side()
            need_draw = True
        elif AUTO_FLIP_SECS > 0 and mode == NORMAL and now - last_flip >= AUTO_FLIP_SECS:
            next_side()

        # --- radio in ---
        if radio is not None:
            for mac, kind, body, rssi, _t in radio.poll():
                if kind == bn.HELLO:
                    handle = ""
                    try:
                        handle = body.decode()[:12]
                    except Exception:
                        pass                      # garbled handle; keep the id
                    peers.observe(mac, rssi, now=now, handle=handle)
                    if stats is not None:
                        stats.observe(mac, rssi, now=now, handle=handle)
                elif kind == bx.MODMSG and len(body) >= 2:
                    runtime.deliver((body[0] << 8) | body[1], mac,
                                    bytes(body[2:]), rssi)
                elif kind == bx.REQ and sender is not None:
                    sender.on_req(body)
                elif kind in (bx.OFFER, bx.DATA) and mode == LISTEN:
                    # Only while listening: a badge showing its card should not
                    # be quietly buffering code nobody asked it about.
                    receiver.on_frame(mac, kind, body, rssi, now=now)
            peers.age(now)

        # --- radio out: exactly one frame per pass, always ---
        if radio is not None:
            if mode == SHARE and sender is not None:
                sender.tick(now)
            elif now - last_beacon >= BEACON_SECS:
                radio_send(bn.HELLO, beacon_body)
                last_beacon = now

        # A transfer accepted mid-flight finishes here.
        if receiver.state == bx.RECEIVING and receiver.complete:
            accept_offer(now)
        if mode == LISTEN:
            receiver.tick(now)

        # --- modules ---
        if runtime.tick(now):
            need_draw = True

        # --- banner ---
        if mode == NORMAL:
            changed = set_banner()
        elif mode == LISTEN:
            if receiver.state == bx.OFFERED:
                o = receiver.offer
                src = receiver.src_mac or b"\x00" * 6
                who = (peers.label(peers.peers[src]) if src in peers.peers
                       else bn.short_id(src))
                changed = set_banner(
                    "GET %s?" % o.name[:12],
                    "%s %.1fK hop%d %d%%" % (who[:8], o.total / 1024.0, o.hops,
                                             int(receiver.progress * 100)),
                    "SW1/SW2 yes  SW3 no", 0x00FF99)
            elif receiver.state == bx.FAILED:
                changed = set_banner("TRANSFER FAILED", (receiver.error or "")[:20],
                                     "SW3 to carry on", 0xFF6666)
            else:
                near = len(peers.nearby(now))
                changed = set_banner(
                    "LISTENING",
                    last_result or "%d badge%s near" % (near, "" if near == 1 else "s"),
                    "SW3 for SHARE", 0x00CCFF)
        else:
            if sender is None:
                changed = set_banner("NOTHING TO SHARE", "put a .py in /mods",
                                     "SW3 to carry on", 0xFFCC00)
            else:
                changed = set_banner(
                    "SHARING %s" % sender.offer.name[:11],
                    "lap %d  %d frames" % (sender.laps, sender.frames),
                    "SW1/SW2 pick  SW3 off", 0xFFCC00)
        if changed:
            need_draw = True

        # --- display: only when something changed, and at most ~6 fps ---
        # A full repaint is 87 ms and even a banner is 20. Refreshing every pass
        # would starve the radio and the buttons for no visible benefit.
        if need_draw and now - last_draw > 0.15:
            display.refresh()
            last_draw = now
            need_draw = False

    

        # --- LEDs ---
        # A module that asked for the pixels owns them completely; the card's
        # own breathe would otherwise fight it every frame.
        #
        # Rate limited to LED_HZ and wrapped, for the same measured reason as
        # in mods/syncflash.py: every NeoPixel write allocates from the
        # ESP-IDF heap, that heap is tight with WiFi up, and a write really
        # does fail with espidf.MemoryError sometimes. A lost LED frame is
        # invisible; an exception here would stop the badge polling buttons.
        if (runtime.pixel_owner is None and leds_on
                and now - last_lit >= 1.0 / LED_HZ):
            last_lit = now
            lvl = 0.25 + 0.75 * ((math.sin(now * 1.4) + 1) / 2)
            if mode == LISTEN:
                r, g, b = (0, int(120 * lvl), int(200 * lvl))
            elif mode == SHARE:
                r, g, b = (int(220 * lvl), int(160 * lvl), 0)
            else:
                r, g, b = tints[side]
                r, g, b = int(r * lvl), int(g * lvl), int(b * lvl)
            try:
                pixels.fill((r, g, b))
                pixels.show()
            except MemoryError:
                led_drops += 1

        # --- the log ---
        # One nvm write a minute, and never while a carousel is running: 65 ms of
        # flash erase mid-transfer is several dropped frames.
        if stats is not None:
            stats.flush(now, allow=(mode != SHARE
                                    and receiver.state != bx.RECEIVING))

        time.sleep(0.02 if (leds_on or mode != NORMAL) else 0.06)

except KeyboardInterrupt:
    print("interrupted")
finally:
    # Whatever happened, the watchdog must not outlive the loop, and the
    # log should not lose the last minute of company just because the badge
    # was unplugged or interrupted.
    runtime.disarm()
    if stats is not None:
        try:
            stats.flush(time.monotonic(), force=True)
        except Exception as ex:
            print("final flush failed: %s %s" % (type(ex).__name__, ex))
    dropped = led_drops + sum(m.ctx.led_drops for m in runtime.mods)
    if dropped:
        print("LED frames lost to memory pressure: %d" % dropped)
    pixels.fill((0, 0, 0)); pixels.show()
