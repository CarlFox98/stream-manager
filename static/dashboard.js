let lastLogHead = null;

function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent = now.toLocaleTimeString();
  document.getElementById('clock-date').textContent = now.toLocaleDateString(undefined, { weekday:'long', month:'long', day:'numeric' });
}
setInterval(updateClock, 1000);
updateClock();

function copyUrl(el) {
  const url = window.location.origin + el.querySelector('code').textContent;
  navigator.clipboard.writeText(url).then(() => {
    const orig = el.innerHTML;
    el.innerHTML = '<span style="color:#22c55e">✓ Copied!</span>';
    setTimeout(() => el.innerHTML = orig, 1200);
  }).catch(() => {});
}
function fmtUptime(secs) {
  if (!secs || secs <= 0) return '';
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
  return h ? h+'h '+m+'m' : m ? m+'m '+s+'s' : s+'s';
}

function twCard(el) {
  if (!el) return;
  const live = document.getElementById('twitch-label')?.textContent === 'LIVE';
  el.classList.toggle('card-live', live);
}

const SCENE_LABELS = { modern: 'Modern Neon', retro: 'Retro Win98' };
let sceneSwitching = false;

function renderScenes(data) {
  const active = data.active_set;
  const available = data.available || [];
  const activeEl = document.getElementById('scene-active');
  if (active) {
    activeEl.textContent = SCENE_LABELS[active] || active;
    activeEl.classList.remove('unknown');
  } else {
    activeEl.textContent = 'Unknown / not set';
    activeEl.classList.add('unknown');
  }
  const btnWrap = document.getElementById('scene-btns');
  const order = ['modern', 'retro'];
  const sets = order.filter(n => available.includes(n)).concat(available.filter(n => !order.includes(n)));
  btnWrap.innerHTML = sets.map(name => {
    const label = SCENE_LABELS[name] || name;
    const cur = name === active ? ' current' : '';
    const dis = (sceneSwitching || name === active) ? ' disabled' : '';
    return '<button class="scene-btn' + cur + '" ' + dis + ' onclick="switchScene(\'' + name + '\')">'
      + (name === active ? '● ' : '') + label + '</button>';
  }).join('') || '<span class="scene-sub">No scene sets found on disk.</span>';
}

async function switchScene(name) {
  if (sceneSwitching) return;
  sceneSwitching = true;
  const msg = document.getElementById('scene-msg');
  msg.className = 'scene-msg'; msg.textContent = 'Switching to ' + (SCENE_LABELS[name] || name) + '…';
  try {
    const r = await fetch('/api/scenes/switch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ set: name })
    });
    const d = await r.json();
    msg.textContent = d.message || (d.ok ? 'Switched.' : 'Switch failed.');
    msg.classList.add(d.ok ? 'ok' : 'err');
    renderScenes(d);
    if (d.ok) msg.textContent += ' — refresh your OBS browser sources.';
  } catch (e) {
    msg.className = 'scene-msg err'; msg.textContent = 'Switch request failed.';
  } finally {
    sceneSwitching = false;
    setTimeout(() => { if (!sceneSwitching) { msg.className = 'scene-msg'; msg.textContent = ''; } }, 6000);
  }
}

async function pollScenes() {
  if (sceneSwitching) return;  // don't clobber the UI mid-switch
  try {
    const r = await fetch('/api/scenes');
    renderScenes(await r.json());
  } catch (e) { /* leave last state */ }
}
setInterval(pollScenes, 4000);
pollScenes();

let updateInfo = null;
async function checkUpdate() {
  try {
    const r = await fetch('/api/update');
    const d = await r.json();
    updateInfo = d;
    const card = document.getElementById('update-card');
    if (d.available && d.latest) {
      document.getElementById('update-ver').textContent = d.latest + '  (current v' + d.current + ')';
      card.classList.add('show');
    } else {
      card.classList.remove('show');
    }
  } catch (e) { /* offline check — ignore */ }
}
async function installUpdate() {
  if (!updateInfo || !updateInfo.available) return;
  const ok = confirm('Download and install ' + updateInfo.latest + '?\n\nYour current files will be backed up to .update-backup/. You\'ll need to restart Stream Manager afterward.');
  if (!ok) return;
  const btn = document.getElementById('update-btn');
  const msg = document.getElementById('update-msg');
  btn.disabled = true; msg.className = 'update-msg'; msg.textContent = 'Downloading & installing…';
  try {
    const r = await fetch('/api/update/install', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true })
    });
    const d = await r.json();
    msg.textContent = d.message || d.error || (d.ok ? 'Installed.' : 'Failed.');
    msg.classList.add(d.ok ? 'ok' : 'err');
    if (d.ok) msg.textContent += ' Restart Stream Manager to apply.';
    else btn.disabled = false;
  } catch (e) {
    msg.className = 'update-msg err'; msg.textContent = 'Install request failed.';
    btn.disabled = false;
  }
}
// Check on load, then hourly (GitHub check is cheap and read-only)
checkUpdate();
setInterval(checkUpdate, 3600000);

