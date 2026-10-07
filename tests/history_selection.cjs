const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const listeners = {};
const main = { innerHTML: '' };
const context = { document: {querySelector: () => main, addEventListener: (name, handler) => listeners[name] = handler},
  sessionStorage: {getItem: () => ''}, clearTimeout, setTimeout, URL, console,
  fetch: async url => ({ok: true, json: async () => ({total: 21, jobs: url.includes('offset=20') ? [job(0)] : Array.from({length: 20}, (_, i) => job(20 - i))})}) };
function job(n) { return {id: `job-${n}`, metadata: {station_id: '<script>bad()</script>'},
  created_at: '2026-10-07', status: 'completed', summary: {verdict: 'PASS', passed: 1, expected: 1}}; }
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/assets/app.js', 'utf8').replace(/\nboot\(\);\s*$/, ''), context);
async function choose(name, value, label) {
  await listeners.change({target: {name, value, selectedOptions: [{textContent: label}], closest: () => ({id: 'compare-form'})}});
}
(async () => {
  await vm.runInContext('history()', context);
  await choose('current', 'job-20', '<script>bad()</script> · newest');
  await vm.runInContext('S.offset=20; history()', context);
  assert.match(main.innerHTML, /value="job-20" selected/);
  await choose('baseline', 'job-0', '<script>bad()</script> · oldest');
  await vm.runInContext("S.offset=0; S.query='filtered'; history()", context);
  assert.match(main.innerHTML, /value="job-0" selected/);
  assert.match(main.innerHTML, /value="job-20" selected/);
  assert.ok(!main.innerHTML.includes('<script>bad()</script>'));
  console.log('PASS: comparison selections and escaped labels survive paging/filtering');
})().catch(e => {console.error(e); process.exitCode = 1;});
