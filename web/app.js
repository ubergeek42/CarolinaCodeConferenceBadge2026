// app.js -- build a badge payload in the browser and write it to CIRCUITPY.
//
// The whole point: no install, no toolchain. The browser already decodes JPEG
// and resizes images, so this needs less than flash.py does -- the only thing
// it can't do without help is talk to the badge, which the File System Access
// API handles by letting you point at the mounted drive.

import { SIZE, grayToBMP, qrToBMP } from "./bmp.js";
import { glyph, GLYPH_W, GLYPH_H, chooseScale } from "./font.js";
import { makeZip } from "./zip.js";

// Same minimum set flash.py copies. ProfileCard draws its captions with
// terminalio.FONT, which is frozen into the firmware, so adafruit_bitmap_font
// is deliberately absent.
const LIB_FILES = [
  "neopixel.mpy",
  "adafruit_pixelbuf.mpy",
  "adafruit_st7735r.mpy",
  "adafruit_display_text/__init__.mpy",
  "adafruit_display_text/label.mpy",
  "adafruit_imageload/__init__.mpy",
  "adafruit_imageload/displayio_types.mpy",
  "adafruit_imageload/bmp/__init__.mpy",
  "adafruit_imageload/bmp/indexed.mpy",
  "adafruit_imageload/bmp/truecolor.mpy",
  "adafruit_imageload/bmp/negative_height_check.mpy",
];

const CAPTION_MAX = 20;
const REPO_URL = "https://github.com/ubergeek42/CarolinaCodeConferenceBadge2026";

const $ = (id) => document.getElementById(id);
const state = { avatarGray: null, sides: [], files: null, user: null };

// ------------------------------------------------------------------
// Avatar
// ------------------------------------------------------------------
/**
 * Fetch a GitHub avatar and reduce it to 128x128 grayscale bytes.
 *
 * Uses avatars.githubusercontent.com rather than github.com/<user>.png: the
 * latter 302s through a response with no CORS header, so the browser blocks
 * it. The tradeoff is that an unknown username silently returns an identicon
 * instead of a 404 -- which is fine here, because the preview shows you
 * exactly what you're about to flash.
 */
async function fetchAvatarGray(user) {
  const url = `https://avatars.githubusercontent.com/${encodeURIComponent(user)}?size=256`;
  const resp = await fetch(url, { mode: "cors" });
  if (!resp.ok) throw new Error(`avatar fetch failed (HTTP ${resp.status})`);
  const bitmap = await createImageBitmap(await resp.blob());

  // Cover-crop to a square, the same framing as magick's
  // -resize '128x128^' -gravity center -extent 128x128.
  const canvas = new OffscreenCanvas(SIZE, SIZE);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const scale = Math.max(SIZE / bitmap.width, SIZE / bitmap.height);
  const w = bitmap.width * scale, h = bitmap.height * scale;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bitmap, (SIZE - w) / 2, (SIZE - h) / 2, w, h);

  const rgba = ctx.getImageData(0, 0, SIZE, SIZE).data;
  const gray = new Uint8Array(SIZE * SIZE);
  for (let i = 0; i < gray.length; i++) {
    const r = rgba[i * 4], g = rgba[i * 4 + 1], b = rgba[i * 4 + 2];
    gray[i] = (0.299 * r + 0.587 * g + 0.114 * b) | 0;   // Rec.601 luma
  }
  return levelStretch(gray);
}

/**
 * Normalize then clip, mirroring `-normalize -level '12%,90%'`.
 * A 1.77" panel viewed across a table has far less usable contrast than a
 * screen does, so pushing the histogram out matters more than fidelity.
 */
function levelStretch(gray) {
  let lo = 255, hi = 0;
  for (const v of gray) { if (v < lo) lo = v; if (v > hi) hi = v; }
  const span = Math.max(1, hi - lo);
  const black = 0.12 * 255, white = 0.90 * 255;
  const range = white - black;
  const out = new Uint8Array(gray.length);
  for (let i = 0; i < gray.length; i++) {
    const norm = ((gray[i] - lo) * 255) / span;          // -normalize
    out[i] = Math.max(0, Math.min(255, ((norm - black) * 255) / range)) | 0;
  }
  return out;
}

