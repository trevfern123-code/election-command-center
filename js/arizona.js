(() => {
  const { fmt, fmtCompact, isNum, fmtDate, todayAZ, link, el, badge, ratingChip, candNodes, hostOf } = window.ec;

  function hbar(box, rows, opts) {
    box.replaceChildren();
    if (!rows.length) { box.append(el('p', 'ec-note', opts.empty)); return; }
    const W = box.clientWidth || 400;
    const barH = 20, gap = 8;
    const svg = d3.select(box).append('svg').attr('width', '100%');
    const tmp = svg.append('text').attr('font-size', 12);
    const labW = d3.max(rows, (r) => { tmp.text(opts.label(r)); return tmp.node().getComputedTextLength(); }) + 10;
    const valW = d3.max(rows, (r) => { tmp.text(opts.valText(r)); return tmp.node().getComputedTextLength(); }) + 8;
    tmp.remove();
    const H = rows.length * (barH + gap);
    svg.attr('viewBox', `0 0 ${W} ${H}`).attr('height', H);
    const x = d3.scaleLinear([0, opts.max ?? d3.max(rows, opts.value)], [0, Math.max(40, W - labW - valW)]);
    const g = svg.selectAll('g').data(rows).join('g').attr('transform', (r, i) => `translate(0,${i * (barH + gap)})`);
    g.append('text').attr('x', labW - 8).attr('y', barH / 2).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-size', 12).attr('fill', 'var(--color-fg)').text(opts.label);
    g.append('rect').attr('x', labW).attr('height', barH).attr('rx', 4).attr('width', (r) => Math.max(2, x(opts.value(r))))
      .attr('fill', (r, i) => opts.color(r, i))
      .each(function (r) { Object.entries(opts.mark(r)).forEach(([k, v]) => this.setAttribute(k, v)); });
    g.append('text').attr('x', (r) => labW + Math.max(2, x(opts.value(r))) + 4).attr('y', barH / 2).attr('dy', '0.35em').attr('font-size', 12).attr('fill', 'var(--color-fg-muted)').text(opts.valText);
  }
  window.ec.hbar = hbar;

  function drawTiles() {
    window.ec.fill('az_returns');
    window.ec.fill('az_leg');
    const d = dash.data('az_returns');
    document.querySelectorAll('[data-approx-for]').forEach((s) => {
      const row = d.status === 'ok' ? d.data.find((r) => r.area === s.dataset.approxFor) : null;
      s.textContent = row && row.precision === 'rounded' ? '≈' : '';
    });
  }

  function drawCounties() {
    const tbl = document.getElementById('table-az-counties');
    const cov = document.getElementById('az-county-cov');
    const d = dash.data('az_counties');
    if (d.status !== 'ok') { tbl.querySelector('tbody').replaceChildren(); cov.textContent = ''; return; }
    const have = d.data.filter((r) => isNum(r.returned));
    const off = have.filter((r) => r.classification === 'official').length;
    cov.textContent = `Counts available for ${have.length} of ${d.data.length} counties (${off} official). Missing counties are listed with the reason. A statewide total is never built from incomplete county figures.`;
    window.ec.table(tbl, d.data, {
      sort: 'returned', dir: -1,
      cols: {
        returned: { kind: 'num', render: (r) => (isNum(r.returned) ? (r.precision === 'rounded' ? '≈' : '') + fmt(+r.returned) : el('span', 'ec-note', 'missing')) },
        issued: { kind: 'num' },
        as_of: { kind: 'date' },
        classification: { render: (r) => [r.classification === 'official' ? badge('Official', 'ok') : r.classification === 'tracker' ? badge('Tracker') : badge('Missing', 'warn'), ' ', r.source_url ? link('source', r.source_url) : ''] },
      },
    });
  }

  function drawDates() {
    const box = document.getElementById('az-dates');
    const d = dash.data('az_dates');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok') { box.replaceChildren(); return; }
    const today = todayAZ();
    const rows = d.data.slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const next = rows.find((r) => r.date >= today);
    box.replaceChildren(...rows.map((r) => {
      const e = el('div', 'ec-date' + (r.date < today ? ' past' : '') + (r === next ? ' next' : ''));
      const body = el('span');
      body.append(link(r.label, r.source_url));
      if (r.detail) body.append(el('span', 'ec-note', ' — ' + r.detail));
      e.append(el('span', 'd', fmtDate(r.date)), body);
      return e;
    }));
  }

  function drawHowto() {
    const box = document.getElementById('az-howto');
    const d = dash.data('az_howto');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok') { box.replaceChildren(); return; }
    box.replaceChildren(...d.data.map((r) => {
      const li = el('li');
      li.append(el('b', '', r.topic + ': '), r.text + ' ');
      if (r.source_url) li.append(link('Source', r.source_url));
      return li;
    }));
  }

  function drawSplit() {
    const box = document.getElementById('chart-az-split');
    const d = dash.data('az_split');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok' || window.ecTab !== 'arizona') return;
    const order = ['Republican', 'Democratic', 'Other'];
    const rows = d.data.filter((r) => isNum(r.share)).sort((a, b) => order.indexOf(a.party) - order.indexOf(b.party));
    hbar(box, rows, {
      empty: 'No party-registration breakdown published yet.', max: 1,
      label: (r) => r.party, value: (r) => +r.share,
      valText: (r) => d3.format('.1%')(+r.share) + (isNum(r.returned) ? ' (' + fmtCompact(+r.returned) + ')' : ''),
      color: (r) => (r.party === 'Republican' ? 'var(--ec-rep)' : r.party === 'Democratic' ? 'var(--ec-dem)' : 'var(--cds-chart-muted)'),
      mark: (r) => ({ 'data-source': 'az_split', 'data-field': 'share', 'data-where': 'id=' + encodeURIComponent(r.id) }),
    });
    if (d.data[0]) { const p = el('p', 'ec-note'); p.append(link('Source: ' + d.data[0].source_name + ', observed ' + fmtDate(d.data[0].as_of), d.data[0].source_url)); box.append(p); }
  }

  function drawTrackers() {
    const box = document.getElementById('chart-az-trackers');
    const d = dash.data('az_trackers');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok' || window.ecTab !== 'arizona') return;
    const rows = d.data.filter((r) => isNum(r.returned)).sort((a, b) => b.returned - a.returned);
    hbar(box, rows, {
      empty: 'No tracker figures yet.',
      label: (r) => r.source + (r.primary ? ' (primary)' : ''), value: (r) => +r.returned,
      valText: (r) => (r.precision === 'rounded' ? '≈' : '') + fmtCompact(+r.returned) + ' · ' + fmtDate(r.as_of),
      color: (r) => (r.primary ? 'var(--color-fg)' : 'var(--cds-chart-muted)'),
      mark: (r) => ({ 'data-source': 'az_trackers', 'data-field': 'returned', 'data-where': 'id=' + encodeURIComponent(r.id) }),
    });
    const prov = rows.filter((r) => r.provider).map((r) => r.source + ': ' + r.provider);
    if (prov.length) box.append(el('p', 'ec-note', 'Underlying data — ' + prov.join(' · ')));
  }

  function drawRaces() {
    const d = dash.data('az_races');
    if (d.status !== 'ok') return;
    const cols = { rating: { kind: 'rating' }, office: { kind: 'link', linkField: 'source_url' }, candidates: { kind: 'cands' } };
    window.ec.table(document.getElementById('table-az-statewide'), d.data.filter((r) => r.level === 'Statewide'), { cols });
    window.ec.table(document.getElementById('table-az-house'), d.data.filter((r) => r.level === 'U.S. House'), { sort: 'office', cols });
  }

  function draw() { drawTiles(); drawCounties(); drawDates(); drawHowto(); drawSplit(); drawTrackers(); drawRaces(); }
  dash.onData(draw);
  window.addEventListener('ec-tab', draw);
})();
