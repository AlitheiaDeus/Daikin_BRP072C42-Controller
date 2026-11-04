const fs = require('fs');
const zlib = require('zlib');
const path = require('path');

const files = ['index.html', 'style.css', 'app.js'];

fs.mkdirSync('micropython/web', { recursive: true });

console.log("Pre-compressing web assets with gzip (Node.js)...");
let totalRaw = 0;
let totalGz = 0;

files.forEach(fname => {
  const src = path.join('web', fname);
  const data = fs.readFileSync(src);
  const rawSize = data.length;
  totalRaw += rawSize;

  const gzData = zlib.gzipSync(data, { level: 9 });
  const gzSize = gzData.length;
  totalGz += gzSize;

  const gzDstWeb = path.join('web', fname + '.gz');
  fs.writeFileSync(gzDstWeb, gzData);

  const dstMicro = path.join('micropython/web', fname);
  fs.copyFileSync(src, dstMicro);

  const gzDstMicro = path.join('micropython/web', fname + '.gz');
  fs.writeFileSync(gzDstMicro, gzData);

  const reduction = ((1 - gzSize / rawSize) * 100).toFixed(1);
  console.log(`  ${fname.padEnd(12)}: ${rawSize.toString().padStart(6)} B -> ${gzSize.toString().padStart(6)} B gz (${reduction}% reduction)`);
});

const totalReduction = ((1 - totalGz / totalRaw) * 100).toFixed(1);
console.log(`TOTAL: ${totalRaw} B -> ${totalGz} B gz (${totalReduction}% reduction)`);
console.log("Synced assets to micropython/web/ successfully!");
