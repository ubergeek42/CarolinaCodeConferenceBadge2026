// font.js -- terminalio's font (ter-u12n), printable ASCII only.
//
// The badge draws its captions with terminalio.FONT. Carrying the real glyphs
// means the preview in the browser is pixel-identical to the panel, rather
// than an approximation in whatever the system monospace happens to be.
//
// 95 glyphs (0x20-0x7E), 12 rows each, one byte per row, bit 5 = leftmost of
// the 6 px cell. Generated from ter-u12n.bdf; Terminus is SIL OFL licensed.

const PACKED =
  "AAAAAAAAAAAAAAAAAAAICAgICAAICAAAABQUFAAAAAAAAAAAAAAUFD4UFD4UFAAAAAAIHCooHAoq" +
  "HAgAAAASKhQECAoVEgAAAAAIFBQIGiQkGgAAAAgICAAAAAAAAAAAAAAECBAQEBAIBAAAAAAQCAQE" +
  "BAQIEAAAAAAAABQIPggUAAAAAAAAAAgIPggIAAAAAAAAAAAAAAAICBAAAAAAAAAAPgAAAAAAAAAA" +
  "AAAAAAAICAAAAAACAgQECAgQEAAAAAAcIiYqMiIiHAAAAAAIGAgICAgIHAAAAAAcIiICBAgQPgAA" +
  "AAAcIgIMAgIiHAAAAAACBgoSIj4CAgAAAAA+ICA8AgIiHAAAAAAcICA8IiIiHAAAAAA+AgIEBAgI" +
  "CAAAAAAcIiIcIiIiHAAAAAAcIiIiHgICHAAAAAAAAAgIAAAICAAAAAAAAAgIAAAICBAAAAAAAgQI" +
  "EAgEAgAAAAAAAD4AAD4AAAAAAAAAEAgEAgQIEAAAAAAcIiIECAAICAAAAAAcIiYqKiYgHgAAAAAc" +
  "IiIiPiIiIgAAAAA8IiI8IiIiPAAAAAAcIiAgICAiHAAAAAA4JCIiIiIkOAAAAAA+ICA8ICAgPgAA" +
  "AAA+ICA8ICAgIAAAAAAcIiAgLiIiHAAAAAAiIiI+IiIiIgAAAAAcCAgICAgIHAAAAAAOBAQEBCQk" +
  "GAAAAAAiJCgwMCgkIgAAAAAgICAgICAgPgAAAAAiNioqIiIiIgAAAAAiIjIqJiIiIgAAAAAcIiIi" +
  "IiIiHAAAAAA8IiIiPCAgIAAAAAAcIiIiIiIqHAIAAAA8IiIiPCgkIgAAAAAcIiAcAgIiHAAAAAA+" +
  "CAgICAgICAAAAAAiIiIiIiIiHAAAAAAiIiIUFBQICAAAAAAiIiIiKio2IgAAAAAiIhQICBQiIgAA" +
  "AAAiIhQUCAgICAAAAAA+AgQIECAgPgAAAAAcEBAQEBAQHAAAAAAQEAgIBAQCAgAAAAAcBAQEBAQE" +
  "HAAAAAgUIgAAAAAAAAAAAAAAAAAAAAAAAD4AEAgAAAAAAAAAAAAAAAAAABwCHiIiHgAAAAAgIDwi" +
  "IiIiPAAAAAAAABwiICAiHAAAAAACAh4iIiIiHgAAAAAAABwiPiAgHgAAAAAGCBwICAgICAAAAAAA" +
  "AB4iIiIiHgIcAAAgIDwiIiIiIgAAAAgIABgICAgIHAAAAAICAAYCAgICAhIMAAAQEBIUGBgUEgAA" +
  "AAAYCAgICAgIHAAAAAAAADwqKioqKgAAAAAAADwiIiIiIgAAAAAAABwiIiIiHAAAAAAAADwiIiIi" +
  "PCAgAAAAAB4iIiIiHgICAAAAAC4wICAgIAAAAAAAAB4gHAICPAAAAAAICBwICAgIBgAAAAAAACIi" +
  "IiIiHgAAAAAAACIiFBQICAAAAAAAACIiKioqHAAAAAAAACIUCAgUIgAAAAAAACIiIiIiHgIcAAAA" +
  "AD4ECBAgPgAAAAAGCAgQCAgIBgAAAAAICAgICAgICAAAAAAYBAQCBAQEGAAAABIqJAAAAAAAAAAA";

const DATA = Uint8Array.from(atob(PACKED), (c) => c.charCodeAt(0));

export const GLYPH_W = 6, GLYPH_H = 12;

/** Rows for one character: 12 bytes, bit 5 = leftmost pixel. */
export function glyph(ch) {
  const cp = ch.charCodeAt(0);
  if (cp < 0x20 || cp > 0x7E) return DATA.subarray(0, GLYPH_H);   // space
  return DATA.subarray((cp - 0x20) * GLYPH_H, (cp - 0x20 + 1) * GLYPH_H);
}

/** Width in pixels of a string at scale 1 -- the font is fixed pitch. */
export function textWidth(text) {
  return text.length * GLYPH_W;
}

/**
 * Largest scale (4..1) that keeps text within maxPx.
 * Mirrors choose_scale() in the badge's code.py.
 */
export function chooseScale(text, maxPx = 124) {
  for (const s of [4, 3, 2, 1]) if (text.length * GLYPH_W * s <= maxPx) return s;
  return 1;
}
