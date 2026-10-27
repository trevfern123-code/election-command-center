#!/usr/bin/env python3
"""Election command center — hourly validate-and-publish pipeline (toolkit v4).

Commands (each prints JSON to stdout):
  vp.py clock    --started ISO [--now ISO]                -> time budget: keep fetching or stop and publish
  vp.py direct   --registry R --out DIR [--now ISO]       -> direct downloads + code extraction (no AI); writes DIR/direct_obs.json
  vp.py plan     --registry R [--direct DIR/direct_obs.json] [--now ISO] -> fetch groups still needed, in priority order
  vp.py prepare  --bundle B --registry R --obs O --out DIR [--direct D] [--prev-latest L] [--conflicts DIR]
                 [--trigger-status S] [--now ISO] [--toolkit-version V]
                 -> validates, applies, writes changed datasets, archive and publish plan
  vp.py batch    --out DIR --ids IDS --docs DOCS --versions V -> DIR/batch.json (atomic, version-pinned) + DIR/delete.json
  vp.py unpack   --archive A --out B
The bundle is the full pipeline state. The archive is the gzip+base64 bundle plus this run's observations and log.
"""
import argparse, base64, copy, datetime as dt, glob, gzip, hashlib, html, json, os, re, ssl, sys, time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

TOOLKIT_VERSION = '2026-10-27.14'
PIPE = ['national', 'states', 'bg_states', 'az_returns', 'az_trackers', 'az_counties', 'pace', 'registration',
        'watch_races', 'rating_history', 'polls', 'headlines', 'briefing', 'checks', 'corrections',
        'source_compare', 'source_changes', 'run_log', 'results', 'az_dates', 'source_registry',
        'source_status', 'reviews']
ELECTION_DAY = dt.date(2026, 11, 3)
POLLS_CLOSE_AZ = dt.datetime(2026, 11, 4, 2, 0, tzinfo=dt.timezone.utc)   # 7:00 p.m. Arizona time, Nov 3
VALID_FROM = dt.date(2026, 9, 1)
AZ = dt.timezone(dt.timedelta(hours=-7))
RATINGS = ['Solid D', 'Likely D', 'Lean D', 'Toss Up', 'Lean R', 'Likely R', 'Solid R']
RESULT_STATUS = ['Polls not closed', 'Awaiting results', 'Leading in reported results', 'Projected winner', 'Too close to call', 'Recount', 'Certified']
BAD_DEF_WORDS = ['requested', 'sent', 'issued', 'mailed out', 'applications', 'outstanding']
GOOD_DEF_WORDS = ['cast', 'voted', 'vote', 'ballot', 'turnout', 'returned', 'received', 'accepted', 'counted']

# time budget (minutes)
FETCH_BUDGET_MIN = 20      # stop fetching this long after the run starts ...
FETCH_LATEST_MIN = 28      # ... or at slot+28 min, whichever is first (never less than MIN_FETCH_MIN)
MIN_FETCH_MIN = 8
PUBLISH_TARGET_MIN = 35    # publication target: scheduled hour + 35 min (watchdog checks at :40)
SOURCE_TIMEOUT_S = 180     # a source whose attempts take longer than this in total is recorded as timed out
ATTEMPT_TIMEOUT_S = 90
REVIEW_CONFIRM_READS = 2   # unexplained changes are adopted after this many consecutive identical reads
REVIEW_MIN_GAP_MIN = 40    # reads must come from different hourly runs


# ----------------------------------------------------------------- helpers
def now_utc(s=None):
    if s:
        return dt.datetime.fromisoformat(str(s).replace('Z', '+00:00')).astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)

def iso(t):
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

def parse_asof(s):
    """'YYYY-MM-DD' (data through end of that day, AZ time) or ISO datetime -> aware datetime (end of period)."""
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r'^(\d{4}-\d{2}-\d{2})$', s)
    if m:
        d = dt.date.fromisoformat(m.group(1))
        return dt.datetime(d.year, d.month, d.day, 23, 59, tzinfo=AZ)
    try:
        t = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
        return t if t.tzinfo else t.replace(tzinfo=AZ)
    except ValueError:
        return None

def asof_key(s):
    t = parse_asof(s)
    return t.astimezone(dt.timezone.utc).isoformat() if t else ''

def asof_date(s):
    """Calendar date of the period as the source states it (not shifted to UTC)."""
    s = str(s or '')
    m = re.match(r'^(\d{4}-\d{2}-\d{2})', s)
    return dt.date.fromisoformat(m.group(1)) if m else None

def host(u):
    try:
        h = urlparse(u).hostname or ''
        return h[4:] if h.startswith('www.') else h
    except Exception:
        return ''

def host_ok(u, hosts):
    h = host(u)
    return any(h == x or h.endswith('.' + x) for x in hosts)

def norm_text(t):
    t = html.unescape(str(t or ''))
    t = re.sub(r'(?<=\d)[    ](?=\d{3}\b)', ',', t)   # 1 234 567 with thin/no-break spaces
    t = t.replace('−', '-').replace(' ', ' ')
    return t

NUM_RE = re.compile(r'(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(million|mil\.?|thousand|[kKmM])?(?![\w])')

def numbers_in(text):
    """All numbers in text as (value, multiplier, decimals). Handles 1,234,567 / 1234567 / 1,234,567.0 / 1.27M / 1.27 million / 744K."""
    out = []
    for m in NUM_RE.finditer(norm_text(text)):
        raw, unit = m.group(1), (m.group(2) or '').lower().rstrip('.')
        try:
            v = float(raw.replace(',', ''))
        except ValueError:
            continue
        mult = {'k': 1e3, 'thousand': 1e3, 'm': 1e6, 'mil': 1e6, 'million': 1e6}.get(unit, 1)
        decimals = len(raw.split('.')[1]) if '.' in raw else 0
        out.append((v * mult, mult, decimals))
    return out

def evidence_has(evidence, value, precision):
    """The evidence text must contain the figure: exactly for exact counts; within its own rounding for rounded ones."""
    for v, mult, dec in numbers_in(evidence):
        if precision in ('exact', 'calculated'):
            if mult == 1 and abs(v - value) < 0.5:
                return True
        else:
            step = mult * (10 ** -dec) if mult > 1 else 1
            if abs(v - value) <= max(step, abs(value) * 0.001):
                return True
            if mult == 1 and abs(v - value) < 0.5:
                return True
    return False

MONTHS = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}

def dates_in(text, default_year=2026):
    """Calendar dates written in text: 2026-10-22, 10/22/2026, 10/22/26, 10/22, Oct 22, October 22nd, 2026, 22 October."""
    t = norm_text(text)
    out = set()
    for y, mo, d in re.findall(r'\b(20\d\d)-(\d{1,2})-(\d{1,2})\b', t):
        try: out.add(dt.date(int(y), int(mo), int(d)))
        except ValueError: pass
    for mo, d, y in re.findall(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b', t):
        yy = int(y) if y else default_year
        yy = yy + 2000 if yy < 100 else yy
        try: out.add(dt.date(yy, int(mo), int(d)))
        except ValueError: pass
    for mon, d, d2, y in re.findall(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:\s*[-\u2013\u2014]\s*(\d{1,2})\b)?(?:,?\s*(20\d\d))?', t, re.I):
        for dd in (d, d2):
            if dd:
                try: out.add(dt.date(int(y) if y else default_year, MONTHS[mon[:3].lower()], int(dd)))
                except ValueError: pass
    for d, mon, y in re.findall(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:,?\s*(20\d\d))?', t, re.I):
        try: out.add(dt.date(int(y) if y else default_year, MONTHS[mon[:3].lower()], int(d)))
        except ValueError: pass
    return out

def lc(t):
    return re.sub(r'\s+', ' ', norm_text(t)).strip().lower()

def load(p, default=None):
    if p and os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return default

def dump(p, obj):
    os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
    with open(p, 'w') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)

def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def fmt_int(v):
    try:
        return f'{int(round(float(v))):,}'
    except Exception:
        return str(v)


# ----------------------------------------------------------------- registry
class Registry:
    def __init__(self, reg):
        self.reg = reg
        self.sources = {s['id']: s for s in reg['sources']}
        self.measures = {m['id']: m for m in reg['measures']}

    def priority(self, measure_id, source_id):
        lst = self.measures[measure_id]['sources']
        return lst.index(source_id) if source_id in lst else 999

    def source_for_row(self, measure_id, row):
        if row.get('source_id'):
            return row['source_id']
        u = row.get('source_url') or ''
        for sid in self.measures[measure_id]['sources']:
            if host_ok(u, self.sources[sid]['hosts']):
                return sid
        return 'legacy'

    def name(self, sid):
        return self.sources.get(sid, {}).get('name', sid)


# ----------------------------------------------------------------- measure <-> row binding
def measure_rows(bundle):
    for r in bundle['states']:
        yield f"{r['abbr']}.early_total", 'states', r
    for r in bundle['national']:
        yield 'US.early_total', 'national', r
    for r in bundle['az_returns']:
        yield ('AZ.early_returned' if r['area'] == 'Statewide' else f"AZ-{r['area']}.early_returned"), 'az_returns', r
    for r in bundle['az_counties']:
        if r['county'] != 'Maricopa':
            yield f"AZ-{r['county']}.county_returned", 'az_counties', r

def value_field(ds):
    return {'states': 'total_early', 'national': 'total_early', 'az_returns': 'returned', 'az_counties': 'returned'}[ds]


# ----------------------------------------------------------------- clock
def budget(started, now, slot=None):
    slot = slot or started.replace(minute=0, second=0, microsecond=0)
    cutoff = min(started + dt.timedelta(minutes=FETCH_BUDGET_MIN), slot + dt.timedelta(minutes=FETCH_LATEST_MIN))
    cutoff = max(cutoff, started + dt.timedelta(minutes=MIN_FETCH_MIN))
    target = slot + dt.timedelta(minutes=PUBLISH_TARGET_MIN)
    return slot, cutoff, target

def cmd_clock(a):
    started, now = now_utc(a.started), now_utc(a.now)
    slot, cutoff, target = budget(started, now)
    left = (cutoff - now).total_seconds()
    print(json.dumps({'now': iso(now), 'scheduled_for': iso(slot), 'fetch_cutoff': iso(cutoff), 'publish_target': iso(target),
                      'seconds_left_for_fetching': int(left),
                      'action': 'continue' if left > 0 else 'stop_fetching_and_publish',
                      'note': 'When action is stop_fetching_and_publish: record every remaining group as status "skipped" and go straight to prepare.'}))


# ----------------------------------------------------------------- direct retrieval (no AI)
def _ssl_ctx():
    ctx = ssl.create_default_context()
    for p in ('/root/.ccr/ca-bundle.crt', os.environ.get('SSL_CERT_FILE') or ''):
        if p and os.path.exists(p):
            try:
                ctx.load_verify_locations(p)
            except Exception:
                pass
    return ctx

def egress_connect(host, port=443):
    """Ask the environment's egress proxy for a tunnel and return its status line and its own denial text (curl hides it)."""
    import socket
    p = urlparse(os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or '')
    if not p.hostname:
        return {'host': host, 'connect': 'no proxy configured in this environment'}
    try:
        s = socket.create_connection((p.hostname, p.port), 10)
        s.settimeout(15)
        s.sendall(f'CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n'.encode())
        d = b''
        while b'\r\n\r\n' not in d:
            c = s.recv(4096)
            if not c:
                break
            d += c
        head, _, body = d.partition(b'\r\n\r\n')
        status = head.split(b'\r\n')[0].decode('utf-8', 'replace')
        if ' 200' not in status:
            try:
                while len(body) < 2000:
                    c = s.recv(2048)
                    if not c:
                        break
                    body += c
            except Exception:
                pass
        s.close()
        return {'host': host, 'connect': status, 'proxy_message': body.decode('utf-8', 'replace').strip()[:300]}
    except Exception as e:
        return {'host': host, 'connect': 'error', 'proxy_message': f'{type(e).__name__}: {e}'[:300]}

def http_get(url, timeout=20):
    import urllib.request, urllib.error
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (election-command-center hourly check; contact via dashboard owner)',
                                               'Accept-Encoding': 'gzip'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
            body = r.read()
            enc = r.headers.get('Content-Encoding', '')
            ctype = r.headers.get('Content-Type', '')
            status = r.status
    except urllib.error.HTTPError as e:
        return {'ok': False, 'status': 'error', 'detail': f'HTTP {e.code} from the site', 'raw_error': f'HTTPError {e.code} {e.reason}', 'seconds': round(time.time() - t0, 1)}
    except Exception as e:
        msg = f'{type(e).__name__}: {e}'
        blocked = any(x in msg for x in ('Tunnel connection failed: 403', 'connect_rejected', 'ProxyError', '403 Forbidden'))
        if blocked:
            pm = egress_connect(urlparse(url).hostname or '')
            msg = f"{msg} | proxy said: {pm.get('connect')} {pm.get('proxy_message', '')}"
        return {'ok': False, 'status': 'blocked' if blocked else 'unreachable',
                'detail': ('egress proxy refused CONNECT (403): host not allowed for this environment' if blocked else msg[:200]),
                'raw_error': msg[:500], 'seconds': round(time.time() - t0, 1)}
    if enc == 'gzip' or body[:2] == b'\x1f\x8b':
        try:
            body = gzip.decompress(body)
        except Exception:
            pass
    return {'ok': True, 'status_code': status, 'body': body, 'ctype': ctype, 'seconds': round(time.time() - t0, 1)}

def html_to_text(b):
    t = b.decode('utf-8', 'replace')
    t = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', t)
    t = re.sub(r'(?i)<br\s*/?>|</(p|div|tr|li|h\d|table|section)>', '\n', t)
    t = re.sub(r'(?i)</t[dh]>', ' | ', t)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = html.unescape(t)
    t = re.sub(r'[ \t\r\f\v]+', ' ', t)
    return re.sub(r'\n\s*\n+', '\n', t)

def pdf_to_text(body):
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix='.pdf') as tf:
        tf.write(body); tf.flush()
        r = subprocess.run(['pdftotext', '-layout', tf.name, '-'], capture_output=True, timeout=60)
        if r.returncode:
            raise RuntimeError('pdftotext failed: ' + r.stderr.decode()[:120])
        return r.stdout.decode('utf-8', 'replace')

def xlsx_to_text(body):
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f'## sheet {ws.title}')
        for row in ws.iter_rows(values_only=True):
            cells = ['' if c is None else (f'{c:,}' if isinstance(c, (int, float)) and not isinstance(c, bool) else str(c)) for c in row]
            if any(cells):
                lines.append(' | '.join(cells))
    return '\n'.join(lines)

def csv_to_text(body):
    import csv, io
    rows = list(csv.reader(io.StringIO(body.decode('utf-8-sig', 'replace'))))
    # some official CSVs are padded with ~1M empty rows; drop rows with no content
    return '\n'.join(' | '.join(c.strip() for c in r) for r in rows if any(c.strip() for c in r))

def expand_url(u, now):
    if '{' not in u:
        return [u]
    out = []
    for back in (0, 1):
        d = (now.astimezone(AZ) - dt.timedelta(days=back)).date()
        out.append(u.replace('{YMD}', d.isoformat()).replace('{MDY2}', d.strftime('%m-%d-%y')))
    return out

