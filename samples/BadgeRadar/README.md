# BadgeRadar

Shows which other badges are near you. Every badge broadcasts a `HELLO` twice a second over ESP-NOW; this listens and plots each one it hears as a blip whose distance from the centre comes from signal strength. Copy this sample's `code.py` over the top-level `code.py` to run it (or pick it from the Launcher menu).

The shared radio plumbing lives in [`lib/badgenet.py`](../../lib/badgenet.py), so other badge-to-badge samples don't re-derive it.

## Controls

| Switch | Action |
|--------|--------|
| SW1 (IO1) | Switch view — RADAR / LIST |
| SW2 (IO2) | Toggle SIM mode |
| SW3 (IO43) | LEDs on/off |

The 5 NeoPixels double as a proximity meter for the closest badge: more pixels lit means closer, green through red as they drift away, and a slow blue breathe when nobody is around.

## SIM mode, and why it exists

**ESP-NOW has no loopback** — a lone badge hears absolutely nothing, not even its own broadcasts. This is confirmed on hardware, not assumed: sending five broadcasts and then draining the receive buffer yields zero packets. So with one badge, the radio can only ever be smoke-tested.

SIM mode substitutes `badgenet.SimRadio` for `badgenet.Radio`. It invents five neighbours whose signal strengths follow a momentum-biased random walk, so they drift in and out of range and get aged out of the table like real ones. That makes the peer table, the RSSI smoothing, the aging path, both views and the LED meter all exercisable on a single badge at a desk.

`SimRadio` and `Radio` present the same interface, so the sample swaps between them on one line and nothing downstream knows the difference. Set `START_IN_SIM = False`, or press SW2, once there is a second badge in the room.

## Tuning

```python
HANDLE       = "ubergeek42"   # broadcast to nearby badges
START_IN_SIM = True
BEACON_HZ    = 2.0            # HELLO broadcasts per second
FORGET_AFTER = 20.0           # seconds unseen before a badge drops off
TX_POWER     = 20.0           # 2.0 = arm's length, 20.0 = across the room
```

`TX_POWER` is the interesting one. The badge has no accelerometer, so there is no "bump to swap". Turning transmit power down to the 2 dBm floor shrinks the radio bubble until being in range *is* the proximity check — physically enforced rather than trusted. That is the lever a handshake sample should pull.

**The near/far calibration in `badgenet.rssi_to_unit()` is currently a guess** (−40 dBm ≙ touching, −85 dBm ≙ gone). RSSI is not a distance sensor; it is a signal-strength reading that correlates with distance on a good day, and bodies between two badges move it a lot. It needs measuring against a real second badge before any threshold built on it should be trusted.

## Verified ESP-NOW behaviour on this badge

Established by probing CircuitPython 10.2.1 on the actual hardware. Several of these contradict or are missing from the documentation, so they are recorded here rather than relearned.

