#!/usr/bin/env python3
"""Hourly run on GitHub Actions for the Election Command Center public site.

No AI step and no approvals: code-only direct downloads (vp.py direct), the same validation as before (vp.py prepare),
the same privacy-checked public export (pubexport.py), and a commit with the fixed publisher identity.

Modes (environment variable PUBLISH):
  "dry-run" (default): everything runs, but the public site is not touched. Results go to the branch "actions-dryrun"
                       and the chained state to "pipeline-state-dryrun".
  "live":              data/ is committed to main (the public site) and the state to "pipeline-state".

Exit code is non-zero whenever the run did not publish, so the Actions run shows as failed.
"""
import datetime as dt
import gzip
import json
import os
import shutil
import subprocess
import sys

ROOT = os.getcwd()
TOOLS = os.path.join(ROOT, 'tools')
WORK = os.path.join(ROOT, '.work')
MODE = os.environ.get('PUBLISH', 'dry-run')
LIVE = MODE == 'live'
STATE_BRANCH = 'pipeline-state' if LIVE else 'pipeline-state-dryrun'
OUT_BRANCH = 'main' if LIVE else 'actions-dryrun'
BOT = {'GIT_AUTHOR_NAME': 'Election Command Center publisher', 'GIT_AUTHOR_EMAIL': 'publisher@election-command-center.invalid',
       'GIT_COMMITTER_NAME': 'Election Command Center publisher', 'GIT_COMMITTER_EMAIL': 'publisher@election-command-center.invalid'}

sys.path.insert(0, TOOLS)
import pubexport  # noqa: E402


