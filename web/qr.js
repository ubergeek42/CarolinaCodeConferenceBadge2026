// qr.js -- QR generation, ported from adafruit_miniqr.
//
// SPDX-FileCopyrightText: 2009 Kazuhiko Arase
// SPDX-FileCopyrightText: 2021 ladyada for Adafruit Industries
// SPDX-License-Identifier: MIT
//
// Ported to JavaScript for this project from tools/adafruit_miniqr.py, which
// is itself ladyada's CircuitPython port of Kazuhiko Arase's original
// JavaScript. Kept deliberately faithful so the browser and flash.py emit
// byte-identical codes -- there is a test that checks exactly that.
//
// "QR Code" is a registered trademark of DENSO WAVE INCORPORATED.

export const ECC_M = 0, ECC_L = 1, ECC_H = 2, ECC_Q = 3;

const MODE_8BIT_BYTE = 1 << 2;
const PAD0 = 0xEC, PAD1 = 0x11;

// GF(256) log/exp tables, generated rather than transcribed. Verified to
// match the tables embedded in adafruit_miniqr byte for byte.
const EXP = new Uint8Array(256);
const LOG = new Uint8Array(256);
(function buildTables() {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;          // primitive polynomial
  }
  EXP[255] = EXP[0];
  for (let i = 0; i < 255; i++) LOG[EXP[i]] = i;
})();

function glog(n) {
  if (n < 1) throw new Error("glog(" + n + ")");
  return LOG[n];
}

function gexp(n) {
  while (n < 0) n += 255;
  while (n >= 256) n -= 255;
  return EXP[n];
}

const RS_BLOCK_TABLE = [
  [1,26,16], [1,26,19], [1,26,9], [1,26,13],
  [1,44,28], [1,44,34], [1,44,16], [1,44,22],
  [1,70,44], [1,70,55], [2,35,13], [2,35,17],
  [2,50,32], [1,100,80], [4,25,9], [2,50,24],
  [2,67,43], [1,134,108], [2,33,11,2,34,12], [2,33,15,2,34,16],
  [4,43,27], [2,86,68], [4,43,15], [4,43,19],
  [4,49,31], [2,98,78], [4,39,13,1,40,14], [2,32,14,4,33,15],
  [2,60,38,2,61,39], [2,121,97], [4,40,14,2,41,15], [4,40,18,2,41,19],
  [3,58,36,2,59,37], [2,146,116], [4,36,12,4,37,13], [4,36,16,4,37,17],
];

const PATTERN_POSITION_TABLE = [
  [], [6,18], [6,22], [6,26], [6,30],
  [6,34], [6,22,38], [6,24,42], [6,26,46], [6,28,50],
];

const G15 = 0b10100110111;
const G18 = 0b1111100100101;
const G15_MASK = 0b101010000010010;

function bchDigit(data) {
  let digit = 0;
  while (data !== 0) { digit++; data >>>= 1; }
  return digit;
}

function bchTypeInfo(data) {
  let d = data << 10;
  while (bchDigit(d) - bchDigit(G15) >= 0) d ^= G15 << (bchDigit(d) - bchDigit(G15));
  return ((data << 10) | d) ^ G15_MASK;
}

function bchTypeNumber(data) {
  let d = data << 12;
  while (bchDigit(d) - bchDigit(G18) >= 0) d ^= G18 << (bchDigit(d) - bchDigit(G18));
  return (data << 12) | d;
}

function getMask(mask, i, j) {
  switch (mask) {
    case 0: return (i + j) % 2 === 0;
    case 1: return i % 2 === 0;
    case 2: return j % 3 === 0;
    case 3: return (i + j) % 3 === 0;
    case 4: return (Math.floor(i / 2) + Math.floor(j / 3)) % 2 === 0;
    case 5: return ((i * j) % 2) + ((i * j) % 3) === 0;
    case 6: return (((i * j) % 2) + ((i * j) % 3)) % 2 === 0;
    case 7: return (((i * j) % 3) + ((i + j) % 2)) % 2 === 0;
    default: throw new Error("bad mask pattern: " + mask);
  }
}

class Polynomial {
  constructor(num, shift) {
    if (!num.length) throw new Error("empty polynomial");
    let offset = 0;
    while (offset < num.length && num[offset] === 0) offset++;
    this.num = new Array(num.length - offset + shift).fill(0);
    for (let i = 0; i < num.length - offset; i++) this.num[i] = num[i + offset];
  }
  get(i) { return this.num[i]; }
  get length() { return this.num.length; }
  multiply(other) {
    const num = new Array(this.length + other.length - 1).fill(0);
    for (let i = 0; i < this.length; i++) {
      for (let j = 0; j < other.length; j++) {
        num[i + j] ^= gexp(glog(this.get(i)) + glog(other.get(j)));
      }
    }
    return new Polynomial(num, 0);
  }
}

