(() => {
  // Public Gilbert-area ballot guide: contests grouped by the jurisdiction that votes on them, candidate comparisons,
  // ballot measures and judicial retention. Read-only; nothing here is tied to any one voter.
  const { el, link, badge, partyCls, fmtDate } = window.ec;
  const jurSel = document.querySelector('.ec-local-jur');
  const qEl = document.querySelector('.ec-local-q');
  jurSel.addEventListener('change', () => draw());
  qEl.addEventListener('input', () => draw());

  const field = (label, why, content) => {
    const f = el('div', 'ec-field');
    const k = el('div', 'ec-field-k', label);
    if (why) k.append(el('span', 'ec-field-why', ' · ' + why));
    f.append(k, content);
    return f;
  };
  const listOrNF = (items, nf) => {
    if (!items || !items.length) return el('div', 'ec-nf', nf);
    const ul = el('ul', 'ec-list');
    items.forEach((it) => {
      const li = el('li', '', it.text + ' ');
      (it.sources || []).slice(0, 2).forEach((s, i) => { const a = link(i ? '[' + (i + 1) + ']' : 'source', s); a.className = 'ec-src'; li.append(a, ' '); });
      ul.append(li);
    });
    return ul;
  };
  function candCard(c) {
    const card = el('div', 'ec-cand');
    card.append(el('div', 'ec-cand-name', c.name));
    const meta = el('div', 'ec-note');
    meta.append(el('span', 'ec-p-' + partyCls(c.party), c.party || 'No party listed'), c.incumbent ? ' · incumbent' : '', c.write_in ? ' · write-in' : '');
    card.append(meta);
    if (c.running_mate) card.append(el('div', 'ec-note', 'Running mate: ' + c.running_mate));
    card.append(field('Current or recent office', 'documented', c.office ? el('div', '', c.office) : el('div', 'ec-nf', 'Not found')));
    const exp = el('div');
    if (c.experience && c.experience.text) {
      exp.append(c.experience.text + ' ');
      (c.experience.sources || []).slice(0, 2).forEach((s) => { const a = link('source', s); a.className = 'ec-src'; exp.append(a, ' '); });
    } else exp.append(el('span', 'ec-nf', 'Not found'));
    card.append(field('Experience', 'documented', exp));
    card.append(field('Stated positions', "candidate's own words or campaign claims", listOrNF(c.positions, 'No campaign statement found')));
    card.append(field('Public record', 'independent sources', listOrNF(c.record, 'No independent record found')));
    if (c.links && c.links.length) {
      const l = el('div', 'ec-controls');
      c.links.slice(0, 4).forEach((x) => l.append(link(x.label, x.url)));
      card.append(field('Links', null, l));
    }
    return card;
  }

  function drawInfo() {
    const d = dash.data('local_info');
    if (d.status !== 'ok' || !d.data[0]) return;
    const r = d.data[0];
    const box = document.getElementById('local-districts');
    const dl = el('dl', 'ec-kv');
    r.districts.forEach((x) => { const dd = el('dd'); dd.append(x.name, x.note ? el('span', 'ec-note', ' — ' + x.note) : ''); dl.append(el('dt', '', x.type), dd); });
    box.replaceChildren(dl);
    document.getElementById('local-note').replaceChildren(r.note + ' ', link('Maricopa County: what is on the ballot', r.source_url));
    const lk = dash.data('local_links');
    if (lk.status === 'ok') document.getElementById('local-links').replaceChildren(...lk.data.map((x) => { const a = link(x.label, x.url); a.className = 'ec-linkbtn'; if (x.purpose) a.title = x.purpose; return a; }));
  }

  function drawContests() {
    const box = document.getElementById('local-contests');
    const d = dash.data('local_contests');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok') return;
    const jurs = [...new Set(d.data.map((c) => c.jurisdiction))];
    const cur = jurSel.value;
    jurSel.replaceChildren(new Option('All jurisdictions', ''), ...jurs.map((j) => new Option(j, j)));
    jurSel.value = jurs.includes(cur) ? cur : '';
    const q = qEl.value.trim().toLowerCase();
    const rows = d.data.slice().sort((a, b) => (a.order || 0) - (b.order || 0)).filter((c) => (!jurSel.value || c.jurisdiction === jurSel.value) &&
      (!q || JSON.stringify([c.office, c.jurisdiction, c.choices, (c.candidates || []).map((x) => x.name)]).toLowerCase().includes(q)));
    if (!rows.length) { box.replaceChildren(el('p', 'ec-note', 'No contests match these filters.')); return; }
    box.replaceChildren(...rows.map((c) => {
      const p = el('article', 'ec-card ec-contest'); p.id = 'contest-' + c.id;
      const head = el('div', 'ec-row');
      head.append(el('h3', '', c.office), badge(c.jurisdiction));
      p.append(head);
      const where = el('p', 'ec-note');
      where.append(el('b', '', 'Appears on: '), c.appears_on + (c.vote_for ? ' · Vote for ' + c.vote_for : ''));
      p.append(where);
      if (c.note) p.append(el('p', 'ec-note', c.note));
      if (c.candidates && c.candidates.length) {
        const g = el('div', 'ec-cands');
        c.candidates.forEach((x) => g.append(candCard(x)));
        p.append(g);
        if (c.candidates.length > 1) p.append(el('p', 'ec-note', 'Scroll sideways to compare candidates. Every candidate gets the same fields.'));
      } else if (c.choices) p.append(el('p', '', c.choices));
      return p;
    }));
  }

  function drawMeasures() {
    const box = document.getElementById('prop-cards');
    const d = dash.data('local_measures');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok') return;
    box.replaceChildren(...d.data.map((m) => {
      const card = el('div', 'ec-card'); card.id = 'prop-' + m.id;
      const h = el('div', 'ec-row');
      h.append(el('h3', '', `Prop ${m.number}: ${m.title}`), badge(m.jurisdiction));
      card.append(h, el('p', 'ec-note', 'Appears on: ' + m.appears_on + (m.type ? ' · ' + m.type : '')));
      const dl = el('dl', 'ec-kv');
      const add = (k, v) => { if (!v) return; const dd = el('dd'); dd.append(...[].concat(v)); dl.append(el('dt', '', k), dd); };
      add('A Yes vote', m.yes_means || el('span', 'ec-nf', 'Not found'));
      add('A No vote', m.no_means || el('span', 'ec-nf', 'Not found'));
      add('Fiscal effect', m.fiscal_impact ? [m.fiscal_impact, m.fiscal_source ? el('span', 'ec-note', ' (' + m.fiscal_source + ')') : ''] : el('span', 'ec-nf', m.fiscal_note || 'No official fiscal analysis found'));
      add('Takes effect', m.effective_date || el('span', 'ec-nf', 'Not stated in official materials'));
      if (m.analysis) add('Official analysis', [m.analysis, ' ', m.analysis_url ? link('Legislative Council', m.analysis_url) : '']);
      if (m.arguments_note) add('Arguments', m.arguments_note);
      card.append(dl);
      const links = el('div', 'ec-controls');
      [['Full text', m.text_url], ['Publicity pamphlet' + (m.pamphlet_page ? ' (p. ' + m.pamphlet_page + ')' : ''), m.pamphlet_url], ['Arguments for', m.for_url], ['Arguments against', m.against_url]]
        .forEach(([t, u]) => { if (u) { const a = link(t, u); a.className = 'ec-linkbtn'; links.append(a); } });
      card.append(links);
      return card;
    }));
  }

  function drawJudicial() {
    const box = document.getElementById('local-judicial');
    const d = dash.data('local_judicial');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok' || !d.data[0]) return;
    const j = d.data[0];
    const out = [];
    if (j.overview) out.push(el('p', 'ec-note', j.overview));
    const top = el('div', 'ec-controls');
    (j.pages || []).forEach((x) => { const a = link(x.label, x.url); a.className = 'ec-linkbtn'; top.append(a); });
    out.push(top);
    j.groups.forEach((g) => {
      const card = el('div', 'ec-card');
      const head = el('div', 'ec-row');
      head.append(el('h3', '', `${g.court} (${g.judges.length})`), badge(g.jurisdiction));
      card.append(head, el('p', 'ec-note', 'Appears on: ' + g.appears_on));
      const det = el('details', 'ec-more');
      det.append(el('summary', '', 'Judges and official performance evaluations'));
      const ul = el('ul', 'ec-list');
      g.judges.forEach((x) => { const li = el('li', '', x.name + ' — ' + (x.finding || 'finding not listed') + ' '); if (x.report_url) li.append(link('evaluation', x.report_url)); ul.append(li); });
      det.append(ul);
      card.append(det);
      out.push(card);
    });
    box.replaceChildren(...out);
  }

  function draw() { drawInfo(); drawContests(); drawMeasures(); drawJudicial(); }
  dash.onData(draw);
  window.addEventListener('ec-tab', draw);
})();
