(() => {
const root = document.querySelector('[data-view="sessions"]');
const $ = s => root.querySelector(s);
let data = null;
let selected = '';

function syntheticNote(n, kinds) {
  const k = kinds || {}, bits = [];
  if (k.replayed) bits.push(`${k.replayed.toLocaleString()} replayed history`);
  if (k.interrupted) bits.push(`${k.interrupted.toLocaleString()} interrupted`);
  if (k.api_error) bits.push(`${k.api_error.toLocaleString()} API errors`);
  if (k.no_response) bits.push(`${k.no_response.toLocaleString()} no-response placeholders`);
  return `${n.toLocaleString()} synthetic entries skipped${bits.length ? `: ${bits.join(', ')}` : ''}; they carry no usage, so they are not counted as requests.`;
}

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
  const shown = d.sessions.slice(0, TOP_MULTIPLES);
  host.innerHTML = shown.map(s => `<article class="mini ${s.session === selected ? 'selected' : ''}" data-session="${esc(s.session)}" title="session ${esc(s.short)} · ${esc(s.project)}">
    <h3><b>${esc(projectTail(s.project))}</b><span>${esc(s.day)}</span></h3>${sessionSvg(s, d.settings, W, MINI_HEIGHT, false)}
    <dl class="kv"><dt>prompts</dt><dd>${s.prompts}</dd><dt>steps</dt><dd>${s.steps}</dd><dt>peak</dt><dd>${fmtK(s.peak)}</dd><dt>bill</dt><dd>${fmtM(s.bill)}</dd></dl></article>`).join('') || emptyLine('sessions');
  host.querySelectorAll('.mini').forEach(el => el.addEventListener('click', () => select(el.dataset.session)));
  bindTips(host);
  const legend = host.parentElement && host.parentElement.querySelector('.legend');
  if (legend) legend.innerHTML = markLegend(shown);
}

// Only the marks that actually appear in the cards shown, so the legend never explains a symbol that is not on the page.
function markLegend(sessions) {
  const has = kind => sessions.some(s => (s.series || []).some(p => (p.marks || []).includes(kind)));
  return '<span><i class="tick"></i>prompt</span>'
    + (has('compaction') ? '<span><i class="ring"></i>compaction</span>' : '')
    + (has('pruned') ? '<span><i class="ring pruned"></i>pruned compaction</span>' : '')
    + (has('check') ? '<span><i class="amber"></i>re-read check</span>' : '')
    + (has('stop') ? '<span><i class="red"></i>turn stopped</span>' : '')
    + '<span><i class="line"></i>warning</span><span><i class="line warn"></i>hard limit</span>';
}

let openAction = null;
let allPrompts = false;

function actionRows(rows) {
  return rows.map((a, i) => `<div class="action click ${openAction === i ? 'open' : ''}" data-i="${i}"><span class="when">${esc(stamp(a.ts))}</span><span>${pill(a.kind)}</span><span class="what">${esc(a.label)}</span>
    <span class="saved${a.kept_out ? '' : ' zero'}">${a.kept_out ? fmtM(a.kept_out) : '·'}</span><span class="steps">${['turn_stop', 'turn_over'].includes(a.kind) ? `${a.later_steps} after` : ''}</span></div>${openAction === i ? `<div class="detail"><p>${esc(a.kept_out ? `${a.kept_out.toLocaleString()} tokens cut from the conversation` : 'no token count for this row; it is counted')}</p><pre>${esc(JSON.stringify(a.record, null, 1))}</pre></div>` : ''}`).join('');
}

function postmortemList(s) {
  const rows = s.findings || [];
  if (!rows.length) return '';
  return `<h3 class="sub" data-jump="Postmortem">What cost the most</h3><ol class="postmortem">${rows.map(f =>
    `<li><span class="pm-cost">${f.cost ? fmtM(f.cost) : '·'}</span><div>${esc(f.label)}<small>${esc(f.fix)}</small></div></li>`).join('')}</ol>`;
}

function billSplit(s, d) {
  const rows = s.prompt_rows || [];
  const bands = [['under the warning', r => r.steps < d.settings.turn_warn_steps, 'hist'], ['warned', r => r.steps >= d.settings.turn_warn_steps && r.steps < d.settings.turn_stop_steps, 'delegated'], [(d.settings.turn_budget_mode || 'pause') === 'pause' ? 'paused' : 'over budget', r => r.steps >= d.settings.turn_stop_steps, 'red']];
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
  const flag = r => r.steps >= d.settings.turn_stop_steps ? pill('turn_stop', (d.settings.turn_budget_mode || 'pause') === 'pause' ? 'paused' : 'over budget') : r.steps >= d.settings.turn_warn_steps ? pill('turn_warn', 'warned') : '';
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
    <div class="rule-nums"><b>${fmtM(s.bill)}<small>re-read bill</small></b><b>${fmtM(s.kept_out || 0)}<small>tokens cut</small></b><b>${s.prompts}<small>prompts</small></b><b>${s.steps}<small>steps</small></b><b>${fmtK(s.startup || 0)}<small>startup</small></b><b>${fmtK(s.mean_context)}<small>mean context</small></b><b>${fmtK(s.median_context)}<small>median</small></b><b>${fmtK(s.p90_context)}<small>p90</small></b><b>${fmtK(s.peak)}<small>peak</small></b><b>${s.compactions}<small>compactions</small></b></div>
    ${s.synthetic_skipped ? `<p class="hist-note">${syntheticNote(s.synthetic_skipped, s.synthetic_kinds)}</p>` : ''}
    <h3 class="sub" data-jump="Context">Context at every step</h3>
    <div class="chart">${sessionSvg(s, d.settings, panelWidth(), DETAIL_HEIGHT, true)}</div>
    <div class="legend"><span><i class="tick"></i>prompt</span><span><i class="ring"></i>compaction</span><span><i class="ring pruned"></i>pruned compaction</span><span><i class="amber"></i>re-read check</span><span><i class="red"></i>turn stopped</span><span><i class="line"></i>warning</span><span><i class="line warn"></i>hard limit</span></div>
    ${postmortemList(s)}
    <h3 class="sub" data-jump="Bill">Where the bill went</h3>${billSplit(s, d)}
    <h3 class="sub">Prompts</h3><p class="hist-note">Each prompt's cost is its context summed over the steps it ran. Prompt text is never logged.</p>${promptTable(s, d)}
    <h3 class="sub" data-jump="Actions">Kiasi actions in this session</h3>${actionRows(rows) || '<div class="empty">None logged.</div>'}`;
  bindTips(host);
  const toggle = host.querySelector('#toggle-prompts');
  if (toggle) toggle.addEventListener('click', e => { e.preventDefault(); allPrompts = !allPrompts; renderDetail(d); });
  host.querySelectorAll('.action.click').forEach(el => el.addEventListener('click', () => { const i = Number(el.dataset.i); openAction = openAction === i ? null : i; renderDetail(d); }));
  finishPanelContent();
}

function select(session, keep) {
  selected = selected === session && !keep ? '' : session;
  openAction = null;
  allPrompts = false;
  setViewParam(selected ? selected.slice(0, 8) : '');
  renderMultiples(data);
  renderDetail(data);
}

registerView('sessions', d => {
  data = d;
  const wanted = viewParam();
  if (wanted && !(selected || '').startsWith(wanted)) selected = (d.sessions.find(s => s.session.startsWith(wanted)) || {}).session || selected;
  renderMultiples(d);
  renderDetail(d);
});
})();