def parse_text_regex(text, spec, url, run_now):
    """spec: {measure, value_re (group 'value'), label, asof_re (group 'asof' or y/m/d), election_re} -> figure or (None, reason)."""
    calc = None
    if spec.get('parts'):
        parts = []
        for ps in spec['parts']:
            pm = re.search(ps['value_re'], text, re.I | re.S)
            if not pm:
                return None, f"part '{ps['label']}' not found"
            pv = numbers_in(pm.group('value'))
            if not pv:
                return None, f"part '{ps['label']}' not numeric"
            parts.append({'label': ps['label'], 'value': round(pv[0][0]), 'evidence': pm.group(0)[:300]})
        calc = {'op': 'sum', 'parts': parts}
        total = sum(p_['value'] for p_ in parts)
        text_ev = ' ; '.join(p_['evidence'] for p_ in parts)
        m = re.search(re.escape(parts[0]['evidence'][:40]), text)
        spec = dict(spec, value_re=None)
    else:
        m = re.search(spec['value_re'], text, re.I | re.S)
    if not m:
        return None, 'pattern for the value not found'
    line_start = text.rfind('\n', 0, m.start()) + 1
    line_end = text.find('\n', m.end())
    evidence = text[max(line_start, m.start() - 200): (line_end if line_end > 0 else m.end() + 200)][:400].strip()
    if calc:
        v, evidence = total, text_ev[:400]
    else:
        vals = numbers_in(m.group('value'))
        if not vals:
            return None, 'value not numeric'
        v = vals[0][0]
    if spec.get('rows_sum_re'):
        # internal consistency: the detail rows printed in the same file must add up to the total
        rows = [numbers_in(x.group('value'))[0][0] for x in re.finditer(spec['rows_sum_re'], text, re.I | re.M)]
        if len(rows) < spec.get('rows_min', 1):
            return None, f"only {len(rows)} detail rows found (expected at least {spec.get('rows_min', 1)})"
        if round(sum(rows)) != round(v):
            return None, f'detail rows add up to {round(sum(rows)):,}, not the printed total {round(v):,}'
    asof_txt, asof = '', None
    if spec.get('asof_re'):
        if spec.get('asof_scope') == 'preceding':
            # live blogs: the date is the nearest update header ABOVE the number, not the newest header on the page
            ams = [x for x in re.finditer(spec['asof_re'], text, re.I | re.S) if x.start() < m.start()]
            am = ams[-1] if ams else None
        else:
            am = re.search(spec['asof_re'], text, re.I | re.S)
        if not am:
            return None, 'pattern for the observation date not found'
        asof_txt = am.group(0)[:200]
        ds = sorted(dates_in(am.group('asof') if 'asof' in am.groupdict() else am.group(0)))
        if not ds:
            return None, 'observation date not parseable'
        asof = (ds[-1] - dt.timedelta(days=spec.get('asof_offset_days', 0))).isoformat()
    el = ''
    if spec.get('election_re'):
        em = re.search(spec['election_re'], text, re.I | re.S)
        if not em:
            return None, 'election identifier not found on page'
        el = em.group(0)[:160]
    label_ev = evidence
    if spec.get('label_re'):
        lm = re.search(spec['label_re'], text, re.I | re.S)
        if not lm:
            return None, 'label/column header not found on page'
        label_ev = lm.group(0)[:200]
    out = {'measure': spec['measure'], 'source_id': spec['source_id'], 'value': round(v), 'precision': 'exact', 'data_as_of': asof,
           'election': el, 'url': url, 'evidence': evidence, 'metric_label': spec['label'], 'label_evidence': label_ev,
           'as_of_evidence': asof_txt, 'method': 'direct'}
    if calc:
        out['calc'] = calc
        out['label_evidence'] = label_ev + ' ' + ' '.join(p_['label'] for p_ in calc['parts'])
    if spec.get('asof_basis'):
        out['asof_basis'] = spec['asof_basis']
    return out, 'ok'

def parse_tpsi_payload(body, url, run_now, reg_measures, h):
    """TPSI / On Point pages embed their full state data in the Next.js payload. Returns (figures, not_reporting, info)."""
    t = body.decode('utf-8', 'replace')
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', t, re.S)
    full = ''.join(json.loads('"' + c + '"') for c in chunks)
    text = html_to_text(body)
    am = re.search(r'As of (?P<asof>[A-Z][a-z]+ \d{1,2}, 20\d\d)\s*,\s*(?P<nat>\d{1,3}(?:,\d{3})+) ballots have been cast in the 2026 midterms across (?P<n>\d+) reporting states', text)
    if not am:
        raise RuntimeError('page "as of" sentence not found')
    asof_txt = am.group(0)
    page_date = sorted(dates_in(am.group('asof')))[-1].isoformat()
    header = re.search(r'State\s*\|\s*Ballots[^\n]*', text)
    label_ev = header.group(0) if header else 'State | Ballots'
    figs, notrep, seen = [], [], set()
    for mm in re.finditer(r'\{"st":"(?P<st>[A-Z]{2})","name":"(?P<name>[^"]+)","slug":"[^"]*","total":(?P<total>\d+),"mail":(?P<mail>\d+),"inperson":(?P<inp>\d+)', full):
        st = mm.group('st')
        if st in seen:
            continue
        seen.add(st)
        rec_end = full.find('{"st":"', mm.end())
        rec = full[mm.start(): rec_end if rec_end > 0 else mm.end() + 3000]
        total, mail, inp = int(mm.group('total')), int(mm.group('mail')), int(mm.group('inp'))
        mid = f'{st}.early_total'
        if mid not in reg_measures:
            continue
        if total == 0:
            notrep.append(mid); continue
        s26 = re.search(r'"s26":(\[\[[^\]]*\](?:,\[[^\]]*\])*\])', rec)
        since = None
        if s26:
            series = json.loads(s26.group(1))
            run_days = []
            for d_, v_ in sorted(series, key=lambda x: x[0]):      # newest (fewest days left) first
                if v_ == total:
                    run_days.append(d_)
                else:
                    break
            if run_days:
                since = (ELECTION_DAY - dt.timedelta(days=max(run_days))).isoformat()
        ev = f"{mm.group('name')} | {total:,} | {mail:,} | {inp:,}  (TPSI page data record: total {total}, mail {mail}, in person {inp})"
        f = {'measure': mid, 'source_id': 'tpsi', 'value': total, 'mail': mail, 'in_person': inp, 'precision': 'exact', 'data_as_of': page_date,
             'election': 'the 2026 midterms', 'url': url, 'evidence': ev, 'metric_label': 'Ballots', 'label_evidence': label_ev, 'as_of_evidence': asof_txt,
             'method': 'direct', 'raw_sha256': h, 'value_unchanged_since': since}
        figs.append(f)
        if st == 'AZ':
            figs.append(dict(f, measure='AZ.early_returned'))
    # jurisdictions with no record in the page data are ones the tracker does not report (it states how many it covers)
    for mid in sorted(reg_measures):
        if re.match(r'^[A-Z]{2}\.early_total$', mid) and not mid.startswith('US.') and mid.split('.')[0] not in seen:
            notrep.append(mid)
    nat = int(am.group('nat').replace(',', ''))
    # internal consistency: the headline must equal the sum of the tracker's own state records in the same file
    comp = [f_['value'] for f_ in figs if re.match(r'^[A-Z]{2}\.early_total$', f_['measure'])]
    figs.append({'measure': 'US.early_total', 'source_id': 'tpsi', 'value': nat, 'precision': 'exact', 'data_as_of': page_date, 'election': 'the 2026 midterms',
                 'url': url, 'evidence': asof_txt, 'metric_label': 'ballots have been cast', 'as_of_evidence': asof_txt, 'method': 'direct', 'raw_sha256': h,
                 'components_sum': sum(comp), 'components_n': len(comp)})
    return figs, notrep, {'reporting_states_stated': int(am.group('n')), 'records': len(seen), 'page_date': page_date}

US_STATES = ['Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado', 'Connecticut', 'Delaware', 'District of Columbia', 'Florida', 'Georgia', 'Hawaii',
             'Idaho', 'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana', 'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Minnesota', 'Mississippi',
             'Missouri', 'Montana', 'Nebraska', 'Nevada', 'New Hampshire', 'New Jersey', 'New Mexico', 'New York', 'North Carolina', 'North Dakota', 'Ohio', 'Oklahoma',
             'Oregon', 'Pennsylvania', 'Rhode Island', 'South Carolina', 'South Dakota', 'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia', 'Washington',
             'West Virginia', 'Wisconsin', 'Wyoming']
US_ABBR = dict(zip(US_STATES, ['AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN',
                                 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA',
                                 'WA', 'WV', 'WI', 'WY']))

def parse_ctm_states(body, url, run_now, reg_measures, h):
    """Call the Map early-vote page: one section per state, each with its source line ("State file of <date> · <office>" or
    "civicAPI snapshot of <date>") and "Ballots returned N through <Mon D>". The "through" date is the day the count covers.
    Sections that say the count covers only part of the state (e.g. one county's mail ballots) are not recorded."""
    text = html_to_text(body)
    tm = re.search(r'[^\n]*who has voted so far[^\n]*', text, re.I)
    if not tm or '2026' not in text[:3000]:
        raise RuntimeError('page title for the 2026 early vote not found')
    title = re.sub(r'\s+', ' ', tm.group(0)).strip()[:120]
    year = 2026
    starts = []
    for st in US_STATES:
        # the heading may be followed by a few short share-button lines ("Share <state>", "Post to …") before its source line
        for m in re.finditer(r'\n[ \t]*' + re.escape(st) + r'[ \t]*\n(?:[ \t]*(?:Share|Post to)[^\n]{0,40}\n){0,6}[ \t]*(?P<src>(?:State file of|civicAPI snapshot of)[^\n]*)', text):
            starts.append((m.start(), st, m.group('src').strip()))
            break
    starts.sort()
    figs, notes = [], []
    for i, (pos, st, src) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else min(len(text), pos + 6000)
        sec = text[pos:end]
        bm = re.search(r'Ballots returned (?P<value>\d{1,3}(?:,\d{3})+|\d+) through (?P<asof>[A-Z][a-z]{2} \d{1,2})', sec)
        mid = f'{US_ABBR[st]}.early_total'
        if not bm:
            notes.append(f'{st}: no "Ballots returned … through" line'); continue
        part = re.search(r'(?i)\b(?:county|counties)\b[^\n]{0,40}\bonly\b|\bonly\b[^\n]{0,40}\b(?:county|counties)\b|part of the state', sec[:bm.start()])
        if part:
            notes.append(f'{st}: count covers only part of the state ("{part.group(0)[:60]}"), not recorded'); continue
        if mid not in reg_measures:
            continue
        ds = dates_in(f"{bm.group('asof')} {year}")
        if not ds:
            notes.append(f'{st}: date not parseable'); continue
        asof = sorted(ds)[-1].isoformat()
        f = {'measure': mid, 'source_id': 'callthemap', 'value': int(bm.group('value').replace(',', '')), 'precision': 'exact', 'data_as_of': asof,
             'election': title, 'url': url, 'evidence': f'{st} | {src[:160]} | {bm.group(0)}', 'metric_label': 'Ballots returned',
             'label_evidence': bm.group(0), 'as_of_evidence': bm.group(0), 'method': 'direct', 'raw_sha256': h, 'asof_basis': 'period'}
        figs.append(f)
        if st == 'Arizona':
            figs.append(dict(f, measure='AZ.early_returned'))
    nm = re.search(r'(?P<value>\d{1,3}(?:,\d{3})+) ballots in (?P<n>\d+) states\s*Through (?P<range>[A-Z][a-z]{2} \d{1,2}(?:\s*[–-]\s*(?:[A-Z][a-z]{2} )?\d{1,2})?)', text)
    if nm and 'US.early_total' in reg_measures:
        rng = nm.group('range')
        first = re.match(r'([A-Z][a-z]{2}) (\d{1,2})', rng)
        # the national total mixes states counted through different days: it is dated by the EARLIEST day in its range
        d0 = sorted(dates_in(f'{first.group(1)} {first.group(2)} {year}'))[0].isoformat()
        figs.append({'measure': 'US.early_total', 'source_id': 'callthemap', 'value': int(nm.group('value').replace(',', '')), 'precision': 'exact', 'data_as_of': d0,
                     'election': title, 'url': url, 'evidence': nm.group(0), 'metric_label': 'ballots', 'label_evidence': nm.group(0),
                     'as_of_evidence': nm.group(0) + ' (dated by the earliest day in the range)', 'method': 'direct', 'raw_sha256': h, 'asof_basis': 'period'})
    return figs, notes, {'sections': len(starts)}

def parse_substack_abev(body, url, run_now, h):
    """Derek Weech's Substack RSS feed: newest post titled "AZ ABEV Returns - MM/DD/YYYY"; its opening line gives
    "N returned of M issued". The title date is the date of the returns."""
    t = body.decode('utf-8', 'replace')
    for it in re.findall(r'<item>(.*?)</item>', t, re.S):
        tm = re.search(r'<title>(?:<!\[CDATA\[)?(?P<title>.*?)(?:\]\]>)?</title>', it, re.S)
        title = html.unescape(tm.group('title')).strip() if tm else ''
        dm = re.match(r'AZ ABEV Returns\s*-\s*(?P<d>\d{1,2}/\d{1,2}/(?P<y>20\d\d))$', title)
        if not dm or dm.group('y') != '2026':
            continue
        cm = re.search(r'<content:encoded>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content:encoded>', it, re.S)
        body_txt = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(cm.group(1) if cm else ''))).strip()
        vm = re.search(r'(?P<value>\d{1,3}(?:,\d{3})+) (?:ballots )?returned of (?P<issued>\d{1,3}(?:,\d{3})+) issued', body_txt)
        lm = re.search(r'<link>(.*?)</link>', it)
        if not vm:
            return [], [f'newest returns post "{title}" has no "N returned of M issued" line']
        asof = sorted(dates_in(dm.group('d')))[-1].isoformat()
        return [{'measure': 'AZ.early_returned', 'source_id': 'weech', 'value': int(vm.group('value').replace(',', '')), 'precision': 'exact', 'data_as_of': asof,
                 'election': title, 'url': (lm.group(1).strip() if lm else url), 'evidence': f'{title}: {vm.group(0)}', 'metric_label': 'returned',
                 'label_evidence': vm.group(0), 'as_of_evidence': title, 'method': 'direct', 'raw_sha256': h, 'asof_basis': 'period'}], []
    return [], ['no "AZ ABEV Returns - <date>" post for 2026 in the feed']

