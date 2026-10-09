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

const EFFORT_ORDER = ['low', 'medium', 'high', 'xhigh', 'max'];
const THINKING_MIN_CHARS = 1000;  // below this the per-prompt figure rounds to 0 k and says nothing

function offenderFix(tool, n) {
  if (n > 1) return tool === 'Read' ? `Read ${n} times; read it once and keep notes.` : `Fetched ${n} times; save it once and read the file.`;
  if (tool === 'Read') return 'Read it by section.';
  if (tool === 'WebFetch') return 'Save the page and read it by section.';
  return 'Cap the tool or ask for less.';
}

// The short tag shown on the row; the full sentence sits in its tooltip and the intro says it once.
function offenderTag(tool, n) {
  if (n > 1) return 'read once';
  if (tool === 'Read') return 'by section';
  if (tool === 'WebFetch') return 'save it';
  return 'cap it';
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
    + `<span class="fx-do tag" title="${esc(offenderFix(g.tool, g.n))}">${esc(offenderTag(g.tool, g.n))}</span><span class="chev">›</span></a>`).join('')
    : `<p class="empty">No tool result over ${fmtK(b.settings.big_output_chars / CHARS_PER_TOKEN)} tokens this week.</p>`;
}

registerView('budget', b => { renderChart(b); renderFixes(b); }, 'budget');
})();
