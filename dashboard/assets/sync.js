// Sync button: POST /sync to dashboard.py, then watch sync.json until a run that started after
// the click has finished, then reload the report.
(function () {
  const btn = $('#sync');
  if (!btn) return;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  async function status() {
    const res = await fetch(`${SYNC_STATUS_FILE}?t=${Date.now()}`);
    if (!res.ok) return null;
    return res.json();
  }
  function fail(text, detail) {
    setStatus(text, 'bad');
    $('#banner').textContent = detail;
    $('#banner').classList.remove('hidden');
  }
  async function sync() {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add('busy');
    setStatus('sync requested', '');
    try {
      const before = await status().catch(() => null);
      let seen = before ? `${before.started}|${before.state}` : '';
      const res = await fetch(SYNC_URL, { method: 'POST' });
      if (!res.ok) throw new Error(`the dashboard server answered ${res.status}`);
      const t0 = Date.now();
      let last = null;
      while (Date.now() - t0 < SYNC_TIMEOUT_MS) {
        await wait(SYNC_POLL_MS);
        last = await status().catch(() => null);
        if (!last || `${last.started}|${last.state}` === seen) continue;
        if (last.state === 'running') { setStatus('rebuilding reports', ''); seen = ''; continue; }
        break;
      }
      if (!last || last.state === 'running' || `${last.started}|${last.state}` === seen) {
        fail('sync not picked up', 'POST /sync was sent but nothing rebuilt the reports within 60 s. Check the dashboard.py process is still running.');
      } else if (last.state === 'failed') {
        fail('sync failed', `The rebuild failed: ${last.error}. Run /kiasi:sync in Claude Code to see the full error.`);
      } else {
        setStatus(`synced in ${last.seconds} s`, 'ok');
        $('#banner').classList.add('hidden');
        if (window.reloadReport) await window.reloadReport();
      }
    } catch (err) {
      fail('sync failed', `Could not reach sync.php: ${err.message}. Is the folder served over localhost with PHP enabled?`);
    } finally {
      btn.disabled = false;
      btn.classList.remove('busy');
    }
  }
  btn.addEventListener('click', sync);
})();