def cmd_direct(a):
    """Try direct download + code extraction for every group with a 'direct' spec. Fast-fails when the workspace allowlist blocks the host."""
    reg = load(a.registry)
    run_now = now_utc(a.now)
    out = a.out
    os.makedirs(f'{out}/raw', exist_ok=True)
    jobs = []
    measures = {m['id'] for m in reg['measures']}
    for g in reg['fetch_groups']:
        for d in g.get('direct', []):
            if d.get('active', True):
                jobs.append((g, d))
    def work(job):
        g, d = job
        t0 = now_utc()
        cand_urls, disc_note, link_text = expand_url(d['url'], run_now), None, None
        if d.get('discover'):
            # the file name can change: take the current link from the official page that lists it
            import urllib.parse
            pg = http_get(d['discover']['page'], timeout=20)
            if pg['ok']:
                lm = re.search(d['discover']['link_re'], pg['body'].decode('utf-8', 'replace'), re.I)
                if lm:
                    found = urllib.parse.urljoin(d['discover']['page'], html.unescape(lm.group('href')))
                    cand_urls = [found] + [u for u in cand_urls if u != found]
                    disc_note = f"link found on {d['discover']['page']}"
                    if 'text' in lm.groupdict() and lm.group('text'):
                        link_text = re.sub(r'\s+', ' ', html.unescape(lm.group('text'))).strip()
                        disc_note += f' as "{link_text}"'
                else:
                    disc_note = f"no matching link on {d['discover']['page']}; tried the registered URL"
            else:
                disc_note = f"listing page not read ({pg['status']}: {pg.get('raw_error') or pg['detail']}); tried the registered URL"
        for url in cand_urls:     # newest dated file first
            r = http_get(url, timeout=min(ATTEMPT_TIMEOUT_S, d.get('timeout_s', 20)))
            if r['ok'] or r['status'] == 'blocked':
                break
        att = {'source_id': g['source_id'], 'group': g['id'], 'url': url, 'attempt': 1, 'method': 'direct', 'started_at': iso(t0), 'ended_at': iso(now_utc()),
               'seconds': r.get('seconds')}
        if disc_note:
            att['discovery'] = disc_note
        figs, res = [], None
        if not r['ok']:
            att.update(status=r['status'], detail=r['detail'], raw_error=r.get('raw_error'))
            return att, figs, res
        h = hashlib.sha256(r['body']).hexdigest()
        ext = {'azev_json': 'json', 'pdf_text_regex': 'pdf', 'xlsx_text_regex': 'xlsx', 'csv_text_regex': 'csv'}.get(d.get('parser'), 'html')
        raw_path = f"{out}/raw/{g['source_id']}-{h[:12]}.{ext}"
        with open(raw_path, 'wb') as f:
            f.write(r['body'])
        att.update(status='ok', detail=f"downloaded {len(r['body'])} bytes, sha256 {h[:16]}", raw_file=raw_path, sha256=h)
        notrep = []
        try:
            if d.get('parser') == 'tpsi_payload':
                figs, notrep, info = parse_tpsi_payload(r['body'], url, run_now, measures, h)
                for f in figs:
                    f['raw_file'] = os.path.basename(raw_path)
                att['rows_stated'] = info['reporting_states_stated']
                att['detail'] += f"; page data records for {info['records']} jurisdictions, {len(notrep)} with zero; page date {info['page_date']}"
            elif d.get('parser') in ('text_regex', 'pdf_text_regex', 'xlsx_text_regex', 'csv_text_regex'):
                text = {'text_regex': html_to_text, 'pdf_text_regex': pdf_to_text, 'xlsx_text_regex': xlsx_to_text, 'csv_text_regex': csv_to_text}[d['parser']](r['body'])
                if link_text and url == cand_urls[0]:
                    # a bare data file names nothing; the official page's own link text says what the file is
                    text = f"Linked from {d['discover']['page']} as: {link_text}\n" + text
                with open(raw_path + '.txt', 'w') as tf:
                    tf.write(text)
                for spec in d['patterns']:
                    spec = dict(spec, source_id=g['source_id'])
                    f, why = parse_text_regex(text, spec, url, run_now)
                    if f:
                        f['raw_sha256'] = h
                        f['raw_file'] = os.path.basename(raw_path)
                        figs.append(f)
                    else:
                        att.setdefault('parse_notes', []).append(f"{spec['measure']}: {why}")
            elif d.get('parser') == 'ctm_states':
                figs, notes, info = parse_ctm_states(r['body'], url, run_now, measures, h)
                for f in figs:
                    f['raw_file'] = os.path.basename(raw_path)
                att['detail'] += f"; {info['sections']} state sections, {len([f for f in figs if f['measure'].endswith('.early_total') and not f['measure'].startswith('US.')])} state counts with their own dates"
                if notes:
                    att['parse_notes'] = notes
            elif d.get('parser') == 'substack_abev':
                figs, notes = parse_substack_abev(r['body'], url, run_now, h)
                for f in figs:
                    f['raw_file'] = os.path.basename(raw_path)
                if notes:
                    att['parse_notes'] = notes
                    if not figs:
                        att['status'] = 'no_data'
                        att['detail'] += '; ' + notes[0]
            elif d.get('parser') == 'azev_json':
                res = parse_azev(json.loads(r['body'].decode('utf-8', 'replace')), url, h)
            nd = d.get('no_data_re')
            if nd and not figs and ext == 'html' and re.search(nd, html_to_text(r['body']), re.I):
                att['coverage'] = 'not_reporting'
                att['measure'] = d.get('measure')
        except Exception as e:
            att.update(status='error', detail=f'parser failed: {str(e)[:120]} (raw file kept)')
        if att['status'] == 'ok' and not figs and not res:
            if att.get('coverage') == 'not_reporting':
                att.update(status='no_data', detail=att['detail'] + '; page states the jurisdiction has not reported')
            else:
                # a page that downloaded but did not match is NOT treated as covered: the fetch-tool path reads it instead
                att.update(status='error', detail=att['detail'] + '; parser found no figure (layout change?): ' + '; '.join(att.get('parse_notes', []))[:200])
        att['not_reporting_measures'] = notrep
        return att, figs, res
    attempts, figures, results = [], [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for att, figs, res in ex.map(work, jobs):
            attempts.append(att)
            figures += figs
            if res:
                results.append(res)
    # hosts whose access is still being established: record what this environment's egress proxy answers, every run
    egress = [egress_connect(h_) for h_ in reg.get('egress_probe_hosts', [])]
    covered = sorted({x['group'] for x in attempts if x['status'] in ('ok', 'no_data')})
    covered_src = sorted({x['source_id'] for x in attempts if x['status'] in ('ok', 'no_data')})
    dump(f'{out}/direct_obs.json', {'attempts': attempts, 'figures': figures, 'azev': results, 'groups_covered': covered, 'sources_covered': covered_src, 'at': iso(run_now),
                                    'egress_probe': egress})
    print(json.dumps({'direct_jobs': len(jobs), 'ok': len([x for x in attempts if x['status'] == 'ok']),
                      'blocked_by_allowlist': sorted({x['source_id'] for x in attempts if x['status'] == 'blocked'}),
                      'figures': len(figures), 'groups_covered': covered, 'file': f'{out}/direct_obs.json',
                      'egress_probe': [f"{e['host']}: {e['connect']} {e.get('proxy_message', '')}".strip() for e in egress]}, indent=1))

def parse_azev(data, url, h):
    """Enhanced Voting media export (Arizona). Schema not yet confirmed against a live file: walk the JSON for
    contests (objects with a name and a list of options carrying vote counts). Returns a normalized dict; the
    pipeline only uses it for results rows after the schema is confirmed (registry flag azev_schema_confirmed)."""
    contests = []
    statewide = isinstance(data, dict) and isinstance(data.get('results'), dict) and isinstance(data['results'].get('ballotItems'), list)
    if statewide:
        for b in data['results']['ballotItems']:
            opts = [o for o in b.get('ballotOptions') or [] if not re.match(r'(?i)^(write-in|unassigned|unofficial)', str(o.get('name') or ''))]
            contests.append({'contest': b.get('name'), 'type': b.get('contestType'),
                             'options': [{'name': o.get('name'), 'party': o.get('politicalParty'), 'votes': o.get('voteCount')} for o in opts],
                             'precincts_reporting': b.get('precinctsReporting'), 'precincts_total': b.get('precinctsParticipating')})
    def walk(o, depth=0):
        if depth > 8:
            return
        if isinstance(o, dict):
            name = o.get('name') or o.get('ballotItemName') or o.get('contestName') or o.get('title')
            opts = None
            for k in ('ballotOptions', 'options', 'candidates', 'choices'):
                if isinstance(o.get(k), list) and o[k] and isinstance(o[k][0], dict):
                    opts = o[k]; break
            if name and opts:
                rows = []
                for c in opts:
                    votes = next((c[k] for k in ('voteCount', 'votes', 'totalVotes', 'count') if isinstance(c.get(k), (int, float))), None)
                    rows.append({'name': c.get('name') or c.get('candidateName'), 'party': c.get('politicalParty') or c.get('party'), 'votes': votes})
                contests.append({'contest': name, 'options': rows,
                                 'precincts_reporting': o.get('precinctsReporting'), 'precincts_total': o.get('precinctsParticipating') or o.get('precinctsTotal')})
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)
    if not statewide:
        walk(data)
    stamp = None
    flags = {}
    if isinstance(data, dict):
        stamp = data.get('lastUpdated') or data.get('lastUpdatedDate') or data.get('lastModified') or data.get('timestamp') or data.get('createdAt')
        flags = {k: data[k] for k in data if re.search(r'test|demo|sample|preview|unofficial|final|certif', k, re.I) and not isinstance(data[k], (dict, list))}
    total_votes = sum((o.get('votes') or 0) for c in contests for o in c['options'])
    blob = json.dumps(data)[:200000].lower() if contests else ''
    # identity of the election and production status: nonzero votes alone never make a file usable
    el_name = str(data.get('electionName') or '') if isinstance(data, dict) else ''
    el_date = str(data.get('electionDate') or '')[:10] if isinstance(data, dict) else ''
    checks = {
        'election_name': bool(re.search(r'\b2026\b', el_name) and re.search(r'general', el_name, re.I) and not re.search(r'primary|special|recall|test|mock|l&a|logic', el_name, re.I)),
        'election_date': el_date == ELECTION_DAY.isoformat(),
        'statewide_results': statewide and str((data.get('results') or {}).get('name') or '').strip().lower() == 'arizona',
        'not_test': not (any(v is True for k, v in flags.items() if re.search(r'test|demo|sample|preview', k, re.I))
                         or re.search(r'\b(test|mock|sample|demo) (data|results|election|file)\b|logic and accuracy|\bl&a\b', blob)),
    }
    try:
        created = now_utc(re.sub(r'(\.\d{6})\d+', r'\1', str(stamp))) if stamp else None
    except ValueError:
        created = None
    checks['created_after_polls_close'] = bool(created and created >= POLLS_CLOSE_AZ)
    failed = [k for k, ok in checks.items() if not ok]
    if not checks['not_test']:
        status = 'test data'
    elif not contests:
        status = 'no contests found (schema not recognized)'
    elif not (checks['election_name'] and checks['election_date'] and checks['statewide_results']):
        status = 'wrong or unidentified election: ' + ', '.join(k for k in ('election_name', 'election_date', 'statewide_results') if not checks[k])
    elif total_votes == 0:
        status = 'zero report (no votes yet)'
    elif not checks['created_after_polls_close']:
        # votes in a file generated before polls close cannot be production results (pre-election testing)
        status = 'votes present but file generated before polls closed: treated as test data'
    else:
        status = 'contains votes'
    return {'url': url, 'sha256': h, 'last_updated': stamp, 'flags': flags, 'data_status': status, 'total_votes': total_votes,
            'election_name': el_name, 'election_date': el_date, 'identity_checks': checks, 'identity_failed': failed,
            'contests': contests[:2000], 'contest_count': len(contests),
            'top_keys': list(data.keys())[:30] if isinstance(data, dict) else type(data).__name__}


# ----------------------------------------------------------------- plan
PRIORITY_TIER = {}

def cmd_plan(a):
    reg = load(a.registry)
    now = now_utc(a.now)
    direct = load(a.direct, {}) or {}
    covered = set(direct.get('groups_covered', []))
    covered_src = set(direct.get('sources_covered', []))
    groups = []
    stats = ((load(a.bundle, {}) or {}).get('_state') or {}).get('source_stats', {}) if a.bundle else {}
    def order(g):
        # sources skipped by the time limit last run move up, right after tier 1, so the same sources are not skipped run after run
        tier = g.get('tier', 3)
        skipped = (stats.get(g['source_id']) or {}).get('skip_streak', 0)
        return (min(tier, 1.5) if skipped else tier, -skipped, reg['fetch_groups'].index(g))
    bstate = ((load(a.bundle, {}) or {}).get('_state') or {}) if a.bundle else {}
    expanded = []
    for g in reg['fetch_groups']:
        if not g.get('per_state'):
            expanded.append(g); continue
        tc = (bstate.get('table_coverage') or {}).get(g['states_from'], {})
        wanted = tc.get('not_returned') or g.get('default_states', [])
        nrep = bstate.get('not_reporting') or {}
        keyset = {m['id'].split('.')[0] for m in reg['measures'] if m.get('key')}
        for ab in sorted(wanted, key=lambda x: (x not in keyset, x)):
            mid = f'{ab}.early_total'
            if mid in nrep and (now - now_utc(nrep[mid]['at'])).total_seconds() < 6 * 3600:
                continue
            name = next((m['label'] for m in reg['measures'] if m['id'] == mid), None)
            if not name:
                continue
            expanded.append(dict(g, id=f"{g['id']}_{ab}", tier=2 if ab in keyset else 3, per_state=False, measure_only=mid,
                                 urls=[g['url_template'].replace('{slug}', name.lower().replace(' ', '-'))],
                                 extract=g['extract'].replace('{state}', name)))
    reg = dict(reg, fetch_groups=expanded)
    blocked_groups = []
    for g in sorted(reg['fetch_groups'], key=order):
        if g['id'] in covered or g['source_id'] in covered_src or g.get('how') == 'direct_only':
            continue
        if g.get('unattended') == 'blocked':
            blocked_groups.append({'group': g['id'], 'source_id': g['source_id'], 'reason': g.get('blocked_reason')})
            continue
        urls = []
        for u in g['urls']:
            for back in ([0, 1] if '{' in u else [0]):
                d = (now.astimezone(AZ) - dt.timedelta(days=back)).date()
                urls.append(u.replace('{YMD}', d.isoformat()).replace('{MDY2}', d.strftime('%m-%d-%y')))
        # the web reader can serve a cached copy of a page (seen 2026-10-23: Minnesota's data page came back with the 10/16 version
        # while the live page showed 10/23). A per-run query parameter forces a fresh read; the plain URL is the fallback.
        token = now.strftime('%Y%m%d%H%M')
        fetch_urls = [u + ('&' if '?' in u else '?') + f'_ecc={token}' for u in urls]
        groups.append({'group': g['id'], 'tier': g.get('tier', 3), 'skipped_last_runs': (stats.get(g['source_id']) or {}).get('skip_streak', 0),
                       'source_id': g['source_id'], 'how': g.get('how', 'webfetch'),
                       'urls': urls, 'fetch_urls': fetch_urls, 'search_query': g.get('search_query'), 'extract': g['extract'],
                       'measures': [g['measure_only']] if g.get('measure_only') else [m['id'] for m in reg['measures'] if g['source_id'] in m['sources']]})
    print(json.dumps({'now': iso(now), 'groups': groups, 'covered_by_direct_download': sorted(covered),
                      'not_fetched_permission_blocked': blocked_groups,
                      'instructions': 'Fetch groups in this order (tier 1 first). WebFetch each group\'s fetch_urls (fresh, cache-busting); only if that '
                                      'fails, retry the plain url and set "cache_bust": false in the attempt. Record the plain url (not the fetch_url) in figures. '
                                      'Before each group run `vp.py clock`; stop when it says so and '
                                      'record the rest as status "skipped". Retry a failed fetch once. Record every attempt with started_at/ended_at. '
                                      'Each figure needs verbatim evidence (number), label_evidence (its label/column header), as_of_evidence '
                                      '(the date text) and election text. Use status "no_data" when the page loads but has no new figure.'}, indent=1))


# ----------------------------------------------------------------- observation-date basis, definitions, coverage
PERIOD_WORDS = re.compile(r'\bthrough\b|\bthru\b|\bas of\b|cast through|data (?:as of|through)|reporting period|cumulative|so far|to date|\bfor (?:mon|tue|wed|thu|fri|sat|sun)', re.I)
PUB_WORDS = re.compile(r'updated|published|posted|generated|compiled|released|report date|last modified', re.I)

def asof_basis(f):
    """'period' when the saved date text states the period the data covers ('through', 'as of', 'so far'); 'publication' when it only
    gives a publication/update time; 'unstated' otherwise. Only 'period' dates may justify moving to a different source because it is newer."""
    if f.get('_basis'):
        return f['_basis']
    if f.get('asof_basis') in ('period', 'publication'):
        return f['asof_basis']
    t = lc(f.get('as_of_evidence') or f.get('evidence'))
    if PERIOD_WORDS.search(t):
        return 'period'
    if PUB_WORDS.search(t):
        return 'publication'
    return 'unstated'

def has_official(R, mid):
    return any(R.sources.get(x, {}).get('class', '').startswith('official') for x in R.measures.get(mid, {}).get('sources', []))

def page_dated(R, sid):
    return R.sources.get(sid, {}).get('row_date_basis') == 'page'

def def_key(R, mid, sid):
    m = R.measures.get(mid, {})
    return (m.get('def_override') or {}).get(sid) or R.sources.get(sid, {}).get('def_key') or sid

def cov_key(R, mid, sid):
    m = R.measures.get(mid, {})
    return (m.get('coverage_override') or {}).get(sid) or R.sources.get(sid, {}).get('coverage_key') or 'full'


# ----------------------------------------------------------------- validation
def election_ok(el, context=''):
    """Election text must identify the 2026 general (year or Nov 3). If it names no election year at all (e.g. a press-release
    headline), it passes only when the saved date text shows 2026 and the figure falls in the general-election window (checked by the caller)."""
    t = lc(el)
    if context and not re.search(r'\b(19|20)\d\d\b', t) and not re.search(r'\bprimary\b|\brunoff\b|special election', t):
        if '2026' in lc(context) or re.search(r'\b\d{1,2}/\d{1,2}/26\b', lc(context)):
            return True
    if re.search(r'\bprimary\b|\brunoff\b|special election', t):
        return False
    has2026 = '2026' in t
    nov3_general = bool(re.search(r'(november|nov\.?)\s*3(rd)?\b|\b11/0?3(/(20)?26)?\b', t)) and ('general' in t or 'election date' in t or '2026' in t)
    if re.search(r'\b(2024|2022|2020)\b', t) and not has2026:
        return False
    return has2026 or nov3_general

def validate_figure(f, R, run_now, registered):
    """Return (ok, reason, flags). Checks every figure as a unit: number, label, geography, election, metric definition,
    observation date, registered host, precision and plausibility. flags may include 'comparison_only' or 'over_registration'."""
    flags = []
    mid, sid = f.get('measure'), f.get('source_id')
    if mid not in R.measures:
        return False, f'unknown measure {mid}', flags
    m = R.measures[mid]
    if sid not in m['sources']:
        return False, f'source {sid} is not registered for {mid}', flags
    src = R.sources[sid]
    if not host_ok(f.get('url', ''), src['hosts']):
        return False, f'URL host {host(f.get("url",""))} is not a registered host for {sid}', flags
    el = f.get('election') or ''
    ctx = ' '.join(str(f.get(k) or '') for k in ('as_of_evidence', 'evidence'))
    if not election_ok(el, ctx):
        return False, f'election check failed ("{el[:60]}")', flags
    # observation date: parse, window, and must be written in the saved source text
    t = parse_asof(f.get('data_as_of'))
    if not t:
        return False, 'missing or unparseable data_as_of', flags
    if t.date() < VALID_FROM or t > run_now + dt.timedelta(hours=26):
        return False, f'data_as_of {f.get("data_as_of")} outside the 2026 general reporting window', flags
    d0 = asof_date(f.get('data_as_of'))
    date_text = ' '.join(str(f.get(k) or '') for k in ('as_of_evidence', 'evidence', 'label_evidence', 'election'))
    found = dates_in(date_text)
    if not any(0 <= (x - d0).days <= 1 for x in found):
        return False, f'observation date {f.get("data_as_of")} is not shown in the saved source text', flags
    # label: verbatim, and describes ballots cast/returned
    label = lc(f.get('metric_label'))
    label_text = lc((f.get('label_evidence') or '') + ' ' + (f.get('evidence') or ''))
    if not label:
        return False, 'missing metric label', flags
    if re.search(r'\bmodel(?:ed|led)?\b|estimat|projected|forecast|expected', label):
        return False, f'"{label[:40]}" is a modeled or estimated figure, not a reported count', flags
    if label not in label_text:
        return False, f'metric label "{label[:40]}" does not appear in the saved source text', flags
    def_label = label + ' ' + ' '.join(lc(p.get('label')) for p in ((f.get('calc') or {}).get('parts') or []))
    if m['kind'] in ('early_total', 'early_returned', 'county_returned'):
        label = def_label
        if any(w in label for w in BAD_DEF_WORDS) and not re.search(r'returned|cast|voted|received|accepted', label):
            return False, f'metric label "{label[:50]}" is a request/issue count, not ballots cast/returned', flags
        if not any(w in label for w in GOOD_DEF_WORDS):
            return False, f'metric label "{label[:50]}" does not describe ballots cast or returned', flags
    # geography
    geo_words = [w.lower() for w in m['geo_words']]
    geo_text = lc((f.get('evidence') or '') + ' ' + (f.get('label_evidence') or '') + ' ' + (f.get('geo_evidence') or ''))
    if not src.get('single_geography') and sid not in m.get('geo_implicit_sources', []) and not any(w in geo_text for w in geo_words):
        return False, 'saved source text does not name the geography', flags
    # number
    prec = f.get('precision')
    calc = f.get('calc')
    try:
        v = float(f['value'])
    except Exception:
        return False, 'value is not a number', flags
    if v <= 0:
        return False, 'value must be positive', flags
    if calc:
        ok, why = validate_calc(f, v)
        if not ok:
            return False, why, flags
        prec = 'calculated'
    else:
        if prec not in ('exact', 'rounded'):
            return False, 'precision must be exact or rounded', flags
        if not evidence_has(f.get('evidence'), v, prec):
            return False, 'evidence text does not contain the reported value', flags
    if prec == 'rounded' and not m.get('allow_rounded', False):
        flags.append('comparison_only')
    mail, inp = f.get('mail'), f.get('in_person')
    if mail is not None and inp is not None and prec in ('exact', 'calculated'):
        if abs((float(mail) + float(inp)) - v) > max(2, v * 0.002):
            return False, 'mail + in_person does not equal total', flags
    # registration denominator: hard rule only when the denominator is total registration, current, and same geography
    reg = registered.get(m.get('geo_key'))
    if reg and v > reg['registered'] * 1.01:
        definitive = reg.get('registration_type') in ('total', 'active+inactive') and asof_date(reg.get('as_of')) and asof_date(reg.get('as_of')) >= dt.date(2026, 7, 1)
        if definitive:
            return False, f"value exceeds total registered voters ({int(reg['registered']):,}, {reg.get('registration_type')}, as of {reg.get('as_of')})", flags
        flags.append('over_registration')
    f['_prec'] = prec
    # tracker pages carry one page-level date for every state; it is not each state's observation date
    f['_basis'] = 'page date' if src.get('row_date_basis') == 'page' else None
    return True, 'ok', flags

