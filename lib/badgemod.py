"""
badgemod.py -- cooperative background modules for the badge
===========================================================
First-party module (not part of the Adafruit bundle -- see lib/NOTICES.md).
Runs small Python modules -- authored locally or caught off the air from
another badge -- alongside whatever the badge is already showing.

There is no `asyncio` and no `_thread` in this build (checked, not
assumed), so "background" can only mean one thing: a `tick()` called from
the main loop. Everything here follows from that. A module cannot block,
cannot sleep, and cannot own the loop; it gets a few milliseconds per pass
and has to give them back.

The module contract
-------------------
    NAME = "syncflash"          # required; identity on the air
    VERSION = 1                 # optional
    WANTS_PIXELS = True         # optional; ask for the NeoPixels

    def setup(ctx): ...         # once, at load
    def tick(ctx, now): ...     # every loop pass; must return fast
    def teardown(ctx): ...      # once, at unload

Only NAME and tick() are required. Note what is missing: there is no
`draw()` returning a Group per frame. A module that wants pixels on screen
mutates `ctx.group` -- a `displayio.Group` the runtime owns and has already
put on screen. Returning a fresh Group each frame would reallocate
`displayio` objects at frame rate and fragment a 100 KB heap; mutating one
group in place is how `displayio` wants to be driven anyway.

Three guards, because a module is someone else's code
-----------------------------------------------------
1. A hardware watchdog in RAISE mode. This is the only preemption
   CircuitPython offers: a module that spins forever raises
   `WatchDogTimeout` into the VM instead of wedging the badge, and
   `Runtime.tick()` knows which module was running when it fired.
2. A time budget. Every `tick()` is timed; a module that repeatedly
   overruns is unloaded rather than left to starve the radio.
3. Exception quarantine. Anything out of `setup`/`tick`/`teardown` unloads
   that module and prints the traceback. One bad module must not take the
   badge down in the middle of a conference.

Deliberate omissions, so the gaps read as decisions:
  * No buttons in `ctx`. Modules are ambient -- LEDs, screen, radio -- and
    the three switches are already fully spoken for by the card UI.
  * No sandbox. `exec` into a fresh globals dict is namespace hygiene, not
    security. The security model is the button press that accepted the
    module, and the fact that any module can be unloaded from the badge.
"""

import gc
import time

import binascii

# Import the watchdog lazily-ish: this module has to import on CPython too,
# where the self-test runs and where `microcontroller` does not exist.
try:
    import watchdog as _watchdog
    from microcontroller import watchdog as _wd
except ImportError:                          # pragma: no cover -- CPython
    _watchdog = None
    _wd = None

# A module that overruns this many milliseconds in tick() collects a strike.
# The main loop aims for ~20 ms per pass, so 15 ms leaves the badge itself
# room to breathe.
BUDGET_MS = 15
STRIKES = 3

# Watchdog window. Generous on purpose: it is a backstop against an infinite
# loop, not a scheduler. A full-screen repaint alone is 87 ms, and a module
# transfer can hold the loop for a while, so anything under a second or two
# would fire on healthy code.
WATCHDOG_SECS = 8.0

MODS_DIR = "/mods"


def mod_id_for(name):
    """Stable 16-bit id for a module name.

    Every frame a module sends carries this, so two different modules
    sharing the air never read each other's mail. Derived rather than
    registered: no allocation table to keep in sync across badges, and two
    badges independently compute the same id for the same name.
    """
    return binascii.crc32(name.encode() if isinstance(name, str) else name) & 0xFFFF


