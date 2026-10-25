/* Stream Manager dashboard: vanilla, no build step. */
(() => {
'use strict';

const TOK = document.querySelector('meta[name="sm-token"]')?.content || '';
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
const fmtNum = n => (Number(n) || 0).toLocaleString();

// ── connection state ────────────────────────────────────────────────
let online = true;
// Declared up here, not next to the restart code below: setOnline() reads it,
// and `typeof` does NOT protect a `let` that hasn't been initialised yet.
let lcMode = null;   // null | 'restart' | 'shutdown'; set once, never cleared
function setOnline(v) {
  if (v === online) return;
  online = v;
  // Once a restart or shutdown is under way the banner is saying something more
  // useful than "connection lost", and the gap is expected, so it keeps the
  // message lcFire put there instead of being overwritten with a false alarm.
  if (!lcMode) connBanner(CONN_LOST, !v);
  // #conn-pill is the top bar's; .js-conn is its narrow-window twin in the sidebar.
  $$('#conn-pill, .js-conn').forEach(pill => {
    pill.classList.toggle('off', !v);
    const d = pill.querySelector('.status-dot'); if (d) d.className = 'status-dot ' + (v ? 'on' : 'off');
  });
  $$('#conn-text, .js-conn-text').forEach(t => { t.textContent = v ? 'Connected' : 'Disconnected'; });
}
async function api(path) {
  const r = await fetch(path, { headers: { 'X-SM-Token': TOK } });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const d = await r.json(); setOnline(true); return d;
}
async function post(path, body) {
  const r = await fetch(path, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-SM-Token': TOK },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const d = await r.json().catch(() => ({})); return { ok: r.ok, status: r.status, data: d };
}

// ── toasts ──────────────────────────────────────────────────────────
function toast(msg, kind = '') {
  const wrap = $('#toast-wrap'); if (!wrap) return;
  const el = document.createElement('div');
  el.className = 'toast ' + kind; el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => { el.classList.add('leaving'); setTimeout(() => el.remove(), 260); }, 3400);
}

// ── clock ───────────────────────────────────────────────────────────
function tick() {
  const n = new Date();
  $('#clock').textContent = n.toLocaleTimeString();
  $('#clock-date').textContent = n.toLocaleDateString(undefined, { weekday:'long', month:'long', day:'numeric' });
}
setInterval(tick, 1000); tick();

// ── tabs ────────────────────────────────────────────────────────────
function showTab(name) {
  $$('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
  try { localStorage.setItem('sm_tab', name); } catch (e) {}
  if (name === 'commands') loadCommands();
  // Don't re-fetch over unsaved edits; that used to silently throw them away
  // whenever you flipped to another tab and back.
  if (name === 'config' && !cfgDirty.size) loadConfig();
  if (name === 'timers') loadTimers();
  if (name === 'quotes') loadQuotes();
  if (name === 'health') pollMonitor();
}
let lastState = null, lastIx = null;
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('.tab'); if (b) showTab(b.dataset.tab);
});

// ── copy-to-clipboard on overlay URLs (event delegation) ────────────
function copyItem(el) {
  const code = el.querySelector('code'); if (!code) return;
  const url = window.location.origin + code.textContent;
  navigator.clipboard?.writeText(url).then(() => {
    const hint = el.querySelector('.hint'); if (!hint) return;
    const orig = hint.textContent; hint.textContent = '✓ Copied';
    setTimeout(() => hint.textContent = orig, 1200);
  }).catch(() => toast('Clipboard blocked by browser', 'err'));
}
document.addEventListener('click', e => {
  const item = e.target.closest('.url-item[data-copy]'); if (item) copyItem(item);
});
document.addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('.url-item[data-copy]')) {
    e.preventDefault(); copyItem(e.target);
  }
});

// ── overview: status ────────────────────────────────────────────────
let lastLogHead = null;
// Log tab state. It lives up here because render() (which assigns lastStatus) can run
// before a `let` further down would be initialised.
let lastStatus = null, logEventsOnly = true;
try { logEventsOnly = localStorage.getItem('sm_log_events') !== '0'; } catch (e) {}
let srvStartedAt = 0;
function renderStatus(s) {
  // The session token is regenerated on every server start and baked into this
  // page, so a restart this page did not initiate (from prism-ctl, the Stream
  // Deck key, the tray menu, or a second dashboard tab) leaves it holding a
  // dead token. /api/status is a GET and keeps answering, so nothing looked
  // wrong: the page stayed up and every POST quietly 403'd. started_at is the
  // only thing that changes, so it is what we watch.
  const started = Number(s.server && s.server.started_at) || 0;
  if (started) {
    if (!srvStartedAt) srvStartedAt = started;
    else if (started !== srvStartedAt) { location.reload(); return; }
  }
  lastState = s;
  $('#obs-dot').className = 'status-dot ' + (s.obs.running ? 'on' : 'off');
  $('#obs-label').textContent = s.obs.running ? 'Running' : 'Not running';
  $('#obs-pid').textContent = s.obs.pid ? 'PID ' + s.obs.pid : '';
  $('#obs-uptime').textContent = s.obs.uptime ? 'Uptime: ' + fmtUptime(s.obs.uptime) : '';
  $('#obs-scene').textContent = s.obs.scene ? 'Scene: ' + s.obs.scene : '';
  const out = []; if (s.obs.streaming) out.push('● Streaming'); if (s.obs.recording) out.push('● Recording');
  $('#obs-outputs').textContent = out.join('   ');

  const live = s.twitch.live;
  $('#twitch-dot').className = 'status-dot ' + (live ? 'on' : 'off');
  $('#twitch-label').textContent = live ? 'LIVE' : 'Offline';
  $('#twitch-title').textContent = s.twitch.title || '-';
  $('#twitch-game').textContent = s.twitch.game || '-';
  $('#twitch-viewers').textContent = live ? s.twitch.viewers + ' viewers' : '';
  $('#twitch-uptime').textContent = live ? s.twitch.uptime : '';
  $('#twitch-card').classList.toggle('card-live', live);
  if (s.twitch.display_name) $('#display-name').textContent = s.twitch.display_name;
  if (s.twitch.view_count) $('#view-count').textContent = fmtNum(s.twitch.view_count);
  const av = $('#avatar');
  if (s.twitch.profile_image_url) { av.src = s.twitch.profile_image_url; av.style.display = 'block'; }
  $('#twitch-api-dot').className = 'status-dot ' + (s.twitch.connected ? 'on' : 'off');
  $('#twitch-api-label').textContent = s.twitch.connected ? 'Connected' : 'No credentials';

  $('#server-uptime').textContent = s.server.uptime || '0s';
  $('#server-port').textContent = ':' + s.server.port;
  // Read the running version rather than trusting a number typed into the
  // markup; that one was two releases behind and said so on every page load.
  if (s.server.version) $$('#ver-chip, .js-ver').forEach(vc => { vc.textContent = 'v' + s.server.version; });

  // No supervisor means exit code 42 goes nowhere and Restart is really just
  // Shutdown. Say so on the button rather than finding out by clicking it.
  const lcR = $('#lc-restart'), lcSub = $('#lc-sub');
  if (lcR && !lcMode) {
    const unsup = s.server.supervised === false;
    lcR.disabled = unsup;
    lcR.title = unsup ? 'Nothing is supervising this instance' : '';
    if (lcSub) lcSub.classList.toggle('warn', unsup);
    if (lcSub && unsup) lcSub.innerHTML = 'Restart needs a supervisor. Launch with <code>Start Stream Manager.bat</code> or the tray app; shut down still works.';
  }

  $('#cpu-pct').textContent = s.system.cpu;
  $('#cpu-bar').style.width = s.system.cpu + '%';
  $('#ram-used').textContent = s.system.ram_used_gb;
  $('#ram-total').textContent = s.system.ram_total_gb;
  $('#ram-bar').style.width = s.system.ram_pct + '%';
  $('#ram-pct-label').textContent = s.system.ram_pct + '% used';
  if (s.system.gpu) $('#gpu-name').textContent = s.system.gpu;

  lastStatus = s;
  renderLog();
  renderHealth();
}
// Log tab: "Hide served files" (default on) shows the server's events buffer,
// which static-file lines can't push out. Off = the full request log.
function renderLog(force) {
  const s = lastStatus; if (!s) return;
  const lines = logEventsOnly && Array.isArray(s.events) ? s.events : (s.requests || []);
  const head = (logEventsOnly ? 'e:' : 'r:') + (lines[0] || '');
  if (!force && head === lastLogHead) return;
  lastLogHead = head;
  if (!lines.length) { $('#log-box').innerHTML = `<div class="log-entry"><span class="log-text">${logEventsOnly
    ? 'No events since Stream Manager started, only served files so far. Switch off “Hide served files” to see them.'
    : 'Nothing logged yet.'}</span></div>`; return; }
  {
    $('#log-box').innerHTML = lines.map(r => {
      const m = r.match(/^\[(\d+:\d+:\d+)\]\s+(.*)/);
      return m
        ? `<div class="log-entry"><span class="log-timestamp">[${esc(m[1])}]</span> <span class="log-text">${esc(m[2])}</span></div>`
        : `<div class="log-entry"><span class="log-text">${esc(r)}</span></div>`;
    }).join('');
  }
}
function fmtUptime(secs) {
  if (!secs || secs <= 0) return '';
  const h = Math.floor(secs/3600), m = Math.floor((secs%3600)/60), s = secs%60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
}

