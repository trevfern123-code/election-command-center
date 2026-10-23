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
  const shownStatus = (r, lc) => (r.status === 'Polls not closed' && lc.has(r.state_abbr) && Date.now() >= lc.get(r.state_abbr) ? 'Awaiting results' : r.status);
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
    const rows = d.data.filter((r) => !resFilter || (resFilter === 'Arizona' ? r.arizona : r.chamber === resFilter));
    window.ec.table(tbl, rows, {
      sort: 'race_label',
      cols: {
        status: { render: (r) => [badge(shownStatus(r, lc), STATUS_KIND[r.status] || ''), r.projection_by ? el('div', 'ec-note', 'Call by ' + r.projection_by) : '', r.certification_status ? el('div', 'ec-note', r.certification_status) : ''] },
        tally: {
          render: (r) => {
            if (!isNum(r.votes_d) && !isNum(r.votes_r)) return el('span', 'ec-note', 'No votes reported');
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
        outstanding: { render: (r) => [r.outstanding || el('span', 'ec-note', 'Remaining total unknown'), isNum(r.expected_vote_pct) ? el('div', 'ec-note', `Estimate: ${r.expected_vote_pct}% of expected vote counted (${r.expected_vote_provider || 'provider not named'})`) : ''] },
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

  function draw() { drawStatus(); drawResults(); drawClosings(); drawLists(); }
  dash.onData(draw);
  window.addEventListener('ec-tab', draw);
  setInterval(() => { if (window.ecTab === 'night') { drawStatus(); drawClosings(); drawResults(); } }, 60000);
})();
