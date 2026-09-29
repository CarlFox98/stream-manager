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

// CI images sometimes ship a chromium that doesn't match the pinned playwright.
// PW_CHROMIUM points at one that does; unset, playwright finds its own.
const LAUNCH = { args: ['--no-sandbox'] };
if (process.env.PW_CHROMIUM) LAUNCH.executablePath = process.env.PW_CHROMIUM;

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
  server: { started_at: Date.now() / 1000, uptime: '10s', port: 5000, version: '9.9.9',
            supervised: true },
  scenes: { active_set: 'prism-soft', available: ['prism-soft'] },
  requests: [],
};
// Restart/shutdown stub. PID is what tells the page "it came back".
let PID = 111;
const TOKEN = 'test-session-token-8f3a';
const LC = { calls: [], down: false };

const J = {
  '/api/status': () => STATUS,
  '/api/ping': () => ({ app: 'stream-manager', version: '9.9.9', pid: PID, port: 5000 }),
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
  if (req.method === 'POST' && u.pathname.startsWith('/api/lifecycle/')) {
    let raw = '';
    req.on('data', c => { raw += c; });
    return req.on('end', () => {
      const action = u.pathname.split('/').pop();
      let body = {};
      try { body = JSON.parse(raw || '{}'); } catch {}
      LC.calls.push({ action, confirm: body.confirm, token: req.headers['x-sm-token'] });
      const send = (code, o) => { res.writeHead(code, { 'Content-Type': 'application/json' });
                                  res.end(JSON.stringify(o)); };
      if (body.confirm !== true) return send(400, { ok: false, error: 'Confirmation required (confirm: true)' });
      if (action === 'restart' && STATUS.server.supervised === false)
        return send(409, { ok: false, error: 'Nothing is supervising this instance, so restarting would just stop it.' });
      if (action === 'restart') {          // go away, then come back as a new process
        LC.down = true;
        setTimeout(() => {
          LC.down = false; PID = 222;
          STATUS.server.started_at = Date.now() / 1000;
        }, 4000);
      }
      return send(200, { ok: true, action });
    });
  }
  if (u.pathname === '/api/stream') {           // SSE: connect, then stay quiet
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return res.write(': connected\n\n');
  }
  if (LC.down) { res.writeHead(503); return res.end('restarting'); }
  const j = J[u.pathname];
  if (j) { const b = JSON.stringify(j());
           res.writeHead(200, { 'Content-Type': 'application/json' }); return res.end(b); }
  if (u.pathname === '/dashboard') {          // substitute the token, as the server does
    const html = fs.readFileSync(path.join(REPO, 'static/dashboard.html'), 'utf8')
                   .replace('__SM_TOKEN__', TOKEN);
    res.writeHead(200, { 'Content-Type': 'text/html' });
    return res.end(html);
  }
  const file = u.pathname === '/dashboard' ? 'static/dashboard.html'
             : u.pathname.replace(/^\//, '');
  const p = path.join(REPO, file);
  if (!p.startsWith(REPO) || !fs.existsSync(p)) { res.writeHead(404); return res.end('nf'); }
  res.writeHead(200, { 'Content-Type': MIME[path.extname(p)] || 'text/plain' });
  res.end(fs.readFileSync(p));
});
await new Promise(r => srv.listen(0, '127.0.0.1', r));
const BASE = 'http://127.0.0.1:' + srv.address().port;

const browser = await chromium.launch(LAUNCH);
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
  const b2 = await chromium.launch(LAUNCH);
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

