// bmp.js -- 128x128 8-bit indexed uncompressed BMPs, the one image format
// adafruit_imageload reliably reads on this badge.
//
// Written by hand for the same reason flash.py does it: encoders pick their
// bit depth from the colour count, and a 1-bit or 4-bit BMP silently fails to
// load on the badge. This always emits 8-bit with a full 256-entry palette,
// byte-for-byte identical to what flash.py produces.

import { makeQR, ECC_L } from "./qr.js";

export const SIZE = 128;

/**
 * @param {Uint8Array} pixels  SIZE*SIZE palette indices, top row first
 * @param {Array<[number,number,number]>} palette  up to 256 RGB triples
 * @returns {Uint8Array} a complete .bmp file
 */
export function writeBMP8(pixels, palette) {
  if (pixels.length !== SIZE * SIZE) {
    throw new Error(`expected ${SIZE * SIZE} pixels, got ${pixels.length}`);
  }
  const palBytes = 256 * 4;
  const rowBytes = SIZE * SIZE;
  const offset = 14 + 40 + palBytes;
  const out = new Uint8Array(offset + rowBytes);
  const dv = new DataView(out.buffer);

  out[0] = 0x42; out[1] = 0x4D;                       // "BM"
  dv.setUint32(2, out.length, true);
  dv.setUint32(10, offset, true);
  dv.setUint32(14, 40, true);                         // BITMAPINFOHEADER
  dv.setInt32(18, SIZE, true);
  dv.setInt32(22, SIZE, true);
  dv.setUint16(26, 1, true);                          // planes
  dv.setUint16(28, 8, true);                          // bits per pixel
  dv.setUint32(30, 0, true);                          // BI_RGB, no compression
  dv.setUint32(34, rowBytes, true);
  dv.setInt32(38, 3780, true);
  dv.setInt32(42, 3780, true);
  dv.setUint32(46, 256, true);
  dv.setUint32(50, 256, true);

  for (let i = 0; i < 256; i++) {
    const [r, g, b] = palette[i] || [0, 0, 0];
    const p = 14 + 40 + i * 4;
    out[p] = b; out[p + 1] = g; out[p + 2] = r; out[p + 3] = 0;   // BGRA
  }

  // BMP rows run bottom-up.
  for (let y = 0; y < SIZE; y++) {
    out.set(pixels.subarray((SIZE - 1 - y) * SIZE, (SIZE - y) * SIZE),
            offset + y * SIZE);
  }
  return out;
}

const GRAY_PALETTE = Array.from({ length: 256 }, (_, i) => [i, i, i]);

/** Grayscale bytes (top row first) -> BMP. */
export function grayToBMP(gray) {
  return writeBMP8(gray, GRAY_PALETTE);
}

/**
 * Render a QR for `url` into a 128x128 BMP.
 * @returns {{bmp: Uint8Array, modules: number, scale: number}}
 */
/**
 * Write a 128x128 1-bit BMP. `pixels` is 1 for a dark module.
 *
 * A QR is two colours, so 8 bits per pixel wastes 14 KB on a badge with about
 * 45 KB free once its radio is up -- and that 14 KB is the difference between
 * four sides and three. adafruit_imageload reads 1-bit fine; verified on
 * hardware.
 */
export function writeBMP1(pixels) {
  if (pixels.length !== SIZE * SIZE) {
    throw new Error(`expected ${SIZE * SIZE} pixels, got ${pixels.length}`);
  }
  const palBytes = 2 * 4;
  const rowBytes = SIZE / 8;                          // 16, already 4-byte aligned
  const offset = 14 + 40 + palBytes;
  const out = new Uint8Array(offset + rowBytes * SIZE);
  const dv = new DataView(out.buffer);

  out[0] = 0x42; out[1] = 0x4D;                       // "BM"
  dv.setUint32(2, out.length, true);
  dv.setUint32(10, offset, true);
  dv.setUint32(14, 40, true);
  dv.setInt32(18, SIZE, true);
  dv.setInt32(22, SIZE, true);
  dv.setUint16(26, 1, true);                          // planes
  dv.setUint16(28, 1, true);                          // bits per pixel
  dv.setUint32(30, 0, true);                          // BI_RGB
  dv.setUint32(34, rowBytes * SIZE, true);
  dv.setInt32(38, 3780, true);
  dv.setInt32(42, 3780, true);
  dv.setUint32(46, 2, true);
  dv.setUint32(50, 2, true);

  // 0 = black, 1 = white, BGRA.
  out[14 + 40 + 3] = 0;
  out[14 + 40 + 4] = 255; out[14 + 40 + 5] = 255; out[14 + 40 + 6] = 255;

  for (let y = 0; y < SIZE; y++) {
    const dst = offset + (SIZE - 1 - y) * rowBytes;   // rows are bottom-up
    for (let x = 0; x < SIZE; x++) {
      if (!pixels[y * SIZE + x]) {                    // light pixel -> bit set
        out[dst + (x >> 3)] |= 0x80 >> (x & 7);
      }
    }
  }
  return out;
}

export function qrToBMP(url) {
  const qr = makeQR(url, ECC_L);
  const n = qr.size;

  // Widest module size that still leaves the 4 modules of quiet zone the spec
  // asks for. This is the difference between a code that scans across a table
  // and one that never scans at all.
  let scale = 0;
  for (let s = 1; s <= 8; s++) if ((n + 8) * s <= SIZE) scale = s;
  if (!scale) {
    throw new Error(`URL needs ${n} modules, too many for a 128 px code. Shorten it.`);
  }
  const pad = Math.floor((SIZE - n * scale) / 2);

  const pixels = new Uint8Array(SIZE * SIZE);
  for (let y = 0; y < SIZE; y++) {
    const my = Math.floor((y - pad) / scale);
    for (let x = 0; x < SIZE; x++) {
      const mx = Math.floor((x - pad) / scale);
      if (my >= 0 && my < n && mx >= 0 && mx < n && qr.get(my, mx)) {
        pixels[y * SIZE + x] = 1;
      }
    }
  }
  return {
    bmp: writeBMP1(pixels),                 // 1-bit: 2 KB, not 16 KB
    modules: n,
    scale,
  };
}