// ------------------------------------------------------------------
// Preview -- drawn with the badge's own font, at the badge's own layout
// ------------------------------------------------------------------
function drawText(img, text, scale, color, cy) {
  const w = text.length * GLYPH_W * scale;
  const x0 = ((128 - w) / 2) | 0;
  const y0 = (cy - (GLYPH_H * scale) / 2) | 0;
  for (let i = 0; i < text.length; i++) {
    const rows = glyph(text[i]);
    for (let r = 0; r < GLYPH_H; r++) {
      for (let c = 0; c < GLYPH_W; c++) {
        if (!(rows[r] & (1 << (5 - c)))) continue;
        for (let dy = 0; dy < scale; dy++) {
          for (let dx = 0; dx < scale; dx++) {
            const x = x0 + (i * GLYPH_W + c) * scale + dx;
            const y = y0 + r * scale + dy;
            if (x >= 0 && x < 128 && y >= 0 && y < 160) {
              const p = (y * 128 + x) * 4;
              img.data[p] = color[0]; img.data[p + 1] = color[1];
              img.data[p + 2] = color[2]; img.data[p + 3] = 255;
            }
          }
        }
      }
    }
  }
}

/** Render one 128x160 side into a canvas, exactly as code.py composes it. */
function renderSide(canvas, side, pixels, palette) {
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(128, 160);
  const [style, , line1, line2, accent] = side;
  const bg = style === "photo" ? [0, 0, 0] : [255, 255, 255];
  for (let i = 0; i < 128 * 160; i++) {
    img.data[i * 4] = bg[0]; img.data[i * 4 + 1] = bg[1];
    img.data[i * 4 + 2] = bg[2]; img.data[i * 4 + 3] = 255;
  }
  const imageY = style === "photo" ? 4 : 6;
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const [r, g, b] = palette[pixels[y * SIZE + x]];
      const p = ((y + imageY) * 128 + x) * 4;
      img.data[p] = r; img.data[p + 1] = g; img.data[p + 2] = b;
    }
  }
  const rgb = (v) => [(v >> 16) & 255, (v >> 8) & 255, v & 255];
  if (style === "photo") {
    drawText(img, line1, chooseScale(line1), [255, 255, 255], 146);
  } else {
    drawText(img, line1, 1, rgb(accent), 143);
    if (line2) drawText(img, line2, 1, [48, 48, 48], 154);
  }
  ctx.putImageData(img, 0, 0);
}

// ------------------------------------------------------------------
// Build
// ------------------------------------------------------------------
function normalizeLinkedIn(value) {
  let v = value.trim().replace(/\/+$/, "");
  v = v.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/^linkedin\.com\//, "");
  v = v.replace(/^\/+/, "");
  if (!v.startsWith("in/")) v = "in/" + v;
  return { url: `https://linkedin.com/${v}`, caption: v };
}

function caption(text, what, warnings) {
  if (text.length > CAPTION_MAX) {
    warnings.push(`${what} caption trimmed to ${CAPTION_MAX} characters (it would run off the panel)`);
    return text.slice(0, CAPTION_MAX);
  }
  return text;
}

function profileSource(sides) {
  const rows = sides.map(([style, img, l1, l2, accent]) =>
    `    ("${style}", "${img}", "${l1}", "${l2}", 0x${accent.toString(16).toUpperCase().padStart(6, "0")}),`);
  return `"""badge_profile.py -- written by the web flasher. Edit freely; it is just data.

ProfileCard imports SIDES from here when present, so re-flashing never
rewrites your code.py.
"""

SIDES = (
${rows.join("\n")}
)
`;
}

const GRAY_PAL = Array.from({ length: 256 }, (_, i) => [i, i, i]);
const QR_PAL = [[255, 255, 255], [0, 0, 0]];

/** Turn form input into finished files plus preview data. */
async function build() {
  const user = $("github").value.trim();
  if (!user) throw new Error("a GitHub username is required");

  const warnings = [];
  const files = new Map();
  const previews = [];

  status(`fetching github.com/${user}'s avatar…`);
  const gray = await fetchAvatarGray(user);
  files.set("img/avatar.bmp", grayToBMP(gray));

  const handle = caption(($("handle").value.trim() || user).toUpperCase(), "handle", warnings);
  const sides = [["photo", "/img/avatar.bmp", handle, "", 0xFFC878]];
  previews.push({ pixels: gray, palette: GRAY_PAL });

  const li = $("linkedin").value.trim();
  if (li) {
    const { url, caption: cap } = normalizeLinkedIn(li);
    const { bmp, modules, scale } = qrToBMP(url);
    files.set("img/qr.bmp", bmp);
    sides.push(["qr", "/img/qr.bmp", "LINKEDIN", caption(cap, "linkedin", warnings), 0x0A66C2]);
    previews.push(qrPreview(url));
    note(`LinkedIn QR: ${modules} modules at ${scale} px`);
  }

  const ghUrl = `https://github.com/${user}`;
  files.set("img/github.bmp", qrToBMP(ghUrl).bmp);
  sides.push(["qr", "/img/github.bmp", "GITHUB", caption(user, "github", warnings), 0x8250DF]);
  previews.push(qrPreview(ghUrl));

  if ($("repo").checked) {
    files.set("img/repo.bmp", qrToBMP(REPO_URL).bmp);
    sides.push(["qr", "/img/repo.bmp", "BADGE CODE", "make your own", 0x2DA44E]);
    previews.push(qrPreview(REPO_URL));
  }

  files.set("badge_profile.py", new TextEncoder().encode(profileSource(sides)));

  // All twelve at once. Sequentially this is twelve round trips, which is a
  // visible stall on GitHub Pages and a painful one on conference wifi.
  status("fetching badge code…");
  const wanted = [["code.py", "../samples/ProfileCard/code.py"],
                  ...LIB_FILES.map((n) => [`lib/${n}`, `../lib/${n}`])];
  const fetched = await Promise.all(wanted.map(([, url]) => fetchBinary(url)));
  wanted.forEach(([dest], i) => files.set(dest, fetched[i]));

  state.files = files;
  state.sides = sides;
  state.user = user;
  showPreview(sides, previews);
  warnings.forEach(note);
  return files;
}

