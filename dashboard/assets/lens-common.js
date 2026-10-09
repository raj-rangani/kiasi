const $ = s => document.querySelector(s);
const est = key => `<abbr class="est" title="${esc(EST_TIPS[key] || EST_TIPS.avoided)}">${EST_TAG}</abbr>`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtM = n => n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)} M` : n >= 1000 ? `${Math.round(n / 1000)} k` : String(Math.round(n || 0));
const fmtK = n => n == null ? '–' : `${Math.round(n / 1000)} k`;
const pct = x => x == null ? '–' : `${Math.round(x * 100)}%`;
const fmtUsd = n => n == null ? '–' : n >= 1000 ? `$${Math.round(n).toLocaleString()}` : n >= 100 ? `$${n.toFixed(0)}` : n >= 10 ? `$${n.toFixed(1)}` : `$${n.toFixed(2)}`;
const label = k => KIND_LABELS[k] || k;
const stamp = ts => String(ts || '').replace('T', ' ');
const read = k => { try { return localStorage.getItem(k); } catch { return null; } };
const store = v => { try { localStorage.setItem('theme', v); } catch {} };

const emptyLine = what => `<div class="empty">No ${what} in range yet.</div>`;
const hasDays = d => (d.per_day || []).length > 0;
const LOGO_SVG = '<svg class="logo" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H4v18h4M16 3h4v18h-4"/><circle cx="12" cy="12" r="3.6"/></svg>';

function emptyMarkup(name, kind, d) {
  if (kind === 'loading') return `${LOGO_SVG}<h2>${EMPTY_LOADING}</h2>`;
  const [title, body] = EMPTY_STATES[name][kind];
  const days = (d && d.days) || 7;
  return `${LOGO_SVG}<h2>${esc(title.replace('{days}', days))}</h2><p>${esc(body.replace('{days}', days))}</p>
    <div class="ve-actions"><button class="ve-btn" type="button" data-sync>${esc(EMPTY_ACTIONS[kind])}</button>
    <a class="ve-link" href="${EMPTY_LINK.href}" target="_blank" rel="noopener">${esc(EMPTY_LINK.text)}</a></div>
    <p class="ve-note" hidden>${esc(EMPTY_BUILDING)}</p>`;
}

function renderEmpty(view, name, kind, d) {
  const host = view.root.querySelector('.view-empty');
  host.innerHTML = emptyMarkup(name, kind, d);
  host.hidden = false;
  view.root.classList.add('is-empty');
  const btn = host.querySelector('[data-sync]');
  if (btn) btn.addEventListener('click', () => { if (window.runSync) window.runSync(); });
  setSyncBusy(document.body.classList.contains('syncing'));
}

function clearEmpty(view) {
  const host = view.root.querySelector('.view-empty');
  host.hidden = true;
  host.innerHTML = '';
  view.root.classList.remove('is-empty');
}

function setSyncBusy(busy) {
  document.body.classList.toggle('syncing', busy);
  document.querySelectorAll('.view-empty [data-sync]').forEach(btn => { btn.disabled = busy; });
  document.querySelectorAll('.view-empty .ve-note').forEach(note => { note.hidden = !busy; });
}

function setStatus(text, tone) {
  const el = $('#status');
  el.className = `status ${tone}`;
  el.innerHTML = `<i></i>${esc(text)}`;
}

const WHEN_OPTS = { weekday: 'short', hour: '2-digit', minute: '2-digit' };

function untilText(ms) {
  const mins = Math.max(1, Math.round(ms / 60000));
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

// The project name a session card shows: the path with its container directories dropped, so
// "-var-www-html-kiasi" reads as "kiasi". A session at a container itself keeps that container's name.
const PROJECT_ROOTS = new Set(['var', 'www', 'html', 'home', 'users', 'srv', 'opt', 'tmp', 'mnt', 'projects', 'code', 'src', 'dev', 'work', 'repos', 'sites', 'htdocs']);
const PROJECT_NAME_CHARS = 22;

function projectTail(project) {
  const segs = (project || '').replace(/^-+/, '').split('-').filter(Boolean);
  let i = 0;
  while (i < segs.length - 1 && PROJECT_ROOTS.has(segs[i].toLowerCase())) i++;
  const rest = segs.slice(i);
  if (!rest.length) return project || '';
  // Too long: keep whole segments from the end, since the last ones name the project.
  let keep = rest.length;
  while (keep > 1 && rest.slice(rest.length - keep).join('-').length > PROJECT_NAME_CHARS) keep--;
  return (keep < rest.length ? '…' : '') + rest.slice(rest.length - keep).join('-');
}

const LIMIT_SPAN = { session: 5 * 3600, weekly: 7 * 86400 };

function paceText(used, pace) {
  const gap = used - pace;
  if (gap > LIMIT_PACE_SLACK) return 'using it faster than the clock runs';
  if (gap < -LIMIT_PACE_SLACK) return 'using it slower than the clock runs';
  return 'on pace with the clock';
}

function limitColumn(item, now, first, stale, updated) {
  const resets = item.resets_at ? new Date(item.resets_at * 1000) : null;
  const span = LIMIT_SPAN[item.group];
  const expired = resets && resets < now;
  const used = expired ? 0 : Math.max(0, Math.min(100, Math.round(item.used)));
  const tone = expired ? '' : item.severity === 'critical' || used >= LIMIT_BAD ? 'bad'
    : item.severity === 'warning' || used >= LIMIT_WARN ? 'warn' : '';
  const pace = !expired && resets && span ? Math.max(0, Math.min(100, Math.round((1 - (resets - now) / 1000 / span) * 100))) : null;
  const when = resets ? esc(resets.toLocaleString([], WHEN_OPTS)) : '';
  const resetLine = expired ? `reset ${when}` : resets ? `resets in <strong>${untilText(resets - now)}</strong> · ${when}` : '';
  const paceLine = expired ? 'no reading since'
    : stale ? `as of ${esc(updated.toLocaleString([], WHEN_OPTS))}`
    : pace == null ? '' : paceText(used, pace);
  const runOut = item.run_out_at && !expired ? new Date(item.run_out_at * 1000) : null;
  const runOutLine = runOut && resets && runOut < resets && runOut > now && !stale
    ? `<br>at this pace runs out <strong>${esc(runOut.toLocaleString([], WHEN_OPTS))}</strong>, before the reset` : '';
  return `<div class="lim ${tone}${item === first ? ' first' : ''}${stale ? ' old' : ''}" role="group" aria-label="${esc(item.label)}">`
    + `<div class="lim-head"><span>${esc(item.label)}</span>${item === first ? '<span class="lim-first">runs out first</span>' : ''}</div>`
    + `<div class="lim-value">${expired ? 'new' : `${used}%`}<small>used</small></div>`
    + `<span class="lim-bar" role="meter" aria-valuenow="${used}" aria-valuemin="0" aria-valuemax="100" aria-label="${esc(item.label)}"><i style="width:${used}%"></i>${pace != null ? `<b style="left:${pace}%" title="${pace}% of the window gone"></b>` : ''}</span>`
    + `<p>${resetLine}<br>${paceLine}${runOutLine}</p></div>`;
}

function renderLimits(l) {
  window.limitsReading = l;
  if (typeof renderRunway === 'function') renderRunway(l);
  const el = $('#limits');
  const items = (l && l.limits || []).filter(item => item.used != null);
  if (!items.length) {
    const hint = LIMITS_HINTS[l && l.setup];
    el.hidden = !hint;
    el.classList.add('empty-limits');
    el.innerHTML = hint ? `<p class="lim-hint">${esc(hint)}</p>` : '';
    return;
  }
  el.classList.remove('empty-limits');
  const now = new Date();
  const updated = new Date(l.updated * 1000);
  const stale = now - updated > LIMITS_STALE_MS;
  const first = items.length > 1 ? items.find(item => item.active) : null;
  el.hidden = false;
  el.classList.toggle('stale', stale);
  el.style.setProperty('--lim-cols', items.length);
  el.title = `Read ${updated.toLocaleString([], WHEN_OPTS)} from the Claude Code status line. The tick on each bar is how much of the window has gone.`;
  el.innerHTML = items.map(item => {
    const read = item.as_of ? new Date(item.as_of * 1000) : updated;
    return limitColumn(item, now, first, now - read > LIMITS_STALE_MS, read);
  }).join('');
}

async function loadLimits() {
  try {
    const res = await fetch(`${LIMITS_FILE}?t=${Date.now()}`);
    renderLimits(res.ok ? await res.json() : null);
  } catch (err) {
    renderLimits(null);
  }
}

function chartWidth(sel) {
  return Math.max(320, Math.min(1120, (activeRoot.querySelector(sel) || {}).clientWidth || 900));
}

function frame(W, H, pad = PAD) {
  return { W, H, pad, innerW: W - pad.left - pad.right, innerH: H - pad.top - pad.bottom, bottom: pad.top + H - pad.top - pad.bottom };
}

function scaleY(f, max) {
  return v => f.pad.top + f.innerH - (v / max) * f.innerH;
}

function gridY(f, y, max, fmt) {
  let svg = '';
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i;
    svg += `<line class="grid" x1="${f.pad.left}" x2="${f.W - f.pad.right}" y1="${y(v)}" y2="${y(v)}"/><text x="${f.pad.left - 8}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`;
  }
  return svg;
}

