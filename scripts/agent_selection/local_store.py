#!/usr/bin/env python3
"""Markdown-only local record store, one directory per terminal scope (#120).

Design contract: ``docs/design/agent-role-discovery-and-model-selection.md`` §8
(``.local/agent-orch/<scope-id>/``). This module deliberately does **not** define
a capability model: C1-C4 / C1-C8 stay in ``kit/orchestration.md`` and
``templates/runtime-capability.md``. A record here is a reference plus a short
summary.

Guarantees:

* every persistent file is Markdown (``*.md``); there is no JSON / SQLite store;
* writes go through a temporary file plus ``os.replace``, so a crash cannot
  leave a half-written document;
* a writer takes an exclusive lock file and raises :class:`StoreConflictError`
  instead of silently losing a concurrent append;
* every record carries ``scope_id``, UTC ``created_at`` / ``updated_at``, a
  ``source_class`` from the design's OBSERVED / INFERRED / UNKNOWN / STALE set
  and an explicit ``status``;
* appended records are never replaced by a later append, so two different
  selection events cannot collapse into one history entry;
* a missing file, a broken link, corrupt Markdown or a missing metadata field is
  reported as ``MISSING`` / ``UNKNOWN`` / ``CORRUPT`` -- never as ``PASS``;
* a privacy scan refuses credential shapes, private-key headers and absolute
  terminal / home paths before anything is written.

Two operational boundaries are explicit rather than automatic:

* a lock file left by a crash is reported as :class:`StoreConflictError` and is
  never reclaimed automatically (a human decides whether the old writer is
  really gone);
* ``_namespace-claims/`` is an internal Markdown index of which explicit
  namespace has been used. It is not a scope directory and holds no selection
  record for any scope.

Standard library only. Tests always use a temporary project root; the real
``.local`` directory is never touched.
"""
from __future__ import annotations

import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import scope

__all__ = [
    'DOCUMENTS',
    'RECORD_DOCUMENTS',
    'SOURCE_CLASSES',
    'RECORD_STATUSES',
    'CLAIMS_DIRNAME',
    'UNVERIFIED_CLAIM',
    'StoreError',
    'StoreConflictError',
    'StoreWriteError',
    'StoreReadError',
    'StorePathError',
    'StoreCorruptError',
    'StorePrivacyError',
    'StoreNotInitializedError',
    'MissingRecordError',
    'HistoryDisabledError',
    'Record',
    'DocumentState',
    'LocalStore',
    'open_store',
    'read_namespace_claims',
    'record_namespace_claim',
    'assert_private_safe',
    'utc_timestamp',
]

DOC_README = 'README.md'
DOCUMENTS = (
    DOC_README,
    'agent-registry.md',
    'runtime-capabilities.md',
    'model-evaluation-snapshots.md',
    'selection-history.md',
    'observed-performance.md',
)
RECORD_DOCUMENTS = DOCUMENTS[1:]

SOURCE_CLASSES = ('OBSERVED', 'INFERRED', 'UNKNOWN', 'STALE')
RECORD_STATUSES = ('RECORDED', 'UNKNOWN', 'STALE', 'SUPERSEDED')
UNVERIFIED_CLAIM = 'unverified'
CLAIMS_DIRNAME = '_namespace-claims'
LOCK_NAME = '.agentorch-write.lock'

README_META_START = '<!-- agentorch:scope-meta -->'
README_META_END = '<!-- /agentorch:scope-meta -->'
INDEX_START = '<!-- agentorch:index:start -->'
INDEX_END = '<!-- agentorch:index:end -->'
RECORD_META_START = '<!-- agentorch:record-meta -->'
RECORD_META_END = '<!-- /agentorch:record-meta -->'

