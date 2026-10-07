(() => {
const root = document.querySelector('[data-view="budget"]');
const $ = s => root.querySelector(s);
const reread = x => (x && x.cache_read_input_tokens) || 0;
const dayLabel = day => day.slice(5).replace('-', '/');

function renderChart(b) {
  const host = $('#levers');
  const days = b.per_day;
  if (!days.length) { host.innerHTML = emptyLine('transcripts'); return; }
  const t = b.totals;
  const W = Math.max(300, host.clientWidth || 900);
  const narrow = W < 600;
  const L = narrow ? 0 : 190, R = narrow ? 0 : 70, H = narrow ? 96 : 120;
  const head = narrow ? 30 : 0, top = 10, base = top + head + H;
  const plot = W - L - R, bw = plot / days.length;
  const total = fmtM(reread(t.main) + reread(t.sub));
  const vmax = Math.max(...days.map(d => reread(d.main) + reread(d.sub))) || 1;
  let svg = narrow
    ? `<text class="mx-name" x="0" y="${top + 12}">re-read</text><text class="mx-total sm" text-anchor="end" x="${W}" y="${top + 16}">${total}</text>`
    : `<text class="mx-name" x="0" y="${top + 14}">re-read</text><text class="mx-total" x="0" y="${top + 46}">${total}</text><text class="mx-sub" x="0" y="${top + 64}">tokens sent again</text>`;
  svg += `<line class="mx-base" x1="${L}" x2="${L + plot}" y1="${base}" y2="${base}"/>`;
  days.forEach((d, j) => {
    const main = reread(d.main), sub = reread(d.sub);
    const x = L + j * bw + bw * 0.22, w = bw * 0.56, h = (main + sub) / vmax * H, sh = sub / vmax * H;
    const tip = `${d.day} · re-read ${fmtM(main + sub)} (subagents ${fmtM(sub)})`;
    svg += `<g data-tip="${esc(tip)}"><rect class="mx-hit" x="${(L + j * bw).toFixed(1)}" y="${top + head}" width="${bw.toFixed(1)}" height="${H}"/>`
      + topBar('mx-bar', x, base - h, w, h)
      + (main ? `<rect class="mx-subbar" x="${x.toFixed(1)}" y="${(base - sh).toFixed(1)}" width="${w.toFixed(1)}" height="${sh.toFixed(1)}"/>` : topBar('mx-subbar', x, base - sh, w, sh)) + '</g>';
  });
  days.forEach((d, j) => {
    const on = d.day === b.install_day;
    svg += `<text class="mx-day${on ? ' on' : ''}" x="${(L + j * bw + bw / 2).toFixed(1)}" y="${base + 22}" text-anchor="middle">${bw < NARROW_STEP_PX ? d.day.slice(8) : dayLabel(d.day)}</text>`;
    if (on) {
      const x = L + j * bw;
      svg += `<line class="mx-on" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${top + head - 6}" y2="${base + 6}"/><text class="mx-onlab" x="${(x + 6).toFixed(1)}" y="${top + head + 2}">Kiasi on</text>`;
    }
  });
  host.innerHTML = `<svg class="mx" viewBox="0 0 ${W} ${base + 32}" role="img" aria-label="Re-read tokens per day">${svg}</svg>`
    + '<div class="legend"><span><i></i>main sessions</span><span><i class="sub"></i>subagents</span></div>';
  bindTips(host);
}

function renderContext(b) {
  const days = b.per_day.filter(d => d.context);
  if (days.length < 2) return '';
  const W = Math.max(300, ($('#levers').clientWidth || 900)), H = 150;
  const f = frame(W, H, PAD);
  const max = Math.max(...days.map(d => d.context.p90)) || 1;
  const y = scaleY(f, max), x = j => f.pad.left + j / (days.length - 1) * f.innerW;
  const line = (cls, key) => `<path class="${cls}" d="${days.map((d, j) => `${j ? 'L' : 'M'}${x(j).toFixed(1)},${y(d.context[key]).toFixed(1)}`).join(' ')}"/>`;
  const dots = days.map((d, j) => `<circle class="mx-hit-dot" cx="${x(j).toFixed(1)}" cy="${y(d.context.p90).toFixed(1)}" r="6" data-tip="${esc(`${d.day} · median ${fmtK(d.context.median)} · p90 ${fmtK(d.context.p90)}`)}"/>`).join('');
  return `<h3 class="sub">Context per step, by day</h3><div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Median and p90 context per day">${gridY(f, y, max, fmtK)}${line('mx-p90', 'p90')}${line('mx-med', 'median')}${dots}`
    + `<text x="${f.pad.left}" y="${H - f.pad.bottom + 18}" class="lbl">${dayLabel(days[0].day)}</text><text x="${W - f.pad.right}" y="${H - f.pad.bottom + 18}" text-anchor="end" class="lbl">${dayLabel(days[days.length - 1].day)}</text>${baseline(f)}</svg></div>`
    + '<div class="legend"><span><i class="line"></i>median</span><span><i class="sub"></i>p90</span></div>';
}

const EFFORT_ORDER = ['low', 'medium', 'high', 'xhigh', 'max'];
const THINKING_MIN_CHARS = 1000;  // below this the per-prompt figure rounds to 0 k and says nothing

function effortLine(c) {
  if (!c) return '';
  if (!c.ready) return `<p>collecting: ${c.prompts} of ${c.min_prompts} prompts at ${esc(c.level)}</p>`;
  return `<p>${esc(c.high.name)} uses ${c.ratio}× the output per prompt of ${esc(c.low.name)}; on this week's output that is ${fmtM(c.extra_tokens)} more.</p>`;
}

function offenderFix(tool, n) {
  if (n > 1) return tool === 'Read' ? `Read ${n} times; read it once and keep notes.` : `Fetched ${n} times; save it once and read the file.`;
  if (tool === 'Read') return 'Read it by section.';
  if (tool === 'WebFetch') return 'Save the page and read it by section.';
  return 'Cap the tool or ask for less.';
}

function toolName(tool) {
  return tool.replace(/^mcp__/, '').split('__').join(' · ');
}

function tail(label) {
  const text = label.replace(/^https?:\/\//, '');
  return text.length > OFFENDER_LABEL_CHARS ? `…${text.slice(-(OFFENDER_LABEL_CHARS - 1))}` : text;
}

function renderFixes(b) {
  const groups = new Map();
  (b.big_outputs || []).forEach(o => {
    const key = `${o.tool}\u0000${o.label || o.tool}`;
    const g = groups.get(key) || { tool: o.tool, label: o.label || toolName(o.tool), chars: 0, n: 0, session: o.session };
    g.chars += o.chars;
    g.n += 1;
    groups.set(key, g);
  });
  const rows = [...groups.values()].sort((x, y) => y.chars - x.chars).slice(0, OFFENDER_ROWS);
  $('#fixes').innerHTML = rows.length ? rows.map(g =>
    `<a class="fx go" href="#sessions/${esc(g.session)}" title="${esc(g.label)}"><span class="fx-size">${fmtK(g.chars / CHARS_PER_TOKEN)}<small>tokens</small></span>`
    + `<span class="fx-what"><b>${esc(tail(g.label))}</b><small>${g.label === toolName(g.tool) ? '' : `${esc(toolName(g.tool))} · `}session ${esc(g.session)}</small></span>`
    + `<span class="fx-n">${g.n > 1 ? `×${g.n}` : ''}</span>`
    + `<span class="fx-do">${esc(offenderFix(g.tool, g.n))}</span><span class="chev">›</span></a>`).join('')
    : `<p class="empty">No tool result over ${fmtK(b.settings.big_output_chars / CHARS_PER_TOKEN)} tokens this week.</p>`;
}

function table(head, rows) {
  return `<table><thead><tr>${head.map((h, i) => `<th${i ? ' class="num"' : ''}>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td${i ? ' class="num"' : ''}>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

function renderNotes(b) {
  let host = $('#cache-notes');
  if (!host) {
    host = document.createElement('section');
    host.className = 'sheet';
    host.id = 'cache-notes';
    root.appendChild(host);
  }
  const t = b.totals, c = t.context || { mean: t.mean_context, median: 0, p90: 0 };
  const lines = [`<p>Context per step: mean ${fmtK(c.mean)}, median ${fmtK(c.median)}, p90 ${fmtK(c.p90)}.</p>`];
  if (t.cache_gaps_5_60 > 0 && t.cache_cold_after_gap > 0) {
    lines.push(`<p>${t.cache_gaps_5_60} breaks of 5–60 min this week rewrote about ${fmtM(t.cache_gap_rewrite_tokens)} tokens. `
      + (b.settings.cache_ttl_1h ? 'The 1-hour cache is already set in this environment.' : 'If this is an API key, set CLAUDE_CODE_PROMPT_CACHE_TTL=1h.') + '</p>');
  }
  if (t.model_switches > 0 || t.effort_switches > 0) {
    const n = t.model_switches + t.effort_switches, cost = t.model_switch_rewrite_tokens + t.effort_switch_rewrite_tokens;
    lines.push(`<p>${n} model or effort switches rewrote about ${fmtK(cost)}. A model or effort change invalidates the cached messages.</p>`);
  }
  const e = b.effort || { levels: [], models: [] };
  const rows = (e.known ? [...e.levels].sort((a, b) => EFFORT_ORDER.indexOf(a.name) - EFFORT_ORDER.indexOf(b.name)) : e.models);
  const out = rows.length ? table([e.known ? 'effort' : 'model', 'output per prompt', 'prompts'], rows.map(r => [esc(r.name), String(r.per_prompt), String(r.prompts)])) : '';
  const thinking = t.thinking_per_prompt >= THINKING_MIN_CHARS ? `<p>Thinking text: about ${fmtK(t.thinking_per_prompt)} characters per prompt.</p>`
    : '<p>Thinking text is not kept in transcripts (signature only), so it is not measured here.</p>';
  host.innerHTML = '<div class="sheet-head"><h2><span class="num">03</span>Cache and output</h2>'
    + `<p>Output is ${fmtM(t.output_tokens)} tokens, ${t.output_per_prompt} per prompt.</p></div>` + lines.join('') + renderContext(b) + out + effortLine(e.comparison) + thinking;
  bindTips(host);
}

registerView('budget', b => { renderChart(b); renderFixes(b); renderNotes(b); }, 'budget');
})();
