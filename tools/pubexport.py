#!/usr/bin/env python3
"""Public-site exporter for the Election Command Center.

Runs inside the existing hourly job AFTER a verified publication. It reads the bundle that was just
published (WORK/out/bundle.json) and the verified pipeline/latest record, and writes the public data
files for the static website: one JSON file per public dataset plus manifest.json.

Safety rules:
- Allow-list only: datasets not named in PUBLIC_DYNAMIC are never written. Keys starting with "_" are dropped.
- Every string is scanned for personal or administrative markers (private artifact links, account names,
  e-mail addresses, credentials, personal "your ballot" wording). Any hit aborts the export with a non-zero
  exit code, so nothing is pushed.
- The manifest carries the verified publication times so the site never implies freshness the pipeline did
  not verify.

Usage: python3 pubexport.py --bundle WORK/out/bundle.json --latest WORK/latest_readback.json --out WORK/public_data
"""
import argparse, hashlib, json, os, re, sys, datetime as dt

EXPORTER_VERSION = '2026-10-27.a1'

# datasets the hourly pipeline produces (taken from the published bundle every hour)
PUBLIC_DYNAMIC = ['az_counties', 'az_dates', 'az_returns', 'az_trackers', 'bg_states', 'briefing', 'checks', 'corrections', 'headlines',
                  'national', 'pace', 'polls', 'rating_history', 'registration', 'results', 'reviews', 'run_log', 'source_changes',
                  'source_compare', 'source_registry', 'source_status', 'states', 'watch_races']
# curated reference datasets committed once with the site (sanitized by build_static.py; listed so the manifest is complete)
PUBLIC_STATIC = ['az_howto', 'az_leg', 'az_races', 'az_split', 'certification', 'control', 'counting', 'glossary', 'method_notes',
                 'pace_notes', 'poll_avgs', 'poll_close', 'provenance', 'seat_baseline', 'source_policy',
                 'local_info', 'local_links', 'local_contests', 'local_measures', 'local_judicial']

# fields removed from public rows (internal bookkeeping, not needed to read the dashboard)
DROP_FIELDS = {'raw_file', 'raw_error', 'trigger_status', 'session_id', 'archive', 'uploaded_ids'}

BANNED = [
    (r'claude\.ai/(?:artifact|code|chat|project)', 'link to a private Claude page'),
    (r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', 'e-mail address'),
    (r'(?i)\b(?:api[_-]?key|client[_-]?secret|secret[_-]?key|access[_-]?token|password\s*[:=])|\bbearer\s+[A-Za-z0-9._-]{20,}|\bghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9-]{10,}', 'credential-like text'),
    (r'(?i)\bmy ballot\b|\bon your ballot\b|\byour ballot:|\byou told us\b|\byou live in\b|\byou are in\b|\byou confirmed\b|\byour stated districts\b|\byour address\b|\bprivate checklist\b', 'personal ballot wording'),
    (r'/_blob/[0-9a-f]{32}', 'private storage reference'),
]


# Private words (the owner's name and account, private page ids, a home ZIP code) are checked by hash, so this public file
# does not itself contain them. Each word of every string is lower-cased and hashed; a match blocks the export.
PRIVATE_TOKEN_HASHES = {'b8d836e2b9a78a2febb7', '638e872a85ca1d6b0473', 'e3211df0ac8580b37482', 'fac5cc07ba7d14387ae5', 'c3584620a1582492b997', 'eeac7816005ff0bde670', 'b69d7a7b31ed4a62f913'}

def private_tokens(s):
    import hashlib
    out = []
    for w in re.findall(r'[A-Za-z0-9]+', s):
        lw = w.lower()
        cands = {lw, re.sub(r'\d+$', '', lw)}
        if any(hashlib.sha256(c.encode()).hexdigest()[:20] in PRIVATE_TOKEN_HASHES for c in cands if c):
            out.append(w)
    return out


def scan(obj, where, hits):
    if isinstance(obj, dict):
        for k, v in obj.items():
            scan(v, f'{where}.{k}', hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            scan(v, f'{where}[{i}]', hits)
    elif isinstance(obj, str):
        for pat, why in BANNED:
            m = re.search(pat, obj)
            if m:
                hits.append(f'{where}: {why} ("{m.group(0)[:40]}")')
        if private_tokens(obj):
            hits.append(f'{where}: private name, account, page id or home ZIP code')


def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if not str(k).startswith('_') and k not in DROP_FIELDS}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]


# the only identity allowed on public commits: not a person, and the .invalid domain can never belong to anyone
BOT_NAME = 'Election Command Center publisher'
BOT_EMAIL = 'publisher@election-command-center.invalid'


