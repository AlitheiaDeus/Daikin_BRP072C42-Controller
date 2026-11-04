/**
 * @file compress_assets.js
 * @description Pre-compresses frontend assets (HTML, CSS, JS) with Gzip (level 9)
 * and synchronizes them to micropython/web/ for microcontroller flash deployment.
 * 
 * Usage:
 *   node tools/compress_assets.js
 */

const fs = require('fs');
const zlib = require('zlib');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const webDir = path.join(repoRoot, 'web');
const microWebDir = path.join(repoRoot, 'micropython', 'web');

const files = ['index.html', 'style.css', 'app.js'];

fs.mkdirSync(microWebDir, { recursive: true });

console.log("Pre-compressing web assets with Gzip (Node.js)...");
let totalRaw = 0;
let totalGz = 0;

files.forEach(fname => {
  const src = path.join(webDir, fname);
  const data = fs.readFileSync(src);
  const rawSize = data.length;
  totalRaw += rawSize;

  const gzData = zlib.gzipSync(data, { level: 9 });
  const gzSize = gzData.length;
  totalGz += gzSize;

  const gzDstWeb = path.join(webDir, fname + '.gz');
  fs.writeFileSync(gzDstWeb, gzData);

  const dstMicro = path.join(microWebDir, fname);
  fs.copyFileSync(src, dstMicro);

  const gzDstMicro = path.join(microWebDir, fname + '.gz');
  fs.writeFileSync(gzDstMicro, gzData);

  const reduction = ((1 - gzSize / rawSize) * 100).toFixed(1);
  console.log(`  ${fname.padEnd(12)}: ${rawSize.toString().padStart(6)} B -> ${gzSize.toString().padStart(6)} B gz (${reduction}% reduction)`);
});

const totalReduction = ((1 - totalGz / totalRaw) * 100).toFixed(1);
console.log(`\nTOTAL: ${totalRaw} B -> ${totalGz} B gz (${totalReduction}% reduction)`);
console.log("Successfully synchronized pre-compressed assets to micropython/web/");