class Ctx:
    """What a module is handed. One per module, alive for its whole load."""

    def __init__(self, name, mod_id, group, pixels=None, peers=None,
                 send=None, log=None, mac=b"\x00\x00\x00\x00\x00\x00"):
        self.name = name
        self.mod_id = mod_id
        self.group = group          # a displayio.Group, already on screen
        self.pixels = pixels        # NeoPixel object, or None if not granted
        self.peers = peers          # badgenet.PeerTable, or None
        self.mac = mac              # this badge's identity, for tie-breaking
        self.state = {}             # module scratch space
        self.inbox = []             # [(mac, payload, rssi)] since last tick
        self.dirty = False          # set True when `group` changed
        self.needs_radio = False    # set True to veto radio duty-cycling
        self.led_drops = 0          # LED frames lost to IDF-heap pressure
        self._send = send
        self._log = log

    def send(self, payload):
        """Broadcast `payload` to the same module on nearby badges.

        Returns False when there is no radio (running solo, or the host has
        the radio powered down). Modules must treat that as normal -- a
        badge on a desk has nobody to talk to, and the badge with no radio
        is the common case at a desk.
        """
        if self._send is None:
            return False
        return self._send(self.mod_id, payload)

    def log(self, *args):
        if self._log is not None:
            self._log(self.name, *args)
        else:
            print("[%s]" % self.name, *args)

    def show(self):
        """Push the pixel buffer, tolerating a transient out-of-memory.

        Modules should call this rather than `ctx.pixels.show()`.

        A NeoPixel write allocates from the **ESP-IDF** heap on every call,
        and with WiFi up that heap is under real pressure -- so `show()` can
        raise `espidf.MemoryError` while tens of kilobytes of *Python* heap
        remain free. Measured, not theorised: the first version of this badge
        died exactly that way after a few seconds of running SyncFlash with
        the radio on.

        A dropped LED frame is invisible at 30 Hz. A module unloaded for one,
        or a main loop killed by one, is not. The count is exposed rather
        than swallowed so the failure stays a fact instead of folklore.
        """
        if self.pixels is None:
            return False
        try:
            self.pixels.show()
            return True
        except MemoryError:
            # espidf.MemoryError subclasses the builtin, so this catches both.
            self.led_drops += 1
            return False


class Module:
    """One loaded module: its code, its ctx, and how well it is behaving."""

    def __init__(self, name, source, glb, ctx):
        self.name = name
        self.source = source        # kept: this is what we re-offer to peers
        self.mod_id = ctx.mod_id
        self.glb = glb
        self.ctx = ctx
        self.version = glb.get("VERSION", 0)
        self.ticks = 0
        self.worst_ms = 0.0
        self.total_ms = 0.0
        self.strikes = 0
        self.error = None

    @property
    def avg_ms(self):
        return self.total_ms / self.ticks if self.ticks else 0.0

    def __repr__(self):
        return "<mod %s v%d %d ticks avg %.1fms worst %.1fms>" % (
            self.name, self.version, self.ticks, self.avg_ms, self.worst_ms)


