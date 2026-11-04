/**
 * @file test_dom_eval.js
 * @description Automated DOM integrity & syntax verification for Daikin Climate Hub.
 * Simulates a browser environment to verify that web/app.js executes against web/index.html
 * without throwing Uncaught TypeErrors (e.g. missing IDs, undefined listeners, broken selectors).
 * 
 * Usage:
 *   node tests/test_dom_eval.js
 */

const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..');
const htmlPath = path.join(repoRoot, 'web', 'index.html');
const jsPath = path.join(repoRoot, 'web', 'app.js');

const html = fs.readFileSync(htmlPath, 'utf8');
const js = fs.readFileSync(jsPath, 'utf8');

// Parse all IDs declared in index.html
const ids = new Set();
const idRegex = /id=["']([^"']+)["']/g;
let m;
while ((m = idRegex.exec(html)) !== null) {
  ids.add(m[1]);
}

const elements = {};
ids.forEach(id => {
  elements[id] = {
    id: id,
    textContent: '',
    innerHTML: '',
    className: '',
    classList: {
      add: () => {},
      remove: () => {},
      toggle: () => {},
      contains: () => false
    },
    style: {},
    addEventListener: () => {},
    querySelectorAll: () => [],
    querySelector: () => null,
    appendChild: () => {}
  };
});

global.window = {
  innerWidth: 1024,
  addEventListener: () => {},
  location: { hash: '' }
};
global.navigator = { userAgent: 'test-agent' };
global.document = {
  getElementById: (id) => elements[id] || null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => ({
    tagName: tag,
    className: '',
    innerHTML: '',
    style: {},
    classList: { add: () => {}, remove: () => {}, toggle: () => {} },
    appendChild: () => {},
    addEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => []
  }),
  addEventListener: () => {},
  hidden: false
};
global.localStorage = {
  getItem: () => null,
  setItem: () => {}
};
global.history = {
  pushState: () => {}
};
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ devices: [] }) });
global.setTimeout = (fn) => 1;
global.clearTimeout = () => {};
global.setInterval = (fn) => 1;
global.clearInterval = () => {};

try {
  eval(js);
  console.log("PASS: web/app.js executed against web/index.html with 0 errors.");
} catch (err) {
  console.error("FAIL: Runtime exception during web/app.js execution:", err);
  process.exit(1);
}