def validate_calc(f, v):
    c = f['calc']
    if c.get('op') != 'sum' or not isinstance(c.get('parts'), list) or len(c['parts']) < 2:
        return False, 'calculation must be a sum of at least two printed parts'
    tot = 0
    last_days = []
    for p in c['parts']:
        try:
            pv = float(p['value'])
        except Exception:
            return False, 'calculation part is not a number'
        txt = (p.get('evidence') or '') + ' ' + (f.get('evidence') or '')
        if not evidence_has(txt, pv, 'exact'):
            return False, f'calculation part {fmt_int(pv)} is not in the saved source text'
        if not p.get('label') or lc(p['label']) not in lc(txt + ' ' + (f.get('label_evidence') or '')):
            return False, 'calculation part label is not in the saved source text'
        if p.get('heading') and lc(p['heading']) not in lc(txt + ' ' + (f.get('label_evidence') or '')):
            return False, f'table heading "{p["heading"][:40]}" is not in the saved source text'
        if p.get('rows') is not None:
            rs = p['rows']
            if not isinstance(rs, list) or not rs:
                return False, 'detail rows missing for a part that must be checked against its table'
            try:
                rsum = sum(float(r['value']) for r in rs)
            except Exception:
                return False, 'detail row value is not a number'
            if abs(rsum - pv) >= 0.5:
                return False, f'detail rows add up to {fmt_int(rsum)}, not the printed TOTAL {fmt_int(pv)} ({p.get("label")})'
            for r in rs:
                if not evidence_has((r.get('evidence') or '') + ' ' + txt, float(r['value']), 'exact'):
                    return False, f'detail row {fmt_int(float(r["value"]))} is not in the saved source text'
            days = [d for r in rs for d in dates_in(r.get('label') or '') if float(r['value']) > 0]
            if not days:
                return False, 'detail rows carry no dates'
            last_days.append(max(days))
        tot += pv
    if abs(tot - v) >= 0.5:
        return False, f'calculation does not add up ({fmt_int(tot)} vs {fmt_int(v)})'
    if last_days:
        common = min(last_days)
        if asof_date(f.get('data_as_of')) != common:
            return False, f'data date {f.get("data_as_of")} is not the last day reported in every table ({common.isoformat()})'
    return True, 'ok'

def calc_note(f):
    c = f.get('calc')
    if not c:
        return None
    return 'Calculated: ' + ' + '.join(f"{p['label']} ({fmt_int(p['value'])})" for p in c['parts']) + f" = {fmt_int(f['value'])}, each part as printed by the source."