| Behaviour | Reality |
|---|---|
| Module availability | `espnow` is built in — no library to install |
| CircuitPython version | Works on 10.2.1. **Broken on ESP32-S3 in 10.3.0-alpha.1 → alpha.3**; fixed in alpha.4. Don't upgrade casually |
| Payload cap | 250 bytes total, so 246 usable after framing |
| Oversized sends | **Silently accepted.** A 1000-byte broadcast raises nothing and increments no counter. `badgenet.encode()` enforces the limit because the library won't |
| Broadcast delivery feedback | **None.** Broadcast frames are never ACKed, so `send_success` and `send_failure` both stay 0 forever. Everything must tolerate loss |
| `send()` peer argument | Mandatory. Bare `e.send(msg)` raises `IDFError 0x3069` even with a broadcast peer registered |
| Receiving | Needs no peer registration; unknown MACs arrive fine, each with `.rssi` and `.time` |
| Peer list cap | **20 entries.** Registering a peer per badge you meet would fail after 19 people — hence broadcast plus in-payload addressing |
| Channel | Must match across badges. There is no `wifi.radio.channel`; the only way to pin it is bouncing a throwaway AP *before* constructing `ESPNow()` |
| `tx_power` | Valid 2.0–20.0 dBm. **Out-of-range writes are silently ignored** — no exception, value unchanged. Always read it back |
| Singleton | Second `ESPNow()` raises `RuntimeError: Already running`. `badgenet` rewraps this with a message that says what to do |
| RSSI per peer | No `peers_table` (that's MicroPython). RSSI is per received packet only; the peer table is built in Python |
| RX ring buffer | Defaults to 526 bytes — **two** packets. `badgenet` uses 2048 and exposes `.dropped`, because a display refresh can easily stall the loop past two packets |
| Known bug | The RX buffer is filled from the WiFi task on another core and is not multicore-safe. Once garbled, `read()` raises `ValueError` forever. `badgenet.Radio.poll()` catches it and reopens the radio, counting `.recoveries` |

## Code design

- **Three separable layers in `badgenet`** — `encode`/`decode`, `PeerTable`, and the transports. The split is not decoration: with no loopback, keeping the codec and peer table hardware-free is what makes them testable at all. Both have a 62-assertion self-test that passes on CPython *and* on the badge.
- **Addressing in the payload, not the radio** — the 20-peer cap makes per-person peer registration a dead end, so `pack_addressed()` puts a 6-byte target at the front of the body and receivers filter in Python. One registered peer (broadcast) forever, and it scales to a whole conference.
- **RSSI is smoothed** — raw dBm jumps several points packet to packet, which makes any fixed threshold chatter. `PeerTable` keeps an exponential moving average per badge.
- **Bearing is derived from the MAC**, not stored — `angle_for()` hashes the last three octets, so a badge always appears in the same direction with no state to age out.
- **One repainted bitmap, plus a fixed pool of labels** — the radar is drawn with `bitmaptools` into a single 6-colour bitmap. The six captions are allocated once and reused, because allocating labels per frame would fragment the heap.
- **Display throttled to ~6 fps** — a full repaint pushes about 40 KB over SPI at 8 MHz, so redrawing every loop would starve both the button polling and the receive buffer.
- **Blips stop at `R_MIN`** — mapping the strongest signal to radius 0 put the nearest badge directly on top of the "you are here" marker, making the two indistinguishable. Found by dumping the canvas off the badge and rendering it; the innermost ring is now reserved for you.

## Testing without a second badge

`lib/badgenet.py`'s pure layers have a 62-assertion self-test, [`test_badgenet.py`](test_badgenet.py), which runs unmodified on CPython and on the badge:

```sh
# on your computer, from lib/
python3 ../samples/BadgeRadar/test_badgenet.py

# or on the badge: copy it to the CIRCUITPY root, then from the REPL
exec(open("/test_badgenet.py").read())
# PASSED 62, FAILED 0
```

Running it on the badge as well as the host is not redundant. CircuitPython differs from CPython in ways this code touches — dicts are not insertion-ordered there, so anything depending on ordering has to sort explicitly, and the test pins that down.

The display can be verified too, without a camera — run the sample's setup up to its main loop, then dump the canvas back over serial and reconstruct it host-side:

```python
src = open("/samples/BadgeRadar/code.py").read()
exec(src.split("while True:")[0])          # real drawing code, no main loop
table.observe(bytes((2,0,0,0,0,1)), -38, now=0.0, handle="ada")
draw_radar(table.nearby(0.0), 0.0)
for y in range(H):
    print("".join(chr(48 + canvas[x, y]) for x in range(W)))
```

Map those digits through the palette into a PPM and you have a real screenshot of the badge's own rendering. Note it captures only the bitmap — `displayio` labels are composited separately and won't appear.

Re-running that in one REPL session fails with `ValueError: IO5 in use` until the previous run's pins are released; deinit `spi`, `pixels`, `bl`, `font_cs` and the buttons, and call `displayio.release_displays()` first.