def now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def sh(*cmd, cwd=None, check=True, env=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run(list(cmd), cwd=cwd or ROOT, capture_output=True, text=True, env=e)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {(r.stderr or r.stdout)[-600:]}")
    return r


def remote_has(branch):
    return bool(sh('git', 'ls-remote', '--heads', 'origin', branch).stdout.strip())


def worktree(path, branch, seed_from=None):
    """Check out origin/<branch> (detached) into path; if the branch does not exist yet, start it from origin/<seed_from>.
    Detached so it also works for main, which the workflow's own checkout already holds."""
    if os.path.exists(path):
        sh('git', 'worktree', 'remove', '--force', path, check=False)
        shutil.rmtree(path, ignore_errors=True)
    src = branch if remote_has(branch) else seed_from
    if not src or not remote_has(src):
        raise RuntimeError(f'branch {branch} not found (and nothing to seed it from)')
    sh('git', 'fetch', '--depth', '1', 'origin', f'+refs/heads/{src}:refs/remotes/origin/{src}')
    sh('git', 'worktree', 'add', '--detach', path, f'origin/{src}')


def commit_and_push(path, branch, paths, message, run_id=None):
    sh('git', 'add', '--', *paths, cwd=path)
    if not sh('git', 'status', '--porcelain', cwd=path).stdout.strip():
        return 'nothing to commit'
    sh('git', '-c', f"user.name={BOT['GIT_AUTHOR_NAME']}", '-c', f"user.email={BOT['GIT_AUTHOR_EMAIL']}", 'commit', '-q', '-m', message, cwd=path, env=BOT)
    problems = pubexport.check_commit(path, run_id)
    if problems:
        sh('git', 'reset', '--soft', 'HEAD~1', cwd=path)
        raise RuntimeError('COMMIT METADATA BLOCKED (not pushed): ' + '; '.join(problems))
    for attempt in (1, 2):
        r = sh('git', 'push', 'origin', f'HEAD:refs/heads/{branch}', cwd=path, check=False)
        if r.returncode == 0:
            return sh('git', 'rev-parse', 'HEAD', cwd=path).stdout.strip()
        if attempt == 1:
            sh('git', 'fetch', '--deepen', '50', 'origin', f'+refs/heads/{branch}:refs/remotes/origin/{branch}', cwd=path)
            sh('git', 'rebase', f'origin/{branch}', cwd=path, env=BOT)
            problems = pubexport.check_commit(path, run_id)
            if problems:
                raise RuntimeError('COMMIT METADATA BLOCKED after rebase: ' + '; '.join(problems))
    raise RuntimeError(f'push to {branch} failed: {r.stderr[-400:]}')


def main():
    started = now()
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(f'{WORK}/conflicts')
    report = {'started_at': started, 'mode': MODE, 'publisher': 'github-actions', 'run_url': os.environ.get('RUN_URL')}
    try:
        # 1. previous state (the last published bundle and its publication record)
        state_dir = os.path.join(ROOT, '.state')
        worktree(state_dir, STATE_BRANCH, seed_from=None if LIVE else 'pipeline-state')
        with gzip.open(f'{state_dir}/bundle.json.gz', 'rt') as f:
            json.dump(json.load(f), open(f'{WORK}/bundle_prev.json', 'w'))
        prev_latest = json.load(open(f'{state_dir}/latest.json'))
        report['previous_run'] = prev_latest.get('run_id')

        # 2. direct downloads (code extraction, raw-file hashes kept)
        d = sh(sys.executable, f'{TOOLS}/vp.py', 'direct', '--registry', f'{TOOLS}/registry.json', '--out', WORK)
        report['direct'] = json.loads(d.stdout)

        # 3. validation (no agent observations: web-search groups are not read here)
        json.dump({'started_at': started, 'attempts': [], 'figures': [], 'approvals_answered': 0}, open(f'{WORK}/obs.json', 'w'))
        tkv = json.load(open(f'{TOOLS}/registry.json'))['version']
        p = sh(sys.executable, f'{TOOLS}/vp.py', 'prepare', '--bundle', f'{WORK}/bundle_prev.json', '--registry', f'{TOOLS}/registry.json',
               '--obs', f'{WORK}/obs.json', '--direct', f'{WORK}/direct_obs.json', '--prev-latest', f'{state_dir}/latest.json',
               '--conflicts', f'{WORK}/conflicts', '--out', f'{WORK}/out', '--trigger-status', 'github-actions', '--toolkit-version', tkv)
        report['prepare'] = json.loads(p.stdout)
        pub = json.load(open(f'{WORK}/out/publish.json'))
        s = pub['summary']
        run_id = pub['run_id']
        report['run_id'] = run_id

        # 4. public export into the output branch, privacy-scanned, then commit with the publisher identity
        site = os.path.join(ROOT, '.site')
        worktree(site, OUT_BRANCH, seed_from='main')
        latest = {k: s.get(k) for k in ('run_id', 'scheduled_for', 'started_at', 'completed_at', 'publish_target', 'status', 'coverage_health',
                                        'key_figures_confirmed', 'figures_updated', 'sources_checked', 'sources_failed', 'reviews_open',
                                        'toolkit_version', 'run_mode', 'sources_permission_blocked', 'permission_requests_refused',
                                        'web_reader_calls', 'direct_downloads_ok', 'unresolved')}
        latest.update(run_id=run_id, notify=pub.get('notify', []), publisher='github-actions', published_at=now(), publication_verified=True)
        manifest = pubexport.export(json.load(open(f'{WORK}/out/bundle.json')), latest, f'{site}/data', f'{site}/data')
        paths = ['data']
        if not LIVE:
            json.dump(dict(report, latest=latest), open(f'{site}/dryrun_report.json', 'w'), indent=1)
            paths.append('dryrun_report.json')
        commit = commit_and_push(site, OUT_BRANCH, paths, f'Verified publication {run_id}', run_id)
        report['public_commit'] = commit
        # verify: the branch now carries this run's manifest
        sh('git', 'fetch', '--depth', '1', 'origin', f'+refs/heads/{OUT_BRANCH}:refs/remotes/origin/{OUT_BRANCH}', cwd=site)
        remote_manifest = json.loads(sh('git', 'show', f'origin/{OUT_BRANCH}:data/manifest.json', cwd=site).stdout)
        if remote_manifest.get('run_id') != run_id:
            raise RuntimeError(f"verification failed: {OUT_BRANCH} shows {remote_manifest.get('run_id')}, expected {run_id}")
        report['verified_on_branch'] = OUT_BRANCH

        # 5. chained state for the next run (bundle, publication record, this run's evidence archive and changed raw files)
        archive = json.load(open(f'{WORK}/out/archive-{run_id}.json'))
        hits = []
        pubexport.scan({'bundle': json.load(open(f'{WORK}/out/bundle.json')), 'summary': s}, 'state', hits)
        if hits:
            raise RuntimeError('STATE NOT SAVED (privacy scan): ' + '; '.join(hits[:5]))
        with gzip.open(f'{state_dir}/bundle.json.gz', 'wt') as f:
            json.dump(json.load(open(f'{WORK}/out/bundle.json')), f)
        json.dump(latest, open(f'{state_dir}/latest.json', 'w'), indent=1)
        os.makedirs(f'{state_dir}/archives', exist_ok=True)
        json.dump(archive, open(f'{state_dir}/archives/{run_id}.json', 'w'))
        os.makedirs(f'{state_dir}/raw', exist_ok=True)
        for rp in pub.get('raw_uploads', []):
            shutil.copy(rp, f'{state_dir}/raw/{os.path.basename(rp)}')
        report['state_commit'] = commit_and_push(state_dir, STATE_BRANCH, ['bundle.json.gz', 'latest.json', 'archives', 'raw'], f'State after {run_id}')

        # 6. ask GitHub Pages to rebuild (live only; a push made by Actions may not trigger it on its own)
        if LIVE and os.environ.get('GH_TOKEN'):
            r = sh('gh', 'api', '-X', 'POST', f"repos/{os.environ.get('GITHUB_REPOSITORY')}/pages/builds", check=False)
            report['pages_build_request'] = 'requested' if r.returncode == 0 else f'not requested: {(r.stderr or r.stdout)[-200:]}'
        report['result'] = 'published' if LIVE else 'dry run complete (public site untouched)'
        report['finished_at'] = now()
        print(json.dumps(report, indent=1))
        print(f"OK — {run_id} ({tkv}, {MODE}): coverage {s.get('coverage_health')} ({s.get('key_figures_confirmed')} key figures), "
              f"direct downloads {s.get('direct_downloads_ok')}, {s.get('figures_updated')} figures updated, failures: {s.get('sources_failed') or 'none'}")
        return 0
    except Exception as e:
        report['result'] = 'FAILED'
        report['error'] = str(e)[:1500]
        report['finished_at'] = now()
        print(json.dumps(report, indent=1))
        print(f'ATTENTION: hourly run did not publish: {str(e)[:500]}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
