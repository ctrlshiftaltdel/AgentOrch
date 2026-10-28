"""Group D - orchestration base and evidence decay scenarios (D1-D9).

Edition fail-closed behaviour, resource locks, stale SHAs, retry caps, audit
classification and metric honesty. Every fixture is synthetic and lives in the
scenario sandbox.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from .common import (
    HEAD_SHA,
    MERGE_SHA,
    _add,
    _canonical,
    _count,
    _dispatch,
    _expect_clean,
    _expect_finding,
    _expect_refused,
    _metrics,
    _packet,
    _packet_errors,
    _project_root,
    _quiet,
    _ready,
    _require,
    _status,
    _task_row,
)


STALE_SHA = 'c' * 40


def _workspace(ctx, root, task_id, head_sha):
    args = SimpleNamespace(id=task_id, head_sha=head_sha, branch='', worktree='', note='')
    with _quiet():
        ctx.orch.set_workspace(root, args)


def _drive_to_master_review(ctx, root, task_id, head_sha=HEAD_SHA):
    _ready(ctx, root, task_id)
    _dispatch(ctx, root, task_id)
    _status(ctx, root, task_id, 'IN_PROGRESS')
    _status(ctx, root, task_id, 'REVIEW_READY', head_sha=head_sha)
    _status(ctx, root, task_id, 'MASTER_REVIEW')


# --------------------------------------------------------------------------- #
# Group D - orchestration base and evidence decay
# --------------------------------------------------------------------------- #
def scenario_d1(ctx):
    """Positive control: a legal edition with a declared resource closes out cleanly."""
    root = _project_root(ctx, config={'resource_lease_ttl': 3600, 'resource_reclaim_grace': 60})
    edition = ctx.orch.read_project_edition(root)
    _require(edition == 'local', f'expected edition local, got {edition!r}')
    _add(ctx, root, 'TASK-D1', ['src/d1.py'], resources=['db:test/main:exclusive'])
    _ready(ctx, root, 'TASK-D1')
    _dispatch(ctx, root, 'TASK-D1')
    _status(ctx, root, 'TASK-D1', 'IN_PROGRESS')
    _status(ctx, root, 'TASK-D1', 'REVIEW_READY', head_sha=HEAD_SHA)
    _status(ctx, root, 'TASK-D1', 'MASTER_REVIEW')
    _status(ctx, root, 'TASK-D1', 'APPROVED', head_sha=HEAD_SHA)
    _status(ctx, root, 'TASK-D1', 'MERGED', merge_sha=MERGE_SHA)
    _status(ctx, root, 'TASK-D1', 'VERIFIED')
    remaining = _count(ctx, root, "SELECT COUNT(*) FROM resource_locks "
                                 "WHERE task_id=? AND state<>'RELEASED'", ('TASK-D1',))
    held = _count(ctx, root, 'SELECT COUNT(*) FROM resource_locks WHERE task_id=?', ('TASK-D1',))
    _require(remaining == 0, f'resource lock still held after VERIFIED ({remaining})')
    _require(held == 1, f'expected exactly one acquired resource lock row, got {held}')
    return [f'edition: {edition}', 'resource db:test/main:exclusive acquired on dispatch',
            'closed out: 1 lock row, 0 non-RELEASED after VERIFIED']


def scenario_d2(ctx):
    """An illegal, missing or duplicated edition stops scheduling instead of guessing."""
    cases = []
    for label, body in (('missing key', '# Synthetic project\n\nno edition line here\n'),
                        ('illegal value', '# Synthetic project\n\n- `edition`\uFF1A`gitlab`\n'),
                        ('duplicate key', '# Synthetic project\n\n- `edition`\uFF1A`local`\n'
                                          '- `edition`\uFF1A`github`\n')):
        root = ctx.sandbox / f'edition-{label.replace(" ", "-")}'
        root.mkdir(parents=True, exist_ok=True)
        (root/'docs').mkdir(parents=True, exist_ok=True) or (root / 'docs/PROJECT.md').write_text(body, encoding='utf-8')
        message = _expect_refused(lambda root=root: ctx.orch.read_project_edition(root),
                                  'docs/PROJECT.md', label)
        cases.append(f'{label}: {message}')

    broken = ctx.sandbox / 'edition-absent'
    broken.mkdir(parents=True, exist_ok=True)
    cases.append('missing file: ' + _expect_refused(
        lambda: ctx.orch.read_project_edition(broken), 'file not found', 'missing file'))

    github = _project_root(ctx, edition='github')
    cases.append('github scheduling: ' + _expect_refused(
        lambda: ctx.orch.require_local_scheduler(github, 'task dispatch'),
        'cannot run', 'github edition'))
    return cases


def scenario_d3(ctx):
    """A conflicting exclusive resource is denied atomically, leaving no half lock."""
    root = _project_root(ctx, config={'resource_lease_ttl': 3600, 'resource_reclaim_grace': 60})
    _add(ctx, root, 'TASK-D3A', ['src/d3a.py'], resources=['db:test/main:exclusive'])
    _add(ctx, root, 'TASK-D3B', ['src/d3b.py'], resources=['db:test/main:exclusive'])
    _ready(ctx, root, 'TASK-D3A')
    _dispatch(ctx, root, 'TASK-D3A', holder='synthetic-host:1')
    _ready(ctx, root, 'TASK-D3B')
    message = _expect_refused(lambda: _dispatch(ctx, root, 'TASK-D3B', holder='synthetic-host:2'),
                              'MODE_CONFLICT', 'conflicting exclusive resource')
    held = _count(ctx, root, "SELECT COUNT(*) FROM resource_locks "
                             "WHERE task_id='TASK-D3B' AND state<>'RELEASED'")
    locks = _count(ctx, root, "SELECT COUNT(*) FROM locks WHERE task_id='TASK-D3B'")
    denied = _count(ctx, root, "SELECT COUNT(*) FROM resource_lock_events "
                               "WHERE task_id='TASK-D3B' AND event_type='LOCK_DENIED_CONFLICT'")
    status = _task_row(ctx, root, 'TASK-D3B')['status']
    _require(held == 0, f'the refused task keeps {held} resource lock row(s)')
    _require(locks == 0, f'the refused task keeps {locks} path lock(s)')
    _require(denied >= 1, 'the denial left no LOCK_DENIED_CONFLICT audit event')
    _require(status == 'READY', f'the refused task left READY (status {status})')
    return [f'gate fired: {message}', 'refused task: 0 resource locks, 0 path locks, still READY',
            f'audit: {denied} LOCK_DENIED_CONFLICT event(s)']


def scenario_d4(ctx):
    """A new head invalidates the old approval; the stale SHA cannot be merged."""
    root = _project_root(ctx)
    _add(ctx, root, 'TASK-D4', ['src/d4.py'])
    _drive_to_master_review(ctx, root, 'TASK-D4', head_sha=HEAD_SHA)
    _status(ctx, root, 'TASK-D4', 'APPROVED', head_sha=HEAD_SHA)
    _workspace(ctx, root, 'TASK-D4', STALE_SHA)
    row = _task_row(ctx, root, 'TASK-D4')
    _require(row['status'] == 'MASTER_REVIEW',
             f'a new head did not return the task to MASTER_REVIEW (got {row["status"]})')
    _require(row['approved_sha'] == '', 'the stale approval SHA survived the head change')
    merge = _expect_refused(lambda: _status(ctx, root, 'TASK-D4', 'MERGED', merge_sha=MERGE_SHA),
                            'illegal transition MASTER_REVIEW -> MERGED', 'merge after head change')
    stale = _expect_refused(lambda: _status(ctx, root, 'TASK-D4', 'APPROVED', head_sha=HEAD_SHA),
                            'does not match current task head', 'approval reusing the old head')
    return ['new head: status MASTER_REVIEW, approved_sha cleared',
            f'merge refused: {merge}',
            f'old-head approval refused: {stale}']


def scenario_d5(ctx):
    """The review-fix cap is enforced; the fourth round becomes BLOCKED."""
    root = _project_root(ctx, config={'retry_cap': 3})
    _add(ctx, root, 'TASK-D5', ['src/d5.py'])
    _drive_to_master_review(ctx, root, 'TASK-D5')
    for _ in range(3):
        _status(ctx, root, 'TASK-D5', 'IN_PROGRESS')
        _status(ctx, root, 'TASK-D5', 'REVIEW_READY', head_sha=HEAD_SHA)
        _status(ctx, root, 'TASK-D5', 'MASTER_REVIEW')
    _status(ctx, root, 'TASK-D5', 'IN_PROGRESS', note='fourth review-fix round requested')
    row = _task_row(ctx, root, 'TASK-D5')
    _require(row['status'] == 'BLOCKED', f'expected BLOCKED after the cap, got {row["status"]}')
    _require(row['retries'] >= 3, f'retry counter is {row["retries"]}, expected at least 3')
    note = _task_row(ctx, root, 'TASK-D5', 'last_note')['last_note']
    _require('Retry cap reached' in note, f'the block note does not explain the cap: {note!r}')
    return [f'retries={row["retries"]}' , 'fourth round -> BLOCKED',
            f'note: {note}']


def scenario_d6(ctx):
    """BLOCKING / NEEDS_HUMAN_DECISION in the audit forbid a closeout claim."""
    header = '| 检查项 | verdict | 依据 |\n|---|---|---|\n'
    clean = header + ('| functional completeness | PASS | task 1 evidence, head aaaa |\n'
                      '| cross-module invariants | NON_BLOCKING_HARDENING | tracked as hardening |\n'
                      '| residual items | NOT_APPLICABLE | N/A: no legacy data in this stage |\n')
    _expect_clean(ctx.vk.audit_conclusion_errors('## 阶段审计结论\n\n' + clean),
                  'clean audit closeout')

    blocking = clean + '| permissions and security | BLOCKING | probe missing |\n'
    finding = _expect_finding(ctx.vk.audit_conclusion_errors('## 阶段审计结论\n\n' + blocking),
                              'BLOCKING', 'blocking audit row')
    _require('不得标记为完成' in finding or 'not be claimed complete' in finding,
             f'the BLOCKING finding does not forbid the closeout: {finding}')

    human = clean + '| legacy data migration | NEEDS_HUMAN_DECISION | owner has not decided |\n'
    human_finding = _expect_finding(ctx.vk.audit_conclusion_errors('## 阶段审计结论\n\n' + human),
                                    'NEEDS_HUMAN_DECISION', 'human-decision audit row')
    return [f'clean audit: 0 findings (PASS / NON_BLOCKING_HARDENING / NOT_APPLICABLE)',
            f'gate fired: {finding}', f'gate fired: {human_finding}']


def scenario_d7(ctx):
    """An executor's unbacked 'done' claim cannot reach APPROVED or VERIFIED."""
    fields = _canonical(ctx)[0]
    text = _packet(ctx, [('x', 'AC-1', 'the change is verified')], [])
    findings = _packet_errors(ctx, text)
    empty = _expect_finding(findings, 'evidence matrix has no evidence items', 'empty matrix')
    no_evidence = _expect_finding(findings, 'AC-1 has no evidence item', 'unbacked AC')

    root = _project_root(ctx)
    _add(ctx, root, 'TASK-D7', ['src/d7.py'])
    verify = _expect_refused(lambda: _status(ctx, root, 'TASK-D7', 'VERIFIED'),
                             'illegal transition PLANNED -> VERIFIED', 'self-verification')
    _require(_count(ctx, root, 'SELECT COUNT(*) FROM locks WHERE task_id=?', ('TASK-D7',)) == 0,
             'a refused VERIFIED left locks behind')
    _require(fields, 'evidence contract is empty')
    return [f'gate fired: {empty}', f'gate fired: {no_evidence}', f'state machine: {verify}']