class Runtime:
    """Loads, ticks, budgets and unloads modules.

    Takes a `displayio.Group` to hang module graphics under and, optionally,
    the NeoPixels, the peer table and a send function. All four are optional
    so the runtime can be exercised host-side with none of them.
    """

    def __init__(self, overlay=None, pixels=None, peers=None, send=None,
                 log=None, mac=b"\x00\x00\x00\x00\x00\x00",
                 budget_ms=BUDGET_MS, strikes=STRIKES,
                 watchdog_secs=WATCHDOG_SECS):
        self.overlay = overlay
        self.pixels = pixels
        self.peers = peers
        self.mac = mac
        self.mods = []
        self.budget_ms = budget_ms
        self.max_strikes = strikes
        self.watchdog_secs = watchdog_secs
        self.pixel_owner = None
        self.unloaded = []          # [(name, reason)] -- for the UI to show
        self._send = send
        self._log = log
        self._armed = False

    # -- watchdog ---------------------------------------------------------
    def arm(self):
        """Arm the watchdog in RAISE mode. Safe to call when unavailable.

        The mode is cleared first, and the whole thing is wrapped, because of
        a sharp edge found the hard way: after a WatchDogTimeout has fired,
        assigning `timeout` while the mode is still RAISE raises
        `espidf.IDFError: Invalid argument`. Re-arming after a rescue is
        exactly when that happens, so an unguarded arm() turns "we saved the
        badge from a bad module" into "we crashed on the way back".
        """
        if _wd is None or self._armed:
            return False
        try:
            _wd.mode = None
            _wd.timeout = self.watchdog_secs
            _wd.mode = _watchdog.WatchDogMode.RAISE
        except Exception as ex:
            print("[badgemod] watchdog unavailable: %s %s" % (type(ex).__name__, ex))
            return False
        self._armed = True
        return True

    def feed(self):
        if self._armed:
            _wd.feed()

    def disarm(self):
        # There is no wd.deinit() on this build -- setting mode to None is
        # how you actually turn it off.
        if _wd is not None and self._armed:
            _wd.mode = None
            self._armed = False

    # -- loading ----------------------------------------------------------
    def load(self, source, name=None, want_pixels=None):
        """Compile and start a module. Returns the Module, or None on failure.

        Failure is a normal outcome -- the source came off a radio -- so this
        reports and returns rather than raising into the main loop.
        """
        if isinstance(source, (bytes, bytearray)):
            source = bytes(source).decode()

        glb = {"__name__": name or "mod"}
        try:
            exec(source, glb)
        except Exception as ex:
            self._note(name or "?", "compile: %s %s" % (type(ex).__name__, ex))
            return None

        name = glb.get("NAME") or name
        if not name:
            self._note("?", "no NAME")
            return None
        if self.get(name):
            self.unload(name, "replaced")

        group = None
        if self.overlay is not None:
            import displayio
            group = displayio.Group()
            self.overlay.append(group)

        # The NeoPixels have exactly one owner -- two modules both filling the
        # strip would fight every frame and look broken. The *newest* asker
        # wins, and the previous owner is told it lost them.
        #
        # Newest rather than first, deliberately: the common case is a module
        # someone has just accepted off another badge, and a module you chose
        # that then does nothing visible because an older one holds the strip
        # is indistinguishable from a transfer that failed.
        wants = glb.get("WANTS_PIXELS", False) if want_pixels is None else want_pixels
        pixels = None
        if wants and self.pixels is not None:
            previous = self.get(self.pixel_owner) if self.pixel_owner else None
            if previous is not None:
                previous.ctx.pixels = None
                print("[badgemod] %s took the pixels from %s"
                      % (name, previous.name))
            pixels = self.pixels

        ctx = Ctx(name, mod_id_for(name), group, pixels=pixels,
                  peers=self.peers, send=self._send, log=self._log,
                  mac=self.mac)
        mod = Module(name, source, glb, ctx)

        setup = glb.get("setup")
        if setup is not None:
            try:
                setup(ctx)
            except Exception as ex:
                self._note(name, "setup: %s %s" % (type(ex).__name__, ex))
                self._drop_group(group)
                return None

        if pixels is not None:
            self.pixel_owner = name
        self.mods.append(mod)
        gc.collect()
        return mod

    def load_file(self, path, **kw):
        try:
            with open(path) as f:
                return self.load(f.read(), name=path.rsplit("/", 1)[-1][:-3], **kw)
        except OSError as ex:
            self._note(path, "read: %s" % ex)
            return None

    def autoload(self, directory=MODS_DIR):
        """Load every .py in `directory`. Returns the modules that started.

        Missing directory is not an error -- a badge that has never caught
        anything simply has no /mods.
        """
        import os
        try:
            names = sorted(n for n in os.listdir(directory)
                           if n.endswith(".py") and not n.startswith("."))
        except OSError:
            return []
        # The dot filter is not paranoia. Copying a module onto CIRCUITPY from
        # a Mac leaves an AppleDouble sidecar next to it -- `._syncflash.py`,
        # which ends in .py and is a small binary file. Without this, the
        # first thing autoload does on a freshly loaded badge is try to
        # compile it.
        return [m for m in (self.load_file(directory + "/" + n) for n in names) if m]

    def get(self, name):
        for m in self.mods:
            if m.name == name:
                return m
        return None

    def unload(self, name, reason="unloaded"):
        mod = self.get(name) if isinstance(name, str) else name
        if mod is None:
            return False
        td = mod.glb.get("teardown")
        if td is not None:
            try:
                td(mod.ctx)
            except Exception as ex:
                print("[badgemod] %s teardown raised: %s" % (mod.name, ex))
        self._drop_group(mod.ctx.group)
        if self.pixel_owner == mod.name:
            self.pixel_owner = None
            if self.pixels is not None:
                # Wrapped because this runs from inside tick()'s error
                # handling, and a NeoPixel write can itself fail with
                # espidf.MemoryError under WiFi pressure. Unwrapped, the
                # cleanup for one dead module took down the whole main loop --
                # the badge stopped polling buttons and looked bricked.
                try:
                    self.pixels.fill((0, 0, 0))
                    self.pixels.show()
                except Exception as ex:
                    print("[badgemod] could not blank pixels: %s" % ex)
        self.mods.remove(mod)
        self._note(mod.name, reason)
        gc.collect()
        return True

    def unload_all(self, reason="unloaded"):
        for mod in list(self.mods):
            self.unload(mod, reason)

    # -- running ----------------------------------------------------------
    def tick(self, now):
        """Tick every loaded module. Returns True if any wants a redraw.

        Never raises: a module that throws, hangs or overruns is unloaded
        here and the badge carries on. That is the entire point of this
        method existing rather than the main loop calling modules directly.
        """
        self.feed()
        dirty = False
        for mod in list(self.mods):
            fn = mod.glb.get("tick")
            if fn is None:
                continue
            t0 = time.monotonic_ns()
            try:
                fn(mod.ctx, now)
            except Exception as ex:
                # WatchDogTimeout lands here too -- it is an Exception
                # subclass -- which is exactly what we want: the module that
                # was running when the watchdog fired is the one to blame.
                name = type(ex).__name__
                mod.error = "%s: %s" % (name, ex)
                # Everything in the handler is itself wrapped. Printing a
                # traceback allocates, unloading touches hardware, and both
                # run at the exact moment memory is scarcest -- so a failure
                # in here is likely, and a failure in here would propagate
                # into the main loop and stop the badge dead. That is what
                # happened on the first hardware run: a module hit an
                # out-of-memory, and cleaning up after it raised the same
                # error again, out of tick(), killing the loop.
                try:
                    try:
                        import traceback
                        traceback.print_exception(ex)
                    except Exception:
                        print("[badgemod] %s raised %s" % (mod.name, mod.error))
                    self.unload(mod, "crashed: %s" % name)
                    if name == "WatchDogTimeout":
                        # The watchdog is spent once it fires; re-arm so the
                        # next misbehaving module is caught too.
                        self._armed = False
                        self.arm()
                except Exception as ex2:
                    print("[badgemod] cleanup after %s also failed: %s"
                          % (mod.name, ex2))
                    if mod in self.mods:
                        self.mods.remove(mod)
                continue
            ms = (time.monotonic_ns() - t0) / 1000000.0
            mod.ticks += 1
            mod.total_ms += ms
            if ms > mod.worst_ms:
                mod.worst_ms = ms
            if ms > self.budget_ms:
                mod.strikes += 1
                if mod.strikes >= self.max_strikes:
                    self.unload(mod, "over budget (%.0fms)" % ms)
                    continue
            if mod.ctx.dirty:
                dirty = True
                mod.ctx.dirty = False
            mod.ctx.inbox = []
        return dirty

    def deliver(self, mod_id, mac, payload, rssi=0):
        """Hand a MODMSG to whichever module owns `mod_id`, if any.

        Unknown ids are dropped silently: hearing traffic for a module you
        don't have is the normal case in a room, not an error.
        """
        for mod in self.mods:
            if mod.mod_id == mod_id:
                mod.ctx.inbox.append((mac, payload, rssi))
                return True
        return False

    @property
    def needs_radio(self):
        """True if any loaded module wants the radio left continuously on."""
        return any(m.ctx.needs_radio for m in self.mods)

    # -- persistence ------------------------------------------------------
    def save(self, name, source, blob=None, directory=MODS_DIR):
        """Persist a module so it survives a reset. Returns True if it stuck.

        False is expected and not a failure: `storage.remount()` raises
        "Cannot remount path when visible via USB" whenever the badge is
        tethered, so a module accepted at a desk runs from RAM only. The
        conference case is battery, where this succeeds.

        `blob` is the compressed wire form, stored beside the source so a
        relay can forward the exact bytes it was handed instead of
        recompressing (which it has no compressor for anyway).
        """
        import os
        import storage
        try:
            storage.remount("/", False)
        except RuntimeError:
            return False
        try:
            try:
                os.mkdir(directory)
            except OSError:
                pass                        # already there
            with open("%s/%s.py" % (directory, name), "w") as f:
                f.write(source)
            if blob is not None:
                with open("%s/%s.mod" % (directory, name), "wb") as f:
                    f.write(blob)
            return True
        except OSError as ex:
            self._note(name, "save: %s" % ex)
            return False
        finally:
            try:
                storage.remount("/", True)
            except RuntimeError:
                pass

    # -- internals --------------------------------------------------------
    def _drop_group(self, group):
        if group is not None and self.overlay is not None:
            try:
                self.overlay.remove(group)
            except ValueError:
                pass

    def _note(self, name, reason):
        self.unloaded.append((name, reason))
        del self.unloaded[:-4]                  # keep the last few for the UI
        print("[badgemod] %s: %s" % (name, reason))
