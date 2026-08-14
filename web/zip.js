// zip.js -- a minimal store-only ZIP writer.
//
// The fallback path for browsers without the directory picker (Firefox,
// Safari): you get a .zip to unpack onto CIRCUITPY. Store-only because the
// payload is 78 KB of already-compressed images and .mpy, so deflate would buy
// almost nothing and cost a dependency.

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(data) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < data.length; i++) c = CRC_TABLE[(c ^ data[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

/**
 * @param {Map<string, Uint8Array>} files  path -> bytes
 * @returns {Uint8Array} a complete .zip
 */
export function makeZip(files) {
  const enc = new TextEncoder();
  const parts = [];
  const central = [];
  let offset = 0;

  for (const [path, data] of files) {
    const name = enc.encode(path);
    const crc = crc32(data);

    const local = new Uint8Array(30 + name.length);
    const dv = new DataView(local.buffer);
    dv.setUint32(0, 0x04034b50, true);        // local file header
    dv.setUint16(4, 20, true);                // version needed
    dv.setUint16(6, 0, true);                 // flags
    dv.setUint16(8, 0, true);                 // method 0 = stored
    dv.setUint16(10, 0, true);                // mod time
    dv.setUint16(12, 0x21, true);             // mod date (1980-01-01)
    dv.setUint32(14, crc, true);
    dv.setUint32(18, data.length, true);
    dv.setUint32(22, data.length, true);
    dv.setUint16(26, name.length, true);
    local.set(name, 30);
    parts.push(local, data);

    const cd = new Uint8Array(46 + name.length);
    const cdv = new DataView(cd.buffer);
    cdv.setUint32(0, 0x02014b50, true);       // central directory header
    cdv.setUint16(4, 20, true);               // version made by
    cdv.setUint16(6, 20, true);               // version needed
    cdv.setUint16(10, 0, true);               // stored
    cdv.setUint16(14, 0x21, true);
    cdv.setUint32(16, crc, true);
    cdv.setUint32(20, data.length, true);
    cdv.setUint32(24, data.length, true);
    cdv.setUint16(28, name.length, true);
    cdv.setUint32(42, offset, true);          // offset of local header
    cd.set(name, 46);
    central.push(cd);

    offset += local.length + data.length;
  }

  const cdSize = central.reduce((n, c) => n + c.length, 0);
  const end = new Uint8Array(22);
  const edv = new DataView(end.buffer);
  edv.setUint32(0, 0x06054b50, true);         // end of central directory
  edv.setUint16(8, files.size, true);
  edv.setUint16(10, files.size, true);
  edv.setUint32(12, cdSize, true);
  edv.setUint32(16, offset, true);

  const total = offset + cdSize + end.length;
  const out = new Uint8Array(total);
  let p = 0;
  for (const chunk of [...parts, ...central, end]) { out.set(chunk, p); p += chunk.length; }
  return out;
}
