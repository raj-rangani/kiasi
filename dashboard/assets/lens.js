(() => {
const root = document.querySelector('[data-view="overview"]');
const $ = s => root.querySelector(s);
const stepsPerDay = p => p.steps_per_day !== undefined ? p.steps_per_day : p.reread_per_turn && p.reread_per_day != null ? Math.round(p.reread_per_day / p.reread_per_turn) : null;

function change(before, after) {
  if (!before || after == null) return '–';
  const r = (after - before) / before;
  return `${r < 0 ? '−' : '+'}${Math.abs(Math.round(r * 100))}%`;
}

function renderBill(d) {
  const days = d.per_day;
  const host = $('#bill-chart');
  if (!days.length) { host.innerHTML = emptyLine('days'); return; }
  const f = frame(chartWidth('#bill-chart'), CHART_HEIGHT, BILL_PAD);
  const max = Math.max(...days.map(day => day.paid)) || 1;
  const y = scaleY(f, max);
  const step = f.innerW / days.length;
  const barW = Math.max(8, step * 0.55);
  let svg = `<svg viewBox="0 0 ${f.W} ${f.H}" role="img" aria-label="Re-read tokens paid per day">${gridY(f, y, max, fmtM)}`;
  days.forEach((day, i) => {
    const x = f.pad.left + i * step + (step - barW) / 2;
    const mid = x + barW / 2;
    const tip = `${day.day} · ${fmtM(day.paid)} re-read · ${day.prompts || 0} prompts · ${fmtK(day.reread_per_prompt || 0)} per prompt · ${day.sessions || 0} sessions`;
    svg += `<g data-tip="${esc(tip)}">${topBar('hist', x, y(day.paid), barW, Math.max(0, f.bottom - y(day.paid)))}<text class="lbl" x="${mid}" y="${y(day.paid) - 6}" text-anchor="middle">${fmtM(day.paid)}</text>`;
    svg += `<text x="${mid}" y="${f.H - f.pad.bottom + 18}" text-anchor="middle" class="lbl">${dayLabel(day.day, step)}</text>`;
    svg += '</g>';
  });
  const install = (d.since || {}).install_day;
  const idx = install ? days.findIndex(day => day.day >= install) : -1;
  if (idx > 0) {
    const mx = f.pad.left + idx * step;
    svg += `<line class="marker" x1="${mx}" x2="${mx}" y1="${f.pad.top}" y2="${f.bottom}"/><text class="marker-lbl" x="${mx + 6}" y="${f.pad.top + 12}">Kiasi on</text>`;
  }
  host.innerHTML = svg + baseline(f) + '</svg>';
  bindTips(host);
}

let allFloor = false;

const ATTRIB_ADVICE = {
  'floor': 'What a session loads before its first reply, paid on every request. Trim CLAUDE.md, memory and MCP servers you do not use.',
  'system reminders': 'Context that hooks and plugins attach on every prompt; it stays for the rest of the session. Turn off plugins you do not use.',
  'file reads': 'A whole file read stays in the context until compaction. Read files by section, or distill them.',
  'shell output': 'What commands printed. Route long commands through mcp__kiasi__run so only the useful lines stay.',
  'web pages': 'Pages fetched into the conversation. Fetch them through mcp__kiasi__fetch so only a summary stays.',
  'tool calls': 'Edits and writes carry their whole text. Many small ones add up.',
  'subagent results': 'What subagents report back lands in the main context. Brief them to return a short answer.',
  'pastes': 'Text pasted into prompts. Paste the part that matters, or save it to a file and name it.',
  'compaction summary': 'The summary left after a compaction. Kiasi prunes it when it can.',
};

const PLAIN_KIND = {
  'floor': ['Startup floor', 'what a session loads before its first reply'],
  'system reminders': ['System reminders', 'context that hooks and plugins attach'],
  'file reads': ['File reads', 'whole files read into the conversation'],
  'shell output': ['Shell output', 'what commands printed'],
  'tool calls': ['Tool calls', 'edits, writes and their arguments'],
  'subagent results': ['Subagent results', 'what subagents reported back'],
  'web pages': ['Web pages', 'pages fetched into the conversation'],
  'pastes': ['Pastes', 'text pasted into prompts'],
  'compaction summary': ['Compaction summary', 'the summary left after a compaction'],
};
const plainKind = kind => PLAIN_KIND[kind] || [kind.charAt(0).toUpperCase() + kind.slice(1), ''];
const FIX_ROWS = 3;

function renderFixes(d) {
  const all = ((d.attribution || {}).kinds || []);
  const block = $('#fix-block');
  block.hidden = !all.length;
  if (!all.length) return;
  const rows = all.slice(0, FIX_ROWS);
  const max = all[0].tokens;
  $('#fix-sub').textContent = `the ${FIX_ROWS} biggest of ${all.length} kinds`;
  $('#fixes-list').innerHTML = rows.map((r, i) => {
    const [name, what] = plainKind(r.kind);
    return `<li class="fix"><span class="rank">${i + 1}</span><span class="name">${esc(name)}</span>
      <span class="share"><span class="hbar"><i style="width:${Math.max(1, 100 * r.tokens / max)}%"></i></span><span>${Math.round(r.share * 100)}%</span></span>
      <span class="what">${esc(ATTRIB_ADVICE[r.kind] || what)}</span></li>`;
  }).join('');
}

function renderSpikeNote(d) {
  const rows = (d.spikes || {}).rows || [];
  const host = $('#spike-note');
  host.hidden = !rows.length;
  if (host.hidden) return;
  const top = rows[0];
  const more = rows.length > 1 ? ` and ${rows.length - 1} more` : '';
  host.innerHTML = `One session on ${esc(String(top.day || '').slice(5, 10))} sent ${fmtM(top.reread)} of context across ${top.prompts} prompt${top.prompts === 1 ? '' : 's'}, ${top.factor}× the usual${more}. <a href="#sessions">See it in Sessions →</a>`;
}

function heroCard(label, big, tone, sub, note, tip) {
  return `<div class="hero-card"${tip ? ` title="${esc(tip)}"` : ''}><span class="hero-label">${esc(label)}</span><b class="${tone}">${big}</b><span class="hero-sub">${sub}</span><span class="hero-note">${note}</span></div>`;
}

function renderHero(d) {
  const s = d.since || {}, b = s.before, a = s.after, t = d.totals, p = d.pace || {};
  const kind = key => (d.by_kind || {})[key] || { count: 0, kept_out: 0 };
  const kept = kind('cap').kept_out + kind('pruned').kept_out;
  const cards = [];
  let since, sinceTip;
  if (b && a && a.reread_per_turn != null && b.reread_per_turn != null && s.factor != null) {
    since = s.factor >= 1 ? `<em class="good">${s.factor}× less</em> context per request since Kiasi` : `<em class="bad">${(1 / s.factor).toFixed(1)}× more</em> context per request since Kiasi`;
    sinceTip = `Tokens re-sent per main-session step: ${fmtK(a.reread_per_turn)} now, ${fmtK(b.reread_per_turn)} before the install. The work differs between the two periods, so read it as an observation.`;
  } else if (b && a) {
    since = `${fmtK(a.reread_per_turn)} per request now, ${fmtK(b.reread_per_turn)} before Kiasi`;
    sinceTip = `The factor appears once each side has ${(s.factor_min_steps || 0).toLocaleString()} steps.`;
  } else {
    since = `${fmtK(t.reread_per_prompt)} of context per prompt`;
    sinceTip = 'The comparison with before Kiasi appears after its first full day.';
  }
  cards.push(heroCard('Kept out of your limit', `${fmtM(kept)}<small>tokens</small>`, 'good', since,
    `${kind('cap').count} outputs capped, ${t.pruned} compactions pruned, ${d.days || d.per_day.length} days`,
    `Tokens that would have sat in the context on every later request: tool output past the cap, and transcript removed at compaction. ${sinceTip}`));
  if (p.rate != null && p.prior_rate) {
    cards.push(heroCard('This week', change(p.prior_rate, p.rate), p.rate <= p.prior_rate ? 'good' : 'bad', 'context sent, against last week',
      `${fmtM(p.rate)} a day, ${fmtM(p.prior_rate)} last week`, p.note || ''));
  } else if (p.rate != null) {
    cards.push(heroCard('This week', `${fmtM(p.rate)}`, '', 'context sent a day', 'no earlier week to compare', p.note || ''));
  }
  const top = ((d.attribution || {}).kinds || [])[0];
  if (top) {
    const [name, what] = plainKind(top.kind);
    cards.push(heroCard('Next fix', `<span class="hero-text">${esc(name)}</span>`, '', `${Math.round(top.share * 100)}% of the context sent`, esc(ATTRIB_ADVICE[top.kind] || what), ''));
  }
  $('#hero').innerHTML = `${cards.join('')}<p class="hero-caption">Every request re-sends the whole conversation to the model. That is what uses up a limit.</p>`;
  $('#chart-sub').textContent = `${d.days || d.per_day.length} days`;
}

registerView('overview', d => { renderHero(d); renderSpikeNote(d); renderBill(d); renderFixes(d); });
})();
