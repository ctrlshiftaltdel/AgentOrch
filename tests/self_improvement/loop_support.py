"""Fixtures for the #50 self-improvement loop tests.

Lesson records, scans and state are invented. Risk evidence is not: the
fixtures create throw-away Git repositories with real commits so the planner can
be tested against the same Git checks production uses. No network is touched and
no real task carrier is written.
"""
import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import support
from agentorch_self_improvement import lessons, orchestration

NOW = support.T2
PR_URL = 'https://github.com/ctrlshiftaltdel/TalkDND/pull/12'


class GitRepo:
    """A real, throw-away Git repository for risk-evidence tests."""

    def __init__(self, prefix='agentorch-risk-'):
        base = os.environ.get('TMPDIR') or None
        self.root = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
        self._git('init', '-q')
        self._git('config', 'user.email', 'risk@example.invalid')
        self._git('config', 'user.name', 'risk fixture')
        self._git('config', 'commit.gpgsign', 'false')
        self._git('config', 'core.autocrlf', 'false')

    def _git(self, *args, check=True, input_bytes=None):
        completed = subprocess.run(
            ['git', '-C', str(self.root), *args], capture_output=True,
            input=input_bytes)
        if check and completed.returncode != 0:
            raise RuntimeError(
                f'git {" ".join(args)} failed: '
                f'{completed.stderr.decode("utf-8", "replace")}')
        return completed

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding='utf-8')

    def symlink(self, path, target):
        link = self.root / path
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)

    def mv(self, old, new):
        self._git('mv', old, new)

    def gitlink(self, path, sha):
        self._git('update-index', '--add', '--cacheinfo',
                  f'160000,{sha},{path}')

    def commit(self, files, *, message='fixture commit', stage=False):
        for path, content in files.items():
            if content is None:
                target = self.root / path
                if target.is_symlink() or target.exists():
                    target.unlink()
            else:
                self.write(path, content)
        if not stage:
            self._git('add', '-A')
        self._git('commit', '-qm', message)
        return self.head()

    def head(self):
        return self._git('rev-parse', 'HEAD').stdout.decode().strip()

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


_DEFAULT_REPO = GitRepo('agentorch-risk-default-')
DEFAULT_REPO_ROOT = _DEFAULT_REPO.root
DEFAULT_BASE_SHA = _DEFAULT_REPO.commit({'docs/example.md': 'old line\n'},
                                        message='base')
DEFAULT_HEAD_SHA = _DEFAULT_REPO.commit({'docs/example.md': 'old line  \n'},
                                        message='whitespace-only docs change')
HEAD = DEFAULT_HEAD_SHA
atexit.register(_DEFAULT_REPO.close)


def general_lesson(**overrides):
    """A lesson that is allowed to become a task: GENERAL_LESSON + GENERALIZED."""
    payload = {
        'content_class': 'GENERAL_LESSON',
        'evidence_level': 'E3_FIXED_WITH_TEST',
        'status': 'GENERALIZED',
        'applicability': {
            'scope': 'agentorch_general',
            'applies_to': ['scripts/self_improvement/planning.py'],
            'excluded_contexts': ['没有 CI 的项目'],
        },
        'decision': support.decision('ADOPT'),
    }
    payload.update(overrides)
    return support.lesson(**payload)


def spec(**overrides):
    payload = {
        'title': '在规划模块拒绝缺少验证命令的提案',
        'goal': '让规划模块在生成任务时拒绝缺少验证命令的提案。',
        'context': '当前提案只做字段搬运，缺少可执行验证时会静默通过。',
        'allowed_paths': ['docs/example.md'],
        'git_evidence': git_evidence(DEFAULT_BASE_SHA, DEFAULT_HEAD_SHA),
        'forbidden_paths': ['every other path'],
        'dependencies': [],
        'acceptance_criteria': [
            {'id': 'AC-1',
             'statement': '缺少 validation_commands 的提案不进入 READY。',
             'verification': 'python3 -m unittest tests.self_improvement.test_planning'},
            {'id': 'AC-2',
             'statement': '现有完整提案仍保持 READY。',
             'verification': 'python3 -m unittest tests.self_improvement.test_planning'},
        ],
        'invariants': '只在 planning 模块内校验，不改状态机或锁语义。',
        'negative_cases': '缺少 AC 验证、空 verification、裸 N/A 都必须被拒绝。',
        'migration_cases': 'N/A: 不改变任何已持久化格式，旧提案按字段校验处理。',
        'integration_paths': 'orchestration.run_loop 只消费 READY 提案；carrier 只在 READY 时登记。',
        'unverified_assumptions': '现有任务包字段是否足以表达所有改进；由 Master 审查确认。',
        'validation_commands': [
            'python3 -m unittest tests.self_improvement.test_planning',
            'python3 -m unittest tests.self_improvement.test_orchestration',
        ],
        'risks': 'none identified for the low-risk path.',
        'declared_resources': [],
    }
    payload.update(overrides)
    return payload


