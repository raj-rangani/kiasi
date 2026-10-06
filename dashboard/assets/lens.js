(() => {
const root = document.querySelector('[data-view="overview"]');
const $ = s => root.querySelector(s);
function renderSince(d) {
  const s = d.since || {};
  const host = $('#since');
  const b = s.before, a = s.after;
  const day = s.install_day ? stamp(s.install_day).slice(0, 10) : null;
  if (!day) {
    host.innerHTML = `<div class="since-empty">No Kiasi actions logged yet. Work in a Claude Code session or two and this block will compare the days before the install with the days after.</div>`;
    return;
  }
  if (!b || !a) {
    host.innerHTML = `<div class="since-empty">Kiasi has been on since ${day}. ${!b ? 'No transcripts from before that day were found, so there is no baseline to compare with.' : 'The comparison appears after the first full day with Kiasi on.'}</div>`;
    return;
  }
  const rows = [
    ['Context re-sent per step', fmtK(b.reread_per_turn), fmtK(a.reread_per_turn), change(b.reread_per_turn, a.reread_per_turn)],
    ['Mean context per turn', fmtK(b.mean_context), fmtK(a.mean_context), change(b.mean_context, a.mean_context)],
    ['Turns over 200 k', pct(b.high_share), pct(a.high_share), change(b.high_share, a.high_share)],
    ['Re-read tokens per day', fmtM(b.reread_per_day), a.reread_per_day == null ? '–' : fmtM(a.reread_per_day), change(b.reread_per_day, a.reread_per_day)],
  ];
  const headline = s.factor && s.factor >= 1.1
    ? `Each step costs <b>${s.factor}×</b> less than before`
    : s.factor ? `Each step costs about the same as before (${s.factor}×)`
    : `Not enough steps yet to compare: ${Math.min(b.turns, a.turns).toLocaleString()} of the ${s.factor_min_steps || 0} needed before and after`;
  const runway = s.factor && s.factor >= 1.1
    ? `The weekly limit mostly counts re-read tokens, so at the same pace of work it now lasts about <b>${s.factor}×</b> longer.`
    : 'The weekly limit is spent mostly on re-read tokens, so this number is the one to watch.';
  host.innerHTML = `
    <div class="since-lead"><small>Since Kiasi was switched on, ${day}</small><h3>${headline}</h3><p>${runway}</p>
      <div class="since-span" role="img" aria-label="Before: ${b.days} full days, ${b.turns.toLocaleString()} steps. After: ${a.days} full days plus today, ${a.turns.toLocaleString()} steps.">
        <div class="before" style="flex-grow: ${b.days}"><small>before · ${b.days} days</small><span>${b.turns.toLocaleString()} steps</span></div><div class="after" style="flex-grow: ${a.days + 0.5}" title="switched on ${day}"><small>after · ${a.days} days + today</small><span>${a.turns.toLocaleString()} steps</span></div></div></div>
    <table class="since-table"><thead><tr><th></th><th class="num">before</th><th class="num">after</th><th class="num">change</th></tr></thead>
      <tbody>${rows.map(([k, x, y, c]) => `<tr><td>${k}</td><td class="num">${x}</td><td class="num"><b>${y}</b></td><td class="num ${c.startsWith('−') ? 'good' : 'bad'}">${c}</td></tr>`).join('')}</tbody></table>`;
}

function change(before, after) {
  if (!before || after == null) return '–';
  const r = (after - before) / before;
  return `${r < 0 ? '−' : '+'}${Math.abs(Math.round(r * 100))}%`;
}

function todayVersus(d, key) {
  const install = (d.since || {}).install_day || '';
  const days = d.per_day.filter(day => day.prompts && day.day >= install);
  const today = days[days.length - 1] || {};
  const earlier = days.slice(0, -1).filter(day => day[key]);
  const mean = earlier.length ? earlier.reduce((sum, day) => sum + day[key], 0) / earlier.length : 0;
  return { today: today[key] || 0, day: today.day || '', mean };
}

function renderStats(d) {
  const t = d.totals;
  const all = d.all_time;
  const perPrompt = todayVersus(d, 'reread_per_prompt');
  const startup = todayVersus(d, 'startup');
  const versus = (row, what) => `${what} on ${row.day}; ${row.mean ? `${change(row.mean, row.today)} against the ${fmtK(row.mean)} daily mean since Kiasi was on` : 'no earlier day to compare with'}`;
  $('#stats').innerHTML = [
    [perPrompt.mean && perPrompt.today > perPrompt.mean ? 'warn' : '', fmtK(perPrompt.today), '<small>/ prompt</small>', versus(perPrompt, 're-read tokens per prompt')],
    [startup.mean && startup.today > startup.mean ? 'warn' : '', fmtK(startup.today), '<small>/ session</small>', versus(startup, 'first-request context per new session')],
    ['', fmtM(all.saved), `<small>${est('avoided')}</small>`, `re-read tokens avoided in all, since ${all.first_day || 'today'}, by ${all.caps} caps (${fmtM(all.kept_out)} kept out); grows with session length, so read it next to the two figures before it`],
    [t.summaries ? 'warn' : '', String(t.pruned), `<small>/ ${t.pruned + t.summaries} plugin</small>`, `compactions pruned instead of summarised; ${t.compactions} compactions in all`],
    [t.stops && t.stops_complied < t.stops ? 'warn' : '', String(t.stops), '<small>pauses</small>', `turns paused at the budget, ${t.stops ? `${t.resumed || 0} resumed, ${t.stops_complied} complied` : 'none yet'}, mean ${t.mean_steps_after_stop} steps after`],
  ].map(([cls, big, small, text]) => `<div class="stat ${cls}"><b>${big}${small}</b><span>${esc(text)}</span></div>`).join('');
}

function sparkline(values) {
  const { width, height } = MISS_SPARK;
  const lo = Math.min(...values), hi = Math.max(...values);
  const x = i => values.length > 1 ? (i * width / (values.length - 1)).toFixed(1) : width / 2;
  const y = v => (hi > lo ? height - 2 - (v - lo) / (hi - lo) * (height - 4) : height / 2).toFixed(1);
  const points = values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true"><polyline points="${points}"/><circle cx="${x(values.length - 1)}" cy="${y(values[values.length - 1])}" r="2"/></svg>`;
}

function cacheTiles(c) {
  const rate = c.hit_rate == null ? '–' : `${(c.hit_rate * 100).toFixed(1)}<small>%</small>`;
  const met = c.hit_rate != null && c.hit_rate >= c.hit_target;
  const days = c.hit_days.map(day => day.rate);
  const delta = c.prior_avoidable == null ? 'no earlier week to compare' : c.avoidable === c.prior_avoidable ? 'same as the week before'
    : `${c.avoidable > c.prior_avoidable ? '▲' : '▼'} ${Math.abs(c.avoidable - c.prior_avoidable)} vs ${c.prior_avoidable} the week before`;
  const cost = `every miss outside compaction, idle expiry included; a cache write is priced ${c.write_price}× input and a read ${c.read_price}×, so each missed token costs as much as ${Math.round((c.write_price - c.read_price) / c.read_price)} re-read tokens`;
  return [
    ['Hit rate', `${rate}${days.length > 1 ? sparkline(days) : ''}`, `${met ? '✓ above' : 'below'} the ${pct(c.hit_target)} target`, met ? '' : 'warn', 'share of input read from the cache, per day on the line'],
    ['Avoidable misses', `${c.avoidable}<small>of ${c.misses}</small>`, `${delta}${c.idle ? ` · ${c.idle} more from idle expiry` : ''}`, c.prior_avoidable != null && c.avoidable > c.prior_avoidable ? 'warn' : '', 'misses caused by neither compaction nor the cache expiring while the session was idle'],
    ['Extra cost', `${fmtM(c.extra)}<small>re-read tokens</small>${est('extra')}`, c.extra_share == null ? '' : `${(c.extra_share * 100).toFixed(1)}% of what you paid`, '', cost],
  ].map(([label, big, note, cls, tip]) => `<div class="cache-tile ${cls}" title="${esc(tip)}"><span class="cache-label">${label}</span><b>${big}</b><span>${esc(note)}</span></div>`).join('');
}

function cacheCauses(c) {
  const outside = c.causes.filter(row => row.cause !== 'compaction');
  const totalExtra = outside.reduce((sum, row) => sum + row.extra, 0);
  const shown = outside.filter(row => row.extra >= totalExtra * CACHE_CAUSE_MIN_SHARE);
  const max = Math.max(...shown.map(row => row.extra), 1);
  const rows = shown.map(row => `<div class="cause-row" title="${esc(MISS_CAUSE_HELP[row.cause] || '')} · ${row.count} misses">
      <span class="cause-name">${esc(row.cause)}</span>
      <span class="cause-bar"><i style="width:${(100 * row.extra / max).toFixed(1)}%"></i></span>
      <span class="cause-num">${fmtM(row.extra)}</span>
      <span class="cause-fix">${esc(MISS_CAUSE_FIX[row.cause] || '')}</span></div>`).join('');
  const left = [];
  const small = outside.length - shown.length;
  if (small) left.push(`${small} smaller cause${small > 1 ? 's' : ''}`);
  const compaction = c.causes.find(row => row.cause === 'compaction');
  if (compaction) left.push(`${compaction.count} compaction misses (expected)`);
  return `<div class="cause-rows">${rows}</div>${left.length ? `<p class="cause-note">Not shown: ${left.join(' and ')}.</p>` : ''}`;
}

function renderMisses(d) {
  const c = d.cache;
  $('#cache-tiles').innerHTML = c ? cacheTiles(c) : '';
  $('#cache-causes').innerHTML = c && c.misses ? cacheCauses(c) : emptyLine('cache misses');
}

function recallVerdict(share) {
  if (share < RECALL_LOW) return 'could be tighter';
  if (share > RECALL_HIGH) return 'cuts too much';
  return 'about right';
}

function renderRecall(d) {
  const rows = d.recall || [];
  const host = $('#recall');
  if (!rows.length) { host.innerHTML = emptyLine('cut outputs'); return; }
  const cuts = rows.reduce((sum, row) => sum + row.cuts, 0);
  const back = rows.reduce((sum, row) => sum + row.recalled, 0);
  const share = back / Math.max(1, cuts);
  host.innerHTML = `<p class="lead-line"><b>${back}</b> of <b>${cuts}</b> cut outputs were read back (${pct(share)}) · overall the caps ${recallVerdict(share) === 'about right' ? 'look about right' : recallVerdict(share)}</p>
  <table class="since-table recall-table"><thead><tr><th>cut kind</th><th class="num">cuts</th><th class="num">read back</th><th class="num">share</th><th>verdict</th></tr></thead><tbody>${rows.map(row => {
    const part = row.recalled / Math.max(1, row.cuts);
    return `<tr><td>${esc(row.kind)}</td><td class="num">${row.cuts}</td><td class="num">${row.recalled}</td><td class="num">${pct(part)}</td><td>${recallVerdict(part)}</td></tr>`;
  }).join('')}</tbody></table>
  <p class="recall-note">A cut counts as read back when a later tool call in the same session names its saved file. Skipped re-reads: ${d.totals.reads_skipped || 0}, let through on retry: ${d.totals.reads_retried || 0}.</p>`;
}

function renderStartupHint(d) {
  const startup = todayVersus(d, 'startup');
  const host = $('#startup-hint');
  host.hidden = !(startup.mean && startup.today > startup.mean * STARTUP_HINT_RATIO);
  if (host.hidden) return;
  host.innerHTML = `Startup cost is ${change(startup.mean, startup.today).slice(1)} above its mean since Kiasi was on. Run <code>/skill-doctor</code> in Claude Code to see unused skills and plugins, then set them to <code>off</code> or <code>user-invocable-only</code> in <code>/skills</code>, or turn plugins off for a project with <code>enabledPlugins</code> in its <code>.claude/settings.json</code>.`;
}

function mb(bytes) {
  return `${(bytes / 1e6).toFixed(bytes < 1e7 ? 1 : 0)} MB`;
}

function renderStorage(d) {
  const s = d.storage;
  const host = $('#storage');
  if (!s) { host.hidden = true; return; }
  host.hidden = false;
  const last = s.last || {};
  const parts = [`Data folder ${mb(s.size)} of ${mb(s.max)}`];
  if (s.mode === 'off') parts.push('cleanup is off (<code>KIASI_CLEANUP=off</code>)');
  else if (!last.ts) parts.push('no cleanup run yet');
  else if (last.error) parts.push(`last cleanup ${stamp(last.ts).slice(0, 16)} failed: ${esc(last.error)}`);
  else if (last.mode === 'report') parts.push(`report only until ${esc(addDays(last.first_run, s.report_days))}: ${last.candidates} files (${mb(last.candidate_bytes)}) unused ${s.idle_days}+ days would go to trash`);
  else parts.push(`last cleanup ${stamp(last.ts).slice(0, 16)} moved ${last.moved} files to trash, deleted ${last.purged} after ${s.trash_days} days; trash ${mb(last.trash_bytes || 0)}`);
  if (last.over_cap) parts.push('<b>over the size cap</b>');
  host.innerHTML = parts.join(' · ') + '. <a href="#storage">Storage details</a>';
}

function addDays(ts, n) {
  const t = new Date(stamp(ts).replace(' ', 'T'));
  t.setDate(t.getDate() + n);
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}

function avoidedOf(day, kinds) {
  return kinds.reduce((s, k) => s + (day[k] || 0), 0);
}

function renderBill(d) {
  const days = d.per_day;
  const host = $('#bill-chart');
  const kinds = d.settings.saving_kinds;
  if (!days.length) { host.innerHTML = emptyLine('days'); return; }
  const f = frame(chartWidth('#bill-chart'), CHART_HEIGHT);
  const max = Math.max(...days.map(day => day.paid + avoidedOf(day, kinds))) || 1;
  const y = scaleY(f, max);
  const step = f.innerW / days.length;
  const barW = Math.max(8, step * 0.55);
  let svg = `<svg viewBox="0 0 ${f.W} ${f.H}" role="img" aria-label="Re-read tokens paid and avoided per day">${gridY(f, y, max, fmtM)}`;
  days.forEach((day, i) => {
    const x = f.pad.left + i * step + (step - barW) / 2;
    const avoided = avoidedOf(day, kinds);
    const share = day.paid + avoided ? avoided / (day.paid + avoided) : 0;
    const tip = `${day.day} · paid ${fmtM(day.paid)} · avoided ${fmtM(avoided)} · ${pct(share)} of the total`;
    const paidH = Math.max(0, f.bottom - y(day.paid));
    svg += `<g data-tip="${esc(tip)}">` + (avoided ? `<rect class="paid" x="${x}" y="${y(day.paid)}" width="${barW}" height="${paidH}"/>` : topBar('paid', x, y(day.paid), barW, paidH));
    svg += topBar('avoided', x, y(day.paid + avoided), barW, Math.max(0, y(day.paid) - y(day.paid + avoided) - 1));
    if (avoided) svg += `<text class="share" x="${x + barW / 2}" y="${y(day.paid + avoided) - 6}" text-anchor="middle">${pct(share)}</text>`;
    svg += `<text x="${x + barW / 2}" y="${f.H - f.pad.bottom + 18}" text-anchor="middle" class="lbl">${dayLabel(day.day, step)}</text></g>`;
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


function activityStrip(counts, days, install, top) {
  return `<span class="strip">${days.map(day => {
    const n = counts[day] || 0;
    const h = n ? Math.max(8, Math.round(n / top * 100)) : 0;
    const cls = install && day < install ? 'pre' : day === install ? 'on' : '';
    return `<i class="${cls}" title="${esc(day.slice(5).replace('-', '/'))}: ${n} fired"><b style="height:${h}%"></b></i>`;
  }).join('')}</span>`;
}

function renderKinds(d) {
  const rows = Object.entries(d.by_kind).filter(([k, v]) => v.count && !BOOKKEEPING_KINDS.includes(k)).sort((a, b) => b[1].saved - a[1].saved || b[1].count - a[1].count);
  const saving = rows.filter(([, v]) => v.saved);
  const signals = rows.filter(([, v]) => !v.saved);
  const totalSaved = saving.reduce((sum, [, v]) => sum + v.saved, 0) || 1;
  const days = d.per_day.map(p => p.day).slice(-KIND_STRIP_DAYS);
  const install = (d.since || {}).install_day;
  const md = day => day.slice(5).replace('-', '/');
  const perDay = {};
  d.actions.forEach(a => { const day = a.ts.slice(0, 10); (perDay[a.kind] = perDay[a.kind] || {})[day] = ((perDay[a.kind] || {})[day] || 0) + 1; });
  const peak = group => Math.max(1, ...group.flatMap(([k]) => days.map(day => (perDay[k] || {})[day] || 0)));
  const ruleOf = kind => (RULES.find(r => r.kinds.includes(kind)) || {}).key;
  const go = kind => { const rule = ruleOf(kind); return rule ? ` go" tabindex="0" data-rule="${rule}` : ''; };
  const pct = v => { const share = v.saved / totalSaved * 100; return share < 1 ? '<1%' : `${share.toFixed(0)}%`; };
  const card = (k, v, top, big) => `<div class="kcard${big ? ' big' : ''}${go(k)}" title="${esc(KIND_HELP[k] || '')}">
    <div class="kc-top">${pill(k)}<span class="chev">›</span></div>
    <div class="kc-fig">${big ? `<b>${fmtM(v.saved)}</b><span>saved · ${pct(v)}${est(k)}</span>` : `<b>${v.count}<small>×</small></b><span>fired</span>`}</div>
    ${activityStrip(perDay[k] || {}, days, install, top)}
    <p>${esc(KIND_SHORT[k] || KIND_HELP[k] || '')}${big ? `<span class="kc-meta">fired ${v.count}× · kept out ${fmtM(v.kept_out)}</span>` : ''}</p></div>`;
  const note = `<span class="kinds-note">fires per day, ${md(days[0])}–${md(days[days.length - 1])}${install ? ` · <i class="mark"></i> kiasi on ${md(install)}` : ''}</span>`;
  $('#kinds').innerHTML = `${saving.length ? `<h3 class="kinds-head"><span>Saved re-read tokens · ${fmtM(totalSaved)}${est('avoided')}</span>${note}</h3><div class="kcards two">${saving.map(([k, v]) => card(k, v, peak(saving), true)).join('')}</div>` : ''}
    ${signals.length ? `<h3 class="kinds-head">Counted, not credited${saving.length ? '' : note}</h3><div class="kcards four">${signals.map(([k, v]) => card(k, v, peak(signals), false)).join('')}</div>` : ''}`;
  const open = el => { location.hash = `#rules/${el.dataset.rule}`; };
  $('#kinds').querySelectorAll('.go').forEach(el => {
    el.addEventListener('click', () => open(el));
    el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(el); } });
  });
}


registerView('overview', d => { $('#est-legend').innerHTML = est('avoided'); $('#est-note').textContent = EST_NOTE; renderSince(d); renderStats(d); renderBill(d); renderKinds(d); renderMisses(d); renderRecall(d); renderStorage(d); renderStartupHint(d); });
})();
