# Third-Party Library Notices

This directory bundles the CircuitPython libraries the badge samples depend on. All are distributed by Adafruit Industries under the MIT License. The badge project ships them precompiled (`.mpy`) or as source (`.py`), unmodified from the upstream releases in the [Adafruit CircuitPython Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle).

Full text of the license used by every library below is included at the bottom of this file. Each upstream repository contains its own `LICENSE` file — follow the links for the authoritative copy.

## Bundled Libraries

| Library on disk                       | Upstream repository                                                       | License |
|---------------------------------------|---------------------------------------------------------------------------|---------|
| `adafruit_bitmap_font/`               | https://github.com/adafruit/Adafruit_CircuitPython_Bitmap_Font            | MIT     |
| `adafruit_display_text/`              | https://github.com/adafruit/Adafruit_CircuitPython_Display_Text           | MIT     |
| `adafruit_imageload/`                 | https://github.com/adafruit/Adafruit_CircuitPython_ImageLoad              | MIT     |
| `adafruit_connection_manager.mpy`     | https://github.com/adafruit/Adafruit_CircuitPython_ConnectionManager      | MIT     |
| `adafruit_pixelbuf.mpy`               | https://github.com/adafruit/Adafruit_CircuitPython_Pixelbuf               | MIT     |
| `adafruit_requests.mpy`               | https://github.com/adafruit/Adafruit_CircuitPython_Requests               | MIT     |
| `adafruit_st7735r.mpy`                | https://github.com/adafruit/Adafruit_CircuitPython_ST7735R                | MIT     |
| `neopixel.mpy`                        | https://github.com/adafruit/Adafruit_CircuitPython_NeoPixel               | MIT     |

## First-Party Modules

`badgenet.py` is **not** a third-party library — it is part of this badge project and is covered by the repository's own [MIT license](../LICENSE). It lives in `lib/` because that is where CircuitPython looks for shared imports.

## Attribution

Copyright (c) Adafruit Industries and contributors. Individual copyright years and contributors are listed in each upstream repository's `LICENSE` and git history.

## MIT License (as applied to the bundled libraries)

```
The MIT License (MIT)

Copyright (c) Adafruit Industries

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
