dash.calc('pace_summary', {
  title: 'Latest reports vs 2022 and registration',
  description: 'For each place: the latest 2026 report, the change since the previous report from the same source and definition, the 2022 figure at the same number of days before Election Day (only if reported for that exact day), and the latest as a share of registered voters.',
  inputs: ['pace', 'registration'],
  fn: (pace, registration) => {
    const geos = [...new Set(pace.map((p) => p.series_id))];
    return geos.map((g) => {
      const pts = pace.filter((p) => p.series_id === g);
      const y26 = pts.filter((p) => Number(p.year) === 2026).sort((a, b) => String(a.date).localeCompare(String(b.date)));
      const latest = y26[y26.length - 1] || null;
      const prev = latest ? [...y26].reverse().find((p) => p !== latest && p.source_name === latest.source_name && p.definition === latest.definition && p.date !== latest.date) : null;
      const same22 = latest ? pts.find((p) => Number(p.year) === 2022 && Number(p.days_before) === Number(latest.days_before) && p.definition === latest.definition) : null;
      const key = pts.find((p) => p.state_key) ? pts.find((p) => p.state_key).state_key : null;
      const reg = key ? registration.find((r) => r.abbr === key) : null;
      return {
        geography: g,
        label: pts[0] ? pts[0].geography : g,
        latest: latest ? Number(latest.value) : null,
        latest_date: latest ? latest.date : null,
        latest_precision: latest ? latest.precision : null,
        previous: prev ? Number(prev.value) : null,
        previous_date: prev ? prev.date : null,
        newly_reported: latest && prev && latest.precision === 'exact' && prev.precision === 'exact' ? Number(latest.value) - Number(prev.value) : null,
        same_point_2022: same22 ? Number(same22.value) : null,
        pct_registered: latest && reg && latest.precision === 'exact' && Number(reg.registered) > 0 ? Number(latest.value) / Number(reg.registered) : null,
        denominator: reg ? `${Number(reg.registered).toLocaleString('en-US')} ${reg.registration_type} registered, ${reg.as_of}` : null,
      };
    });
  },
});

