(() => {
const root = document.querySelector('[data-view="rules"]');
const $ = s => root.querySelector(s);
const P = s => document.querySelector('#panel-content').querySelector(s);
function renderSteps(d) {
  const hist = d.steps_hist;
  const warn = d.settings.turn_warn_steps, stop = d.settings.turn_stop_steps;
  const total = hist.reduce((s, h) => s + h.prompts, 0);
  const prompts = total || 1;
  const bill = hist.reduce((s, h) => s + h.bill, 0) || 1;
  const top = Math.max(...hist.map(h => Math.max(h.prompts / prompts, h.bill / bill))) || 1;
  const bar = (cls, share, value) => `<span class="sb-bar ${cls}"><span class="track"><i style="width:${(share / top * 100).toFixed(1)}%"></i></span><b>${value}</b><small>${pct(share)}</small></span>`;
  const row = h => `<div class="sb-row${h.low >= stop ? ' bad' : h.low >= warn ? ' warn' : ''}"><span class="sb-band">${esc(h.label)}</span>
      ${bar('prompts', h.prompts / prompts, h.prompts.toLocaleString())}${bar('bill', h.bill / bill, fmtM(h.bill))}
      <span class="sb-each">${h.prompts ? fmtM(h.bill / h.prompts) : '–'}</span></div>`;
  $('#steps-chart').innerHTML = `<div class="sb-head"><span>steps</span><span>prompts with a step · share of ${total.toLocaleString()}</span><span>re-read tokens · share of ${fmtM(bill)}</span><span>per prompt</span></div>${hist.map(row).join('')}`;
  $('#steps-legend').innerHTML = `<span><i class="sbp"></i>prompts</span><span><i class="sbb"></i>re-read tokens</span>${hist.some(h => h.low >= warn && h.low < stop) ? '<span><i class="amber"></i>warned</span>' : ''}<span><i class="red"></i>${stop}+ steps, the turn budget</span><span>both bars are the band's share of the total, on one scale</span>`;
  const over = hist.filter(h => h.low >= warn);
  const overPrompts = over.reduce((s, h) => s + h.prompts, 0);
  const overBill = over.reduce((s, h) => s + h.bill, 0);
  $('#steps-note').textContent = `A band whose blue bar is longer than its grey bar costs more than its share of prompts. ${overPrompts.toLocaleString()} of ${total.toLocaleString()} prompts ran ${over.length ? over[0].low : stop}+ steps and carry ${pct(overBill / bill)} of the re-read tokens.`;
}

let openRule = '';
let openRow = null;

function countOf(d, kind) { return (d.by_kind[kind] || {}).count || 0; }
function fired(d, rule) { return rule.kinds.reduce((t, k) => t + countOf(d, k), 0); }
function keptOut(d, rule) { return rule.kinds.reduce((t, k) => t + ((d.by_kind[k] || {}).kept_out || 0), 0); }

function facts(d, rule) {
  const n = kind => countOf(d, kind);
  if (rule.key === 'cap') {
    const tool = r => { const [name, how] = r.tool.split(' · '); return `${r.count} ${name}${how ? ` (${how})` : ''}`; };
    return d.caps.by_tool.map(tool).join(' · ') || 'no cap has fired yet';
  }
  if (rule.key === 'pruner') {
    const before = d.compactions.filter(r => !r.mode).length;
    return `${n('pruned')} pruned · ${n('summary')} summary fallbacks${before ? ` · ${before} compactions before the plugin was loaded` : ''}`;
  }
  if (rule.key === 'turn') {
    const pauses = d.budget_rows.filter(b => b.kind === 'turn_stop');
    const choices = Object.entries(d.totals.turn_choices || {}).map(([k, v]) => `${v} ${k}`).join(', ');
    const complied = pauses.filter(b => b.after != null && b.after < d.settings.comply_steps).length;
    const over = n('turn_over') ? ` · ${n('turn_over')} over budget in warn mode` : '';
    const mode = d.settings.turn_budget_mode && d.settings.turn_budget_mode !== 'pause' ? ` · budget set to ${d.settings.turn_budget_mode}` : '';
    return `${n('turn_warn')} warnings · ${n('turn_stop')} pauses (budget ${d.settings.turn_stop_steps} steps, warning at ${d.settings.turn_warn_steps})${pauses.length ? `, ${n('turn_resume')} resumed, ${complied} complied, mean ${d.totals.mean_steps_after_stop} steps after` : ''}${choices ? ` · at the pause question: ${choices}` : ''}${over}${mode}`;
  }
  if (rule.key === 'reread') return `${n('reread_check')} shown · ${n('delegated')} delegated`;
  if (rule.key === 'paste') return `${n('paste_saved')} saved · ${n('paste_refused')} refused`;
  return rule.kinds.filter(kind => n(kind)).map(kind => `${n(kind)} ${label(kind)}`).join(' · ') || 'nothing yet';
}

const OUTCOME_KEYS = { notes: ['handoff', 'nudge', 'task_switch', 'read_nudge'], reads: ['reads'], route: ['route'], reread: ['reread'], turn: ['turn'] };
const OUTCOME_NAMES = { handoff: 'handoff notice', nudge: 'compact or clear nudge', task_switch: 'task-switch notice', read_nudge: 'read nudge' };
function outcomeLines(d, rule) {
  const lines = (OUTCOME_KEYS[rule.key] || []).filter(k => (d.outcomes || {})[k]).map(k => {
    const o = d.outcomes[k];
    const name = OUTCOME_NAMES[k] ? `${OUTCOME_NAMES[k]}: ` : '';
    return `<span title="${esc(o.note)}">${esc(name)}followed ${o.followed} of ${o.fired}${o.rate == null ? '' : ` (${Math.round(o.rate * 100)}%)`} · ${esc(o.effect)}</span>`;
  });
  return lines.length ? `<p class="outcome">${lines.join('<br>')}</p>` : '';
}

function renderExperiment(d) {
  const e = d.experiment;
  const s = d.settings || {};
  $('#experiment-sheet').hidden = false;
  if (!e) {
    const rules = (s.holdout_rules || []).map(r => `<code>${esc(r)}</code>`).join(', ');
    $('#experiment').innerHTML = `<p class="hist-note">No holdout is running${s.holdout ? ` in this report's window` : ''}. To run one, set the plugin option <code>holdout</code> (or the environment variable <code>CLAUDE_PLUGIN_OPTION_HOLDOUT</code>) to one of ${rules || 'the holdout rules'}. Sessions whose id hashes odd then run with that rule off, and this sheet compares the two halves on context, re-read and steps per prompt once each side has ${s.experiment_min_sessions || 10} sessions. It is the one controlled measurement Kiasi can make of its own effect.</p>`;
    return;
  }
  const v = (side, key, f) => side[key] == null ? '–' : f(side[key]);
  const rows = [['sessions', 'sessions', String], ['prompts', 'prompts', String], ['median context', 'median_context', fmtK], ['re-read per prompt', 'reread_per_prompt', fmtM], ['steps per prompt', 'steps_per_prompt', String]]
    .map(([name, key, f]) => [name, num(v(e.on, key, f)), num(v(e.off, key, f))]);
  const short = e.enough ? '' : `<p class="hist-note">too few sessions yet: ${e.on.sessions} on, ${e.off.sessions} off of ${e.min_sessions} each</p>`;
  $('#experiment').innerHTML = `<h3 class="sub">${esc(e.rule)}</h3>${table(['', '#rule on', '#rule off'], rows)}${short}`;
}

function ruleDays(d) {
  const days = d.per_day.map(p => p.day).slice(-RULE_STRIP_DAYS);
  const perRule = {};
  // from the per-day counts of the report: the actions list is cut, the counts are not
  d.per_day.forEach(p => Object.entries(p.kinds || {}).forEach(([kind, n]) => {
    const rule = RULES.find(r => r.kinds.includes(kind));
    if (!rule) return;
    const row = perRule[rule.key] = perRule[rule.key] || {};
    row[p.day] = (row[p.day] || 0) + n;
  }));
  return { days, perRule };
}

function renderRules(d) {
  const { days, perRule } = ruleDays(d);
  const install = (d.since || {}).install_day;
  const md = day => day.slice(5).replace('-', '/');
  const span = days.length ? ` · ${md(days[0])}–${md(days[days.length - 1])}` : '';
  const row = rule => {
    const n = fired(d, rule), cut = keptOut(d, rule);
    const counts = perRule[rule.key] || {};
    const open = openRule === rule.key;
    return `<div class="rl-row${n ? '' : ' zero'}${open ? ' selected' : ''}" data-rule="${rule.key}" role="button" tabindex="0" aria-expanded="${open}">
      <div class="rl-name"><h3>${esc(rule.name)}</h3><p>${esc(rule.short)}</p></div>
      <b class="rl-num fired" data-label="fired">${n.toLocaleString()}</b>
      <b class="rl-num cut${cut ? '' : ' none'}" data-label="tokens cut">${cut ? fmtM(cut) : '–'}</b>
      <div class="rl-days">${days.length ? dayLine(counts, days, install) : ''}</div>
      <div class="rl-facts"><p class="facts">${esc(facts(d, rule))}</p>${outcomeLines(d, rule)}</div><span class="chev">›</span></div>`;
  };
  $('#rules').innerHTML = `<div class="rl-head"><span>rule</span><span class="num">fired · ${d.days || d.per_day.length} days</span><span class="num">tokens cut</span><span class="rl-dayhead"><em>fired per day${span}</em>${dayCellLabels(days)}</span><span>breakdown</span><span></span></div>${RULES.map(row).join('')}`;
  $('#rules').querySelectorAll('.rl-row').forEach(el => {
    el.addEventListener('click', () => select(el.dataset.rule));
    el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(el.dataset.rule); } });
  });
}