# ----------------------------------------------------------------- prepare
def cmd_prepare(a):
    R = Registry(load(a.registry))
    bundle = load(a.bundle)
    for d in ('source_status', 'reviews'):
        bundle.setdefault(d, [])
    prev_bundle = copy.deepcopy(bundle)
    obs = load(a.obs, {}) or {}
    direct = load(a.direct, {}) or {}
    run_now = now_utc(a.now)
    run_id = 'r' + run_now.strftime('%Y%m%dT%H%M')
    state = bundle.setdefault('_state', {})
    for k, v in (('alerts', {}), ('fail_streak', {}), ('briefed', []), ('reviews', {}), ('source_stats', {}), ('logged_conflicts', [])):
        state.setdefault(k, v)
    out = a.out
    os.makedirs(out, exist_ok=True)
    log = {'accepted': [], 'rejected': [], 'comparison_only': [], 'no_change': [], 'reviews': [], 'source_changes': [], 'corrections': [],
           'stale': [], 'unreachable_measures': [], 'retained': [], 'notes': []}
    started = now_utc(obs.get('started_at')) if obs.get('started_at') else run_now
    slot, cutoff, target = budget(started, run_now)

    registered = {}
    for r in bundle.get('registration', []):
        if isinstance(r.get('registered'), (int, float)):
            registered[r['abbr']] = r

    # ---- attempts (agent + direct) -> checks rows, source health
    attempts = [dict(x, method=x.get('method', 'fetch tool + agent')) for x in obs.get('attempts', [])] + direct.get('attempts', [])
    # sources with no permitted automatic method in an unattended run are recorded as permission-blocked every run (never silently dropped,
    # never 'no data'); the last verified figure stays published with its date
    seen_src = {x['source_id'] for x in attempts if x.get('status') in ('ok', 'no_data')}
    # a direct download that reached the host (any answer other than the proxy's refusal) is this environment's real outcome for that
    # source; the permission-blocked label describes the Claude workspace and must not be added on top of it
    seen_src |= {x['source_id'] for x in direct.get('attempts', []) if x.get('status') != 'blocked'}
    for g in R.reg.get('fetch_groups', []):
        if g.get('unattended') == 'blocked' and g['source_id'] not in seen_src and not any(x['source_id'] == g['source_id'] for x in obs.get('attempts', [])):
            attempts.append({'source_id': g['source_id'], 'group': g['id'], 'url': (g.get('urls') or [None])[0], 'attempt': 0, 'method': 'none (no permitted unattended method)',
                             'status': 'permission_blocked', 'detail': g.get('blocked_reason', 'no permitted automatic method in an unattended run')[:300],
                             'started_at': iso(run_now), 'ended_at': iso(run_now)})
    for at in attempts:
        st = at.get('status') or 'error'
        if at.get('started_at') and at.get('ended_at'):
            try:
                at['seconds'] = round((now_utc(at['ended_at']) - now_utc(at['started_at'])).total_seconds())
            except Exception:
                pass
        at['status'] = st if st in ('ok', 'no_data', 'unreachable', 'blocked', 'error', 'timeout', 'skipped', 'permission_blocked') else 'error'
        if st not in ('permission_blocked',) and re.search(r'(?i)permission request|PROVENANCE_REQUIRED|not answered in time|requires? (?:user )?approval', str(at.get('detail') or '') + str(at.get('raw_error') or '')):
            # a web-reader refusal for lack of approval is never 'no data' and never an ordinary site error
            at['status'] = 'permission_blocked'
    # direct-download blocks are expected until the domain is allowed; they never count as a failure when the agent path read the source
    src_ok, src_nodata, src_fail, src_skipped, src_secs, src_perm = set(), set(), {}, set(), {}, set()
    # sources read only by direct download (no web-reader fallback): a failed download is a failed source
    # sources whose only permitted unattended method is the direct download (no reader fallback this run): a direct failure is a failure
    direct_only_src = {g['source_id'] for g in R.reg.get('fetch_groups', []) if g.get('how') == 'direct_only' or g.get('unattended') == 'blocked'}
    websearch_src = {g['source_id'] for g in R.reg.get('fetch_groups', []) if g.get('how') == 'websearch'}
    for at in attempts:
        if at['status'] == 'ok':
            src_ok.add(at['source_id'])
        elif at['status'] == 'no_data':
            src_nodata.add(at['source_id'])
        if isinstance(at.get('seconds'), (int, float)) and at.get('method') != 'direct':
            src_secs[at['source_id']] = src_secs.get(at['source_id'], 0) + at['seconds']
    reached = src_ok | src_nodata
    for at in attempts:
        sid = at['source_id']
        if at['status'] == 'skipped' and sid not in reached:
            src_skipped.add(sid)
        elif at['status'] in ('unreachable', 'blocked', 'error', 'timeout', 'permission_blocked') and sid not in reached and (at.get('method') != 'direct' or sid in direct_only_src):
            src_fail.setdefault(sid, []).append(at)
            if at['status'] == 'permission_blocked':
                src_perm.add(sid)
    for sid, secs in src_secs.items():
        if secs > SOURCE_TIMEOUT_S:
            log['notes'].append(f'{R.name(sid)} took {secs}s (limit {SOURCE_TIMEOUT_S}s)')
            if sid not in reached:
                src_fail.setdefault(sid, []).append({'status': 'timeout'})
    retry_missing = [s for s, lst in src_fail.items() if len([x for x in lst if x.get('status') not in ('timeout', 'blocked', 'permission_blocked')]) < 2
                     and not any(x.get('status') in ('timeout', 'blocked', 'permission_blocked') for x in lst)]
    for s in reached:
        state['fail_streak'][s] = 0
    for s in src_fail:
        state['fail_streak'][s] = state['fail_streak'].get(s, 0) + 1
    STATUS_LABEL = {'ok': 'checked', 'no_data': 'no new data', 'unreachable': 'unreachable', 'blocked': 'unreachable', 'error': 'error',
                    'timeout': 'timed out', 'skipped': 'skipped (time budget)', 'permission_blocked': 'blocked: web-reader permission not available in an unattended run'}
    check_rows = []
    for i, at in enumerate(attempts):
        check_rows.append({'id': f'{run_id}-{i}', 'run_id': run_id, 'run_at': iso(run_now), 'source': R.name(at['source_id']),
                           'source_id': at['source_id'], 'url': at.get('url'), 'attempt': at.get('attempt', 1), 'method': at.get('method'),
                           'status': STATUS_LABEL[at['status']], 'seconds': at.get('seconds'),
                           'detail': (at.get('detail') or '')[:300] + (f" [{at['sha256'][:12]}]" if at.get('sha256') else '')})

    # ---- figures (direct first so code-extracted figures win duplicates)
    all_figs = [dict(f, method='direct') for f in direct.get('figures', [])] + [dict(f, method=f.get('method', 'fetch tool + agent')) for f in obs.get('figures', [])]
    for dst, srcm in (R.reg.get('derive_measures') or {}).items():
        for f in list(all_figs):
            if f.get('measure') == srcm and f.get('source_id') in R.measures.get(dst, {}).get('sources', []) \
                    and not any(g.get('measure') == dst and g.get('source_id') == f['source_id'] for g in all_figs):
                all_figs.append(dict(f, measure=dst, derived_from=srcm))
    valid_by_measure, seen, flags_by = {}, set(), {}
    for f in all_figs:
        key = (f.get('measure'), f.get('source_id'))
        ok, why, flags = validate_figure(f, R, run_now, registered)
        if ok and key in seen:
            ok, why = False, 'duplicate figure for the same measure and source in this run (not counted twice)'
        if not ok:
            log['rejected'].append({'measure': f.get('measure'), 'source_id': f.get('source_id'), 'value': f.get('value'), 'reason': why, 'method': f.get('method')})
            continue
        seen.add(key)
        if 'comparison_only' in flags:
            log['comparison_only'].append({'measure': f['measure'], 'source_id': f['source_id'], 'value': f['value'],
                                           'reason': 'rounded figure; shown in source comparison only (this measure requires an exact count)'})
            upsert_compare(bundle, f['measure'], f, R, primary=False)
            continue
        f['_flags'] = flags
        valid_by_measure.setdefault(f['measure'], []).append(f)

    # ---- table completeness for multi-row sources: expected vs unique jurisdictions returned, missing rows, duplicates
    table_cov = {}
    for sid, src in R.sources.items():
        if src.get('single_geography') or src.get('table_check') is False:
            continue
        state_measures = [k for k, mm in R.measures.items() if sid in mm['sources'] and re.match(r'^[A-Z]{2}\.early_total$', k) and not k.startswith('US.')]
        if len(state_measures) < 5:
            continue
        mine = [f for f in all_figs if f.get('source_id') == sid and f.get('measure') in state_measures]
        if not mine and not any(at['source_id'] == sid for at in attempts):
            continue
        from collections import Counter
        cnt = Counter(f['measure'] for f in mine)
        stated = max([int(at['rows_stated']) for at in attempts if at['source_id'] == sid and str(at.get('rows_stated') or '').isdigit()] or [0]) or None
        missing = sorted(k.split('.')[0] for k in set(state_measures) - set(cnt))
        dups = sorted(k.split('.')[0] for k, c in cnt.items() if c > 1)
        complete = (stated is not None and len(cnt) >= stated) and not dups
        table_cov[sid] = {'source': R.name(sid), 'rows_stated_by_source': stated, 'unique_rows_returned': len(cnt), 'rows_registered': len(state_measures),
                          'not_returned': missing, 'duplicates': dups, 'complete': complete}
    state['table_coverage'] = table_cov

    # ---- apply per measure
    rows = {mid: (ds, row) for mid, ds, row in measure_rows(bundle)}
    reviews = state['reviews']
    for mid, (ds, row) in rows.items():
        if mid not in R.measures:
            continue
        m = R.measures[mid]
        vf = value_field(ds)
        prev_sid = R.source_for_row(mid, row)
        prev_val, prev_asof = row.get(vf), row.get('as_of')
        pv = round(float(prev_val)) if prev_val is not None else None
        cands = sorted(valid_by_measure.get(mid, []), key=lambda f: R.priority(mid, f['source_id']))
        attempted = [s for s in m['sources'] if s in reached or s in src_fail]
        accepted, review_hit = None, None
        switch_reason_override = None
        stale_resp = None
        prev_age_h = (run_now - parse_asof(prev_asof)).total_seconds() / 3600 if prev_asof and parse_asof(prev_asof) else 1e9
        # (r) repair: an earlier run replaced this figure with an OLDER observation from another source. If the source it
        # replaced still reports a figure at least as recent and at least as large (as the replaced figure AND as the figure now
        # published), return to it (one switch, logged).
        last_sc = next((e for e in bundle.get('source_changes', []) if e.get('measure') == mid), None)
        if (last_sc and last_sc.get('to_source') == R.name(prev_sid) and last_sc.get('from_as_of') and last_sc.get('to_as_of')
                and asof_key(last_sc['to_as_of']) < asof_key(last_sc['from_as_of'])):
            fv = last_sc.get('from_value')
            back_to = [f for f in cands if R.name(f['source_id']) == last_sc.get('from_source') and f['source_id'] != prev_sid
                       and asof_key(f['data_as_of']) >= asof_key(last_sc['from_as_of']) and fv is not None and round(float(f['value'])) >= round(float(fv)) and (pv is None or round(float(f['value'])) >= pv)
                       and 'over_registration' not in f['_flags']]
            if back_to:
                accepted = ('switch', back_to[0])
                switch_reason_override = (f"reverted an earlier switch to an older observation ({last_sc['to_source']}, data as of {last_sc['to_as_of']}); "
                                          f"{last_sc['from_source']} reports {back_to[0]['data_as_of']}")
        # (a) a higher-priority source has same-or-newer data -> switch (attribution moves up; never counted as new ballots)
        for f in ([] if accepted else cands):
            sid, v, asof = f['source_id'], round(float(f['value'])), f['data_as_of']
            if sid != prev_sid and R.priority(mid, sid) < R.priority(mid, prev_sid):
                # a preferred source takes over when its data period is at least as recent; a date that is only a
                # publication/update time must be strictly newer, because the data behind it may be a day older
                same_kind = asof_basis(f) == 'period' or (page_dated(R, sid) and page_dated(R, prev_sid))   # page date vs page date compares like with like
                ok_date = (not prev_asof) or (asof_key(asof) >= asof_key(prev_asof) if same_kind else
                                              (asof_date(asof) and asof_date(prev_asof) and asof_date(asof) > asof_date(prev_asof)))
                if not ok_date:
                    continue
                if 'over_registration' in f['_flags']:
                    review_hit = ('over_registration', f); break
                accepted = ('update' if pv is None else 'switch', f); break
        # (a2) a page-dated tracker is published for a measure that has an official source, and a higher-priority source
        # with its own stated data period was read this run: return to it (the tracker's page date is not an observation date)
        if not accepted and not review_hit and page_dated(R, prev_sid) and has_official(R, mid):
            back = [f for f in cands if R.priority(mid, f['source_id']) < R.priority(mid, prev_sid) and asof_basis(f) in ('period', 'unstated', 'publication')
                    and not page_dated(R, f['source_id']) and 'over_registration' not in f['_flags']]
            # the tracker's own daily series says when its count first appeared; an official report covering an earlier day
            # is an older observation, not a correction: keep the published count and record the older response
            count_since = row.get('tracker_value_since') or row.get('count_since')
            # without the tracker's daily series, its page date is the only date known for its count: an official report for an
            # earlier day is still an older observation and never replaces it (it is shown as a stale source response instead)
            ref_date = count_since or prev_asof
            older = [f for f in back if ref_date and asof_date(f['data_as_of']) and asof_date(ref_date) and asof_date(f['data_as_of']) < asof_date(ref_date)]
            if older:
                stale_resp = older[0]
                back = [f for f in back if f not in older]
            if back:
                accepted = ('switch', back[0])
                switch_reason_override = (f"returned to {R.name(back[0]['source_id'])}: the tracker's date was its page date, not an observation date "
                                          f"for this state; {R.name(back[0]['source_id'])} states its own data date ({back[0]['data_as_of']})")
        # (b) the published source itself
        if not accepted and not review_hit:
            f = next((x for x in cands if x['source_id'] == prev_sid), None)
            if f:
                v, asof = round(float(f['value'])), f['data_as_of']
                if prev_asof and asof_key(asof) < asof_key(prev_asof):
                    stale_resp = f
                elif prev_asof and asof_key(asof) == asof_key(prev_asof) and pv is not None and v == pv:
                    accepted = ('nochange', f)
                elif 'over_registration' in f['_flags']:
                    review_hit = ('over_registration', f)
                elif (pv is not None and v > pv and asof_key(asof) == asof_key(prev_asof) and page_dated(R, prev_sid) and v <= pv * 1.25
                      and ((f.get('value_unchanged_since') and asof_date(f['value_unchanged_since']) == asof_date(asof))
                           or (f.get('components_n', 0) >= 40 and f.get('components_sum') == v))):
                    # a tracker refreshes its page several times a day under one page date: an increase that the tracker's own
                    # daily series records on that same day, within normal growth, is a routine update (decreases still go to review)
                    accepted = ('update', f)
                    f['_intraday'] = True
                elif pv is not None and v != pv and asof_key(asof) == asof_key(prev_asof) and not f.get('is_correction'):
                    review_hit = ('same_period_change', f)
                elif pv is not None and v < pv and not f.get('is_correction'):
                    review_hit = ('decrease', f)
                elif pv is not None and pv > 0 and v > pv * 3 and v - pv > 50000 and not f.get('is_correction'):
                    review_hit = ('jump', f)
                elif f.get('is_correction') and pv is not None and v != pv:
                    accepted = ('correction', f)
                else:
                    accepted = ('update', f)
        # (c) published figure is stale: newest eligible backup (ties by priority); identical value = same underlying data
        # Backups are chosen by observation date (the period the data covers, never a page's publication date), and only
        # when they measure the same thing (definition) over the same coverage. A figure is never replaced because an
        # article is newer. When the published figure has gone stale and no like-for-like backup exists, a backup with a
        # different definition may be used, labeled as such.
        on_backup = bool(m['sources']) and prev_sid != m['sources'][0]
        switch_note = None
        if (not accepted or accepted[0] == 'nochange') and not review_hit and (prev_age_h > m.get('stale_hours', 36) or on_backup):
            stale_mode = prev_age_h > m.get('stale_hours', 36)
            official_measure = has_official(R, mid)
            base = [x for x in cands if x['source_id'] != prev_sid and (asof_basis(x) == 'period' or (asof_basis(x) == 'page date' and not official_measure))
                    and 'over_registration' not in x['_flags'] and (pv is None or round(float(x['value'])) != pv)
                    and (not prev_asof or (asof_key(x['data_as_of']) > asof_key(prev_asof) if stale_mode
                                           else (asof_date(x['data_as_of']) and asof_date(prev_asof) and asof_date(x['data_as_of']) > asof_date(prev_asof))))]
            same = [x for x in base if def_key(R, mid, x['source_id']) == def_key(R, mid, prev_sid) and cov_key(R, mid, x['source_id']) == cov_key(R, mid, prev_sid)]
            elig = same or ([x for x in base if cov_key(R, mid, x['source_id']) == cov_key(R, mid, prev_sid)] if stale_mode else [])
            if elig:
                best = max(elig, key=lambda x: (asof_key(x['data_as_of']), -R.priority(mid, x['source_id'])))
                accepted = ('switch', best)
                if best not in same:
                    switch_note = (f"Different definition: {R.sources[best['source_id']].get('definition')} (was {R.sources.get(prev_sid, {}).get('definition', prev_sid)}). "
                                   'Used only because the published figure is stale and no backup with the same definition reported newer data.')
            for x in cands:
                if x['source_id'] != prev_sid and x not in base and asof_basis(x) != 'period' and (not prev_asof or asof_key(x['data_as_of']) > asof_key(prev_asof)):
                    log['notes'].append(f"{mid}: {R.name(x['source_id'])} not used as a newer backup: its date ({x['data_as_of']}) is a publication/update time, not a stated data period")
        if stale_resp is None and accepted and accepted[0] == 'nochange':
            olders0 = [x for x in cands if x is not accepted[1] and R.priority(mid, x['source_id']) < R.priority(mid, prev_sid) and prev_asof
                       and asof_key(x['data_as_of']) < asof_key(prev_asof) and not page_dated(R, x['source_id'])]
            if olders0:
                stale_resp = olders0[0]
        if not accepted and not review_hit and not stale_resp:
            olders = [x for x in cands if R.priority(mid, x['source_id']) <= R.priority(mid, prev_sid) and prev_asof and asof_key(x['data_as_of']) < asof_key(prev_asof)]
            if olders:
                stale_resp = olders[0]
        if stale_resp:
            row['stale_response'] = {'source': R.name(stale_resp['source_id']), 'shown_as_of': stale_resp['data_as_of'], 'value': round(float(stale_resp['value'])),
                                     'at': iso(run_now), 'label': 'stale source response; freshness not verified this run'}
            log.setdefault('stale_responses', []).append({'measure': mid, 'source_id': stale_resp['source_id'], 'shown_as_of': stale_resp['data_as_of'],
                                                          'published_as_of': prev_asof})
        else:
            row.pop('stale_response', None)
        # ---- review handling (visible, re-checked every run, both observations kept)
        rev = reviews.get(mid)
        if review_hit:
            kind, f = review_hit
            v = round(float(f['value']))
            obsrec = {'at': iso(run_now), 'run_id': run_id, 'value': v, 'as_of': f['data_as_of'], 'source_id': f['source_id'],
                      'evidence': (f.get('evidence') or '')[:300], 'url': f.get('url')}
            rising = (rev and rev['status'] == 'open' and kind == 'same_period_change' and rev['kind'] == 'same_period_change' and rev['source_id'] == f['source_id']
                      and page_dated(R, f['source_id']) and pv is not None and v > rev['observed_value'] > pv and v <= pv * 1.25
                      and (run_now - now_utc(rev['last_seen'])).total_seconds() >= REVIEW_MIN_GAP_MIN * 60)
            if rising:
                # a tracker count that keeps rising under one page date never repeats a value; two consecutive hourly reads that both
                # rise (published < first read < second read) confirm it. Decreases and non-monotonic changes stay under review.
                rev['history'] = (rev['history'] + [obsrec])[-10:]
                rev['reads'] += 1
                rev['status'] = 'confirmed'
                rev['resolution'] = (f"Adopted: rising tracker count on consecutive hourly reads ({fmt_int(pv)} -> {fmt_int(rev['observed_value'])} -> {fmt_int(v)}) "
                                     'under the same page date; no identical repeat is required for a monotonic increase.')
                f['_rising'] = True
                accepted = ('update', f)
                review_hit = None
            elif rev and rev['status'] == 'open' and rev['source_id'] == f['source_id'] and rev['observed_value'] == v and asof_key(rev['observed_as_of']) == asof_key(f['data_as_of']):
                if (run_now - now_utc(rev['last_seen'])).total_seconds() >= REVIEW_MIN_GAP_MIN * 60:
                    rev['reads'] += 1
                    rev['history'] = (rev['history'] + [obsrec])[-10:]
                rev['last_seen'] = iso(run_now)
            else:
                rev = {'id': f'rev-{mid}-{run_id}', 'measure': mid, 'geography': m['label'], 'kind': kind, 'status': 'open',
                       'source_id': f['source_id'], 'source': R.name(f['source_id']), 'published_value': pv, 'published_as_of': prev_asof,
                       'observed_value': v, 'observed_as_of': f['data_as_of'], 'first_seen': iso(run_now), 'last_seen': iso(run_now),
                       'reads': 1, 'history': [obsrec], 'url': f.get('url'), 'resolution': None}
                reviews[mid] = rev
            auto = kind in ('decrease', 'same_period_change', 'jump')
            if rising:
                pass
            elif auto and rev['reads'] >= REVIEW_CONFIRM_READS:
                rev['status'] = 'confirmed'
                rev['resolution'] = f"Adopted after {rev['reads']} consecutive hourly reads showed the same figure; the source gave no explanation."
                f = dict(f, is_correction=True, correction_note=f"Source changed the figure without a stated explanation ({kind.replace('_', ' ')}); "
                                                                  f"confirmed on {rev['reads']} consecutive hourly reads since {rev['first_seen']}")
                accepted = ('correction', f)
            else:
                log['reviews'].append({'measure': mid, 'kind': kind, 'reads': rev['reads'], 'published': pv, 'observed': v})
        elif rev and rev['status'] == 'open':
            if accepted and accepted[0] != 'nochange':
                rev['status'] = 'resolved'; rev['resolution'] = f'Superseded by a validated figure ({accepted[0]}) on {iso(run_now)}.'
            elif accepted and accepted[0] == 'nochange' and accepted[1]['source_id'] == rev['source_id']:
                rev['status'] = 'resolved'; rev['resolution'] = 'Source returned to the published figure.'
        # ---- comparison rows for every validated figure
        for f in cands:
            upsert_compare(bundle, mid, f, R, primary=(accepted is not None and f is accepted[1]))
        row['last_attempt'] = iso(run_now) if attempted else row.get('last_attempt')
        if accepted:
            kind, f = accepted
            sid, v = f['source_id'], round(float(f['value']))
            row['last_checked'] = iso(run_now)
            if kind == 'nochange':
                row['check_status'] = 'no change'
                log['no_change'].append({'measure': mid, 'source_id': sid, 'reason': 'same figure and period'})
                log['retained'].append(mid)
            else:
                if kind == 'switch':
                    entry = {'id': f'{run_id}-sc-{len(log["source_changes"])}', 'at': iso(run_now), 'measure': mid, 'geography': m['label'],
                             'from_source': R.name(prev_sid), 'from_value': prev_val, 'from_as_of': prev_asof,
                             'to_source': R.name(sid), 'to_value': v, 'to_as_of': f['data_as_of'],
                             'reason': switch_reason_override or ('higher-priority source has current data' if R.priority(mid, sid) < R.priority(mid, prev_sid) else
                                        ('published source unavailable and its figure is stale; newest registered backup used' if prev_sid in src_fail else
                                         (f'primary source has nothing newer; a registered backup reports a later day than {prev_asof}' if prev_age_h <= m.get('stale_hours', 36) else
                                          f'published source has nothing newer than {prev_asof}; newest registered backup used'))),
                             'note': 'A change of source is not newly reported ballots.' + (' ' + switch_note if switch_note else ''),
                             'to_date_basis': asof_basis(f), 'same_definition': def_key(R, mid, sid) == def_key(R, mid, prev_sid)}
                    log['source_changes'].append(entry)
                    bundle['source_changes'].insert(0, entry)
                elif kind == 'correction':
                    c = {'id': f'{run_id}-k-{len(log["corrections"])}', 'at': iso(run_now), 'geography': m['label'], 'field': m['kind'],
                         'previous': prev_val, 'replacement': v, 'source_url': f['url'],
                         'explanation': (f.get('correction_note') or 'Source revised the figure.') + f' (data as of {f["data_as_of"]})'}
                    log['corrections'].append(c)
                    bundle['corrections'].append(c)
                if kind == 'switch':
                    # provider metadata belongs to the new source; fields the new source does not supply keep their own attribution
                    if 'provider' in row:
                        row['provider'] = R.sources[sid].get('provider')
                    if 'coverage' in row:
                        row['coverage'] = R.sources[sid].get('coverage') or None
                    if row.get('ballots_requested') is not None and not row.get('requested_source'):
                        row['requested_source'] = R.name(prev_sid)
                        row['requested_as_of'] = prev_asof
                row[vf] = v
                row['as_of'] = f['data_as_of']
                for k in ('mail', 'in_person'):
                    if k in row:
                        row[k] = f.get(k)
                row['precision'] = f.get('_prec') or f['precision']
                row['calc_note'] = calc_note(f)
                row['source_id'] = sid
                row['source_name'] = R.name(sid)
                row['source_url'] = f['url']
                row['classification'] = R.sources[sid]['class']
                if 'definition' in row or ds in ('states', 'national'):
                    row['definition'] = R.sources[sid].get('definition') or row.get('definition')
                row['check_status'] = 'changed' if kind != 'correction' else 'corrected'
                # what kind of change this is, so genuinely newer data is never mixed up with a source switch or an older replacement
                ka, kp = asof_key(f['data_as_of']), (asof_key(prev_asof) if prev_asof else None)
                if pv is None:
                    uk = 'first figure for this row'
                elif kind == 'correction':
                    uk = ('same-day change confirmed on two matching reads' if 'same period change' in (f.get('correction_note') or '')
                          else 'correction')
                elif kind == 'switch':
                    uk = ('source switch to an OLDER observation' if kp and ka < kp else 'source switch, same date' if kp and ka == kp else 'source switch to newer data')
                elif f.get('_intraday'):
                    uk = 'same-day tracker update'
                elif f.get('_rising'):
                    uk = 'rising tracker count confirmed on consecutive reads'
                else:
                    uk = 'newer data, same source' if (not kp or ka > kp) else 'same source, same date'
                row['update_kind'] = uk
                log['accepted'].append({'measure': mid, 'source_id': sid, 'value': v, 'as_of': f['data_as_of'], 'kind': kind, 'update_kind': uk,
                                        'delta_same_source': (v - pv) if (kind == 'update' and pv is not None) else None})
            row['tracker_value_since'] = f.get('value_unchanged_since') if page_dated(R, sid) else None
            # the page date of a tracker is kept apart from the date its count first appeared
            row['as_of_basis'] = 'tracker page date' if page_dated(R, sid) else asof_basis(f)
            if f.get('_intraday'):
                log['notes'].append(f"{mid}: same-day tracker update accepted ({fmt_int(pv)} -> {fmt_int(v)}; the tracker's daily series records the change on {f['data_as_of']})")
            row['evidence'] = {'text': (f.get('evidence') or '')[:300], 'label': f.get('metric_label'), 'label_text': (f.get('label_evidence') or '')[:200],
                               'as_of_text': (f.get('as_of_evidence') or '')[:200], 'election': (f.get('election') or '')[:120],
                               'url': f.get('url'), 'method': f.get('method'), 'read_at': iso(run_now), 'raw_sha256': f.get('raw_sha256')}
        else:
            log['retained'].append(mid)
            if stale_resp and not review_hit and not any(r.get('measure') == mid for r in log['rejected']):
                # an answer older than what is already verified: keep the figure and its provenance; this run did not verify freshness
                row['check_status'] = 'stale source response; freshness not verified this run'
            elif review_hit:
                row['check_status'] = 'under review'
                row['last_checked'] = iso(run_now)
            elif any(r.get('measure') == mid for r in log['rejected']):
                row['check_status'] = 'rejected'
            elif cands and has_official(R, mid) and all(page_dated(R, x['source_id']) for x in cands) and not page_dated(R, prev_sid):
                row['check_status'] = 'official kept; tracker figure shown separately'
            elif cands:
                row['check_status'] = 'no change'
                row['last_checked'] = iso(run_now)
            elif m['sources'] and m['sources'][0] in src_perm:
                # the source that would supply this figure cannot be read without an approval an unattended run cannot get
                row['check_status'] = 'permission-blocked (no approval in an unattended run)'
            elif attempted and all(s in src_fail for s in attempted):
                row['check_status'] = 'unreachable'
                log['unreachable_measures'].append(mid)
            elif attempted and all(s in src_nodata or s in src_fail for s in attempted) and any(s in src_nodata for s in attempted):
                row['check_status'] = 'no new data from source'
            elif attempted:
                row['check_status'] = 'not found on source'
            elif any(s in src_skipped for s in m['sources']):
                row['check_status'] = 'skipped (time budget)'
            else:
                row['check_status'] = 'not checked this run'
        # tracker figure shown alongside an official figure (never replaces it on a page date)
        if has_official(R, mid) and not page_dated(R, row.get('source_id') or prev_sid):
            alt = next((x for x in cands if page_dated(R, x['source_id'])), None)
            if alt:
                row['tracker_alt'] = {'source': R.name(alt['source_id']), 'value': round(float(alt['value'])), 'page_date': alt['data_as_of'],
                                      'value_unchanged_since': alt.get('value_unchanged_since'),
                                      'url': alt.get('url'), 'read_at': iso(run_now),
                                      'note': 'Tracker figure with a page-level date; not confirmed by an official report and not used as the published figure.'}
        elif row.get('tracker_alt') and page_dated(R, row.get('source_id') or ''):
            row['tracker_alt'] = None
        rv = reviews.get(mid)
        row['review'] = ({'kind': rv['kind'], 'observed_value': rv['observed_value'], 'observed_as_of': rv['observed_as_of'], 'source': rv['source'],
                          'since': rv['first_seen'], 'reads': rv['reads']} if rv and rv['status'] == 'open' else None)
        t = parse_asof(row.get('as_of'))
        age = (run_now - t).total_seconds() / 3600 if t else None
        row['stale'] = bool(age is not None and age > m.get('stale_hours', 36)) if row.get(vf) is not None else False
        row['age_hours'] = round(age) if age is not None else None
        if row['stale'] and m.get('alert'):
            log['stale'].append({'measure': mid, 'as_of': row.get('as_of'), 'age_hours': round(age)})

    # ---- coverage categories: what happened to each figure this hour
    not_rep = state.setdefault('not_reporting', {})
    for at in attempts:
        if at.get('coverage') == 'not_reporting' and at.get('measure'):
            not_rep[at['measure']] = {'source': R.name(at['source_id']), 'at': iso(run_now), 'detail': (at.get('detail') or '')[:160]}
        for mid_ in at.get('not_reporting_measures') or []:
            not_rep[mid_] = {'source': R.name(at['source_id']), 'at': iso(run_now), 'detail': 'zero ballots in the tracker page data (state not reporting)'}
    hidden_by = {}
    for sid_, t in table_cov.items():
        if not t['complete'] and t['rows_stated_by_source']:
            for ab in t['not_returned']:
                hidden_by[f'{ab}.early_total'] = t['source']
    cat_lists = {}
    for mid, (ds, row) in rows.items():
        if mid not in R.measures:
            continue
        if any(f.get('measure') == mid for fs in valid_by_measure.values() for f in fs):
            not_rep.pop(mid, None)
        cs = row.get('check_status')
        if cs in ('changed', 'corrected'):
            cat = 'updated: ' + (row.get('update_kind') or 'unclassified')
        elif cs == 'stale source response; freshness not verified this run':
            cat = 'stale source response, freshness not verified'
        elif cs == 'no change':
            cat = 'verified, unchanged'
        elif cs == 'under review':
            cat = 'under review'
        elif cs == 'official kept; tracker figure shown separately':
            cat = 'official kept, tracker unconfirmed'
        elif cs == 'rejected':
            cat = 'retrieved, failed validation'
        elif mid in not_rep and (row.get(value_field(ds)) is None or page_dated(R, row.get('source_id') or '')):
            cat = 'not reported by tracker'
        elif mid in hidden_by:
            cat = 'hidden row, not retrieved'
        elif cs == 'unreachable':
            cat = 'source unreachable'
        elif cs == 'permission-blocked (no approval in an unattended run)':
            cat = 'permission-blocked (unattended run)'
        elif cs == 'no new data from source':
            cat = 'source reached, no new data'
        elif cs == 'skipped (time budget)':
            cat = 'skipped (time limit)'
        else:
            cat = 'not checked / not found'
        row['coverage_category'] = cat
        cat_lists.setdefault(cat, []).append(mid.split('.')[0] if mid.endswith('.early_total') else mid)
    state['coverage_categories'] = cat_lists

    # reviews dataset: open first, then recent closed
    allrev = sorted(reviews.values(), key=lambda r: (r['status'] != 'open', r['last_seen']), reverse=False)
    bundle['reviews'] = [dict({k: v for k, v in r.items() if k != 'history'}, history_text=' · '.join(
        f"{h['at'][:16]}Z {fmt_int(h['value'])} (as of {h['as_of']})" for h in r['history'])) for r in allrev][:200]
    if not bundle['reviews']:
        bundle['reviews'] = [{'id': '_none', 'status': 'none', 'geography': '', 'kind': '', 'note': 'Nothing under review'}]
    for k in [k for k, r in reviews.items() if r['status'] != 'open' and (run_now - now_utc(r['last_seen'])).days > 14]:
        del reviews[k]

    # ---- derived: bg_states from states
    st = {r['abbr']: r for r in bundle['states']}
    for b in bundle['bg_states']:
        s = st.get(b['abbr'])
        if s and s.get('total_early') is not None:
            b.update({'early_total': s['total_early'], 'early_as_of': s['as_of'], 'early_source_type': s.get('classification'), 'early_source_url': s.get('source_url'),
                      'stale': s.get('stale', False), 'last_checked': s.get('last_checked'), 'check_status': s.get('check_status'), 'review': s.get('review')})

    # ---- az_trackers from validated AZ statewide figures
    for f in valid_by_measure.get('AZ.early_returned', []):
        tid = {'tpsi': 'tpsi', 'callthemap': 'callthemap', 'weech': 'weech'}.get(f['source_id'])
        for t in bundle['az_trackers']:
            if t['id'] == tid and asof_key(f['data_as_of']) >= asof_key(t.get('as_of')):
                t.update({'returned': round(float(f['value'])), 'as_of': f['data_as_of'], 'precision': f['precision'], 'source_url': f['url']})

    # ---- pace: append same-source points
    for mid, fs in valid_by_measure.items():
        for f in fs:
            series = R.reg.get('pace_series', {}).get(f'{mid}|{f["source_id"]}')
            if not series:
                continue
            d = parse_asof(f['data_as_of']).date()
            if any(p['series_id'] == series and str(p.get('date')) == d.isoformat() and int(p['year']) == 2026 for p in bundle['pace']):
                continue
            if reviews.get(mid, {}).get('status') == 'open':
                continue
            proto = next((p for p in bundle['pace'] if p['series_id'] == series and int(p['year']) == 2026), None)
            if not proto:
                continue
            bundle['pace'].append({'id': f'{run_id}-{series}', 'series_id': series, 'geography': proto['geography'], 'state_key': proto.get('state_key'),
                                   'year': 2026, 'date': d.isoformat(), 'days_before': (ELECTION_DAY - d).days, 'value': round(float(f['value'])),
                                   'mail': f.get('mail'), 'in_person': f.get('in_person'), 'definition': proto['definition'],
                                   'source_name': R.name(f['source_id']), 'source_url': f['url'], 'precision': f.get('_prec') or f['precision']})
            c22 = f.get('compare_2022')
            if c22 and isinstance(c22.get('value'), (int, float)) and evidence_has(f.get('evidence', '') + ' ' + (c22.get('evidence') or ''), c22['value'], 'exact'):
                dbe = (ELECTION_DAY - d).days
                if not any(p['series_id'] == series and int(p['year']) == 2022 and int(p['days_before']) == dbe for p in bundle['pace']):
                    bundle['pace'].append({'id': f'{run_id}-{series}-22', 'series_id': series, 'geography': proto['geography'], 'state_key': proto.get('state_key'),
                                           'year': 2022, 'date': None, 'days_before': dbe, 'value': round(c22['value']), 'mail': None, 'in_person': None,
                                           'definition': proto['definition'], 'source_name': R.name(f['source_id']) + ' (2022 comparison printed in the 2026 report)',
                                           'source_url': f['url'], 'precision': 'exact'})

    # ---- registration: needs definition (active / total) and date
    for f in obs.get('registration', []):
        rt = (f.get('registration_type') or '').lower()
        if rt not in ('active', 'total', 'active+inactive'):
            log['rejected'].append({'measure': f"{f.get('abbr')}.registered", 'reason': 'registration_type must be active, total or active+inactive'}); continue
        if not (f.get('abbr') and isinstance(f.get('registered'), (int, float)) and evidence_has(f.get('evidence'), f['registered'], 'exact')
                and f.get('source_url') and asof_date(f.get('as_of'))):
            log['rejected'].append({'measure': f"{f.get('abbr')}.registered", 'reason': 'registration needs a verbatim number, a date and a source'}); continue
        row = next((r for r in bundle['registration'] if r['abbr'] == f['abbr']), None)
        new = {'abbr': f['abbr'], 'registered': int(f['registered']), 'registration_type': rt, 'as_of': f.get('as_of'),
               'source_name': f.get('source_name'), 'source_url': f['source_url']}
        if row:
            if (row['registered'], row.get('registration_type')) != (new['registered'], rt) and asof_key(new['as_of']) >= asof_key(row.get('as_of')):
                row.update(new); log['accepted'].append({'measure': f['abbr'] + '.registered', 'value': new['registered']})
        else:
            bundle['registration'].append(new); log['accepted'].append({'measure': f['abbr'] + '.registered', 'value': new['registered']})

    # ---- ratings
    races = {r['id']: r for r in bundle['watch_races']}
    rating_changes = []
    for rc in obs.get('ratings', []):
        r = races.get(rc.get('race_id'))
        rating = rc.get('rating')
        if not r or rating not in RATINGS or not rc.get('source_url') or not parse_asof(rc.get('date')):
            log['rejected'].append({'measure': f"rating {rc.get('race_id')}", 'reason': 'invalid race, rating, date or source'}); continue
        if rating != r['rating']:
            prev = r['rating']
            r.update({'previous_rating': prev, 'previous_rating_date': r.get('rating_date'), 'rating': rating, 'rating_date': rc['date'],
                      'favored_party': None if rating == 'Toss Up' else rating[-1]})
            hold = r.get('incumbent_party')
            r['flip_status'] = 'Possible flip' if rating == 'Toss Up' else ('Rated flip' if r['favored_party'] != hold else 'Favors holding party')
            bundle['rating_history'].append({'id': f'{run_id}-rh-{r["id"]}', 'race_id': r['id'], 'date': rc['date'], 'rater': 'Cook', 'from_rating': prev, 'to_rating': rating, 'source_url': rc['source_url']})
            rating_changes.append((r, prev, rating, rc))
            log['accepted'].append({'measure': f"rating {r['id']}", 'value': rating})

    # ---- polls
    pkeys = {(p['race_id'], p['pollster'], p['field_end']) for p in bundle['polls']}
    for p in obs.get('polls', []):
        need = ['race_id', 'pollster', 'field_end', 'd_name', 'd_pct', 'r_name', 'r_pct', 'url']
        if any(p.get(k) in (None, '') for k in need) or p['race_id'] not in races or not parse_asof(p['field_end']):
            log['rejected'].append({'measure': f"poll {p.get('race_id')}", 'reason': 'missing required poll fields or unknown race'}); continue
        k = (p['race_id'], p['pollster'], p['field_end'])
        if k in pkeys:
            continue
        pkeys.add(k)
        row = {x: p.get(x) for x in ['race_id', 'pollster', 'sponsor', 'sponsor_type', 'field_start', 'field_end', 'sample_size', 'population', 'method', 'moe', 'd_name', 'd_pct', 'r_name', 'r_pct', 'other_text', 'url', 'verified_from', 'note']}
        row['id'] = f'{run_id}-p{len(pkeys)}'
        row['sponsor_type'] = row.get('sponsor_type') or 'independent'
        bundle['polls'].append(row)
        log['accepted'].append({'measure': f"poll {p['race_id']}", 'value': f"{p['pollster']} {p['field_end']}"})

    # ---- headlines (dedupe by url)
    urls = {h['url'] for h in bundle['headlines']}
    for h in obs.get('headlines', []):
        if not h.get('url') or h['url'] in urls or not h.get('title') or not parse_asof(h.get('date')):
            continue
        urls.add(h['url'])
        bundle['headlines'].append({'id': f'{run_id}-h{len(urls)}', 'scope': h.get('scope', 'National'), 'title': h['title'], 'outlet': h.get('outlet', ''),
                                    'url': h['url'], 'date': h['date'], 'summary': h.get('summary', ''), 'event_id': h.get('event_id')})
    bundle['headlines'].sort(key=lambda h: str(h.get('date')), reverse=True)
    bundle['headlines'] = bundle['headlines'][:150]

    # ---- results (election night)
    have = {r['id'] for r in bundle['results']}
    for w in bundle['watch_races']:
        if w['id'] not in have:
            bundle['results'].append({'id': w['id'], 'race_id': w['id'], 'race_label': w['race_label'], 'chamber': w['chamber'], 'state_abbr': w.get('state_abbr'),
                                      'arizona': w.get('state_abbr') == 'AZ', 'status': 'Polls not closed'})
    res = {r['id']: r for r in bundle['results']}
    for u in obs.get('results', []):
        r = res.get(u.get('id'))
        if not r or u.get('status') not in RESULT_STATUS or not u.get('source_url'):
            log['rejected'].append({'measure': f"result {u.get('id')}", 'reason': 'unknown race, invalid status, or no source'}); continue
        if u['status'] == 'Projected winner' and not u.get('projection_by'):
            log['rejected'].append({'measure': f"result {u['id']}", 'reason': 'projection without a named organization'}); continue
        if u.get('expected_vote_pct') is not None and not u.get('expected_vote_provider'):
            log['rejected'].append({'measure': f"result {u['id']}", 'reason': 'expected-vote estimate without its provider'}); continue
        if r.get('votes_d') and u.get('votes_d') is not None and u['votes_d'] < r['votes_d'] and not u.get('is_correction'):
            log['rejected'].append({'measure': f"result {u['id']}", 'reason': 'vote total decreased without a stated correction'}); continue
        for k in ['status', 'cand_d', 'votes_d', 'cand_r', 'votes_r', 'other_votes', 'reported_at', 'latest_batch', 'source_name', 'source_url', 'outstanding',
                  'expected_vote_pct', 'expected_vote_provider', 'next_update', 'projection_by', 'certification_status']:
            if k in u:
                r[k] = u[k]
        if isinstance(r.get('votes_d'), (int, float)) and isinstance(r.get('votes_r'), (int, float)):
            tot = r['votes_d'] + r['votes_r'] + (r.get('other_votes') or 0)
            r['leader'] = 'D' if r['votes_d'] > r['votes_r'] else 'R' if r['votes_r'] > r['votes_d'] else None
            r['margin_votes'] = abs(r['votes_d'] - r['votes_r'])
            r['margin_pct'] = round(100 * r['margin_votes'] / tot, 2) if tot else None
        log['accepted'].append({'measure': f"result {u['id']}", 'value': u['status']})
    # ---- explicit coverage gaps: a jurisdiction with no usable figure says why (researched source status), never just blank
    for r in bundle['states']:
        if r['abbr'] in (R.reg.get('row_notes') or {}):
            r['notes'] = R.reg['row_notes'][r['abbr']]
    gaps = R.reg.get('coverage_gaps') or {}
    nrep_state = state.get('not_reporting') or {}
    for r in bundle['states']:
        has_val = r.get('total_early') is not None
        g_ = gaps.get(r['abbr'])
        if has_val:
            r['coverage_gap'] = None
        else:
            why = []
            nr = nrep_state.get(r['abbr'] + '.early_total')
            if nr:
                why.append('TPSI / On Point list it as not reporting (checked ' + str(nr.get('at') or '')[:16].replace('T', ' ') + ' UTC)')
            if g_:
                why.append(g_)
            r['coverage_gap'] = '; '.join(why) or 'No registered source has a figure for this jurisdiction.'
    # ---- Arizona official results export (Enhanced Voting): production results only, never test or zero files
    if direct.get('azev'):
        ax = direct['azev'][0]
        state['azev_last'] = dict({k: v for k, v in ax.items() if k != 'contests'}, at=iso(run_now))
        polls_closed = run_now >= POLLS_CLOSE_AZ
        usable = ax['data_status'] == 'contains votes' and polls_closed and R.reg.get('azev_schema_confirmed')
        if not usable:
            log['notes'].append(f"AZ results export read ({ax['data_status']}); not used for results: "
                                + ('schema not yet confirmed against a live file' if not R.reg.get('azev_schema_confirmed') else
                                   'polls not closed' if not polls_closed else
                                   f"file is not a validated production results file ({ax['data_status']}; identity checks failed: {', '.join(ax.get('identity_failed') or []) or 'none'})"))
        else:
            for race_id, pat in (R.reg.get('azev_contest_map') or {}).items():
                r = res.get(race_id)
                c = next((c for c in ax['contests'] if re.search(pat, str(c['contest']), re.I)), None)
                if not r or not c:
                    continue
                dem = [o for o in c['options'] if re.match(r'(?i)^(dem|democrat)', str(o.get('party') or ''))]
                rep_ = [o for o in c['options'] if re.match(r'(?i)^(rep|republican)', str(o.get('party') or ''))]
                if len(dem) != 1 or len(rep_) != 1:
                    log['notes'].append(f'AZ export: {race_id} contest found but not exactly one DEM and one REP option; not applied'); continue
                vd, vr = dem[0].get('votes'), rep_[0].get('votes')
                if not isinstance(vd, (int, float)) or not isinstance(vr, (int, float)):
                    continue
                if r.get('votes_d') and vd < r['votes_d']:
                    log['rejected'].append({'measure': f'result {race_id}', 'reason': 'AZ export vote total decreased; held for review'}); continue
                other = sum((o.get('votes') or 0) for o in c['options'] if o not in dem + rep_)
                r.update({'status': 'Leading in reported results', 'cand_d': dem[0].get('name'), 'votes_d': vd, 'cand_r': rep_[0].get('name'), 'votes_r': vr,
                          'other_votes': other, 'reported_at': ax.get('last_updated'), 'source_name': 'Arizona Secretary of State results (Enhanced Voting export)',
                          'source_url': R.reg.get('azev_public_url'), 'data_label': 'production export (unofficial until canvass)',
                          'precincts_reporting': c.get('precincts_reporting'), 'precincts_total': c.get('precincts_total'), 'export_sha256': ax['sha256'][:16]})
                tot = vd + vr + other
                r['leader'] = 'D' if vd > vr else 'R' if vr > vd else None
                r['margin_votes'] = abs(vd - vr)
                r['margin_pct'] = round(100 * abs(vd - vr) / tot, 2) if tot else None
                log['accepted'].append({'measure': f'result {race_id}', 'value': 'AZ export'})

    # ---- briefing
    briefed = set(state.get('briefed', []))
    def brief(key, cat, what, why, src_name, src_url, observed, imp):
        if key in briefed:
            return
        briefed.add(key)
        bundle['briefing'].insert(0, {'id': f'{run_id}-b{len(briefed)}', 'at': iso(run_now), 'category': cat, 'what_changed': what, 'why_it_matters': why,
                                      'source_name': src_name, 'source_url': src_url, 'observed_at': observed, 'importance': imp})
    for r, prev, new, rc in rating_changes:
        brief(f'rating|{r["id"]}|{rc["date"]}', 'Rating change', f"Cook moved {r['race_label']} from {prev} to {new}.",
              'Now rated a toss-up: a seat that could change party.' if new == 'Toss Up' else "A shift in the rater's expert judgment; not a probability or a poll result.",
              'Cook Political Report', rc['source_url'], rc['date'], 2 if r['chamber'] != 'House' else 1)
    for acc in log['accepted']:
        mid = acc.get('measure', '')
        if (mid.endswith('.early_total') or mid.endswith('.early_returned')) and acc.get('kind') == 'update':
            m = R.measures.get(mid)
            if not m or not m.get('key'):
                continue
            row = rows[mid][1]
            if not R.sources[acc['source_id']]['class'].startswith('official') and mid != 'US.early_total':
                continue
            day = parse_asof(acc['as_of']).date().isoformat()
            brief(f'turnout|{mid}|{day}', 'Turnout data', f"{m['label']}: {int(acc['value']):,} {m['noun']} as of {acc['as_of']} ({R.name(acc['source_id'])}).",
                  'Shows ballots received so far; it does not indicate final turnout or any candidate\'s standing.', R.name(acc['source_id']), row.get('source_url'), acc['as_of'], 1)
    for c in log['corrections']:
        brief(f'corr|{c["id"]}', 'Correction', f"{c['geography']}: {c['field'].replace('_', ' ')} revised from {fmt_int(c['previous'])} to {fmt_int(c['replacement'])}.", 'The dashboard shows the revised figure; the change is not counted as new ballots.', 'Correction log', c['source_url'], iso(run_now)[:10], 2)
    for s in log['source_changes']:
        brief(f'srcchg|{s["measure"]}|{iso(run_now)[:10]}', 'Source change', f"{s['geography']}: now shown from {s['to_source']} (was {s['from_source']}).", 'Figures from different providers are not compared as new ballots.', s['to_source'], None, s['to_as_of'], 1)
    for rv in reviews.values():
        if rv['status'] == 'open' and rv['reads'] == 1 and rv['first_seen'] == iso(run_now):
            brief(f"review|{rv['id']}", 'Under review', f"{rv['geography']}: {rv['source']} now shows {fmt_int(rv['observed_value'])} (published: {fmt_int(rv['published_value'])}). Held for re-check.",
                  'The published figure stays visible and is marked; both numbers are kept until the next hourly read confirms or clears the change.', rv['source'], rv.get('url'), rv['observed_as_of'], 2)
    today = run_now.astimezone(AZ).date()
    for d_ in bundle.get('az_dates', []):
        try:
            dd = dt.date.fromisoformat(d_['date'])
        except Exception:
            continue
        if d_.get('voter_action') and 0 <= (dd - today).days <= 2:
            brief(f'deadline|{d_["date"]}|{d_["label"]}', 'Deadline', f"{dd.strftime('%a %b %-d')}: {d_['label']}." + (f" {d_['detail']}" if d_.get('detail') else ''),
                  'A voting deadline within the next 48 hours.', 'Arizona / Maricopa official sources', d_.get('source_url'), d_['date'], 3)
    for b in obs.get('briefing', []):
        if all(b.get(k) for k in ('category', 'what_changed', 'source_url')) and b.get('category') in ('Election administration', 'Watched race', 'Results', 'Correction'):
            brief(f'agent|{b["source_url"]}', b['category'], b['what_changed'], b.get('why_it_matters', ''), b.get('source_name', host(b['source_url'])), b['source_url'], b.get('observed_at') or iso(run_now)[:10], int(b.get('importance', 2)))
    state['briefed'] = sorted(briefed)[-2000:]
    bundle['briefing'] = bundle['briefing'][:200]

    # ---- previous run's publication time (written after its batch) and unpublished (conflicted) runs
    prev_latest = load(a.prev_latest, {}) or {}
    if prev_latest.get('published_at') and prev_latest.get('run_id'):
        for r in bundle['run_log']:
            if r.get('run_id') == prev_latest['run_id'] and not r.get('published_at'):
                r['published_at'] = prev_latest['published_at']
                if r.get('publish_target'):
                    r['on_time'] = now_utc(r['published_at']) <= now_utc(r['publish_target'])
    if a.conflicts and os.path.isdir(a.conflicts):
        for fn in sorted(os.listdir(a.conflicts)):
            c = load(os.path.join(a.conflicts, fn), {}) or {}
            rid = c.get('run_id')
            if rid and rid not in state['logged_conflicts']:
                state['logged_conflicts'].append(rid)
                bundle['run_log'].insert(0, {'run_id': rid, 'scheduled_for': c.get('scheduled_for'), 'started_at': c.get('started_at'), 'completed_at': c.get('at'),
                                             'status': 'not published (version conflict)', 'sources_checked': c.get('sources_checked'), 'figures_updated': 0,
                                             'figures_rejected': None, 'updated': '', 'retained': None,
                                             'unresolved': 'Another run published first; this run\'s batch was refused by the version lock and nothing it prepared was written. '
                                                           + (c.get('reason') or '')[:200]})
        state['logged_conflicts'] = state['logged_conflicts'][-200:]

    # ---- run log, missed-run detection, alerts
    prev_runs = [r for r in bundle['run_log'] if r.get('status') not in ('missed',) and r.get('scheduled_for')]
    if prev_runs:
        last_slot = max(now_utc(r['scheduled_for']) for r in prev_runs)
        k = last_slot + dt.timedelta(hours=1)
        while k < slot:
            if not any(r.get('scheduled_for') == iso(k) for r in bundle['run_log']):
                bundle['run_log'].insert(0, {'run_id': 'missed-' + k.strftime('%Y%m%dT%H'), 'scheduled_for': iso(k), 'started_at': None, 'completed_at': None,
                                             'status': 'missed', 'sources_checked': 0, 'sources_failed': '', 'figures_updated': 0, 'figures_rejected': 0,
                                             'updated': '', 'unresolved': 'No run completed for this hour.'})
            k += dt.timedelta(hours=1)
    failed_names = sorted({R.name(s) for s in src_fail if s not in src_perm})
    perm_names = sorted({R.name(s) for s in src_perm})
    approvals_answered = int(obs.get('approvals_answered') or 0) + len([x for x in attempts if x.get('approval') == 'granted'])
    run_mode = 'assisted' if approvals_answered or obs.get('assisted') else 'unattended'
    nodata_names = sorted({R.name(s) for s in src_nodata - src_ok})
    skipped_names = sorted({R.name(s) for s in src_skipped})
    key_stale = [s['measure'] for s in log['stale']]
    open_reviews = [r for r in reviews.values() if r['status'] == 'open']
    unresolved = []
    if failed_names:
        unresolved.append('Could not retrieve: ' + ', '.join(failed_names))
    if perm_names:
        unresolved.append('Blocked by web-reader permission (no approval in an unattended run): ' + ', '.join(perm_names))
    if skipped_names:
        unresolved.append('Skipped (time budget): ' + ', '.join(skipped_names))
    if key_stale:
        unresolved.append('Stale key figures: ' + ', '.join(key_stale))
    if open_reviews:
        unresolved.append('Under review: ' + ', '.join(f"{r['measure']} ({r['kind'].replace('_', ' ')}, {r['reads']} read)" for r in open_reviews))
    if retry_missing:
        unresolved.append('Retry not recorded for: ' + ', '.join(R.name(s) for s in retry_missing))
    # coverage health is separate from publication: a run can publish successfully and still have poor source coverage
    usable = sorted({f['source_id'] for fs in valid_by_measure.values() for f in fs})
    key_ids = [mid for mid in rows if R.measures.get(mid, {}).get('key')]
    key_ok = [mid for mid in key_ids if rows[mid][1].get('check_status') in ('changed', 'corrected', 'no change', 'under review')]
    ratio = len(key_ok) / len(key_ids) if key_ids else 0
    incomplete_tables = [t['source'] for t in table_cov.values() if not t['complete'] and t['unique_rows_returned']]
    coverage_health = 'none (no usable source)' if not usable else 'good' if ratio >= 0.8 and not incomplete_tables else 'partial' if ratio >= 0.4 else 'poor'
    if incomplete_tables:
        unresolved.append('Incomplete tables: ' + '; '.join(f"{t['source']} {t['unique_rows_returned']}/{t['rows_stated_by_source'] or '?'} rows"
                                                            + (f" (duplicates {', '.join(t['duplicates'])})" if t['duplicates'] else '') for t in table_cov.values() if t['source'] in incomplete_tables))
    azev = (direct.get('azev') or [None])[0]
    az_export = None
    if azev:
        az_export = f"{azev['data_status']}; {azev['contest_count']} contests; file updated {azev.get('last_updated') or 'not stated'}; sha256 {azev['sha256'][:12]}"
    else:
        da = next((x for x in direct.get('attempts', []) if x['source_id'] == 'azev'), None)
        if da:
            az_export = f"not read: {da['status']} ({da.get('detail', '')[:80]})"
    status = 'ok' if not failed_names and not perm_names and not key_stale and not skipped_names and coverage_health == 'good' else 'degraded'
    retained_stale = [f"{mid.split('.')[0]} {rows[mid][1].get('age_hours')}h" for mid in log['retained'] if rows.get(mid) and rows[mid][1].get('stale')]
    run_row = {'run_id': run_id, 'scheduled_for': iso(slot), 'started_at': iso(started), 'completed_at': iso(now_utc()),
               'publish_target': iso(target), 'fetch_cutoff': iso(cutoff), 'published_at': None, 'on_time': None,
               'status': status, 'sources_checked': len(reached | set(src_fail)), 'sources_ok': len(reached),
               'sources_no_new_data': ', '.join(nodata_names), 'sources_failed': ', '.join(failed_names), 'sources_skipped': ', '.join(skipped_names),
               'sources_permission_blocked': ', '.join(perm_names), 'permission_requests_refused': len([x for x in attempts if x['status'] == 'permission_blocked' and not str(x.get('method', '')).startswith('none')]),
               'run_mode': run_mode, 'approvals_answered': approvals_answered,
               # attempts the agent recorded for sources that are neither web-search groups nor direct downloads: should be 0 since v12
               'web_reader_calls': len([x for x in obs.get('attempts', []) if x.get('source_id') not in websearch_src and x.get('status') not in ('skipped',)
                                        and 'search' not in str(x.get('method', '')).lower()]),
               'direct_downloads_ok': len([x for x in direct.get('attempts', []) if x['status'] in ('ok', 'no_data')]),
               'direct_downloads_blocked': len([x for x in direct.get('attempts', []) if x['status'] == 'blocked']),
               'figures_updated': len([x for x in log['accepted'] if x.get('kind') in ('update', 'switch', 'correction') or not x.get('kind')]),
               'figures_rejected': len(log['rejected']), 'figures_comparison_only': len(log['comparison_only']), 'reviews_open': len(open_reviews),
               'retained': len(log['retained']), 'retained_stale': ', '.join(retained_stale)[:400],
               'updated': ', '.join(sorted({x['measure'] for x in log['accepted'] if x.get('kind') != 'nochange'}))[:500],
               'rejected_reasons': ' · '.join(sorted({f"{r.get('measure')}: {r['reason']}" for r in log['rejected']}))[:600],
               'stale_responses': ' · '.join(f"{x['measure']}: {R.name(x['source_id'])} returned data as of {x['shown_as_of']} (published figure is as of {x['published_as_of']})" for x in log.get('stale_responses', []))[:600],
               'unresolved': ' · '.join(unresolved)[:900], 'trigger_status_prev': a.trigger_status, 'toolkit_version': a.toolkit_version or TOOLKIT_VERSION,
               'coverage_health': coverage_health, 'key_figures_confirmed': f'{len(key_ok)}/{len(key_ids)}', 'usable_sources': len(usable),
               'tables': ' · '.join(f"{t['source']}: {t['unique_rows_returned']} of {t['rows_stated_by_source'] or '?'} rows" for t in table_cov.values()),
               'az_results_export': az_export,
               'coverage_breakdown': ' · '.join(f"{k} {len(v)}" + (f" ({', '.join(v[:12])}{'…' if len(v) > 12 else ''})" if k not in ('updated', 'verified, unchanged') else '')
                                                 for k, v in sorted(cat_lists.items(), key=lambda kv: -len(kv[1])))[:900]}
    bundle['run_log'].insert(0, run_row)
    bundle['run_log'] = bundle['run_log'][:24 * 60]

    # ---- source status (one row per source: method, last successful read, latest observation, backup, limitation)
    stats = state['source_stats']
    newest_obs = {}
    for fs in valid_by_measure.values():
        for f in fs:
            k = asof_key(f['data_as_of'])
            if k > asof_key(newest_obs.get(f['source_id'])):
                newest_obs[f['source_id']] = f['data_as_of']
    for x in log['comparison_only']:
        pass
    direct_by_src = {}
    for at in direct.get('attempts', []):
        direct_by_src[at['source_id']] = at
    for sid in R.sources:
        s = stats.setdefault(sid, {})
        mine = [at for at in attempts if at['source_id'] == sid and at.get('method') != 'direct']
        if mine:
            s['last_attempt'] = iso(run_now)
            s['last_status'] = 'read OK' if sid in src_ok else 'reachable, no new data' if sid in src_nodata else \
                               'skipped (time budget)' if sid in src_skipped else STATUS_LABEL.get(mine[-1]['status'], 'error')
            if sid in reached:
                s['last_success'] = iso(run_now)
            s['last_detail'] = (mine[-1].get('detail') or '')[:160]
        if sid in newest_obs and asof_key(newest_obs[sid]) > asof_key(s.get('latest_observation')):
            s['latest_observation'] = newest_obs[sid]
        if sid in direct_by_src:
            s['direct_status'] = direct_by_src[sid]['status']
        s['fail_streak'] = state['fail_streak'].get(sid, 0)
        if sid in src_skipped:
            s['skip_streak'] = s.get('skip_streak', 0) + 1
            s['skips_total'] = s.get('skips_total', 0) + 1
        elif mine:
            s['skip_streak'] = 0
        if sid in table_cov:
            s['table'] = table_cov[sid]
    manual_by_src = {}
    for mm in R.reg.get('manual', []):
        for sid in mm.get('sources', []):
            manual_by_src[sid] = mm
    srows = []
    for sid, src in R.sources.items():
        s = stats.get(sid, {})
        prim = [R.measures[k]['label'] for k, mm in R.measures.items() if mm['sources'] and mm['sources'][0] == sid]
        backup_for = [R.measures[k]['label'] for k, mm in R.measures.items() if sid in mm['sources'][1:]]
        backups = sorted({R.name(x) for k, mm in R.measures.items() if mm['sources'] and mm['sources'][0] == sid for x in mm['sources'][1:]})
        groups = [g for g in R.reg['fetch_groups'] if g['source_id'] == sid]
        has_direct = any(g.get('direct') for g in groups)
        dstat = s.get('direct_status')
        if dstat in ('ok', 'no_data'):
            method = 'direct download + code extraction (no AI)'
        elif any(g.get('how') == 'direct_only' for g in groups):
            method = 'direct download + code parser only' + (' — blocked by workspace allowlist' if dstat == 'blocked' else '')
        elif any(g.get('how') == 'websearch' for g in groups):
            method = 'web search snippets + AI extraction, code validation'
        else:
            method = 'fetch tool + AI extraction, code validation' + (' (direct download coded; blocked by workspace allowlist)' if has_direct and dstat == 'blocked' else '')
        srows.append({'id': sid, 'source': src['name'], 'class': src['class'],
                      'primary_for': (', '.join(prim[:6]) + (f' +{len(prim) - 6}' if len(prim) > 6 else '')) or '—',
                      'backup_for': (', '.join(backup_for[:4]) + (f' +{len(backup_for) - 4}' if len(backup_for) > 4 else '')) or '—',
                      'backups': ', '.join(backups) or '—', 'method': method,
                      'last_success': s.get('last_success'), 'last_attempt': s.get('last_attempt'), 'last_status': s.get('last_status'),
                      'latest_observation': s.get('latest_observation'), 'fail_streak': s.get('fail_streak', 0),
                      'limitation': (manual_by_src.get(sid) or {}).get('why') or src.get('limitation') or '',
                      'next_fix': (manual_by_src.get(sid) or {}).get('next_fix') or src.get('next_fix') or '',
                      'detail': s.get('last_detail', ''),
                      'table_coverage': (f"{s['table']['unique_rows_returned']} rows returned of {s['table']['rows_stated_by_source'] or '?'} the source reports"
                                         + (f"; duplicates: {', '.join(s['table']['duplicates'])}" if s['table']['duplicates'] else '')) if s.get('table') else None,
                      'skips_total': s.get('skips_total', 0), 'definition': src.get('definition'), 'date_basis_note': src.get('date_note')})
    bundle['source_status'] = srows

    # alerts: new problems, or every 12 h while they persist
    alerts = state.setdefault('alerts', {})
    current = {}
    for r in bundle['run_log'][:24]:
        if r['status'] == 'missed':
            current[f"missed|{r['scheduled_for']}"] = f"Missed hourly run at {r['scheduled_for']}"
        if r['status'].startswith('not published'):
            current[f"conflict|{r['run_id']}"] = f"Run {r['run_id']} was not published (version conflict)"
    late = [r for r in bundle['run_log'][1:4] if r.get('on_time') is False]
    if len(late) >= 3:
        current['late'] = 'The last 3 runs published after the hourly publication target'
    for s, n in state['fail_streak'].items():
        if n >= 3:
            meas_without_backup = [mid for mid, m in R.measures.items() if s in m['sources'] and m.get('key') and rows.get(mid, (None, {}))[1].get('check_status') == 'unreachable']
            if meas_without_backup:
                current[f'source|{s}'] = f"{R.name(s)} has failed {n} runs in a row and no backup covered {', '.join(meas_without_backup)}"
    for s in log['stale']:
        current[f"stale|{s['measure']}"] = f"{s['measure']} data is {s['age_hours']} h old (as of {s['as_of']})"
    for rv in open_reviews:
        if rv['kind'] == 'over_registration' and rv['reads'] >= 3:
            current[f"review|{rv['id']}"] = f"{rv['geography']}: figure above registered voters has persisted {rv['reads']} reads; check the registration denominator"
    if coverage_health.startswith(('none', 'poor')):
        current['coverage|poor'] = f'Run {run_id} published but source coverage is {coverage_health}: {len(key_ok)}/{len(key_ids)} key figures confirmed'
    for sid, st_ in state['source_stats'].items():
        if st_.get('skip_streak', 0) >= 2:
            current[f'skip|{sid}'] = f"{R.name(sid)} skipped by the time limit {st_['skip_streak']} runs in a row"
    if a.trigger_status and 'FAIL' in a.trigger_status.upper():
        current['trigger|prev'] = f'Previous scheduled run reported {a.trigger_status}'
    notify = []
    for k, msg in current.items():
        al = alerts.get(k)
        if not al:
            alerts[k] = {'first': iso(run_now), 'last_notified': iso(run_now), 'msg': msg}
            notify.append(msg)
        elif (run_now - now_utc(al['last_notified'])).total_seconds() > 12 * 3600:
            al['last_notified'] = iso(run_now)
            notify.append(msg + ' (still unresolved)')
    for k in list(alerts):
        if k not in current:
            del alerts[k]

    # ---- checks dataset (newest first, cap)
    bundle['checks'] = check_rows[::-1] + bundle['checks']
    bundle['checks'] = bundle['checks'][:500]

    # ---- write outputs
    changed = [d for d in PIPE if d in bundle and sha(bundle[d]) != sha(prev_bundle.get(d))]
    for d in changed:
        dump(f'{out}/datasets/{d}.json', bundle[d])
    bundle['_state']['last_run'] = run_id
    dump(f'{out}/bundle.json', bundle)
    # original files: saved (gzip+base64 JSON wrapper) whenever the figures extracted from them change, so every published value
    # can be traced to the exact bytes it came from; unchanged pages are not re-saved every hour
    raw_uploads = []
    last_sig = state.setdefault('raw_last_sig', {})
    raw_dir = os.path.dirname(a.direct) if a.direct else None
    figs_by_sha = {}
    for f in direct.get('figures', []):
        figs_by_sha.setdefault(f.get('raw_sha256'), []).append((f.get('measure'), f.get('value'), f.get('data_as_of')))
    for at in direct.get('attempts', []):
        if not (at.get('raw_file') and at.get('sha256')):
            continue
        sig = sha(sorted(figs_by_sha.get(at['sha256'], [])) or at['sha256'])
        if at['source_id'] == 'azev':
            sig = at['sha256']
        if last_sig.get(at['source_id']) == sig:
            continue
        src_path = at['raw_file'] if os.path.isabs(at['raw_file']) else os.path.join(raw_dir or '.', at['raw_file'])
        if os.path.exists(src_path) and os.path.getsize(src_path) < 15_000_000:
            body = open(src_path, 'rb').read()
            dst = f"{out}/raw/{os.path.basename(src_path)}.json"
            dump(dst, {'source_id': at['source_id'], 'url': at.get('url'), 'downloaded_at': at.get('started_at'), 'sha256': at['sha256'],
                       'bytes': len(body), 'figures': figs_by_sha.get(at['sha256'], []), 'format': 'gzip+base64 of the original bytes',
                       'gz_b64': base64.b64encode(gzip.compress(body)).decode()})
            raw_uploads.append(dst)
            last_sig[at['source_id']] = sig
    archive = {'run_id': run_id, 'created_at': iso(now_utc()), 'format': 'gzip+base64 of the full pipeline bundle, this run\'s observations (with verbatim evidence) and its log',
               'changed_datasets': changed, 'summary': run_row,
               'gz_b64': base64.b64encode(gzip.compress(json.dumps({'bundle': bundle, 'observations': obs, 'direct': direct, 'log': log}, ensure_ascii=False).encode())).decode()}
    dump(f'{out}/archive-{run_id}.json', archive)
    publish = {'run_id': run_id, 'upload': [f'{out}/datasets/{d}.json' for d in changed] + [f'{out}/archive-{run_id}.json'] + raw_uploads, 'raw_uploads': raw_uploads,
               'changed_datasets': changed, 'notify': notify, 'summary': run_row, 'log_counts': {k: len(v) for k, v in log.items()},
               'myballot': myballot_payload(bundle, prev_bundle, run_now)}
    dump(f'{out}/publish.json', publish)
    dump(f'{out}/log.json', log)
    print(json.dumps({'run_id': run_id, 'status': status, 'changed_datasets': changed, 'upload_count': len(publish['upload']), 'notify': notify,
                      'accepted': len(log['accepted']), 'comparison_only': len(log['comparison_only']), 'reviews': log['reviews'],
                      'rejected': log['rejected'][:25], 'publish_target': iso(target)}, indent=1))


