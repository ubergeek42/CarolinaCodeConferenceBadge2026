"""
autostart.py -- installed as the badge's top-level code.py by flash.py.

Boots straight into ProfileCard, and keeps the sample picker one button away:

    power on / reset            -> ProfileCard, immediately
    hold any button while it    -> the Launcher's picker, with ProfileCard
    boots                          listed alongside every other sample

Why a shim rather than just copying ProfileCard over code.py: that is what
this used to do, and it removed the picker from the boot path entirely. The
obvious alternative -- leave the Launcher as code.py and pre-select
ProfileCard -- is not possible from a computer. The Launcher remembers its
last pick in microcontroller.nvm, which lives in internal flash and is
reachable only from CircuitPython, so no host-side flasher can write it. With
nvm unset the Launcher falls back to the alphabetically first sample, which is
not this one.

Note this deliberately ignores the Launcher's remembered selection: the badge
comes back to being a business card on every reset, which is the point of it.
Picking another sample from the picker still runs that sample now; it just
does not become the new default. To go back to the stock behaviour, copy
samples/Launcher/code.py over the top-level code.py.
"""

import gc
import time
import board
import digitalio

PROFILECARD = "/samples/ProfileCard/code.py"
LAUNCHER = "/samples/Launcher/code.py"


def _any_button_held():
    """True if SW1, SW2 or SW3 is down right now.

    The switches are active-low with internal pull-ups, so the line needs a
    moment to settle after being configured -- reading immediately can catch
    it still floating and report a phantom press.
    """
    buttons = []
    for pin in (board.IO1, board.IO2, board.IO43):
        b = digitalio.DigitalInOut(pin)
        b.switch_to_input(pull=digitalio.Pull.UP)
        buttons.append(b)
    time.sleep(0.02)
    held = any(not b.value for b in buttons)
    # Hand the pins back: whatever we run next claims them itself, and a
    # second DigitalInOut on a live pin raises ValueError.
    for b in buttons:
        b.deinit()
    return held


def _run(path):
    """Run a sample, having first given back every byte we can spare.

    Compiling and then dropping the source matters more than it looks.
    ProfileCard is ~25 KB of text, and holding that string alive for the whole
    run costs it a side: each card is about 19 KB of image and there is only
    about 45 KB free once the radio is up. Booting through this shim without
    the `del` really did build one side instead of three.
    """
    with open(path) as f:
        source = f.read()
    code = compile(source, path, "exec")
    del source
    gc.collect()
    exec(code, {"__name__": "__main__", "__file__": path})


target = LAUNCHER if _any_button_held() else PROFILECARD

# If the intended target is missing -- someone deleted a folder, or the
# picker was never installed -- fall through to the other rather than
# dropping the attendee into a bare REPL.
try:
    open(target).close()
except OSError:
    target = PROFILECARD if target is LAUNCHER else LAUNCHER

print("autostart: running", target)
_run(target)