function baseline(f) {
  return `<line class="axis" x1="${f.pad.left}" x2="${f.W - f.pad.right}" y1="${f.bottom}" y2="${f.bottom}"/>`;
}

function topBar(cls, x, y, w, h, r = BAR_RADIUS) {
  if (h <= 0) return '';
  const k = Math.min(r, w / 2, h);
  const n = v => (+v).toFixed(1);
  return `<path class="${cls}" d="M${n(x)},${n(y + h)}V${n(y + k)}Q${n(x)},${n(y)} ${n(x + k)},${n(y)}H${n(x + w - k)}Q${n(x + w)},${n(y)} ${n(x + w)},${n(y + k)}V${n(y + h)}Z"/>`;
}

function limitLines(f, y, settings, withText) {
  return [['warn_tokens', 'limit', 'warn'], ['hard_tokens', 'limit hard', 'hard']].map(([key, cls, name]) => {
    const v = settings[key];
    const text = withText ? `<text x="${f.W - f.pad.right + 8}" y="${y(v) + 4}">${fmtK(v)} ${name}</text>` : '';
    return `<line class="${cls}" x1="${f.pad.left}" x2="${f.W - f.pad.right}" y1="${y(v)}" y2="${y(v)}"/>${text}`;
  }).join('');
}

function dayLabel(day, step) {
  return step < NARROW_STEP_PX ? day.slice(8) : day.slice(5);
}

