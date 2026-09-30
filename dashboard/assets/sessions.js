(() => {
const root = document.querySelector('[data-view="sessions"]');
const $ = s => root.querySelector(s);
let data = null;
let selected = '';

function sessionSvg(s, settings, W, H, detailed) {
  const f = frame(W, H, detailed ? PAD : MINI_PAD);
  const points = s.series;
  const max = Math.max(...points.map(p => p.context), settings.hard_tokens * 1.1) || 1;
  const last = points[points.length - 1].i || 1;
  const x = i => f.pad.left + (i / last) * f.innerW;
  const y = scaleY(f, max);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Context per step in session ${esc(s.short)}">`;
  if (detailed) svg += gridY(f, y, max, fmtK);
  svg += limitLines(f, y, settings, detailed);
  points.filter(p => p.prompt).forEach(p => { svg += `<line class="ctx-prompt" x1="${x(p.i).toFixed(1)}" x2="${x(p.i).toFixed(1)}" y1="${f.pad.top}" y2="${f.bottom}"/>`; });
  svg += `<path class="ctx" d="${points.map((p, k) => `${k ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.context).toFixed(1)}`).join(' ')}"/>`;
  points.forEach(p => {
    const marks = p.marks || [];
    const cls = marks.includes('stop') ? 'ctx-stop' : marks.includes('check') ? 'ctx-check' : marks.includes('pruned') ? 'ctx-pruned' : marks.includes('compaction') ? 'ctx-ring' : detailed ? 'ctx-dot' : '';
    if (!cls) return;
    const r = marks.length ? (detailed ? 5 : 3.5) : 2;
    const tip = `step ${p.i + 1} · ${fmtK(p.context)}${marks.length ? ` · ${marks.map(m => MARK_LABELS[m] || m).join(', ')}` : ''}${p.prompt ? ' · prompt' : ''}`;
    svg += `<circle class="${cls}" cx="${x(p.i).toFixed(1)}" cy="${y(p.context).toFixed(1)}" r="${r}" data-tip="${esc(tip)}"/>`;
  });
  if (detailed) svg += `<text x="${f.pad.left}" y="${f.H - f.pad.bottom + 18}" class="lbl">step 1</text><text x="${f.W - f.pad.right}" y="${f.H - f.pad.bottom + 18}" text-anchor="end" class="lbl">step ${last + 1}</text>` + baseline(f);
  return svg + '</svg>';
}


function renderMultiples(d) {
  const host = $('#multiples');
  const W = 300;
  host.innerHTML = d.sessions.slice(0, TOP_MULTIPLES).map(s => `<article class="mini ${s.session === selected ? 'selected' : ''}" data-session="${esc(s.session)}">
    <h3><b>${esc(s.short)}</b><span>${esc(s.day)}</span></h3><div class="proj" title="${esc(s.project)}">${esc(s.project)}</div>${sessionSvg(s, d.settings, W, MINI_HEIGHT, false)}
    <dl class="kv"><dt>prompts</dt><dd>${s.prompts}</dd><dt>steps</dt><dd>${s.steps}</dd><dt>peak</dt><dd>${fmtK(s.peak)}</dd><dt>bill</dt><dd>${fmtM(s.bill)}</dd></dl></article>`).join('') || '<div class="empty">No sessions in range.</div>';
  host.querySelectorAll('.mini').forEach(el => el.addEventListener('click', () => select(el.dataset.session)));
  bindTips(host);
}

let openAction = null;
let allPrompts = false;

function actionRows(rows) {
  return rows.map((a, i) => `<div class="action click ${openAction === i ? 'open' : ''}" data-i="${i}"><span class="when">${esc(stamp(a.ts))}</span><span>${pill(a.kind)}</span><span class="what">${esc(a.label)}</span>
    <span class="saved${a.saved ? '' : ' zero'}">${a.saved ? fmtM(a.saved) : '·'}</span><span class="steps">${a.kind === 'turn_stop' ? `${a.later_steps} after` : a.later_steps ? `× ${a.later_steps}` : ''}</span></div>${openAction === i ? `<div class="detail"><p>${esc(a.formula || 'no token effect is attributed to this row; it is counted')}</p><pre>${esc(JSON.stringify(a.record, null, 1))}</pre></div>` : ''}`).join('');
}

function billSplit(s, d) {
  const rows = s.prompt_rows || [];
  const bands = [['under the warning', r => r.steps < d.settings.turn_warn_steps, 'hist'], ['warned', r => r.steps >= d.settings.turn_warn_steps && r.steps < d.settings.turn_stop_steps, 'delegated'], ['stopped', r => r.steps >= d.settings.turn_stop_steps, 'red']];
  const total = rows.reduce((t, r) => t + r.cost, 0) || 1;
  const parts = bands.map(([name, test, cls]) => { const cost = rows.filter(test).reduce((t, r) => t + r.cost, 0); return { name, cls, cost, share: cost / total }; }).filter(p => p.cost);
  if (!parts.length) return '<div class="empty">No prompt timing was logged for this session.</div>';
  return `<div class="split">${parts.map(p => `<i class="${p.cls}" style="width:${(p.share * 100).toFixed(1)}%" title="${esc(p.name)} · ${fmtM(p.cost)}"></i>`).join('')}</div>
    <div class="legend">${parts.map(p => `<span><i class="${p.cls}"></i>${esc(p.name)} ${Math.round(p.share * 100)}% · ${fmtM(p.cost)}</span>`).join('')}</div>`;
}

function promptTable(s, d) {
  const rows = s.prompt_rows || [];
  if (!rows.length) return '';
  const shown = allPrompts ? rows : rows.slice(0, PROMPT_ROWS);
  const maxCost = Math.max(...rows.map(r => r.cost)) || 1;
  const flag = r => r.steps >= d.settings.turn_stop_steps ? pill('turn_stop', 'stopped') : r.steps >= d.settings.turn_warn_steps ? pill('turn_warn', 'warned') : '';
  return `<table class="prompts"><thead><tr><th class="num">#</th><th>when</th><th class="num">context at start</th><th class="num">steps</th><th>cost of this prompt</th><th></th></tr></thead><tbody>${shown.map(r => `
    <tr><td class="num">${r.n}</td><td class="mono">${esc(stamp(r.ts))}</td><td class="num${r.context >= d.settings.warn_tokens ? ' warn' : ''}">${fmtK(r.context)}</td><td class="num">${r.steps}</td><td class="cost"><div class="fillbar" title="${fmtM(r.cost)}"><i style="width:${(r.cost / maxCost * 100).toFixed(1)}%"></i></div><span>${fmtM(r.cost)}</span></td><td>${flag(r)}</td></tr>`).join('')}</tbody></table>
    ${rows.length > PROMPT_ROWS ? `<p class="more"><a href="#sessions" id="toggle-prompts">${allPrompts ? `show the first ${PROMPT_ROWS}` : `show all ${rows.length} prompts`}</a></p>` : ''}`;
}

function renderDetail(d) {
  const s = d.sessions.find(row => row.session === selected);
  if (!s) { closePanel(true); return; }
  const rows = d.actions.filter(a => a.session === s.session);
  const order = d.sessions.map(x => x.session);
  const host = openPanel(`Session ${s.short}`, `${s.project} · ${s.day}`, '', {
    onClose: () => { selected = ''; openAction = null; allPrompts = false; setViewParam(''); renderMultiples(data); renderTable(data); },
    nav: { index: order.indexOf(s.session), total: order.length, go: dir => { const next = order[order.indexOf(selected) + dir]; if (next) select(next, true); } },
  });
  host.innerHTML = `<dl class="kv wide"><dt>session</dt><dd>${esc(s.session)}</dd><dt>project</dt><dd>${esc(s.project)}</dd><dt>started</dt><dd>${esc(stamp(s.start))}</dd></dl>
    <div class="rule-nums"><b>${fmtM(s.bill)}<small>re-read bill</small></b><b>${fmtM(s.saved)}<small>avoided${est('avoided')}</small></b><b>${s.prompts}<small>prompts</small></b><b>${s.steps}<small>steps</small></b><b>${fmtK(s.startup || 0)}<small>startup</small></b><b>${fmtK(s.mean_context)}<small>mean context</small></b><b>${fmtK(s.peak)}<small>peak</small></b><b>${s.compactions}<small>compactions</small></b></div>
    <h3 class="sub" data-jump="Context">Context at every step</h3>
    <div class="chart">${sessionSvg(s, d.settings, panelWidth(), DETAIL_HEIGHT, true)}</div>
    <div class="legend"><span><i class="tick"></i>prompt</span><span><i class="ring"></i>compaction</span><span><i class="ring pruned"></i>pruned compaction</span><span><i class="amber"></i>re-read check</span><span><i class="red"></i>turn stopped</span><span><i class="line"></i>warning</span><span><i class="line warn"></i>hard limit</span></div>
    <h3 class="sub" data-jump="Bill">Where the bill went</h3>${billSplit(s, d)}
    <h3 class="sub">Prompts</h3><p class="hist-note">Each prompt's cost is its context summed over the steps it ran. Prompt text is never logged.</p>${promptTable(s, d)}
    <h3 class="sub" data-jump="Actions">Kiasi actions in this session</h3>${actionRows(rows) || '<div class="empty">None logged.</div>'}`;
  bindTips(host);
  const toggle = host.querySelector('#toggle-prompts');
  if (toggle) toggle.addEventListener('click', e => { e.preventDefault(); allPrompts = !allPrompts; renderDetail(d); });
  host.querySelectorAll('.action.click').forEach(el => el.addEventListener('click', () => { const i = Number(el.dataset.i); openAction = openAction === i ? null : i; renderDetail(d); }));
  finishPanelContent();
}

let showAll = false;
function renderTable(d) {
  const all = d.sessions.filter(s => s.steps >= SMALL_SESSION_STEPS || s.session === selected);
  const rows = showAll ? d.sessions : all.slice(0, SESSION_ROWS);
  const hit = d.sessions.find(s => s.session === selected);
  if (hit && !rows.includes(hit)) rows.push(hit);
  $('#sessions-more').innerHTML = d.sessions.length > rows.length || showAll ? `<a href="#sessions" id="toggle-all">${showAll ? `show the first ${SESSION_ROWS}` : `show all ${d.sessions.length} sessions`}</a>` : '';
  const toggle = $('#toggle-all');
  if (toggle) toggle.addEventListener('click', e => { e.preventDefault(); showAll = !showAll; renderTable(d); });
  const maxBill = Math.max(...rows.map(s => s.bill)) || 1;
  $('#sessions').innerHTML = `<table><thead><tr><th>session</th><th>project</th><th>day</th><th class="num">steps</th><th class="num">mean ctx</th><th class="num">compactions</th><th class="num">avoided${est('avoided')}</th><th>bill</th></tr></thead><tbody>${rows.map(s => `
    <tr class="click ${s.session === selected ? 'selected' : ''}" data-session="${esc(s.session)}"><td class="mono">${esc(s.short)}</td><td>${esc(s.project.slice(0, 40))}</td><td class="mono">${esc(s.day)}</td><td class="num">${s.steps}</td><td class="num${s.mean_context >= d.settings.warn_tokens ? ' warn' : ''}">${fmtK(s.mean_context)}</td><td class="num">${s.compactions}</td><td class="num">${s.saved ? fmtM(s.saved) : ''}</td>
    <td><div class="fillbar" title="${fmtM(s.bill)}"><i class="${s.mean_context >= d.settings.warn_tokens ? 'warn' : ''}" style="width:${(s.bill / maxBill * 100).toFixed(1)}%"></i></div></td></tr>`).join('')}</tbody></table>`;
  $('#sessions').querySelectorAll('tr.click').forEach(el => el.addEventListener('click', () => select(el.dataset.session)));
}

function select(session, keep) {
  selected = selected === session && !keep ? '' : session;
  openAction = null;
  allPrompts = false;
  setViewParam(selected ? selected.slice(0, 8) : '');
  renderMultiples(data);
  renderDetail(data);
  renderTable(data);
}

registerView('sessions', d => {
  data = d;
  const wanted = viewParam();
  if (wanted && !(selected || '').startsWith(wanted)) selected = (d.sessions.find(s => s.session.startsWith(wanted)) || {}).session || selected;
  renderMultiples(d);
  renderDetail(d);
  renderTable(d);
});
})();
