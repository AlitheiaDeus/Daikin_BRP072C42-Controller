/**
 * @file benchmark_hub.js
 * @description Latency and throughput benchmark for XIAO ESP32-C5 Daikin Hub.
 * Simulates mobile client sequential and parallel asset loading over LAN.
 * 
 * Usage:
 *   node tools/benchmark_hub.js [hub_ip]
 */

const http = require('http');

const HUB_IP = process.argv[2] || process.env.HUB_IP || process.env.XIAO_IP || '192.168.1.100';

const headers = {
  'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Encoding': 'gzip',
  'Connection': 'close'
};

function fetchUrl(path) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const req = http.get(`http://${HUB_IP}${path}`, { headers, timeout: 8000 }, (res) => {
      let len = 0;
      res.on('data', chunk => len += chunk.length);
      res.on('end', () => resolve({
        path,
        status: res.statusCode,
        encoding: res.headers['content-encoding'] || 'identity',
        bytes: len,
        ms: Date.now() - t0
      }));
    });
    req.on('error', err => reject({ path, error: err.message, ms: Date.now() - t0 }));
    req.on('timeout', () => { req.destroy(); reject({ path, error: 'TIMEOUT', ms: Date.now() - t0 }); });
  });
}

async function run() {
  console.log(`=== Benchmarking Daikin Hub at http://${HUB_IP} ===\n`);

  console.log("1. Sequential Asset Load (Single-threaded client):");
  for (const p of ['/', '/style.css?v=3', '/app.js?v=3', '/api/devices']) {
    try {
      const res = await fetchUrl(p);
      console.log(`   [${res.status}] ${p.padEnd(20)} -> ${res.bytes.toString().padStart(6)} B [${res.encoding}] (${res.ms}ms)`);
    } catch (e) {
      console.log(`   [FAIL] ${p.padEnd(20)} -> ${e.error} (${e.ms}ms)`);
    }
  }

  console.log("\n2. Parallel Asset Load (Standard modern browser):");
  const endpoints = ['/', '/style.css?v=3', '/app.js?v=3', '/api/devices'];
  const promises = endpoints.map(p => fetchUrl(p));
  const results = await Promise.allSettled(promises);
  results.forEach((r, i) => {
    const p = endpoints[i];
    if (r.status === 'fulfilled') {
      const v = r.value;
      console.log(`   [${v.status}] ${p.padEnd(20)} -> ${v.bytes.toString().padStart(6)} B [${v.encoding}] (${v.ms}ms)`);
    } else {
      console.log(`   [FAIL] ${p.padEnd(20)} -> ${r.reason.error} (${r.reason.ms}ms)`);
    }
  });
}

run();