function dayBars(d, rule, W, H) {
  const days = d.per_day.map(r => r.day);
  const perDay = Object.fromEntries(days.map(day => [day, { count: 0, cut: 0 }]));
  d.actions.forEach(a => { const row = perDay[a.ts.slice(0, 10)]; if (row && rule.kinds.includes(a.kind)) { row.count += 1; row.cut += a.kept_out; } });
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
    svg += `<g><title>${esc(day)} · ${row.count} fired${row.cut ? ` · ${fmtM(row.cut)} cut` : ''}</title>${topBar('hist', x, top, bw, Math.max(0, f.bottom - top))}</g>`;
    if (row.count) svg += `<text class="lbl" x="${(x + bw / 2).toFixed(1)}" y="${(top - 5).toFixed(1)}" text-anchor="middle">${row.cut ? fmtM(row.cut) : row.count}</text>`;
    if (bw >= 34 || i % 2 === 0) svg += `<text class="lbl" x="${(x + bw / 2).toFixed(1)}" y="${f.H - f.pad.bottom + 18}" text-anchor="middle">${esc(day.slice(5))}</text>`;
  });
  return svg + baseline(f) + '</svg>';
}

const num = v => ({ v, cls: 'num' });
const mono = v => ({ v, cls: 'mono' });
function cell(c) { return typeof c === 'object' && c !== null ? `<td class="${c.cls}">${esc(String(c.v))}</td>` : `<td>${esc(String(c))}</td>`; }
function table(head, rows) {
  if (!rows.length) return '';
  return `<table><thead><tr>${head.map(h => `<th${h.startsWith('#') ? ' class="num"' : ''}>${esc(h.replace(/^#/, ''))}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(cell).join('')}</tr>`).join('')}</tbody></table>`;
}
function section(title, body, note) {
  if (!body && !note) return '';
  return `${body ? `<h3 class="sub" data-jump="${esc(JUMP_LABELS[title] || title)}">${esc(title)}</h3>${body}` : ''}${note ? `<p class="hist-note">${esc(note)}</p>` : ''}`;
}

function evidence(d, rule) {
  if (rule.key === 'cap') {
    return section('Which tools get capped', table(['tool · kind', '#count', '#tokens cut'], d.caps.by_tool.map(r => [r.tool, num(r.count), num(fmtK(r.kept_out))])))
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
    return section('Every paste handled', table(['when', 'session', 'what', '#tokens cut'], d.actions.filter(a => rule.kinds.includes(a.kind)).map(a => [mono(stamp(a.ts)), mono(a.session.slice(0, 8)), a.label, num(a.kept_out ? fmtM(a.kept_out) : '')])));
  }
  return '';
}

function actionRow(a, i) {
  const tone = a.kind === 'turn_stop' ? (a.later_steps < data.settings.comply_steps ? 'ok' : 'bad') : a.kind;
  return `<div class="action click ${openRow === i ? 'open' : ''}" data-i="${i}"><span class="when">${esc(stamp(a.ts))}</span><span>${pill(tone, label(a.kind))}</span>
    <span class="what">${esc(a.label)}</span><span class="saved${a.kept_out ? '' : ' zero'}">${a.kept_out ? fmtM(a.kept_out) : '·'}</span><span class="steps">${['turn_stop', 'turn_over'].includes(a.kind) ? `${a.later_steps} after` : ''}</span></div>`;
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
  const cut = keptOut(d, rule);
  const order = RULES.map(x => x.key);
  const host = openPanel(rule.name, rule.what, '', {
    onClose: () => { openRule = ''; openRow = null; setViewParam(''); renderRules(data); },
    nav: { index: order.indexOf(rule.key), total: order.length, go: dir => { const next = order[order.indexOf(openRule) + dir]; if (next) select(next, true); } },
  });
  host.innerHTML = `<div class="rule-nums"><b>${fired(d, rule)}<small>fired</small></b>${cut ? `<b>${fmtM(cut)}<small>tokens cut</small></b>` : ''}</div>
    <p class="facts">${esc(facts(d, rule))}</p>
    <h3 class="sub" data-jump="Per day">Fired per day</h3><div class="chart">${dayBars(d, rule, panelWidth(), RULE_CHART_HEIGHT)}</div>
    <p class="hist-note">${cut ? 'Bars count how often the rule fired; the figure above a bar is the tokens it cut that day.' : 'Bars count how often the rule fired that day.'}</p>
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

function renderProblems(d) {
  const rows = d.problems || [];
  $('#problems-sheet').hidden = !rows.length;
  $('#problems').innerHTML = rows.length ? `<table class="since-table"><thead><tr><th>problem</th><th class="num">count</th><th>last seen</th><th>last message</th></tr></thead><tbody>${rows.map(row => {
    const at = new Date(row.ts);
    const seen = isNaN(at) ? row.ts : at.toLocaleString([], WHEN_OPTS);
    return `<tr><td>${esc(row.kind)}<br><span class="hist-note">${esc(row.explanation)}</span></td><td class="num">${row.count}</td><td>${esc(seen)}${row.session ? ` · ${esc(row.session)}` : ''}</td><td>${esc(row.message || row.path || '–')}</td></tr>`;
  }).join('')}</tbody></table>` : '';
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
  renderExperiment(d);
  renderSteps(d);
  renderTuning(d);
  renderProblems(d);
});
})();