# --------------------------------------------------------------------------- #
# Group D, second family - metric honesty
# --------------------------------------------------------------------------- #
METRIC_KEYS = ('sample_size', 'eligible', 'missing', 'missing_rate', 'coverage')
FORBIDDEN_SCORE_TERMS = ('coupling', 'cohesion', 'cohesion score', '内聚', '耦合')


def scenario_d8(ctx):
    """Positive control: the report carries window, sample and missing for every metric."""
    metrics = _metrics()
    dataset = metrics.load_gold_sample()
    window = metrics.resolve_window(dataset['events'], '2026-01-01', '2026-02-01',
                                    datetime(2026, 2, 2, tzinfo=timezone.utc))
    report = metrics.build_report(dataset, window, 3, 14.0)
    _require(len(report['metrics']) == 7, len(report['metrics']))
    _require(report['window']['start'] == '2026-01-01T00:00:00Z', report['window'])
    _require(report['window']['end'] == '2026-02-01T00:00:00Z', report['window'])
    for metric in report['metrics']:
        for key in METRIC_KEYS:
            _require(key in metric, f'{metric["id"]} is missing {key}')
        _require(metric['coverage'] is not None, f'{metric["id"]} has no coverage')
        _require(metric['missing_rate'] is not None, f'{metric["id"]} has no missing rate')
        _require(metric['sample_size'] >= 0, metric)
    _require(report['summary']['metrics_with_data'] == 7, report['summary'])
    _require(report['summary']['overall_coverage'] > 0, report['summary'])
    text = json.dumps(report, ensure_ascii=False).lower()
    for term in FORBIDDEN_SCORE_TERMS:
        _require(term not in text, f'the report invented a score term: {term}')
    _require(all(not metric['id'].startswith('M8') for metric in report['metrics']),
             'the report grew a metric outside the documented seven')
    rendered = metrics.render(report)
    _require('not a ranking' in rendered, 'the human output dropped its no-ranking limit')
    return [f'metrics: {len(report["metrics"])} with data, '
            f'sample sizes {[m["sample_size"] for m in report["metrics"]]}',
            f'window: {report["window"]["start"]} .. {report["window"]["end"]} '
            f'({report["window"]["timezone"]})',
            f'missing / coverage present on every row; overall coverage '
            f'{report["summary"]["overall_coverage"]}',
            'no coupling/cohesion score term in the JSON or the human output']