// ── Interactive (games & redeems) ───────────────────────────────
function ixWhen(ts) {
  const d = Math.max(0, Math.floor(Date.now()/1000 - ts));
  if (d < 60) return d + 's ago';
  if (d < 3600) return Math.floor(d/60) + 'm ago';
  return Math.floor(d/3600) + 'h ago';
}
async function ixTest(action) {
  const msg = document.getElementById('ix-msg');
  msg.className = 'scene-msg'; msg.textContent = 'Triggering ' + action + '…';
  try {
    const r = await fetch('/api/interactive/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, user: 'Dashboard' })
    });
    const d = await r.json();
    msg.classList.add(d.ok ? 'ok' : 'err');
    msg.textContent = d.ok ? ('▶ ' + action + ' → ' + (d.result ?? 'sent') + ' (check your overlay)') : (d.error || 'Failed.');
  } catch (e) { msg.className = 'scene-msg err'; msg.textContent = 'Request failed.'; }
  setTimeout(() => { msg.className = 'scene-msg'; msg.textContent = ''; }, 6000);
}
async function ixAuthorize() {
  const msg = document.getElementById('ix-msg');
  msg.className = 'scene-msg'; msg.textContent = 'Starting Twitch login… watch the app window / this card for a code.';
  try { await fetch('/api/interactive/authorize', { method: 'POST' }); } catch (e) {}
}
async function ixReload() {
  const msg = document.getElementById('ix-msg');
  msg.className = 'scene-msg'; msg.textContent = 'Reloading config.json…';
  try {
    const r = await fetch('/api/interactive/reload', { method: 'POST' });
    const d = await r.json();
    msg.classList.add(d.ok ? 'ok' : 'err');
    msg.textContent = d.ok ? '♻️ Config reloaded (wheels, cooldowns, redeems).' : 'Reload failed.';
  } catch (e) { msg.className = 'scene-msg err'; msg.textContent = 'Reload request failed.'; }
  setTimeout(() => { msg.className = 'scene-msg'; msg.textContent = ''; }, 5000);
}
async function pollStats() {
  try {
    const r = await fetch('/api/interactive/stats');
    const s = await r.json();
    const board = document.getElementById('ix-board');
    const players = s.top_players || [];
    board.innerHTML = players.length
      ? players.slice(0, 6).map((p, i) =>
          '<div class="row"><span class="who">' + (i + 1) + '. ' + p.user + '</span>' +
          '<span class="n">' + p.plays + ' plays' + (p.wins ? ' · ' + p.wins + ' wins' : '') + '</span></div>').join('')
      : '<div class="stat-label">No plays yet.</div>';
  } catch (e) { /* stats unavailable */ }
}
setInterval(pollStats, 5000);
pollStats();
async function pollInteractive() {
  try {
    const r = await fetch('/api/interactive');
    const d = await r.json();
    const dot = document.getElementById('ix-auth-dot');
    const label = document.getElementById('ix-auth-label');
    const hint = document.getElementById('ix-auth-hint');
    const btn = document.getElementById('ix-auth-btn');
    const st = d.auth?.status;
    btn.style.display = 'none'; hint.innerHTML = '';
    // transport / automation chips
    const meta = document.getElementById('ix-meta');
    if (meta) {
      const chips = [];
      const es = d.redeems?.transport === 'eventsub';
      chips.push('<span class="ix-chip ' + (es ? 'on' : 'off') + '">' + (es ? 'EventSub ⚡' : 'Polling') + '</span>');
      chips.push('<span class="ix-chip ' + (d.automation?.enabled ? 'on' : 'off') + '">Automation ' + (d.automation?.enabled ? 'on' : 'off') + '</span>');
      if (d.eventsub && d.eventsub.available === false)
        chips.push('<span class="ix-chip off">websocket-client not installed</span>');
      meta.innerHTML = chips.join('');
    }
    if (st === 'ok') {
      dot.className = 'status-dot on';
      const chat = d.chat?.connected ? 'chat connected' : 'chat connecting…';
      const rd = d.redeems?.ready ? 'redeems ready' : 'redeems setting up…';
      label.textContent = 'Authorized as ' + (d.auth.login || '—');
      hint.textContent = chat + ' · ' + rd + (d.chat?.channel ? ' · #' + d.chat.channel : '');
    } else if (st === 'pending' || st === 'unauthorized') {
      dot.className = 'status-dot warn';
      label.textContent = 'Twitch authorization needed';
      if (d.auth.user_code) {
        hint.innerHTML = '1. Open <code>' + (d.auth.verification_uri || 'twitch.tv/activate') +
          '</code> &nbsp; 2. Enter code <code>' + d.auth.user_code + '</code>';
      } else {
        hint.textContent = 'Click Authorize, then enter the code Twitch shows you.';
      }
      btn.style.display = 'inline-block';
    } else if (st === 'unconfigured') {
      dot.className = 'status-dot off';
      label.textContent = 'Not configured';
      hint.textContent = 'Add TWITCH_CLIENT_ID / SECRET to your .env, then restart.';
    } else {
      dot.className = 'status-dot off';
      label.textContent = 'Unavailable';
      hint.textContent = d.auth?.error || '';
    }
    const feed = document.getElementById('ix-feed');
    const items = d.recent || [];
    feed.innerHTML = items.length
      ? items.map(it => '<div class="ix-item">' + (it.text || '') +
          ' <span class="ix-when">· ' + ixWhen(it.ts) + '</span></div>').join('')
      : '<div class="stat-label">No spins yet.</div>';
  } catch (e) { /* interactive layer disabled or server busy */ }
}
setInterval(pollInteractive, 3000);
pollInteractive();

