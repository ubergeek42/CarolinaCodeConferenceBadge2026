# Carolina Code Conference 2026 Badge

The [Carolina Code Conference 2026](https://blog.carolina.codes/p/2026-circuit-board-badge) badge is yours to keep and hack — a full ESP32-S3 dev board designed by [Circuit Board Medics](https://circuitboardmedics.com). Plug it in, edit `code.py`, and CircuitPython runs your code the instant you save. No toolchain, no build step, no upload button.

![Meet your badge — CCC 2026 hardware overview](img/badge_infographic.png)

> **New to CircuitPython or the badge?** Jump into the [**15-minute tutorial**](TUTORIAL.md) — you'll go from "hello world" to blinking LEDs, reading a button, and drawing on the display.

The board ships with **CircuitPython 10.2.1** already flashed
([board build](https://circuitpython.org/board/espressif_esp32s3_devkitc_1_n8/)
· [additional libraries](https://circuitpython.org/libraries)).

## Getting Started

1. **Plug in.** Connect the badge to your computer with a USB-Micro cable.
   The badge appears as a USB drive named **`CIRCUITPY`** — on Windows it
   shows up in Explorer, on macOS on the desktop, on Linux under
   `/media/$USER/CIRCUITPY` (or `/run/media/$USER/CIRCUITPY`).
2. **Edit.** Open `code.py` on the `CIRCUITPY` drive in any text editor
   (Mu, Thonny, VS Code, or plain notepad). Change something, save.
3. **Watch it run.** CircuitPython automatically re-runs `code.py` on
   every save — no compiler, no upload button. If your code has a
   syntax error, you'll drop into the REPL.
4. **See `print()` output.** Open the USB serial console — see
   [`docs/SERIAL_CONSOLE.md`](docs/SERIAL_CONSOLE.md) for how, on every OS.

**Tip — work from a local clone if you'd like git history.** Editing
directly on the `CIRCUITPY` drive is the fastest workflow but has no
undo. If you'd rather revert / checkpoint / share your changes, clone
this repository to your computer, edit there, and copy files onto the
drive when you want to try them. The badge is your deployment target;
your laptop is the source of truth — the same shape as real embedded
firmware work.

## Project Layout

```
public/
├── code.py                   The active program on the badge. Ships preloaded
│                             with the launcher (see samples/Launcher/).
├── settings.toml.example     Copy to settings.toml and fill in your WiFi
│                             credentials (used by samples that call wifi.radio).
├── README.md                 This file — hardware reference + folder guide.
├── TUTORIAL.md               Beginner walkthrough: your first program on this badge.
├── LICENSE                   MIT license for badge code (bundled libs keep their own).
├── AGENTS.md                 CircuitPython patterns and gotchas for AI coding agents.
│
├── docs/
│   └── SERIAL_CONSOLE.md     How to open the USB serial console for print().
│
├── img/                      Shared images (BMPs loaded via adafruit_imageload).
│   ├── CarolinaCodeConference.bmp
│   ├── avatar.bmp            Profile photo for ProfileCard (128x128, 8-bit indexed).
│   └── qr.bmp                LinkedIn QR for ProfileCard (same format).
│
├── lib/                      CircuitPython libraries the samples import.
│   ├── adafruit_bitmap_font/
│   ├── adafruit_display_text/
│   ├── adafruit_imageload/
│   ├── adafruit_connection_manager.mpy
│   ├── adafruit_pixelbuf.mpy
│   ├── adafruit_requests.mpy
│   ├── adafruit_st7735r.mpy
│   ├── neopixel.mpy
│   └── NOTICES.md            Attribution + licenses for bundled libraries.
│
└── samples/                  Each folder is a standalone sample. Drop its
    │                         code.py at the top level of this directory to run
    │                         it on the badge (back up the existing code.py first).
    ├── CCCLogo/              Splash animation — BMP logo + backlight fade + LED bounce.
    ├── DVDBounce/            Bouncing "DVD" screensaver with color-shifting LED trail.
    ├── Launcher/             Boot-time sample picker. Preloaded as code.py — restore
    │                         from here after copying another sample over code.py.
    ├── LEDLab/               Pattern + palette + speed demo across 16 patterns.
    ├── MorseCode/            Tap Morse code on SW1 and it decodes live on the display.
    ├── Nameplate/            Conference nameplate — large font name + LED patterns.
    ├── ProfileCard/          Two-sided badge — photo + handle, any button flips to
    │                         a LinkedIn QR code.
    ├── Weather/              WiFi weather app (ZIP → forecast + LED dashboard).
    └── WiFiScanner/          Live WiFi scanner + signal-strength meter.
```

Each sample folder contains a `code.py` and a `README.md` explaining what the sample does, its controls, and a brief walkthrough of the code design.


### What's preloaded

The badge ships with the **Launcher** (`samples/Launcher/code.py`) already installed as the top-level `code.py`. On boot the display shows a picker with a 3-second countdown — press any button to enter the menu, or wait to auto-run your last selection. See [`samples/Launcher/README.md`](samples/Launcher/README.md) for the full behaviour.

### Running a sample

1. Open the sample's `code.py` (e.g. `samples/Weather/code.py`).
2. Copy it to the top-level `code.py`. Move or rename the current one first — otherwise you'll overwrite the launcher (or whatever sample is currently deployed).
3. CircuitPython auto-reloads on save.

To get the launcher back, do the same thing with `samples/Launcher/code.py`.

`settings.toml` holds shared WiFi credentials, so any sample that connects to the internet works without editing the source. Copy `settings.toml.example` to `settings.toml` on first setup and fill in your network.


## Hardware Specifications

### Microcontroller — ESP32-S3-WROOM-1-N8

Espressif module. Datasheet: <https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf>

|    Signal     |             Pin               |
|---------------|-------------------------------|
| USB D+        | GPIO20                        |
| USB D-        | GPIO19                        |
| Boot Button   | GPIO0                         |
| Reset Button  | EN (Enable pin)               |


### Display — HS180S10B (ST7735S driver)

> Note: the `adafruit_st7735r` library is compatible with the LCD's ST7735S driver chip.

|     Item    |       Specs        |
|-------------|--------------------|
| Mfr Part#   | HS180S10B          |
| LCSC Part#  | C5329585           |
| Driver IC   | ST7735S            |
| Resolution  | 160 × 128          |
| Size        | 1.77 inch diagonal |

#### Display Pin Mapping

|       LCD Pin         | ESP32 GPIO |                            Notes                           |
|-----------------------|------------|------------------------------------------------------------|
| SPI Clock (SCK)       | GPIO12     |                                                            |
| MOSI                  | GPIO11     | Data input to LCD from MCU                                 |
| Reset                 | GPIO7      | Active low                                                 |
| Data/Command (DC)     | GPIO6      | High = display data, Low = command register                |
| Chip Select (CS)      | GPIO10     | Active low                                                 |
| Backlight             | GPIO5      | High = on, Low = off; PWM-capable for brightness control   |
| FS0 (font chip MISO)  | GPIO44     | Font chip data output to MCU                               |
| Font Chip Select      | GPIO9      | Active Low                                                 |


### Addressable LEDs (NeoPixel / WS2812)

|   Item   |      Details       |
|----------|--------------------|
| Count    | 5                  |
| Data Pin | GPIO4              |
| Protocol | WS2812 (NeoPixel)  |


### Tactile Switches

The three on-board switches are wired to ground and use the ESP32-S3's
internal pull-up resistors, so the GPIO reads **HIGH when idle** and
**LOW when pressed** (active-low). In CircuitPython, configure them with
`digitalio.Pull.UP` — see [`AGENTS.md`](AGENTS.md) for the exact snippet.

| Switch |  GPIO  |
|--------|--------|
| SW 1   | GPIO1  |
| SW 2   | GPIO2  |
| SW 3   | GPIO43 |

### Broken-out GPIO

The remaining GPIO pins are broken out to through-hole solder pads. 2.54 mm pitch headers can be soldered to these pads for easy access. Each pad is labeled on the badge PCB silkscreen.

### Power System

Power is supplied through either the **USB Micro** connector or a **CR123A** battery. The ON/OFF switch controls only the battery — the badge stays on while USB is connected.

Power from the battery is automatically disconnected by the control circuitry when power is detected from the USB input, regardless of the ON/OFF switch position.

There is no on-board battery charging system. Turn the switch off or disconnect the battery when not in use.


## License

The badge code and documentation in this repository are released under the [MIT License](LICENSE) — copy, modify, and reuse freely with attribution.

Bundled third-party libraries under `lib/` retain their own licenses (all MIT). See [`lib/NOTICES.md`](lib/NOTICES.md) for per-library attribution and upstream sources.
