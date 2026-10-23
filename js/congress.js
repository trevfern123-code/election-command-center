(() => {
  const { fmt, isNum, fmtDate, link, el, badge, ratingChip, candNodes, chip, partyCls } = window.ec;

  // ---------- chambers now ----------
  function drawControl() {
    const ctl = dash.data('control');
    const tbl = document.querySelector('#card-control table');
    if (ctl.status === 'ok') window.ec.table(tbl, ctl.data, { cols: {} });
  }

  // ---------- watchlist ----------
  const chamberSeg = document.getElementById('watch-chamber');
  const flipSel = document.querySelector('.ec-watch-flip');
  const qEl = document.querySelector('.ec-watch-q');
  let chamber = '';
  chamberSeg.addEventListener('click', (ev) => {
    const b = ev.target.closest('button'); if (!b) return;
    chamber = b.dataset.v;
    chamberSeg.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    drawWatch();
  });
  flipSel.addEventListener('change', () => drawWatch());
  qEl.addEventListener('input', () => drawWatch());

  function drawWatch() {
    const d = dash.data('watch_races');
    const tbl = document.getElementById('table-watch');
    if (d.status !== 'ok') { tbl.querySelector('tbody').replaceChildren(); return; }
    const q = qEl.value.trim().toLowerCase();
    const rows = d.data.filter((r) => (!chamber || r.chamber === chamber) && (!flipSel.value || r.flip_status === flipSel.value) &&
      (!q || (r.race_label + ' ' + r.candidates + ' ' + r.state).toLowerCase().includes(q)));
    window.ec.table(tbl, rows, {
      sort: 'race_label',
      empty: 'No races match these filters.',
      cols: {
        race_label: { render: (r) => [r.race_label, r.special ? ' ' : '', r.special ? badge('special') : ''], sortVal: (r) => r.chamber_order + r.race_label },
        candidates: { kind: 'cands' },
        incumbent_party: { render: (r) => [chip(r.incumbent_party, partyCls(r.incumbent_party)), r.open_seat ? ' open' : ''] },
        rating: { render: (r) => [ratingChip(r.rating), el('div', 'ec-note', (r.rating_source || '') + (r.cross_checks ? ' · ' + r.cross_checks : ''))] },
        rating_date: { kind: 'date' },
        previous_rating: { render: (r) => (r.previous_rating ? [ratingChip(r.previous_rating), el('div', 'ec-note', r.previous_rating_date ? 'set ' + fmtDate(r.previous_rating_date) : 'date set unknown')] : el('span', 'ec-note', 'not recorded')) },
        flip_status: { render: (r) => badge(r.flip_status, r.flip_status === 'Favors holding party' ? '' : 'warn') },
      },
    });
  }

  // ---------- scenario calculator ----------
  const calcSeg = document.getElementById('calc-chamber');
  let calcChamber = 'Senate';
  const picks = new Map();
  calcSeg.addEventListener('click', (ev) => {
    const b = ev.target.closest('button'); if (!b) return;
    calcChamber = b.dataset.v;
    calcSeg.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    drawCalc();
  });
  document.getElementById('calc-reset').addEventListener('click', () => {
    [...picks.keys()].forEach((k) => { if (k.startsWith(calcChamber + ':')) picks.delete(k); });
    drawCalc();
  });
  document.getElementById('calc-favored').addEventListener('click', () => {
    const d = dash.data('watch_races');
    if (d.status !== 'ok') return;
    d.data.filter((r) => r.chamber === calcChamber && (r.favored_party === 'D' || r.favored_party === 'R')).forEach((r) => picks.set(calcChamber + ':' + r.id, r.favored_party));
    drawCalc();
  });

  function drawCalc() {
    const res = document.getElementById('calc-result');
    const barBox = document.getElementById('calc-bar');
    const baseBox = document.getElementById('calc-baseline');
    const list = document.getElementById('calc-list');
    const b = dash.data('seat_baseline');
    const w = dash.data('watch_races');
    const ctl = dash.data('control');
    if (b.status !== 'ok' || w.status !== 'ok' || ctl.status !== 'ok') {
      [res, barBox, baseBox, list].forEach((x) => x.replaceChildren());
      if ([b, w, ctl].some((x) => x.status === 'error')) window.ec.fail(res);
      return;
    }
    if (window.ecTab !== 'congress') return;
    const cfg = ctl.data.find((r) => r.chamber === (calcChamber === 'Governor' ? 'Governors' : calcChamber)) || {};
    const base = b.data.filter((r) => r.chamber === calcChamber);
    const races = w.data.filter((r) => r.chamber === calcChamber).sort((a, b2) => a.race_label.localeCompare(b2.race_label));
    const tot = { D: 0, R: 0, I: 0 };
    base.forEach((r) => { tot[r.party] = (tot[r.party] || 0) + Number(r.seats); });
    let und = 0;
    races.forEach((r) => {
      const p = picks.get(calcChamber + ':' + r.id);
      if (p) tot[p] = (tot[p] || 0) + 1; else und += 1;
    });
    const total = Number(cfg.total_seats) || (tot.D + tot.R + tot.I + und);
    const maj = Number(cfg.majority) || null;
    const dSide = tot.D + (calcChamber === 'Senate' ? tot.I : 0);
    // result sentence
    const p = el('p', 'ec-take');
    const label = calcChamber === 'Governor' ? 'governors' : 'seats';
    p.append(el('b', 'ec-p-rep', `Republicans ${tot.R}`), ' · ', el('b', 'ec-p-dem', `Democrats ${tot.D}`));
    if (tot.I) p.append(` · Independents ${tot.I}`);
    p.append(` · Undecided ${und} ${label}`);
    const status = el('p', 'ec-note');
    if (calcChamber === 'Governor') status.textContent = 'Governorships have no chamber majority; this shows the resulting party split of the 50 governors.';
    else if (maj) {
      const rWin = tot.R >= maj, dWin = dSide >= maj;
      let s;
      if (rWin) s = `In this scenario Republicans hold at least ${maj} ${label}, a majority.`;
      else if (dWin) s = `In this scenario Democrats${tot.I && calcChamber === 'Senate' ? ' (with independents who caucus with them)' : ''} hold at least ${maj} ${label}, a majority.`;
      else if (und === 0 && calcChamber === 'Senate' && tot.R === dSide) s = 'A 50–50 tie. ' + (cfg.tie_rule || '');
      else s = `Neither party reaches ${maj} yet; ${und} watched ${und === 1 ? 'race is' : 'races are'} undecided.`;
      status.textContent = s + (calcChamber === 'Senate' && tot.I ? ' Independents are counted with the party they caucus with for majority purposes only.' : '');
    }
    res.replaceChildren(p, status);

    // bar
    barBox.replaceChildren();
    const W = barBox.clientWidth || 600, H = 64;
    const svg = d3.select(barBox).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`).attr('height', H);
    const x = d3.scaleLinear([0, total], [0, W]);
    const segs = [
      { k: 'D', n: tot.D, fill: 'var(--ec-dem)' },
      { k: 'I', n: tot.I, fill: 'var(--ec-dem)', op: 0.55 },
      { k: 'U', n: und, fill: 'var(--cds-chart-muted)' },
      { k: 'R', n: tot.R, fill: 'var(--ec-rep)' },
    ];
    let acc = 0;
    segs.forEach((s) => {
      if (!s.n) return;
      svg.append('rect').attr('x', x(acc)).attr('y', 18).attr('width', Math.max(0, x(acc + s.n) - x(acc) - 1)).attr('height', 22).attr('fill', s.fill).attr('opacity', s.op || 1);
      acc += s.n;
    });
    if (maj && calcChamber !== 'Governor') {
      [maj, total - maj].forEach((m, i) => {
        svg.append('line').attr('x1', x(m)).attr('x2', x(m)).attr('y1', 12).attr('y2', 46).attr('stroke', 'var(--cds-chart-reference)').attr('stroke-dasharray', '3 3');
      });
      svg.append('text').attr('x', x(maj)).attr('y', 9).attr('text-anchor', 'middle').attr('font-size', 11).attr('fill', 'var(--color-fg-muted)').text(maj + ' for majority');
    }
    svg.append('text').attr('x', 0).attr('y', 58).attr('font-size', 11).attr('fill', 'var(--ec-dem)').text('D ' + (tot.D + tot.I));
    svg.append('text').attr('x', W).attr('y', 58).attr('text-anchor', 'end').attr('font-size', 11).attr('fill', 'var(--ec-rep)').text('R ' + tot.R);
    if (und) svg.append('text').attr('x', x(tot.D + tot.I + und / 2)).attr('y', 58).attr('text-anchor', 'middle').attr('font-size', 11).attr('fill', 'var(--color-fg-muted)').text(und + ' undecided');

    // baseline explanation
    const ul = el('ul', 'ec-list');
    base.forEach((r) => {
      const li = el('li');
      li.append(el('span', r.party === 'R' ? 'ec-p-rep' : r.party === 'D' ? 'ec-p-dem' : '', `${r.party} ${fmt(+r.seats)}`), ' — ' + r.component + (r.assumption ? ' (assumption)' : '') + '. ');
      if (r.source_url) li.append(link('Source', r.source_url));
      ul.append(li);
    });
    baseBox.replaceChildren(el('div', 'ec-strong', 'Seats fixed in this scenario'), ul);

    // race pickers
    list.replaceChildren(...races.map((r) => {
      const row = el('div', 'ec-calc-row');
      const left = el('div');
      const t = el('div', '', r.race_label + ' '); t.append(ratingChip(r.rating));
      left.append(t, el('div', 'ec-note'));
      left.lastChild.append(...candNodes(r.candidates));
      const seg = el('div', 'ec-seg'); seg.setAttribute('role', 'group'); seg.setAttribute('aria-label', 'Winner in ' + r.race_label);
      const cur = picks.get(calcChamber + ':' + r.id) || '';
      [['D', 'D'], ['', '—'], ['R', 'R']].forEach(([v, txt]) => {
        const bt = el('button', '', txt);
        bt.setAttribute('aria-pressed', String(cur === v));
        bt.setAttribute('aria-label', v ? (v === 'D' ? 'Democrat wins' : 'Republican wins') : 'Undecided');
        if (v === 'D') bt.classList.add('ec-p-dem'); if (v === 'R') bt.classList.add('ec-p-rep');
        bt.addEventListener('click', () => { if (v) picks.set(calcChamber + ':' + r.id, v); else picks.delete(calcChamber + ':' + r.id); drawCalc(); });
        seg.append(bt);
      });
      row.append(left, seg);
      return row;
    }));
  }

  // ---------- polls & rating history ----------
  const raceSel = document.querySelector('.ec-poll-race');
  raceSel.addEventListener('change', () => { drawPolls(); drawHistory(); });
  const LEVELS = ['Solid D', 'Likely D', 'Lean D', 'Toss Up', 'Lean R', 'Likely R', 'Solid R'];
  const level = (r) => {
    const s = String(r || '').replace('Toss-up', 'Toss Up').replace(/Democrat(ic)?/, 'D').replace(/Republican/, 'R').replace('Leans', 'Lean').split(' (')[0].split(' · ')[0].trim();
    const i = LEVELS.indexOf(s);
    return i < 0 ? null : i;
  };

  function fillRaceSel() {
    const w = dash.data('watch_races'), p = dash.data('polls'), h = dash.data('rating_history');
    if (w.status !== 'ok') return;
    const withPolls = new Set((p.data || []).map((r) => r.race_id));
    const withHist = new Set((h.data || []).map((r) => r.race_id));
    const opts = w.data.filter((r) => withPolls.has(r.id) || withHist.has(r.id))
      .sort((a, b) => (a.chamber_order - b.chamber_order) || a.race_label.localeCompare(b.race_label));
    const cur = raceSel.value;
    raceSel.replaceChildren(...opts.map((r) => new Option(r.race_label + ' (' + r.chamber + ')' + (withPolls.has(r.id) ? '' : ' — no polls'), r.id)));
    if (opts.some((r) => r.id === cur)) raceSel.value = cur;
    else { const az = opts.find((r) => r.id === 'G-AZ'); if (az) raceSel.value = az.id; }
  }

  function drawPolls() {
    const box = document.getElementById('chart-polls');
    const tbl = document.getElementById('table-polls');
    const avgBox = document.getElementById('poll-avgs');
    const p = dash.data('polls');
    if (p.status === 'error') return window.ec.fail(box);
    if (p.status !== 'ok' || window.ecTab !== 'congress') return;
    const rid = raceSel.value;
    const rows = p.data.filter((r) => r.race_id === rid).sort((a, b) => String(b.field_end).localeCompare(String(a.field_end)));
    window.ec.table(tbl, rows, {
      sort: 'field_end', dir: -1, empty: 'No public polls recorded for this race since Sept 1.',
      cols: {
        field_end: { render: (r) => (r.field_start && r.field_start !== r.field_end ? fmtDate(r.field_start) + '–' + fmtDate(r.field_end) : fmtDate(r.field_end)) },
        pollster: { render: (r) => [link(r.pollster, r.url), r.verified_from && r.verified_from !== 'original' ? el('div', 'ec-note', 'via ' + r.verified_from) : '', r.note ? el('div', 'ec-note', r.note.length > 140 ? r.note.slice(0, 137) + '…' : r.note) : ''] },
        sponsor: { render: (r) => [r.sponsor || '—', r.sponsor_type && r.sponsor_type !== 'independent' ? ' ' : '', r.sponsor_type && r.sponsor_type !== 'independent' ? badge(r.sponsor_type, 'warn') : ''] },
        sample: { from: (r) => (isNum(r.sample_size) ? fmt(+r.sample_size) + ' ' : '') + (r.population || '') },
        result: { render: (r) => [el('span', 'ec-p-dem', `${r.d_name} ${r.d_pct}`), ', ', el('span', 'ec-p-rep', `${r.r_name} ${r.r_pct}`), r.other_text ? ', ' + r.other_text : ''] },
        moe: { from: (r) => (isNum(r.moe) ? String(r.moe) : 'n/r') },
      },
    });
    const a = dash.data('poll_avgs');
    const avgs = (a.status === 'ok' ? a.data : []).filter((r) => r.race_id === rid);
    avgBox.replaceChildren(...(avgs.length ? avgs.map((r) => {
      const s = el('div');
      s.append(el('b', '', 'Average · ' + r.aggregator + ' (' + fmtDate(r.date) + '): '), el('span', 'ec-p-dem', `${r.d_name} ${r.d_pct}`), ', ', el('span', 'ec-p-rep', `${r.r_name} ${r.r_pct}`), ' ', link('source', r.url));
      return s;
    }) : [el('span', '', 'No named polling average recorded for this race.')]));
    // chart: dots per poll, by field end date
    box.replaceChildren();
    if (!rows.length) return;
    const W = box.clientWidth || 500, H = 240, m = { t: 12, r: 70, b: 26, l: 36 };
    const svg = d3.select(box).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`).attr('height', H);
    const dates = rows.map((r) => new Date(r.field_end + 'T12:00:00Z'));
    const xd = d3.extent(dates);
    if (+xd[0] === +xd[1]) { xd[0] = d3.utcDay.offset(xd[0], -3); xd[1] = d3.utcDay.offset(xd[1], 3); }
    const x = d3.scaleUtc(xd, [m.l, W - m.r]);
    const vals = rows.flatMap((r) => [+r.d_pct, +r.r_pct]);
    const y = d3.scaleLinear([Math.floor(d3.min(vals) - 3), Math.ceil(d3.max(vals) + 3)], [H - m.b, m.t]);
    svg.append('g').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(Math.min(5, rows.length)).tickFormat(d3.utcFormat('%b %-d'))).attr('font-size', null).attr('font-family', null).call((g) => g.selectAll('text').attr('fill', 'var(--color-fg-muted)')).call((g) => g.selectAll('line,path').attr('stroke', 'var(--cds-chart-axis)'));
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - m.l - m.r))).attr('font-size', null).attr('font-family', null).call((g) => g.selectAll('text').attr('fill', 'var(--color-fg-muted)')).call((g) => g.select('.domain').remove()).call((g) => g.selectAll('line').attr('stroke', 'var(--cds-chart-grid)'));
    [['d_pct', 'var(--ec-dem)', 'D'], ['r_pct', 'var(--ec-rep)', 'R']].forEach(([f, col, pl]) => {
      svg.append('g').selectAll('circle').data(rows).join('circle')
        .attr('cx', (r) => x(new Date(r.field_end + 'T12:00:00Z'))).attr('cy', (r) => y(+r[f])).attr('r', 4.5)
        .attr('fill', col).attr('stroke', 'var(--color-panel)')
        .attr('data-source', 'polls').attr('data-field', f).attr('data-where', (r) => 'id=' + encodeURIComponent(r.id));
      const last = rows[0];
      svg.append('text').attr('x', W - m.r + 6).attr('y', y(+last[f])).attr('dy', '0.35em').attr('font-size', 11).attr('fill', col).text(pl + ' ' + (pl === 'D' ? last.d_name : last.r_name).split(' ').slice(-1)[0]);
    });
  }

  function drawHistory() {
    const box = document.getElementById('chart-history');
    const tbl = document.getElementById('table-history');
    const h = dash.data('rating_history'), w = dash.data('watch_races');
    if (h.status === 'error') return window.ec.fail(box);
    if (h.status !== 'ok' || w.status !== 'ok' || window.ecTab !== 'congress') return;
    const rid = raceSel.value;
    const race = w.data.find((r) => r.id === rid);
    const rows = h.data.filter((r) => r.race_id === rid).sort((a, b) => String(a.date).localeCompare(String(b.date)));
    window.ec.table(tbl, rows, { sort: 'date', dir: -1, empty: 'No verified rating changes recorded yet for this race.', cols: { date: { kind: 'date' }, from_rating: { kind: 'rating' }, to_rating: { render: (r) => [ratingChip(r.to_rating), ' ', link('source', r.source_url)] } } });
    box.replaceChildren();
    if (!race) return;
    // build steps: start at first known "from", then each change, end today at current rating
    const pts = [];
    let before = null;
    rows.forEach((r, i) => {
      const dt = new Date(r.date + 'T12:00:00Z');
      if (i === 0 && level(r.from_rating) !== null) before = { d: dt, v: level(r.from_rating) };
      if (level(r.to_rating) !== null) pts.push({ d: dt, v: level(r.to_rating), label: r.to_rating, known: true });
    });
    const now = new Date();
    if (level(race.rating) !== null) pts.push({ d: now, v: level(race.rating), label: race.rating, known: true });
    if (pts.length < 1) { box.append(el('p', 'ec-note', 'Not enough verified history to chart.')); return; }
    if (pts.length === 1 && !before) { box.append(el('p', 'ec-note', 'Only the current rating is recorded; changes will be charted as they are verified.')); return; }
    const W = box.clientWidth || 500, H = 230, m = { t: 10, r: 14, b: 26, l: 64 };
    const svg = d3.select(box).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`).attr('height', H);
    const xd = d3.extent(pts, (p) => p.d);
    if (+xd[0] === +xd[1]) xd[0] = d3.utcDay.offset(xd[0], -30);
    const x = d3.scaleUtc(xd, [m.l, W - m.r]);
    const y = d3.scalePoint(d3.range(7), [m.t, H - m.b]);
    svg.append('g').selectAll('line').data(d3.range(7)).join('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', (i) => y(i)).attr('y2', (i) => y(i)).attr('stroke', (i) => (i === 3 ? 'var(--cds-chart-reference)' : 'var(--cds-chart-grid)'));
    svg.append('g').selectAll('text').data(LEVELS).join('text').attr('x', m.l - 6).attr('y', (t, i) => y(i)).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-size', 11)
      .attr('fill', (t) => (/ D$/.test(t) ? 'var(--ec-dem)' : / R$/.test(t) ? 'var(--ec-rep)' : 'var(--color-fg-muted)')).text((t) => t);
    const longSpan = (xd[1] - xd[0]) > 200 * 864e5;
    svg.append('g').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(4).tickFormat(d3.utcFormat(longSpan ? '%b %Y' : '%b %-d'))).attr('font-size', null).attr('font-family', null).call((g) => g.selectAll('text').attr('fill', 'var(--color-fg-muted)')).call((g) => g.selectAll('line,path').attr('stroke', 'var(--cds-chart-axis)'));
    const line = d3.line().curve(d3.curveStepAfter).x((p) => x(p.d)).y((p) => y(p.v));
    svg.append('path').datum(pts).attr('d', line).attr('fill', 'none').attr('stroke', 'var(--color-fg)').attr('stroke-width', 2).attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round');
    svg.append('g').selectAll('circle').data(pts.filter((p) => p.known)).join('circle').attr('cx', (p) => x(p.d)).attr('cy', (p) => y(p.v)).attr('r', 3.5).attr('fill', 'var(--color-fg)');
    if (before) {
      const bx = x(before.d);
      svg.append('line').attr('x1', Math.max(m.l, bx - 28)).attr('x2', bx).attr('y1', y(before.v)).attr('y2', y(before.v)).attr('stroke', 'var(--color-fg-muted)').attr('stroke-width', 2).attr('stroke-dasharray', '3 3');
      svg.append('line').attr('x1', bx).attr('x2', bx).attr('y1', y(before.v)).attr('y2', y(pts[0].v)).attr('stroke', 'var(--color-fg)').attr('stroke-width', 2);
      svg.append('text').attr('x', Math.max(m.l, bx - 28)).attr('y', y(before.v) - 6).attr('font-size', 10).attr('fill', 'var(--color-fg-muted)').text('earlier: start date not recorded');
    }
  }

  function draw() {
    drawControl(); drawWatch(); drawCalc(); fillRaceSel(); drawPolls(); drawHistory();
  }
  dash.onData(draw);
  window.addEventListener('ec-tab', draw);
})();