function dayLine(counts, days, install) {
  const { width, height, pad, dot, peakDot } = DAY_LINE;
  const values = days.map(day => counts[day] || 0);
  const top = Math.max(1, ...values);
  const slot = width / days.length;
  const floor = height - pad;
  const x = i => ((i + 0.5) * slot).toFixed(1);
  const y = v => (floor - v / top * (floor - pad)).toFixed(1);
  const md = day => day.slice(5).replace('-', '/');
  const points = values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  const peak = values.indexOf(Math.max(...values));
  const dots = values.map((v, i) => `<circle cx="${x(i)}" cy="${y(v)}" r="${v && i === peak ? peakDot : dot}"><title>${esc(md(days[i]))}: ${v} fired</title></circle>`).join('');
  const on = install ? days.indexOf(install) : -1;
  const marker = on > 0 ? `<line class="marker" x1="${(on * slot).toFixed(1)}" x2="${(on * slot).toFixed(1)}" y1="0" y2="${floor}"><title>Kiasi switched on ${esc(md(install))}</title></line>` : '';
  const svg = `<svg class="day-line" viewBox="0 0 ${width} ${height}" role="img" aria-label="Fires per day, ${esc(md(days[0]))} to ${esc(md(days[days.length - 1]))}"><line class="base" x1="0" x2="${width}" y1="${floor}" y2="${floor}"/>${marker}<polygon points="${x(0)},${floor} ${points} ${x(values.length - 1)},${floor}"/><polyline pathLength="1" points="${points}"/>${dots}</svg>`;
  return `${svg}<span class="cells counts" style="--n:${days.length}">${values.map((v, i) => `<i class="${v ? '' : 'nil'}" data-day="${esc(days[i].slice(8))}" title="${esc(md(days[i]))}: ${v} fired">${v}</i>`).join('')}</span>`;
}