// ── 4. restart / shutdown controls ───────────────────────────────────────
console.log('\n4. restart and shutdown');
{
  STATUS.server.supervised = true;
  PID = 111; LC.calls.length = 0;
  const b3 = await chromium.launch(LAUNCH);
  const p3 = await b3.newPage({ viewport: { width: 1600, height: 900 } });
  let loads = 0;
  p3.on('load', () => { loads++; });
  await p3.goto(BASE + '/dashboard', { waitUntil: 'domcontentloaded' });
  await p3.waitForTimeout(900);

  const label = () => p3.$eval('#lc-restart', n => n.textContent.trim());

  // one click arms, it does not fire
  await p3.click('#lc-restart');
  await p3.waitForTimeout(150);
  ok(LC.calls.length === 0, 'one click on Restart sends nothing');
  ok((await label()) !== 'Restart', `the button asks for confirmation (says "${await label()}")`);

  // and it disarms itself, so a stray click can't sit there waiting to be completed
  await p3.waitForTimeout(5200);
  ok((await label()) === 'Restart', 'the armed state expires on its own');
  ok(LC.calls.length === 0, 'expiring does not fire it either');

  // two clicks fire exactly one request, with confirmation and the token
  await p3.click('#lc-restart');
  await p3.waitForTimeout(100);
  await p3.click('#lc-restart');
  await p3.waitForTimeout(400);
  ok(LC.calls.length === 1, `two clicks send exactly one request (got ${LC.calls.length})`);
  ok(LC.calls[0] && LC.calls[0].confirm === true, 'it carries confirm: true');
  ok(LC.calls[0] && LC.calls[0].token === TOKEN,
     'it carries the session token the server baked into the page');

  // Wait until the server has actually gone away and at least one status poll
  // has failed — that is the moment a "connection lost" message would stomp on
  // the restart message, and the only moment this can be tested.
  await p3.waitForTimeout(2600);
  const banner = await p3.$eval('#conn-banner', n => ({ text: n.textContent, shown: n.classList.contains('show') }));
  ok(banner.shown && /restart/i.test(banner.text),
     `the banner says what is happening ("${banner.text.slice(0, 48)}…")`);
  ok(!/connection to stream manager lost/i.test(banner.text),
     'it does not report the expected gap as a failure');

  const disabled = await p3.$eval('#lc-shutdown', n => n.disabled);
  ok(disabled, 'Shut Down is locked out while a restart is in flight');

  // the page reloads itself once the new process answers with a new pid
  const before = loads;
  await p3.waitForTimeout(4000);
  ok(loads > before, `the page reloads when it comes back (loads ${before} -> ${loads})`);
  await b3.close();
}

// ── 5. no supervisor: the button must say so, not find out by clicking ───
console.log('\n5. restart with nothing to restart into');
{
  STATUS.server.supervised = false;
  LC.calls.length = 0;
  const b4 = await chromium.launch(LAUNCH);
  const p4 = await b4.newPage({ viewport: { width: 1600, height: 900 } });
  await p4.goto(BASE + '/dashboard', { waitUntil: 'domcontentloaded' });
  await p4.waitForTimeout(1200);

  ok(await p4.$eval('#lc-restart', n => n.disabled), 'Restart is disabled with no supervisor');
  ok(!(await p4.$eval('#lc-shutdown', n => n.disabled)), 'Shut Down still works');
  const sub = await p4.$eval('#lc-sub', n => n.textContent);
  ok(/supervisor/i.test(sub), `and the card explains why ("${sub.slice(0, 48)}…")`);
  await b4.close();
  STATUS.server.supervised = true;
}

// ── 6. a restart this page did not ask for ──────────────────────────────
// prism-ctl, the Stream Deck key, the tray menu, or a second tab. The token in
// this page died with the old process; /api/status keeps answering, so the page
// looks fine while every POST 403s. It has to notice on its own.
console.log('\n6. a restart from somewhere else');
{
  PID = 111; LC.calls.length = 0;
  STATUS.server.started_at = Date.now() / 1000;
  const b5 = await chromium.launch(LAUNCH);
  const p5 = await b5.newPage({ viewport: { width: 1600, height: 900 } });
  let loads = 0;
  p5.on('load', () => { loads++; });
  await p5.goto(BASE + '/dashboard', { waitUntil: 'domcontentloaded' });
  await p5.waitForTimeout(1200);
  const before = loads;

  // nothing clicked here — the server simply comes back as a new process
  STATUS.server.started_at = Date.now() / 1000 + 60;
  PID = 333;
  await p5.waitForTimeout(3500);

  ok(loads > before, `the page reloads itself to pick up the new token (loads ${before} -> ${loads})`);
  ok(LC.calls.length === 0, 'and it did so without sending anything');
  await b5.close();
}

srv.close();
console.log(`\n${fail ? '✗' : '✓'} dashboard: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
