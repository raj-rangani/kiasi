// The Month view: the last 30 days on one card, to copy as text or save as an SVG image.
(() => {
const fmtBig = n => n >= 1e9 ? `${(n / 1e9).toFixed(2)} B` : fmtM(n);
const toolName = t => (t || '').replace(/^mcp__([^_]+(?:_[^_]+)*)__/, '$1 ').replace(/_/g, ' ');
const F = { serif: 'Georgia, "Times New Roman", serif', mono: 'ui-monospace, Menlo, Consolas, monospace' };

function dayText(day) {
  return day ? new Date(`${day}T12:00:00`).toLocaleDateString([], { day: 'numeric', month: 'short' }) : '';
}

function rows(w) {
  const out = [];
  if (w.bought_back_days != null) out.push(['Bought back', w.bought_back_days < 0.1 ? `${Math.round(w.bought_back_days * 240) / 10} hours` : `${w.bought_back_days} days`, 'of the weekly limit']);
  out.push(['Kept out', `${fmtM(w.kept_out)} tokens`, `${fmtM(w.saved)} of re-reads avoided`]);
  out.push(['Context sent', fmtBig(w.sent), `${w.prompts.toLocaleString()} prompts on ${w.active_days} days`]);
  if (w.busiest) out.push(['Busiest day', dayText(w.busiest.day), `${fmtM(w.busiest.sent)} sent`]);
  if (w.heaviest) out.push(['Heaviest session', (w.heaviest.project || '').replace(/^-+/, '').split('-').slice(-2).join('-') || w.heaviest.short, `${fmtM(w.heaviest.bill)} re-read, ${dayText(w.heaviest.day)}`]);
  if (w.top_tool && w.top_tool.count) out.push(['Most cut', toolName(w.top_tool.tool), `${w.top_tool.count} outputs, ${fmtM(w.top_tool.kept_out)} kept out`]);
  return out;
}

function headline(w) {
  if (w.factor == null) return ['Kiasi on', 'the comparison with before Kiasi appears after its first full day'];
  return w.factor >= 1 ? [`${w.factor}× less`, 'context per request than before Kiasi'] : [`${(1 / w.factor).toFixed(1)}× more`, 'context per request than before Kiasi'];
}

function span(w) {
  const last = (w.last_day || new Date().toISOString().slice(0, 10));
  return `${dayText(w.first_day)} – ${dayText(last)}`;
}

function cardHtml(w) {
  const [big, sub] = headline(w);
  return `<div class="wrap-head"><span class="hero-label">Kiasi · last ${w.days} days</span><span class="hero-label">${esc(span(w))}</span></div>`
    + `<div class="wrap-big"><b>${esc(big)}</b><span>${esc(sub)}</span></div>`
    + `<div class="wrap-grid">${rows(w).map(([k, v, n]) => `<div><span class="hero-label">${esc(k)}</span><b>${esc(v)}</b><small>${esc(n)}</small></div>`).join('')}</div>`;
}

function cardText(w) {
  const [big, sub] = headline(w);
  return [`Kiasi, last ${w.days} days (${span(w)})`, `${big} ${sub}`, ...rows(w).map(([k, v, n]) => `${k}: ${v}, ${n}`)].join('\n');
}

function cardSvg(w) {
  const dark = document.documentElement.dataset.theme === 'dark' || (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
  const c = dark ? { paper: '#17140f', ink: '#f1ebe0', muted: '#9a9080', rule: '#3a342b', ok: '#7fbf8f' } : { paper: '#fbf8f1', ink: '#1a1612', muted: '#6f675c', rule: '#ddd6c8', ok: '#2e7d4f' };
  const [big, sub] = headline(w);
  const items = rows(w);
  const cols = 3, colW = 240, top = 210, rowH = 92;
  const h = top + Math.ceil(items.length / cols) * rowH + 36;
  const t = (x, y, txt, size, fill, family, extra = '') => `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-family='${family}'${extra}>${esc(txt)}</text>`;
  const cells = items.map(([k, v, n], i) => {
    const x = 40 + (i % cols) * colW, y = top + Math.floor(i / cols) * rowH;
    return t(x, y, k.toUpperCase(), 11, c.muted, F.mono, ' letter-spacing="1"') + t(x, y + 32, v, 24, c.ink, F.serif) + t(x, y + 54, n, 12.5, c.muted, F.serif);
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="${h}" viewBox="0 0 800 ${h}">`
    + `<rect width="800" height="${h}" rx="18" fill="${c.paper}" stroke="${c.rule}"/>`
    + t(40, 48, `KIASI · LAST ${w.days} DAYS`, 11, c.muted, F.mono, ' letter-spacing="1"') + t(760, 48, span(w), 11, c.muted, F.mono, ' text-anchor="end" letter-spacing="1"')
    + t(40, 120, big, 56, w.factor != null && w.factor < 1 ? c.ink : c.ok, F.serif) + t(40, 152, sub, 16, c.ink, F.serif)
    + `<line x1="40" y1="${top - 40}" x2="760" y2="${top - 40}" stroke="${c.rule}"/>` + cells
    + t(40, h - 16, 'kiasi keeps Claude Code sessions small · raj-rangani.github.io/kiasi', 11, c.muted, F.mono) + '</svg>';
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function flash(msg) {
  const el = $('#wrap-done');
  el.textContent = msg; el.hidden = false;
  clearTimeout(flash.t); flash.t = setTimeout(() => { el.hidden = true; }, 1800);
}

function render(d) {
  const w = { ...d.wrapped, last_day: (d.per_day && d.per_day.length ? d.per_day[d.per_day.length - 1].day : undefined) };
  $('#wrap-card').innerHTML = cardHtml(w);
  $('#wrap-copy').onclick = async () => {
    try { await navigator.clipboard.writeText(cardText(w)); flash('Copied'); } catch { download(`kiasi-month-${w.last_day || 'card'}.txt`, cardText(w), 'text/plain'); }
  };
  $('#wrap-save').onclick = () => { download(`kiasi-month-${w.last_day || 'card'}.svg`, cardSvg(w), 'image/svg+xml'); flash('Saved'); };
}

registerView('month', render, 'lens', d => d && d.wrapped && d.wrapped.active_days > 0);
})();