function dayCellLabels(days) {
  return `<span class="cells labels" style="--n:${days.length}">${days.map(day => `<i title="${esc(day)}">${esc(day.slice(8))}</i>`).join('')}</span>`;
}

function pill(kind, text) {
  return `<span class="pill ${esc(kind)}">${esc(text ?? label(kind))}</span>`;
}

function bindTips(host) {
  const tip = $('#tooltip');
  host.querySelectorAll('[data-tip]').forEach(el => {
    el.addEventListener('mousemove', e => { tip.textContent = el.dataset.tip; tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 330)}px`; tip.style.top = `${e.clientY + 14}px`; tip.classList.add('show'); });
    el.addEventListener('mouseleave', () => tip.classList.remove('show'));
  });
}

function bindTheme() {
  const saved = read('theme') || new URLSearchParams(location.search).get('theme');
  if (saved) document.documentElement.dataset.theme = saved;
  $('#theme').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    store(next);
  });
}

const views = {};
let activeRoot = document;

let panelClose = null;
let panelNav = null;
let panelOpener = null;
let panelHideTimer = null;
let panelKeepScroll = 0;

function panelWidth() {
  return Math.max(320, Math.min(1120, ($('#panel-content') || {}).clientWidth || 640));
}

function pageParts() {
  return Array.from(document.querySelectorAll('header.masthead, main.view, #banner'));
}

function focusables(root) {
  return Array.from(root.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])')).filter(el => el.offsetParent !== null);
}

function renderPanelNav() {
  const host = $('#panel-nav');
  if (!panelNav) { host.innerHTML = ''; host.hidden = true; return; }
  host.hidden = false;
  host.innerHTML = `<button class="iconbtn" id="panel-prev" title="previous (←)" aria-label="previous" ${panelNav.index <= 0 ? 'disabled' : ''}>‹</button><span class="mono">${panelNav.index + 1} of ${panelNav.total}</span><button class="iconbtn" id="panel-next" title="next (→)" aria-label="next" ${panelNav.index >= panelNav.total - 1 ? 'disabled' : ''}>›</button>`;
  $('#panel-prev').addEventListener('click', () => panelNav.go(-1));
  $('#panel-next').addEventListener('click', () => panelNav.go(1));
}

function renderPanelJumps() {
  const content = $('#panel-content');
  const heads = Array.from(content.querySelectorAll('h3.sub'));
  const host = $('#panel-jumps');
  if (heads.length < 2) { host.innerHTML = ''; host.hidden = true; return; }
  host.hidden = false;
  heads.forEach((h, i) => { h.id = `panel-sec-${i}`; });
  host.innerHTML = `<div class="jump-track"><i class="jump-ind"></i>${heads.map((h, i) => `<a href="#" data-sec="${i}">${esc(h.dataset.jump || h.textContent)}</a>`).join('')}</div>`;
  const tabs = Array.from(host.querySelectorAll('a'));
  const ind = host.querySelector('.jump-ind');
  let current = -1;
  const setActive = i => {
    if (i === current) return;
    current = i;
    tabs.forEach((a, k) => { a.classList.toggle('active', k === i); a.setAttribute('aria-current', k === i ? 'true' : 'false'); });
    const a = tabs[i];
    ind.style.width = `${a.offsetWidth}px`;
    ind.style.transform = `translateX(${a.offsetLeft}px)`;
    const edge = a.offsetLeft - (host.clientWidth - a.offsetWidth) / 2;
    host.scrollTo({ left: Math.max(0, edge), behavior: 'smooth' });
  };
  tabs.forEach((a, i) => a.addEventListener('click', e => {
    e.preventDefault();
    const target = content.querySelector(`#panel-sec-${a.dataset.sec}`);
    if (target) content.scrollTo({ top: target.offsetTop - content.offsetTop - 8, behavior: 'smooth' });
    setActive(i);
  }));
  content.onscroll = () => {
    if (content.scrollTop + content.clientHeight >= content.scrollHeight - 4) { setActive(heads.length - 1); return; }
    const line = content.scrollTop + 40;
    let active = 0;
    heads.forEach((h, i) => { if (h.offsetTop - content.offsetTop <= line) active = i; });
    setActive(active);
  };
  requestAnimationFrame(() => { ind.classList.add('still'); content.onscroll(); requestAnimationFrame(() => ind.classList.remove('still')); });
}

