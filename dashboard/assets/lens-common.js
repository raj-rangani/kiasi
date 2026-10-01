const $ = s => document.querySelector(s);
const est = key => `<abbr class="est" title="${esc(EST_TIPS[key] || EST_TIPS.avoided)}">${EST_TAG}</abbr>`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtM = n => n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)} M` : n >= 1000 ? `${Math.round(n / 1000)} k` : String(Math.round(n || 0));
const fmtK = n => `${Math.round(n / 1000)} k`;
const pct = x => `${Math.round(x * 100)}%`;
const label = k => KIND_LABELS[k] || k;
const stamp = ts => String(ts || '').replace('T', ' ');
const read = k => { try { return localStorage.getItem(k); } catch { return null; } };
const store = v => { try { localStorage.setItem('theme', v); } catch {} };

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

const LIMIT_SPAN = { session: 5 * 3600, weekly: 7 * 86400 };

function paceText(used, pace) {
  const gap = used - pace;
  if (gap > LIMIT_PACE_SLACK) return `${gap} pts ahead of pace`;
  if (gap < -LIMIT_PACE_SLACK) return `${-gap} pts under pace`;
  return 'on pace';
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
    ? ` · at this pace runs out <strong>${esc(runOut.toLocaleString([], WHEN_OPTS))}</strong>, before the reset` : '';
  return `<div class="lim ${tone}${item === first ? ' first' : ''}${stale ? ' old' : ''}" role="group" aria-label="${esc(item.label)}">`
    + `<div class="lim-head"><span>${esc(item.label)}</span>${item === first ? '<span class="lim-first">runs out first</span>' : ''}</div>`
    + `<div class="lim-value">${expired ? 'new' : `${used}%`}<small>used</small></div>`
    + `<span class="lim-bar" role="meter" aria-valuenow="${used}" aria-valuemin="0" aria-valuemax="100" aria-label="${esc(item.label)}"><i style="width:${used}%"></i>${pace != null ? `<b style="left:${pace}%" title="${pace}% of the window gone"></b>` : ''}</span>`
    + `<p>${resetLine}<br>${paceLine}${runOutLine}</p></div>`;
}

function renderLimits(l) {
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

function registerView(name, render, source = 'lens') {
  views[name] = { render, source, dirty: true, root: document.querySelector(`[data-view="${name}"]`) };
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
  bindPanel();

  function paint(name) {
    const view = views[name];
    const d = data[view.source];
    if (!view.dirty || !d) return;
    activeRoot = view.root;
    view.render(d);
    view.dirty = false;
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

  async function load() {
    loadLimits();
    const results = await Promise.allSettled([fetchReport('lens', LENS_FILE), fetchReport('budget', BUDGET_FILE)]);
    const lensOk = results[0].status === 'fulfilled';
    if (lensOk || results[1].status === 'fulfilled') {
      const built = (data.lens || data.budget).generated;
      setStatus(`built ${stamp(built)} · ${SYNC_EVERY_TEXT}`, 'ok');
      $('#banner').classList.add('hidden');
    } else {
      setStatus('no report yet', 'bad');
      $('#banner').textContent = 'No report yet. Click Sync to build one from your transcripts; the savings numbers fill in after a session or two with Kiasi on.';
      $('#banner').classList.remove('hidden');
    }
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
