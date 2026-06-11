// Render ../slh_icon.svg into the app icon assets:
//   build/icon.png  (512px, window icon + electron-builder source)
//   build/icon.ico  (multi-size, NSIS installer + exe icon)
//
// Usage: npm i --no-save sharp && node build-icon.js
// The .ico is assembled by hand (PNG-compressed entries) so we don't need
// another dependency.

const fs = require("fs");
const path = require("path");
const sharp = require("sharp");

const SVG = path.join(__dirname, "..", "slh_icon.svg");
const OUT = path.join(__dirname, "build");
const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256];

function icoFromPngs(pngs) {
  // ICONDIR + ICONDIRENTRY[] + png blobs (Vista+ supports PNG entries)
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); header.writeUInt16LE(1, 2);
  header.writeUInt16LE(pngs.length, 4);
  const entries = [];
  let offset = 6 + 16 * pngs.length;
  for (const { size, buf } of pngs) {
    const e = Buffer.alloc(16);
    e.writeUInt8(size === 256 ? 0 : size, 0); // 0 means 256
    e.writeUInt8(size === 256 ? 0 : size, 1);
    e.writeUInt8(0, 2); e.writeUInt8(0, 3);
    e.writeUInt16LE(1, 4);   // planes
    e.writeUInt16LE(32, 6);  // bpp
    e.writeUInt32LE(buf.length, 8);
    e.writeUInt32LE(offset, 12);
    offset += buf.length;
    entries.push(e);
  }
  return Buffer.concat([header, ...entries, ...pngs.map((p) => p.buf)]);
}

(async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const svg = fs.readFileSync(SVG);
  await sharp(svg, { density: 512 }).resize(512, 512).png()
    .toFile(path.join(OUT, "icon.png"));
  const pngs = [];
  for (const size of ICO_SIZES) {
    pngs.push({ size, buf: await sharp(svg, { density: 512 }).resize(size, size).png().toBuffer() });
  }
  fs.writeFileSync(path.join(OUT, "icon.ico"), icoFromPngs(pngs));
  console.log(`build/icon.png + build/icon.ico (${ICO_SIZES.join("/")}) written`);
})();
