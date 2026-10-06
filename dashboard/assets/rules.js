(() => {
const root = document.querySelector('[data-view="rules"]');
const $ = s => root.querySelector(s);
const P = s => document.querySelector('#panel-content').querySelector(s);
function renderSteps(d) {
  const hist = d.steps_hist;
  const host = $('#steps-chart');
  const f = frame(chartWidth('#steps-chart'), CHART_HEIGHT);
  const max = Math.max(...hist.map(h => h.prompts)) || 1;
  const y = scaleY(f, max);
  const step = f.innerW / hist.length;
  const barW = Math.max(8, step * 0.55);
  const totalBill = hist.reduce((s, h) => s + h.bill, 0) || 1;
  let svg = `<svg viewBox="0 0 ${f.W} ${f.H}" role="img" aria-label="Prompts per step band">${gridY(f, y, max, v => String(Math.round(v)))}`;
  hist.forEach((h, i) => {
    const x = f.pad.left + i * step + (step - barW) / 2;
    const cls = h.low >= d.settings.turn_stop_steps ? 'bad' : h.low >= d.settings.turn_warn_steps ? 'warn' : '';
    const tip = `${h.label} steps · ${h.prompts} prompts · ${fmtM(h.bill)} re-read, ${pct(h.bill / totalBill)} of the bill`;
    svg += `<g data-tip="${esc(tip)}">` + topBar(`hist ${cls}`, x, y(h.prompts), barW, Math.max(0, f.bottom - y(h.prompts)));
    svg += `<text x="${x + barW / 2}" y="${y(h.prompts) - 6}" text-anchor="middle" class="lbl sub">${fmtM(h.bill)}</text>`;
    svg += `<text x="${x + barW / 2}" y="${f.H - f.pad.bottom + 18}" text-anchor="middle" class="lbl">${esc(h.label)}</text></g>`;
  });
  host.innerHTML = svg + baseline(f) + '</svg>';
  bindTips(host);
  const over = hist.filter(h => h.low >= d.settings.turn_warn_steps);
  const overPrompts = over.reduce((s, h) => s + h.prompts, 0);
  const overBill = over.reduce((s, h) => s + h.bill, 0);
  $('#steps-note').textContent = `Bars count prompts; the figure above each is the re-read bill of that band. Prompts of ${d.settings.turn_warn_steps}+ steps are ${pct(overPrompts / Math.max(1, d.totals.prompts))} of prompts and ${pct(overBill / totalBill)} of the bill.`;
}

let data = null;
let openRule = '';
let openRow = null;

function countOf(d, kind) { return (d.by_kind[kind] || {}).count || 0; }
function fired(d, rule) { return rule.kinds.reduce((t, k) => t + countOf(d, k), 0); }
function avoided(d, rule) { return rule.kinds.reduce((t, k) => t + ((d.by_kind[k] || {}).saved || 0), 0); }

function facts(d, rule) {
  const n = kind => countOf(d, kind);
  if (rule.key === 'cap') {
    const caps = d.caps.by_tool.map(r => `${r.tool} ${r.count}`).join(' · ');
    return caps ? `${fmtK((d.by_kind.cap || {}).kept_out || 0)} kept out · ${caps}` : 'no cap has fired yet';
  }
  if (rule.key === 'pruner') {
    const before = d.compactions.filter(r => !r.mode).length;
    return `${n('pruned')} pruned · ${n('summary')} summary fallbacks${before ? ` · ${before} compactions before the plugin was loaded` : ''}`;
  }
  if (rule.key === 'turn') {
    const pauses = d.budget_rows.filter(b => b.kind === 'turn_stop');
    const complied = pauses.filter(b => b.after != null && b.after < d.settings.comply_steps).length;
    const over = n('turn_over') ? ` · ${n('turn_over')} over budget in warn mode` : '';
    const mode = d.settings.turn_budget_mode && d.settings.turn_budget_mode !== 'pause' ? ` · budget set to ${d.settings.turn_budget_mode}` : '';
    return `${n('turn_warn')} warnings at ${d.settings.turn_warn_steps} steps · ${n('turn_stop')} pauses at ${d.settings.turn_stop_steps}${pauses.length ? `, ${n('turn_resume')} resumed, ${complied} complied` : ''}${over}${mode}`;
  }
  if (rule.key === 'reread') return `${n('reread_check')} shown · ${n('delegated')} delegated`;
  if (rule.key === 'paste') return `${n('paste_saved')} saved · ${n('paste_refused')} refused`;
  return rule.kinds.filter(kind => n(kind)).map(kind => `${n(kind)} ${label(kind)}`).join(' · ') || 'nothing yet';
}

function renderRules(d) {
  $('#rules').innerHTML = RULES.map(rule => `<article class="card rule ${rule.muted ? 'muted' : ''} ${openRule === rule.key ? 'selected' : ''}" data-rule="${rule.key}"><h3>${esc(rule.name)}</h3><p>${esc(rule.what)}</p>
      <div class="rule-nums"><b>${fired(d, rule)}<small>fired</small></b>${avoided(d, rule) ? `<b>${fmtM(avoided(d, rule))}<small>avoided${est(rule.key)}</small></b>` : ''}</div>
      <p class="facts">${esc(facts(d, rule))}</p><span class="logbtn">${openRule === rule.key ? 'close' : 'open →'}</span></article>`).join('');
  $('#rules').querySelectorAll('.rule').forEach(el => el.addEventListener('click', () => select(el.dataset.rule)));
}

function dayBars(d, rule, W, H) {
  const days = d.per_day.map(r => r.day);
  const perDay = Object.fromEntries(days.map(day => [day, { count: 0, saved: 0 }]));
  d.actions.forEach(a => { const row = perDay[a.ts.slice(0, 10)]; if (row && rule.kinds.includes(a.kind)) { row.count += 1; row.saved += a.saved; } });
  const f = frame(W, H);
  const max = Math.max(1, ...days.map(day => perDay[day].count));
  const y = scaleY(f, max);
  const slot = f.innerW / Math.max(1, days.length);
  const bw = Math.min(46, slot * 0.6);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(rule.name)} fired per day">` + gridY(f, y, max, v => String(Math.round(v)));
  days.forEach((day, i) => {
    const row = perDay[day];
    const x = f.pad.left + slot * i + (slot - bw) / 2;
    const top = y(row.count);
    svg += `<g><title>${esc(day)} · ${row.count} fired${row.saved ? ` · ${fmtM(row.saved)} avoided` : ''}</title>${topBar('hist', x, top, bw, Math.max(0, f.bottom - top))}</g>`;
    if (row.count) svg += `<text class="lbl" x="${(x + bw / 2).toFixed(1)}" y="${(top - 5).toFixed(1)}" text-anchor="middle">${row.saved ? fmtM(row.saved) : row.count}</text>`;
    if (bw >= 34 || i % 2 === 0) svg += `<text class="lbl" x="${(x + bw / 2).toFixed(1)}" y="${f.H - f.pad.bottom + 18}" text-anchor="middle">${esc(day.slice(5))}</text>`;
  });
  return svg + baseline(f) + '</svg>';
}

const num = v => ({ v, cls: 'num' });
const mono = v => ({ v, cls: 'mono' });
function cell(c) { return typeof c === 'object' && c !== null ? `<td class="${c.cls}">${esc(String(c.v))}</td>` : `<td>${esc(String(c))}</td>`; }
function table(head, rows) {
  if (!rows.length) return '';
  return `<table><thead><tr>${head.map(h => `<th${h.startsWith('#') ? ' class="num"' : ''}>${esc(h.replace(/^#/, ''))}${h === '#avoided' ? est(openRule) : ''}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(cell).join('')}</tr>`).join('')}</tbody></table>`;
}
function section(title, body, note) {
  if (!body && !note) return '';
  return `${body ? `<h3 class="sub" data-jump="${esc(JUMP_LABELS[title] || title)}">${esc(title)}</h3>${body}` : ''}${note ? `<p class="hist-note">${esc(note)}</p>` : ''}`;
}

function evidence(d, rule) {
  if (rule.key === 'cap') {
    return section('Which tools get capped', table(['tool · kind', '#count', '#kept out', '#avoided'], d.caps.by_tool.map(r => [r.tool, num(r.count), num(fmtK(r.kept_out)), num(fmtM(r.saved))])))
      + section('How much each cut kept out', table(['size of the cut', '#count'], d.caps.sizes.filter(r => r.count).map(r => [r.label, num(r.count)])));
  }
  if (rule.key === 'pruner') {
    const rows = d.compactions.filter(r => r.mode);
    const before = d.compactions.length - rows.length;
    return section('Every compaction the pruner handled', table(['when', 'session', 'trigger', 'mode', '#before', '#after', '#level'], rows.map(r => [mono(stamp(r.ts)), mono(r.session || '·'), r.trigger || '', r.mode, num(fmtK(r.before)), num(fmtK(r.after)), num(r.level == null ? '' : r.level)])),
      before ? `${before} compactions from before the plugin was loaded carry no data and are not listed.` : '');
  }
  if (rule.key === 'turn') {
    return section('Every warning, pause and resume', table(['when', 'session', 'what', '#steps', '#re-read so far', 'after the pause'], d.budget_rows.map(r => [mono(stamp(r.ts)), mono(r.session), label(r.kind), num(r.steps), num(r.reread == null ? '' : fmtM(r.reread)), r.after == null ? esc(r.note || '') : `${r.after} steps${r.after < d.settings.comply_steps ? ', complied' : ', ignored'}`])));
  }
  if (rule.key === 'reread') {
    return section('Every check shown', table(['when', 'session', '#context', '#steps per prompt', '#here', '#in a subagent', 'chosen'], d.checks.map(r => [mono(stamp(r.ts)), mono(r.session), num(fmtK(r.context)), num(r.steps), num(fmtM(r.here)), num(fmtM(r.delegated)), r.mode])));
  }
  if (rule.key === 'paste') {
    return section('Every paste handled', table(['when', 'session', 'what', '#avoided'], d.actions.filter(a => rule.kinds.includes(a.kind)).map(a => [mono(stamp(a.ts)), mono(a.session.slice(0, 8)), a.label, num(a.saved ? fmtM(a.saved) : '')])));
  }
  return '';
}

function actionRow(a, i) {
  const tone = a.kind === 'turn_stop' ? (a.later_steps < data.settings.comply_steps ? 'ok' : 'bad') : a.kind;
  return `<div class="action click ${openRow === i ? 'open' : ''}" data-i="${i}"><span class="when">${esc(stamp(a.ts))}</span><span>${pill(tone, label(a.kind))}</span>
    <span class="what">${esc(a.label)}</span><span class="saved${a.saved ? '' : ' zero'}">${a.saved ? fmtM(a.saved) : '·'}</span><span class="steps">${['turn_stop', 'turn_over'].includes(a.kind) ? `${a.later_steps} after` : a.later_steps ? `× ${a.later_steps}` : ''}</span></div>`;
}
function detailRow(a) {
  const link = a.session ? ` · <a href="#sessions/${esc(a.session.slice(0, 8))}">session ${esc(a.session.slice(0, 8))}</a>` : '';
  return `<div class="detail"><p>${esc(a.formula || 'no token effect is attributed to this row; it is counted')}${link}</p><pre>${esc(JSON.stringify(a.record, null, 1))}</pre></div>`;
}

function renderLog(d) {
  const rule = RULES.find(r => r.key === openRule);
  if (!rule || !P('#log')) return;
  const text = P('#log-filter').value.trim().toLowerCase();
  const rows = d.actions.map((a, i) => [a, i]).filter(([a]) => rule.kinds.includes(a.kind) && (!text || `${a.label} ${a.session} ${JSON.stringify(a.record)}`.toLowerCase().includes(text)));
  P('#log-count').textContent = rows.length > LOG_ROWS ? `first ${LOG_ROWS} of ${rows.length}` : `${rows.length} rows`;
  P('#log').innerHTML = rows.slice(0, LOG_ROWS).map(([a, i]) => actionRow(a, i) + (openRow === i ? detailRow(a) : '')).join('') || '<div class="empty">Nothing logged for this rule yet.</div>';
}

function renderDetail(d) {
  const rule = RULES.find(r => r.key === openRule);
  if (!rule) { closePanel(true); return; }
  const saved = avoided(d, rule);
  const order = RULES.map(x => x.key);
  const host = openPanel(rule.name, rule.what, '', {
    onClose: () => { openRule = ''; openRow = null; setViewParam(''); renderRules(data); },
    nav: { index: order.indexOf(rule.key), total: order.length, go: dir => { const next = order[order.indexOf(openRule) + dir]; if (next) select(next, true); } },
  });
  host.innerHTML = `<div class="rule-nums"><b>${fired(d, rule)}<small>fired</small></b>${saved ? `<b>${fmtM(saved)}<small>avoided${est(rule.key)}</small></b>` : ''}</div>
    <p class="facts">${esc(facts(d, rule))}</p>
    <h3 class="sub" data-jump="Per day">Fired per day</h3><div class="chart">${dayBars(d, rule, panelWidth(), RULE_CHART_HEIGHT)}</div>
    <p class="hist-note">${saved ? 'Bars count how often the rule fired; the figure above a bar is the tokens it avoided that day.' : 'Bars count how often the rule fired that day.'}</p>
    ${evidence(d, rule)}
    <h3 class="sub">Log</h3><p class="hist-note">Newest first. A saving is tokens kept out × later steps in the session. Open a row for the raw record.</p>
    <div class="filters"><input id="log-filter" type="search" placeholder="filter by text, tool or path"><span class="count" id="log-count"></span></div><div id="log"></div>`;
  P('#log-filter').addEventListener('input', () => renderLog(d));
  P('#log').addEventListener('click', e => {
    const row = e.target.closest('.action');
    if (!row || e.target.closest('a')) return;
    const i = Number(row.dataset.i);
    openRow = openRow === i ? null : i;
    renderLog(d);
  });
  renderLog(d);
  finishPanelContent();
}

function select(key, keep) {
  openRule = openRule === key && !keep ? '' : key;
  openRow = null;
  setViewParam(openRule);
  renderRules(data);
  renderDetail(data);
}

registerView('rules', d => {
  data = d;
  const wanted = viewParam();
  if (wanted && wanted !== openRule && RULES.some(r => r.key === wanted)) { openRule = wanted; openRow = null; }
  renderRules(d);
  renderDetail(d);
  renderSteps(d);
});
})();