function openPanel(title, lead, html, opts) {
  const panel = $('#panel');
  const o = typeof opts === 'function' ? { onClose: opts } : (opts || {});
  const changed = $('#panel-title').textContent !== title;
  $('#panel-title').textContent = title;
  $('#panel-lead').textContent = lead;
  panelClose = o.onClose || null;
  panelNav = o.nav || null;
  renderPanelNav();
  if (panel.hidden) {
    clearTimeout(panelHideTimer);
    panelOpener = document.activeElement;
    panel.hidden = false;
    document.documentElement.classList.add('panel-open');
    pageParts().forEach(el => { el.inert = true; });
    void panel.offsetHeight;
    panel.classList.add('open');
    setTimeout(() => { if (!panel.hidden) $('#panel-content').focus({ preventScroll: true }); }, 60);
  }
  panelKeepScroll = changed ? 0 : $('#panel-content').scrollTop;
  $('#panel-content').innerHTML = html;
  if (changed) $('#panel-content').scrollTop = 0;
  return $('#panel-content');
}

function finishPanelContent() {
  $('#panel-content').scrollTop = panelKeepScroll;
  renderPanelJumps();
}

function closePanel(silent) {
  const panel = $('#panel');
  if (panel.hidden) return;
  panel.classList.remove('open');
  document.documentElement.classList.remove('panel-open');
  pageParts().forEach(el => { el.inert = false; });
  panelNav = null;
  const fn = panelClose;
  panelClose = null;
  const opener = panelOpener;
  panelOpener = null;
  panelHideTimer = setTimeout(() => { panel.hidden = true; $('#panel-content').innerHTML = ''; }, PANEL_MS);
  if (opener && opener.focus && document.contains(opener)) opener.focus({ preventScroll: true });
  if (fn && !silent) fn();
}

