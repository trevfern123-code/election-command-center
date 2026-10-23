dash.calc('states_pct', {
  title: 'Early votes as % of registered',
  description: 'Each state\'s early votes divided by its official registered-voter count (only where both are exact).',
  inputs: ['states', 'registration'],
  fn: (states, registration) => states.map((s) => {
    const reg = registration.find((r) => r.abbr === s.abbr);
    const ok = s.total_early !== null && s.total_early !== undefined && s.precision === 'exact' && reg && reg.registered > 0;
    return { abbr: s.abbr, pct_registered: ok ? Number(s.total_early) / Number(reg.registered) : null };
  }),
});

dash.calc('sum_check', {
  title: 'State sum vs national total',
  description: 'Adds the exact early-vote counts in the state table and sets them beside the national headline figure.',
  inputs: ['states', 'national'],
  fn: (states, national) => {
    const exact = states.filter((s) => s.total_early !== null && s.total_early !== undefined && s.precision === 'exact');
    return [{
      state_sum: exact.reduce((a, s) => a + Number(s.total_early), 0),
      states_counted: exact.length,
      national_total: national[0] ? Number(national[0].total_early) : null,
    }];
  },
});

(() => {
  const { fmt, fmtCompact, isNum, fmtDate, link, el, badge, bgBadge, ratingChip, candNodes, hostOf } = window.ec;
  const qEl = document.querySelector('.ec-states-q');
  const bgEl = document.querySelector('.ec-states-bg');
  let selected = null;
  qEl.addEventListener('input', () => drawTable());
  bgEl.addEventListener('change', () => drawTable());

  const typeBadge = (t) => (t === 'official' ? badge('Official', 'ok') : t === 'tracker' ? badge('Tracker') : badge('None yet', 'warn'));
  const checkBadge = (s) => (s === 'unreachable' || s === 'error' ? badge('Not retrieved', 'bad') : s === 'changed' ? badge('Updated') : s === 'no change' ? badge('Checked, no change') : badge(s || 'not yet checked'));

  function drawChart() {
    const box = document.getElementById('chart-states');
    const d = dash.data('states');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok' || window.ecTab !== 'states') return;
    const rows = d.data.filter((r) => isNum(r.total_early) && r.precision === 'exact' && +r.total_early > 0)
      .sort((a, b) => b.total_early - a.total_early).slice(0, 25);
    box.replaceChildren();
    if (!rows.length) return;
    const W = box.clientWidth || 600;
    const barH = 18, gap = 6;
    const svg = d3.select(box).append('svg').attr('width', '100%');
    const tmp = svg.append('text').attr('font-size', 12);
    const lab = (r) => r.state + (r.battleground ? ' · BG' : '');
    const labW = d3.max(rows, (r) => { tmp.text(lab(r)); return tmp.node().getComputedTextLength(); }) + 10;
    const valW = d3.max(rows, (r) => { tmp.text(fmtCompact(+r.total_early)); return tmp.node().getComputedTextLength(); }) + 8;
    tmp.remove();
    const H = rows.length * (barH + gap);
    svg.attr('viewBox', `0 0 ${W} ${H}`).attr('height', H);
    const x = d3.scaleLinear([0, d3.max(rows, (r) => +r.total_early)], [0, Math.max(40, W - labW - valW)]);
    const tip = d3.select(box).append('div').attr('class', 'ec-tip');
    const g = svg.selectAll('g').data(rows).join('g').attr('transform', (r, i) => `translate(0,${i * (barH + gap)})`);
    g.append('text').attr('x', labW - 8).attr('y', barH / 2).attr('dy', '0.35em').attr('text-anchor', 'end')
      .attr('font-size', 12).attr('fill', 'var(--color-fg)').attr('font-weight', (r) => (r.battleground ? 600 : 400)).text(lab);
    g.append('rect').attr('x', labW).attr('height', barH).attr('rx', 4).attr('width', (r) => Math.max(2, x(+r.total_early)))
      .attr('fill', (r) => (r.battleground ? 'var(--color-fg)' : 'var(--cds-chart-muted)'))
      .attr('data-source', 'states').attr('data-field', 'total_early').attr('data-where', (r) => 'abbr=' + encodeURIComponent(r.abbr));
    g.append('text').attr('x', (r) => labW + Math.max(2, x(+r.total_early)) + 4).attr('y', barH / 2).attr('dy', '0.35em')
      .attr('font-size', 12).attr('fill', 'var(--color-fg-muted)').text((r) => fmtCompact(+r.total_early));
    g.on('mousemove', (ev, r) => {
      const [mx, my] = d3.pointer(ev, box);
      tip.style('display', 'block').html('');
      tip.append('div').attr('class', 'k').text(r.state + (r.battleground ? ' · battleground' : '') + ' · observed ' + fmtDate(r.as_of));
      tip.append('div').text('Early votes: ' + fmt(+r.total_early) + ' (' + (r.classification || '') + ')');
      if (isNum(r.mail)) tip.append('div').text('Mail: ' + fmt(+r.mail));
      if (isNum(r.in_person)) tip.append('div').text('In person: ' + fmt(+r.in_person));
      const tw = tip.node().offsetWidth;
      tip.style('left', Math.max(0, Math.min(mx + 12, box.clientWidth - tw - 4)) + 'px').style('top', (my + 12) + 'px');
    }).on('mouseleave', () => tip.style('display', 'none'))
      .on('click', (ev, r) => { selected = r.abbr; drawTable(); document.getElementById('state-detail').scrollIntoView({ block: 'nearest' }); });
  }

  function drawDetail() {
    const box = document.getElementById('state-detail');
    const d = dash.data('states');
    if (!selected || d.status !== 'ok') { box.hidden = true; return; }
    const s = d.data.find((r) => r.abbr === selected);
    if (!s) { box.hidden = true; return; }
    box.hidden = false;
    const head = el('div', 'ec-row');
    const h = el('h3', '', s.state); if (s.battleground) h.append(bgBadge());
    const close = el('button', 'ec-linkbtn', 'Close'); close.addEventListener('click', () => { selected = null; drawTable(); });
    head.append(h, close);
    const reg = (dash.data('registration').data || []).find((r) => r.abbr === s.abbr);
    const dl = el('dl', 'ec-kv');
    const add = (k, v) => { const dd = el('dd'); dd.append(...[].concat(v)); dl.append(el('dt', '', k), dd); };
    add('Early votes', isNum(s.total_early) ? fmt(+s.total_early) : (s.approx_text || 'not available'));
    add('Primary source', s.source_url ? link(s.source_name || hostOf(s.source_url), s.source_url) : (s.source_name || '—'));
    add('Source type', typeBadge(s.classification));
    add('Data as of', [fmtDate(s.as_of) || '—', s.stale ? ' ' : '', s.stale ? badge('Stale data', 'bad') : '']);
    add('Last checked', fmtDate(s.last_checked) || 'not yet read successfully');
    add('Last attempt', [fmtDate(s.last_attempt) || '—', ' ', ...window.ec.freshBadges(Object.assign({}, s, { stale: false }))]);
    add('Coverage', s.coverage || '—');
    if (s.coverage_gap) add('Why no figure', s.coverage_gap);
    add('What it counts', s.definition || '—');
    add('Precision', s.precision === 'calculated' ? 'Calculated from printed parts' : (s.precision || '—'));
    if (s.calc_note) add('Calculation', s.calc_note);
    if (s.coverage_category) add('This hour', s.coverage_category);
    if (s.tracker_alt) add('Tracker figure (not used)', [link(s.tracker_alt.source, s.tracker_alt.url), ': ' + fmt(+s.tracker_alt.value) + ' — page dated ' + fmtDate(s.tracker_alt.page_date) + '. ' + s.tracker_alt.note]);
    if (s.review) add('Under review', window.ec.freshBadges({ review: s.review }));
    if (s.evidence && s.evidence.text) {
      const ev = el('div');
      ev.append(el('div', 'ec-evidence', s.evidence.text));
      if (s.evidence.label_text && s.evidence.label_text !== s.evidence.text) ev.append(el('div', 'ec-evidence', 'Label: ' + s.evidence.label_text));
      if (s.evidence.as_of_text) ev.append(el('div', 'ec-evidence', 'Date: ' + s.evidence.as_of_text));
      ev.append(el('div', 'ec-note', `Read ${fmtDate(s.evidence.read_at)} by ${s.evidence.method || 'fetch tool + agent'}` + (s.evidence.raw_sha256 ? ` · raw file sha256 ${s.evidence.raw_sha256.slice(0, 12)}` : '')));
      add('Source text saved', ev);
    }
    if (reg) add('Registered voters', [fmt(+reg.registered) + ' (' + reg.registration_type + ', ' + fmtDate(reg.as_of) + ') ', link('source', reg.source_url)]);
    if (s.official_election_site) add('State election office', link(hostOf(s.official_election_site), s.official_election_site));
    if (s.notes) add('Notes', s.notes);
    const cmpWrap = el('div');
    const cmp = (dash.data('source_compare').data || []).filter((r) => r.geography === s.abbr);
    if (cmp.length) {
      cmpWrap.append(el('h3', '', 'Compare sources'));
      const tbl = el('table', 'ec-table');
      tbl.dataset.source = 'source_compare'; tbl.dataset.rowKey = 'id';
      tbl.innerHTML = '<thead><tr><th data-field="source">Source</th><th data-field="value" class="n">Value</th><th data-field="observed_at">Observed</th><th data-field="provider" class="ec-hide-narrow">Underlying data</th><th data-field="note" class="ec-hide-narrow">Difference</th></tr></thead><tbody></tbody>';
      cmpWrap.append(tbl);
      window.ec.table(tbl, cmp, { cols: { value: { render: (r) => (isNum(r.value) ? fmt(+r.value) : (r.value_text || '—')) }, observed_at: { kind: 'date' }, source: { render: (r) => [link(r.source, r.url), r.primary ? ' ' : '', r.primary ? badge('primary') : ''] } } });
    }
    box.replaceChildren(head, dl, cmpWrap);
  }

  function drawTable() {
    const tbl = document.getElementById('table-states');
    const d = dash.data('states');
    if (d.status !== 'ok') { tbl.querySelector('tbody').replaceChildren(); return; }
    const pct = dash.data('states_pct');
    const pmap = new Map((pct.status === 'ok' ? pct.data : []).map((r) => [r.abbr, r.pct_registered]));
    const q = qEl.value.trim().toLowerCase();
    const rows = d.data.filter((r) => (!bgEl.checked || r.battleground) && (!q || (r.state + ' ' + r.abbr).toLowerCase().includes(q)));
    window.ec.table(tbl, rows, {
      sort: 'total_early', dir: -1,
      onRow: (k) => { selected = selected === k ? null : k; drawTable(); },
      selected: (r) => r.abbr === selected,
      cols: {
        state: { render: (r) => [r.state, r.battleground ? bgBadge() : ''] },
        total_early: { kind: 'num', render: (r) => (isNum(r.total_early) ? fmt(+r.total_early) : (r.approx_text ? el('span', 'ec-note', r.approx_text) : (r.coverage_gap ? el('span', 'ec-note', 'No source — gap') : ''))) },
        pct_registered: { kind: 'pct', from: (r) => pmap.get(r.abbr), sortVal: (r) => pmap.get(r.abbr) },
        mail: { kind: 'num' }, in_person: { kind: 'num' },
        as_of: { kind: 'date', render: (r) => [fmtDate(r.as_of), r.as_of_basis === 'tracker page date' ? el('div', 'ec-note', 'tracker page date' + (r.tracker_value_since ? '; count shown since ' + fmtDate(r.tracker_value_since) : '')) : ''] },
        last_checked: { kind: 'date' },
        check_status: { render: (r) => window.ec.freshBadges(r), sortVal: (r) => (r.stale ? 'a' : 'b') + (r.check_status || '') },
        classification: { render: (r) => typeBadge(r.classification) },
      },
    });
    drawDetail();
  }

  function drawSum() {
    window.ec.fill('sum_check', (f, v) => fmt(+v));
  }

  // ---------- battlegrounds ----------
  function drawBG() {
    const box = document.getElementById('bg-cards');
    const st = dash.data('bg_states');
    const wr = dash.data('watch_races');
    const hl = dash.data('headlines');
    if (st.status === 'error') return window.ec.fail(box);
    if (st.status !== 'ok') { box.replaceChildren(); return; }
    const races = wr.status === 'ok' ? wr.data : [];
    box.replaceChildren(...st.data.map((s) => {
      const c = el('div', 'ec-card'); c.id = 'bg-' + s.abbr;
      const h = el('div', 'ec-row');
      const h3 = el('h3', '', s.state); h3.append(bgBadge());
      h.append(h3);
      if (s.official_election_site) h.append(link('Official site', s.official_election_site));
      c.append(h);
      const mine = races.filter((r) => r.state_abbr === s.abbr && r.chamber !== 'House');
      mine.forEach((r) => {
        const row = el('div', 'ec-row');
        const left = el('span', 'ec-note', r.chamber + ': '); left.append(...candNodes(r.candidates));
        row.append(left, ratingChip(r.rating));
        c.append(row);
      });
      const house = races.filter((r) => r.state_abbr === s.abbr && r.chamber === 'House');
      if (house.length) c.append(el('p', 'ec-note', 'Watched House seats: ' + house.map((r) => r.district + ' (' + r.rating + ')').join(', ')));
      const ev = el('div', 'ec-row');
      ev.append(el('span', 'ec-note', 'Early votes' + (s.early_as_of ? ', observed ' + fmtDate(s.early_as_of) : '')));
      const big = el('span', 'ec-big');
      big.style.fontFamily = 'var(--font-anthropic-serif)'; big.style.fontSize = 'var(--cds-font-size-heading)';
      big.setAttribute('data-source', 'bg_states'); big.setAttribute('data-field', 'early_total'); big.setAttribute('data-where', 'abbr=' + encodeURIComponent(s.abbr));
      big.textContent = isNum(s.early_total) ? fmt(+s.early_total) : '—';
      ev.append(big);
      c.append(ev);
      const meta = el('p', 'ec-note');
      if (s.early_source_type) meta.append(typeBadge(s.early_source_type), ' ');
      if (s.vs_2022) meta.append(s.vs_2022 + ' ');
      if (s.party_split_note) meta.append(s.party_split_note + ' ');
      if (s.early_source_url) meta.append(link('Source', s.early_source_url));
      c.append(meta);
      const items = (hl.status === 'ok' ? hl.data : []).filter((n) => n.scope === s.abbr).slice(0, 3);
      if (items.length) {
        const ul = el('ul', 'ec-list');
        items.forEach((n) => { const li = el('li'); li.append(link(n.title, n.url), el('span', 'ec-note', ' — ' + (n.outlet || '') + ', ' + fmtDate(n.date))); ul.append(li); });
        c.append(ul);
      }
      return c;
    }));
  }

  function draw() { drawSum(); drawChart(); drawTable(); drawBG(); }
  dash.onData(draw);
  window.addEventListener('ec-tab', draw);
})();