function errorCorrectPolynomial(eccLength) {
  let poly = new Polynomial([1], 0);
  for (let i = 0; i < eccLength; i++) poly = poly.multiply(new Polynomial([1, gexp(i)], 0));
  return poly;
}

function getRsBlocks(qrType, ecc) {
  const row = RS_BLOCK_TABLE[(qrType - 1) * 4 + ecc];
  const blocks = [];
  for (let i = 0; i < row.length / 3; i++) {
    const count = row[i * 3], total = row[i * 3 + 1], data = row[i * 3 + 2];
    for (let c = 0; c < count; c++) blocks.push({ total, data });
  }
  return blocks;
}

class BitBuffer {
  constructor() { this.buffer = []; this.length = 0; }
  putBit(bit) {
    const i = Math.floor(this.length / 8);
    if (this.buffer.length <= i) this.buffer.push(0);
    if (bit) this.buffer[i] |= 0x80 >>> (this.length % 8);
    this.length++;
  }
  put(num, length) {
    for (let i = 0; i < length; i++) this.putBit((num >>> (length - i - 1)) & 1);
  }
}

// A module is true (dark), false (light) or null (not yet set). The null state
// is load-bearing: data placement skips modules already claimed by the
// function patterns, so "unset" has to be distinguishable from "light".
class Matrix {
  constructor(size) {
    this.size = size;
    this.cells = new Array(size * size).fill(null);
  }
  get(r, c) { return this.cells[r * this.size + c]; }
  set(r, c, v) { this.cells[r * this.size + c] = v; }
}

function createData(qrType, ecc, data) {
  const rsBlocks = getRsBlocks(qrType, ecc);
  const buffer = new BitBuffer();

  buffer.put(MODE_8BIT_BYTE, 4);
  buffer.put(data.length, 8);
  for (const byte of data) buffer.put(byte, 8);

  let totalDataCount = 0;
  for (const b of rsBlocks) totalDataCount += b.data;

  if (buffer.length > totalDataCount * 8) {
    throw new Error("code length overflow: " + buffer.length + " > " + totalDataCount * 8);
  }
  if (buffer.length + 4 <= totalDataCount * 8) buffer.put(0, 4);
  while (buffer.length % 8 !== 0) buffer.putBit(false);
  for (;;) {
    if (buffer.length >= totalDataCount * 8) break;
    buffer.put(PAD0, 8);
    if (buffer.length >= totalDataCount * 8) break;
    buffer.put(PAD1, 8);
  }
  return createBytes(buffer, rsBlocks);
}

function createBytes(buffer, rsBlocks) {
  let offset = 0, maxDc = 0, maxEc = 0;
  const dcdata = new Array(rsBlocks.length);
  const ecdata = new Array(rsBlocks.length);

  for (let r = 0; r < rsBlocks.length; r++) {
    const dcCount = rsBlocks[r].data;
    const ecCount = rsBlocks[r].total - dcCount;
    maxDc = Math.max(maxDc, dcCount);
    maxEc = Math.max(maxEc, ecCount);

    dcdata[r] = new Array(dcCount).fill(0);
    for (let i = 0; i < dcCount; i++) dcdata[r][i] = 0xFF & buffer.buffer[i + offset];
    offset += dcCount;

    const rsPoly = errorCorrectPolynomial(ecCount);
    let modPoly = new Polynomial(dcdata[r], rsPoly.length - 1);

    for (;;) {
      if (modPoly.length - rsPoly.length < 0) break;
      const ratio = glog(modPoly.get(0)) - glog(rsPoly.get(0));
      const num = new Array(modPoly.length);
      for (let i = 0; i < modPoly.length; i++) num[i] = modPoly.get(i);
      for (let i = 0; i < rsPoly.length; i++) num[i] ^= gexp(glog(rsPoly.get(i)) + ratio);
      modPoly = new Polynomial(num, 0);
    }

    ecdata[r] = new Array(rsPoly.length - 1).fill(0);
    for (let i = 0; i < ecdata[r].length; i++) {
      const idx = i + modPoly.length - ecdata[r].length;
      ecdata[r][i] = idx >= 0 ? modPoly.get(idx) : 0;
    }
  }

  let totalCodeCount = 0;
  for (const b of rsBlocks) totalCodeCount += b.total;

  const out = new Array(totalCodeCount);
  let index = 0;
  for (let i = 0; i < maxDc; i++) {
    for (let r = 0; r < rsBlocks.length; r++) {
      if (i < dcdata[r].length) out[index++] = dcdata[r][i];
    }
  }
  for (let i = 0; i < maxEc; i++) {
    for (let r = 0; r < rsBlocks.length; r++) {
      if (i < ecdata[r].length) out[index++] = ecdata[r][i];
    }
  }
  return out;
}

/**
 * Build a QR matrix for a string.
 * Returns { size, get(row, col) -> bool } with the same module layout
 * adafruit_miniqr produces for the same input.
 */
