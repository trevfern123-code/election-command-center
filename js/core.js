(() => {
  const fmt = d3.format(',');
  const badge = (text, kind) => { const e = document.createElement('span'); e.className = 'ec-badge' + (kind ? ' ' + kind : ''); e.textContent = text; return e; };
  const fmtCompact = (v) => (Math.abs(v) >= 10000 ? new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(v) : fmt(v));
  const isNum = (v) => v !== null && v !== undefined && v !== '' && typeof v !== 'boolean' && !isNaN(Number(v));
  const AZ = 'America/Phoenix';
  const todayAZ = () => new Date().toLocaleDateString('en-CA', { timeZone: AZ });
  const fmtDate = (s) => {
    if (!s) return '';
    const t = String(s);
    if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return d3.utcFormat('%b %-d')(new Date(t + 'T00:00:00Z'));
    const d = new Date(t);
    if (isNaN(d)) return t;
    return d.toLocaleString('en-US', { timeZone: AZ, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' AZ';
  };
  const ageHours = (s) => {
    if (!s) return Infinity;
    const t = String(s);
    const d = /^\d{4}-\d{2}-\d{2}$/.test(t) ? new Date(t + 'T12:00:00-07:00') : new Date(t);
    return isNaN(d) ? Infinity : (Date.now() - d.getTime()) / 3.6e6;
  };
  const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);
  const link = (text, url) => {
    const u = safeUrl(url);
    if (!u) { const s = document.createElement('span'); s.textContent = text ?? ''; return s; }
    const a = document.createElement('a');
    a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = text;
    return a;
  };
  const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return 'link'; } };
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };

  // rating → class: party favored by the rating, or toss-up
  const ratingClass = (v) => {
    const first = String(v || '').split(' (')[0].split(' · ')[0];
    if (/^toss/i.test(first)) return 'tossup';
    if (/\b(D|Dem|Democrat|Democratic)\b/.test(first)) return 'dem';
    if (/\b(R|Rep|Republican)\b/.test(first)) return 'rep';
    return '';
  };
  const partyCls = (p) => {
    const s = String(p || '').trim();
    if (/^(D|Dem|Democrat|Democratic)\b/.test(s)) return 'dem';
    if (/^(R|Rep|Republican)\b/.test(s)) return 'rep';
    return '';
  };
  const chip = (text, cls) => { const s = el('span', 'ec-chip' + (cls ? ' ' + cls : ''), text); return s; };
  const ratingChip = (v) => chip(v, ratingClass(v));
  const candNodes = (text) => String(text ?? '').split(/(; | — )/).map((part) => {
    const sp = el('span', '', part);
    if (part !== '; ' && part !== ' — ') {
      const m = /\(([^,)]+)/.exec(part);
      const c = m ? partyCls(m[1]) : '';
      if (c) sp.className = 'ec-p-' + c;
    }
    return sp;
  });
  const bgBadge = () => { const b = el('span', 'ec-bg-badge', 'BG'); b.setAttribute('aria-label', 'battleground state'); return b; };

  const STATUS_TEXT = {
    'changed': ['Updated this hour', 'ok'], 'corrected': ['Corrected this hour', 'warn'], 'no change': ['Checked, no newer data', ''],
    'unreachable': ['Source unreachable — last verified figure kept', 'bad'], 'error': ['Source error — last verified figure kept', 'bad'],
    'rejected': ['New figure failed validation — last verified figure kept', 'warn'], 'not found on source': ['Source read, figure not listed', 'warn'],
    'not checked this run': ['Not checked this hour', ''], 'under review': ['Under review — published figure kept', 'warn'],
    'no new data from source': ['Source reached, no new data', ''], 'stale source response; freshness not verified this run': ['Stale source response — freshness not verified this run', 'warn'], 'official kept; tracker figure shown separately': ['Official figure kept; tracker figure not confirmed', 'warn'], 'skipped (time budget)': ['Skipped this hour (time limit) — last verified figure kept', 'warn'],
    'permission-blocked (no approval in an unattended run)': ['Not read this hour: the source can only be read with an approval an unattended run cannot get — last verified figure kept', 'warn'],
  };
  const freshBadges = (row) => {
    const out = [];
    if (!row) return out;
    const st = STATUS_TEXT[row.check_status];
    if (st) out.push(badge(st[0], st[1]));
    if (row.stale) { out.push(' '); out.push(badge('Stale data', 'bad')); }
    if (row.precision === 'calculated') { out.push(' '); out.push(badge('Calculated')); }
    // a failed or incomplete check is never shown as freshly verified: say when it was tried and when the figure was last confirmed
    const failedCheck = ['unreachable', 'error', 'rejected', 'not found on source'].includes(row.check_status);
    if (failedCheck && row.last_attempt) out.push(el('span', 'ec-review', `Check at ${fmtDate(row.last_attempt)} did not confirm this figure; last successful check ${row.last_checked ? fmtDate(row.last_checked) : 'none yet'}.`));
    if (['skipped (time budget)', 'not checked this run'].includes(row.check_status)) out.push(el('span', 'ec-review', ` Last successful check ${row.last_checked ? fmtDate(row.last_checked) : 'none yet'}.`));
    if (row.check_status === 'no change' && row.last_checked) out.push(el('span', 'ec-note', ` Confirmed unchanged at ${fmtDate(row.last_checked)}; data still as of ${fmtDate(row.as_of)}.`));
    if (row.tracker_value_since && row.as_of && row.tracker_value_since < String(row.as_of).slice(0, 10)) out.push(el('span', 'ec-review', `Tracker value unchanged since ${fmtDate(row.tracker_value_since)} (carried forward under a newer page date).`));
    if (row.stale_response) out.push(el('span', 'ec-review', `${row.stale_response.source} answered with data as of ${fmtDate(row.stale_response.shown_as_of)}, older than the figure shown: stale source response; freshness not verified this run. The figure shown and its source are kept.`));
    if (row.update_kind && row.check_status === 'changed') out.push(el('span', 'ec-note', ' ' + row.update_kind.charAt(0).toUpperCase() + row.update_kind.slice(1) + '.'));
    if (row.tracker_alt) out.push(el('span', 'ec-review', `Tracker: ${row.tracker_alt.source} shows ${fmt(+row.tracker_alt.value)} (page dated ${fmtDate(row.tracker_alt.page_date)}; not an observation date, not confirmed by an official report).`));
    if (row.review) {
      const kinds = { decrease: 'unexplained decrease', same_period_change: 'changed figure for the same period', jump: 'unusually large jump', over_registration: 'above registered voters' };
      out.push(el('span', 'ec-review', `${row.review.source} now shows ${fmt(+row.review.observed_value)} (as of ${fmtDate(row.review.observed_as_of)}): ${kinds[row.review.kind] || row.review.kind}; re-checked hourly, ${row.review.reads} read${row.review.reads === 1 ? '' : 's'} so far.`));
    }
    return out;
  };
  window.ec = { freshBadges, fmt, fmtCompact, isNum, fmtDate, ageHours, todayAZ, safeUrl, link, hostOf, el, ratingClass, partyCls, chip, ratingChip, candNodes, badge, bgBadge };

  // ---------- tabs ----------
  const tabs = [...document.querySelectorAll('.ec-tab')];
  const pages = [...document.querySelectorAll('.ec-page')];
  const show = (name, scroll) => {
    tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
    pages.forEach((p) => { p.hidden = p.dataset.page !== name; });
    window.ecTab = name;
    dash.setLink(name);
    window.dispatchEvent(new Event('ec-tab'));
    if (scroll) document.querySelector('.ec-tabs').scrollIntoView({ block: 'start' });
  };
  window.ec.show = show;
  tabs.forEach((t) => t.addEventListener('click', () => show(t.dataset.tab)));
  document.querySelectorAll('[data-goto]').forEach((b) => b.addEventListener('click', () => show(b.dataset.goto, true)));
  const initial = location.hash.slice(1);
  show(pages.some((p) => p.id === initial) ? initial : 'states');

  // ---------- generic table ----------
  const states = new WeakMap();
  window.ec.table = (tbl, rows, opts) => {
    const st = states.get(tbl) || { sort: opts.sort || null, dir: opts.dir || 1 };
    states.set(tbl, st);
    const ths = [...tbl.querySelectorAll('thead th')];
    tbl._rows = rows; tbl._opts = opts;
    if (!tbl.dataset.wired) {
      tbl.dataset.wired = '1';
      ths.forEach((th) => th.addEventListener('click', () => {
        const f = th.dataset.field;
        const o = tbl._opts;
        st.dir = st.sort === f ? -st.dir : (['num', 'pct'].includes(o.cols[f]?.kind) ? -1 : 1);
        st.sort = f;
        window.ec.table(tbl, tbl._rows || [], o);
      }));
      if (opts.onRow) tbl.querySelector('tbody').addEventListener('click', (ev) => {
        if (ev.target.closest('a')) return;
        const tr = ev.target.closest('tr[data-key]');
        if (tr) tbl._opts.onRow(tr.dataset.key);
      });
    }
    ths.forEach((th) => th.setAttribute('aria-sort', th.dataset.field === st.sort ? (st.dir > 0 ? 'ascending' : 'descending') : 'none'));
    const list = rows.slice();
    if (st.sort) {
      const c = opts.cols[st.sort] || {};
      const key = c.sortField || st.sort;
      const val = (r) => (c.sortVal ? c.sortVal(r) : r[key]);
      list.sort((a, b) => {
        let x = val(a), y = val(b);
        if (['num', 'pct'].includes(c.kind) || c.numericSort) { x = isNum(x) ? +x : -Infinity; y = isNum(y) ? +y : -Infinity; return (x - y) * st.dir; }
        return String(x ?? '').localeCompare(String(y ?? '')) * st.dir;
      });
    }
    const keyCols = tbl.dataset.rowKey.split(',');
    const tbody = tbl.querySelector('tbody');
    if (!list.length && opts.empty) {
      const tr = el('tr'); const td = el('td', 'ec-note', opts.empty); td.colSpan = ths.length; tr.append(td);
      tbody.replaceChildren(tr);
      return;
    }
    tbody.replaceChildren(...list.map((r) => {
      const tr = el('tr');
      tr.dataset.key = keyCols.map((k) => (keyCols.length > 1 ? encodeURIComponent(r[k]) : r[k])).join(',');
      if (opts.onRow) tr.classList.add('ec-click');
      if (opts.selected && opts.selected(r)) tr.classList.add('ec-sel');
      ths.forEach((th) => {
        const f = th.dataset.field;
        const c = opts.cols[f] || {};
        const td = el('td');
        if (th.classList.contains('n')) td.className = 'n';
        if (th.classList.contains('ec-hide-narrow')) td.classList.add('ec-hide-narrow');
        const v = c.from ? c.from(r) : r[f];
        if (c.render) { const out = c.render(r, v); if (out !== null && out !== undefined) td.append(...[].concat(out)); }
        else if (c.kind === 'num') td.textContent = isNum(v) ? fmt(+v) : '';
        else if (c.kind === 'pct') td.textContent = isNum(v) ? d3.format('.1%')(+v) : '';
        else if (c.kind === 'date') td.textContent = fmtDate(v);
        else if (c.kind === 'link') td.append(link(v ?? '', r[c.linkField]));
        else if (c.kind === 'urlcell') { if (safeUrl(v)) td.append(link(hostOf(v), v)); }
        else if (c.kind === 'rating') { if (v) td.append(ratingChip(v)); }
        else if (c.kind === 'party') { if (v) td.append(chip(v, partyCls(v))); }
        else if (c.kind === 'cands') td.append(...candNodes(v));
        else td.textContent = v ?? '';
        tr.append(td);
      });
      return tr;
    }));
  };

  window.ec.fail = (box) => { const d = el('div', 'ec-fail', 'Failed to load'); box.replaceChildren(d); };
  window.ec.skel = (node, on) => node.classList.toggle('dash-skeleton', on);

  // fill single-value marks for a dataset
  window.ec.fill = (id, fmtFn) => {
    const d = dash.data(id);
    document.querySelectorAll(`[data-source="${id}"][data-field]`).forEach((node) => {
      if (node.tagName === 'TH' || node.closest('svg') || node.closest('table')) return;
      if (node.dataset.row === undefined && !node.dataset.where) return;
      window.ec.skel(node, d.status === 'loading');
      if (d.status !== 'ok') { node.textContent = '—'; return; }
      let row;
      if (node.dataset.row !== undefined) row = d.data[+node.dataset.row];
      else {
        const conds = node.dataset.where.split('&').map((p) => p.split('=').map(decodeURIComponent));
        row = d.data.find((r) => conds.every(([k, v]) => String(r[k]) === v));
      }
      const v = row ? row[node.dataset.field] : null;
      node.textContent = v === null || v === undefined || v === '' ? '—' : (fmtFn || window.ec.valFmt)(node.dataset.field, v, node);
    });
  };
  window.ec.valFmt = (f, v, node) => {
    if (/(^as_of$|_at$|^at$|date|checked|attempt)/.test(f)) return fmtDate(v);
    if (isNum(v)) return fmt(+v);
    return String(v);
  };

  // ---------- overview ----------
  const winSel = document.querySelector('.ec-brief-win');
  winSel.addEventListener('change', () => drawOverview());

  function drawDeadline() {
    const box = document.getElementById('ov-deadline-body');
    const d = dash.data('az_dates');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok') { box.replaceChildren(); return; }
    const today = todayAZ();
    const rows = d.data.filter((r) => r.date >= today && r.voter_action).sort((a, b) => a.date.localeCompare(b.date));
    const next = rows[0];
    if (!next) { box.replaceChildren(el('p', 'ec-note', 'No upcoming voter deadlines.')); return; }
    const days = Math.round((new Date(next.date + 'T00:00:00Z') - new Date(today + 'T00:00:00Z')) / 864e5);
    const when = days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : 'In ' + days + ' days';
    const big = el('div', 'ec-deadline-date', fmtDate(next.date));
    const lab = el('div', 'ec-strong'); lab.append(link(next.label, next.source_url));
    const sub = el('p', 'ec-note', when + (next.detail ? ' · ' + next.detail : ''));
    const after = rows.slice(1, 3).map((r) => el('p', 'ec-note', 'Then ' + fmtDate(r.date) + ': ' + r.label));
    box.replaceChildren(big, lab, sub, ...after);
  }

  function drawBrief() {
    const box = document.getElementById('ov-brief-list');
    const d = dash.data('briefing');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok') { box.replaceChildren(); return; }
    const hrs = +winSel.value;
    let rows = d.data.filter((r) => ageHours(r.at) <= hrs).sort((a, b) => (b.importance - a.importance) || String(b.at).localeCompare(String(a.at))).slice(0, 5);
    if (!rows.length) {
      box.replaceChildren(el('li', 'ec-note', 'No meaningful developments logged in this window. Routine checks are in the refresh log.'));
      return;
    }
    box.replaceChildren(...rows.map((r) => {
      const li = el('li');
      const top = el('div', 'ec-row');
      top.append(badge(r.category), el('span', 'ec-note', fmtDate(r.at)));
      li.append(top, el('span', 'what', r.what_changed));
      if (r.why_it_matters) li.append(el('span', 'why', r.why_it_matters));
      const src = el('span', 'src', 'Source: '); src.append(link(r.source_name || 'link', r.source_url));
      if (r.observed_at) src.append(' · observed ' + fmtDate(r.observed_at));
      li.append(src);
      return li;
    }));
  }

  function drawWatch() {
    const box = document.getElementById('ov-watch-body');
    const d = dash.data('watch_races');
    if (d.status === 'error') return window.ec.fail(box);
    if (d.status !== 'ok') { box.replaceChildren(); return; }
    const rows = d.data.filter((r) => r.default_watch).sort((a, b) => (a.chamber_order - b.chamber_order) || a.race_label.localeCompare(b.race_label));
    const ul = el('ul', 'ec-list');
    ul.style.listStyle = 'none'; ul.style.paddingLeft = '0';
    rows.slice(0, 10).forEach((r) => {
      const li = el('li', 'ec-row');
      const left = el('span', '', r.race_label);
      li.append(left, ratingChip(r.rating));
      ul.append(li);
    });
    const note = el('p', 'ec-note', `Showing ${Math.min(10, rows.length)} of ${rows.length} default-watched races (Toss Ups and Arizona).`);
    box.replaceChildren(ul, note);
  }

  function drawFresh() {
    const box = document.getElementById('ov-fresh-body');
    const s = dash.data('states'), rl = dash.data('run_log'), n = dash.data('national'), az = dash.data('az_returns');
    if ([s, rl, n, az].some((x) => x.status === 'error')) return window.ec.fail(box);
    if ([s, rl, n, az].some((x) => x.status !== 'ok')) { box.replaceChildren(); return; }
    const done = rl.data.filter((r) => r.completed_at);
    const last = done[0];
    const missed24 = rl.data.slice(0, 24).filter((r) => r.status === 'missed').length;
    const exact = s.data.filter((r) => isNum(r.total_early));
    const staleStates = s.data.filter((r) => r.stale).map((r) => r.abbr);
    const keyStale = [n.data[0] && n.data[0].stale ? 'U.S.' : null, ...az.data.filter((r) => r.stale).map((r) => 'AZ ' + r.area)].filter(Boolean);
    const lines = [
      ['Last completed run', last ? fmtDate(last.completed_at) + ' · ' + last.status : '—'],
      ['Missed runs, last 24 h', String(missed24)],
      ['Sources failing last run', last && last.sources_failed ? last.sources_failed : 'none'],
      ['Key figures with stale data', keyStale.length ? keyStale.join(', ') : 'none'],
      ['States with stale data', staleStates.length ? staleStates.join(', ') : 'none'],
      ['States with exact counts', `${exact.filter((r) => r.classification === 'official').length} official · ${exact.filter((r) => r.classification !== 'official').length} tracker · ${s.data.length - exact.length} none`],
    ];
    const dl = el('dl', 'ec-kv');
    lines.forEach(([k, v]) => { dl.append(el('dt', '', k), el('dd', '', v)); });
    box.replaceChildren(dl);
    const hdr = document.getElementById('hdr-run');
    hdr.textContent = last ? `Last hourly run completed ${fmtDate(last.completed_at)} (${last.status}).` : '';
    // independent of any scheduler: computed in the viewer's browser from the newest completed run
    const alertBox = document.getElementById('hdr-alert');
    const age = last ? ageHours(last.completed_at) : Infinity;
    if (age > 1.5) {
      alertBox.hidden = false;
      alertBox.replaceChildren(el('b', '', 'No hourly update for ' + (isFinite(age) ? Math.floor(age) + ' h ' + Math.round((age % 1) * 60) + ' min' : 'a while') + '. '),
        'The last completed run was ' + (last ? fmtDate(last.completed_at) : 'not recorded') + '. Figures below are the last verified values and may be out of date; check each figure\'s "data as of" date.');
    } else { alertBox.hidden = true; }
    const nat = n.data[0] || {};
    document.querySelectorAll('.ec-nat-prec').forEach((b) => { b.hidden = nat.precision !== 'rounded'; });
    const rq = document.getElementById('nat-req-src');
    if (rq) rq.textContent = nat.requested_source ? `From ${nat.requested_source}, as of ${fmtDate(nat.requested_as_of)}` : '';
    const rel = document.getElementById('runs-reliability');
    if (rel) {
      const day = rl.data.filter((r) => r.scheduled_for && ageHours(r.scheduled_for) <= 24 && ageHours(r.scheduled_for) >= 1);
      const done = day.filter((r) => r.completed_at && r.status !== 'missed' && !String(r.status).startsWith('not published'));
      const pub = day.filter((r) => r.published_at);
      const onTime = day.filter((r) => r.on_time === true);
      rel.textContent = day.length ? `Last 24 h: ${done.length} of ${day.length} scheduled hours completed; ${pub.length} with a recorded publication time, ${onTime.length} published by the target.` : '';
    }
  }

  // "data as of / last checked" lines under headline figures
  function drawFreshLines() {
    document.querySelectorAll('.ec-fresh').forEach((p) => {
      const d = dash.data(p.dataset.freshDs);
      const slot = p.querySelector('.ec-fresh-status');
      if (!slot) return;
      if (d.status !== 'ok') { slot.replaceChildren(); return; }
      const row = p.dataset.freshWhere ? d.data.find((r) => r.area === p.dataset.freshWhere) : d.data[+(p.dataset.freshRow || 0)];
      slot.replaceChildren(...freshBadges(row));
    });
  }

  function drawOverview() {
    drawDeadline(); drawBrief(); drawWatch(); drawFresh(); drawFreshLines();
    window.ec.fill('national');
  }
  dash.onData(drawOverview);
})();