function qrPreview(url) {
  // Re-derive the module grid for the preview rather than decoding the BMP.
  const { bmp } = qrToBMP(url);
  const pixels = new Uint8Array(SIZE * SIZE);
  const offset = 14 + 40 + 1024;
  for (let y = 0; y < SIZE; y++) {
    pixels.set(bmp.subarray(offset + (SIZE - 1 - y) * SIZE, offset + (SIZE - y) * SIZE),
               y * SIZE);
  }
  return { pixels, palette: QR_PAL };
}

async function fetchBinary(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`could not load ${path} (HTTP ${r.status})`);
  return new Uint8Array(await r.arrayBuffer());
}

function showPreview(sides, previews) {
  const host = $("preview");
  host.innerHTML = "";
  sides.forEach((side, i) => {
    const c = document.createElement("canvas");
    c.width = 128; c.height = 160;
    c.className = "screen";
    host.appendChild(c);
    renderSide(c, side, previews[i].pixels, previews[i].palette);
  });
  $("previewWrap").hidden = false;
}

// ------------------------------------------------------------------
// Writing to the badge
// ------------------------------------------------------------------
async function writeToBadge(files, force = false) {
  const root = await window.showDirectoryPicker({ mode: "readwrite" });

  // Gate on boot_out.txt, not the volume name: the name is user-changeable,
  // and picking the wrong folder should fail loudly rather than scatter 16
  // files into someone's Documents.
  let bootOut;
  try {
    const fh = await root.getFileHandle("boot_out.txt");
    bootOut = (await (await fh.getFile()).text()).split("\n")[0];
  } catch {
    throw new Error(
      "that folder has no boot_out.txt, so it isn't a CircuitPython badge. " +
      "Pick the drive called CIRCUITPY.");
  }
  note(`badge: ${bootOut}`);

  // Write only what differs, and never destroy work we didn't create. A file
  // may be overwritten if it is identical anyway, if the manifest says we
  // wrote it last time, or if it is an unmodified file from the repo (the
  // Launcher that ships as code.py, or the sample itself). Anything else is
  // treated as the owner's and left alone.
  const manifest = await readManifest(root);
  const stock = await stockHashes();
  const newManifest = {};
  let written = 0, skipped = 0, bytes = 0, seen = 0;
  const preserved = [];

  for (const [path, data] of files) {
    status(`checking… ${++seen}/${files.size}`);
    const parts = path.split("/");
    const name = parts[parts.length - 1];

    let dir = root;
    for (const part of parts.slice(0, -1)) {
      dir = await dir.getDirectoryHandle(part, { create: true });
    }

    const digest = await sha256(data);
    const existing = await readIfPresent(dir, name);
    if (existing) {
      if (await sha256(existing) === digest) {
        skipped++;
        newManifest[path] = digest;
        continue;
      }
      const have = await sha256(existing);
      if (!force && have !== manifest[path] && !stock.has(have)) {
        preserved.push(path);
        continue;
      }
    }

    const fh = await dir.getFileHandle(name, { create: true });
    const w = await fh.createWritable();
    await w.write(data);
    await w.close();
    newManifest[path] = digest;
    written++;
    bytes += data.length;
  }

  if (written) {
    const fh = await root.getFileHandle(MANIFEST, { create: true });
    const w = await fh.createWritable();
    await w.write(new TextEncoder().encode(
      JSON.stringify({ version: 1, files: newManifest }, null, 1)));
    await w.close();
  }
  return { written, skipped, bytes, preserved };
}

