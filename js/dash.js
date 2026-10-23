/* Read-only data loader for the public Election Command Center.
   Provides the small `dash` interface the dashboard code expects (data, calc, onData, setLink).
   It only ever GETs the public JSON files in ./data; there is no write path, no login and no admin action.
   Every 5 minutes it re-reads data/manifest.json and redraws when a newer verified publication has been published. */
(() => {
  const DATA = {};            // id -> rows
  const STATE = {};           // id -> 'loading' | 'ok' | 'error'
  const calcs = {};
  const listeners = [];
  let manifest = null;
  const bust = () => Date.now().toString(36);

  async function getJSON(path) {
    const r = await fetch(path, { cache: 'no-store', credentials: 'omit' });
    if (!r.ok) throw new Error(path + ' HTTP ' + r.status);
    return r.json();
  }

  const dash = {
    data(id) {
      if (calcs[id]) {
        const c = calcs[id];
        const ins = c.inputs.map((i) => dash.data(i));
        if (ins.some((x) => x.status === 'error')) return { status: 'error', data: [] };
        if (ins.some((x) => x.status !== 'ok')) return { status: 'loading', data: [] };
        try { return { status: 'ok', data: c.fn(...ins.map((x) => x.data)) }; } catch (e) { console.error('calc', id, e); return { status: 'error', data: [] }; }
      }
      const st = STATE[id] || (manifest ? 'error' : 'loading');
      return { status: st, data: DATA[id] || [] };
    },
    calc(id, c) { calcs[id] = c; },
    onData(fn) { listeners.push(fn); try { fn(); } catch (e) { console.error(e); } },
    setLink(name) { try { if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name); } catch (e) { /* ignore */ } },
    manifest() { return manifest; },
  };
  window.dash = dash;

  const redraw = () => listeners.forEach((f) => { try { f(); } catch (e) { console.error(e); } });

  async function load(initial) {
    let m;
    try { m = await getJSON('data/manifest.json?t=' + bust()); }
    catch (e) {
      if (initial) { Object.keys(STATE).forEach((k) => { STATE[k] = 'error'; }); showLoadError(); redraw(); }
      return;  // keep showing what is already loaded; the page's own freshness banner flags age
    }
    if (!initial && manifest && m.run_id === manifest.run_id && m.exported_at === manifest.exported_at) return;
    const ids = Object.keys(m.datasets || {});
    if (initial) ids.forEach((id) => { STATE[id] = 'loading'; });
    await Promise.all(ids.map(async (id) => {
      if (!initial && manifest && manifest.datasets && manifest.datasets[id] === m.datasets[id]) return;
      try { DATA[id] = await getJSON('data/' + id + '.json?v=' + encodeURIComponent(m.datasets[id])); STATE[id] = 'ok'; }
      catch (e) { console.error(e); if (!DATA[id]) STATE[id] = 'error'; }
    }));
    manifest = m;
    window.dispatchEvent(new CustomEvent('ec-manifest', { detail: m }));
    redraw();
  }

  function showLoadError() {
    const b = document.getElementById('hdr-alert');
    if (b) { b.hidden = false; b.textContent = 'The dashboard data could not be loaded. Check your connection and reload the page.'; }
  }

  document.addEventListener('DOMContentLoaded', () => {
    load(true);
    setInterval(() => load(false), 5 * 60 * 1000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) load(false); });
  });
})();