def upsert_compare(bundle, mid, f, R, primary):
    m = R.measures[mid]
    src = R.sources[f['source_id']]
    prec = f.get('_prec') or f['precision']
    row = next((c for c in bundle['source_compare'] if c.get('measure') == mid and c.get('source_id') == f['source_id']), None)
    new = {'measure': mid, 'metric': m['compare_label'], 'geography': m['geo_key'], 'source': src['name'], 'source_id': f['source_id'],
           'value': round(float(f['value'])) if prec != 'rounded' else float(f['value']), 'value_text': None, 'observed_at': f['data_as_of'],
           'classification': src['class'], 'provider': src.get('provider', ''), 'definition': src.get('definition', ''), 'precision': prec,
           'url': f['url'], 'primary': primary, 'note': src.get('note', '')}
    if row:
        if asof_key(f['data_as_of']) >= asof_key(row.get('observed_at')):
            row.update(new)
        else:
            row['primary'] = primary
    else:
        new['id'] = f"cmp-{mid}-{f['source_id']}"
        bundle['source_compare'].append(new)
    if primary:
        for c in bundle['source_compare']:
            if c.get('measure') == mid and c.get('source_id') != f['source_id']:
                c['primary'] = False


def myballot_payload(bundle, prev, run_now):
    out = {}
    if sha(bundle['briefing'][:60]) != sha(prev['briefing'][:60]):
        out['desk/briefing'] = {'items': bundle['briefing'][:60], 'updated_at': iso(run_now)}
    if sha(bundle['watch_races']) != sha(prev['watch_races']):
        out['desk/races'] = {'items': [{'id': w['id'], 'label': w['race_label'], 'chamber': w['chamber'], 'rating': w['rating'], 'rating_source': 'Cook',
                                        'rating_date': w.get('rating_date'), 'previous_rating': w.get('previous_rating'), 'candidates': w['candidates'],
                                        'flip_status': w['flip_status']} for w in bundle['watch_races']], 'updated_at': iso(run_now)}
    return out