function bindPanel() {
  const panel = $('#panel');
  $('#panel-close').addEventListener('click', () => closePanel());
  $('#panel-backdrop').addEventListener('click', () => closePanel());
  document.addEventListener('keydown', e => {
    if (panel.hidden) return;
    if (e.key === 'Escape') { closePanel(); return; }
    if (e.target && e.target.matches && e.target.matches('input, textarea, select')) return;
    if (panelNav && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) { e.preventDefault(); panelNav.go(e.key === 'ArrowLeft' ? -1 : 1); return; }
    if (e.key === 'Tab') {
      const items = focusables(panel);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
}

function registerView(name, render, source = 'lens', hasData = hasDays) {
  views[name] = { render, source, hasData, dirty: true, root: document.querySelector(`[data-view="${name}"]`) };
}

function currentView() {
  const raw = location.hash.slice(1).split('/')[0];
  const name = VIEW_ALIASES[raw] || raw;
  return views[name] ? name : 'overview';
}

function viewParam() {
  return location.hash.slice(1).split('/')[1] || '';
}

function setViewParam(value) {
  history.replaceState(null, '', value ? `#${currentView()}/${value}` : `#${currentView()}`);
}

function renderNav() {
  const here = currentView();
  $('#nav').innerHTML = VIEWS.map(([name, title]) => `<a href="#${name}" class="${name === here ? 'active' : ''}">${title}</a>`).join('');
  $('#tagline').innerHTML = (VIEWS.find(([name]) => name === here) || [])[2] || '';
  document.title = `Kiasi · ${(VIEWS.find(([name]) => name === here) || [])[1] || ''}`;
}

function startApp() {
  const data = { lens: null, budget: null };
  const signature = { lens: '', budget: '' };
  let shown = '';
  let loaded = false;
  bindPanel();

  function paint(name) {
    const view = views[name];
    const d = data[view.source];
    if (!view.dirty) return;
    const kind = !d ? (loaded ? 'missing' : 'loading') : view.hasData(d) ? '' : 'empty';
    view.dirty = false;
    if (kind) { renderEmpty(view, name, kind, d); return; }
    clearEmpty(view);
    activeRoot = view.root;
    view.render(d);
  }

  function show() {
    const name = currentView();
    Object.entries(views).forEach(([key, view]) => { view.root.hidden = key !== name; });
    renderNav();
    if (shown !== name) { closePanel(); window.scrollTo({ top: 0 }); }
    views[name].dirty = true;
    shown = name;
    paint(name);
  }

  async function fetchReport(source, file) {
    const res = await fetch(`${file}?t=${Date.now()}`);
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();
    if (d.generated === signature[source]) return false;
    signature[source] = d.generated;
    data[source] = d;
    Object.values(views).forEach(view => { if (view.source === source) view.dirty = true; });
    return true;
  }

  async function lastSync() {
    try {
      const res = await fetch(`${SYNC_STATUS_FILE}?t=${Date.now()}`);
      return res.ok ? await res.json() : null;
    } catch { return null; }
  }

  async function load() {
    loadLimits();
    const results = await Promise.allSettled([fetchReport('lens', LENS_FILE), fetchReport('budget', BUDGET_FILE)]);
    const lensOk = results[0].status === 'fulfilled';
    if (lensOk || results[1].status === 'fulfilled') {
      const built = (data.lens || data.budget).generated;
      let sync = await lastSync();
      // A run that never wrote its end (its process died) stays "running": past the longest a rebuild can take, it failed.
      if (sync && sync.state === 'running' && Date.now() - Date.parse(sync.started) > SYNC_RUNNING_MAX_MS) sync = { ...sync, state: 'failed', error: 'it never finished' };
      const age = Date.now() - Date.parse(built);
      const skipped = Object.values((data.lens || data.budget).skipped || {}).reduce((a, n) => a + n, 0);
      const unread = skipped ? ` · ${skipped} unreadable transcript lines or files skipped` : '';
      if (sync && sync.state === 'failed') {
        setStatus(`built ${stamp(built)} · last rebuild failed`, 'bad');
        $('#banner').textContent = `The rebuild${sync.started ? ` at ${stamp(sync.started)}` : ''} failed: ${sync.error}. Run /kiasi:sync in Claude Code to see the full error.`;
        $('#banner').classList.remove('hidden');
      } else if (age > 2 * REBUILD_MS) {
        setStatus(`built ${stamp(built)} · stale, no rebuild for ${Math.round(age / 3600000)} h${unread}`, 'bad');
        $('#banner').classList.add('hidden');
      } else {
        setStatus(`built ${stamp(built)} · ${SYNC_EVERY_TEXT}${unread}`, 'ok');
        $('#banner').classList.add('hidden');
      }
    } else {
      setStatus('no report yet', '');
    }
    if (!loaded) { loaded = true; Object.values(views).forEach(view => { view.dirty = true; }); }
    paint(currentView());
  }

  bindTheme();
  window.reloadReport = load;
  window.addEventListener('hashchange', show);
  window.addEventListener('resize', () => { Object.values(views).forEach(view => { view.dirty = true; }); paint(currentView()); });
  show();
  load();
  setInterval(load, REFRESH_MS);
}