// ── overview: scenes ────────────────────────────────────────────────
const SCENE_LABELS = { modern: 'Modern Neon', retro: 'Retro Win98', 'prism-holo': 'PRISM Holo', 'prism-signal': 'PRISM Signal', 'prism-soft': 'PRISM Soft Holo', 'prism-haunt': 'PRISM Haunt' };
let sceneSwitching = false;
function renderScenes(d) {
  const active = d.active_set, available = d.available || [];
  const el = $('#scene-active');
  if (active) { el.textContent = SCENE_LABELS[active] || active; el.classList.remove('unknown'); }
  else { el.textContent = 'Unknown / not set'; el.classList.add('unknown'); }
  const order = ['modern', 'retro'];
  const sets = order.filter(n => available.includes(n)).concat(available.filter(n => !order.includes(n)));
  $('#scene-btns').innerHTML = sets.map(name => {
    const cur = name === active, dis = sceneSwitching || cur;
    return `<button class="scene-btn${cur ? ' current' : ''}" ${dis ? 'disabled' : ''} data-scene="${esc(name)}">${cur ? '● ' : ''}${esc(SCENE_LABELS[name] || name)}</button>`;
  }).join('') || '<span class="scene-sub">No scene sets found on disk.</span>';
}
async function switchScene(name) {
  if (sceneSwitching) return; sceneSwitching = true;
  const msg = $('#scene-msg'); msg.className = 'scene-msg'; msg.textContent = 'Switching to ' + (SCENE_LABELS[name] || name) + '…';
  try {
    const { data: d } = await post('/api/scenes/switch', { set: name });
    msg.textContent = d.message || (d.ok ? 'Switched.' : 'Switch failed.');
    msg.classList.add(d.ok ? 'ok' : 'err');
    renderScenes(d);
    if (d.ok) { msg.textContent += '. Refresh your OBS browser sources.'; toast('Scene set → ' + (SCENE_LABELS[name] || name), 'ok'); }
  } catch (e) { msg.className = 'scene-msg err'; msg.textContent = 'Switch request failed.'; }
  finally { sceneSwitching = false; setTimeout(() => { if (!sceneSwitching) { msg.className = 'scene-msg'; msg.textContent = ''; } }, 6000); }
}

// ── overview: update ────────────────────────────────────────────────
let updateInfo = null;
async function checkUpdate() {
  try {
    const d = await api('/api/update'); updateInfo = d;
    const card = $('#update-card');
    if (d.available && d.latest) { $('#update-ver').textContent = d.latest + '  (current v' + d.current + ')'; card.classList.add('show'); }
    else card.classList.remove('show');
  } catch (e) {}
}
async function installUpdate() {
  if (!updateInfo || !updateInfo.available) return;
  if (!confirm('Download and install ' + updateInfo.latest + '?\n\nYour current files are backed up to .update-backup/. Restart afterward.')) return;
  const btn = $('#update-btn'), msg = $('#update-msg');
  btn.disabled = true; msg.className = 'update-msg'; msg.textContent = 'Downloading & installing…';
  try {
    const { data: d } = await post('/api/update/install', { confirm: true });
    msg.textContent = d.message || d.error || (d.ok ? 'Installed.' : 'Failed.');
    msg.classList.add(d.ok ? 'ok' : 'err');
    if (d.ok) { msg.textContent += ' Restart to apply.'; toast('Update installed. Restart to apply', 'ok'); }
    else btn.disabled = false;
  } catch (e) { msg.className = 'update-msg err'; msg.textContent = 'Install request failed.'; btn.disabled = false; }
}

// ── interactive ─────────────────────────────────────────────────────
// One relative-time format for the whole dashboard: hours stop at 48, then days.
function fmtAgo(ts, secs = false) {
  const d = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (d < 60) return secs ? d + 's ago' : 'just now';
  if (d < 3600) return Math.floor(d / 60) + 'm ago';
  if (d < 48 * 3600) return Math.floor(d / 3600) + 'h ago';
  return Math.floor(d / 86400) + 'd ago';
}
const ixWhen = ts => fmtAgo(ts, true);
async function renderInteractive() {
  let d; try { d = await api('/api/interactive'); } catch (e) { return; }
  const st = d.auth?.status, dot = $('#ix-auth-dot'), label = $('#ix-auth-label'), hint = $('#ix-auth-hint');
  const btn = $('#ix-auth-btn'), logoutBtn = $('#ix-logout-btn');
  btn.style.display = 'none'; logoutBtn.style.display = 'none'; hint.innerHTML = '';
  const meta = $('#ix-meta');
  if (meta) {
    const chips = [];
    const es = d.redeems?.transport === 'eventsub';
    chips.push(`<span class="ix-chip ${es ? 'on' : 'off'}">${es ? 'EventSub ⚡' : 'Polling'}</span>`);
    chips.push(`<span class="ix-chip ${d.automation?.enabled ? 'on' : 'off'}">Automation ${d.automation?.enabled ? 'on' : 'off'}</span>`);
    if (d.eventsub && d.eventsub.available === false) chips.push('<span class="ix-chip off">websocket-client not installed</span>');
    meta.innerHTML = chips.join('');
  }
  if (st === 'ok') {
    dot.className = 'status-dot on';
    label.textContent = 'Authorized as ' + (d.auth.login || '-');
    const chat = d.chat?.connected ? 'chat connected' : 'chat connecting…';
    const rd = d.redeems?.ready ? 'redeems ready' : 'redeems setting up…';
    hint.textContent = chat + ' · ' + rd + (d.chat?.channel ? ' · #' + d.chat.channel : '');
    logoutBtn.style.display = 'inline-block';
  } else if (st === 'pending') {
    dot.className = 'status-dot warn'; label.textContent = 'Waiting for Twitch…';
    hint.innerHTML = 'A browser window opened. Click <strong>Authorize</strong> there.' +
      (d.auth.authorize_url ? ` &nbsp;<a href="${esc(d.auth.authorize_url)}" target="_blank" rel="noopener">Reopen login</a>` : '');
  } else if (st === 'unauthorized' || st === 'error') {
    dot.className = 'status-dot warn'; label.textContent = 'Connect your Twitch account';
    hint.textContent = d.auth?.error || 'One click: a browser window will open for you to approve access.';
    btn.textContent = 'Login with Twitch'; btn.style.display = 'inline-block';
  } else if (st === 'unconfigured') {
    dot.className = 'status-dot off'; label.textContent = 'Not configured';
    hint.textContent = 'Add TWITCH_CLIENT_ID to your .env, then restart.';
  } else { dot.className = 'status-dot off'; label.textContent = 'Unavailable'; hint.textContent = d.auth?.error || ''; }

  const feed = $('#ix-feed'), items = d.recent || [];
  feed.innerHTML = items.length
    ? items.map(it => `<div class="ix-item">${esc(it.text || '')} <span class="ix-when">· ${esc(ixWhen(it.ts))}</span></div>`).join('')
    : '<div class="stat-label">No spins yet.</div>';
  lastIx = d; renderHealth();
}

// ── health strip ────────────────────────────────────────────────────
function renderHealth() {
  const el = $('#health'); if (!el) return;
  const items = [];
  if (lastState) {
    items.push(['Twitch API', lastState.twitch?.connected, false, lastState.twitch?.connected ? '' : 'No app/user token']);
    items.push(['OBS', lastState.obs?.running, false, lastState.obs?.running ? '' : 'Not detected']);
  }
  if (lastIx && lastIx.enabled !== false) {
    const a = lastIx.auth || {}, c = lastIx.chat || {}, r = lastIx.redeems || {}, e = lastIx.eventsub || {};
    items.push(['Auth', a.status === 'ok', a.status === 'pending', a.error || (a.status !== 'ok' ? a.status : '')]);
    items.push(['Chat', c.connected, false, c.error || '']);
    items.push(['Redeems', r.ready, false, r.error || '']);
    if (e.available) items.push(['EventSub', e.connected, !e.connected, e.error || '']);
  }
  el.innerHTML = items.map(([label, ok, warn, err]) => {
    const cls = ok ? '' : (warn ? 'warn' : 'bad');
    const dot = ok ? 'on' : (warn ? 'warn' : 'off');
    return `<span class="health-item ${cls}" ${err ? `title="${esc(err)}"` : ''}><span class="status-dot ${dot}"></span>${esc(label)}</span>`;
  }).join('');
}

// ── timers tab ──────────────────────────────────────────────────────
let timerData = { enabled: false, list: [] };
async function loadTimers() {
  try { timerData = await api('/api/timers'); } catch (e) { return; }
  $('#tm-enabled').checked = !!timerData.enabled;
  renderTimers();
}
function renderTimers() {
  const list = timerData.list || [];
  $('#timer-list').innerHTML = list.length ? list.map((t, i) => `
    <div class="timer-row" data-ti="${i}">
      <div class="tm-head">
        <input class="field" data-tf="name" value="${esc(t.name || '')}" placeholder="name (optional)" style="max-width:180px">
        <label class="switch" title="Enabled"><input type="checkbox" data-tf="enabled" ${t.enabled !== false ? 'checked' : ''}><span class="track"></span><span class="knob"></span></label>
        <button class="icon-btn" data-tm-del="${i}" title="Delete" style="margin-left:auto">✕</button>
      </div>
      <input class="field" data-tf="message" value="${esc(t.message || '')}" placeholder="Message to post in chat" maxlength="400">
      <div class="tm-nums">
        <div><span class="col-label">Every (min)</span><input class="field num" type="number" min="1" data-tf="interval" value="${Number(t.interval) || 15}"></div>
        <div><span class="col-label">Min lines</span><input class="field num" type="number" min="0" data-tf="min_lines" value="${Number(t.min_lines) || 0}"></div>
      </div>
    </div>`).join('') : '<div class="stat-label">No timers yet. Add one below.</div>';
}
function gatherTimers() {
  timerData.list = $$('#timer-list .timer-row').map(row => ({
    name: row.querySelector('[data-tf=name]').value.trim(),
    message: row.querySelector('[data-tf=message]').value.trim(),
    interval: +row.querySelector('[data-tf=interval]').value || 15,
    min_lines: +row.querySelector('[data-tf=min_lines]').value || 0,
    enabled: row.querySelector('[data-tf=enabled]').checked,
  }));
}
function addTimerRow() { gatherTimers(); (timerData.list = timerData.list || []).push({ name:'', message:'', interval:15, min_lines:5, enabled:true }); renderTimers(); }
function deleteTimerRow(i) { gatherTimers(); timerData.list.splice(i, 1); renderTimers(); }
async function saveTimers() {
  gatherTimers();
  const msg = $('#tm-msg'); msg.className = 'scene-msg';
  const { data: d } = await post('/api/timers/save', { data: { enabled: $('#tm-enabled').checked, list: timerData.list } });
  if (d.ok) { msg.classList.add('ok'); msg.textContent = 'Saved.'; toast('Timed messages saved', 'ok'); timerData = d.timers; }
  else { msg.classList.add('err'); msg.textContent = d.error || 'Save failed.'; toast(d.error || 'Save failed', 'err'); }
}