def scenario_d9(ctx):
    """Empty input is "no data"; a missing event is missing, never a zero value."""
    metrics = _metrics()
    empty_path = ctx.sandbox / 'empty-events.json'
    empty_path.write_text(json.dumps({'events': []}), encoding='utf-8')
    empty = metrics.load_fixture(empty_path)
    window = metrics.resolve_window(empty['events'], '2026-01-01', '2026-02-01',
                                    datetime(2026, 2, 2, tzinfo=timezone.utc))
    report = metrics.build_report(empty, window, 3, 14.0)
    for metric in report['metrics']:
        _require(metric['status'] == 'no_data',
                 f'{metric["id"]} on an empty input is {metric["status"]}')
        _require(metric['value'] is None, f'{metric["id"]} fabricated a value')
        _require(metric['sample_size'] == 0, metric)
        _require(metric['coverage'] == 0.0, metric)
    _require(report['summary']['headline'] == 'no data', report['summary'])
    _require('no data' in metrics.render(report), 'the human output hides the empty state')

    partial_path = ctx.sandbox / 'one-ready.json'
    partial_path.write_text(json.dumps({'events': [{
        'task_id': 'T-1', 'at': '2026-01-02T09:00:00Z', 'action': 'STATUS_CHANGED',
        'from_status': 'PLANNED', 'to_status': 'READY', 'data': {}}]}), encoding='utf-8')
    partial = metrics.load_fixture(partial_path)
    window = metrics.resolve_window(partial['events'], '2026-01-01', '2026-02-01',
                                    datetime(2026, 2, 2, tzinfo=timezone.utc))
    partial_report = metrics.build_report(partial, window, 3, 14.0)
    elapsed = next(metric for metric in partial_report['metrics'] if metric['id'] == 'M1')
    _require(elapsed['status'] == 'partial', elapsed['status'])
    _require(elapsed['sample_size'] == 0 and elapsed['missing'] == 1, elapsed)
    _require(elapsed['value'] is None,
             'a task with no end event produced a zero-hour duration')
    _require(elapsed['detail']['missing_tasks'] == ['T-1'], elapsed['detail'])
    return [f'empty input: {len(report["metrics"])} metrics all no_data with coverage 0.0',
            f'headline: {report["summary"]["headline"]}',
            f'READY without VERIFIED: M1 {elapsed["status"]} sample={elapsed["sample_size"]} '
            f'missing={elapsed["missing"]} value={elapsed["value"]}']
