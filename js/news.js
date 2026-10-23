(() => {
  const { fmt, isNum, fmtDate, link, el, badge } = window.ec;
  const catSel = document.querySelector('.ec-brief-cat');
  const scopeEl = document.querySelector('.ec-news-scope');
  const qEl = document.querySelector('.ec-news-q');
  const chkSel = document.querySelector('.ec-check-status');
  const cmpSel = document.querySelector('.ec-cmp-metric');
  [catSel, scopeEl, chkSel, cmpSel].forEach((s) => s.addEventListener('change', () => draw()));
  qEl.addEventListener('input', () => draw());

  const fillSelect = (sel, values, allLabel) => {
    const cur = sel.value;
    sel.replaceChildren(...(allLabel ? [new Option(allLabel, '')] : []), ...values.map((v) => new Option(v, v)));
    sel.value = values.includes(cur) ? cur : (allLabel ? '' : values[0] || '');
  };

  function drawBriefing() {
    const d = dash.data('briefing');
    if (d.status !== 'ok') return;
    fillSelect(catSel, [...new Set(d.data.map((r) => r.category))].sort(), 'All types');
    const rows = d.data.filter((r) => !catSel.value || r.category === catSel.value);
    window.ec.table(document.getElementById('table-briefing'), rows, {
      sort: 'at', dir: -1,
      cols: { at: { kind: 'date' }, category: { render: (r) => badge(r.category) }, source_name: { render: (r) => [link(r.source_name, r.source_url), r.observed_at ? el('div', 'ec-note', 'observed ' + fmtDate(r.observed_at)) : ''] } },
    });
  }

  function drawHeadlines() {
    const h = dash.data('headlines');
    if (h.status !== 'ok') return;
    const scopes = [...new Set(h.data.map((r) => r.scope))].sort((a, b) => (a === 'National' ? -1 : b === 'National' ? 1 : a === 'AZ' ? -1 : b === 'AZ' ? 1 : a.localeCompare(b)));
    fillSelect(scopeEl, scopes, 'All places');
    const q = qEl.value.trim().toLowerCase();
    const filtered = h.data.filter((r) => (!scopeEl.value || r.scope === scopeEl.value) && (!q || (r.title + ' ' + r.outlet).toLowerCase().includes(q)));
    // group stories about the same event: keep the newest as the lead
    const groups = new Map();
    filtered.forEach((r) => { const k = r.event_id || r.id; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(r); });
    const leads = [...groups.values()].map((g) => { g.sort((a, b) => String(b.date).localeCompare(String(a.date))); const lead = g[0]; lead._more = g.slice(1); return lead; });
    window.ec.table(document.getElementById('table-headlines'), leads, {
      sort: 'date', dir: -1,
      cols: {
        date: { kind: 'date' },
        title: { render: (r) => [link(r.title, r.url), r._more.length ? el('div', 'ec-note', 'Also: ' + r._more.map((m) => m.outlet).join(', ')) : ''] },
      },
    });
  }

  function drawChecks() {
    const c = dash.data('checks');
    if (c.status !== 'ok') return;
    const rows = c.data.filter((r) => !chkSel.value || r.status === chkSel.value);
    window.ec.table(document.getElementById('table-checks'), rows, {
      sort: 'run_at', dir: -1,
      cols: {
        run_at: { kind: 'date' },
        status: { render: (r) => [badge(r.status === 'unreachable' ? 'could not retrieve' : r.status === 'checked' ? 'read OK' : r.status, r.status === 'changed' || r.status === 'checked' ? 'ok' : r.status === 'unreachable' || r.status === 'error' || r.status === 'timed out' ? 'bad' : r.status === 'skipped (time budget)' ? 'warn' : ''), r.attempt > 1 ? el('div', 'ec-note', 'attempt ' + r.attempt) : ''] },
        seconds: { kind: 'num' },
      },
    });
    const k = dash.data('corrections');
    if (k.status === 'ok') window.ec.table(document.getElementById('table-corrections'), k.data, {
      sort: 'at', dir: -1, empty: 'No corrections recorded yet.',
      cols: { at: { kind: 'date' }, previous: { render: (r) => (isNum(r.previous) ? fmt(+r.previous) : r.previous) }, replacement: { render: (r) => (isNum(r.replacement) ? fmt(+r.replacement) : r.replacement) }, explanation: { render: (r) => [r.explanation + ' ', link('source', r.source_url)] } },
    });
  }

  function drawSources() {
    const sp = dash.data('source_registry');
    if (sp.status === 'ok') window.ec.table(document.getElementById('table-primary'), sp.data, { cols: {
      measure: { render: (r) => [r.measure, r.why ? el('div', 'ec-note', r.why) : ''] },
      primary_class: { render: (r) => badge(r.primary_class === 'manual' ? 'needs manual' : r.primary_class, r.primary_class === 'official' ? 'ok' : r.primary_class === 'manual' ? 'warn' : '') },
      stale_after_hours: { kind: 'num' } } });
    const sc = dash.data('source_compare');
    if (sc.status === 'ok') {
      const metrics = [...new Set(sc.data.map((r) => r.metric))];
      fillSelect(cmpSel, metrics, null);
      const rows = sc.data.filter((r) => r.metric === cmpSel.value);
      window.ec.table(document.getElementById('table-compare'), rows, {
        sort: 'value', dir: -1,
        cols: {
          source: { render: (r) => [link(r.source, r.url), r.primary ? ' ' : '', r.primary ? badge('primary') : ''] },
          value: { kind: 'num', render: (r) => (isNum(r.value) ? (r.precision === 'rounded' ? '≈' : '') + fmt(+r.value) : (r.value_text || '—')) },
          observed_at: { kind: 'date' },
          classification: { render: (r) => badge(r.classification, r.classification === 'official' ? 'ok' : '') },
        },
      });
    }
    const pv = dash.data('provenance');
    const pbox = document.getElementById('prov-list');
    if (pv.status === 'ok') pbox.replaceChildren(...pv.data.map((r) => { const li = el('li'); li.append(el('b', '', r.source + ': '), r.underlying_provider + '. ' + (r.evidence || '') + ' '); if (r.url) li.append(link('Source', r.url)); return li; }));
    const g = dash.data('glossary');
    const gbox = document.getElementById('glossary');
    if (g.status === 'ok') { gbox.replaceChildren(); g.data.forEach((r) => gbox.append(el('dt', '', r.term), el('dd', '', r.definition))); }
  }

  function drawReviews() {
    const r = dash.data('reviews');
    if (r.status !== 'ok') return;
    const kinds = { decrease: 'Unexplained decrease', same_period_change: 'Same period, different figure', jump: 'Very large jump', over_registration: 'Above registered voters' };
    window.ec.table(document.getElementById('table-reviews'), r.data.filter((x) => x.id !== '_none'), {
      sort: 'first_seen', dir: -1, empty: 'Nothing under review.',
      cols: {
        first_seen: { kind: 'date' }, kind: { render: (x) => kinds[x.kind] || x.kind },
        published_value: { kind: 'num' }, observed_value: { kind: 'num', render: (x) => [fmt(+x.observed_value), el('div', 'ec-note', 'as of ' + fmtDate(x.observed_as_of))] },
        reads: { kind: 'num' },
        status: { render: (x) => [badge(x.status, x.status === 'open' ? 'warn' : x.status === 'confirmed' ? 'ok' : ''), x.resolution ? el('div', 'ec-note', x.resolution) : ''] },
        geography: { render: (x) => [x.geography, el('div', 'ec-note', x.source)] },
      },
    });
  }

  function drawSrcStatus() {
    const r = dash.data('source_status');
    if (r.status !== 'ok') return;
    window.ec.table(document.getElementById('table-srcstatus'), r.data, {
      sort: 'source', dir: 1,
      cols: {
        source: { render: (x) => [x.source, el('div', 'ec-note', x.class)] },
        last_success: { kind: 'date' }, latest_observation: { render: (x) => [fmtDate(x.latest_observation) || '—', x.date_basis_note ? el('div', 'ec-note', x.date_basis_note) : ''] },
        last_status: { render: (x) => [badge(x.last_status || 'not yet checked', x.last_status === 'read OK' ? 'ok' : /unreach|error|timed/.test(x.last_status || '') ? 'bad' : ''), x.fail_streak > 1 ? el('div', 'ec-note', 'failed ' + x.fail_streak + ' runs in a row') : ''] },
        limitation: { render: (x) => [x.limitation || '—', x.next_fix ? el('div', 'ec-note', 'Next fix: ' + x.next_fix) : '',
          x.table_coverage ? el('div', 'ec-note', 'Table: ' + x.table_coverage) : '', x.skips_total ? el('div', 'ec-note', 'Skipped by time limit ' + x.skips_total + ' time(s)') : ''] },
      },
    });
  }

  function drawRuns() {
    const r = dash.data('run_log');
    if (r.status === 'ok') window.ec.table(document.getElementById('table-runs'), r.data.slice(0, 72), {
      sort: 'scheduled_for', dir: -1,
      cols: {
        scheduled_for: { kind: 'date' }, started_at: { kind: 'date' },
        published_at: { render: (x) => [x.published_at ? fmtDate(x.published_at) : (x.completed_at ? 'completed ' + fmtDate(x.completed_at) : '—'),
          x.on_time === true ? el('div', 'ec-note', 'on target') : x.on_time === false ? el('div', 'ec-note', 'after target ' + fmtDate(x.publish_target)) : ''] },
        status: { render: (x) => [badge(x.completed_at && x.status !== 'missed' && !String(x.status).startsWith('not published') ? (x.published_at ? 'published' : 'completed') : x.status,
                                         x.status === 'missed' || String(x.status).startsWith('not published') ? 'bad' : 'ok'),
                                   x.coverage_health ? ' ' : '', x.coverage_health ? badge('coverage ' + x.coverage_health + (x.key_figures_confirmed ? ' · ' + x.key_figures_confirmed + ' key' : ''),
                                                                                         x.coverage_health === 'good' ? 'ok' : x.coverage_health === 'partial' ? 'warn' : 'bad') : '',
                                   x.toolkit_version ? el('div', 'ec-note', 'v' + x.toolkit_version) : ''] },
        sources_checked: { kind: 'num', render: (x) => (isNum(x.sources_checked) ? (isNum(x.sources_ok) ? `${x.sources_ok}/${x.sources_checked}` : String(x.sources_checked)) : '') },
        figures_updated: { kind: 'num' }, figures_rejected: { kind: 'num' }, retained: { kind: 'num' },
        unresolved: { render: (x) => [x.unresolved || '', x.updated ? el('div', 'ec-note', 'Updated: ' + x.updated) : '',
          x.retained_stale ? el('div', 'ec-note', 'Kept, stale: ' + x.retained_stale) : '', x.sources_no_new_data ? el('div', 'ec-note', 'Reached, no new data: ' + x.sources_no_new_data) : '',
          x.rejected_reasons ? el('div', 'ec-note', 'Rejected: ' + x.rejected_reasons) : '',
          x.tables ? el('div', 'ec-note', 'Tables: ' + x.tables) : '', x.coverage_breakdown ? el('div', 'ec-note', 'Coverage: ' + x.coverage_breakdown) : '', x.az_results_export ? el('div', 'ec-note', 'AZ results export: ' + x.az_results_export) : ''] },
      },
    });
    const sc = dash.data('source_changes');
    if (sc.status === 'ok') window.ec.table(document.getElementById('table-srcchg'), sc.data, {
      sort: 'at', dir: -1, empty: 'No source changes yet.',
      cols: {
        at: { kind: 'date' },
        from_source: { render: (x) => [x.from_source, el('div', 'ec-note', (isNum(x.from_value) ? fmt(+x.from_value) : '—') + ' · as of ' + fmtDate(x.from_as_of))] },
        to_source: { render: (x) => [x.to_source, el('div', 'ec-note', (isNum(x.to_value) ? fmt(+x.to_value) : '—') + ' · as of ' + fmtDate(x.to_as_of))] },
        reason: { render: (x) => [x.reason, el('div', 'ec-note', x.note || '')] },
      },
    });
  }

  function draw() { drawBriefing(); drawHeadlines(); drawChecks(); drawRuns(); drawReviews(); drawSrcStatus(); drawSources(); }
  dash.onData(draw);
})();