const MANIFEST = ".badge_flash.json";

async function sha256(data) {
  const d = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function readIfPresent(dir, name) {
  try {
    return new Uint8Array(await (await (await dir.getFileHandle(name)).getFile())
      .arrayBuffer());
  } catch {
    return null;                                    // not there yet
  }
}

async function readManifest(root) {
  const data = await readIfPresent(root, MANIFEST);
  if (!data) return {};
  try {
    return JSON.parse(new TextDecoder().decode(data)).files || {};
  } catch {
    return {};
  }
}

/**
 * Hashes of the repo files a badge is likely to already be carrying, so a
 * stock badge isn't mistaken for a customised one. flash.py scans the whole
 * repo for this; the page can only fetch what it knows the names of, so an
 * untouched *other* sample gets conservatively preserved rather than replaced.
 */
let _stock = null;
async function stockHashes() {
  if (_stock) return _stock;
  _stock = new Set();
  for (const p of ["../samples/Launcher/code.py", "../samples/ProfileCard/code.py"]) {
    try { _stock.add(await sha256(await fetchBinary(p))); } catch { /* optional */ }
  }
  return _stock;
}

// ------------------------------------------------------------------
// ZIP fallback for browsers without the directory picker
// ------------------------------------------------------------------
function downloadZip(files, user) {
  const blob = new Blob([makeZip(files)], { type: "application/zip" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `badge-${user}.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ------------------------------------------------------------------
// UI
// ------------------------------------------------------------------
function status(msg) { $("status").textContent = msg; }
function note(msg) {
  const li = document.createElement("li");
  li.textContent = msg;
  $("notes").appendChild(li);
}
function clearNotes() { $("notes").innerHTML = ""; }

function fail(err) {
  status("");
  const box = $("error");
  box.textContent = err.message || String(err);
  box.hidden = false;
  console.error(err);
}

async function onPreview() {
  clearNotes();
  $("error").hidden = true;
  try {
    await build();
    status(`ready — ${state.files.size} files, ${(totalBytes(state.files) / 1024).toFixed(1)} KB`);
    $("flash").disabled = false;
    $("zip").disabled = false;
  } catch (e) { fail(e); }
}

function totalBytes(files) {
  let n = 0;
  for (const d of files.values()) n += d.length;
  return n;
}

async function onFlash() {
  $("error").hidden = true;
  const force = $("force").checked;
  try {
    if (!state.files) await build();
    const { written, skipped, bytes, preserved } = await writeToBadge(state.files, force);
    status(written
      ? `done — ${written} files, ${(bytes / 1024).toFixed(1)} KB. The badge reloads on its own.`
      : (preserved.length ? "nothing written." : "already up to date — nothing needed writing."));
    if (skipped) note(`${skipped} files were already on the badge, unchanged (the stock badge ships the libraries).`);
    if (preserved.length) {
      note(`left alone, because they look like your own edits rather than mine: ${preserved.join(", ")}. ` +
           "Save copies somewhere, then tick “replace my edits” to overwrite them.");
      $("forceWrap").hidden = false;
    }
    note("SW1/SW2 step through the sides, SW3 toggles the LEDs.");
    note("Your details are in badge_profile.py on the badge — edit and save, CircuitPython reloads instantly.");
  } catch (e) {
    if (e.name === "AbortError") { status("cancelled"); return; }
    fail(e);
  }
}

async function onZip() {
  $("error").hidden = true;
  try {
    if (!state.files) await build();
    downloadZip(state.files, state.user);
    status("downloaded — unzip onto the CIRCUITPY drive, keeping the folders.");
  } catch (e) { fail(e); }
}

function init() {
  $("preview-btn").addEventListener("click", onPreview);
  $("flash").addEventListener("click", onFlash);
  $("zip").addEventListener("click", onZip);
  for (const id of ["github", "linkedin", "handle"]) {
    $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") onPreview(); });
  }

  if (!window.showDirectoryPicker) {
    $("flash").disabled = true;
    $("unsupported").hidden = false;
  }

  // ?gh=user&li=in/name&handle=TEXT&repo=1 prefills the form, and builds the
  // preview straight away when a username is supplied. Makes the page
  // shareable as a ready-made link ("here, flash your badge") and gives the
  // whole pipeline a way to be exercised without a human clicking.
  const q = new URLSearchParams(location.search);
  if (q.get("gh")) $("github").value = q.get("gh");
  if (q.get("li")) $("linkedin").value = q.get("li");
  if (q.get("handle")) $("handle").value = q.get("handle");
  if (q.get("repo")) $("repo").checked = true;
  if (q.get("gh")) onPreview();
}

init();