async function poll() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();

    // OBS
    const obsDot = document.getElementById('obs-dot');
    const obsLabel = document.getElementById('obs-label');
    obsDot.className = 'status-dot ' + (s.obs.running ? 'on' : 'off');
    obsLabel.textContent = s.obs.running ? 'Running' : 'Not running';
    document.getElementById('obs-pid').textContent = s.obs.pid ? 'PID ' + s.obs.pid : '';
    document.getElementById('obs-uptime').textContent = s.obs.uptime ? 'Uptime: ' + fmtUptime(s.obs.uptime) : '';
    document.getElementById('obs-scene').textContent = s.obs.scene ? 'Scene: ' + s.obs.scene : '';
    const outputs = [];
    if (s.obs.streaming) outputs.push('● Streaming');
    if (s.obs.recording) outputs.push('● Recording');
    document.getElementById('obs-outputs').textContent = outputs.join('   ');

    // Twitch stream
    const twDot = document.getElementById('twitch-dot');
    const twLabel = document.getElementById('twitch-label');
    const twLive = s.twitch.live;
    twDot.className = 'status-dot ' + (twLive ? 'on' : 'off');
    twLabel.textContent = twLive ? 'LIVE' : 'Offline';
    document.getElementById('twitch-title').textContent = s.twitch.title || '—';
    document.getElementById('twitch-game').textContent = s.twitch.game || '—';
    document.getElementById('twitch-viewers').textContent = twLive ? s.twitch.viewers + ' viewers' : '';
    document.getElementById('twitch-uptime').textContent = twLive ? s.twitch.uptime : '';

    // Twitch user info
    if (s.twitch.display_name) {
      document.getElementById('display-name').textContent = s.twitch.display_name;
    }
    if (s.twitch.view_count) {
      document.getElementById('view-count').textContent = s.twitch.view_count.toLocaleString();
    }
    const avatar = document.getElementById('avatar');
    if (s.twitch.profile_image_url) {
      avatar.src = s.twitch.profile_image_url;
      avatar.style.display = 'block';
    }

    // Twitch API status
    const apiDot = document.getElementById('twitch-api-dot');
    const apiLabel = document.getElementById('twitch-api-label');
    apiDot.className = 'status-dot ' + (s.twitch.connected ? 'on' : 'off');
    apiLabel.textContent = s.twitch.connected ? 'Connected' : 'No credentials';

    // Live glow on Twitch card
    twCard(document.querySelector('.card:nth-child(2)'));

    // Server uptime
    document.getElementById('server-uptime').textContent = s.server.uptime || '0s';
    document.getElementById('server-port').textContent = ':' + s.server.port;

    // System
    document.getElementById('cpu-pct').textContent = s.system.cpu;
    document.getElementById('cpu-bar').style.width = s.system.cpu + '%';

    const ramPct = s.system.ram_pct;
    document.getElementById('ram-used').textContent = s.system.ram_used_gb;
    document.getElementById('ram-total').textContent = s.system.ram_total_gb;
    document.getElementById('ram-bar').style.width = ramPct + '%';
    document.getElementById('ram-pct-label').textContent = ramPct + '% used';

    // GPU
    const gpuEl = document.getElementById('gpu-name');
    if (gpuEl && s.system.gpu) gpuEl.textContent = s.system.gpu;

    // Log — skip the rebuild entirely when nothing new has arrived
    if (s.requests && s.requests.length && s.requests[0] !== lastLogHead) {
      lastLogHead = s.requests[0];
      const logBox = document.getElementById('log-box');
      logBox.innerHTML = s.requests.map(r => {
        const m = r.match(/^\[(\d+:\d+:\d+)\]\s+(.*)/);
        if (m) return '<div class="log-entry"><span class="log-timestamp">[' + m[1] + ']</span> <span class="log-text">' + m[2] + '</span></div>';
        return '<div class="log-entry"><span class="log-text">' + r + '</span></div>';
      }).join('');
    }
  } catch(e) {
    document.getElementById('obs-label').textContent = 'Disconnected';
  }
}
setInterval(poll, 2000);
poll();