(() => {
  const { fmt, fmtCompact, isNum, fmtDate, link, el, badge } = window.ec;
  const geoSel = document.querySelector('.ec-pace-geo');
  geoSel.addEventListener('change', () => drawChart());

  function fillSel() {
    const d = dash.data('pace');
    if (d.status !== 'ok') return;
    const geos = [];
    d.data.forEach((p) => { if (!geos.some((g) => g.id === p.series_id)) geos.push({ id: p.series_id, label: p.geography, both: false }); });
    geos.forEach((g) => { const yrs = new Set(d.data.filter((p) => p.series_id === g.id).map((p) => Number(p.year))); g.both = yrs.has(2022) && yrs.has(2026); });
    geos.sort((a, b) => (b.both - a.both) || a.label.localeCompare(b.label));
    const cur = geoSel.value;
    geoSel.replaceChildren(...geos.map((g) => new Option(g.label + (g.both ? '' : ' (2026 only)'), g.id)));
    if (geos.some((g) => g.id === cur)) geoSel.value = cur;
  }

  function drawChart() {
    const box = document.getElementById('chart-pace');
    const notesBox = document.getElementById('pace-notes');
    const d = dash.data('pace');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok' || window.ecTab !== 'pace') return;
    const g = geoSel.value;
    const pts = d.data.filter((p) => p.series_id === g && isNum(p.value));
    box.replaceChildren();
    const n = dash.data('pace_notes');
    const notes = (n.status === 'ok' ? n.data : []).filter((r) => r.geography === g || r.geography === 'All');
    notesBox.replaceChildren(...notes.map((r) => { const li = el('li', '', r.note + ' '); if (r.source_url) li.append(link('Source', r.source_url)); return li; }));
    if (!pts.length) { box.append(el('p', 'ec-note', 'No comparable series for this place.')); return; }
    const defs = [...new Set(pts.map((p) => p.definition))];
    const W = box.clientWidth || 600, H = 320, m = { t: 14, r: 70, b: 34, l: 56 };
    const svg = d3.select(box).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`).attr('height', H);
    const maxDays = d3.max(pts, (p) => +p.days_before), minDays = d3.min(pts, (p) => +p.days_before);
    const x = d3.scaleLinear([maxDays + 1, Math.max(0, minDays - 2)], [m.l, W - m.r]);
    const y = d3.scaleLinear([0, d3.max(pts, (p) => +p.value) * 1.08], [H - m.b, m.t]);
    svg.append('g').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(6).tickFormat((v) => (v === 0 ? 'Election Day' : v + 'd'))).attr('font-size', null).attr('font-family', null)
      .call((gg) => gg.selectAll('text').attr('fill', 'var(--color-fg-muted)')).call((gg) => gg.selectAll('line,path').attr('stroke', 'var(--cds-chart-axis)'));
    svg.append('text').attr('x', (m.l + W - m.r) / 2).attr('y', H - 2).attr('text-anchor', 'middle').attr('font-size', 11).attr('fill', 'var(--color-fg-muted)').text('Days before Election Day');
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => fmtCompact(v)).tickSize(-(W - m.l - m.r))).attr('font-size', null).attr('font-family', null)
      .call((gg) => gg.select('.domain').remove()).call((gg) => gg.selectAll('text').attr('fill', 'var(--color-fg-muted)')).call((gg) => gg.selectAll('line').attr('stroke', 'var(--cds-chart-grid)'));
    const tip = d3.select(box).append('div').attr('class', 'ec-tip');
    [[2022, 'var(--cds-chart-muted)', '4 4'], [2026, 'var(--color-fg)', null]].forEach(([yr, col, dash_]) => {
      const s = pts.filter((p) => Number(p.year) === yr).sort((a, b) => b.days_before - a.days_before);
      if (!s.length) return;
      const line = d3.line().x((p) => x(+p.days_before)).y((p) => y(+p.value));
      if (s.length > 1) svg.append('path').datum(s).attr('d', line).attr('fill', 'none').attr('stroke', col).attr('stroke-width', 2).attr('stroke-dasharray', dash_).attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round');
      svg.append('g').selectAll('circle').data(s).join('circle').attr('cx', (p) => x(+p.days_before)).attr('cy', (p) => y(+p.value)).attr('r', 4).attr('fill', col).attr('stroke', 'var(--color-panel)')
        .attr('data-source', 'pace').attr('data-field', 'value').attr('data-where', (p) => 'id=' + encodeURIComponent(p.id))
        .on('mousemove', (ev, p) => {
          const [mx, my] = d3.pointer(ev, box);
          tip.style('display', 'block').html('');
          tip.append('div').attr('class', 'k').text(`${p.year} · ${p.days_before} days before · ${fmtDate(p.date)}`);
          tip.append('div').text(fmt(+p.value) + (p.precision === 'rounded' ? ' (rounded)' : ''));
          if (isNum(p.mail) || isNum(p.in_person)) tip.append('div').text(`Mail ${isNum(p.mail) ? fmt(+p.mail) : '—'} · In person ${isNum(p.in_person) ? fmt(+p.in_person) : '—'}`);
          tip.append('div').attr('class', 'k').text(p.source_name);
          const tw = tip.node().offsetWidth;
          tip.style('left', Math.max(0, Math.min(mx + 12, box.clientWidth - tw - 4)) + 'px').style('top', (my + 12) + 'px');
        }).on('mouseleave', () => tip.style('display', 'none'));
      const last = s[s.length - 1];
      svg.append('text').attr('x', x(+last.days_before) + 7).attr('y', y(+last.value)).attr('dy', '0.35em').attr('font-size', 11).attr('fill', col === 'var(--color-fg)' ? 'var(--color-fg)' : 'var(--color-fg-muted)').text(yr + ' ' + fmtCompact(+last.value));
    });
    if (defs.length > 1) box.append(el('p', 'ec-note', 'Note: this series mixes definitions — ' + defs.join(' / ') + '. Compare with care.'));
    else box.append(el('p', 'ec-note', 'Counts: ' + defs[0] + '. Lines join reported figures only; no values are filled in between.'));
  }

  function drawTable() {
    const tbl = document.getElementById('table-pace');
    const d = dash.data('pace_summary');
    if (d.status !== 'ok') { tbl.querySelector('tbody').replaceChildren(); return; }
    window.ec.table(tbl, d.data, {
      sort: 'latest', dir: -1,
      cols: {
        geography: { render: (r) => r.label, sortVal: (r) => r.label },
        latest: { kind: 'num', render: (r) => (isNum(r.latest) ? (r.latest_precision === 'rounded' ? '≈' : '') + fmt(r.latest) : '') },
        latest_date: { kind: 'date' },
        newly_reported: { kind: 'num', render: (r) => (isNum(r.newly_reported) ? (r.newly_reported >= 0 ? '+' : '') + fmt(r.newly_reported) : '') },
        previous_date: { kind: 'date' },
        same_point_2022: { kind: 'num' },
        pct_registered: { kind: 'pct' },
      },
    });
  }

  function draw() { fillSel(); drawChart(); drawTable(); }
  dash.onData(draw);
  window.addEventListener('ec-tab', draw);
})();
