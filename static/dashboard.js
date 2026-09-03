/* Stream Manager dashboard — vanilla, no build step. */
(() => {
'use strict';

const TOK = document.querySelector('meta[name="sm-token"]')?.content || '';
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
const fmtNum = n => (Number(n) || 0).toLocaleString();

// ── connection state ────────────────────────────────────────────────
let online = true;
function setOnline(v) {
  if (v === online) return;
  online = v;
  $('#conn-banner')?.classList.toggle('show', !v);
  const pill = $('#conn-pill'); if (pill) pill.classList.toggle('off', !v);
  const t = $('#conn-text'); if (t) t.textContent = v ? 'Live' : 'Offline';
  const d = $('#conn-pill .status-dot'); if (d) d.className = 'status-dot ' + (v ? 'on' : 'off');
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
  if (name === 'config') loadConfig();
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
function renderStatus(s) {
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
  $('#twitch-title').textContent = s.twitch.title || '—';
  $('#twitch-game').textContent = s.twitch.game || '—';
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

  $('#cpu-pct').textContent = s.system.cpu;
  $('#cpu-bar').style.width = s.system.cpu + '%';
  $('#ram-used').textContent = s.system.ram_used_gb;
  $('#ram-total').textContent = s.system.ram_total_gb;
  $('#ram-bar').style.width = s.system.ram_pct + '%';
  $('#ram-pct-label').textContent = s.system.ram_pct + '% used';
  if (s.system.gpu) $('#gpu-name').textContent = s.system.gpu;

  if (s.requests && s.requests.length && s.requests[0] !== lastLogHead) {
    lastLogHead = s.requests[0];
    $('#log-box').innerHTML = s.requests.map(r => {
      const m = r.match(/^\[(\d+:\d+:\d+)\]\s+(.*)/);
      return m
        ? `<div class="log-entry"><span class="log-timestamp">[${esc(m[1])}]</span> <span class="log-text">${esc(m[2])}</span></div>`
        : `<div class="log-entry"><span class="log-text">${esc(r)}</span></div>`;
    }).join('');
  }
  renderHealth();
}
function fmtUptime(secs) {
  if (!secs || secs <= 0) return '';
  const h = Math.floor(secs/3600), m = Math.floor((secs%3600)/60), s = secs%60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
}

// ── overview: scenes ────────────────────────────────────────────────
const SCENE_LABELS = { modern: 'Modern Neon', retro: 'Retro Win98' };
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
    if (d.ok) { msg.textContent += ' — refresh your OBS browser sources.'; toast('Scene set → ' + (SCENE_LABELS[name] || name), 'ok'); }
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
    if (d.ok) { msg.textContent += ' Restart to apply.'; toast('Update installed — restart to apply', 'ok'); }
    else btn.disabled = false;
  } catch (e) { msg.className = 'update-msg err'; msg.textContent = 'Install request failed.'; btn.disabled = false; }
}

// ── interactive ─────────────────────────────────────────────────────
function ixWhen(ts) {
  const d = Math.max(0, Math.floor(Date.now()/1000 - ts));
  if (d < 60) return d + 's ago'; if (d < 3600) return Math.floor(d/60) + 'm ago'; return Math.floor(d/3600) + 'h ago';
}
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
    label.textContent = 'Authorized as ' + (d.auth.login || '—');
    const chat = d.chat?.connected ? 'chat connected' : 'chat connecting…';
    const rd = d.redeems?.ready ? 'redeems ready' : 'redeems setting up…';
    hint.textContent = chat + ' · ' + rd + (d.chat?.channel ? ' · #' + d.chat.channel : '');
    logoutBtn.style.display = 'inline-block';
  } else if (st === 'pending') {
    dot.className = 'status-dot warn'; label.textContent = 'Waiting for Twitch…';
    hint.innerHTML = 'A browser window opened — click <strong>Authorize</strong> there.' +
      (d.auth.authorize_url ? ` &nbsp;<a href="${esc(d.auth.authorize_url)}" target="_blank" rel="noopener">Reopen login</a>` : '');
  } else if (st === 'unauthorized' || st === 'error') {
    dot.className = 'status-dot warn'; label.textContent = 'Connect your Twitch account';
    hint.textContent = d.auth?.error || 'One click — a browser window will open for you to approve access.';
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
    </div>`).join('') : '<div class="stat-label">No timers yet — add one below.</div>';
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
  $('#quote-count').textContent = d.count ? `(${d.count})` : '';
  const qs = d.quotes || [];
  $('#quote-list').innerHTML = qs.length ? qs.slice().reverse().map(q => `
    <div class="cmd-row quote-row">
      <div class="cmd-main">
        <div class="cmd-desc"><span class="qnum">#${esc(q.id)}</span>${esc(q.text)}</div>
        <div class="mini">${esc(q.added_by || '')}${q.date ? ' · ' + esc(q.date) : ''}</div>
      </div>
      <button class="icon-btn" data-q-del="${esc(q.id)}" title="Delete">✕</button>
    </div>`).join('') : '<div class="stat-label">No quotes yet.</div>';
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
async function ixTest(action) {
  const msg = $('#ix-msg'); msg.className = 'scene-msg'; msg.textContent = 'Triggering ' + action + '…';
  try {
    const { data: d } = await post('/api/interactive/test', { action, user: 'Dashboard' });
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
  $('#cmd-custom').innerHTML = custom.length ? custom.map(c => `
    <div class="cmd-row">
      <div class="cmd-main">
        <div class="cmd-name"><span class="mono">${esc(pfx + c.name)}</span> ${c.permission === 'mods' ? '<span class="cmd-badge">mods</span>' : ''}</div>
        <div class="cmd-desc">${esc(c.response)}</div>
      </div>
      ${sw(c.enabled, `data-cc-toggle="${esc(c.name)}"`)}
      <button class="icon-btn" title="Delete" data-cc-del="${esc(c.name)}">✕</button>
    </div>`).join('') : '<div class="stat-label">No custom commands yet.</div>';
}
async function toggleCommand(name, enabled) {
  const { data: d } = await post('/api/commands/toggle', { name, enabled });
  if (d.ok) toast(`${name} ${enabled ? 'enabled' : 'disabled'}`, 'ok'); else { toast('Toggle failed', 'err'); loadCommands(); }
}
async function saveCustom() {
  const name = $('#cc-name').value.trim(), response = $('#cc-response').value.trim(), permission = $('#cc-perm').value;
  const msg = $('#cmd-msg'); msg.className = 'scene-msg';
  if (!name || !response) { msg.classList.add('err'); msg.textContent = 'Name and response are required.'; return; }
  const { data: d } = await post('/api/commands/custom', { action: 'add', name, response, permission, enabled: true });
  msg.classList.add(d.ok ? 'ok' : 'err'); msg.textContent = d.message || (d.ok ? 'Saved.' : 'Failed.');
  if (d.ok) { $('#cc-name').value = ''; $('#cc-response').value = ''; toast('Added !' + name, 'ok'); loadCommands(); }
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
const CD_KEYS = ['coinflip','5050','slots','dice','duel','lucky','risky','quote'];
async function loadConfig() {
  let c; try { c = await api('/api/config'); } catch (e) { $('#cfg-root').innerHTML = '<div class="stat-label">Could not load.</div>'; return; }
  const root = $('#cfg-root'); root.innerHTML = '';

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
  root.appendChild(section('Wheels (advanced)', 'Edit Lucky/Risky wheel segments as JSON. Validated on save.',
    `<textarea class="field" id="cfg-wheels" spellcheck="false">${esc(JSON.stringify(c.wheels || {}, null, 2))}</textarea>
     <div class="save-row"><button class="btn-primary" data-save="wheels">Save wheels</button><span class="mini" id="cfg-wheels-err"></span></div>`));
}
function section(title, hint, inner) {
  const el = document.createElement('div'); el.className = 'cfg-section';
  el.innerHTML = `<h4>${esc(title)}</h4><div class="cfg-hint">${esc(hint)}</div>${inner}`;
  return el;
}
function toggle(label, id, checked) { return `<div class="cfg-row2"><span class="lbl">${esc(label)}</span>${sw(checked, `id="${id}"`)}</div>`; }

async function saveConfig(section, data) {
  const { data: d } = await post('/api/config/save', { section, data });
  if (d.ok) toast('Saved ' + section, 'ok'); else toast(d.error || 'Save failed', 'err');
  return d.ok;
}
async function onSave(what) {
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
    body.innerHTML = `<div class="stat-label">Add <code>SPOTIFY_CLIENT_ID</code> to your .env to enable now-playing. Redirect URL to register: <code>${esc(location.origin)}/auth/spotify/callback</code></div>`;
    return;
  }
  if (d.status === 'ok') {
    const n = d.now || {};
    const track = n.title
      ? `<div class="now-track">🎵 <strong>${esc(n.title)}</strong> — ${esc(n.artist || '')}${n.url ? ` &nbsp;<a href="${esc(n.url)}" target="_blank" rel="noopener">open</a>` : ''}</div>`
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

// ── global action delegation ────────────────────────────────────────
document.addEventListener('click', e => {
  const t = e.target;
  const scene = t.closest('[data-scene]'); if (scene && !scene.disabled) return switchScene(scene.dataset.scene);
  const test = t.closest('[data-test]'); if (test) return ixTest(test.dataset.test);
  const del = t.closest('[data-cc-del]'); if (del) return deleteCustom(del.dataset.ccDel);
  const tmDel = t.closest('[data-tm-del]'); if (tmDel) return deleteTimerRow(+tmDel.dataset.tmDel);
  const qDel = t.closest('[data-q-del]'); if (qDel) return deleteQuote(qDel.dataset.qDel);
  const prev = t.closest('[data-preview]'); if (prev) return switchPreview(prev.dataset.preview);
  const save = t.closest('[data-save]'); if (save) return onSave(save.dataset.save);
  const act = t.closest('[data-act]'); if (!act) return;
  ({ 'update-install': installUpdate, 'ix-auth': ixAuth, 'ix-logout': ixLogout, 'ix-reload': ixReload,
     'cc-add': saveCustom, 'tm-add': addTimerRow, 'tm-save': saveTimers, 'q-add': addQuote,
     'sp-auth': spAuth, 'sp-logout': spLogout,
     'preflight': runPreflight, 'hm-mute': hmToggleMute }[act.dataset.act] || (() => {}))();
});
document.addEventListener('change', e => {
  const bt = e.target.closest('[data-cmd-toggle]'); if (bt) return toggleCommand(bt.dataset.cmdToggle, e.target.checked);
  const ct = e.target.closest('[data-cc-toggle]'); if (ct) return toggleCustom(ct.dataset.ccToggle, e.target.checked);
});


// ── Stream Health Monitor ───────────────────────────────────────────
let hmSeen = new Set();        // alert ids we've already toasted
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
  alerts.forEach(a => {
    if (hmSeen.has(a.id)) return;
    toast((a.level === 'bad' ? '⚠ ' : '') + a.message, a.level === 'bad' ? 'err' : '');
    if (a.level === 'bad') hmBeep();
  });
  hmSeen.forEach(id => { if (!ids.has(id)) toast('Recovered: ' + id, 'ok'); });
  hmSeen = ids;

  if (!$('#tab-health')?.classList.contains('active')) return;   // panel hidden

  const sub = $('#hm-sub');
  if (sub) {
    if (!m.obs_ws) sub.textContent = 'OBS WebSocket: ' + (m.obs_ws_error || 'not reachable — enable it in OBS → Tools → WebSocket Server Settings.');
    else if (m.live) sub.textContent = `Live · ${fmtUptime(m.duration_sec || 0)} · monitoring every few seconds`;
    else sub.textContent = 'OBS connected · not streaming — stream metrics appear when you go live.';
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
      }).join('') || '<div class="stat-label">No metrics yet — waiting for the first sample.</div>';
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
  catch (e) { if (sum) { sum.textContent = 'Preflight failed — ' + e.message; sum.style.color = 'var(--err)'; } return; }
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
  toast(d.ready ? 'Preflight passed — ready to stream' : 'Preflight found blockers',
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
setInterval(renderStats, 5000); renderStats();
setInterval(pollMonitor, 3000); pollMonitor();
checkUpdate(); setInterval(checkUpdate, 3600000);
loadSpotify(); setInterval(loadSpotify, 5000);
scalePreview(); setTimeout(scalePreview, 300);
initSSE();

// restore last tab
try { const t = localStorage.getItem('sm_tab'); if (t && t !== 'overview') showTab(t); } catch (e) {}
})();
