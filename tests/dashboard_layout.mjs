// Dashboard layout + alert-noise tests.
//
// The dashboard had no browser coverage at all, which is how a banner that
// renders as a full-height column beside the sidebar survived: it only appears
// while an alert is live, so nobody looked. Every case here is a defect that
// actually shipped.
//
//   npm i -D playwright && npx playwright install chromium
//   node tests/dashboard_layout.mjs
//
// Skips cleanly (exit 0) when playwright is absent, so it never blocks anyone.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

let chromium;
try { ({ chromium } = await import('playwright')); }
catch { console.log('· dashboard layout test skipped (playwright not installed)'); process.exit(0); }

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MIME = { '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript' };

let pass = 0, fail = 0;
const ok = (c, why) => { if (c) { pass++; console.log('  ok   ' + why); }
                         else { fail++; console.log('  FAIL ' + why); } };

// ── a stub Stream Manager, just enough for the dashboard to render ────────
const S = { alerts: [], checks: [] };
const STATUS = {
  obs: { running: true, pid: 1, uptime: 10, streaming: false, recording: false, scene: 'Game' },
  twitch: { live: false, title: '', game: '', viewers: 0, started_at: '', uptime: '',
            connected: true, display_name: 'NeoTheFox98', profile_image_url: '', view_count: 0 },
  system: { cpu: 5, ram_pct: 40, ram_used_gb: 12, ram_total_gb: 31, gpu: 'Test GPU' },
  server: { started_at: Date.now() / 1000, uptime: '10s', port: 5000, version: '9.9.9' },
  scenes: { active_set: 'prism-soft', available: ['prism-soft'] },
  requests: [],
};
const J = {
  '/api/status': () => STATUS,
  '/api/scenes': () => ({ active: 'prism-soft', sets: ['prism-soft'] }),
  '/api/interactive': () => ({ enabled: true, prefix: '!', auth: {}, chat: {}, redeems: {},
                               eventsub: {}, automation: {}, quotes: { count: 0 },
                               shoutout: {}, recent: [], overlays: {} }),
  '/api/interactive/stats': () => ({}),
  '/api/health/monitor': () => ({ alerts: S.alerts, checks: S.checks, metrics: {} }),
  '/api/spotify': () => ({ connected: false }),
  '/api/commands': () => ({ commands: [], custom: [] }),
  '/api/quotes': () => ({ count: 0, quotes: [] }),
  '/api/timers': () => ({ timers: [] }),
  '/api/config': () => ({}),
  '/api/update': () => ({ available: false, current: '9.9.9', latest: null }),
};

const srv = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (u.pathname === '/api/stream') {           // SSE: connect, then stay quiet
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return res.write(': connected\n\n');
  }
  const j = J[u.pathname];
  if (j) { const b = JSON.stringify(j());
           res.writeHead(200, { 'Content-Type': 'application/json' }); return res.end(b); }
  const file = u.pathname === '/dashboard' ? 'static/dashboard.html'
             : u.pathname.replace(/^\//, '');
  const p = path.join(REPO, file);
  if (!p.startsWith(REPO) || !fs.existsSync(p)) { res.writeHead(404); return res.end('nf'); }
  res.writeHead(200, { 'Content-Type': MIME[path.extname(p)] || 'text/plain' });
  res.end(fs.readFileSync(p));
});
await new Promise(r => srv.listen(0, '127.0.0.1', r));
const BASE = 'http://127.0.0.1:' + srv.address().port;

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const toasts = [];
page.on('console', () => {});
await page.goto(BASE + '/dashboard', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);

const box = (sel) => page.$eval(sel, n => {
  const r = n.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
});

// ── 1. a visible banner must not be a flex item ──────────────────────────
console.log('\n1. banner layout (it rendered as a full-height column beside the sidebar)');
for (const id of ['health-banner', 'conn-banner']) {
  const before = await box('.sidebar');
  await page.$eval('#' + id, (n, cls) => {
    n.className = cls + ' show';
    n.textContent = '⚠ not authorized (unconfigured) · disconnected';
  }, id === 'health-banner' ? 'health-banner' : 'conn-banner');
  await page.waitForTimeout(250);
  const b = await box('#' + id);
  const after = await box('.sidebar');
  const vw = await page.evaluate(() => window.innerWidth);

  ok(after.x === before.x && after.y === before.y,
     `#${id}: showing it does not move the sidebar (was x=${before.x}, now x=${after.x})`);
  ok(b.h > 0 && b.h < 80, `#${id}: it is a bar, not a column (height ${Math.round(b.h)}px)`);
  ok(b.w > vw * 0.9, `#${id}: it spans the window (${Math.round(b.w)} of ${vw}px)`);
  ok(b.y < 5, `#${id}: it sits at the top (y=${Math.round(b.y)})`);

  await page.$eval('#' + id, (n, cls) => { n.className = cls; n.textContent = ''; },
                   id === 'health-banner' ? 'health-banner' : 'conn-banner');
  await page.waitForTimeout(150);
}

// ── 2. the version chip reads the running version ────────────────────────
console.log('\n2. version chip');
{
  const txt = await page.$eval('#ver-chip', n => n.textContent.trim());
  ok(txt === 'v9.9.9', `shows the version the server reported, not a hardcoded one (got "${txt}")`);
}

// ── 3. opening the dashboard with alerts live must not toast them all ────
console.log('\n3. alert noise on load');
await browser.close();
{
  S.alerts = [
    { id: 'auth', level: 'bad', label: 'Twitch auth', message: 'not authorized (unconfigured)' },
    { id: 'chat', level: 'bad', label: 'Twitch chat', message: 'disconnected' },
  ];
  const b2 = await chromium.launch({ args: ['--no-sandbox'] });
  const p2 = await b2.newPage({ viewport: { width: 1600, height: 900 } });
  await p2.goto(BASE + '/dashboard', { waitUntil: 'domcontentloaded' });
  await p2.waitForTimeout(1500);
  const n1 = await p2.$$eval('.toast', ns => ns.length);
  ok(n1 === 0, `two alerts already live at load produce no toasts (got ${n1})`);

  const bannerShown = await p2.$eval('#health-banner', n => n.classList.contains('show'));
  ok(bannerShown, 'the banner still reports them — they are not hidden, just not shouted');

  // a NEW alert after the baseline is news, and must toast
  S.alerts = S.alerts.concat(
    [{ id: 'dropped', level: 'bad', label: 'Dropped frames', message: 'dropping frames' }]);
  await p2.waitForTimeout(4000);
  const n2 = await p2.$$eval('.toast', ns => ns.length);
  ok(n2 >= 1, `an alert raised AFTER load still toasts (got ${n2})`);
  await b2.close();
}

srv.close();
console.log(`\n${fail ? '✗' : '✓'} dashboard: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