# ----------------------------------------------------------------- batch
NEW_DOCS = {
    'run_log': {'title': 'Hourly run log', 'description': 'One row per scheduled hour: scheduled, start, completion and publication times, sources checked and failed, figures updated, rejected or kept, unresolved problems, and missed runs.'},
    'source_changes': {'title': 'Source changes', 'description': 'When a figure switched to a different provider, with both values. A change of source is never counted as new ballots.'},
    'source_registry': {'title': 'Source registry', 'description': 'Primary and backup sources for each measure, and items that have no automatic source.'},
    'source_status': {'title': 'Source status', 'description': 'Each source: how it is retrieved, last successful read, latest observation date, backups and unresolved limitation.'},
    'reviews': {'title': 'Figures under review', 'description': 'Unexplained decreases, same-period changes, large jumps and figures above registration: both observations are kept and re-checked every hour.'},
}


def cmd_batch(a):
    """--versions: JSON {"datasets/<id>": n, "pipeline/latest": n} copied from the ArtifactData list output."""
    pub = load(f'{a.out}/publish.json')
    ids = load(a.ids)
    vers = load(a.versions, {})
    def idfor(path):
        for k in (path, os.path.basename(path)):
            if k in ids:
                return ids[k]
        raise SystemExit(f'missing uploaded id for {path}')
    writes, delete, retired = [], [], []
    now = iso(now_utc())
    for d in pub['changed_datasets']:
        doc = load(f'{a.docs}/{d}.json')
        newid = idfor(f'{a.out}/datasets/{d}.json')
        src = {'source': {'kind': 'file', 'url': '/_blob/' + newid, 'name': d + '.json'}, 'updated': {'at': now, 'by': 'Hourly refresh ' + pub['run_id']}}
        if doc is None:
            if f'datasets/{d}' in vers:
                raise SystemExit(f'dataset document file for {d} is missing; list collection datasets again')
            meta = NEW_DOCS.get(d, {'title': d.replace('_', ' ').title(), 'description': ''})
            writes.append({'op': 'set', 'collection': 'datasets', 'doc_id': d, 'data': dict(meta, schedule='Refreshed hourly by a scheduled task', staleAfterHours=3, **src)})
            continue
        if f'datasets/{d}' not in vers:
            raise SystemExit(f'need the version of datasets/{d} (from the ArtifactData list output)')
        old = doc['source']['url'].split('/')[-1]
        writes.append({'op': 'update', 'collection': 'datasets', 'doc_id': d, 'if_version': int(vers[f'datasets/{d}']), 'data': src})
        if old != newid:
            retired.append(old)
    # replaced files are kept for one more hour: a page opened before this publication still points at them. Only the files
    # replaced by the PREVIOUS run are deleted now, and never one that a dataset document still references.
    prev = load(a.prev_latest, {}) if getattr(a, 'prev_latest', None) else {}
    prev = (prev or {}).get('data', prev) or {}
    referenced = set()
    for f_ in glob.glob(f'{a.docs}/*.json'):
        try:
            d_ = load(f_) or {}
            referenced.add((d_.get('data') or d_)['source']['url'].split('/')[-1])
        except Exception:
            pass
    retired = sorted(set(retired))
    delete = sorted({x for x in (prev.get('retired_blobs') or []) if x not in referenced and x not in set(ids.values())})
    arch = idfor(f"{a.out}/archive-{pub['run_id']}.json")
    s = pub['summary']
    w = {'op': 'set', 'collection': 'pipeline', 'doc_id': 'latest', 'data': {'run_id': pub['run_id'], 'archive': '/_blob/' + arch, 'at': now,
         'status': s['status'], 'changed': pub['changed_datasets'], 'scheduled_for': s['scheduled_for'], 'started_at': s['started_at'],
         'completed_at': s['completed_at'], 'publish_target': s['publish_target'], 'published_at': None, 'notify': pub['notify'],
         'unresolved': s['unresolved'], 'sources_checked': s['sources_checked'], 'sources_failed': s['sources_failed'],
         'figures_updated': s['figures_updated'], 'reviews_open': s['reviews_open'], 'toolkit_version': s.get('toolkit_version'),
         'coverage_health': s.get('coverage_health'), 'key_figures_confirmed': s.get('key_figures_confirmed'), 'usable_sources': s.get('usable_sources'),
         'run_mode': s.get('run_mode'), 'sources_permission_blocked': s.get('sources_permission_blocked'), 'permission_requests_refused': s.get('permission_requests_refused'),
         'web_reader_calls': s.get('web_reader_calls'), 'direct_downloads_ok': s.get('direct_downloads_ok'), 'retired_blobs': retired}}
    if 'pipeline/latest' in vers:
        w['if_version'] = int(vers['pipeline/latest'])
    writes.append(w)
    raw_map = {os.path.basename(p): '/_blob/' + idfor(p) for p in pub.get('raw_uploads', [])}
    writes.append({'op': 'set', 'collection': 'archives', 'doc_id': pub['run_id'], 'data': {'archive': '/_blob/' + arch, 'at': now, 'summary': s, 'changed': pub['changed_datasets'],
                                                                                            'original_files': raw_map}})
    final = []
    for i, w in enumerate(writes):
        fp = f'{a.out}/write_{i:02d}_{w["collection"]}_{w["doc_id"]}.json'
        dump(fp, w.pop('data'))
        w['file_path'] = os.path.abspath(fp)
        final.append(w)
    for key, doc in pub.get('myballot', {}).items():
        dump(f'{a.out}/myballot_{key.replace("/", "_")}.json', doc)
    dump(f'{a.out}/batch.json', final)
    dump(f'{a.out}/delete.json', delete)
    dump(f'{a.out}/uploaded_ids.json', sorted(set(ids.values())))
    print(json.dumps({'writes': len(final), 'delete': delete, 'batch_file': f'{a.out}/batch.json',
                      'latest_new_version': (int(vers['pipeline/latest']) + 1) if 'pipeline/latest' in vers else 1}, indent=1))


def cmd_unpack(a):
    arch = load(a.archive)
    data = json.loads(gzip.decompress(base64.b64decode(arch['gz_b64'])).decode())
    dump(a.out, data['bundle'])
    print(json.dumps({'run_id': arch['run_id'], 'bundle': a.out, 'datasets': len([k for k in data['bundle'] if not k.startswith('_')])}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd')
    ap.add_argument('--bundle'); ap.add_argument('--registry'); ap.add_argument('--obs'); ap.add_argument('--now')
    ap.add_argument('--out'); ap.add_argument('--ids'); ap.add_argument('--docs'); ap.add_argument('--trigger-status', default=None)
    ap.add_argument('--versions'); ap.add_argument('--archive'); ap.add_argument('--started'); ap.add_argument('--direct')
    ap.add_argument('--prev-latest'); ap.add_argument('--conflicts'); ap.add_argument('--toolkit-version')
    a = ap.parse_args()
    {'plan': cmd_plan, 'prepare': cmd_prepare, 'batch': cmd_batch, 'unpack': cmd_unpack, 'clock': cmd_clock, 'direct': cmd_direct}[a.cmd](a)


if __name__ == '__main__':
    main()