export function makeQR(text, ecc = ECC_L, maskPattern = 0) {
  const data = new TextEncoder().encode(text);

  // Smallest version that holds the payload. Note the + 12: byte mode on
  // versions 1-9 spends 4 bits on the mode and 8 on the length, and miniqr's
  // own auto-select forgets them -- so a URL landing exactly on a version
  // boundary (54 characters, say) overflows instead of stepping up. flash.py
  // computes this the same way, which is what keeps the two byte-identical.
  const needBits = 12 + data.length * 8;
  let qrType = null;
  for (let t = 1; t < 10; t++) {
    let capacity = 0;
    for (const b of getRsBlocks(t, ecc)) capacity += b.data;
    if (capacity * 8 >= needBits) { qrType = t; break; }
  }
  if (qrType === null) {
    throw new Error("too much data for a QR of version 1-9 (" + data.length + " bytes)");
  }

  const size = qrType * 4 + 17;
  const m = new Matrix(size);

  const probe = (row, col) => {
    for (let r = -1; r < 8; r++) {
      if (row + r <= -1 || size <= row + r) continue;
      for (let c = -1; c < 8; c++) {
        if (col + c <= -1 || size <= col + c) continue;
        const on = (r >= 0 && r <= 6 && (c === 0 || c === 6))
                || (c >= 0 && c <= 6 && (r === 0 || r === 6))
                || (r >= 2 && r <= 4 && c >= 2 && c <= 4);
        m.set(row + r, col + c, on);
      }
    }
  };
  probe(0, 0);
  probe(size - 7, 0);
  probe(0, size - 7);

  // alignment patterns
  const pos = PATTERN_POSITION_TABLE[qrType - 1];
  for (const row of pos) {
    for (const col of pos) {
      if (m.get(row, col) !== null) continue;
      for (let r = -2; r < 3; r++) {
        for (let c = -2; c < 3; c++) {
          m.set(row + r, col + c,
                Math.abs(r) === 2 || Math.abs(c) === 2 || (r === 0 && c === 0));
        }
      }
    }
  }

  // timing patterns
  for (let r = 8; r < size - 8; r++) {
    if (m.get(r, 6) !== null) continue;
    m.set(r, 6, r % 2 === 0);
  }
  for (let c = 8; c < size - 8; c++) {
    if (m.get(6, c) !== null) continue;
    m.set(6, c, c % 2 === 0);
  }

  // format info
  const bits = bchTypeInfo((ecc << 3) | maskPattern);
  for (let i = 0; i < 15; i++) {
    const mod = ((bits >> i) & 1) === 1;
    if (i < 6) m.set(i, 8, mod);
    else if (i < 8) m.set(i + 1, 8, mod);
    else m.set(size - 15 + i, 8, mod);
  }
  for (let i = 0; i < 15; i++) {
    const mod = ((bits >> i) & 1) === 1;
    if (i < 8) m.set(8, size - i - 1, mod);
    else if (i < 9) m.set(8, 15 - i - 1 + 1, mod);
    else m.set(8, 15 - i - 1, mod);
  }
  m.set(size - 8, 8, true);

  // version info (type 7+)
  if (qrType >= 7) {
    const vbits = bchTypeNumber(qrType);
    for (let i = 0; i < 18; i++) {
      const mod = ((vbits >> i) & 1) === 1;
      m.set(Math.floor(i / 3), (i % 3) + size - 8 - 3, mod);
      m.set((i % 3) + size - 8 - 3, Math.floor(i / 3), mod);
    }
  }

  // data, snaking up and down two columns at a time
  const codewords = createData(qrType, ecc, data);
  let inc = -1, row = size - 1, bitIdx = 7, byteIdx = 0;
  for (let colBase = size - 1; colBase > 0; colBase -= 2) {
    // Skip the vertical timing pattern in column 6, but only for this pass --
    // the column sequence itself must stay on its fixed 2-step stride. (In the
    // Python original this falls out of iterating a range; in JS, decrementing
    // the loop variable would shift every later column and quietly corrupt the
    // payload. It still scans, because error correction papers over it.)
    const col = colBase === 6 ? 5 : colBase;
    for (;;) {
      for (let c = 0; c < 2; c++) {
        if (m.get(row, col - c) === null) {
          let dark = false;
          if (byteIdx < codewords.length) dark = ((codewords[byteIdx] >> bitIdx) & 1) === 1;
          if (getMask(maskPattern, row, col - c)) dark = !dark;
          m.set(row, col - c, dark);
          bitIdx--;
          if (bitIdx === -1) { byteIdx++; bitIdx = 7; }
        }
      }
      row += inc;
      if (row < 0 || size <= row) { row -= inc; inc = -inc; break; }
    }
  }

  return { size, get: (r, c) => !!m.get(r, c) };
}