def git_evidence(base_sha, head_sha, **overrides):
    payload = {'base_sha': base_sha, 'head_sha': head_sha}
    payload.update(overrides)
    return payload


def authorization(**overrides):
    """A caller-asserted authorization that the planner must ignore."""
    payload = {
        'granted_by': 'fake',
        'scope': 'governance-adjustment',
        'source': 'https://example.invalid/',
    }
    payload.update(overrides)
    return payload


def state(edition='local', **overrides):
    payload = {
        'edition': edition,
        'branch': 'task/50-self-improvement-loop',
        'head_sha': HEAD,
        'workspace': {'status_short': [], 'clean': True},
        'active_tasks': [],
        'open_pull_requests': [],
    }
    payload.update(overrides)
    return payload


def source_report(source_id='talkdnd', status='OK', blocked_reason=None,
                  evidence_count=1):
    return {
        'source_id': source_id,
        'repository': 'ctrlshiftaltdel/TalkDND',
        'enabled': True,
        'scan_status': status,
        'blocked_reason': blocked_reason,
        'scan_started_at': NOW,
        'scan_finished_at': NOW,
        'window': {'mode': 'first_scan', 'since': support.T0, 'until': NOW},
        'cursor_before': None,
        'cursor_after': None,
        'evidence_count': evidence_count,
        'excluded_count': 0,
        'evidence': [],
    }


def scan_report(sources=None, ingestions=None, scan_mode='live'):
    sources = [source_report()] if sources is None else list(sources)
    payload = {
        'report_schema_version': 1,
        'generated_at': NOW,
        'scan_mode': scan_mode,
        'sources': sources,
        'lessons': [],
        'counts': {'sources_total': len(sources), 'evidence_items': 0},
        'decisions_required': [],
    }
    if ingestions is not None:
        payload['ingestions'] = list(ingestions)
    return payload


def created(lesson_id='LSN-TALKDND-001'):
    return {'kind': 'created', 'lesson_id': lesson_id, 'duplicate_of': None,
            'evidence_urls': [PR_URL]}


class RecordingCarrier:
    """A fake carrier: records calls, returns a shaped registration, executes nothing."""

    def __init__(self, kind='local'):
        self.kind = kind
        self.edition = kind
        self.calls = []
        self.registrations = []

    def register(self, proposal):
        self.calls.append(dict(proposal))
        if proposal.get('status') != 'READY':
            raise orchestration.CarrierError(
                'fake carrier refuses a non-READY proposal')
        ref = 'TASK-0001' if self.kind == 'local' else 'issue:dry-run/1'
        registration = orchestration.Registration(
            carrier=self.kind, ref=ref, status='PLANNED', executed=False,
            payload={'shape': 'task-row' if self.kind == 'local' else 'issue-body'})
        self.registrations.append(registration)
        return registration


def open_conflict_lesson(**overrides):
    """A record where an OPEN conflict is attached after construction.

    The #49 builders refuse `GENERALIZED` + OPEN conflict (schema `allOf`), but a
    legacy store can contain one. The planner must still refuse to plan it.
    """
    record = support.lesson(
        content_class='GENERAL_LESSON', status='VERIFIED',
        evidence_level='E3_FIXED_WITH_TEST',
        applicability={'scope': 'agentorch_general',
                       'applies_to': ['scripts/self_improvement/planning.py'],
                       'excluded_contexts': []},
        **overrides)
    record['conflicts_with'] = [lessons.make_conflict('LSN-TALKDND-002')]
    return record
