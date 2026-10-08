(() => {
const root = document.querySelector('[data-view="storage"]');
const $ = s => root.querySelector(s);
const mb = bytes => !bytes ? '0 kB' : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(bytes < 1e7 ? 1 : 0)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
const day = ts => stamp(ts).slice(0, 16);
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const date = d => new Date(`${d.slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

function ago(ts) {
  const days = Math.floor((Date.now() - new Date(ts).getTime()) / 864e5);
  return days <= 0 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`;
}

function rows(head, body, empty) {
  if (!body.length) return `<p class="empty">${empty}</p>`;
  return `<table class="store-table"><thead><tr>${head.map(h => `<th${h.startsWith('#') ? ' class="num"' : ''}>${esc(h.replace(/^#/, ''))}</th>`).join('')}</tr></thead><tbody>${body.join('')}</tbody></table>`;
}

function more(shown, total) {
  return total > shown ? `<p class="recall-note">Showing the ${shown} largest of ${total}; <code>cleanup.py --dry-run</code> lists what is due now.</p>` : '';
}

function reporting(s) {
  const last = s.last || {};
  return s.mode === 'report' || (s.mode === 'auto' && (last.mode === 'report' || !last.ts));
}

// One entry per STORE_CATS category with its bytes and files; everything unlisted counts as caches.
function categories(s) {
  const named = new Set(STORE_CATS.flatMap(c => c[3]));
  return STORE_CATS.map(([key, label, icon, members]) => {
    const list = s.folders.filter(f => members.length ? members.includes(f.name) : !named.has(f.name));
    return { key, label, icon, bytes: list.reduce((n, f) => n + f.bytes, 0), files: list.reduce((n, f) => n + f.files, 0) };
  });
}

function growthRate(days) {
  const first = days.findIndex(d => d.added);
  if (first < 0) return 0;
  const recent = days.slice(Math.max(first, days.length - STORE_RATE_DAYS));
  return recent.reduce((n, d) => n + d.added, 0) / recent.length;
}

function renderMeter(s, cats) {
  const used = s.size / Math.max(1, s.max);
  const rate = growthRate(s.growth || []);
  const growth = rate < STORE_FLAT_BYTES ? 'Not growing lately'
    : `Adds about ${mb(rate)} a day · ${s.size + rate * s.idle_days < s.max ? `levels off near ${mb(rate * s.idle_days)}` : `reaches the cap in ~${Math.max(1, Math.round((s.max - s.size) / rate))} days`}`;
  const shown = cats.filter(c => c.bytes);
  const free = Math.max(0, s.max - s.size);
  $('#store-meter').innerHTML = `<div class="store-total"><b>${mb(s.size)}</b><span>of ${mb(s.max)} used · ${(used * 100).toFixed(used < 0.1 ? 1 : 0)}% · <em>${mb(free)} free</em></span></div>
    <div class="store-bar" role="img" aria-label="${esc(`${mb(s.size)} of ${mb(s.max)} used, ${mb(free)} free`)}"><div class="store-fill">${shown.map(c =>
      `<i class="cat-${c.key}" style="width:${(c.bytes / Math.max(s.max, s.size) * 100).toFixed(3)}%" title="${esc(`${c.label}: ${mb(c.bytes)}`)}"></i>`).join('')}</div></div>
    <div class="store-key">${shown.map(c => `<span title="${mb(c.bytes)}"><i class="cat-${c.key}"></i>${esc(c.label)}</span>`).join('')}<span title="${mb(free)}"><i class="cat-free"></i>Free</span></div>
    <p class="store-foot">${growth}. Saved files leave once they and their session go unused; the log and caches stay.</p>`;
}

function rec(title, value, body, action, warn) {
  return `<div class="store-rec${warn ? ' warn' : ''}"><div class="store-rec-head"><b>${title}</b>${value ? `<span>${value}</span>` : ''}</div>
    <p>${body}</p>${action ? `<button type="button" class="store-link" data-rec="${action[0]}">${esc(action[1])} ›</button>` : ''}</div>`;
}

function renderRecs(s) {
  const last = s.last || {};
  const cleaned = s.cleaned || {};
  const cards = [];
  if (s.mode === 'off') cards.push(rec('Cleanup is off', '', 'Nothing is moved or deleted while <code>KIASI_CLEANUP=off</code>. Remove it to let saved files leave once they go unused.', null, true));
  if (last.error) cards.push(rec('The last cleanup failed', '', `${esc(day(last.ts))}: ${esc(last.error)}`, null, true));
  if (last.over_cap) cards.push(rec('Over the size cap', mb(s.size), `The trash is emptied oldest first on the next run to get back under ${mb(s.max)}.`, null, true));
  const moves = s.report_moves || {};
  if (reporting(s) && s.report_until && moves.count) {
    cards.push(rec(`Cleanup starts on ${date(s.report_until)}`, `${mb(moves.bytes)} moves`,
      `Until then it only lists files. ${plural(moves.count, 'file')} will have been unused long enough by then and go to trash on the first run after it; each stays restorable for ${s.trash_days} more days.`, ['moves', 'Review files']));
  }
  const kept = Object.entries(cleaned).filter(([, c]) => !c.auto);
  const unread = kept.reduce((n, [, c]) => n + c.unread_bytes, 0);
  if (unread >= STORE_REC_MIN_BYTES) {
    const parts = kept.filter(([, c]) => c.unread).map(([key, c]) => `${c.unread} of ${c.read + c.unread} ${(STORE_CATS.find(x => x[0] === key) || [, key])[1].toLowerCase()}`);
    cards.push(rec('Saved files never read back', mb(unread),
      `${esc(parts.join(' and '))} were never opened again. They are kept in case a cut part matters and leave on their own after ${s.idle_days} days unused; nothing to do unless you want the space sooner.`, ['unread', 'Review files']));
  }
  $('#store-recs').innerHTML = cards.length ? cards.join('') : '<p class="store-foot">Nothing to do: cleanup keeps the folder in check on its own.</p>';
  root.querySelectorAll('[data-rec]').forEach(btn => { btn.onclick = () => (btn.dataset.rec === 'moves' ? openMoves(s) : openUnread(s)); });
}

function subtitle(s, c) {
  const info = (s.cleaned || {})[c.key];
  if (info) {
    const managed = info.read + info.unread;
    const read = info.auto ? 'read by Kiasi' : `${info.read} of ${managed} read back`;
    const other = c.files > managed ? ` · ${c.files - managed} with other names, never cleaned` : '';
    return `Last used ${ago(info.last)} · ${plural(managed, 'file')} · ${read} · next cleanup ${date(info.next_due)}${other}`;
  }
  if (c.key === 'trash') return c.files ? `${plural(c.files, 'file')} · each deleted ${s.trash_days} days after it moved` : 'Empty';
  return STORE_CAT_HELP[c.key] || '';
}

function renderList(s, cats) {
  const open = c => (s.cleaned || {})[c.key] || (c.key === 'trash' && c.files);
  $('#store-list').innerHTML = `<div class="store-rows">${cats.filter(c => c.bytes || c.files).sort((a, b) => b.bytes - a.bytes).map(c => {
    const tag = open(c) ? 'button' : 'div';
    return `<${tag} class="store-row"${tag === 'button' ? ` type="button" data-cat="${c.key}"` : ''}><span class="store-icon"><svg viewBox="0 0 24 24" aria-hidden="true">${c.icon}</svg></span>
      <span class="store-name"><b><i class="cat-${c.key}"></i>${esc(c.label)}</b><small>${esc(subtitle(s, c))}</small></span><span class="store-size">${mb(c.bytes)}</span>${tag === 'button' ? '<span class="store-chev">›</span>' : '<span class="store-chev"></span>'}</${tag}>`;
  }).join('')}</div>
    <p class="store-foot"><button type="button" class="store-link" data-log="1">Cleanup log · ${s.history.length} ${s.history.length === 1 ? 'entry' : 'entries'} ›</button></p>`;
  root.querySelectorAll('[data-cat]').forEach(btn => { btn.onclick = () => (btn.dataset.cat === 'trash' ? openTrash(s) : openFolder(s, btn.dataset.cat)); });
  $('[data-log]').onclick = () => openLog(s);
}

function fileRows(files) {
  return files.map(f => `<tr><td class="mono">${esc(f.name)}</td><td class="num">${mb(f.bytes)}</td><td class="mono">${esc(f.last)}</td>
    <td class="mono">${esc(f.due.slice(0, 10))}</td><td>${f.read ? 'yes' : '–'}</td></tr>`);
}

function openFolder(s, key) {
  const info = s.cleaned[key];
  const cat = categories(s).find(c => c.key === key);
  const keep = key === 'notes' ? `${s.note_days} days after the newest note` : `${key === 'pastes' ? s.paste_idle_days : s.idle_days} days after it and its session were last used`;
  const facts = [['Size', mb(cat.bytes)], ['Files', cat.files], ['Last used', ago(info.last)], ['Next cleanup', date(info.next_due)],
    ['Read back', info.auto ? 'by Kiasi' : `${info.read} of ${info.read + info.unread}`], ['Due in 14 days', mb(info.soon_bytes)]];
  openPanel(cat.label, `${STORE_CAT_HELP[key]}. Each file is kept ${keep}, then waits ${s.trash_days} days in the trash.`,
    `<div class="rule-nums">${facts.map(([k, v]) => `<b>${esc(String(v))}<small>${k}</small></b>`).join('')}</div>`
    + rows(['file', '#size', 'last used', 'cleanup', 'read'], fileRows(info.files), 'No files.') + more(info.files.length, cat.files));
  finishPanelContent();
}

function openMoves(s) {
  const m = s.report_moves;
  openPanel(`Moves on ${date(s.report_until)}`, `${plural(m.count, 'file')}, ${mb(m.bytes)}, unused long enough by the time report-only ends. Using a file or its session again keeps it.`,
    rows(['file', '#size', 'last used', 'due', 'read'], fileRows(m.files), 'Nothing.') + more(m.files.length, m.count));
  finishPanelContent();
}

function openUnread(s) {
  const files = Object.values(s.cleaned).filter(c => !c.auto).flatMap(c => c.files.filter(f => !f.read)).sort((a, b) => b.bytes - a.bytes);
  openPanel('Never read back', 'Saved files no later tool call named. Each leaves on its own on the date shown.',
    rows(['file', '#size', 'last used', 'cleanup', 'read'], fileRows(files), 'Every saved file was read back.'));
  finishPanelContent();
}

function openTrash(s) {
  openPanel('Trash', 'Moved out but not deleted. python3 scripts/cleanup.py --restore puts everything back; add file names to restore only those.',
    rows(['file', 'from', '#gzipped', 'moved', 'deleted on'], s.trash.slice().reverse().map(f => `<tr><td class="mono">${esc(f.name)}</td><td>${esc(f.folder)}</td><td class="num">${mb(f.bytes)}</td>
      <td class="mono">${esc(day(f.moved))}</td><td class="mono">${esc(f.delete_on)}</td></tr>`), 'The trash is empty.'));
  finishPanelContent();
}

function openLog(s) {
  openPanel('Cleanup log', 'The last moves, deletions and restores, newest first, from cleanup.jsonl.',
    rows(['when', 'action', 'file', '#size', 'why'], s.history.map(h => `<tr><td class="mono">${esc(day(h.ts))}</td><td>${esc(STORAGE_ACTIONS[h.action] || h.action)}</td>
      <td class="mono">${esc(String(h.path || '').split(/[\\/]/).slice(-2).join('/'))}</td><td class="num">${h.size != null ? mb(h.size) : ''}</td><td>${esc(h.reason || '')}</td></tr>`), 'No file has been moved, deleted or restored yet.'));
  finishPanelContent();
}

registerView('storage', d => {
  const s = d.storage;
  const cats = categories(s);
  renderMeter(s, cats); renderRecs(s); renderList(s, cats);
}, 'lens', d => Boolean(d.storage && d.storage.cleaned));
})();