def check_commit(repo_dir, run_id=None, rev='HEAD'):
    """Privacy check of commit metadata before a push: fixed bot identity, a one-line message, no trailers, links or e-mail addresses."""
    import subprocess
    fmt = '%an%x00%ae%x00%cn%x00%ce%x00%B'
    out = subprocess.run(['git', '-C', repo_dir, 'log', '-1', '--format=' + fmt, rev], capture_output=True, text=True, check=True).stdout
    an, ae, cn, ce, body = out.split('\x00', 4)
    body = body.strip()
    problems = []
    if (an, ae) != (BOT_NAME, BOT_EMAIL):
        problems.append(f'author is not the publisher identity ({an} <{ae}>)')
    if (cn, ce) != (BOT_NAME, BOT_EMAIL):
        problems.append(f'committer is not the publisher identity ({cn} <{ce}>)')
    expected = f'Verified publication {run_id}' if run_id else None
    if '\n' in body or (expected and body != expected) or not re.match(r'^[A-Za-z0-9 .:,()_-]{1,120}$', body):
        problems.append(f'message must be exactly "{expected or "one plain line"}" with no trailers (got {body[:80]!r})')
    hits = []
    scan({'author': an, 'committer': cn, 'message': body}, 'commit', hits)
    if re.search(r'https?://|Co-Authored-By|Claude-Session|Signed-off-by', body, re.I):
        hits.append('commit message contains a link or trailer')
    problems += hits
    return problems


def export(bundle, latest, out, static_dir=None):
    os.makedirs(out, exist_ok=True)
    data, hits = {}, []
    for ds in PUBLIC_DYNAMIC:
        if ds not in bundle:
            raise SystemExit(f'bundle has no dataset "{ds}"; refusing a partial public export')
        rows = clean(bundle[ds])
        scan(rows, ds, hits)
        data[ds] = rows
    if static_dir:
        for ds in PUBLIC_STATIC:
            p = os.path.join(static_dir, ds + '.json')
            if os.path.exists(p):
                rows = json.load(open(p))
                scan(rows, ds, hits)
                data[ds] = rows
    if hits:
        raise SystemExit('PUBLIC EXPORT BLOCKED (nothing written): ' + '; '.join(hits[:10]))
    for ds, rows in data.items():
        with open(os.path.join(out, ds + '.json'), 'w') as f:
            json.dump(rows, f, separators=(',', ':'), ensure_ascii=False)
    lt = latest.get('data', latest)
    if not lt.get('publication_verified'):
        raise SystemExit('pipeline/latest is not marked publication_verified; refusing to export')
    manifest = {
        'run_id': lt.get('run_id'), 'scheduled_for': lt.get('scheduled_for'), 'started_at': lt.get('started_at'),
        'completed_at': lt.get('completed_at'), 'published_at': lt.get('published_at'), 'publish_target': lt.get('publish_target'),
        'publication_verified': True, 'status': lt.get('status'), 'coverage_health': lt.get('coverage_health'),
        'key_figures_confirmed': lt.get('key_figures_confirmed'), 'figures_updated': lt.get('figures_updated'),
        'sources_checked': lt.get('sources_checked'), 'sources_failed': lt.get('sources_failed'), 'reviews_open': lt.get('reviews_open'),
        'toolkit_version': lt.get('toolkit_version'), 'exporter_version': EXPORTER_VERSION,
        'exported_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'datasets': {ds: digest(rows) for ds, rows in sorted(data.items())},
        'dynamic': PUBLIC_DYNAMIC,
    }
    scan(manifest, 'manifest', hits)
    if hits:
        raise SystemExit('PUBLIC EXPORT BLOCKED (manifest): ' + '; '.join(hits[:10]))
    with open(os.path.join(out, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)
    return manifest


if __name__ == '__main__':
    if sys.argv[1:2] == ['--check-commit']:
        cp = argparse.ArgumentParser()
        cp.add_argument('--check-commit', required=True, metavar='REPO_DIR')
        cp.add_argument('--run-id')
        ca = cp.parse_args()
        probs = check_commit(ca.check_commit, ca.run_id)
        if probs:
            raise SystemExit('COMMIT METADATA BLOCKED (do not push): ' + '; '.join(probs))
        print(json.dumps({'ok': True, 'commit_metadata': 'publisher identity, one-line message, no trailers or links'}))
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--latest', required=True, help='the verified pipeline/latest document (ArtifactData get output)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--static', help='optional: also copy curated static datasets from this folder')
    a = ap.parse_args()
    m = export(json.load(open(a.bundle)), json.load(open(a.latest)), a.out, a.static)
    print(json.dumps({'ok': True, 'run_id': m['run_id'], 'published_at': m['published_at'], 'files': len(m['datasets']) + 1,
                      'coverage_health': m['coverage_health']}, indent=1))
