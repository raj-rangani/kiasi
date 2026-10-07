(() => {
const root = document.querySelector('[data-view="overview"]');
const $ = s => root.querySelector(s);
function renderSince(d) {
  const s = d.since || {};
  const host = $('#since');
  const b = s.before, a = s.after;
  const day = s.install_day ? stamp(s.install_day).slice(0, 10) : null;
  const inPanels = renderTrend(s, day);
  if (!day) {
    host.innerHTML = `<div class="since-empty">No Kiasi actions logged yet. Work in a Claude Code session or two and this block will compare the days before the install with the days after.</div>`;
    return;
  }
  if (!b || !a) {
    host.innerHTML = `<div class="since-empty">Kiasi has been on since ${day}. ${!b ? 'No transcripts from before that day were found, so there is no baseline to compare with.' : 'The comparison appears after the first full day with Kiasi on.'}</div>`;
    return;
  }
  const count = n => n == null ? '–' : n.toLocaleString();
  const high = fmtK(d.settings.warn_tokens);
  const rows = [
    ['Re-read tokens per day', fmtM(b.reread_per_day), a.reread_per_day == null ? '–' : fmtM(a.reread_per_day), change(b.reread_per_day, a.reread_per_day), true],
    ['Steps per day', count(stepsPerDay(b)), count(stepsPerDay(a)), change(stepsPerDay(b), stepsPerDay(a)), false],
    ['Context re-sent per step', fmtK(b.reread_per_turn), fmtK(a.reread_per_turn), change(b.reread_per_turn, a.reread_per_turn), true],
    ['Mean context per main-session step', fmtK(b.mean_context), fmtK(a.mean_context), change(b.mean_context, a.mean_context), true],
    ...(b.median_context != null && a.median_context != null ? [['Median context per main-session step (p90 in note)', fmtK(b.median_context), fmtK(a.median_context), change(b.median_context, a.median_context), true]] : []),
    [`Main-session steps over ${high}`, pct(b.high_share), pct(a.high_share), change(b.high_share, a.high_share), true],
    ['Steps counted', count(b.turns), count(a.turns), '', false],
  ].slice(inPanels ? 3 : 0);
  host.innerHTML = `<table class="since-table"><thead><tr><th>Kiasi on since ${day}</th><th class="num">before · ${b.days} days</th><th class="num">after · ${a.days} days + today</th><th class="num">change</th></tr></thead>
      <tbody>${rows.map(([k, x, y, c, judged]) => `<tr><td>${k}</td><td class="num">${x}</td><td class="num"><b>${y}</b></td><td class="num ${judged && c !== '–' ? (c.startsWith('−') ? 'good' : 'bad') : ''}">${c}</td></tr>`).join('')}</tbody></table>`;
}

function fillDays(series) {
  const by = Object.fromEntries(series.map(p => [p.day, p]));
  const out = [];
  const end = Date.parse(`${series[series.length - 1].day}T00:00:00Z`);
  for (let t = Date.parse(`${series[0].day}T00:00:00Z`); t <= end; t += 86400000) {
    const day = new Date(t).toISOString().slice(0, 10);
    out.push(by[day] || { day, reread: 0, steps: 0 });
  }
  return out;
}

const stepsPerDay = p => p.steps_per_day !== undefined ? p.steps_per_day : p.reread_per_turn && p.reread_per_day != null ? Math.round(p.reread_per_day / p.reread_per_turn) : null;

function trendCard(m, days, install) {
  const { width: W, height: H } = TREND;
  const pad = { top: 16, right: 4, bottom: 16, left: 4 };
  const vals = days.map(m.value);
  const max = Math.max(...vals, m.before || 0, m.after || 0) || 1;
  const bottom = H - pad.bottom;
  const slot = (W - pad.left - pad.right) / days.length;
  const bw = Math.max(1.5, slot * 0.7);
  const y = v => (bottom - v / max * (bottom - pad.top)).toFixed(1);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(m.title)}, one bar per day">`;
  days.forEach((p, i) => {
    if (!vals[i]) return;
    const h = Math.max(1, vals[i] / max * (bottom - pad.top));
    svg += `<rect class="${install && p.day < install ? 'pre' : 'post'}" data-tip="${esc(`${p.day} · ${m.fmt(vals[i])}`)}" x="${(pad.left + i * slot + (slot - bw) / 2).toFixed(1)}" y="${(bottom - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"/>`;
  });
  const idx = install ? days.findIndex(p => p.day >= install) : -1;
  if (idx > 0) {
    const mx = pad.left + idx * slot;
    const right = mx > W * 0.6;
    const mean = (v, x1, x2) => v == null ? '' : ['halo', 'mean'].map(cls => `<line class="${cls}" x1="${x1.toFixed(1)}" x2="${x2.toFixed(1)}" y1="${y(v)}" y2="${y(v)}"/>`).join('');
    svg += mean(m.before, pad.left, mx) + mean(m.after, mx, W - pad.right);
    svg += `<line class="marker" x1="${mx.toFixed(1)}" x2="${mx.toFixed(1)}" y1="2" y2="${bottom}"/><text class="marker-lbl" x="${(mx + (right ? -4 : 4)).toFixed(1)}" y="10"${right ? ' text-anchor="end"' : ''}>Kiasi on ${esc(install.slice(5))}</text>`;
  }
  svg += `<line class="axis" x1="${pad.left}" x2="${W - pad.right}" y1="${bottom}" y2="${bottom}"/><text x="${pad.left}" y="${H - 3}">${esc(days[0].day.slice(5))}</text><text x="${W - pad.right}" y="${H - 3}" text-anchor="end">${esc(days[days.length - 1].day.slice(5))}</text></svg>`;
  const c = m.before == null ? '' : change(m.before, m.after);
  const nums = m.before == null ? '' : `<p class="trend-nums"><span class="was">${esc(m.fmt(m.before))}</span><i>→</i><b>${m.after == null ? '–' : esc(m.fmt(m.after))}</b><em class="${m.judged && c !== '–' ? (c.startsWith('−') ? 'good' : 'bad') : ''}">${c}</em></p>`;
  return `<div class="trend-card"><h3><span>${esc(m.title)}</span><span>peak ${esc(m.fmt(Math.max(...vals)))}</span></h3>${nums}${svg}</div>`;
}

function renderTrend(s, install) {
  const host = $('#trend');
  const series = (s.series || []).slice(-TREND.maxDays);
  host.hidden = series.length < 2;
  if (host.hidden) return false;
  const days = fillDays(series);
  const b = s.before, a = s.after;
  const both = Boolean(install && b && a);
  const of = (p, pick) => both ? pick(p) : null;
  const metrics = [
    { title: 'Re-read tokens per day', value: p => p.reread, fmt: fmtM, before: of(b, p => p.reread_per_day), after: of(a, p => p.reread_per_day), judged: true },
    { title: 'Steps per day', value: p => p.steps, fmt: v => Math.round(v).toLocaleString(), before: of(b, stepsPerDay), after: of(a, stepsPerDay), judged: false },
    { title: 'Context re-sent per step', value: p => p.steps ? p.reread / p.steps : 0, fmt: fmtK, before: of(b, p => p.reread_per_turn), after: of(a, p => p.reread_per_turn), judged: true },
  ];
  host.innerHTML = `<div class="trend">${metrics.map(m => trendCard(m, days, install || '')).join('')}</div>
    <div class="legend">${install ? `<span><i class="pre"></i>before Kiasi${both ? ` · ${b.days} days` : ''}</span>` : ''}<span><i class="post"></i>${install ? `Kiasi on${both ? ` · ${a.days} days + today` : ''}` : 'measured'}</span>${both ? '<span><i class="line mean"></i>mean of each period</span>' : ''}<span>one bar per day, each chart on its own scale from zero</span></div>`;
  bindTips(host);
  return both;
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
  return { today: today[key] ?? null, day: today.day || '', mean };
}

function renderStats(d) {
  const t = d.totals;
  const c = d.cache;
  const span = `${d.days || d.per_day.length} days`;
  const series = key => d.per_day.filter(day => day[key] !== null).map(day => day[key] || 0);
  const versus = row => row.mean && row.today == null ? `${esc(row.day.slice(5))}: no new session to compare` : row.mean ? `${esc(row.day.slice(5))}: ${fmtK(row.today)}, <em class="${row.today > row.mean ? 'bad' : 'good'}">${change(row.mean, row.today)}</em> against the ${fmtK(row.mean)} mean of earlier days` : 'no earlier day to compare with';
  const rates = c ? c.hit_days.map(day => day.rate) : [];
  const share = v => `${(v * 100).toFixed(1)}%`;
  const range = rates.length > 1 ? `${share(Math.min(...rates))} to ${share(Math.max(...rates))} per day · ` : '';
  const tiles = [
    ['Re-read tokens paid', fmtM(t.paid), series('paid'), true, `${t.prompts.toLocaleString()} prompts · ${t.steps.toLocaleString()} steps · ${t.mean_steps} per prompt · ${t.sessions} sessions`],
    ['Re-read tokens per prompt', fmtK(t.reread_per_prompt), series('reread_per_prompt'), true, versus(todayVersus(d, 'reread_per_prompt'))],
    ['First-request context per new session', fmtK(t.startup_mean), series('startup'), true, versus(todayVersus(d, 'startup'))],
    ['Cache hit rate', c && c.hit_rate != null ? share(c.hit_rate) : '–', rates, false, c ? `${range}${c.avoidable} avoidable misses of ${c.misses}${c.prior_avoidable == null ? '' : ` · ${c.prior_avoidable} the week before`}` : ''],
  ];
  $('#stats').innerHTML = tiles.map(([label, big, values, fromZero, note]) => `<div class="kpi"><span class="kpi-label">${label} · ${span}</span><b>${big}${values.length > 1 ? sparkline(values, fromZero) : ''}</b><span class="kpi-note">${note}</span></div>`).join('');
}

function renderDid(d) {
  const t = d.totals;
  const all = d.all_time;
  const kind = key => (d.by_kind || {})[key] || { count: 0, kept_out: 0 };
  const span = `${d.days || d.per_day.length} days`;
  const recall = d.recall || [];
  const cuts = recall.reduce((sum, row) => sum + row.cuts, 0);
  const back = recall.reduce((sum, row) => sum + row.recalled, 0);
  const rows = [
    ['Tool outputs capped', span, String(kind('cap').count), `${fmtM(kind('cap').kept_out)} tokens cut from the outputs${cuts ? ` · ${back} of ${cuts} cuts read back (${pct(back / cuts)})` : ''}`],
    ['Compactions pruned', span, `${t.pruned} of ${t.pruned + t.summaries}`, `${fmtM(kind('pruned').kept_out)} tokens removed by pruning · ${t.summaries} fell back to the built-in summary`],
    ['Turn pauses', span, String(t.stops), t.stops ? `${t.resumed || 0} resumed · ${t.stops_complied} complied · mean ${t.mean_steps_after_stop} steps after` : ''],
    ['Calls routed to the sandbox', span, String(kind('routed').count), `${kind('route_retry').count} repeated and let through`],
    ['Unchanged re-reads skipped', span, String(t.reads_skipped || 0), `${t.reads_retried || 0} repeated and let through`],
    ['Tokens cut or pruned', `since ${esc((all.first_day || '').slice(5) || 'today')}`, fmtM(all.kept_out), `${all.caps} caps and every pruned compaction over ${all.days} days`],
  ];
  $('#did').innerHTML = `<table class="since-table"><thead><tr><th>counted</th><th>period</th><th class="num">count</th><th>detail</th></tr></thead>
    <tbody>${rows.map(([k, when, v, note]) => `<tr><td>${k}</td><td class="when">${when}</td><td class="num"><b>${v}</b></td><td class="note">${note}</td></tr>`).join('')}</tbody></table>`;
}

function sparkline(values, fromZero) {
  const { width, height } = MISS_SPARK;
  const lo = fromZero ? 0 : Math.min(...values), hi = Math.max(...values);
  const x = i => values.length > 1 ? (i * width / (values.length - 1)).toFixed(1) : width / 2;
  const y = v => (hi > lo ? height - 2 - (v - lo) / (hi - lo) * (height - 4) : height / 2).toFixed(1);
  const points = values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true"><polygon points="${x(0)},${height} ${points} ${x(values.length - 1)},${height}"/><polyline points="${points}"/><circle cx="${x(values.length - 1)}" cy="${y(values[values.length - 1])}" r="2"/></svg>`;
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
  document.querySelector('#cache-tiles').innerHTML = c ? cacheTiles(c) : '';
  document.querySelector('#cache-causes').innerHTML = c && c.misses ? cacheCauses(c) : emptyLine('cache misses');
}

function recallVerdict(share) {
  if (share < RECALL_LOW) return 'could be tighter';
  if (share > RECALL_HIGH) return 'cuts too much';
  return 'about right';
}

function renderRecall(d) {
  const rows = d.recall || [];
  const host = document.querySelector('#recall');
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
    if (step >= NARROW_STEP_PX * 2) svg += `<text x="${mid}" y="${f.H - f.pad.bottom + 33}" text-anchor="middle" class="lbl sub">${day.prompts || 0} prompts</text>`;
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


const KIND_ADVICE = {
  'MCP tool schemas': (chars, p) => `MCP servers load ${Math.round(chars / 4000)} k of tool schemas; disable the ones this project does not use in /mcp`,
  'CLAUDE.md files': chars => `CLAUDE.md is ${Math.round(chars / 4000)} k; sections that only matter sometimes belong in skills`,
  'plugins and skills': chars => `plugins and skills list ${Math.round(chars / 4000)} k; disable plugins this project does not use`,
  'agents list': chars => `the agents list is ${Math.round(chars / 4000)} k; remove agents this project does not use`,
};

function renderFloor(d) {
  const t = d.totals, w = d.window || {}, lines = [];
  if (t.context_median != null) lines.push(`<p>Context per main-session step: mean ${fmtK(t.context_mean)} · median ${fmtK(t.context_median)} · p90 ${fmtK(t.context_p90)}.</p>`);
  lines.push(`<p>Auto-compact window in effect: ${w.tokens ? fmtK(w.tokens) : `not set (the model's own window; 200 k assumed)`}.${t.runaway_sessions ? ` <b class="warn">${t.runaway_sessions} session${t.runaway_sessions > 1 ? 's' : ''} past 200 k in the last ${d.days} days.</b>` : ''}</p>`);
  if (w.recommend) lines.push(`<p class="hist-note">Recommended: set <code>${esc(w.env)}=200000</code> under <code>env</code> in team settings to cap runaway contexts.</p>`);
  const projects = Object.entries(d.prefix || {}).sort((a, b) => b[1].floor_tokens - a[1].floor_tokens);
  const table = projects.length ? `<table class="since-table"><thead><tr><th>project</th><th class="num">floor</th><th>largest pieces</th><th>suggestion</th></tr></thead><tbody>${projects.map(([name, p]) => {
    const sums = {};
    p.pieces.filter(x => x.kind !== 'other').forEach(x => { sums[x.kind] = (sums[x.kind] || 0) + x.chars; });
    const other = p.floor_tokens * CHARS_PER_TOKEN - Object.values(sums).reduce((t, c) => t + c, 0);
    const top = Object.entries(sums).sort((a, b) => b[1] - a[1]);
    const shown = top.map(([k, c]) => [k === 'MCP tool schemas' ? 'MCP tool schemas (measured from the tool listing, not the schemas)' : k, c]);
    if (other > 0) shown.push(['other (floor minus measured pieces)', other]);
    shown.sort((a, b) => b[1] - a[1]);
    const tip = top.filter(([k]) => KIND_ADVICE[k] && sums[k] >= 8000).map(([k, c]) => KIND_ADVICE[k](c))[0] || '';
    return `<tr><td>${esc(name.slice(0, 40))}</td><td class="num">${fmtK(p.floor_tokens)}<br><span class="hist-note">${p.sessions} session${p.sessions > 1 ? 's' : ''}</span></td><td>${shown.slice(0, 4).map(([k, c]) => `${esc(k)} ${Math.round(c / CHARS_PER_TOKEN / 1000 * 10) / 10} k`).join('<br>') || '–'}</td><td>${esc(tip)}</td></tr>`;
  }).join('')}</tbody></table><p class="hist-note">Piece sizes are the visible attachments of a project's latest session, in tokens at 4 chars per token; other is the floor minus the measured pieces.</p>` : '';
  $('#floor').innerHTML = lines.join('') + table;
}

registerView('overview', d => { renderStats(d); renderBill(d); renderStartupHint(d); renderSince(d); renderDid(d); renderFloor(d); renderStorage(d); });
window.renderTuning = d => { renderMisses(d); renderRecall(d); };
})();
