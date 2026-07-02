// DOM smoke test for docs/ dashboard — verifies app.js renders without errors.
// Run: node scripts/test_dashboard_dom.js
// Mocks fetch('./data/dashboard.json') with the real file, then inspects rendered DOM.

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const REPO_ROOT = path.resolve(__dirname, '..');
const DOCS_DIR = path.join(REPO_ROOT, 'docs');
const INDEX_HTML = path.join(DOCS_DIR, 'index.html');
const APP_JS = path.join(DOCS_DIR, 'app.js');
const DASHBOARD_JSON = path.join(DOCS_DIR, 'data', 'dashboard.json');

let failures = 0;
function assert(cond, msg) {
  if (cond) {
    console.log('  PASS: ' + msg);
  } else {
    console.log('  FAIL: ' + msg);
    failures++;
  }
}

(async function main() {
  if (!fs.existsSync(DASHBOARD_JSON)) {
    console.log('FAIL: dashboard.json not found at ' + DASHBOARD_JSON);
    process.exit(1);
  }

  const html = fs.readFileSync(INDEX_HTML, 'utf8');
  const appJs = fs.readFileSync(APP_JS, 'utf8');
  const dashboardJson = fs.readFileSync(DASHBOARD_JSON, 'utf8');

  const dom = new JSDOM(html, {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'http://localhost/',
  });
  const { window } = dom;
  const { document } = window;

  // Mock fetch
  window.fetch = async (url) => {
    if (url === './data/dashboard.json') {
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => JSON.parse(dashboardJson),
      };
    }
    return { ok: false, status: 404, statusText: 'Not Found' };
  };

  // Run app.js in the window context via eval (runScripts: 'outside-only' allows window.eval)
  // app.js is an IIFE that checks document.readyState; if not 'loading', calls loadDashboard() directly.
  window.eval(appJs);

  // Wait for async loadDashboard to complete
  await new Promise((resolve) => setTimeout(resolve, 500));

  console.log('\n=== P3② error placeholder (should be hidden) ===');
  const errPlaceholder = document.getElementById('error-placeholder');
  assert(errPlaceholder.classList.contains('hidden'), 'error-placeholder has hidden class');
  const content = document.getElementById('content');
  assert(!content.classList.contains('hidden'), 'content is visible (no hidden class)');

  console.log('\n=== Header meta ===');
  const meta = document.getElementById('meta');
  assert(meta.innerHTML.includes('Neoverse-N2'), 'meta shows CPU model');
  assert(meta.innerHTML.includes('fabde3b'), 'meta shows llama commit');

  console.log('\n=== P2 Headline cards (3 expected) ===');
  const cards = document.getElementById('headline-cards').querySelectorAll('.card');
  assert(cards.length === 3, '3 headline cards rendered (got ' + cards.length + ')');
  if (cards.length === 3) {
    // Q8_0 card (first in order) — verdict=kai_wins
    const q8Card = cards[0];
    assert(q8Card.className.includes('verdict-kai_wins'), 'Q8_0 card has verdict-kai_wins class');
    assert(q8Card.textContent.includes('+12.46%') || q8Card.textContent.includes('+12.5%'), 'Q8_0 card shows +12.46% or +12.5%');
    assert(q8Card.textContent.includes('44.41'), 'Q8_0 card shows kai decode 44.41');

    // Q4_0 card (second) — verdict=tie
    const q4Card = cards[1];
    assert(q4Card.className.includes('verdict-tie'), 'Q4_0 card has verdict-tie class');
    assert(q4Card.textContent.includes('1853') && q4Card.textContent.includes('1964'), 'Q4_0 card shows kai_mem < repack_mem');

    // Q4_K_M card (third) — verdict=noop
    const qkmCard = cards[2];
    assert(qkmCard.className.includes('verdict-noop'), 'Q4_K_M card has verdict-noop class');
  }

  console.log('\n=== Speedup table (default Q4_K_M tab, 5 variants) ===');
  const speedupRows = document.querySelectorAll('#speedup-table tbody tr');
  assert(speedupRows.length === 5, '5 variant rows in speedup table (got ' + speedupRows.length + ')');
  if (speedupRows.length === 5) {
    const naiveRow = speedupRows[0];
    assert(naiveRow.className.includes('naive-row'), 'naive row has naive-row class');
    assert(naiveRow.textContent.includes('1.000×'), 'naive row shows 1.000× speedup');
    const bestRow = document.querySelector('#speedup-table tbody tr.best-row');
    assert(bestRow !== null, 'best variant row is highlighted');
    if (bestRow) {
      assert(bestRow.textContent.includes('★'), 'best row has star marker');
    }
  }

  console.log('\n=== Tab switch to Q8_0 ===');
  const tabs = document.querySelectorAll('#quant-tabs .tab');
  assert(tabs.length === 3, '3 quant tabs rendered (got ' + tabs.length + ')');
  if (tabs.length === 3) {
    // Click Q8_0 tab (third)
    const q8Tab = tabs[2];
    q8Tab.onclick();
    await new Promise((resolve) => setTimeout(resolve, 100));
    const q8Rows = document.querySelectorAll('#speedup-table tbody tr');
    assert(q8Rows.length === 5, 'Q8_0 tab shows 5 rows');
    const bestQ8 = document.querySelector('#speedup-table tbody tr.best-row');
    assert(bestQ8 !== null && bestQ8.textContent.includes('kleidiai_only'), 'Q8_0 best variant is kleidiai_only');
  }

  console.log('\n=== Decision table (markdown rendered) ===');
  const dt = document.getElementById('decision-table');
  assert(dt.querySelectorAll('table').length >= 1, 'decision table has at least one <table> (markdown rendered)');
  assert(dt.querySelectorAll('h2').length >= 1 || dt.querySelectorAll('h3').length >= 1, 'decision table has headers');
  assert(dt.textContent.includes('Q8_0'), 'decision table mentions Q8_0');
  assert(dt.textContent.includes('12.5%') || dt.textContent.includes('12.46'), 'decision table mentions KleidiAI win %');

  console.log('\n=== PMU summary ===');
  const pmu = document.getElementById('pmu-summary');
  assert(pmu.textContent.includes('armv8_pmuv3_0'), 'PMU summary mentions armv8_pmuv3_0');
  assert(pmu.textContent.includes('True') && pmu.textContent.includes('False'), 'PMU summary shows True/False values');
  assert(pmu.textContent.includes('SPE') || pmu.textContent.includes('Performix'), 'PMU summary mentions SPE/Performix');

  console.log('\n=== Probe matrix ===');
  const pm = document.getElementById('probe-matrix');
  const pmRows = pm.querySelectorAll('tbody tr');
  assert(pmRows.length === 5, 'probe matrix has 5 variant rows (got ' + pmRows.length + ')');
  assert(pm.querySelectorAll('th').length >= 4, 'probe matrix has header cells');

  console.log('\n=== Footnotes ===');
  const foot = document.querySelector('.footnotes');
  assert(foot.textContent.includes('NF4'), 'footnotes mention NF4');
  assert(foot.textContent.includes('TTFT'), 'footnotes mention TTFT');
  assert(foot.textContent.includes('PPL'), 'footnotes mention PPL');

  console.log('\n=== P3② error path (404 → placeholder) ===');
  // Now mock fetch to return 404 and re-run app.js IIFE (creates fresh closure, calls loadDashboard)
  window.fetch = async () => ({ ok: false, status: 404, statusText: 'Not Found', json: async () => { throw new Error('404'); } });
  window.eval(appJs);
  await new Promise((resolve) => setTimeout(resolve, 300));
  const errAfter = document.getElementById('error-placeholder');
  assert(!errAfter.classList.contains('hidden'), 'error-placeholder visible after 404');
  const contentAfter = document.getElementById('content');
  assert(contentAfter.classList.contains('hidden'), 'content hidden after 404');
  assert(errAfter.textContent.includes('暂无数据'), 'error placeholder shows "暂无数据"');
  assert(errAfter.textContent.includes('404'), 'error placeholder shows 404 status');

  console.log('\n=== Summary ===');
  if (failures === 0) {
    console.log('ALL PASS');
    process.exit(0);
  } else {
    console.log(failures + ' FAILURE(S)');
    process.exit(1);
  }
})().catch((e) => {
  console.error('TEST CRASHED:', e.stack || e);
  process.exit(2);
});