RECORD_HEAD_RE = re.compile(r'^###\s+(?P<id>\S.*?)\s*$', re.M)
META_LINE_RE = re.compile(r'^-\s+(?P<key>[a-z][a-z0-9_]*):\s*(?P<value>.*)$')
TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
DETAIL_KEY_RE = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')
CLAIM_TOKEN_RE = re.compile(r'^\s*-\s+`(?P<token>[0-9a-f]{64}|' + UNVERIFIED_CLAIM + r')`\s*$')
SCOPE_META_KEYS = ('scope_id', 'created_at', 'updated_at', 'namespace',
                   'identity_fingerprint', 'identity_sources', 'history_allowed')
REQUIRED_RECORD_KEYS = ('scope_id', 'created_at', 'updated_at',
                        'source_class', 'status', 'summary')

MAX_SUMMARY_LENGTH = 800
MAX_DETAIL_LENGTH = 2000

# Credential shapes and terminal / home paths. Built from parts where a literal
# prefix would otherwise trip the release-package privacy scan (which scans this
# file too, because it ships).
_PEM_BEGIN = '-' * 5 + 'BEGIN'
_AWS_KEY_PREFIX = 'AK' + 'IA'
_GITHUB_PAT = 'github' + '_pat_'
_PRIVACY_PATTERNS = (
    re.compile(r'\bsk-[A-Za-z0-9_-]{16,}'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'),
    re.compile(_GITHUB_PAT + r'[A-Za-z0-9_]{20,}'),
    re.compile(_PEM_BEGIN + r' [A-Z ]*PRIVATE KEY(?: BLOCK)?'),
    re.compile(r'\b' + _AWS_KEY_PREFIX + r'[0-9A-Z]{16}\b'),
    re.compile(r'\bBearer\s+[A-Za-z0-9._~+/=-]{16,}'),
    # Absolute terminal / home paths. A scope id is a digest, so no legitimate
    # record needs one of these.
    re.compile(r'(?<![\w.])/(?:home|Users|root|private/var|dev/pts|dev/tty)(?=[/\s]|$)'),
    re.compile(r'\b[A-Za-z]:[\\/](?:Users|Documents|Desktop)[\\/]', re.I),
)


class StoreError(Exception):
    """Base class for local-store failures."""


class StoreConflictError(StoreError):
    """Another writer holds the lock; the caller must retry explicitly."""


class StoreWriteError(StoreError):
    """A write (including directory / lock creation) failed."""


class StoreReadError(StoreError):
    """A document could not be read."""


class StorePathError(StoreError):
    """A path is a symlink, a directory where a file belongs, or otherwise unsafe."""


class StoreCorruptError(StoreError):
    """Markdown exists but its metadata is missing, duplicated or malformed."""


class StorePrivacyError(StoreError):
    """The record looks like it contains a secret or an absolute terminal path."""


class StoreNotInitializedError(StoreError):
    """The scope directory has no README; initialize() was not completed."""


class MissingRecordError(StoreError):
    """A required document is missing; this is never a PASS."""


class HistoryDisabledError(StoreError):
    """Scope isolation cannot be proven, so history must not be read or written."""


def utc_timestamp(now=None) -> str:
    """Return a UTC ``YYYY-MM-DDTHH:MM:SSZ`` timestamp."""
    if now is None:
        current = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        current = now
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
    else:
        value = str(now)
        if not TIMESTAMP_RE.fullmatch(value):
            raise ValueError(f'timestamp must look like 2026-10-27T12:00:00Z, got {value!r}')
        datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
        return value
    return current.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def assert_private_safe(text: str, label: str) -> None:
    """Raise :class:`StorePrivacyError` if *text* looks like private data."""
    for pattern in _PRIVACY_PATTERNS:
        if pattern.search(text):
            # The pattern is shown; the matched value never is.
            raise StorePrivacyError(
                f'{label}: refusing to write private data (matched {pattern.pattern!r})')


def _one_line(value: str, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{label} must be a string')
    if not value.strip():
        raise ValueError(f'{label} must not be empty')
    if '\n' in value or '\r' in value or '\x00' in value:
        raise ValueError(f'{label} must be a single line')
    if '<!--' in value or '-->' in value:
        raise ValueError(f'{label} must not contain an HTML comment marker')
    if len(value) > limit:
        raise ValueError(f'{label} is longer than {limit} characters')
    return value


@dataclass(frozen=True)
class Record:
    """One appended event in one document."""

    record_id: str
    document: str
    scope_id: str
    created_at: str
    updated_at: str
    source_class: str
    status: str
    summary: str
    details: dict = field(default_factory=dict)
    raw: str = ''


@dataclass(frozen=True)
class DocumentState:
    """Parse result for one document.

    ``status`` is one of ``MISSING`` / ``EMPTY`` / ``RECORDED`` / ``UNKNOWN`` /
    ``CORRUPT``. There is intentionally no ``PASS``: a document is a record
    container, not a capability verdict, and a missing one can never be read as
    passing.
    """

    name: str
    path: Path
    exists: bool
    status: str
    records: tuple[Record, ...] = ()
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exists and not self.issues

    @property
    def passing(self) -> bool:
        """Always ``False``: a record container is never a PASS verdict."""
        return False


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* through a same-directory temporary file + ``os.replace``."""
    path = Path(path)
    fd = None
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent))
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            fd = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
    except OSError as exc:
        raise StoreWriteError(f'could not write {path.name}: {exc}') from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


@contextmanager
def _file_lock(directory: Path):
    """Exclusive lock file; an existing lock is a conflict, never a wait."""
    lock_path = Path(directory) / LOCK_NAME
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise StoreConflictError(
            f'another writer holds {LOCK_NAME} in {Path(directory).name or "."}; '
            'retry after it finishes (no silent overwrite)') from exc
    except OSError as exc:
        raise StoreWriteError(f'could not create the write lock: {exc}') from exc
    try:
        try:
            os.write(fd, f'pid={os.getpid()}\nstarted_at={utc_timestamp()}\n'.encode('utf-8'))
        finally:
            os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def _ensure_directory(directory: Path) -> None:
    if directory.is_symlink():
        raise StorePathError(f'refusing a symlinked directory: {directory.name}')
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoreWriteError(f'could not create {directory.name}: {exc}') from exc
    if not directory.is_dir():
        raise StorePathError(f'{directory.name} exists but is not a directory')


def _clean_meta_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith('`') and value.endswith('`'):
        value = value[1:-1]
    return value.strip()


def _parse_meta_block(text: str, start: str, end: str, label: str) -> tuple[dict, list[str]]:
    issues: list[str] = []
    start_at = text.find(start)
    if start_at < 0:
        return {}, [f'{label}: missing metadata block {start}']
    end_at = text.find(end, start_at + len(start))
    if end_at < 0:
        return {}, [f'{label}: metadata block is not closed with {end}']
    meta: dict[str, str] = {}
    for line in text[start_at + len(start):end_at].splitlines():
        if not line.strip():
            continue
        match = META_LINE_RE.match(line.strip())
        if not match:
            issues.append(f'{label}: unreadable metadata line {line.strip()!r}')
            continue
        key = match.group('key')
        if key in meta:
            issues.append(f'{label}: duplicate metadata field {key}')
            continue
        meta[key] = _clean_meta_value(match.group('value'))
    return meta, issues


def _replace_block(text: str, start: str, end: str, body: str, label: str) -> str:
    start_at = text.find(start)
    end_at = text.find(end, start_at + 1) if start_at >= 0 else -1
    if start_at < 0 or end_at < 0:
        raise StoreCorruptError(f'{label}: managed block {start} / {end} is missing')
    return text[:start_at] + start + '\n' + body.rstrip('\n') + '\n' + end + text[end_at + len(end):]


def _render_scope_readme(scope_id: str, created_at: str, updated_at: str,
                         namespace: str | None, identity_fingerprint: str | None,
                         identity_sources: tuple[str, ...], history_allowed: bool) -> str:
    sources = ', '.join(f'`{source}`' for source in identity_sources) or '`(none)`'
    lines = [
        f'# AgentOrch local scope: {scope_id}',
        '',
        README_META_START,
        f'- scope_id: `{scope_id}`',
        f'- created_at: `{created_at}`',
        f'- updated_at: `{updated_at}`',
        f'- namespace: `{namespace or "(none)"}`',
        f'- identity_fingerprint: `{identity_fingerprint or "(none)"}`',
        f'- identity_sources: {sources}',
        f'- history_allowed: `{str(bool(history_allowed)).lower()}`',
        README_META_END,
        '',
        '## Purpose',
        '',
        'This directory holds local agent-selection records for exactly one',
        'terminal / Agent session. It is not shared between terminals and is not',
        'a capability verdict: C1-C4 / C1-C8 stay in the kit contract.',
        '',
        '## Privacy',
        '',
        'Records are Markdown only. They must not contain credentials, API keys,',
        'tokens, full private prompts, raw conversations, sensitive logs or raw',
        'terminal paths. The scope id above selects a directory; it is not an',
        'authorization token.',
        '',
        INDEX_START,
        '## Index',
        '',
        '| Document | Records | Last updated |',
        '| --- | --- | --- |',
        INDEX_END,
        '',
    ]
    return '\n'.join(lines)


def _render_document(name: str, scope_id: str, created_at: str) -> str:
    title = name[:-3] if name.endswith('.md') else name
    return '\n'.join([
        f'# {title}',
        '',
        'Append-only records for this scope. Missing metadata is reported as',
        'UNKNOWN; it is never read as PASS.',
        '',
        f'<!-- scope: {scope_id}; created_at: {created_at} -->',
        '',
    ])


def _render_record(record: Record) -> str:
    lines = [
        f'### {record.record_id}',
        '',
        RECORD_META_START,
        f'- scope_id: `{record.scope_id}`',
        f'- created_at: `{record.created_at}`',
        f'- updated_at: `{record.updated_at}`',
        f'- source_class: `{record.source_class}`',
        f'- status: `{record.status}`',
        f'- summary: {record.summary}',
        RECORD_META_END,
    ]
    for key in sorted(record.details):
        lines.append(f'- {key}: {record.details[key]}')
    lines.append('')
    return '\n'.join(lines)


def _parse_document(name: str, text: str, scope_id: str) -> tuple[tuple[Record, ...], list[str]]:
    issues: list[str] = []
    first_line = text.splitlines()[0].strip() if text.splitlines() else ''
    if first_line != f'# {name[:-3]}':
        issues.append(f'{name}: document heading is missing or does not match the file name')
    headings = list(RECORD_HEAD_RE.finditer(text))
    records: list[Record] = []
    for index, match in enumerate(headings):
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[match.end():section_end]
        label = f'{name} record {match.group("id")!r}'
        meta, meta_issues = _parse_meta_block(
            section, RECORD_META_START, RECORD_META_END, label)
        issues.extend(meta_issues)
        for key in REQUIRED_RECORD_KEYS:
            if key not in meta:
                issues.append(f'{label}: missing required metadata {key}')
            elif not meta[key]:
                issues.append(f'{label}: empty required metadata {key}')
        if meta_issues or any(not meta.get(key) for key in REQUIRED_RECORD_KEYS):
            continue
        for key in ('created_at', 'updated_at'):
            if not TIMESTAMP_RE.fullmatch(meta[key]):
                issues.append(f'{label}: {key} is not a UTC timestamp')
                break
        else:
            if meta['scope_id'] != scope_id:
                issues.append(f'{label}: scope_id {meta["scope_id"]!r} does not match {scope_id!r}')
            if meta['source_class'] not in SOURCE_CLASSES:
                issues.append(f'{label}: unknown source_class {meta["source_class"]!r}')
            if meta['status'] not in RECORD_STATUSES:
                issues.append(f'{label}: unknown status {meta["status"]!r}')
            details: dict[str, str] = {}
            after = section.split(RECORD_META_END, 1)[1] if RECORD_META_END in section else ''
            for line in after.splitlines():
                if not line.strip():
                    continue
                detail = META_LINE_RE.match(line.strip())
                if not detail:
                    issues.append(f'{label}: unreadable detail line {line.strip()!r}')
                    continue
                key = detail.group('key')
                if not DETAIL_KEY_RE.match(key):
                    issues.append(f'{label}: invalid detail key {key!r}')
                    continue
                details[key] = _clean_meta_value(detail.group('value'))
            records.append(Record(
                record_id=match.group('id'), document=name, scope_id=meta['scope_id'],
                created_at=meta['created_at'], updated_at=meta['updated_at'],
                source_class=meta['source_class'], status=meta['status'],
                summary=meta['summary'], details=details,
                raw=text[match.start():section_end].strip()))
    return tuple(records), issues


def _parse_scope_meta(text: str) -> tuple[dict, list[str]]:
    meta, issues = _parse_meta_block(
        text, README_META_START, README_META_END, DOC_README)
    for key in SCOPE_META_KEYS:
        if key not in meta:
            issues.append(f'{DOC_README}: missing required metadata {key}')
        elif not meta[key]:
            issues.append(f'{DOC_README}: empty required metadata {key}')
    return meta, issues


class LocalStore:
    """One scope's Markdown store under ``<project>/.local/agent-orch/<id>/``."""

    def __init__(self, project_root, scope_id: str, *, history_allowed: bool = True,
                 metadata: dict | None = None):
        self.project_root = Path(project_root).resolve()
        self.scope_id = scope.validate_scope_id(scope_id)
        self.directory = scope.scope_directory(self.project_root, self.scope_id)
        self.history_allowed = bool(history_allowed)
        self.metadata = dict(metadata or {})

    @classmethod
    def from_resolution(cls, project_root, resolution: scope.ScopeResolution, *,
                        metadata: dict | None = None) -> 'LocalStore':
        payload = {
            'namespace': resolution.namespace,
            'identity_fingerprint': resolution.identity_fingerprint,
            'identity_sources': resolution.identity_sources,
            'warning': resolution.warning,
        }
        payload.update(metadata or {})
        return cls(project_root, resolution.require_scope(),
                   history_allowed=resolution.history_allowed, metadata=payload)

    # --- paths -------------------------------------------------------------
    def _doc_path(self, name: str) -> Path:
        if name not in DOCUMENTS:
            raise ValueError(f'unknown document {name!r}; expected one of {DOCUMENTS}')
        path = self.directory / name
        if path.parent != self.directory:
            raise StorePathError(f'refusing a document outside the scope: {name}')
        return path

    def _guard_history(self) -> None:
        if not self.history_allowed:
            raise HistoryDisabledError(
                'scope isolation cannot be proven for this run; persistent history '
                'is disabled by design (use a unique AGENTORCH_SCOPE_ID per terminal '
                'or one-shot isolation mode)')

    def _require_initialized(self) -> None:
        if not self.directory.is_dir():
            raise StoreNotInitializedError(
                f'scope directory does not exist yet: {self.scope_id}')
        readme = self.directory / DOC_README
        if not readme.is_file():
            raise StoreNotInitializedError(
                f'scope {self.scope_id} has no {DOC_README}; run initialize() first')

    # --- initialization ----------------------------------------------------
    def initialize(self, *, now=None, source_class: str = 'UNKNOWN',
                   status: str = 'UNKNOWN') -> DocumentState:
        """Create the scope directory and its six Markdown documents.

        Idempotent: an existing initialized scope is validated, missing documents
        are created, and existing records are preserved untouched.
        """
        self._guard_history()
        if source_class not in SOURCE_CLASSES:
            raise ValueError(f'source_class must be one of {SOURCE_CLASSES}')
        if status not in RECORD_STATUSES:
            raise ValueError(f'status must be one of {RECORD_STATUSES}')
        created_at = utc_timestamp(now)
        _ensure_directory(self.directory)
        with _file_lock(self.directory):
            readme = self.directory / DOC_README
            if readme.exists() or readme.is_symlink():
                if readme.is_symlink():
                    raise StorePathError(f'{DOC_README} must not be a symlink')
                text = self._read_text(readme)
                meta, issues = _parse_scope_meta(text)
                if issues:
                    raise StoreCorruptError('; '.join(issues))
                if meta.get('scope_id') != self.scope_id:
                    raise StoreCorruptError(
                        f'{DOC_README} belongs to scope {meta.get("scope_id")!r}, '
                        f'not {self.scope_id!r}')
            else:
                text = _render_scope_readme(
                    self.scope_id, created_at, created_at,
                    self.metadata.get('namespace'),
                    self.metadata.get('identity_fingerprint'),
                    tuple(self.metadata.get('identity_sources') or ()),
                    self.history_allowed)
                assert_private_safe(text, DOC_README)
                _atomic_write(readme, text)
            for name in RECORD_DOCUMENTS:
                path = self._doc_path(name)
                if path.is_symlink():
                    raise StorePathError(f'{name} must not be a symlink')
                if not path.exists():
                    assert_private_safe(_render_document(name, self.scope_id, created_at), name)
                    _atomic_write(path, _render_document(name, self.scope_id, created_at))
                elif not path.is_file():
                    raise StorePathError(f'{name} exists but is not a file')
            self._write_index_locked(created_at)
        self._record_namespace_claim()
        return self._read_state(DOC_README)

    # --- reading -----------------------------------------------------------
    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding='utf-8')
        except UnicodeDecodeError as exc:
            raise StoreCorruptError(f'{path.name} is not valid UTF-8: {exc}') from exc
        except OSError as exc:
            raise StoreReadError(f'could not read {path.name}: {exc}') from exc

    def _read_state(self, name: str) -> DocumentState:
        path = self._doc_path(name)
        if path.is_symlink():
            exists = path.exists()
            return DocumentState(
                name=name, path=path, exists=exists, status='CORRUPT',
                issues=(f'{name}: refusing to read a symlink'
                        + ('' if exists else ' (broken link)'),))
        if not path.exists():
            return DocumentState(
                name=name, path=path, exists=False, status='MISSING',
                issues=(f'{name}: document does not exist',))
        if not path.is_file():
            return DocumentState(
                name=name, path=path, exists=True, status='CORRUPT',
                issues=(f'{name}: not a regular file',))
        text = self._read_text(path)
        if name == DOC_README:
            meta, issues = _parse_scope_meta(text)
            if meta.get('scope_id') != self.scope_id:
                issues.append(
                    f'{DOC_README}: scope_id {meta.get("scope_id")!r} does not match '
                    f'{self.scope_id!r}')
            if INDEX_START not in text or INDEX_END not in text:
                issues.append(f'{DOC_README}: index block is missing')
            return DocumentState(
                name=name, path=path, exists=True,
                status='UNKNOWN' if issues else 'RECORDED',
                issues=tuple(issues))
        records, issues = _parse_document(name, text, self.scope_id)
        if issues:
            status_value = 'UNKNOWN'
        elif not records:
            status_value = 'EMPTY'
        else:
            status_value = 'RECORDED'
        return DocumentState(name=name, path=path, exists=True,
                             status=status_value, records=records,
                             issues=tuple(issues))

    def read_document(self, name: str) -> DocumentState:
        self._guard_history()
        return self._read_state(name)

    def read_records(self, name: str) -> tuple[Record, ...]:
        state = self.read_document(name)
        if not state.exists:
            raise MissingRecordError(f'{name}: document does not exist (not a PASS)')
        if state.issues:
            raise StoreCorruptError('; '.join(state.issues))
        return state.records

    def read_scope_metadata(self) -> dict:
        self._guard_history()
        state = self._read_state(DOC_README)
        if not state.exists:
            raise MissingRecordError(f'{DOC_README} does not exist')
        meta, issues = _parse_scope_meta(self._read_text(self._doc_path(DOC_README)))
        if issues:
            raise StoreCorruptError('; '.join(issues))
        if meta.get('scope_id') != self.scope_id:
            raise StoreCorruptError(
                f'{DOC_README}: scope_id {meta.get("scope_id")!r} does not match '
                f'{self.scope_id!r}')
        return meta

    # --- appending ---------------------------------------------------------
    def append_record(self, document: str, *, summary: str, source_class: str = 'UNKNOWN',
                      status: str = 'RECORDED', details: dict | None = None,
                      record_id: str | None = None, now=None) -> Record:
        """Append one event; never rewrite an earlier event."""
        self._guard_history()
        if document not in RECORD_DOCUMENTS:
            raise ValueError(f'{document!r} is not a record document; '
                             f'expected one of {RECORD_DOCUMENTS}')
        if source_class not in SOURCE_CLASSES:
            raise ValueError(f'source_class must be one of {SOURCE_CLASSES}')
        if status not in RECORD_STATUSES:
            raise ValueError(f'status must be one of {RECORD_STATUSES}')
        summary = _one_line(summary, 'summary', MAX_SUMMARY_LENGTH)
        timestamp = utc_timestamp(now)
        clean_details: dict[str, str] = {}
        for key, value in (details or {}).items():
            if not isinstance(key, str) or not DETAIL_KEY_RE.match(key):
                raise ValueError(f'invalid detail key {key!r}')
            clean_details[key] = _one_line(value, f'detail {key}', MAX_DETAIL_LENGTH)
        identifier = record_id or f'{timestamp.replace("-", "").replace(":", "")}-{uuid.uuid4().hex[:8]}'
        if (not isinstance(identifier, str) or not identifier.strip()
                or '\n' in identifier or '\r' in identifier or '\x00' in identifier
                or '<!--' in identifier or '-->' in identifier):
            raise ValueError('record_id must be a non-empty single line without comment markers')
        package = Record(
            record_id=identifier, document=document, scope_id=self.scope_id,
            created_at=timestamp, updated_at=timestamp,
            source_class=source_class, status=status,
            summary=summary, details=clean_details)
        rendered = _render_record(package)
        assert_private_safe(rendered, f'{document} record')
        self._require_initialized()
        with _file_lock(self.directory):
            state = self._read_state(document)
            if not state.exists:
                raise MissingRecordError(f'{document}: document does not exist (not a PASS)')
            if state.issues:
                raise StoreCorruptError('; '.join(state.issues))
            path = self._doc_path(document)
            current = self._read_text(path)
            _atomic_write(path, current.rstrip('\n') + '\n\n' + rendered)
            self._write_index_locked(timestamp)
        return package

    # --- index -------------------------------------------------------------
    def _index_body(self) -> str:
        rows = ['## Index', '', '| Document | Records | Last updated |',
                '| --- | --- | --- |']
        for name in RECORD_DOCUMENTS:
            state = self._read_state(name)
            if state.issues:
                count = 'UNKNOWN'
                last = 'UNKNOWN'
            else:
                count = str(len(state.records))
                last = max((record.updated_at for record in state.records), default='(none)')
            rows.append(f'| [{name}]({name}) | {count} | {last} |')
        return '\n'.join(rows)

    def _write_index_locked(self, updated_at: str) -> None:
        readme = self._doc_path(DOC_README)
        text = self._read_text(readme)
        text = _replace_block(text, INDEX_START, INDEX_END,
                              self._index_body() + '\n', DOC_README)
        _atomic_write(readme, text)

    def refresh_index(self, *, now=None) -> None:
        """Rewrite the managed index block; repeated calls are byte-identical."""
        self._guard_history()
        self._require_initialized()
        timestamp = utc_timestamp(now)
        with _file_lock(self.directory):
            self._write_index_locked(timestamp)

    # --- namespace claims --------------------------------------------------
    def _record_namespace_claim(self) -> None:
        namespace = self.metadata.get('namespace')
        if not namespace:
            return
        fingerprint = self.metadata.get('identity_fingerprint') or UNVERIFIED_CLAIM
        record_namespace_claim(self.project_root, namespace, fingerprint)


def open_store(project_root, *, env=None, tty_name=scope._UNSET,
               runtime_session_id=None, namespace=None) -> LocalStore:
    """Resolve a scope from this terminal and open its store.

    Namespace claims are read here (never from another module), so the resolver
    itself stays a pure function.
    """
    env = dict(os.environ) if env is None else dict(env)
    raw_namespace = namespace if namespace is not None else env.get(scope.NAMESPACE_ENV)
    claims: tuple[str, ...] = ()
    if raw_namespace:
        try:
            claims = read_namespace_claims(project_root, str(raw_namespace))
        except StoreCorruptError:
            # A corrupt claim ledger means isolation cannot be proven; treat the
            # namespace as already reused so history stays disabled.
            claims = (UNVERIFIED_CLAIM,)
    resolution = scope.resolve_scope(
        project_root, env=env, tty_name=tty_name,
        runtime_session_id=runtime_session_id, namespace=namespace,
        namespace_claims=claims)
    return LocalStore.from_resolution(project_root, resolution)


def _claims_directory(project_root) -> Path:
    return scope.store_root(project_root) / CLAIMS_DIRNAME


def read_namespace_claims(project_root, namespace: str) -> tuple[str, ...]:
    """Read the Markdown claim ledger for one explicit namespace."""
    namespace = scope.validate_scope_id(namespace)
    path = _claims_directory(project_root) / f'{namespace}.md'
    if path.is_symlink():
        raise StoreCorruptError(f'namespace claim {namespace} is a symlink')
    if not path.exists():
        return ()
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise StoreReadError(f'could not read namespace claim {namespace}: {exc}') from exc
    if '## Claims' not in text:
        raise StoreCorruptError(f'namespace claim {namespace} has no Claims section')
    tokens: list[str] = []
    for line in text.splitlines():
        match = CLAIM_TOKEN_RE.match(line)
        if match:
            tokens.append(match.group('token'))
    if not tokens:
        raise StoreCorruptError(f'namespace claim {namespace} has no readable claims')
    return tuple(tokens)


def record_namespace_claim(project_root, namespace: str, fingerprint: str, *,
                           now=None) -> bool:
    """Record that *fingerprint* used *namespace*; return ``True`` if newly added."""
    namespace = scope.validate_scope_id(namespace)
    fingerprint = str(fingerprint)
    if fingerprint != UNVERIFIED_CLAIM and not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
        raise ValueError('identity fingerprint must be 64 lowercase hex characters')
    claims_dir = _claims_directory(project_root)
    _ensure_directory(claims_dir)
    path = claims_dir / f'{namespace}.md'
    timestamp = utc_timestamp(now)
    with _file_lock(claims_dir):
        if path.exists():
            tokens = list(read_namespace_claims(project_root, namespace))
        else:
            tokens = []
        if fingerprint in tokens:
            return False
        tokens.append(fingerprint)
        lines = [
            f'# Namespace claims: {namespace}',
            '',
            f'- namespace: `{namespace}`',
            f'- updated_at: `{timestamp}`',
            '',
            '## Claims',
            '',
        ]
        lines.extend(f'- `{token}`' for token in tokens)
        lines.append('')
        _atomic_write(path, '\n'.join(lines))
    return True
