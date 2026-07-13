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

async function poll() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();

    // OBS
    const obsDot = document.getElementById('obs-dot');
    const obsLabel = document.getElementById('obs-label');
    obsDot.className = 'status-dot ' + (s.obs.running ? 'on' : 'off');
    obsLabel.textContent = s.obs.running ? 'Running' : 'Not running';
    document.getElementById('obs-pid').textContent = s.obs.running ? 'PID ' + s.obs.pid : '';
    document.getElementById('obs-uptime').textContent = s.obs.running ? 'Uptime: ' + fmtUptime(s.obs.uptime) : '';

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