// ── quotes tab ──────────────────────────────────────────────────────
async function loadQuotes() {
  let d; try { d = await api('/api/quotes'); } catch (e) { $('#quote-list').innerHTML = '<div class="stat-label">Could not load.</div>'; return; }
  const qs = d.quotes || [];
  // Ids are stable across deletions, so after a delete the count and the
  // highest number disagree, so say both rather than look like a miscount.
  const maxId = qs.reduce((m, q) => Math.max(m, Number(q.id) || 0), 0);
  $('#quote-count').textContent = !d.count ? ''
    : maxId > d.count ? `(${d.count} · #1–${maxId})` : `(${d.count})`;
  quoteData = qs;
  renderQuotes();
}
let quoteData = [], quoteEditing = null;
function renderQuotes() {
  const term = ($('#q-search')?.value || '').trim().toLowerCase().replace(/^#/, '');
  const shown = quoteData.slice().reverse().filter(q => !term
    || String(q.id) === term
    || (q.text || '').toLowerCase().includes(term)
    || (q.added_by || '').toLowerCase().includes(term));
  $('#quote-list').innerHTML = !quoteData.length ? '<div class="stat-label">No quotes yet.</div>'
    : !shown.length ? '<div class="stat-label">No quotes match.</div>'
    : shown.map(q => String(q.id) === String(quoteEditing) ? `
    <div class="cmd-row quote-row editing">
      <div class="cmd-main">
        <span class="qnum">#${esc(q.id)}</span>
        <input class="field" id="q-edit-text" maxlength="400" value="${esc(q.text)}" aria-label="Edit quote #${esc(q.id)}">
      </div>
      <button class="btn-primary" data-q-save="${esc(q.id)}">Save</button>
      <button class="scene-btn" data-q-cancel="1">Cancel</button>
    </div>` : `
    <div class="cmd-row quote-row">
      <div class="cmd-main">
        <div class="cmd-desc"><span class="qnum">#${esc(q.id)}</span>${esc(q.text)}</div>
        <div class="mini">${esc(q.added_by || '')}${q.date ? ' · ' + esc(q.date) : ''}</div>
      </div>
      <button class="icon-btn edit" data-q-edit="${esc(q.id)}" title="Edit">✎</button>
      <button class="icon-btn" data-q-del="${esc(q.id)}" title="Delete">✕</button>
    </div>`).join('');
  const inp = $('#q-edit-text'); if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
}
function editQuote(id) { quoteEditing = id; renderQuotes(); }
function cancelQuoteEdit() { quoteEditing = null; renderQuotes(); }
async function saveQuoteEdit(id) {
  const text = ($('#q-edit-text')?.value || '').trim();
  if (!text) { toast('Quote text can’t be empty', 'err'); return; }
  const { data: d } = await post('/api/quotes/edit', { id: Number(id), text });
  if (d.ok) { quoteEditing = null; toast('Quote #' + id + ' updated', 'ok'); loadQuotes(); }
  else toast('Edit failed', 'err');
}
async function addQuote() {
  const text = $('#q-text').value.trim(), msg = $('#q-msg'); msg.className = 'scene-msg';
  if (!text) { msg.classList.add('err'); msg.textContent = 'Enter quote text.'; return; }
  const { data: d } = await post('/api/quotes/add', { text, added_by: 'dashboard' });
  if (d.ok) { $('#q-text').value = ''; toast('Quote added', 'ok'); loadQuotes(); }
  else { msg.classList.add('err'); msg.textContent = 'Add failed.'; }
}
async function deleteQuote(id) {
  if (!confirm('Delete quote #' + id + '?')) return;
  const { data: d } = await post('/api/quotes/delete', { id: Number(id) });
  if (d.ok) { toast('Quote deleted'); loadQuotes(); } else toast('Delete failed', 'err');
}

// ── live event stream (SSE) ─────────────────────────────────────────
function initSSE() {
  if (!window.EventSource) return;
  try {
    const es = new EventSource('/api/stream');
    es.onmessage = e => {
      let ev; try { ev = JSON.parse(e.data); } catch (_) { return; }
      if (ev.channel === 'hype' && ev.summary) toast(ev.summary, 'ok');
      renderInteractive();   // instant feed refresh
    };
    es.onerror = () => {};   // EventSource auto-reconnects; polling still covers us
  } catch (e) {}
}
async function renderStats() {
  let s; try { s = await api('/api/interactive/stats'); } catch (e) { return; }
  const players = s.top_players || [];
  $('#ix-board').innerHTML = players.length
    ? players.slice(0, 8).map((p, i) => `<div class="row"><span class="who">${i+1}. ${esc(p.user)}</span><span class="n">${esc(p.plays)} plays${p.wins ? ' · ' + esc(p.wins) + ' wins' : ''}</span></div>`).join('')
    : '<div class="stat-label">No plays yet.</div>';
}
// ── test-to-chat toggle (shared by Quick Test and Test an Outcome) ──
// Off by default: a test mid-stream shouldn't post "Dashboard flipped heads".
let testChat = false;
try { testChat = localStorage.getItem('sm_test_chat') === '1'; } catch (e) {}
function syncTestChat() { $$('.test-chat').forEach(cb => { cb.checked = testChat; }); }
document.addEventListener('change', e => {
  if (!e.target.classList?.contains('test-chat')) return;
  testChat = e.target.checked; syncTestChat();
  try { localStorage.setItem('sm_test_chat', testChat ? '1' : '0'); } catch (err) {}
});
syncTestChat();

async function ixTest(action) {
  const msg = $('#ix-msg'); msg.className = 'scene-msg'; msg.textContent = 'Triggering ' + action + '…';
  try {
    const { data: d } = await post('/api/interactive/test', { action, user: 'Dashboard', chat: testChat });
    msg.classList.add(d.ok ? 'ok' : 'err');
    msg.textContent = d.ok ? `▶ ${action} → ${d.result ?? 'sent'} (check your overlay)` : (d.error || 'Failed.');
  } catch (e) { msg.className = 'scene-msg err'; msg.textContent = 'Request failed.'; }
  setTimeout(() => { msg.className = 'scene-msg'; msg.textContent = ''; }, 6000);
}
async function ixAuth()   { $('#ix-msg').textContent = 'Opening Twitch login…'; try { await post('/api/interactive/authorize'); } catch (e) {} setTimeout(() => { const m = $('#ix-msg'); if (m.textContent.startsWith('Opening')) m.textContent = ''; }, 6000); }
async function ixLogout() { try { await post('/auth/logout'); toast('Signed out of Twitch'); } catch (e) {} renderInteractive(); }
async function ixReload() {
  const msg = $('#ix-msg'); msg.className = 'scene-msg'; msg.textContent = 'Reloading config…';
  try { const { data: d } = await post('/api/interactive/reload'); msg.classList.add(d.ok ? 'ok' : 'err');
    msg.textContent = d.ok ? '♻️ Config reloaded.' : 'Reload failed.'; if (d.ok) toast('Config reloaded', 'ok'); }
  catch (e) { msg.className = 'scene-msg err'; msg.textContent = 'Reload request failed.'; }
  setTimeout(() => { msg.className = 'scene-msg'; msg.textContent = ''; }, 5000);
}

// ── commands tab ────────────────────────────────────────────────────
function sw(checked, attrs = '') { return `<label class="switch">${''}<input type="checkbox" ${checked ? 'checked' : ''} ${attrs}><span class="track"></span><span class="knob"></span></label>`; }
async function loadCommands() {
  let d; try { d = await api('/api/commands'); } catch (e) { $('#cmd-builtins').innerHTML = '<div class="stat-label">Could not load.</div>'; return; }
  const pfx = d.prefix || '!';
  const cats = {};
  (d.builtins || []).forEach(c => { (cats[c.category] = cats[c.category] || []).push(c); });
  let html = '';
  Object.keys(cats).forEach(cat => {
    html += `<div class="cmd-cat">${esc(cat)}</div>`;
    cats[cat].forEach(c => {
      const alias = c.aliases.slice(1).map(a => pfx + a).join(' ');
      html += `<div class="cmd-row">
        <div class="cmd-main">
          <div class="cmd-name"><span class="mono">${esc(pfx + c.name)}</span> ${c.mods_only ? '<span class="cmd-badge">mods</span>' : ''}</div>
          <div class="cmd-desc">${esc(c.description)}${alias ? ' · aka ' + esc(alias) : ''}</div>
        </div>
        ${sw(c.enabled, `data-cmd-toggle="${esc(c.name)}"`)}
      </div>`;
    });
  });
  $('#cmd-builtins').innerHTML = html || '<div class="stat-label">No commands.</div>';

  const custom = d.custom || [];
  window.__ccList = custom;
  $('#cmd-custom').innerHTML = custom.length ? custom.map(c => `
    <div class="cmd-row">
      <div class="cmd-main">
        <div class="cmd-name"><span class="mono">${esc(pfx + c.name)}</span> ${c.permission === 'mods' ? '<span class="cmd-badge">mods</span>' : ''}</div>
        <div class="cmd-desc">${esc(c.response)}</div>
        <div class="mini">${esc(ccMeta(c))}</div>
      </div>
      ${sw(c.enabled, `data-cc-toggle="${esc(c.name)}"`)}
      <button class="icon-btn edit" title="Edit" data-cc-edit="${esc(c.name)}">✎</button>
      <button class="icon-btn" title="Delete" data-cc-del="${esc(c.name)}">✕</button>
    </div>`).join('') : '<div class="stat-label">No custom commands yet.</div>';
}
async function toggleCommand(name, enabled) {
  const { data: d } = await post('/api/commands/toggle', { name, enabled });
  if (d.ok) toast(`${name} ${enabled ? 'enabled' : 'disabled'}`, 'ok'); else { toast('Toggle failed', 'err'); loadCommands(); }
}
function ccMeta(c) {
  const bits = [c.uses === 1 ? 'used once' : `used ${fmtNum(c.uses || 0)} times`];
  if (c.user_cd) bits.push(`${c.user_cd}s per viewer`);
  if (c.global_cd) bits.push(`${c.global_cd}s global`);
  if (!c.user_cd && !c.global_cd) bits.push('no cooldown');
  return bits.join(' · ');
}
// Editing reuses the add form: the server's upsert handles both. A rename is
// upsert-the-new-name, then delete the old one (only once the new one saved).
let ccEditing = null;   // { name, enabled } while editing an existing command
function editCustom(name) {
  const c = (window.__ccList || []).find(x => x.name === name); if (!c) return;
  ccEditing = { name: c.name, enabled: c.enabled };
  $('#cc-name').value = c.name; $('#cc-response').value = c.response; $('#cc-perm').value = c.permission;
  $('#cc-ucd').value = c.user_cd || 0; $('#cc-gcd').value = c.global_cd || 0;
  $('#cc-save').textContent = 'Save changes'; $('#cc-cancel').hidden = false;
  $('#cc-response').focus();
}
function cancelCustomEdit() {
  ccEditing = null;
  $('#cc-name').value = ''; $('#cc-response').value = ''; $('#cc-perm').value = 'everyone';
  $('#cc-ucd').value = 0; $('#cc-gcd').value = 0;
  $('#cc-save').textContent = 'Add command'; $('#cc-cancel').hidden = true;
  const msg = $('#cmd-msg'); msg.className = 'scene-msg'; msg.textContent = '';
}
async function saveCustom() {
  const name = $('#cc-name').value.trim().replace(/^!+/, '').toLowerCase(), response = $('#cc-response').value.trim(), permission = $('#cc-perm').value;
  const msg = $('#cmd-msg'); msg.className = 'scene-msg';
  if (!name || !response) { msg.classList.add('err'); msg.textContent = 'Name and response are required.'; return; }
  const editing = ccEditing;
  const renamed = editing && editing.name !== name;
  if (!editing || renamed) {
    if ((window.__ccList || []).some(x => x.name === name) && !confirm('!' + name + ' already exists. Replace it?')) return;
  }
  const user_cd = Math.max(0, +$('#cc-ucd').value || 0), global_cd = Math.max(0, +$('#cc-gcd').value || 0);
  const { data: d } = await post('/api/commands/custom', { action: editing ? 'update' : 'add', name, response, permission,
    enabled: editing ? editing.enabled : true, user_cd, global_cd });
  if (d.ok && renamed) await post('/api/commands/custom', { action: 'delete', name: editing.name });
  msg.classList.add(d.ok ? 'ok' : 'err'); msg.textContent = d.message || (d.ok ? 'Saved.' : 'Failed.');
  if (d.ok) {
    toast((editing ? 'Updated !' : 'Added !') + name, 'ok');
    cancelCustomEdit(); msg.className = 'scene-msg ok'; msg.textContent = d.message || 'Saved.';
    loadCommands();
  }
}
async function deleteCustom(name) {
  if (!confirm('Delete !' + name + '?')) return;
  const { data: d } = await post('/api/commands/custom', { action: 'delete', name });
  if (d.ok) { toast('Deleted !' + name); loadCommands(); } else toast('Delete failed', 'err');
}
async function toggleCustom(name, enabled) {
  const c = (await api('/api/commands')).custom.find(x => x.name === name); if (!c) return;
  const { data: d } = await post('/api/commands/custom', { action: 'update', name, response: c.response, permission: c.permission, enabled });
  if (!d.ok) { toast('Update failed', 'err'); loadCommands(); }
}

// ── config tab ──────────────────────────────────────────────────────
// Unsaved-change tracking: each top-level section is keyed by its Save
// button's data-save value. Edits mark it dirty; a sticky bar offers Save all.
const cfgDirty = new Set();
const CFG_ORDER = ['prefix', 'cooldowns', 'redeems', 'automation', 'eventsub', 'alerts', 'wheels'];
function cfgMark(key, dirty) {
  if (!key) return;
  dirty ? cfgDirty.add(key) : cfgDirty.delete(key);
  const sec = $(`#cfg-root [data-cfg="${key}"]`); if (sec) sec.classList.toggle('dirty', dirty);
  const bar = $('#cfg-savebar'); if (!bar) return;
  bar.classList.toggle('show', cfgDirty.size > 0);
  const n = cfgDirty.size;
  $('#cfg-savebar-text').textContent = n === 1 ? '1 section has unsaved changes' : `${n} sections have unsaved changes`;
}
function cfgOnEdit(e) {
  const sec = e.target.closest('[data-cfg]'); if (sec) cfgMark(sec.dataset.cfg, true);
}
async function cfgSaveAll() {
  const btn = $('#cfg-saveall'); btn.disabled = true;
  const failed = [];
  for (const key of CFG_ORDER.filter(k => cfgDirty.has(k))) {
    if (!(await onSave(key))) failed.push(key);
  }
  btn.disabled = false;
  if (failed.length) toast('Not saved: ' + failed.join(', '), 'err');
}
function cfgDiscard() {
  [...cfgDirty].forEach(k => cfgMark(k, false));
  loadConfig();
}
const CD_KEYS = ['coinflip','5050','slots','dice','duel','lucky','risky','quote'];
async function loadConfig() {
  let c; try { c = await api('/api/config'); } catch (e) { $('#cfg-root').innerHTML = '<div class="stat-label">Could not load.</div>'; return; }
  const root = $('#cfg-root'); root.innerHTML = '';
  [...cfgDirty].forEach(k => cfgMark(k, false));
  if (!root.dataset.wired) {
    root.dataset.wired = '1';
    root.addEventListener('input', cfgOnEdit);
    root.addEventListener('change', cfgOnEdit);
  }

  // Command prefix
  root.appendChild(section('Command prefix', 'The character chat commands start with.',
    `<div class="save-row"><input class="field" id="cfg-prefix" style="width:80px" maxlength="3" value="${esc(c.command_prefix || '!')}"><button class="btn-primary" data-save="prefix">Save</button></div>`));

  // Cooldowns
  const cd = c.cooldowns || {};
  let cdRows = `<div class="cfg-row2"><span class="lbl">Mods & broadcaster bypass cooldowns</span>${sw(cd.mods_bypass !== false, 'id="cfg-cd-mods"')}</div>`;
  cdRows += '<div class="cfg-grid" style="margin-top:10px"><span class="lbl" style="font-weight:700;color:var(--mute)">Command</span><span class="mini">per-user (s)</span><span class="mini">global (s)</span>';
  CD_KEYS.forEach(k => {
    const v = cd[k] || {};
    cdRows += `<span class="lbl">${esc(k)}</span>
      <input class="field num" type="number" min="0" value="${Number(v.user)||0}" data-cd-user="${esc(k)}">
      <input class="field num" type="number" min="0" value="${Number(v.global)||0}" data-cd-global="${esc(k)}">`;
  });
  cdRows += '</div><div class="save-row"><button class="btn-primary" data-save="cooldowns">Save cooldowns</button></div>';
  root.appendChild(section('Chat-command cooldowns', 'Per-viewer and global limits (seconds). Redeems use Twitch-native limits below.', cdRows));

  // Redeems
  const rd = c.redeems || {};
  let rdRows = `<div class="cfg-row2"><span class="lbl">Auto-fulfill redemptions (spend points)</span>${sw(rd.auto_fulfill !== false, 'id="cfg-rd-auto"')}</div>`;
  ['lucky','risky','coinflip','5050'].forEach(k => {
    const r = rd[k] || {};
    rdRows += `<div class="cfg-section" style="margin-top:10px"><h4>${esc(r.title || k)}</h4>
      <div class="cfg-row2"><span class="lbl">Enabled as a channel-point reward</span>${sw(r.enabled === true, `data-rd-enabled="${esc(k)}"`)}</div>
      <div class="cfg-row2"><span class="lbl">Cost (points)</span><input class="field num" type="number" min="1" value="${Number(r.cost)||100}" data-rd-cost="${esc(k)}"></div>
      <div class="cfg-row2"><span class="lbl" style="flex:1">Prompt</span><input class="field" style="flex:2" value="${esc(r.prompt||'')}" data-rd-prompt="${esc(k)}"></div>
    </div>`;
  });
  rdRows += '<div class="save-row"><button class="btn-primary" data-save="redeems">Save redeems</button><span class="mini">Costs/limits are pushed to Twitch on save.</span></div>';
  root.appendChild(section('Channel-point redeems', 'Which games are redeemable and at what cost.', rdRows));

  // Automation
  const au = c.automation || {};
  const auRows =
    toggle('Automation enabled (wheel outcomes act on Twitch)', 'cfg-au-enabled', au.enabled === true) +
    toggle('Allow scene switch', 'cfg-au-scene', au.allow_scene !== false) +
    toggle('Allow grant VIP', 'cfg-au-vip', au.allow_vip !== false) +
    toggle('Allow shoutout', 'cfg-au-shout', au.allow_shoutout !== false) +
    toggle('Allow timeout (risky wheel)', 'cfg-au-timeout', au.allow_timeout !== false) +
    '<div class="save-row"><button class="btn-primary" data-save="automation">Save automation</button></div>';
  root.appendChild(section('Automated outcomes', 'When on, wheel segments with an action actually change Twitch state.', auRows));

  // EventSub
  const ev = c.eventsub || {};
  const evRows =
    toggle('EventSub enabled (instant redemptions + hype)', 'cfg-ev-enabled', ev.enabled !== false) +
    toggle('Raid events', 'cfg-ev-raid', ev.raid !== false) +
    toggle('Cheer / bits events', 'cfg-ev-cheer', ev.cheer !== false) +
    toggle('Subscribe events', 'cfg-ev-sub', ev.subscribe !== false) +
    toggle('New-follower events', 'cfg-ev-follow', ev.follow !== false) +
    toggle('Free Lucky spin on raid', 'cfg-ev-spin', ev.raid_free_spin === true) +
    '<div class="save-row"><button class="btn-primary" data-save="eventsub">Save EventSub</button></div>';
  root.appendChild(section('EventSub', 'Real-time events for the hype overlay. Needs websocket-client.', evRows));

  // Viewer alerts
  const al = c.alerts || {};
  const alRows =
    toggle('First-time chatter alert', 'cfg-al-first', al.first_chat !== false) +
    `<div class="cfg-row2"><span class="lbl" style="flex:1">First-chat message (blank = overlay only)</span><input class="field" style="flex:2;min-width:0" id="cfg-al-firstmsg" maxlength="300" value="${esc(al.first_chat_message || '')}" placeholder="Welcome {user}! 👋"></div>` +
    toggle('New-follower alert', 'cfg-al-follow', al.follow_alert !== false) +
    `<div class="cfg-row2"><span class="lbl" style="flex:1">Follow message (blank = overlay only)</span><input class="field" style="flex:2;min-width:0" id="cfg-al-followmsg" maxlength="300" value="${esc(al.follow_message || '')}" placeholder="Thanks for the follow, {user}!"></div>` +
    '<div class="save-row"><button class="btn-primary" data-save="alerts">Save alerts</button></div>';
  root.appendChild(section('Viewer alerts', 'First-time chatters and new followers fire the hype overlay. Use {user} in messages.', alRows));

  // Wheels (JSON editor)
  wheelData = JSON.parse(JSON.stringify(c.wheels || {}));
  wheelMode = 'table';
  root.appendChild(section('Wheels', 'Segments on the Lucky and Risky wheels. Chance is each segment\'s weight out of the wheel\'s total. Validated on save.',
    `<div id="cfg-wheels-ui"></div>
     <textarea class="field" id="cfg-wheels" spellcheck="false" hidden></textarea>
     <div class="save-row"><button class="btn-primary" data-save="wheels">Save wheels</button>
       <button class="scene-btn" data-wh-act="mode">Edit as JSON</button>
       <span class="mini" id="cfg-wheels-err"></span></div>`));
  renderWheelEditor();
  if (!root.dataset.whWired) {
    root.dataset.whWired = '1';
    root.addEventListener('input', whEditorInput);
    root.addEventListener('change', whEditorInput);
    root.addEventListener('click', whEditorClick);
  }
}
// ── wheel segment table editor ──
// wheelData is the working copy; the table writes straight into it. Keys the
// table doesn't show (title, color, no_repeat, severe_cooldown_seconds, and any
// unknown segment fields) ride along untouched. JSON mode is the escape hatch.
let wheelData = {}, wheelMode = 'table';
function whChance(w, s) {
  const total = (w.segments || []).reduce((t, x) => t + (Number(x.weight ?? 1) || 0), 0);
  const wt = Number(s.weight ?? 1) || 0;
  return total ? (wt / total * 100) : 0;
}
function whOutcomeOpts(cur) {
  const keys = Object.keys(OUTCOME_LABELS);
  if (cur && !keys.includes(cur)) keys.push(cur);
  return '<option value="">(label only)</option>' + keys.map(k =>
    `<option value="${esc(k)}"${k === cur ? ' selected' : ''}>${esc(OUTCOME_LABELS[k] || k)}</option>`).join('');
}
function renderWheelEditor() {
  const box = $('#cfg-wheels-ui'); if (!box) return;
  box.innerHTML = Object.keys(wheelData).map(kind => {
    const w = wheelData[kind] || {}, segs = w.segments || [];
    return `<div class="wh-ed" data-wk="${esc(kind)}">
      <div class="wh-ed-head"><span class="wh-ed-dot" style="background:${esc(w.color || 'var(--purple)')}"></span>
        <strong>${esc(w.title || kind)}</strong><span class="mini">${segs.length} segments</span></div>
      <div class="wh-ed-grid">
        <span class="mini">Label</span><span class="mini">Weight</span><span class="mini">Outcome</span>
        <span class="mini" title="Severe outcomes share the wheel's severe cooldown">Severe</span><span class="mini">Chance</span><span></span>
        ${segs.map((s, i) => `
          <input class="field" maxlength="60" value="${esc(s.label || '')}" data-wh-f="label" data-wh-i="${i}" aria-label="Segment ${i + 1} label">
          <input class="field num" type="number" min="0" step="1" value="${esc(s.weight ?? 1)}" data-wh-f="weight" data-wh-i="${i}" aria-label="Segment ${i + 1} weight">
          <select class="field" data-wh-f="outcome" data-wh-i="${i}" aria-label="Segment ${i + 1} outcome">${whOutcomeOpts(s.outcome || '')}</select>
          <input type="checkbox" ${s.severe ? 'checked' : ''} data-wh-f="severe" data-wh-i="${i}" aria-label="Segment ${i + 1} severe">
          <span class="wh-chance mono" data-wh-chance="${i}">${whChance(w, s).toFixed(1)}%</span>
          <span class="wh-ed-btns">
            <button class="icon-btn" title="Move up" data-wh-act="up" data-wh-i="${i}" ${i === 0 ? 'disabled' : ''}>↑</button>
            <button class="icon-btn" title="Move down" data-wh-act="down" data-wh-i="${i}" ${i === segs.length - 1 ? 'disabled' : ''}>↓</button>
            <button class="icon-btn" title="Remove" data-wh-act="rm" data-wh-i="${i}" ${segs.length <= 1 ? 'disabled' : ''}>✕</button>
          </span>`).join('')}
      </div>
      <button class="scene-btn wh-add" data-wh-act="add">+ Add segment</button>
    </div>`;
  }).join('') || '<div class="stat-label">No wheels configured.</div>';
}
function whEditorInput(e) {
  const f = e.target.dataset?.whF; if (!f) return;
  const kind = e.target.closest('[data-wk]')?.dataset.wk, i = +e.target.dataset.whI;
  const w = wheelData[kind]; if (!w || !w.segments[i]) return;
  const s = w.segments[i];
  if (f === 'label') s.label = e.target.value;
  else if (f === 'weight') s.weight = Math.max(0, Number(e.target.value) || 0);
  else if (f === 'outcome') { if (e.target.value) s.outcome = e.target.value; else delete s.outcome; }
  else if (f === 'severe') { if (e.target.checked) s.severe = true; else delete s.severe; }
  if (f === 'weight') w.segments.forEach((x, j) => {
    const el = $(`[data-wk="${kind}"] [data-wh-chance="${j}"]`); if (el) el.textContent = whChance(w, x).toFixed(1) + '%';
  });
}
function whEditorClick(e) {
  const b = e.target.closest('[data-wh-act]'); if (!b || b.disabled) return;
  const act = b.dataset.whAct, err = $('#cfg-wheels-err');
  if (act === 'mode') {
    err.textContent = '';
    const ta = $('#cfg-wheels'), ui = $('#cfg-wheels-ui');
    if (wheelMode === 'table') {
      ta.value = JSON.stringify(wheelData, null, 2);
      wheelMode = 'json'; ta.hidden = false; ui.hidden = true; b.textContent = 'Back to table';
    } else {
      let parsed; try { parsed = JSON.parse(ta.value); } catch (x) { err.textContent = 'Invalid JSON: ' + x.message; return; }
      wheelData = parsed; renderWheelEditor();
      wheelMode = 'table'; ta.hidden = true; ui.hidden = false; b.textContent = 'Edit as JSON';
    }
    return;
  }
  const kind = b.closest('[data-wk]')?.dataset.wk, w = wheelData[kind]; if (!w) return;
  const segs = w.segments = w.segments || [], i = +b.dataset.whI;
  if (act === 'add') segs.push({ label: 'New segment', weight: 1 });
  if (act === 'rm' && segs.length > 1) segs.splice(i, 1);
  if (act === 'up' && i > 0) [segs[i - 1], segs[i]] = [segs[i], segs[i - 1]];
  if (act === 'down' && i < segs.length - 1) [segs[i + 1], segs[i]] = [segs[i], segs[i + 1]];
  renderWheelEditor();
  cfgMark('wheels', true);
  if (act === 'add') { const inp = $$(`[data-wk="${kind}"] [data-wh-f="label"]`).pop(); if (inp) { inp.focus(); inp.select(); } }
}

function section(title, hint, inner) {
  const el = document.createElement('div'); el.className = 'cfg-section';
  el.innerHTML = `<h4>${esc(title)}<span class="unsaved-dot">● unsaved</span></h4><div class="cfg-hint">${esc(hint)}</div>${inner}`;
  const key = el.querySelector('[data-save]')?.dataset.save;
  if (key) el.dataset.cfg = key;
  return el;
}
function toggle(label, id, checked) { return `<div class="cfg-row2"><span class="lbl">${esc(label)}</span>${sw(checked, `id="${id}"`)}</div>`; }

async function saveConfig(section, data) {
  const { data: d } = await post('/api/config/save', { section, data });
  if (d.ok) toast('Saved ' + section, 'ok'); else toast(d.error || 'Save failed', 'err');
  return d.ok;
}
async function onSave(what) {
  const ok = await onSaveSection(what);
  if (ok) cfgMark(what, false);
  return !!ok;
}
async function onSaveSection(what) {
  if (what === 'prefix') return saveConfig('command_prefix', $('#cfg-prefix').value || '!');
  if (what === 'cooldowns') {
    const out = { mods_bypass: $('#cfg-cd-mods').checked };
    CD_KEYS.forEach(k => out[k] = { user: +$(`[data-cd-user="${k}"]`).value || 0, global: +$(`[data-cd-global="${k}"]`).value || 0 });
    return saveConfig('cooldowns', out);
  }
  if (what === 'redeems') {
    const out = { auto_fulfill: $('#cfg-rd-auto').checked };
    ['lucky','risky','coinflip','5050'].forEach(k => out[k] = {
      enabled: $(`[data-rd-enabled="${k}"]`).checked,
      cost: +$(`[data-rd-cost="${k}"]`).value || 1,
      prompt: $(`[data-rd-prompt="${k}"]`).value,
    });
    return saveConfig('redeems', out);
  }
  if (what === 'automation') return saveConfig('automation', {
    enabled: $('#cfg-au-enabled').checked, allow_scene: $('#cfg-au-scene').checked, allow_vip: $('#cfg-au-vip').checked,
    allow_shoutout: $('#cfg-au-shout').checked, allow_timeout: $('#cfg-au-timeout').checked });
  if (what === 'eventsub') return saveConfig('eventsub', {
    enabled: $('#cfg-ev-enabled').checked, raid: $('#cfg-ev-raid').checked, cheer: $('#cfg-ev-cheer').checked,
    subscribe: $('#cfg-ev-sub').checked, follow: $('#cfg-ev-follow').checked, raid_free_spin: $('#cfg-ev-spin').checked });
  if (what === 'alerts') return saveConfig('alerts', {
    first_chat: $('#cfg-al-first').checked, first_chat_message: $('#cfg-al-firstmsg').value,
    follow_alert: $('#cfg-al-follow').checked, follow_message: $('#cfg-al-followmsg').value });
  if (what === 'wheels') {
    const errEl = $('#cfg-wheels-err'); errEl.textContent = '';
    if (wheelMode === 'table') {
      const blank = Object.entries(wheelData).find(([, w]) => (w.segments || []).some(s => !String(s.label || '').trim()));
      if (blank) { errEl.textContent = `${blank[1].title || blank[0]}: every segment needs a label`; toast('Wheels: a segment has no label', 'err'); return; }
      return saveConfig('wheels', wheelData);
    }
    let parsed; try { parsed = JSON.parse($('#cfg-wheels').value); }
    catch (e) { errEl.textContent = 'Invalid JSON: ' + e.message; toast('Wheels: invalid JSON', 'err'); return; }
    return saveConfig('wheels', parsed);
  }
}

// ── spotify now-playing ─────────────────────────────────────────────
async function loadSpotify() {
  const body = $('#spotify-body'); if (!body) return;
  let d; try { d = await api('/api/spotify'); } catch (e) { return; }
  if (!d.configured) {
    body.innerHTML = `<div class="stat-label">Set <code>spotify.client_id</code> in config.json (or <code>SPOTIFY_CLIENT_ID</code> in .env) and restart. Redirect URI to register in your Spotify app: <code>${esc(d.redirect_uri || 'http://127.0.0.1:5000/auth/spotify/callback')}</code></div>`;
    return;
  }
  if (d.status === 'ok') {
    const n = d.now || {};
    const track = n.title
      ? `<div class="now-track">🎵 <strong>${esc(n.title)}</strong>${n.artist ? ' by ' + esc(n.artist) : ''}${n.url ? ` &nbsp;<a href="${esc(n.url)}" target="_blank" rel="noopener">open</a>` : ''}</div>`
      : '<div class="stat-label">Nothing playing right now.</div>';
    body.innerHTML = `<div class="auth-row"><span class="status-dot on"></span>Connected</div>${track}
      <div class="auth-actions"><button class="scene-btn" style="flex:0 0 auto" data-act="sp-logout">Disconnect</button></div>`;
  } else if (d.status === 'pending') {
    body.innerHTML = `<div class="auth-row"><span class="status-dot warn"></span>Waiting for Spotify…</div>` +
      (d.authorize_url ? `<div class="scene-sub"><a href="${esc(d.authorize_url)}" target="_blank" rel="noopener">Reopen Spotify login</a></div>` : '');
  } else {
    body.innerHTML = `<div class="auth-row"><span class="status-dot ${d.status === 'error' ? 'warn' : 'off'}"></span>${d.status === 'error' ? esc(d.error || 'Error') : 'Not connected'}</div>
      <div class="auth-actions"><button class="btn-primary" data-act="sp-auth">Connect Spotify</button></div>`;
  }
}
async function spAuth()   { $('#spotify-body').innerHTML = '<div class="stat-label">Opening Spotify login…</div>'; try { await post('/auth/spotify'); } catch (e) {} setTimeout(loadSpotify, 1500); }
async function spLogout() { try { await post('/auth/spotify/logout'); toast('Spotify disconnected'); } catch (e) {} loadSpotify(); }

// ── live overlay preview ────────────────────────────────────────────
function scalePreview() {
  const frame = $('#preview-frame'), ifr = $('#preview-iframe');
  if (!frame || !ifr) return;
  ifr.style.transform = `scale(${frame.clientWidth / 1920})`;
}
function switchPreview(name) {
  const ifr = $('#preview-iframe'); if (!ifr) return;
  ifr.src = `/static/interactive/${name}.html?muted`;
  $$('.preview-btn').forEach(b => b.classList.toggle('active', b.dataset.preview === name));
  scalePreview();
}
window.addEventListener('resize', scalePreview);

// ── restart / shutdown ──────────────────────────────────────────────
// Two clicks to fire, because there is no undo: the second click stops the
// thing that is currently running your stream.
const CONN_LOST = 'Connection to Stream Manager lost. Retrying…';
let lcArmed = null, lcArmTimer = null;

function connBanner(msg, show) {
  const b = $('#conn-banner'); if (!b) return;
  b.textContent = msg;
  b.classList.toggle('show', show !== false);
}
function lcDisarm() {
  clearTimeout(lcArmTimer); lcArmTimer = null; lcArmed = null;
  ['#lc-restart', '#lc-shutdown'].forEach(sel => {
    const b = $(sel); if (!b) return;
    b.classList.remove('armed');
    b.textContent = b.dataset.label || b.textContent;
  });
}
function lcArm(action) {
  if (lcMode) return;                       // already stopping; ignore further clicks
  const btn = $(action === 'restart' ? '#lc-restart' : '#lc-shutdown');
  if (!btn || btn.disabled) return;
  if (lcArmed !== action) {
    lcDisarm();
    lcArmed = action;
    btn.classList.add('armed');
    btn.textContent = 'Click again to confirm';
    lcArmTimer = setTimeout(lcDisarm, 5000);
    return;
  }
  lcDisarm();
  lcFire(action);
}
async function lcFire(action) {
  // The pid is how we tell "it came back" from "it never left": the old
  // process keeps answering for the fraction of a second it takes to flush
  // this very response.
  let pidBefore = null;
  try { pidBefore = (await (await fetch('/api/ping', { cache: 'no-store' })).json()).pid; } catch (e) {}

  // Not bare: lcFire is called un-awaited, so a rejected fetch here used to be
  // an unhandled promise: lcMode never set, both buttons still live, nothing
  // on screen, and the server already on its way down. If the request did land,
  // the started_at watch above reloads the page when it comes back.
  let r;
  try {
    r = await post('/api/lifecycle/' + action, { confirm: true });
  } catch (e) {
    toast('Couldn\'t reach Stream Manager; it may be stopping anyway.', 'bad');
    return;
  }
  if (!r.ok || !r.data.ok) {
    const msg = r.data.error || (action + ' failed');
    $('#lc-msg').textContent = msg;
    toast(msg, 'bad');
    return;
  }
  lcMode = action;
  ['#lc-restart', '#lc-shutdown'].forEach(sel => { const b = $(sel); if (b) b.disabled = true; });

  if (action === 'shutdown') {
    $('#lc-msg').textContent = '';
    connBanner('Stream Manager has shut down. Start it again from the launcher.');
    return;
  }
  connBanner('Restarting Stream Manager. This page reloads by itself when it\'s back.');
  lcWait(pidBefore);
}
async function lcWait(pidBefore) {
  // A reload is not cosmetic: the session token is regenerated every start, so
  // this page's token is dead the moment the old process exits.
  let sawGap = false;
  for (let i = 0; i < 90; i++) {
    await new Promise(res => setTimeout(res, 1000));
    let p = null;
    try { p = await (await fetch('/api/ping', { cache: 'no-store' })).json(); }
    catch (e) { sawGap = true; continue; }
    if (!p || !p.pid) { sawGap = true; continue; }
    if (sawGap || (pidBefore && p.pid !== pidBefore)) { location.reload(); return; }
  }
  connBanner('Stream Manager hasn\'t come back. Check the launcher window.');
}

// ── Wheel tab ───────────────────────────────────────────────────────
const OUTCOME_LABELS = {
  featured: '★ Featured', dj: '🎧 DJ', vip: '⭐ VIP 7d', game_poll: '🎮 Game poll',
  emote_party: '🥳 Emote party', name_run: '✍️ Name run', shoutout: '📣 Shoutout', jackpot: '🎉 Jackpot',
  tuber_flip: '🙃 Flip', cursed_tint: '🧪 Tint', overlay_swap: '🎨 Overlay swap', slow_mode: '🐌 Slow mode',
  chat_bets: '🎲 Prediction', silly_voice: '🤪 Silly voice', bad_pun: '😹 Pun', timeout: '⏱️ Timeout',
  nothing: '😈 Nothing',
};
const REQ_TEXT = { live: 'you\'re offline', target: 'needs a real viewer', streamer: 'needs a viewer who streams',
  vip_ok: 'viewer is a mod or a permanent VIP', spotify: 'Spotify not connected or not playing',
  steam: 'Steam library not found', obs: 'OBS WebSocket not reachable / PNG TUBER not found',
  overlay_sets: 'only one overlay set', not_mod: 'viewer is a mod' };
const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
const ago = ts => fmtAgo(ts);

async function renderWheel() {
  if (!$('#wh-fx')) return;
  try {
    const [fx, ow, ft, el] = await Promise.all([api('/api/timed?all=1'), api('/api/owed'),
                                               api('/api/featured'), api('/api/wheel/eligibility')]);
    const short = fx.effects.filter(e => !e.long), vips = fx.effects.filter(e => e.long);
    $('#wh-fx').innerHTML = short.length ? short.map(e => `<div class="wh-row"><span class="wh-text">${esc(e.label)}
        <span class="wh-sub">· ${esc(e.user || '')} · ${mmss(e.remaining)} left</span></span>
        <button class="scene-btn" data-fx-end="${esc(e.id)}">Undo</button></div>`).join('')
      : '<div class="stat-label">Nothing active.</div>';
    $('#wh-vip').innerHTML = vips.length ? vips.map(e => `<div class="wh-row"><span class="wh-text">${esc(e.label)}
        <span class="wh-sub">· ${Math.ceil(e.remaining / 86400)}d left</span></span>
        <button class="scene-btn" data-fx-end="${esc(e.id)}">Revoke</button></div>`).join('')
      : '<div class="stat-label">None right now.</div>';
    $('#wh-owed').innerHTML = ow.items.length ? ow.items.map(i => `<div class="wh-row"><span class="wh-text" title="${esc(i.detail || '')}">${esc(i.text)}
        <span class="wh-sub">· ${ago(i.created)}</span></span>
        <button class="scene-btn" data-owed="${esc(i.id)}" data-st="done">Done</button>
        <button class="scene-btn" data-owed="${esc(i.id)}" data-st="skipped">Skip</button></div>`).join('')
      : '<div class="stat-label">You\'re all caught up.</div>';
    $('#wh-feat').innerHTML = ft.viewers.length ? ft.viewers.slice().reverse().map(v => `<div class="wh-row"><span class="wh-text">${esc(v.name)}
        <span class="wh-sub">· ${ago(v.ts)}</span></span>
        <button class="scene-btn" data-feat-rm="${esc(v.login)}">Remove</button></div>`).join('')
      : '<div class="stat-label">Nobody featured yet.</div>';
    $('#wh-outcomes').innerHTML = Object.keys(OUTCOME_LABELS).map(k => {
      const info = el[k] || { eligible: false, missing: [] };
      const why = (info.missing || []).map(r => REQ_TEXT[r] || r).join(', ');
      return `<button class="scene-btn" data-outcome="${k}" ${info.eligible ? '' : 'disabled'} title="${esc(why)}">${OUTCOME_LABELS[k]}</button>`;
    }).join('');
  } catch (e) { /* offline banner already covers this */ }
}
async function whUndoAll() {
  const r = await post('/api/timed/undo-all');
  toast(r.ok ? `Undid ${r.data.undone} effect(s)` : 'Undo failed', r.ok ? 'ok' : 'err'); renderWheel();
}
async function whEndFx(id) { const r = await post('/api/timed/end', { id }); if (!r.ok) toast('Could not undo that one', 'err'); renderWheel(); }
async function whOwed(id, st) { await post('/api/owed/resolve', { id, status: st }); renderWheel(); }
async function whFeatRm(login) { await post('/api/featured/remove', { login }); renderWheel(); }
async function whTest(outcome) {
  const r = await post('/api/wheel/test', { outcome, chat: testChat });
  const m = $('#wh-msg'); if (m) m.textContent = r.ok ? `✓ ${OUTCOME_LABELS[outcome]}: ${r.data.message || 'done'}` : `✗ ${r.data.error || r.data.message || 'failed'}`;
  renderWheel();
}
async function renderSteam() {
  const box = $('#wh-steam'); if (!box) return;
  try {
    const d = await api('/api/steam');
    if (!d.ok) { box.innerHTML = `<div class="stat-label">${esc(d.error || 'No installed games found.')}</div>`; return; }
    const pinned = new Set(d.pinned), hidden = new Set(d.hidden);
    $('#wh-steam-sub').textContent = `${d.count} installed games. Pinned games are always offered; hidden ones never are.`;
    box.innerHTML = d.games.map(g => {
      const st = pinned.has(g.appid) ? 'pinned' : hidden.has(g.appid) ? 'hidden' : 'normal';
      return `<div class="wh-row ${st === 'pinned' ? 'pinned' : st === 'hidden' ? 'hidden-game' : ''}">
        <span class="wh-text" title="${esc(g.name)}">${esc(g.name)}</span>
        <button class="scene-btn" data-steam="${g.appid}" data-mark="${st === 'pinned' ? 'normal' : 'pinned'}">${st === 'pinned' ? 'Unpin' : 'Pin'}</button>
        <button class="scene-btn" data-steam="${g.appid}" data-mark="${st === 'hidden' ? 'normal' : 'hidden'}">${st === 'hidden' ? 'Show' : 'Hide'}</button></div>`;
    }).join('');
  } catch (e) {}
}
async function whSteam(appid, mark) { await post('/api/steam/mark', { appid: +appid, mark }); renderSteam(); }

// ── global action delegation ────────────────────────────────────────
document.addEventListener('click', e => {
  const t = e.target;
  const scene = t.closest('[data-scene]'); if (scene && !scene.disabled) return switchScene(scene.dataset.scene);
  const test = t.closest('[data-test]'); if (test) return ixTest(test.dataset.test);
  const del = t.closest('[data-cc-del]'); if (del) return deleteCustom(del.dataset.ccDel);
  const tmDel = t.closest('[data-tm-del]'); if (tmDel) return deleteTimerRow(+tmDel.dataset.tmDel);
  const qDel = t.closest('[data-q-del]'); if (qDel) return deleteQuote(qDel.dataset.qDel);
  const qEd = t.closest('[data-q-edit]'); if (qEd) return editQuote(qEd.dataset.qEdit);
  const qSv = t.closest('[data-q-save]'); if (qSv) return saveQuoteEdit(qSv.dataset.qSave);
  if (t.closest('[data-q-cancel]')) return cancelQuoteEdit();
  const ccEd = t.closest('[data-cc-edit]'); if (ccEd) return editCustom(ccEd.dataset.ccEdit);
  const prev = t.closest('[data-preview]'); if (prev) return switchPreview(prev.dataset.preview);
  const save = t.closest('[data-save]'); if (save) return onSave(save.dataset.save);
  const owedBtn = t.closest('[data-owed]'); if (owedBtn) return whOwed(owedBtn.dataset.owed, owedBtn.dataset.st);
  const fxEnd = t.closest('[data-fx-end]'); if (fxEnd) return whEndFx(fxEnd.dataset.fxEnd);
  const featRm = t.closest('[data-feat-rm]'); if (featRm) return whFeatRm(featRm.dataset.featRm);
  const steamBtn = t.closest('[data-steam]'); if (steamBtn) return whSteam(steamBtn.dataset.steam, steamBtn.dataset.mark);
  const oc = t.closest('[data-outcome]'); if (oc && !oc.disabled) return whTest(oc.dataset.outcome);
  const act = t.closest('[data-act]'); if (!act) return;
  ({ 'update-install': installUpdate, 'ix-auth': ixAuth, 'ix-logout': ixLogout, 'ix-reload': ixReload,
     'cc-add': saveCustom, 'tm-add': addTimerRow, 'tm-save': saveTimers, 'q-add': addQuote,
     'sp-auth': spAuth, 'sp-logout': spLogout, 'fx-undo-all': whUndoAll,
     'preflight': runPreflight, 'hm-mute': hmToggleMute,
     'lc-restart': () => lcArm('restart'), 'lc-shutdown': () => lcArm('shutdown'),
     'cfg-saveall': cfgSaveAll, 'cfg-discard': cfgDiscard, 'cc-cancel': cancelCustomEdit
   }[act.dataset.act] || (() => {}))();
});
document.addEventListener('change', e => {
  const bt = e.target.closest('[data-cmd-toggle]'); if (bt) return toggleCommand(bt.dataset.cmdToggle, e.target.checked);
  const ct = e.target.closest('[data-cc-toggle]'); if (ct) return toggleCustom(ct.dataset.ccToggle, e.target.checked);
});


// ── Stream Health Monitor ───────────────────────────────────────────
let hmSeen = new Set();        // alert ids we've already toasted
let hmPrimed = false;          // first sample is a snapshot, not news
let hmMuted = false;           // audible alerts off?
try { hmMuted = localStorage.getItem('sm_hm_mute') === '1'; } catch (e) {}
let hmAudio = null, hmLastBeep = 0;

function hmBeep() {
  if (hmMuted) return;
  const now = Date.now();
  if (now - hmLastBeep < 20000) return;   // never more than one beep / 20s
  hmLastBeep = now;
  try {
    const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
    hmAudio = hmAudio || new AC();
    if (hmAudio.state === 'suspended') hmAudio.resume();
    [0, 0.22].forEach(off => {
      const o = hmAudio.createOscillator(), g = hmAudio.createGain();
      const t = hmAudio.currentTime + off;
      o.type = 'sine'; o.frequency.setValueAtTime(880, t);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.16, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
      o.connect(g); g.connect(hmAudio.destination); o.start(t); o.stop(t + 0.2);
    });
  } catch (e) {}
}

const HM_METRICS = [
  ['dropped_pct',  'Dropped (net)', '%',    v => v.toFixed(1), 'dropped'],
  ['congestion',   'Congestion',    '',     v => v.toFixed(2), 'congestion'],
  ['bitrate_kbps', 'Bitrate',       'kbps', v => fmtNum(Math.round(v)), 'bitrate'],
  ['bitrate_pct',  'Of target',     '%',    v => v.toFixed(0), 'bitrate'],
  ['fps',          'FPS',           '',     v => v.toFixed(0), null],
  ['render_pct',   'Render lag',    '%',    v => v.toFixed(1), 'render'],
  ['encode_pct',   'Encode lag',    '%',    v => v.toFixed(1), 'encode'],
  ['render_ms',    'Frame time',    'ms',   v => v.toFixed(1), null],
  ['cpu',          'CPU',           '%',    v => v.toFixed(0), 'cpu'],
  ['ram_pct',      'Memory',        '%',    v => v.toFixed(0), 'ram'],
  ['disk_mb',      'Free disk',     'GB',   v => (v / 1024).toFixed(1), 'disk'],
];

function renderMonitor(d) {
  const alerts = d.alerts || [], checks = d.checks || [], m = d.metrics || {};
  const worst = alerts.some(a => a.level === 'bad') ? 'bad'
              : (alerts.length ? 'warn' : 'ok');

  // banner
  const b = $('#health-banner');
  if (b) {
    if (alerts.length) {
      const bad = alerts.filter(a => a.level === 'bad');
      const show = bad.length ? bad : alerts;
      const extra = alerts.length > show.length ? ` (+${alerts.length - show.length} more)` : '';
      b.textContent = (worst === 'bad' ? '⚠ ' : '● ') +
        show.map(a => a.message).join(' · ') + extra;
      b.className = 'health-banner show' + (worst === 'bad' ? '' : ' warn');
    } else { b.className = 'health-banner'; b.textContent = ''; }
  }

  // toast + beep on newly raised alerts
  const ids = new Set(alerts.map(a => a.id));
  if (hmPrimed) {
    alerts.forEach(a => {
      if (hmSeen.has(a.id)) return;
      toast((a.level === 'bad' ? '⚠ ' : '') + a.message, a.level === 'bad' ? 'err' : '');
      if (a.level === 'bad') hmBeep();
    });
    hmSeen.forEach(id => { if (!ids.has(id)) toast('Recovered: ' + id, 'ok'); });
  } else {
    // The first sample after a page load reports what is ALREADY wrong, which
    // is not news; it is the current state. Without this, opening or
    // refreshing the dashboard while any alert is live toasts every one of
    // them at once and beeps for each bad one. Take it as the baseline.
    hmPrimed = true;
  }
  hmSeen = ids;

  if (!$('#tab-health')?.classList.contains('active')) return;   // panel hidden

  const sub = $('#hm-sub');
  if (sub) {
    if (!m.obs_ws) sub.textContent = 'OBS WebSocket: ' + (m.obs_ws_error || 'not reachable. Enable it in OBS → Tools → WebSocket Server Settings.');
    else if (m.live) sub.textContent = `Live · ${fmtUptime(m.duration_sec || 0)} · monitoring every few seconds`;
    else sub.textContent = 'OBS connected · not streaming. Stream metrics appear when you go live.';
  }

  const lv = {};
  checks.forEach(c => { lv[c.id] = c.status; });
  const grid = $('#hm-metrics');
  if (grid) {
    grid.innerHTML = HM_METRICS.filter(([k]) => m[k] !== undefined && m[k] !== null)
      .map(([k, label, unit, fmt, cid]) => {
        const cls = cid && lv[cid] ? lv[cid] : '';
        return `<div class="metric ${cls}"><div class="m-label">${esc(label)}</div>` +
               `<div class="m-val">${esc(fmt(Number(m[k])))}` +
               (unit ? `<span class="unit">${esc(unit)}</span>` : '') + `</div></div>`;
      }).join('') || '<div class="stat-label">No metrics yet; waiting for the first sample.</div>';
  }

  const list = $('#hm-checks');
  if (list) {
    list.innerHTML = checks.length ? checks.map(c =>
      `<div class="chk-row ${c.status === 'ok' ? '' : c.status}">` +
      `<span class="status-dot ${c.status === 'ok' ? 'on' : (c.status === 'warn' ? 'warn' : 'off')}"></span>` +
      `<span class="chk-label">${esc(c.label)}</span>` +
      `<span class="chk-msg">${esc(c.message)}</span></div>`).join('')
      : '<div class="stat-label">No checks yet.</div>';
  }
}

async function pollMonitor() {
  try { renderMonitor(await api('/api/health/monitor')); } catch (e) {}
}

async function runPreflight() {
  const sum = $('#pf-summary'), list = $('#pf-list');
  if (sum) sum.textContent = 'Checking…';
  if (list) list.innerHTML = '';
  let d;
  try { d = await api('/api/health/preflight'); }
  catch (e) { if (sum) { sum.textContent = 'Preflight failed: ' + e.message; sum.style.color = 'var(--err)'; } return; }
  if (sum) {
    sum.textContent = d.summary || '';
    sum.style.color = !d.ready ? 'var(--err)' : (d.warnings ? 'var(--warn)' : 'var(--ok)');
  }
  if (list) {
    list.innerHTML = (d.items || []).map(i =>
      `<div class="chk-row ${i.status === 'ok' ? '' : i.status}">` +
      `<span class="status-dot ${i.status === 'ok' ? 'on' : (i.status === 'warn' ? 'warn' : 'off')}"></span>` +
      `<span class="chk-label">${esc(i.label)}</span>` +
      `<span class="chk-msg">${esc(i.message)}</span></div>`).join('');
  }
  toast(d.ready ? 'Preflight passed, ready to stream' : 'Preflight found blockers',
        d.ready ? 'ok' : 'err');
}

function hmToggleMute() {
  hmMuted = !hmMuted;
  try { localStorage.setItem('sm_hm_mute', hmMuted ? '1' : '0'); } catch (e) {}
  const btn = $('#hm-mute'); if (btn) btn.textContent = hmMuted ? 'Unmute alerts' : 'Mute alerts';
  toast(hmMuted ? 'Audible health alerts muted' : 'Audible health alerts on');
}

// ── poll loops ──────────────────────────────────────────────────────
async function pollStatus() { try { renderStatus(await api('/api/status')); } catch (e) { setOnline(false); } }
async function pollScenes() { if (sceneSwitching) return; try { renderScenes(await api('/api/scenes')); } catch (e) {} }

setInterval(pollStatus, 2000); pollStatus();
setInterval(pollScenes, 4000); pollScenes();
setInterval(renderInteractive, 3000); renderInteractive();
setInterval(renderWheel, 3000); renderWheel();
setInterval(renderSteam, 60000); renderSteam();
setInterval(renderStats, 5000); renderStats();
setInterval(pollMonitor, 3000); pollMonitor();
checkUpdate(); setInterval(checkUpdate, 3600000);
loadSpotify(); setInterval(loadSpotify, 5000);
scalePreview(); setTimeout(scalePreview, 300);
initSSE();

// restore last tab
try { const t = localStorage.getItem('sm_tab'); if (t && t !== 'overview') showTab(t); } catch (e) {}
const logCb = $('#log-events-only');
if (logCb) {
  logCb.checked = logEventsOnly;
  logCb.addEventListener('change', () => {
    logEventsOnly = logCb.checked;
    try { localStorage.setItem('sm_log_events', logEventsOnly ? '1' : '0'); } catch (e) {}
    renderLog(true);
  });
}
// Quotes: live search; Enter saves / Escape cancels an inline edit.
$('#q-search')?.addEventListener('input', renderQuotes);
document.addEventListener('keydown', e => {
  if (e.target.id !== 'q-edit-text') return;
  if (e.key === 'Enter') { e.preventDefault(); saveQuoteEdit(quoteEditing); }
  if (e.key === 'Escape') cancelQuoteEdit();
});
})();
