# mods/ — code that travels

A module in here runs in the background on the badge while it carries on being
a business card, and can be pushed to another badge over ESP-NOW. The runtime
is [`lib/badgemod.py`](../lib/badgemod.py); the transfer is
[`lib/badgexfer.py`](../lib/badgexfer.py).

## The contract

```python
NAME = "syncflash"          # required; identity on the air
VERSION = 1                 # optional
WANTS_PIXELS = True         # optional; ask for the NeoPixels

def setup(ctx): ...         # once, at load
def tick(ctx, now): ...     # every loop pass; must return in a few ms
def teardown(ctx): ...      # once, at unload
```

`ctx` carries `pixels`, `mac`, `peers`, `group` (a `displayio.Group` already on
screen — mutate it, don't rebuild it), `state` (scratch), `inbox` (messages
since the last tick), `send(payload)`, `show()`, `log()`, and two flags you may
set: `dirty` when your group changed, `needs_radio` to veto radio power saving.

Three rules the hardware imposes, all measured:

- **Never block.** No `asyncio`, no threads — `tick()` is called from the main
  loop, and a module that doesn't return is one the watchdog has to kill.
- **Use `ctx.show()`, not `ctx.pixels.show()`.** A NeoPixel write allocates from
  the ESP-IDF heap, which is tight with WiFi up, and really does fail with
  `espidf.MemoryError`. `ctx.show()` counts a lost frame instead of dying.
- **Write the LEDs at ~30 Hz, not every pass.** Past that you are only churning
  that same heap; the eye can't tell on five pixels.

## Comments cost airtime

This is the one style rule that is specific to `mods/`. **A module's source is
its wire payload**, so every byte of comment is broadcast on every hop, to every
badge, forever. Rationale belongs in this README; the module itself stays terse.

It is not a rounding error. SyncFlash with its design notes inline was 10,812
bytes of source — 4,416 compressed, 19 chunks, 0.30 s per carousel lap. The same
module with the prose moved here is a third of that. Same behaviour, a third of
the air.

Build the wire form with [`tools/mkmod.py`](../tools/mkmod.py), which also lints
for the mistakes above:

```sh
python3 tools/mkmod.py mods/syncflash.py --install
```

## Trying a transfer

**A badge will not offer you a module you already have.** The flashers install
`syncflash` on every badge, so two freshly flashed badges have nothing to send
each other — SHARE broadcasts, LISTEN hears it, and declines. It now says so
(`syncflash: have it`) instead of showing nothing, which is how this was found.

So put something on one badge that the other lacks:

```sh
python3 tools/mkmod.py mods/nearby.py --install   # sender only
```

Then SHARE on that badge, LISTEN on the other, and SW1/SW2 to accept. `nearby`
is two chunks — about 40 ms of air — so it lands as fast as you can look up.

"Already have it" is judged on the **bytes**, not the name: a newer build of a
module you are running has the same `mod_id` and will still be offered to you,
because otherwise a fix could never propagate past the first badge.

## nearby

One LED per badge within talking distance, up to five: blue, green, yellow,
orange, red. A crowd meter you can read without looking at the screen. Small on
purpose — it is the module to hand around when demonstrating that handing
modules around works, and at ~450 compressed bytes it is the cheapest useful
thing to put on the air.

**"Nearby" means above `NEAR_DBM`, not merely audible.** Version 1 counted every
badge it could hear, which on a desk with two badges looks perfect and in a room
with two hundred pins at five LEDs and tells you nothing — the radio reaches
much further at 20 dBm than "near" means to a person. It now filters on signal
strength, so the count tracks people you could actually talk to.

The threshold is a judgement, not a measurement: −60 dBm is roughly
conversational range on this hardware, but RSSI moves several dB when someone
steps between two badges, and `badgenet.rssi_to_unit()`'s calibration is still
unverified against a second badge. Treat the count as "a few people around me"
rather than a number. If you want the old behaviour, drop the `min_rssi`
argument; if the LEDs sit dark in a crowd, `NEAR_DBM` is too strict.

It also shows the pixel handover: accept it while `syncflash` is running and it
takes the strip, because **the newest module to ask for the LEDs wins**. A
module you just chose to accept that then does nothing visible is
indistinguishable from a transfer that failed, so ownership follows the most
recent decision rather than the oldest.

## syncflash

Badges near each other walk the same colour cycle at the same moment, so a knot
of people in a hallway reads as one organism from across the room. Walk away and
your badge drifts back to its own rhythm.

**Colour is the signal, not brightness.** Brightness modulation is nearly
invisible in a lit room unless the LEDs are painfully bright, and bright LEDs are
the biggest discretionary current draw on the board. Hue is legible across a room
at 8% brightness. Two badges showing the same hue at the same instant reads as
"these are together" immediately.

**One synced quantity.** `PERIOD` is the colour rotation (12 s), and the breath
(4 per rotation, so 3 s each) is derived from the same phase. An earlier version
synced a 3 s breath and cycled colour on a separate 12 s timer: badges agreed on
the breath, disagreed on the colour, and it looked worse than no sync at all.

**Lowest MAC in earshot wins.** Each badge broadcasts, twice a second, the MAC of
whichever badge it believes is the clock, how many hops away that badge is, and
where in the cycle it currently sits. Hear a lower MAC, adopt it; hear the same
clock from someone closer to it, tighten up to them. Sync therefore spreads
transitively — you follow a badge that can hear the clock even when you can't —
and the hop count is what stops two badges nudging each other in a circle.

The `hops + 1 <= st["hops"]` comparison is load-bearing and was a bug once.
Syncing only to badges *strictly* closer to the clock meant a badge's own
upstream never counted as contact, so every happily-synced badge dropped back to
its own rhythm every `CLOCK_TTL` seconds. `<=` both re-syncs (correcting drift
between two independent `monotonic()` clocks) and refreshes liveness.

**No round-trip correction**, deliberately. One-way airtime is 0.6 ms measured,
so the error is dominated by main-loop jitter — about 20 ms in a 12 s cycle,
under 0.2%. A handshake to fix that would cost more air than the rhythm itself.

The clock's MAC also shifts the group's start point on the colour wheel, so two
unsynced groups look different even when their cycles happen to align.

Everything except the radio hop is testable with no second badge:
[`samples/ProfileCard/test_badgemod.py`](../samples/ProfileCard/test_badgemod.py)
wires several SyncFlash instances to a fake bus with fake time and asserts they
converge — including under 33% packet loss, and in a chain where the far badge
only reaches the clock through a middle one.
