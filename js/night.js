(() => {
  const { fmt, isNum, fmtDate, link, el, badge, candNodes } = window.ec;
  const resSeg = document.getElementById('res-chamber');
  let resFilter = '';
  resSeg.addEventListener('click', (ev) => {
    const b = ev.target.closest('button'); if (!b) return;
    resFilter = b.dataset.v;
    resSeg.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    drawResults();
  });

  const azClock = (hhmm) => {
    if (!hhmm) return '';
    const [h, m] = hhmm.split(':').map(Number);
    const ap = h >= 12 ? 'PM' : 'AM';
    const h12 = ((h + 11) % 12) + 1;
    return `${h12}:${String(m).padStart(2, '0')} ${ap}`;
  };
  const closeStatus = (r) => {
    const t = new Date(r.utc);
    if (isNaN(t)) return '';
    const ms = t - Date.now();
    if (ms <= 0) return 'Closed';
    if (ms < 24 * 3.6e6) { const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4); return `Closes in ${h}h ${m}m`; }
    return 'Election Day';
  };
  // A row the refresh has not updated yet: derive "Awaiting results" from the clock once that state's last polls close.
  const lastClose = () => {
    const pc = dash.data('poll_close');
    const m = new Map();
    (pc.status === 'ok' ? pc.data : []).forEach((r) => { const t = +new Date(r.utc); if (!m.has(r.abbr) || t > m.get(r.abbr)) m.set(r.abbr, t); });
    return m;
  };
  // Races this site does not count live (results_mode "official link") point to the state's official results once its polls close.
  const shownStatus = (r, lc) => (r.status === 'Polls not closed' && lc.has(r.state_abbr) && Date.now() >= lc.get(r.state_abbr)
    ? (r.results_mode === 'official link' ? 'See official results' : 'Awaiting results') : r.status);
  const STATUS_KIND = { 'Certified': 'ok', 'Projected winner': 'ok', 'Leading in reported results': 'warn', 'Recount': 'bad', 'Too close to call': 'warn' };

  function drawStatus() {
    const box = document.getElementById('night-status');
    const pc = dash.data('poll_close'), rs = dash.data('results');
    if (pc.status !== 'ok' || rs.status !== 'ok') { box.replaceChildren(); if (pc.status === 'error' || rs.status === 'error') window.ec.fail(box); return; }
    const times = pc.data.map((r) => new Date(r.utc)).filter((d) => !isNaN(d)).sort((a, b) => a - b);
    const now = Date.now();
    const first = times[0], last = times[times.length - 1];
    const az = pc.data.find((r) => r.abbr === 'AZ');
    const lc = lastClose();
    const counts = d3.rollup(rs.data, (v) => v.length, (r) => shownStatus(r, lc));
    const h = el('h2', '', 'Election night & counting');
    const p = el('p', 'ec-take');
    if (first && now < +first) {
      p.textContent = `Polls have not closed. The first polls close at ${azClock(pc.data.find((r) => +new Date(r.utc) === +first).az_time)} Arizona time on Nov 3; Arizona's close at ${az ? azClock(az.az_time) : '—'}. No results are reported before polls close.`;
    } else if (last && now < +last) {
      const closed = pc.data.filter((r) => +new Date(r.utc) <= now).length;
      p.textContent = `Polls are closing: ${closed} of ${pc.data.length} closing times have passed. Results below are reported counts, not final.`;
    } else {
      p.textContent = 'All polls have closed. Counting continues; results are unofficial until certified.';
    }
    const sum = el('p', 'ec-note', [...counts].map(([k, v]) => `${k}: ${v}`).join(' · '));
    box.replaceChildren(h, p, sum);
  }

  function drawResults() {
    const tbl = document.getElementById('table-results');
    const d = dash.data('results');
    if (d.status !== 'ok') { tbl.querySelector('tbody').replaceChildren(); return; }
    const lc = lastClose();
    const azLiveAny = d.data.some((r) => r.arizona && r.results_mode === 'live');
    const rows = d.data.filter((r) => !resFilter || (resFilter === 'Arizona' ? r.arizona : r.chamber === resFilter));
    window.ec.table(tbl, rows, {
      sort: 'race_label',
      cols: {
        // Arizona races counted live first, then the rest of Arizona, then other states
        race_label: { sortVal: (r) => (r.results_mode === 'live' ? '0' : r.arizona ? '1' : '2') + ' ' + r.race_label },
        status: { render: (r) => [badge(shownStatus(r, lc), STATUS_KIND[r.status] || ''), r.projection_by ? el('div', 'ec-note', 'Call by ' + r.projection_by) : '', r.certification_status ? el('div', 'ec-note', r.certification_status) : ''] },
        tally: {
          render: (r) => {
            if (!isNum(r.votes_d) && !isNum(r.votes_r)) {
              if (r.results_mode === 'official link' && r.source_url) {
                // Arizona races are counted here once the state posts its first results; until then every Arizona row waits for them
                // AZ-03 (no Republican nominee on the ballot) and the 2-seat Corporation Commission are never counted here
                const azWaiting = r.arizona && !azLiveAny && !['AZ-AZ-03', 'AZ-sw-6'].includes(r.id);
                const n = el('span', 'ec-note', azWaiting ? 'Counted here once Arizona posts results (first results expected around 8:00 PM). Official site: ' : 'Not counted live on this site: ');
                n.append(link(r.source_name || 'Official results', r.source_url)); return n;
              }
              return el('span', 'ec-note', 'No votes reported');
            }
            const out = [];
            if (isNum(r.votes_d)) out.push(el('span', 'ec-p-dem', `${r.cand_d} ${fmt(+r.votes_d)}`));
            if (isNum(r.votes_r)) { if (out.length) out.push(' · '); out.push(el('span', 'ec-p-rep', `${r.cand_r} ${fmt(+r.votes_r)}`)); }
            if (isNum(r.other_votes)) out.push(' · others ' + fmt(+r.other_votes));
            if (r.latest_batch) out.push(el('div', 'ec-note', 'Latest batch: ' + r.latest_batch));
            if (r.source_url) out.push(el('div', 'ec-note')), out[out.length - 1].append(link(r.source_name || 'Official results', r.source_url));
            return out;
          },
        },
        margin: { render: (r) => (isNum(r.margin_votes) ? (r.leader ? r.leader + ' +' : '') + fmt(+r.margin_votes) + (isNum(r.margin_pct) ? ` (${(+r.margin_pct).toFixed(1)} pts)` : '') : '') },
        reported_at: { kind: 'date' },
        outstanding: { render: (r) => [r.outstanding || el('span', 'ec-note', r.results_mode === 'official link' ? 'See official results' : 'Remaining total unknown'), isNum(r.expected_vote_pct) ? el('div', 'ec-note', `Estimate: ${r.expected_vote_pct}% of expected vote counted (${r.expected_vote_provider || 'provider not named'})`) : ''] },
        next_update: { render: (r) => r.next_update || el('span', 'ec-note', 'Not announced') },
      },
    });
  }

  function drawClosings() {
    const tbl = document.getElementById('table-closings');
    const d = dash.data('poll_close');
    if (d.status !== 'ok') { tbl.querySelector('tbody').replaceChildren(); return; }
    window.ec.table(tbl, d.data, {
      sort: 'az_time',
      cols: {
        az_time: { render: (r) => azClock(r.az_time), sortVal: (r) => r.utc },
        state: { render: (r) => [r.state, r.battleground ? window.ec.bgBadge() : ''] },
        local_time: { from: (r) => `${r.local_time} ${r.tz}` },
        status: { from: (r) => closeStatus(r) },
      },
    });
  }

  function drawLists() {
    const c = dash.data('counting');
    const cbox = document.getElementById('counting-list');
    if (c.status === 'ok') cbox.replaceChildren(...c.data.map((r) => { const li = el('li'); li.append(el('b', '', r.geography + ': '), r.statement + ' '); if (r.source_url) li.append(link('Source', r.source_url)); if (r.date) li.append(el('span', 'ec-note', ' (' + fmtDate(r.date) + ')')); return li; }));
    const ce = dash.data('certification');
    if (ce.status === 'ok') window.ec.table(document.getElementById('table-cert'), ce.data, { sort: 'state', cols: { state: { render: (r) => link(r.state, r.source_url) }, status: { render: (r) => r.status || el('span', 'ec-note', 'Not started') } } });
    const m = dash.data('method_notes');
    const mbox = document.getElementById('method-list');
    if (m.status === 'ok') mbox.replaceChildren(...m.data.map((r) => { const li = el('li'); li.append(el('b', '', r.topic + ': '), r.text + ' '); if (r.source_url) li.append(link('Source', r.source_url)); return li; }));
  }

  // ---------- overview card: results at the top of every page ----------
  const AZ_ORDER = ['G-AZ', 'H-AZ-01', 'H-AZ-06', 'H-AZ-02', 'AZ-sw-1', 'AZ-sw-2', 'AZ-sw-3', 'AZ-sw-4', 'AZ-sw-5'];
  const azTime = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleTimeString('en-US', { timeZone: 'America/Phoenix', hour: 'numeric', minute: '2-digit' }) + ' AZ'; };
  function drawOverview() {
    const box = document.getElementById('ov-results-body');
    if (!box) return;
    const pc = dash.data('poll_close'), rs = dash.data('results');
    if (pc.status !== 'ok' || rs.status !== 'ok') { box.replaceChildren(); if (pc.status === 'error' || rs.status === 'error') window.ec.fail(box); return; }
    const now = Date.now();
    const az = pc.data.find((r) => r.abbr === 'AZ');
    const azClose = az ? +new Date(az.utc) : NaN;
    const closed = pc.data.filter((r) => +new Date(r.utc) <= now).length;
    const live = rs.data.filter((r) => r.results_mode === 'live' && isNum(r.votes_d) && isNum(r.votes_r))
      .sort((a, b) => ((AZ_ORDER.indexOf(a.id) + 99) % 99) - ((AZ_ORDER.indexOf(b.id) + 99) % 99) || String(a.race_label).localeCompare(String(b.race_label)));
    const seen = new Set();
    const rows = live.filter((r) => { const k = r.race_label; if (seen.has(k)) return false; seen.add(k); return true; });
    const out = [];
    if (!rows.length) {
      const p = el('p', 'ec-take');
      if (!isNaN(azClose) && now < azClose) {
        p.textContent = `No votes are reported before polls close. Arizona polls close at ${azClock(az.az_time)} Arizona time; the first Arizona results are expected around 8:00 PM (Maricopa County). ${closed} of ${pc.data.length} poll closings nationwide have passed.`;
      } else {
        p.textContent = 'Arizona polls have closed. Waiting for the first Arizona results (expected around 8:00 PM Arizona time, Maricopa County). This page checks for new results every 5 minutes.';
      }
      out.push(p, el('p', 'ec-note', 'Arizona races are counted live here from the Arizona Secretary of State results file. Races in other states link to each state\'s official results site once its polls close.'));
    } else {
      const latest = rows.map((r) => r.reported_at).filter(Boolean).sort().pop();
      out.push(el('p', 'ec-note', `Arizona, unofficial reported results${latest ? ' · updated ' + azTime(latest) : ''}. "Leading" means ahead in the votes counted so far, not a projected winner.`));
      const ul = el('ul', 'ec-res-list');
      rows.slice(0, 9).forEach((r) => {
        const li = el('li');
        const head = el('div', 'ec-res-race');
        head.append(el('span', '', r.race_label), el('span', 'ec-note', isNum(r.margin_votes) ? `${r.leader ? r.leader + ' +' : 'Tied '}${fmt(+r.margin_votes)}${isNum(r.margin_pct) ? ` (${(+r.margin_pct).toFixed(1)} pts)` : ''}` : ''));
        const t = el('div');
        t.append(el('span', 'ec-p-dem', `${r.cand_d} ${fmt(+r.votes_d)}`), ' · ', el('span', 'ec-p-rep', `${r.cand_r} ${fmt(+r.votes_r)}`));
        li.append(head, t);
        ul.append(li);
      });
      out.push(ul);
      const others = rs.data.filter((r) => r.results_mode === 'official link' && !r.arizona).length;
      out.push(el('p', 'ec-note', `${others} races in other states: see "All results" for links to each state's official count. ${closed} of ${pc.data.length} poll closings have passed.`));
    }
    box.replaceChildren(...out);
  }

  function draw() { drawStatus(); drawResults(); drawClosings(); drawLists(); drawOverview(); }
  dash.onData(draw);
  window.addEventListener('ec-tab', draw);
  setInterval(() => { drawOverview(); if (window.ecTab === 'night') { drawStatus(); drawClosings(); drawResults(); } }, 60000);
})();
