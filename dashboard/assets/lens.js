(() => {
const root = document.querySelector('[data-view="overview"]');
const $ = s => root.querySelector(s);
const stepsPerDay = p => p.steps_per_day !== undefined ? p.steps_per_day : p.reread_per_turn && p.reread_per_day != null ? Math.round(p.reread_per_day / p.reread_per_turn) : null;

function change(before, after) {
  if (!before || after == null) return '–';
  const r = (after - before) / before;
  return `${r < 0 ? '−' : '+'}${Math.abs(Math.round(r * 100))}%`;
}

let lastData = null;
const DAY_MS = 86400000;
const WEEK_PAD = { top: 30, right: 28, bottom: 26, left: 58 };
const whenText = epoch => new Date(epoch * 1000).toLocaleString([], WHEN_OPTS);
const midnight = t => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
const dayStart = day => new Date(`${day}T00:00:00`).getTime();

function leadText(r) {
  if (!r) return ['Install the Kiasi status line to see when your weekly limit runs out.', 'Run /kiasi:limits setup in Claude Code. The days below are what you sent.', ''];
  const used = `${r.used}% of the weekly limit used`;
  if (r.state === 'runs_out') return [`At this pace your weekly limit runs out <em class="bad">${esc(whenText(r.run_out_at))}</em>, before the reset ${esc(whenText(r.resets_at))}.`, `${used} · ${r.burn_per_day}% a day · the next fix below buys the most time`, 'bad'];
  if (r.state === 'clear') return [`At this pace you reach the reset ${esc(whenText(r.resets_at))} with about <em class="good">${r.spare}% to spare</em>.`, `${used} · ${r.burn_per_day}% a day`, 'good'];
  const pace = Math.abs(r.gap) <= LIMIT_PACE_SLACK ? 'on pace' : `${Math.abs(r.gap)} points ${r.gap > 0 ? 'ahead of' : 'under'} pace`;
  return [`${r.used}% of your weekly limit is used, <em class="${r.gap > LIMIT_PACE_SLACK ? 'bad' : 'good'}">${pace}</em> for the reset ${esc(whenText(r.resets_at))}.`, 'the forecast appears after a few status line readings', ''];
}

function renderWeek(d, l) {
  const r = l && l.runway;
  const [lead, sub, tone] = leadText(r);
  $('#lead').innerHTML = lead; $('#lead').className = `lead ${tone}`;
  $('#lead-sub').textContent = sub;
  const now = Date.now();
  const t1 = r ? r.resets_at * 1000 : midnight(now) + DAY_MS;
  const w0 = t1 - 7 * DAY_MS;           // the limit window
  const t0 = midnight(w0);              // the axis starts at the midnight before it, so every day is whole
  const days = d.per_day.filter(day => dayStart(day.day) + DAY_MS > t0 && dayStart(day.day) < t1);
  const W = chartWidth('#week-chart'), H = r ? 320 : 190, pad = WEEK_PAD;
  const innerW = W - pad.left - pad.right;
  const x = t => pad.left + (t - t0) / (t1 - t0) * innerW;
  const bandTop = pad.top + 24, bandH = 96, bandBottom = bandTop + bandH;
  const yPct = v => bandBottom - Math.max(0, Math.min(100, v)) / 100 * bandH;
  const barTop = r ? bandBottom + 46 : pad.top + 24, barBottom = H - pad.bottom;
  const max = Math.max(...days.map(day => day.paid), 1);
  const yBar = v => barBottom - v / max * (barBottom - barTop);
  const rowLabel = (y, text, cls = 'wk-row') => `<text class="${cls}" x="${pad.left - 12}" y="${y}" text-anchor="end">${text}</text>`;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="The week: limit used and context sent per day">`;
  // day columns and labels
  const todayStart = midnight(now);
  if (todayStart >= t0 && todayStart < t1) svg += `<rect class="wk-today" x="${x(Math.max(todayStart, t0))}" y="${r ? bandBottom + 20 : pad.top}" width="${x(Math.min(todayStart + DAY_MS, t1)) - x(Math.max(todayStart, t0))}" height="${barBottom - (r ? bandBottom + 20 : pad.top)}"/>`;
  for (let t = midnight(t0 + DAY_MS); t < t1; t += DAY_MS) svg += `<line class="wk-tick" x1="${x(t)}" x2="${x(t)}" y1="${pad.top}" y2="${barBottom}"/>`;
  for (let t = midnight(t0); t < t1; t += DAY_MS) {
    const a = Math.max(t, t0), b = Math.min(t + DAY_MS, t1);
    if (b - a < DAY_MS * 0.3) continue;
    const today = midnight(now) === t;
    svg += `<text class="wk-day${today ? ' today' : ''}" x="${x((a + b) / 2)}" y="${pad.top - 10}" text-anchor="middle">${today ? 'today' : new Date(t).toLocaleDateString([], { weekday: 'short' })}</text>`;
  }
  // limit used: a cumulative line from the readings, projected at the current burn
  if (r) {
    const pts = (r.points || []).map(([t, u]) => [t * 1000, u]).filter(([t]) => t >= w0 && t <= now);
    const cur = [now, r.used];
    const line = [[w0, 0]].concat(pts.filter(([t]) => t > w0), [cur]);   // the window opens at zero at the previous reset
    const P = p => `${x(p[0]).toFixed(1)},${yPct(p[1]).toFixed(1)}`;
    const burnMs = (r.burn_per_day || 0) / DAY_MS;       // percent per millisecond
    let end = null;
    if (r.state === 'runs_out') end = [r.run_out_at * 1000, 100];
    else if (burnMs > 0) end = [t1, Math.min(100, r.used + burnMs * (t1 - now))];
    if (x(w0) > pad.left + 1) svg += `<rect class="wk-before" x="${pad.left}" y="${bandTop}" width="${x(w0) - pad.left}" height="${bandH}"/><text class="wk-sub" x="${x(w0) - 6}" y="${bandBottom - 6}" text-anchor="end">last window</text>`;
    svg += rowLabel(bandTop + 4, '100%', 'wk-row') + rowLabel(bandBottom, '0', 'wk-row') + rowLabel((bandTop + bandBottom) / 2 + 4, 'LIMIT')
      + `<line class="wk-ceiling" x1="${x(w0)}" x2="${x(t1)}" y1="${bandTop}" y2="${bandTop}"/>`
      + `<line class="wk-floor" x1="${x(w0)}" x2="${x(t1)}" y1="${bandBottom}" y2="${bandBottom}"/>`;
    if (line.length > 1) {
      const start = `${x(line[0][0]).toFixed(1)},${bandBottom}`;
      svg += `<path class="wk-area" d="M${start} L${line.map(P).join(' L')} L${x(cur[0]).toFixed(1)},${bandBottom} Z"/>`
        + `<path class="wk-line draw" pathLength="1" d="M${line.map(P).join(' L')}"/>`;
    }
    if (end) {
      svg += `<path class="wk-proj-line after" d="M${P(cur)} L${P(end)}"/>`;
      if (r.state === 'runs_out') {
        const rx = x(end[0]);
        const nearReset = rx > x(t1) - 130, inside = nearReset || W < 640;   // keep clear of the reset label
        svg += `<rect class="wk-over after" x="${rx}" y="${bandTop}" width="${Math.max(0, x(t1) - rx)}" height="${bandH}"/>`
          + `<line class="wk-runout after" x1="${rx}" x2="${rx}" y1="${bandTop - 6}" y2="${barBottom}"/>`
          + `<circle class="wk-runout-dot pop" cx="${rx}" cy="${bandTop}" r="4"/>`
          + `<text class="wk-runout-lbl" x="${rx + (nearReset ? -8 : 8)}" y="${inside ? bandTop + 16 : bandTop - 10}" text-anchor="${nearReset ? 'end' : 'start'}">runs out ${esc(whenText(r.run_out_at))}</text>`;
      } else if (r.state === 'clear') {
        svg += `<circle class="wk-end-dot" cx="${x(end[0])}" cy="${yPct(end[1])}" r="3.5"/>`
          + `<text class="wk-sub good" x="${x(t1) - 8}" y="${yPct(end[1]) - 10}" text-anchor="end">${r.spare}% to spare</text>`;
      }
    }
    const cx = x(cur[0]), cy = yPct(cur[1]);
    const leftSide = cx > x(t1) - 170;
    const tip = `${r.used}% of the weekly limit used${r.burn_per_day ? `, ${r.burn_per_day}% a day` : ''}${pts.length ? ` · ${pts.length} readings this window` : ''}`;
    svg += `<g data-tip="${esc(tip)}"><circle class="wk-now-dot pop" cx="${cx}" cy="${cy}" r="4.5"/>`
      + `<text class="wk-now-lbl" x="${cx + (leftSide ? -10 : 10)}" y="${cy + 4}" text-anchor="${leftSide ? 'end' : 'start'}">${r.used}% used${r.burn_per_day ? ` · ${r.burn_per_day}% a day` : ''}</text></g>`;
    svg += `<line class="wk-reset" x1="${x(t1)}" x2="${x(t1)}" y1="${pad.top}" y2="${barBottom}"/><text class="wk-sub" x="${x(t1) - 4}" y="${pad.top + 12}" text-anchor="end">reset ${esc(whenText(r.resets_at))}</text>`;
  }
  // sent bars
  svg += rowLabel(barBottom - 2, 'SENT');
  days.forEach((day, i) => {
    const a = Math.max(dayStart(day.day), t0), b = Math.min(dayStart(day.day) + DAY_MS, t1);
    const bx = x(a) + 6, bw = Math.max(4, x(b) - x(a) - 12), top = yBar(day.paid);
    const delay = `style="animation-delay:${i * 45}ms"`;
    const tip = `${day.day} · ${fmtM(day.paid)} sent · ${day.prompts || 0} prompts · ${fmtK(day.reread_per_prompt || 0)} per prompt · ${day.sessions || 0} sessions`;
    svg += `<g data-tip="${esc(tip)}"><rect class="hist grow${dayStart(day.day) === todayStart ? ' today' : ''}" ${delay} x="${bx}" y="${top}" width="${bw}" height="${Math.max(0, barBottom - top)}" rx="2"/><text class="lbl rise" ${delay} x="${bx + bw / 2}" y="${top - 6}" text-anchor="middle">${fmtM(day.paid)}</text></g>`;
  });
  // now
  if (now > t0 && now < t1) svg += `<line class="wk-now" x1="${x(now)}" x2="${x(now)}" y1="${pad.top}" y2="${barBottom}"/>`;
  svg += `<line class="wk-base" x1="${pad.left}" x2="${W - pad.right}" y1="${barBottom}" y2="${barBottom}"/></svg>`;
  $('#week-chart').innerHTML = svg;
  bindTips($('#week-chart'));
  renderFacts(d);
  const share = $('#month-share'), canShare = typeof openMonthPanel === 'function' && d.wrapped && d.wrapped.active_days > 0;
  share.hidden = !canShare;
  share.onclick = canShare ? () => openMonthPanel(d) : null;
}

function fact(label, value, sub, tip) {
  return `<div${tip ? ` title="${esc(tip)}"` : ''}><span class="hero-label">${label}</span><b>${value}</b><span class="fact-sub">${sub}</span></div>`;
}

function renderFacts(d) {
  const s = d.since || {}, b = s.before, a = s.after, t = d.totals, p = d.pace || {}, bb = d.bought_back;
  const kind = key => (d.by_kind || {})[key] || { count: 0, kept_out: 0 };
  const out = [];
  let since = '';
  if (b && a && s.factor != null) since = s.factor >= 1 ? `<em class="good">${s.factor}× less</em> since Kiasi` : `<em class="bad">${(1 / s.factor).toFixed(1)}× more</em> since Kiasi`;
  else if (b && a) since = `${fmtK(a.reread_per_turn)} per request now, ${fmtK(b.reread_per_turn)} before Kiasi`;
  else since = `${fmtK(t.reread_per_prompt)} of context per prompt`;
  if (bb && bb.days != null) {
    out.push(fact('Bought back', bb.days < 0.1 ? `<span data-count="${Math.round(bb.days * 240) / 10}">${Math.round(bb.days * 240) / 10}</span><small>hours</small>` : `<span data-count="${bb.days}">${bb.days}</span><small>days</small>`, `of the weekly limit · ${since}`,
      `Context per request compared with before Kiasi. ${fmtM(bb.kept_out)} tokens kept out, ${fmtM(bb.saved)} of re-reads avoided, at ${fmtM(bb.rate)} a day. Every token kept out would have been re-sent on every later request of its session.`));
  } else {
    out.push(fact('Kept out', `${fmtM(kind('cap').kept_out + kind('pruned').kept_out)}<small>tokens</small>`, since, `${kind('cap').count} outputs capped, ${t.pruned} compactions pruned`));
  }
  if (p.rate != null && p.prior_rate) {
    const r = (p.rate - p.prior_rate) / p.prior_rate, pct = Math.abs(Math.round(r * 100));
    const value = pct === 0 ? 'Level' : `<em class="${r < 0 ? 'good' : 'bad'}">${pct}% ${r < 0 ? 'less' : 'more'}</em>`;
    out.push(fact('This week', value, `${fmtM(p.rate)} a day against ${fmtM(p.prior_rate)} last week`, p.note || ''));
  }
  else if (p.rate != null) out.push(fact('This week', fmtM(p.rate), 'a day · no earlier week to compare', p.note || ''));
  const top = ((d.attribution || {}).kinds || [])[0];
  if (top) {
    const [name] = plainKind(top.kind);
    out.push(fact('Next fix', `<span class="fact-text">${esc(name)}</span>`, `${Math.round(top.share * 100)}% of the context sent · <a href="#fix-block">how</a>`, ''));
  }
  $('#facts').innerHTML = out.join('');
  $('#facts').querySelectorAll('[data-count]').forEach(countUp);
}

function countUp(el) {
  const target = parseFloat(el.dataset.count), decimals = (el.dataset.count.split('.')[1] || '').length;
  if (!isFinite(target) || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const t0 = performance.now(), ms = COUNT_UP_MS;
  const tick = now => {
    const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3);
    el.textContent = (target * e).toFixed(decimals);
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function renderRunway(l) {
  if (lastData) renderWeek(lastData, l);
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
  const fresh = new Date(Date.now() - (SPIKE_NOTE_DAYS - 1) * DAY_MS).toISOString().slice(0, 10);
  const rows = ((d.spikes || {}).rows || []).filter(r => String(r.day || '').slice(0, 10) >= fresh);
  const host = $('#spike-note');
  host.hidden = !rows.length;
  if (host.hidden) return;
  const top = rows[0];
  const more = rows.length > 1 ? ` and ${rows.length - 1} more` : '';
  host.innerHTML = `One session on ${esc(String(top.day || '').slice(5, 10))} sent ${fmtM(top.reread)} of context across ${top.prompts} prompt${top.prompts === 1 ? '' : 's'}, ${top.factor}× the usual${more}. <a href="#sessions">See it in Sessions →</a>`;
}

registerView('overview', d => { lastData = d; renderWeek(d, window.limitsReading); renderSpikeNote(d); renderFixes(d); });
})();
