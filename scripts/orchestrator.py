#!/usr/bin/env python3
# Local single-user task registry and atomic path-lock manager.
# This tool manages governance state only; it does not launch agents or merge Git branches.
#
# Named resource locks implement the frozen contract docs/design/RESOURCE-LOCKS.md
# (v1). That file is the only authority for their semantics; this module must not
# restate or extend it. Path-lock behaviour is unchanged.
from __future__ import annotations
import argparse, hashlib, json, os, re, socket, sqlite3, stat, sys, unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

ACTIVE = {'DISPATCHED','IN_PROGRESS','REVIEW_READY','MASTER_REVIEW','APPROVED','MERGED','BLOCKED','NEEDS_HUMAN_DECISION','FAILED'}
TERMINAL = {'VERIFIED','CANCELED'}
# Paths may only be re-scoped while the task is parked (kit 3.1 rule 4). These
# are exactly the states that keep their locks: an active task must be blocked
# first, and a merged task is frozen.
AMENDABLE_PATH_STATES = {'BLOCKED','NEEDS_HUMAN_DECISION','FAILED'}
TRANSITIONS = {
 'PLANNED': {'READY','BLOCKED','NEEDS_HUMAN_DECISION','CANCELED'},
 'READY': {'DISPATCHED','BLOCKED','NEEDS_HUMAN_DECISION','CANCELED'},
 'DISPATCHED': {'IN_PROGRESS','FAILED','BLOCKED','NEEDS_HUMAN_DECISION','CANCELED'},
 'IN_PROGRESS': {'REVIEW_READY','FAILED','BLOCKED','NEEDS_HUMAN_DECISION','CANCELED'},
 'REVIEW_READY': {'MASTER_REVIEW','IN_PROGRESS','BLOCKED','NEEDS_HUMAN_DECISION'},
 'MASTER_REVIEW': {'APPROVED','IN_PROGRESS','BLOCKED','NEEDS_HUMAN_DECISION','CANCELED'},
 'APPROVED': {'MERGED','MASTER_REVIEW','BLOCKED'},
 'MERGED': {'VERIFIED','BLOCKED'},
 'VERIFIED': set(), 'BLOCKED': {'READY','IN_PROGRESS','MASTER_REVIEW','NEEDS_HUMAN_DECISION','FAILED','CANCELED'},
 'NEEDS_HUMAN_DECISION': {'READY','IN_PROGRESS','MASTER_REVIEW','CANCELED'},
 'FAILED': {'READY','CANCELED'}, 'CANCELED': set(),
}

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def root_path(raw): return Path(raw).resolve()

def _display_path(root, path, isolated):
    """Path text for a diagnostic; flat mode keeps its historical absolute path."""
    if not isolated:
        return str(path)
    try:
        relative=Path(path).relative_to(Path(root))
    except (TypeError, ValueError):
        return Path(path).name
    return relative.as_posix() or '.'

_ABS_PATH_TOKEN = re.compile(r"(?<![\w.~:-])/(?:[^\s'\"`()\[\]{}<>;:]+)")

def _public_diagnostic(text, root):
    """Strip host-specific absolute paths from an isolated-mode diagnostic.

    Isolated rejection text travels to public Issue evidence. Rewrite the
    AgentOrch root to a relative form and any other absolute token to
    ``<path>``; flat mode never calls this, so its historical text is a fixed
    compatibility contract.
    """
    text=str(text)
    prefix=str(Path(root))
    if prefix not in ('/', '.'):
        text=text.replace(prefix+'/', '').replace(prefix, '.')
    home=str(Path.home())
    if home and home not in ('/', '.'):
        text=text.replace(home+'/', '~/').replace(home, '~')
    def repl(match):
        token=match.group(0).rstrip('.,')
        return '<path>'+match.group(0)[len(token):]
    return _ABS_PATH_TOKEN.sub(repl, text)


# Isolated layout (Issue #153) -------------------------------------------------
# `<host>/agentorch/` is a *recognisable* shape used only to route the CLI. It is
# not source authentication and it is not OS-level isolation. Direct library
# calls keep the historical flat contract: only the CLI may set a separate state
# root, so existing callers (delivery scenarios, tests) are unchanged.
LAYOUTS = ('auto', 'flat', 'isolated')
ISOLATED_DIRNAME = 'agentorch'

class LayoutError(RuntimeError):
    """A CLI layout/root request that cannot be honoured without guessing."""

def state_dir(root, state_root=None):
    """Config/state/backups directory for this call.

    The state root is always bound to the caller. A flat call keeps the
    historical ``<root>/.orchestration``; the isolated CLI passes the explicit
    root resolved from the scheduler location. There is deliberately no
    process-global fallback, so concurrent, nested or direct API calls cannot
    read or write each other's task database.
    """
    return Path(state_root) if state_root is not None else root/'.orchestration'

def db_path(root, state_root=None): return state_dir(root, state_root)/'state.sqlite3'
def connect(root, state_root=None):
    p=db_path(root, state_root)
    if state_root is not None:
        # Defense in depth for direct library callers: the CLI already validated
        # before dispatch, but an explicit isolated state root must never follow
        # a linked component either.
        _validate_isolated_components(root, state_root)
    if not p.exists():
        # The flat message keeps its historical absolute path. An isolated
        # rejection must not expose the host path in public evidence.
        raise RuntimeError('task database not initialized; run `init` first'
                           if state_root is not None
                           else f'task database not initialized: {p}; run `init` first')
    con=sqlite3.connect(p, timeout=15, isolation_level=None)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=15000')
    # Migrate incrementally on open: a database created before resource locks
    # existed must gain the tables without being rebuilt (§8.2 rule 5).
    ensure_resource_schema(con)
    return con

# Review-fix retry cap precedence (see also read_retry_cap):
#   1. <root>/.orchestration/config.json key "retry_cap" when it is an integer >= 1.
#   2. Otherwise the built-in default RETRY_CAP_DEFAULT = 3.
# A missing file, a missing key, or an invalid value falls back to the default so
# the historical hard-coded behaviour (3 rounds) is preserved for existing roots.
RETRY_CAP_DEFAULT = 3
EDITIONS = ('local','github')
# `.orchestration/config.json` holds local CLI runtime parameters only. It never
# selects the edition: that key lives in <root>/docs/PROJECT.md, which is
# the single authority (see read_project_edition). Keeping the edition out of this
# file removes the second, silently-defaulting definition that used to exist.
RUNTIME_DEFAULTS = {'retry_cap': RETRY_CAP_DEFAULT, 'local_dir': '.local'}
PROJECT_CONFIG = 'docs/PROJECT.md'
# docs/PLAN.md is the plan of record (task registration, dispatch and phase
# close-out read it); templates/plan.md is only the seed copy that `init`
# copies once. A root-level PLAN.md is the legacy (<=1.3) location: init never
# touches it and never seeds docs/PLAN.md while it exists, so the design goals
# always keep a single authority. Both names are relative to the project root.
PLAN_DOC = 'docs/PLAN.md'
LEGACY_PLAN_DOC = 'PLAN.md'
PLAN_TEMPLATE = 'templates/plan.md'
EDITION_LINE = re.compile(r'^\s*[-*]\s*`?edition`?\s*[:：]\s*(?P<value>.*)$', re.M)

class EditionError(RuntimeError):
    """The authoritative project edition is missing, unreadable or illegal."""

def config_path(root, state_root=None): return state_dir(root, state_root)/'config.json'
def project_config_path(root): return root/PROJECT_CONFIG

def _runtime_config(root, state_root=None):
    """retry_cap / local_dir from config.json, with the historical silent fallback.

    Unchanged on purpose: a missing file, a missing key, a wrong type, an
    out-of-range value or malformed JSON yields the built-in default so existing
    local roots keep working. The edition is deliberately not read here, so an
    edition problem can never change or block the retry budget.
    """
    cfg=dict(RUNTIME_DEFAULTS)
    sources={key:'default' for key in RUNTIME_DEFAULTS}
    data=None; state='missing'
    try:
        data=json.loads(config_path(root, state_root).read_text(encoding='utf-8'))
        state='invalid' if not isinstance(data,dict) else 'ok'
    except FileNotFoundError:
        pass
    except Exception:
        state='invalid'
    if state=='ok':
        value=data.get('retry_cap')
        if not isinstance(value,bool) and isinstance(value,int) and value>=1:
            cfg['retry_cap']=value; sources['retry_cap']='config.json'
        value=data.get('local_dir')
        if isinstance(value,str) and value.strip():
            cfg['local_dir']=value.strip(); sources['local_dir']='config.json'
    cfg['sources']=sources
    cfg['config_state']=state
    cfg['recorded_edition']=data.get('edition') if state=='ok' else None
    return cfg

def read_project_edition(root, isolated=False):
    """Return the edition declared by <root>/docs/PROJECT.md.

    This key is the only authority for edition selection. A missing file, a
    missing or duplicated key, an empty value, a wrong type or an unsupported
    value raises EditionError naming the actual content and the file to fix.
    Nothing falls back to 'local'.

    ``isolated`` only changes how the file is named in the diagnostic: an
    isolated CLI prints the root-relative path so its error text can be pasted
    into public evidence. The flat contract keeps the absolute path.
    """
    path=project_config_path(root)
    shown=_display_path(root, path, isolated)
    try:
        text=path.read_text(encoding='utf-8')
    except FileNotFoundError:
        raise EditionError(f'{PROJECT_CONFIG}: file not found at {shown}; add the key `edition` with value local or github') from None
    except (OSError,UnicodeDecodeError) as exc:
        detail=_public_diagnostic(exc, root) if isolated else str(exc)
        raise EditionError(f'{PROJECT_CONFIG}: cannot read {shown} ({detail}); repair the file so its `edition` key is readable') from None
    matches=list(EDITION_LINE.finditer(text))
    if not matches:
        raise EditionError(f'{PROJECT_CONFIG}: missing required key `edition` in {shown}; add one line "- `edition`: local" or "- `edition`: github"')
    if len(matches)>1:
        where=', '.join(str(text.count('\n',0,m.start())+1) for m in matches)
        raise EditionError(f'{PROJECT_CONFIG}: key `edition` is declared {len(matches)} times (lines {where}) in {shown}; keep exactly one declaration')
    raw=matches[0].group('value').strip()
    value=raw.strip('`').strip()
    if not value:
        raise EditionError(f'{PROJECT_CONFIG}: key `edition` is empty in {shown}; allowed values are local, github')
    if value not in EDITIONS:
        raise EditionError(f"{PROJECT_CONFIG}: key `edition` has unsupported value {raw!r} in {shown}; "
                           f"allowed values are {', '.join(EDITIONS)} (write the value alone, with no extra text)")
    return value

def read_config(root, state_root=None):
    """Return the effective runtime configuration, its provenance and the edition.

    Runtime keys keep their historical silent fallback. The edition is read from
    docs/PROJECT.md and raises EditionError when missing or illegal, so
    routing can never silently run on the wrong task carrier.
    """
    cfg=_runtime_config(root, state_root)
    cfg['edition']=read_project_edition(root, isolated=state_root is not None)
    cfg['sources']['edition']=PROJECT_CONFIG
    return cfg

def read_retry_cap(root, state_root=None):
    """Return the effective review-fix retry cap (integer >= 1, default 3).

    Precedence: config.json key "retry_cap" wins when present and valid;
    anything else (missing file/key, non-integer, bool, < 1, malformed JSON)
    silently yields RETRY_CAP_DEFAULT. Reads the same parser as read_config, so
    the two entry points can never disagree about the cap, while an edition
    problem cannot block this value.
    """
    return _runtime_config(root, state_root)['retry_cap']

def require_local_scheduler(root, action, state_root=None):
    """Refuse to write local task state unless the project declares the local edition.

    The CLI implements the local edition only. Under a github (or broken) edition
    it must fail loudly and name the next step instead of quietly behaving like
    local: github task state lives in Issues and PRs, and writing the SQLite
    registry would create a second, conflicting source of truth.
    """
    isolated=state_root is not None
    edition=read_project_edition(root, isolated=isolated)
    if edition=='local': return
    where=PROJECT_CONFIG if isolated else project_config_path(root)
    raise EditionError(
        f'edition is {edition!r} ({where}); this CLI cannot run `{action}` for the {edition} edition: '
        f'it writes the local SQLite task registry, which is a read-only archive under {edition}. '
        'Next: create or update the task as a GitHub Issue using editions/github/issue-template.md, record the '
        'ORCH-TASK-REGISTRY block before starting, and drive the PR per editions/github/execution.md. '
        'init, backup, config show, task list, task show and task resources stay available.'
    )

def config_show(root, state_root=None):
    """Print the effective configuration, where each value came from, and the edition.

    Returns 0 when the project edition is legal, 1 when it is missing or illegal.
    The edition is reported from docs/PROJECT.md only; a leftover
    `edition` key in config.json is shown as ignored, never applied.
    """
    cfg=_runtime_config(root, state_root); path=config_path(root, state_root); exists=path.exists()
    isolated=state_root is not None
    shown=_display_path(root, path, isolated)
    edition=None; error=None
    try: edition=read_project_edition(root, isolated=isolated)
    except EditionError as exc: error=exc
    print(f'Config file: {shown}' + ('' if exists else ' (not found; using built-in defaults)'))
    rows=(('retry_cap',cfg['retry_cap'],cfg['sources']['retry_cap']),
          ('local_dir',cfg['local_dir'],cfg['sources']['local_dir']),
          ('edition',edition or '(invalid)',PROJECT_CONFIG))
    for key,value,source in rows:
        print(f'  {key:<10} = {value!s:<10} (source: {source})')
    edition_source=PROJECT_CONFIG if isolated else project_config_path(root)
    print(f'Edition is selected only by `edition` in {edition_source}; '
          f'{path.name} holds local CLI runtime parameters (retry_cap, local_dir) and never selects an edition.')
    if cfg['config_state']=='invalid':
        print(f'Note: {shown} is not a readable JSON object; retry_cap and local_dir fall back to the built-in defaults.')
    if cfg['recorded_edition'] is not None:
        print(f'Note: {shown} also records edition = {cfg["recorded_edition"]!r}; that legacy key is ignored.')
    try: edit_path=path.relative_to(root).as_posix()
    except ValueError: edit_path=_display_path(root, path, True)
    print(f'Edit {edit_path} to change retry_cap or local_dir; invalid values there fall back to defaults.')
    if error:
        print(f'ERROR: {_public_diagnostic(error, root) if isolated else error}',file=sys.stderr)
        return 1
    if edition!='local':
        print(f'Scheduling commands (task add/ready/dispatch/status/workspace/amend/unlock) are refused under the '
              f'{edition} edition; this CLI maintains the local task registry only (matrix: README.md 6.2).')
    return 0

def safe_rollback(con):
    """Roll back only when a transaction is actually open; never mask the real error.

    If BEGIN IMMEDIATE itself failed (for example because the database is locked)
    there is no transaction to roll back, and issuing ROLLBACK would raise
    'cannot rollback - no transaction is active', hiding the original failure.
    """
    try:
        if con.in_transaction:
            con.execute('ROLLBACK')
    except sqlite3.Error:
        pass

def event(con, task_id, action, from_status=None, to_status=None, note='', data=None):
    con.execute('INSERT INTO events(task_id,at,action,from_status,to_status,note,data_json) VALUES(?,?,?,?,?,?,?)',
        (task_id,now(),action,from_status,to_status,note,json.dumps(data or {},ensure_ascii=False)))

def record_path_conflict(con, task_id, phase, status, conflicts):
    """Persist a refused path check in the caller's transaction (M4 evidence).

    `phase` is `dispatch` or `ready`. A dispatch refusal is the conflict M4
    reads (`CONFLICT_RECORDED`, `data.kind=path`); a `ready` preflight is a
    separate, audit-only event and must never be read as a dispatch conflict.
    The caller COMMITs before re-raising, so the refusal leaves evidence while
    still inserting no lock row.
    """
    action='CONFLICT_RECORDED' if phase=='dispatch' else 'PATH_PREFLIGHT_REFUSED'
    detail=[{'path':p,'held_path':held,'holder_task':holder} for p,held,holder in conflicts]
    event(con,task_id,action,None,None,
          f'{phase} refused: path conflict; no lock row was inserted',
          {'kind':'path','phase':phase,'status':status,'conflicts':detail})

def init_plan(root):
    """Seed <root>/docs/PLAN.md from templates/plan.md exactly once.

    docs/PLAN.md is the plan of record, so an existing file is never
    overwritten (that would destroy the project owner's plan). A legacy root
    PLAN.md (<=1.3 location) is never touched either: while it exists,
    docs/PLAN.md is NOT seeded, because two design-goal authorities are worse
    than one stale path — init prints the migration step instead. A missing
    template is reported and skipped: `init` must still finish its task-store
    work and exit 0. A write failure is raised instead of being reported as
    success. Returns 'created', 'kept', 'legacy-present' or 'template-missing'.
    """
    target=root/PLAN_DOC
    legacy=root/LEGACY_PLAN_DOC
    if target.exists():
        print(f'PLAN.md already present, kept unchanged: {target}')
        print('Next: judge task direction from the design goals in docs/PLAN.md; templates/plan.md is only the initial copy.')
        return 'kept'
    if legacy.exists():
        print(f'Legacy root PLAN.md found; docs/PLAN.md not created: {legacy}')
        print('Next: keep a single design-goal authority — run `git mv PLAN.md docs/PLAN.md` (create docs/ first if needed), then rerun init; see README section 10.')
        return 'legacy-present'
    template=root/PLAN_TEMPLATE
    if not template.is_file():
        print(f'PLAN.md not created: template missing at {template}')
        print('Next: copy templates/plan.md into docs/PLAN.md (kit installed there), then rerun init.')
        return 'template-missing'
    (root/'docs').mkdir(parents=True,exist_ok=True)
    target.write_bytes(template.read_bytes())
    print(f'Created docs/PLAN.md from {template}')
    print('下一步：填写 docs/PLAN.md 的设计目标（为什么做、做到什么程度、原则与边界）；配图放 docs/images/，在正文中用相对路径引用，Agent 只按文中指引查看图片')
    return 'created'

def _state_relative_to_root(root, state_root):
    """Root-relative state path, or LayoutError when it has no in-tree form.

    ``init`` prints the backup hint relative to the project root, so a state
    root outside ``root`` cannot be named. The isolated CLI always derives a
    descendant of the root; only a direct library caller can pass an outside
    path. Refusing here keeps init all-or-nothing instead of raising ValueError
    after the database and config already exist.

    A purely lexical ``relative_to`` is not enough: ``<root>/..`` and
    ``<root>/.local/../../x`` share the root's leading path parts, and a
    symlinked in-tree ancestor (``<root>/link -> outside``) is inside the root
    by spelling but outside it by resolution. Both used to be accepted while the
    write landed beside or outside the root, so this check runs before the
    first mkdir/write and rejects ``..`` segments, symlinked or non-directory
    ancestors, and any path that does not resolve back inside the root.
    """
    root=Path(root)
    state=Path(state_root)
    if '..' in state.parts:
        raise LayoutError('state root must not contain ".." path segments; '
                          'refusing a state directory that can escape the project root') from None
    real_root=root.resolve()
    lexical_root=Path(os.path.abspath(root))
    lexical_state=Path(os.path.abspath(state))
    try:
        relative=lexical_state.relative_to(lexical_root)
    except ValueError:
        raise LayoutError('state root must be inside the project root; '
                          'refusing a state directory outside it') from None
    # Walk the caller's own spelling, not the resolved path: a link that points
    # back inside the root still means the caller's path traverses a link, and
    # only an ancestor walk can see it (the final entry is checked elsewhere).
    current=lexical_root
    for part in relative.parts:
        current=current/part
        try:
            info=os.lstat(current)
        except FileNotFoundError:
            # This component and everything below it are absent, so there is no
            # link to traverse yet.
            continue
        except NotADirectoryError:
            raise LayoutError('state root must be a directory path inside the project root; '
                              'a parent component is not a directory') from None
        if stat.S_ISLNK(info.st_mode):
            raise LayoutError('state root must not pass through a symlinked directory; '
                              'refusing a state directory that resolves outside the project root') from None
        if not stat.S_ISDIR(info.st_mode):
            raise LayoutError('state root must be a directory path inside the project root; '
                              'a parent component is not a directory') from None
    try:
        relative=state.resolve().relative_to(real_root)
    except ValueError:
        raise LayoutError('state root must be inside the project root after resolving links; '
                          'refusing a state directory outside it') from None
    return relative

def _validate_git_exclude_target(root):
    """Fail before any state write when the Git exclude rule cannot be recorded.

    ``init`` appends the machine-local state rule to ``.git/info/exclude``. A
    ``.git/info`` that exists as a non-directory used to surface only when the
    append ran, after the database and config were already written, leaving a
    half-created store behind a FileExistsError. Validate first so the refusal
    is all-or-nothing. ``.git`` as a regular file is Git's linked-worktree
    ``gitdir:`` layout: it has no info directory to append to, and it is left
    untouched, exactly as before.
    """
    gitdir=Path(root)/'.git'
    if not gitdir.is_dir():
        return
    info=gitdir/'info'
    try:
        entry=os.stat(info)
    except FileNotFoundError:
        if os.path.lexists(info):
            raise LayoutError('.git/info is a dangling link; refusing to write state '
                              'because the Git exclude rule cannot be recorded') from None
        return
    except NotADirectoryError:
        raise LayoutError('.git/info is not a directory; refusing to write state '
                          'because the Git exclude rule cannot be recorded') from None
    if not stat.S_ISDIR(entry.st_mode):
        raise LayoutError('.git/info exists but is not a directory; refusing to write state '
                          'because the Git exclude rule cannot be recorded')

def init(root, state_root=None):
    state_rel=None
    if state_root is not None:
        # The CLI validated the isolated tree before dispatch, but a direct
        # library caller can pass an explicit isolated state root too. Refuse a
        # linked component here as well, before any state or Git metadata write.
        _validate_isolated_components(root, state_root)
        state_rel=_state_relative_to_root(root, state_root)
    # Both refusals above and this one are all-or-nothing: no state directory,
    # config or database exists yet when the layout cannot be honoured.
    _validate_git_exclude_target(root)
    d=state_dir(root, state_root); d.mkdir(parents=True,exist_ok=True)
    (d/'backups').mkdir(exist_ok=True)
    cfg=config_path(root, state_root)
    if not cfg.exists():
        cfg.write_text(json.dumps(RUNTIME_DEFAULTS)+'\n',encoding='utf-8')
    con=sqlite3.connect(db_path(root, state_root))
    con.executescript('''
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS tasks(
      id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
      paths_json TEXT NOT NULL, depends_json TEXT NOT NULL, acceptance_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'PLANNED', branch TEXT NOT NULL DEFAULT '', worktree TEXT NOT NULL DEFAULT '',
      head_sha TEXT NOT NULL DEFAULT '', approved_sha TEXT NOT NULL DEFAULT '', merged_sha TEXT NOT NULL DEFAULT '', retries INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_note TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS locks(
      path TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS locks_task_idx ON locks(task_id);
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, at TEXT NOT NULL,
      action TEXT NOT NULL, from_status TEXT, to_status TEXT, note TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS events_task_idx ON events(task_id,id);
    ''')
    ensure_resource_schema(con)
    con.close()
    # Keep private, machine-local state out of the worktree and commits. Flat
    # keeps the historical `.orchestration/` text; an isolated call owns a
    # different directory, so its rule must cover the path it actually writes.
    gi=root/'.git'/'info'/'exclude'
    if (root/'.git').is_dir():
        gi.parent.mkdir(parents=True,exist_ok=True)
        current=gi.read_text(encoding='utf-8',errors='ignore') if gi.exists() else ''
        if state_root is not None:
            rule=state_rel.as_posix()+'/'
        else:
            rule='.orchestration/'
        if rule not in current.splitlines():
            with gi.open('a',encoding='utf-8') as f: f.write(f'\n# Agent-Orchestration machine-local state\n{rule}\n')
    print(f'Initialized local task registry: {db_path(root, state_root)}')
    print(f'Review-fix retry cap: {read_retry_cap(root, state_root)} (edit key "retry_cap" in {cfg} to change it)')
    print(f'Local task registry only: the edition is read from {project_config_path(root)}, not from this config.')
    hint=state_rel if state_rel is not None else d.relative_to(root)
    print(f'Back up {hint.as_posix()}/ with your project backups.')
    init_plan(root)

def normalize_path(raw):
    s=raw.replace('\\','/').strip()
    p=PurePosixPath(s)
    if not s or p.is_absolute() or re.match(r'^[A-Za-z]:/', s) or any(part in ('..','.') for part in p.parts) or any(ch in s for ch in '*?[]'):
        raise ValueError(f'invalid relative path: {raw!r}')
    return str(p).rstrip('/') or '.'

def overlaps(a,b):
    if a==b or a=='.' or b=='.': return True
    return a.startswith(b.rstrip('/')+'/') or b.startswith(a.rstrip('/')+'/')

def get_task(con, task_id):
    row=con.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
    if not row: raise RuntimeError(f'unknown task: {task_id}')
    return row

def task_add(root, args, state_root=None):
    require_local_scheduler(root,'task add',state_root=state_root)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{1,63}',args.id): raise ValueError('task ID must be 2-64 chars: letters, digits, dot, underscore, hyphen')
    paths=sorted(set(normalize_path(p) for p in args.paths))
    if not paths: raise ValueError('at least one allowed path is required')
    if not args.acceptance: raise ValueError('at least one acceptance criterion is required')
    # Declared resources are optional; an empty declaration keeps the task outside
    # the resource-lock space entirely (contract §8.2 rule 1).
    resources=[f'{e["resource_id"]}:{e["mode"]}' + (f':{e["ttl"]}' if e['ttl'] else '')
               for e in parse_resource_specs(getattr(args,'resources',None) or [])]
    depends=sorted(set(args.depends or []))
    if args.id in depends: raise ValueError('task cannot depend on itself')
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE')
        for dep in depends:
            if not con.execute('SELECT 1 FROM tasks WHERE id=?',(dep,)).fetchone(): raise RuntimeError(f'dependency not found: {dep}')
        con.execute('INSERT INTO tasks(id,title,description,paths_json,depends_json,acceptance_json,resources_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (args.id,args.title,args.description or '',json.dumps(paths),json.dumps(depends),json.dumps(args.acceptance,ensure_ascii=False),json.dumps(resources),'PLANNED',now(),now()))
        # Detect cycles after insertion. Rollback avoids leaving a bad task behind.
        seen=set(); stack=set()
        def visit(tid):
            if tid in stack: raise RuntimeError('dependency cycle detected')
            if tid in seen: return
            stack.add(tid)
            row=con.execute('SELECT depends_json FROM tasks WHERE id=?',(tid,)).fetchone()
            for d in json.loads(row['depends_json']): visit(d)
            stack.remove(tid); seen.add(tid)
        visit(args.id)
        event(con,args.id,'TASK_CREATED',None,'PLANNED','Task created',{'paths':paths,'depends':depends,'declared_resources':resources})
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{args.id}: created in PLANNED')
    if resources: print(f'{args.id}: declared resources {", ".join(resources)}')

def task_list(root, args, state_root=None):
    con=connect(root, state_root)
    try:
        rows=con.execute('SELECT * FROM tasks ORDER BY created_at,id').fetchall()
        for r in rows:
            if args.active_only and r['status'] in TERMINAL: continue
            paths=', '.join(json.loads(r['paths_json']))
            locks=[x['path'] for x in con.execute('SELECT path FROM locks WHERE task_id=? ORDER BY path',(r['id'],))]
            print(f"{r['id']}\t{r['status']}\t{r['title']}\n  paths: {paths}\n  locks: {', '.join(locks) if locks else '(none)'}\n  retries: {r['retries']}")
            # Resource locks are reported only when the task participates in them,
            # so a path-lock-only task prints exactly what it printed before.
            declared=declared_resources(r)
            held=held_resource_rows(con,r['id'])
            if declared or held:
                print(f"  resources: {', '.join(declared) if declared else '(none)'}")
                for row in held:
                    print(f"    {row['resource_id']} {row['mode']} {row['state']} "
                          f"lease_until {row['lease_until']} holder {row['holder']}"
                          + resource_blocking_note(row))
    finally: con.close()

def resource_blocking_note(row):
    """Short, honest conflict note for listings (no claim beyond the state)."""
    if row['state']=='RELEASED':
        return f" (released {row['released_at']}, reason {row['release_reason']})"
    if row['mode']=='exclusive':
        return ' (blocks every new claim on this resource until released)'
    return ' (blocks new exclusive claims on this resource until released)'

def task_show(root, task_id, state_root=None):
    con=connect(root, state_root)
    try:
        r=get_task(con,task_id)
        print(f"# {r['id']} — {r['title']}\n\nStatus: {r['status']}\nDescription: {r['description']}\nAllowed paths:\n"+'\n'.join(f'- {p}' for p in json.loads(r['paths_json']))+'\n\nDependencies:\n'+('\n'.join(f'- {d}' for d in json.loads(r['depends_json'])) or '- None')+'\n\nAcceptance criteria:\n'+'\n'.join(f'- [ ] {x}' for x in json.loads(r['acceptance_json']))+f"\n\nBranch: {r['branch'] or '(unset)'}\nWorktree: {r['worktree'] or '(unset)'}\nHead SHA: {r['head_sha'] or '(unset)'}\nApproved SHA: {r['approved_sha'] or '(unset)'}\nMerge SHA: {r['merged_sha'] or '(unset)'}\nRetries: {r['retries']}\nLast note: {r['last_note'] or '(none)'}")
        declared=declared_resources(r); held=held_resource_rows(con,task_id)
        lines=[f"- declared: {', '.join(declared)}" if declared else '- declared: (none)']
        for row in held:
            lines.append(f"- {row['resource_id']} {row['mode']} {row['state']} holder {row['holder']} "
                         f"lease_until {row['lease_until']} renewed_at {row['renewed_at']} "
                         f"lock_id {row['lock_id']}"+resource_blocking_note(row))
        denials=con.execute("SELECT at,resource_id,reason FROM resource_lock_events WHERE task_id=? "
                            "AND event_type IN ('LOCK_DENIED_CONFLICT','LOCK_SET_ROLLED_BACK','LOCK_QUARANTINED') "
                            'ORDER BY event_id DESC LIMIT 3',(task_id,)).fetchall()
        for denial in denials:
            lines.append(f"- blocking reason {denial['at']} {denial['resource_id']}: {denial['reason']}")
        print('\nResource locks (contract: docs/design/RESOURCE-LOCKS.md):\n'+'\n'.join(lines))
        print('\nEvents:')
        for e in con.execute('SELECT * FROM events WHERE task_id=? ORDER BY id',(task_id,)):
            line=f"- {e['at']} {e['action']} {e['from_status'] or '-'}→{e['to_status'] or '-'} {e['note']}"
            data=json.loads(e['data_json'] or '{}')
            if e['action']=='RECORD_AMENDED' and data.get('field')=='paths':
                # The before/after path sets stay readable without opening data_json.
                line+=(f" [paths: {', '.join(data.get('before') or []) or '(none)'}"
                       f" -> {', '.join(data.get('after') or []) or '(none)'}]")
            print(line)
    finally: con.close()

def dependency_check(con, row):
    for dep in json.loads(row['depends_json']):
        d=get_task(con,dep)
        if d['status']!='VERIFIED': raise RuntimeError(f'dependency {dep} is {d["status"]}, expected VERIFIED')

def task_ready(root, task_id, state_root=None):
    require_local_scheduler(root,'task ready',state_root=state_root)
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE'); r=get_task(con,task_id)
        if r['status'] not in {'PLANNED','BLOCKED','FAILED','NEEDS_HUMAN_DECISION'}: raise RuntimeError(f'cannot set READY from {r["status"]}')
        dependency_check(con,r)
        # Current locks must not overlap declared paths. It is OK that lock is not acquired until dispatch; this is a preflight only.
        paths=json.loads(r['paths_json'])
        for lock in con.execute('SELECT path,task_id FROM locks').fetchall():
            if lock['task_id']==task_id: continue
            for p in paths:
                if overlaps(p,lock['path']):
                    # Preflight refusals are auditable but are not dispatch
                    # conflicts: record the separate event, commit it, and then
                    # raise the unchanged error (M4 never counts this event).
                    message=f'path conflict: {p} overlaps {lock["path"]}, held by {lock["task_id"]}'
                    record_path_conflict(con,task_id,'ready',r['status'],
                                         [(p,lock['path'],lock['task_id'])])
                    con.execute('COMMIT')
                    raise RuntimeError(message)
        old=r['status']; con.execute("UPDATE tasks SET status='READY',updated_at=?,last_note=? WHERE id=?",(now(),'Dependencies and paths checked',task_id))
        event(con,task_id,'STATUS_CHANGED',old,'READY','Dependencies and paths checked')
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{task_id}: READY')

def default_holder():
    """A locatable default holder: this host and this process (§2.2)."""
    override=os.environ.get('ORCH_HOLDER','').strip()
    if override: return override
    return f'{socket.gethostname()}:{os.getpid()}'

def task_dispatch(root, task_id, options=None, state_root=None):
    require_local_scheduler(root,'task dispatch',state_root=state_root)
    # The holder is only validated when the task declares resources, so a
    # path-lock-only dispatch keeps its previous behaviour byte for byte.
    holder=getattr(options,'holder',None) or default_holder()
    con=connect(root, state_root)
    acquired=None
    try:
        con.execute('BEGIN IMMEDIATE'); r=get_task(con,task_id)
        if r['status']!='READY': raise RuntimeError(f'only READY tasks can be dispatched; current status is {r["status"]}')
        dependency_check(con,r)
        paths=json.loads(r['paths_json'])
        conflicts=[]
        locks=con.execute('SELECT path,task_id FROM locks WHERE task_id<>?',(task_id,)).fetchall()
        for p in paths:
            for lock in locks:
                if overlaps(p,lock['path']): conflicts.append((p,lock['path'],lock['task_id']))
        if conflicts:
            detail='; '.join(f'{p} overlaps {held} (task {tid})' for p,held,tid in conflicts)
            # The refusal is the event M4 counts. Write it in this transaction
            # and commit it before raising, so the audit trail survives while no
            # lock row is inserted and the message / exit code stay unchanged.
            record_path_conflict(con,task_id,'dispatch',r['status'],conflicts)
            con.execute('COMMIT')
            raise RuntimeError('dispatch refused; path conflict: '+detail)
        # Resource locks are an independent second gate (contract §1.2): the path
        # check above keeps its wording and runs first, then the resource set is
        # acquired in the same transaction. A task without a declaration never
        # touches the resource tables.
        specs=declared_resources(r)+list(getattr(options,'resource',None) or [])
        if specs:
            holder=validate_holder(holder)
            entries=resolve_resource_entries(root,specs,state_root)
            acquired=acquire_resource_set(con,task_id,entries,holder,
                                          actor=getattr(options,'actor',None) or 'orchestrator',
                                          reason='dispatch',evidence_ref=getattr(options,'evidence',None) or '(none)')
        for p in paths:
            con.execute('INSERT OR REPLACE INTO locks(path,task_id,created_at) VALUES(?,?,?)',(p,task_id,now()))
        old=r['status']; con.execute("UPDATE tasks SET status='DISPATCHED',updated_at=?,last_note=? WHERE id=?",(now(),'Atomic path locks acquired before dispatch',task_id))
        event(con,task_id,'TASK_DISPATCHED',old,'DISPATCHED','Atomic path locks acquired',
              {'paths':paths,'resources':acquired['entries'] if acquired else [],'resource_status':acquired['status'] if acquired else None,
               'holder':holder if acquired else None,'fingerprint':acquired['fingerprint'] if acquired else None})
        con.execute('COMMIT')
    except ResourceDenied:
        # The denial audit trail (and any quarantine transition) is already written
        # in this transaction; commit it so the evidence survives. No lock row was
        # inserted before the whole set passed, so resource_locks stays净空.
        con.execute('COMMIT'); raise
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{task_id}: DISPATCHED; locks acquired for {", ".join(paths)}')
    if acquired:
        wanted=', '.join(f'{e["resource_id"]}:{e["mode"]}' for e in acquired['entries'])
        print(f'{task_id}: resource locks {acquired["status"]} for {wanted} '
              f'(holder {holder}, fingerprint {acquired["fingerprint"][:12]})')
    print('Now pass the self-contained task packet to a real Execution Agent. This command does not launch agents.')

def set_workspace(root, args, state_root=None):
    require_local_scheduler(root,'task workspace',state_root=state_root)
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE'); r=get_task(con,args.id)
        if r['status'] in TERMINAL: raise RuntimeError('cannot update workspace for terminal task')
        approved=r['approved_sha']
        status=r['status']; oldsha=r['head_sha']
        newsha=args.head_sha or oldsha
        if status=='APPROVED' and newsha and newsha!=approved:
            con.execute("UPDATE tasks SET status='MASTER_REVIEW',approved_sha='',head_sha=?,branch=COALESCE(NULLIF(?,''),branch),worktree=COALESCE(NULLIF(?,''),worktree),updated_at=?,last_note=? WHERE id=?",
                (newsha,args.branch or '',args.worktree or '',now(),'head changed; previous approval invalidated',args.id))
            event(con,args.id,'APPROVAL_INVALIDATED','APPROVED','MASTER_REVIEW','head changed',{'old_head':oldsha,'new_head':newsha})
        else:
            con.execute('UPDATE tasks SET head_sha=?,branch=COALESCE(NULLIF(?,\'\'),branch),worktree=COALESCE(NULLIF(?,\'\'),worktree),updated_at=?,last_note=? WHERE id=?',
                (newsha,args.branch or '',args.worktree or '',now(),args.note or 'workspace updated',args.id))
            event(con,args.id,'WORKSPACE_UPDATED',status,status,args.note or 'workspace updated',{'branch':args.branch,'worktree':args.worktree,'head_sha':newsha})
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{args.id}: workspace updated')

def task_status(root, args, state_root=None):
    require_local_scheduler(root,'task status',state_root=state_root)
    dest=args.status.upper()
    if dest not in TRANSITIONS: raise ValueError(f'unknown status: {dest}')
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE'); r=get_task(con,args.id); old=r['status']
        if dest in {'APPROVED','MERGED','VERIFIED','BLOCKED','NEEDS_HUMAN_DECISION','FAILED','CANCELED'} and not (args.note or '').strip():
            raise RuntimeError(f'{dest} requires an audit note via --note (include decision / evidence / recovery reason)')
        if dest not in TRANSITIONS[old]: raise RuntimeError(f'illegal transition {old} -> {dest}; allowed: {", ".join(sorted(TRANSITIONS[old])) or "none"}')
        if dest=='READY': raise RuntimeError('use `task ready <ID>` so dependency and path-conflict checks cannot be bypassed')
        if dest=='APPROVED':
            sha=args.head_sha or r['head_sha']
            if not sha: raise RuntimeError('APPROVED requires a current --head-sha')
            if r['head_sha'] and sha!=r['head_sha']: raise RuntimeError(f'approval SHA {sha} does not match current task head {r["head_sha"]}')
            con.execute('UPDATE tasks SET approved_sha=? WHERE id=?',(sha,args.id))
        elif old=='APPROVED' and dest=='MERGED':
            if not r['approved_sha'] or r['approved_sha'] != r['head_sha']:
                raise RuntimeError('approved SHA does not match current task head; return to MASTER_REVIEW')
            if not getattr(args, 'merge_sha', None):
                raise RuntimeError('MERGED requires --merge-sha for the resulting local merge commit')
            if not re.fullmatch(r'[0-9a-fA-F]{40}', args.merge_sha):
                raise RuntimeError('MERGED requires a full 40-character hex --merge-sha (abbreviated SHAs are refused)')
            args.merge_sha=args.merge_sha.lower()
        if dest=='IN_PROGRESS' and old=='MASTER_REVIEW':
            retry_cap=read_retry_cap(root, state_root)
            if r['retries'] >= retry_cap:
                dest='BLOCKED'
                retries=r['retries']
                args.note=(args.note + '; ' if args.note else '') + f'Retry cap reached after {retry_cap} review-fix rounds; manual decision required.'
            else:
                retries=r['retries']+1
                con.execute('UPDATE tasks SET retries=? WHERE id=?',(retries,args.id))
        else: retries=r['retries']
        new_head=args.head_sha or r['head_sha']
        if old=='APPROVED' and args.head_sha and dest!='MERGED' and args.head_sha!=r['approved_sha']:
            raise RuntimeError('head changed after approval; approval is invalid')
        note=args.note or ''
        if old=='APPROVED' and dest=='MASTER_REVIEW':
            con.execute("UPDATE tasks SET approved_sha='' WHERE id=?",(args.id,))
        if dest=='MERGED':
            con.execute('UPDATE tasks SET status=?,merged_sha=?,updated_at=?,last_note=? WHERE id=?',(dest,getattr(args, 'merge_sha', None),now(),note,args.id))
        else:
            con.execute('UPDATE tasks SET status=?,head_sha=?,updated_at=?,last_note=? WHERE id=?',(dest,new_head,now(),note,args.id))
        event(con,args.id,'STATUS_CHANGED',old,dest,note,{'head_sha':new_head,'merge_sha':getattr(args, 'merge_sha', None),'retries':retries})
        released_resources=[]
        if dest in TERMINAL:
            con.execute('DELETE FROM locks WHERE task_id=?',(args.id,))
            event(con,args.id,'LOCKS_RELEASED',dest,dest,'terminal task status; path locks released')
            # Only VERIFIED / CANCELED auto-release resource locks (contract §5.2).
            held=held_resource_rows(con,args.id)
            if held:
                release_reason='NORMAL_COMPLETE' if dest=='VERIFIED' else 'CANCELED'
                released_resources=release_resource_set(con,args.id,release_reason,
                                                        note or f'task {dest}: resource locks released',
                                                        'orchestrator','task status '+dest,rows=held)
        elif dest=='NEEDS_HUMAN_DECISION':
            # The holder may still be alive; the lock is frozen, not released (§5.2).
            for row in held_resource_rows(con,args.id):
                quarantine_resource_row(con,row,'orchestrator',
                                        f'task {args.id} is NEEDS_HUMAN_DECISION: {note or "awaiting human decision"}',
                                        'task status NEEDS_HUMAN_DECISION',now())
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{args.id}: {old} -> {dest}' + (f' (head {args.head_sha})' if args.head_sha and dest!='MERGED' else f' (merge {args.merge_sha})' if getattr(args, 'merge_sha', None) else ''))
    if released_resources:
        print(f'{args.id}: released {len(released_resources)} resource lock(s): {", ".join(released_resources)}')

def task_amend(root, args, state_root=None):
    """Audited corrections to task fields that were already recorded.

    Two mutually exclusive modes:

    - `--merge-sha` corrects `merged_sha` on a MERGED / VERIFIED task. Behaviour
      and error text are unchanged from before `--paths` existed.
    - `--paths` re-scopes a task parked in BLOCKED / NEEDS_HUMAN_DECISION / FAILED
      (kit 3.1 rule 4). The conflict check against every other task's locks and
      the lock rewrite happen in one BEGIN IMMEDIATE, so a half lock set can
      never be committed.
    """
    require_local_scheduler(root,'task amend',state_root=state_root)
    raw_paths=getattr(args,'paths',None)
    if raw_paths is not None and (args.merge_sha or '').strip():
        raise RuntimeError('amend accepts either --paths or --merge-sha, not both')
    if raw_paths is not None:
        amend_task_paths(root,args,raw_paths,state_root); return
    sha=(args.merge_sha or '').strip()
    if not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
        raise RuntimeError('amend requires a full 40-character hex --merge-sha')
    if not (args.note or '').strip():
        raise RuntimeError('amend requires --note explaining the merge SHA correction')
    new=sha.lower()
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE'); r=get_task(con,args.id)
        if r['status'] not in {'MERGED','VERIFIED'}:
            raise RuntimeError(f'merge SHA can only be amended on MERGED or VERIFIED tasks; current status is {r["status"]}')
        old=r['merged_sha']
        con.execute('UPDATE tasks SET merged_sha=?,updated_at=?,last_note=? WHERE id=?',(new,now(),args.note,args.id))
        event(con,args.id,'RECORD_AMENDED',r['status'],r['status'],args.note,{'field':'merged_sha','old':old,'new':new})
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{args.id}: merge SHA amended {old or "(unset)"} -> {new}')

def amend_task_paths(root, args, raw_paths, state_root=None):
    """Re-scope a parked task and rewrite its path locks atomically (kit 3.1 rule 4).

    Only states that keep their locks are eligible, so an active task must be
    blocked first. Every new path is checked against the other tasks' locks with
    the same `overlaps()` rule dispatch uses; one conflict refuses the whole
    rewrite. On success the task's lock rows are exactly the new path set.
    """
    if not raw_paths:
        raise RuntimeError('amend --paths requires at least one allowed path')
    note=(args.note or '').strip()
    if not note:
        raise RuntimeError('amend --paths requires --note explaining the reason and the authorisation')
    paths=sorted(set(normalize_path(p) for p in raw_paths))
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE'); r=get_task(con,args.id)
        if r['status'] not in AMENDABLE_PATH_STATES:
            raise RuntimeError('paths can only be amended on BLOCKED / NEEDS_HUMAN_DECISION / FAILED tasks; '
                               f'current status is {r["status"]}')
        before=json.loads(r['paths_json'])
        conflicts=[]
        locks=con.execute('SELECT path,task_id FROM locks WHERE task_id<>?',(args.id,)).fetchall()
        for p in paths:
            for lock in locks:
                if overlaps(p,lock['path']): conflicts.append((p,lock['path'],lock['task_id']))
        if conflicts:
            detail='; '.join(f'{p} overlaps {held} (task {tid})' for p,held,tid in conflicts)
            raise RuntimeError('amend refused; path conflict: '+detail)
        con.execute('UPDATE tasks SET paths_json=?,updated_at=?,last_note=? WHERE id=?',
                    (json.dumps(paths),now(),note,args.id))
        # Rewrite the lock set in the same transaction: the old rows go, exactly
        # the new paths remain. Any failure above rolled back before this point.
        con.execute('DELETE FROM locks WHERE task_id=?',(args.id,))
        for p in paths:
            con.execute('INSERT OR REPLACE INTO locks(path,task_id,created_at) VALUES(?,?,?)',(p,args.id,now()))
        event(con,args.id,'RECORD_AMENDED',r['status'],r['status'],note,
              {'field':'paths','before':before,'after':paths,'reason':note})
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{args.id}: allowed paths amended {", ".join(before) or "(none)"} -> {", ".join(paths)}')

def find_task_resource_lock(con, task_id, reference):
    """Resolve `--resource` as a lock_id or a resource_id held by this task."""
    if '#' in reference:
        row=resource_row(con,reference)
        if row['task_id']!=task_id:
            raise ResourceError('LOCK_NOT_HELD',f'{reference} belongs to task {row["task_id"]}, not {task_id}')
        return row
    resource_id=normalize_resource_id(reference)
    rows=[row for row in held_resource_rows(con,task_id) if row['resource_id']==resource_id]
    if not rows:
        raise ResourceError('LOCK_NOT_HELD',f'task {task_id} holds no non-RELEASED lock on {resource_id}')
    return rows[0]

def unlock(root, args, state_root=None):
    require_local_scheduler(root,'task unlock',state_root=state_root)
    con=connect(root, state_root)
    resource=getattr(args,'resource',None)
    try:
        con.execute('BEGIN IMMEDIATE'); r=get_task(con,args.id)
        if r['status'] not in TERMINAL and not args.force:
            raise RuntimeError('task is not terminal; verify that its execution has stopped, then use --force with a reason if unlock is truly required')
        if resource:
            row=find_task_resource_lock(con,args.id,resource)
            evidence=getattr(args,'evidence',None) or '(none)'
            authorized_by=getattr(args,'authorized_by',None)
            if args.force:
                force_unlock_resource(con,row['lock_id'],args.reason,authorized_by or default_holder(),
                                      evidence,authorized_by=authorized_by)
                outcome='forced'
            else:
                release_reason='CANCELED' if r['status']=='CANCELED' else 'NORMAL_COMPLETE'
                release_resource_set(con,args.id,release_reason,args.reason,'orchestrator',evidence,rows=[row])
                outcome='released'
            event(con,args.id,'MANUAL_UNLOCK',r['status'],r['status'],args.reason,
                  {'resource':row['resource_id'],'mode':row['mode'],'lock_id':row['lock_id'],
                   'outcome':outcome,'force':bool(args.force),'authorized_by':authorized_by})
            con.execute('COMMIT')
            print(f'{args.id}: resource lock {row["resource_id"]} ({row["mode"]}) {outcome}; reason recorded')
            return
        rows=con.execute('SELECT path FROM locks WHERE task_id=?',(args.id,)).fetchall()
        con.execute('DELETE FROM locks WHERE task_id=?',(args.id,))
        event(con,args.id,'MANUAL_UNLOCK',r['status'],r['status'],args.reason,{'released_paths':[x['path'] for x in rows],'force':args.force})
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{args.id}: released {len(rows)} path lock(s); reason recorded')

def lock_line(row, now_stamp=None):
    """One inspection line: id, mode, state, lease, holder, blocking effect (§4.3)."""
    overdue=''
    if row['state']=='ACTIVE' and now_stamp and row['lease_until']<=now_stamp:
        overdue=' lease OVERDUE (expiry pending)'
    line=(f"{row['resource_id']}\t{row['mode']}\t{row['state']}\tlease_until={row['lease_until']}\t"
          f"renewed_at={row['renewed_at']}\tholder={row['holder']}\ttask={row['task_id']}\t"
          f"backend={row['backend']}\tlock_id={row['lock_id']}{overdue}")
    if row['state']!='RELEASED':
        line+=('\tblocks=exclusive+shared' if row['mode']=='exclusive' else '\tblocks=exclusive')
    return line

def task_resources(root, args, state_root=None):
    """Inspect resource locks: every non-RELEASED row plus its blocking reason."""
    con=connect(root, state_root)
    edition=None
    try:
        edition=read_project_edition(root, isolated=state_root is not None)
    except EditionError:
        edition=None                     # an unreadable edition must not hide the listing
    try:
        if edition=='local':
            # Expiring is a state transition, not a release; only the local edition
            # owns this database and may write it.
            con.execute('BEGIN IMMEDIATE'); expire_due_locks(con); con.execute('COMMIT')
        stamp=now()
        rows=con.execute("SELECT * FROM resource_locks WHERE state<>'RELEASED' ORDER BY resource_id, mode, task_id"
                         ).fetchall()
        if getattr(args,'all',False):
            rows=con.execute('SELECT * FROM resource_locks ORDER BY resource_id, mode, task_id').fetchall()
        if getattr(args,'resource',None):
            resource_id=normalize_resource_id(args.resource)
            rows=[row for row in rows if row['resource_id']==resource_id]
        if not rows:
            print('No resource locks held.')
            print(resource_backend_note(edition or 'local') if edition else
                  'the project edition cannot be read; this listing is read-only and no lease was migrated.')
            return
        print('resource_id\tmode\tstate\tlease\tholder\ttask\tbackend\tlock_id\tblocking')
        for row in rows:
            print(lock_line(row,stamp))
        print(f'Total: {len(rows)} row(s); generated with the frozen contract docs/design/RESOURCE-LOCKS.md.')
        print(resource_backend_note(edition or 'local') if edition else
              'the project edition cannot be read; this listing is read-only and no lease was migrated.')
    finally: con.close()

def task_renew(root, args, state_root=None):
    require_local_scheduler(root,'task renew',state_root=state_root)
    holder=validate_holder(getattr(args,'holder',None) or default_holder())
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE')
        ttl=resource_config(root,require_ttl=True,state_root=state_root)[LEASE_TTL_KEY]
        renewed=renew_resource_locks(con,args.id,holder,ttl,getattr(args,'reason','') or 'periodic renewal',
                                     getattr(args,'evidence',None) or '(none)',actor=holder)
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{args.id}: renewed {len(renewed)} resource lock(s) for {ttl}s as holder {holder}')

def task_reclaim(root, args, state_root=None):
    require_local_scheduler(root,'task reclaim',state_root=state_root)
    grace=resource_config(root,require_grace=True,state_root=state_root)[LEASE_GRACE_KEY]
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE'); expire_due_locks(con)
        row=find_task_resource_lock(con,args.id,args.resource)
        reclaim_resource_lock(con,row['lock_id'],args.reason,getattr(args,'evidence',None) or '(none)',grace,
                              actor=getattr(args,'actor',None) or 'orchestrator')
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    print(f'{args.id}: reclaimed {row["resource_id"]} ({row["mode"]}) after a {grace}s cool-down; evidence recorded')

def task_reconcile(root, args, state_root=None):
    """Rebuild lock facts without trusting memory; quarantine what cannot be proven."""
    require_local_scheduler(root,'task reconcile',state_root=state_root)
    con=connect(root, state_root)
    try:
        con.execute('BEGIN IMMEDIATE')
        report=reconcile_resource_locks(con,getattr(args,'reason','cross-session recovery'),
                                        getattr(args,'evidence',None) or '(none)')
        con.execute('COMMIT')
    except Exception:
        safe_rollback(con); raise
    finally: con.close()
    if not report:
        print('No non-RELEASED resource locks to reconcile.')
        return
    print('lock_id\tverdict\taction')
    for item in report:
        print(f"{item['lock_id']}\t{item['verdict']}\t{item['action']}")
    print(f'Reconciled {len(report)} lock(s); verdicts come from the lock table and its audit events only.')

# --- named resource locks (contract: docs/design/RESOURCE-LOCKS.md, frozen v1) ---
# The contract owns the semantics: data model (§2), conflict matrix (§3), atomic
# set acquisition (§4), state machine (§5), recovery (§6), backend boundaries (§7).
# This section only implements it. Error codes below are reported verbatim so a
# caller can act on the outcome instead of parsing prose.
RESOURCE_MODES = ('shared', 'exclusive')
RESOURCE_STATES = ('ACTIVE', 'EXPIRED', 'QUARANTINED', 'RELEASED')
RESOURCE_RELEASE_REASONS = ('NORMAL_COMPLETE', 'CANCELED', 'FORCED_UNLOCK', 'LEASE_RECLAIMED',
                            'TRANSFER', 'MIGRATION_DROP')
RESOURCE_EVENT_TYPES = (
    'LOCK_REQUESTED', 'LOCK_GRANTED', 'LOCK_DENIED_CONFLICT', 'LOCK_SET_ROLLED_BACK',
    'LOCK_RENEWED', 'LOCK_EXPIRED', 'LOCK_RECLAIMED', 'LOCK_RELEASED', 'LOCK_FORCED',
    'LOCK_QUARANTINED', 'LOCK_ADOPTED', 'LOCK_TRANSITION_REJECTED', 'RESOURCE_REGISTERED',
)
# RELEASED is the only terminal state; a released record is never revived, a
# re-acquisition uses a new generation (§5.1).
RESOURCE_TRANSITIONS = {
    'ACTIVE': {'ACTIVE', 'EXPIRED', 'RELEASED', 'QUARANTINED'},
    'EXPIRED': {'ACTIVE', 'RELEASED', 'QUARANTINED'},
    'QUARANTINED': {'ACTIVE', 'RELEASED'},
    'RELEASED': set(),
}
RESOURCE_TYPE_RE = re.compile(r'[a-z][a-z0-9-]{0,15}')
RESOURCE_NAME_RE = re.compile(r'[a-z0-9][a-z0-9._/-]{0,127}')
# Characters that would silently turn one resource into several / a pattern.
# They are rejected rather than rewritten (§2.1 rule 1).
RESOURCE_REJECT_CHARS = (' ', '\t', '\r', '\n', '\\', '?', '*', '#', '[')
# A holder must be locatable; generic role names are refused (§2.2, §6.3).
HOLDER_GENERIC = {'', 'agent', 'subagent', 'assistant', 'ai', 'unknown', 'none', '(none)', 'n/a'}
LEASE_TTL_KEY = 'resource_lease_ttl'
LEASE_GRACE_KEY = 'resource_reclaim_grace'
LEASE_TTL_MAX = 604800          # 7 days: a lease longer than this is not a lease.
LEASE_GRACE_MAX = 86400         # 1 day of reclaim cool-down is already generous.
RESOURCE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS resource_locks(
  lock_id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('shared','exclusive')),
  holder TEXT NOT NULL,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  acquired_at TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  renewed_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('ACTIVE','EXPIRED','QUARANTINED','RELEASED')),
  release_reason TEXT NOT NULL DEFAULT '(none)',
  released_at TEXT NOT NULL DEFAULT '(none)',
  reason TEXT NOT NULL,
  evidence_ref TEXT NOT NULL,
  backend TEXT NOT NULL CHECK(backend IN ('local','github')),
  generation INTEGER NOT NULL DEFAULT 0,
  UNIQUE(resource_id, task_id, mode, generation)
);
CREATE UNIQUE INDEX IF NOT EXISTS resource_locks_single_exclusive
  ON resource_locks(resource_id) WHERE mode='exclusive' AND state<>'RELEASED';
CREATE INDEX IF NOT EXISTS resource_locks_resource_idx ON resource_locks(resource_id, state);
CREATE INDEX IF NOT EXISTS resource_locks_task_idx2 ON resource_locks(task_id, state);
CREATE INDEX IF NOT EXISTS resource_locks_lease_idx ON resource_locks(lease_until);
CREATE TABLE IF NOT EXISTS resource_lock_events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  task_id TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  prev_state TEXT NOT NULL,
  new_state TEXT NOT NULL,
  request_fingerprint TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence_ref TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS resource_lock_events_task_idx ON resource_lock_events(task_id, event_id);
CREATE INDEX IF NOT EXISTS resource_lock_events_resource_idx ON resource_lock_events(resource_id, event_id);
'''

class ResourceError(RuntimeError):
    """A resource-lock operation was refused; `code` is the machine-readable reason."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')

class ResourceDenied(ResourceError):
    """Refusal raised *after* the denial audit trail was written in the same transaction.

    Callers must COMMIT (never roll back) so the LOCK_DENIED_CONFLICT /
    LOCK_SET_ROLLED_BACK / quarantine evidence survives, while resource_locks
    stays净空 because no lock row is written before every entry passed (§4.4).
    """

def _ts(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds')

def add_seconds(stamp, seconds):
    return _ts(datetime.fromisoformat(stamp) + timedelta(seconds=int(seconds)))

def ensure_resource_schema(con):
    """Create the resource tables incrementally; an old database keeps working.

    Called from connect()/init() only, i.e. outside an explicit transaction:
    `executescript` would implicitly commit the caller's transaction. A missing
    table must never be read as "no conflict" (§8.2 rule 5), so this runs before
    any lock judgement.
    """
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'tasks' not in tables:
        return
    columns = {row[1] for row in con.execute('PRAGMA table_info(tasks)')}
    need_tables = not {'resource_locks', 'resource_lock_events'} <= tables
    need_column = 'resources_json' not in columns
    if not (need_tables or need_column):
        return
    if need_tables:
        con.executescript(RESOURCE_SCHEMA)
    if need_column:
        con.execute("ALTER TABLE tasks ADD COLUMN resources_json TEXT NOT NULL DEFAULT '[]'")

def normalize_resource_id(raw):
    """Return the canonical `<type>:<name>` form or raise RESOURCE_ID_INVALID.

    Rules are applied to the raw string, never to a cleaned-up one: silently
    lowering case or trimming spaces would create two IDs for one resource.
    """
    if not isinstance(raw, str) or not raw:
        raise ResourceError('RESOURCE_ID_INVALID', f'resource ID must be a non-empty string, got {raw!r}')
    text = unicodedata.normalize('NFC', raw)
    if text != text.strip():
        raise ResourceError('RESOURCE_ID_INVALID', f'resource ID {raw!r} has leading/trailing whitespace; write it without spaces')
    if text != text.lower():
        raise ResourceError('RESOURCE_ID_INVALID', f'resource ID {raw!r} contains uppercase characters; only lowercase is accepted')
    for char in RESOURCE_REJECT_CHARS:
        if char in text:
            raise ResourceError('RESOURCE_ID_INVALID', f'resource ID {raw!r} contains forbidden character {char!r}')
    res_type, sep, name = text.partition(':')
    if not sep:
        raise ResourceError('RESOURCE_ID_INVALID', f'resource ID {raw!r} must look like <type>:<name> (for example db:test/mysql-main)')
    if not RESOURCE_TYPE_RE.fullmatch(res_type):
        raise ResourceError('RESOURCE_ID_INVALID', f'resource type {res_type!r} in {raw!r} must match {RESOURCE_TYPE_RE.pattern}')
    if not RESOURCE_NAME_RE.fullmatch(name):
        raise ResourceError('RESOURCE_ID_INVALID', f'resource name {name!r} in {raw!r} must match {RESOURCE_NAME_RE.pattern}')
    parts = name.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ResourceError('RESOURCE_ID_INVALID', f'resource name {name!r} in {raw!r} has an empty or dots-only path segment')
    return text

def parse_resource_spec(spec):
    """Parse `<resource_id>:<mode>[:<ttl>]`; the ID itself may contain ':'."""
    if not isinstance(spec, str) or not spec.strip():
        raise ResourceError('RESOURCE_SPEC_INVALID', f'empty resource declaration; expected <resource_id>:<mode>[:<ttl>]')
    parts = spec.split(':')
    ttl = None
    if len(parts) >= 3 and parts[-2] in RESOURCE_MODES and parts[-1].isdigit():
        ttl = int(parts[-1]); parts = parts[:-1]
    if len(parts) < 2 or parts[-1] not in RESOURCE_MODES:
        raise ResourceError('RESOURCE_SPEC_INVALID',
                            f'resource declaration {spec!r} must be <resource_id>:<mode>[:<ttl>] with mode shared or exclusive')
    mode = parts[-1]
    entry = {'resource_id': normalize_resource_id(':'.join(parts[:-1])), 'mode': mode, 'ttl': ttl}
    if ttl is not None and not 1 <= ttl <= LEASE_TTL_MAX:
        raise ResourceError('RESOURCE_CONFIG_INVALID', f'lease ttl {ttl} in {spec!r} must be between 1 and {LEASE_TTL_MAX} seconds')
    return entry

def parse_resource_specs(specs):
    """Normalise a declaration list into one entry per resource_id (§4.1)."""
    entries, seen = [], {}
    for spec in specs or []:
        entry = parse_resource_spec(spec)
        rid = entry['resource_id']
        if rid in seen:
            if seen[rid] != entry['mode']:
                raise ResourceError('RESOURCE_SPEC_INVALID',
                                    f'resource {rid} is declared twice with different modes ({seen[rid]} and {entry["mode"]})')
            continue
        seen[rid] = entry['mode']
        entries.append(entry)
    return entries

def resource_config(root, require_ttl=False, require_grace=False, state_root=None):
    """Read resource_lease_ttl / resource_reclaim_grace from .orchestration/config.json.

    Both are explicit configuration: there is no implicit default (§6.2). A task
    that declares no resource never reads them, so existing roots are untouched.
    """
    data = {}
    try:
        raw = json.loads(config_path(root, state_root).read_text(encoding='utf-8'))
        if isinstance(raw, dict):
            data = raw
    except FileNotFoundError:
        pass
    except Exception:
        data = {}
    cfg, problems = {}, []
    isolated = state_root is not None
    shown = _display_path(root, config_path(root, state_root), isolated)
    try: state_rel = state_dir(root, state_root).relative_to(Path(root)).as_posix()
    except ValueError: state_rel = _display_path(root, state_dir(root, state_root), True)
    for key, low, high, required in (
            (LEASE_TTL_KEY, 1, LEASE_TTL_MAX, require_ttl),
            (LEASE_GRACE_KEY, 0, LEASE_GRACE_MAX, require_grace)):
        value = data.get(key)
        valid = isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
        if valid:
            cfg[key] = value
        elif required:
            problems.append(f'{key} must be an integer between {low} and {high} in {shown}')
        else:
            cfg[key] = None
    if problems:
        raise ResourceError('RESOURCE_CONFIG_INVALID',
                            'resource lock configuration is missing or illegal: ' + '; '.join(problems) +
                            f'; add it to {state_rel}/config.json (a lease without an explicit ttl or cool-down is refused)')
    return cfg

def resolve_resource_entries(root, specs, state_root=None):
    """Validate specs and fill every ttl, sorted by UTF-8 byte order (§4.2)."""
    entries = parse_resource_specs(specs)
    if not entries:
        return []
    cfg = resource_config(root, require_ttl=any(e['ttl'] is None for e in entries),
                          state_root=state_root)
    for entry in entries:
        entry['ttl'] = entry['ttl'] if entry['ttl'] is not None else cfg[LEASE_TTL_KEY]
    return order_resource_entries(entries)

def order_resource_entries(entries):
    """Deterministic order: UTF-8 byte order of resource_id (§4.2)."""
    return sorted(entries, key=lambda e: (e['resource_id'].encode('utf-8'), e['mode']))

def resource_fingerprint(entries):
    """SHA-256 of the normalised, sorted `resource_id:mode` set (§2.3)."""
    payload = '\n'.join(f'{e["resource_id"]}:{e["mode"]}' for e in order_resource_entries(entries))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def validate_holder(holder):
    text = (holder or '').strip()
    if not text or text.lower() in HOLDER_GENERIC:
        raise ResourceError('HOLDER_INVALID',
                            f'holder {holder!r} cannot be located; pass a runtime instance ID, session ID or operator ID '
                            '(generic role names such as "agent" are refused)')
    return text

def resource_event(con, event_type, task_id, resource_id, mode, prev_state, new_state,
                   fingerprint, reason, evidence_ref, actor, at, event_id=None):
    """Append one immutable audit event; a repeated event_id is a no-op (§2.3)."""
    if event_type not in RESOURCE_EVENT_TYPES:
        raise ResourceError('RESOURCE_EVENT_INVALID', f'unregistered resource lock event type: {event_type}')
    if not (actor or '').strip():
        raise ResourceError('RESOURCE_EVENT_INVALID', f'{event_type} for {resource_id} requires a non-empty actor')
    if event_id is not None:
        existing = con.execute('SELECT * FROM resource_lock_events WHERE event_id=?', (event_id,)).fetchone()
        if existing:
            return existing['event_id']
        con.execute('INSERT INTO resource_lock_events(event_id,event_type,at,actor,task_id,resource_id,mode,'
                    'prev_state,new_state,request_fingerprint,reason,evidence_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (event_id, event_type, at, actor, task_id, resource_id, mode, prev_state, new_state,
                     fingerprint, reason, evidence_ref))
        return event_id
    cursor = con.execute('INSERT INTO resource_lock_events(event_type,at,actor,task_id,resource_id,mode,'
                         'prev_state,new_state,request_fingerprint,reason,evidence_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (event_type, at, actor, task_id, resource_id, mode, prev_state, new_state,
                          fingerprint, reason, evidence_ref))
    return cursor.lastrowid

def declared_resources(task_row):
    raw = task_row['resources_json'] if 'resources_json' in task_row.keys() else '[]'
    try:
        return list(json.loads(raw or '[]'))
    except Exception:
        raise ResourceError('RESOURCE_SPEC_INVALID', f'task {task_row["id"]} has unreadable declared_resources {raw!r}')

def held_resource_rows(con, task_id):
    return con.execute("SELECT * FROM resource_locks WHERE task_id=? AND state<>'RELEASED' "
                       "ORDER BY resource_id, mode", (task_id,)).fetchall()

def resource_row(con, lock_id):
    row = con.execute('SELECT * FROM resource_locks WHERE lock_id=?', (lock_id,)).fetchone()
    if not row:
        raise ResourceError('LOCK_NOT_FOUND', f'unknown resource lock: {lock_id}')
    return row

def transition_resource(con, row, new_state, event_type, actor, reason, evidence_ref, at,
                        fingerprint='', **updates):
    """Apply one whitelisted state transition and its audit event (§5.1)."""
    prev = row['state']
    if new_state not in RESOURCE_TRANSITIONS.get(prev, set()):
        resource_event(con, 'LOCK_TRANSITION_REJECTED', row['task_id'], row['resource_id'], row['mode'],
                       prev, new_state, fingerprint, f'{prev} -> {new_state} is not an allowed lock transition: {reason}',
                       evidence_ref, actor, at)
        raise ResourceError('TRANSITION_REJECTED',
                            f'{row["lock_id"]}: {prev} -> {new_state} is not allowed; the lock record is kept unchanged')
    assignments = {'state': new_state, 'reason': reason, 'evidence_ref': evidence_ref}
    assignments.update(updates)
    columns = ', '.join(f'{name}=?' for name in assignments)
    con.execute(f'UPDATE resource_locks SET {columns} WHERE lock_id=?',
                (*assignments.values(), row['lock_id']))
    resource_event(con, event_type, row['task_id'], row['resource_id'], row['mode'], prev, new_state,
                   fingerprint, reason, evidence_ref, actor, at)
    return new_state

def expire_due_locks(con, at=None, actor='orchestrator'):
    """ACTIVE -> EXPIRED for every lease that passed; expiry is not a release (§6.2)."""
    at = at or now()
    rows = con.execute("SELECT * FROM resource_locks WHERE state='ACTIVE' AND lease_until<=? "
                       "ORDER BY resource_id, task_id", (at,)).fetchall()
    expired = []
    for row in rows:
        transition_resource(con, row, 'EXPIRED', 'LOCK_EXPIRED', actor,
                            f'lease_until {row["lease_until"]} passed without renewal; the lock stays held and still conflicts',
                            row['evidence_ref'], at)
        expired.append(row['lock_id'])
    return expired

def conflicting_resource_row(con, resource_id, mode, task_id):
    """First non-RELEASED row blocking (resource_id, mode) for task_id (§3.1)."""
    for row in con.execute("SELECT * FROM resource_locks WHERE resource_id=? AND state<>'RELEASED' "
                           "AND task_id<>? ORDER BY task_id, mode", (resource_id, task_id)):
        if mode == 'exclusive' or row['mode'] == 'exclusive':
            return row
    return None

def _conflict_detail(row):
    return (f'resource {row["resource_id"]} is held by task {row["task_id"]} '
            f'(holder {row["holder"]}, mode {row["mode"]}, state {row["state"]}, lease_until {row["lease_until"]}); '
            'next: wait for that release, declare a different resource, or serialize the two tasks')

def _deny(con, task_id, entry, fingerprint, actor, at, evidence_ref, code, detail, attempted, total):
    """Write the denial evidence, then raise; caller COMMITs so it survives."""
    resource_event(con, 'LOCK_DENIED_CONFLICT', task_id, entry['resource_id'], entry['mode'],
                   '(none)', '(none)', fingerprint, f'DENIED {code}: {detail}', evidence_ref, actor, at)
    resource_event(con, 'LOCK_SET_ROLLED_BACK', task_id, entry['resource_id'], entry['mode'],
                   '(none)', '(none)', fingerprint,
                   f'whole set refused after {attempted} of {total} resource(s); no lock row was written for task {task_id}',
                   evidence_ref, actor, at)
    raise ResourceDenied(code, detail)

def quarantine_resource_row(con, row, actor, reason, evidence_ref, at, fingerprint=''):
    if row['state'] == 'QUARANTINED':
        return row['state']
    return transition_resource(con, row, 'QUARANTINED', 'LOCK_QUARANTINED', actor, reason, evidence_ref,
                               at, fingerprint)

def acquire_resource_set(con, task_id, entries, holder, actor='orchestrator', reason='dispatch',
                         evidence_ref='(none)', backend='local', at=None):
    """Atomically acquire one declared resource set; caller owns the transaction.

    Conflict reads and lock writes share the caller's BEGIN IMMEDIATE transaction
    (§3.2). No row is inserted before *every* entry passed the matrix, so a
    denial leaves resource_locks净空 while its events are committed (§4.4).
    """
    at = at or now()
    holder = validate_holder(holder)
    expire_due_locks(con, at)
    ordered = order_resource_entries(entries)
    fingerprint = resource_fingerprint(ordered)
    held = held_resource_rows(con, task_id)
    if held:
        holders = sorted({row['holder'] for row in held})
        if holder not in holders:
            for row in held:
                quarantine_resource_row(con, row, actor,
                                        f'duplicate ownership: task {task_id} already holds {row["resource_id"]} as '
                                        f'holder {row["holder"]}; new holder {holder} must not write (§6.3)',
                                        evidence_ref, at, fingerprint)
            raise ResourceDenied('DUPLICATE_OWNERSHIP',
                                 f'task {task_id} already holds resource locks as holder {", ".join(holders)}; '
                                 f'holder {holder} cannot continue; resolve the duplicate owner manually')
        wanted = {entry['resource_id']: entry for entry in ordered}
        missing = [entry for entry in ordered if entry['resource_id'] not in {r['resource_id'] for r in held}]
        dropped = sorted({row['resource_id'] for row in held} - set(wanted))
        mode_changed = sorted({row['resource_id'] for row in held
                               if row['resource_id'] in wanted and wanted[row['resource_id']]['mode'] != row['mode']})
        upgrade = sorted({row['resource_id'] for row in held
                          if row['resource_id'] in wanted and row['mode'] == 'shared'
                          and wanted[row['resource_id']]['mode'] == 'exclusive'})
        if upgrade:
            entry = wanted[upgrade[0]]
            _deny(con, task_id, entry, fingerprint, actor, at, evidence_ref, 'UPGRADE_FORBIDDEN',
                  f'{entry["resource_id"]} is already held as shared; in-place upgrade to exclusive is forbidden — '
                  'release the set and request it again as a whole', 1, len(ordered))
        if dropped or mode_changed:
            bad = dropped + mode_changed
            declared_text = '; '.join(f'{e["resource_id"]}:{e["mode"]}' for e in ordered) or '(empty)'
            for row in held:
                quarantine_resource_row(con, row, actor,
                                        f'LOCK_SET_MISMATCH: task {task_id} already holds '
                                        f'{row["resource_id"]}:{row["mode"]} but the new declaration is {declared_text}',
                                        evidence_ref, at, fingerprint)
            raise ResourceDenied('LOCK_SET_MISMATCH',
                                 f'task {task_id} already holds a different resource set (differs on {", ".join(bad)}); '
                                 'the held locks are now QUARANTINED and need a human decision')
        if not missing:
            for entry in ordered:
                row = next(r for r in held
                           if r['resource_id'] == entry['resource_id'] and r['mode'] == entry['mode'])
                if row['state'] != 'ACTIVE':
                    raise ResourceDenied('LOCK_NOT_HELD',
                                         f'{row["lock_id"]} is {row["state"]}, not ACTIVE; renew it or resolve the '
                                         'quarantine before dispatching this task again')
                resource_event(con, 'LOCK_GRANTED', task_id, entry['resource_id'], entry['mode'],
                               'ACTIVE', 'ACTIVE', fingerprint, f'ALREADY_HELD: {reason}',
                               evidence_ref, actor, at)
            return {'status': 'ALREADY_HELD', 'fingerprint': fingerprint, 'entries': ordered,
                    'lock_ids': sorted(row['lock_id'] for row in held)}
        ordered = missing
    granted = []
    for index, entry in enumerate(ordered, 1):
        resource_event(con, 'LOCK_REQUESTED', task_id, entry['resource_id'], entry['mode'], '(none)', 'ACTIVE',
                       fingerprint, f'requested {reason}', evidence_ref, actor, at)
        blocker = conflicting_resource_row(con, entry['resource_id'], entry['mode'], task_id)
        if blocker:
            _deny(con, task_id, entry, fingerprint, actor, at, evidence_ref, 'MODE_CONFLICT',
                  f'{entry["resource_id"]}:{entry["mode"]} conflicts with {_conflict_detail(blocker)}',
                  index, len(ordered))
        granted.append(entry)
    lock_ids = []
    for entry in granted:
        previous = con.execute('SELECT MAX(generation) FROM resource_locks WHERE resource_id=? AND task_id=? AND mode=?',
                               (entry['resource_id'], task_id, entry['mode'])).fetchone()[0]
        generation = 0 if previous is None else int(previous) + 1
        lock_id = f'{entry["resource_id"]}#{entry["mode"]}#{task_id}#{generation}'
        lease_until = add_seconds(at, entry['ttl'])
        con.execute('INSERT INTO resource_locks(lock_id,resource_id,mode,holder,task_id,acquired_at,lease_until,'
                    'renewed_at,state,release_reason,released_at,reason,evidence_ref,backend,generation) '
                    "VALUES(?,?,?,?,?,?,?,?,'ACTIVE','(none)','(none)',?,?,?,?)",
                    (lock_id, entry['resource_id'], entry['mode'], holder, task_id, at, lease_until, at,
                     reason, evidence_ref, backend, generation))
        resource_event(con, 'LOCK_GRANTED', task_id, entry['resource_id'], entry['mode'], '(none)', 'ACTIVE',
                       fingerprint, reason, evidence_ref, actor, at)
        lock_ids.append(lock_id)
    return {'status': 'GRANTED', 'fingerprint': fingerprint, 'entries': granted, 'lock_ids': lock_ids}

def release_resource_set(con, task_id, release_reason, reason, actor, evidence_ref, at=None, rows=None):
    """Release a whole set in deterministic order; one failure does not skip the rest (§4.5)."""
    if release_reason not in RESOURCE_RELEASE_REASONS:
        raise ResourceError('RELEASE_REASON_INVALID', f'release reason {release_reason!r} is not in {list(RESOURCE_RELEASE_REASONS)}')
    at = at or now()
    targets = list(rows) if rows is not None else list(held_resource_rows(con, task_id))
    released, problems = [], []
    for row in targets:
        row = resource_row(con, row['lock_id'])
        if row['state'] == 'RELEASED':
            resource_event(con, 'LOCK_TRANSITION_REJECTED', row['task_id'], row['resource_id'], row['mode'],
                           'RELEASED', 'RELEASED', '', f'ALREADY_RELEASED: {reason}', evidence_ref, actor, at)
            problems.append(f'{row["lock_id"]}: ALREADY_RELEASED (no new state transition)')
            continue
        try:
            transition_resource(con, row, 'RELEASED', 'LOCK_RELEASED', actor, reason, evidence_ref, at,
                                release_reason=release_reason, released_at=at)
            released.append(row['lock_id'])
        except Exception as exc:                     # keep going: report per item, then aggregate
            problems.append(f'{row["lock_id"]}: {exc}')
    if problems:
        raise ResourceError('RELEASE_INCOMPLETE', 'release was not fully applied: ' + '; '.join(problems))
    return released

def force_unlock_resource(con, lock_id, reason, actor, evidence_ref, authorized_by=None, at=None):
    """Manual override: reason + authorisation + audit; skips conflict checks (§6.4)."""
    at = at or now()
    if not (reason or '').strip():
        raise ResourceError('REASON_REQUIRED', 'forced unlock requires --reason explaining why the holder has stopped')
    if not (evidence_ref or '').strip():
        raise ResourceError('EVIDENCE_REQUIRED', 'forced unlock requires a locatable --evidence reference')
    row = resource_row(con, lock_id)
    task = con.execute('SELECT status FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
    status = task['status'] if task else '(unknown)'
    if status not in TERMINAL and not (authorized_by or '').strip():
        raise ResourceError('AUTHORIZATION_REQUIRED',
                            f'task {row["task_id"]} is {status}, not terminal; forced unlock needs --authorized-by '
                            '(the person or role taking responsibility), or wait until the task is VERIFIED/CANCELED')
    whoever = (authorized_by or actor or '').strip()
    if not whoever:
        raise ResourceError('AUTHORIZATION_REQUIRED', 'forced unlock requires a locatable authoriser')
    transition_resource(con, row, 'RELEASED', 'LOCK_FORCED', whoever,
                        f'FORCED_UNLOCK by {whoever}: {reason}', evidence_ref, at, release_reason='FORCED_UNLOCK',
                        released_at=at)
    return row['lock_id']

def reclaim_resource_lock(con, lock_id, reason, evidence_ref, grace, actor='orchestrator', at=None):
    """EXPIRED -> RELEASED with terminal evidence and after the cool-down (§6.2)."""
    at = at or now()
    if not (reason or '').strip():
        raise ResourceError('REASON_REQUIRED', 'reclaim requires --reason stating the terminal evidence')
    if not (evidence_ref or '').strip():
        raise ResourceError('EVIDENCE_REQUIRED', 'reclaim requires a locatable --evidence reference')
    row = resource_row(con, lock_id)
    if row['state'] != 'EXPIRED':
        raise ResourceError('NOT_EXPIRED', f'{lock_id} is {row["state"]}; only an EXPIRED lock can be reclaimed '
                                           '(a live lease is never reclaimed)')
    ready_at = add_seconds(row['lease_until'], int(grace))
    if add_seconds(at, 0) < ready_at:
        raise ResourceError('GRACE_NOT_ELAPSED',
                            f'{lock_id} can be reclaimed at {ready_at} at the earliest (lease_until {row["lease_until"]} '
                            f'+ cool-down {grace}s); renew or raise a forced unlock instead')
    transition_resource(con, row, 'RELEASED', 'LOCK_RECLAIMED', actor, f'LEASE_RECLAIMED: {reason}',
                        evidence_ref, at, release_reason='LEASE_RECLAIMED', released_at=at)
    return lock_id

def renew_resource_locks(con, task_id, holder, ttl, reason, evidence_ref, actor=None, at=None):
    """Extend own lease only; a released/foreign lock yields LOCK_NOT_HELD (§5.2, §6.4)."""
    at = at or now()
    holder = validate_holder(holder)
    rows = [row for row in held_resource_rows(con, task_id) if row['state'] in ('ACTIVE', 'EXPIRED')]
    if not rows:
        raise ResourceError('LOCK_NOT_HELD', f'task {task_id} holds no ACTIVE or EXPIRED resource lock; '
                                            'nothing can be renewed (a forced unlock keeps this true)')
    for row in rows:
        if row['holder'] != holder:
            raise ResourceError('LOCK_NOT_HELD', f'{row["lock_id"]} is held by {row["holder"]}, not {holder}; '
                                                'a lock is only renewed by its own holder')
        transition_resource(con, row, 'ACTIVE', 'LOCK_RENEWED', actor or holder,
                            f'renewed: {reason}' if reason else 'renewed', evidence_ref, at,
                            renewed_at=at, lease_until=add_seconds(at, int(ttl)))
    return [row['lock_id'] for row in rows]

def reconcile_resource_locks(con, reason, evidence_ref, actor='orchestrator', at=None):
    """Rebuild the facts from the store, never from memory (§6.6).

    Classifies every non-RELEASED lock; a record without acquisition evidence,
    with an unlocatable holder, or belonging to a task in NEEDS_HUMAN_DECISION is
    quarantined instead of being trusted or treated as free.

    The evidence count is snapshotted *before* the expiry sweep: an EXPIRED event
    written by this very pass must not be able to justify a holding that has no
    acquisition event at all.
    """
    at = at or now()
    row_ids = [row['lock_id'] for row in con.execute(
        "SELECT lock_id FROM resource_locks WHERE state<>'RELEASED' ORDER BY resource_id, task_id")]
    evidence = {}
    for lock_id in row_ids:
        row = con.execute('SELECT * FROM resource_locks WHERE lock_id=?', (lock_id,)).fetchone()
        evidence[lock_id] = con.execute(
            "SELECT COUNT(*) FROM resource_lock_events WHERE task_id=? AND resource_id=? AND mode=? "
            "AND event_type IN ('LOCK_GRANTED','LOCK_ADOPTED')",
            (row['task_id'], row['resource_id'], row['mode'])).fetchone()[0]
    expire_due_locks(con, at, actor)
    report = []
    for lock_id in row_ids:
        row = con.execute('SELECT * FROM resource_locks WHERE lock_id=?', (lock_id,)).fetchone()
        events = evidence[lock_id]
        task = con.execute('SELECT status FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
        status = task['status'] if task else '(unknown)'
        holder = (row['holder'] or '').strip()
        verdict, action = 'valid', 'kept as held; read-only for a new session'
        if not events:
            verdict, action = 'missing-evidence', 'quarantined: lock rows without events are not evidence'
        elif holder.lower() in HOLDER_GENERIC:
            verdict, action = 'unlocatable-holder', 'quarantined: the holder cannot be located'
        elif status == 'NEEDS_HUMAN_DECISION':
            verdict, action = 'human-decision', 'quarantined: task awaits a human decision'
        elif row['state'] == 'EXPIRED':
            verdict, action = 'expired', 'kept EXPIRED: reclaim after the cool-down or force with a reason'
        elif row['state'] == 'QUARANTINED':
            verdict, action = 'quarantined', 'kept quarantined until a human decides'
        else:
            heartbeat = datetime.fromisoformat(at) - datetime.fromisoformat(row['renewed_at'])
            lease = datetime.fromisoformat(row['lease_until']) - datetime.fromisoformat(row['acquired_at'])
            if lease.total_seconds() > 0 and heartbeat.total_seconds() > lease.total_seconds() / 2:
                verdict, action = 'stale-heartbeat', 'kept as held but flagged: renew before the lease runs out'
        if verdict in ('missing-evidence', 'unlocatable-holder', 'human-decision'):
            quarantine_resource_row(con, row, actor, f'{reason}: {verdict}', evidence_ref, at)
        report.append({'lock_id': row['lock_id'], 'resource_id': row['resource_id'], 'mode': row['mode'],
                       'state': row['state'], 'holder': row['holder'], 'lease_until': row['lease_until'],
                       'task_id': row['task_id'], 'task_status': status, 'events': events,
                       'verdict': verdict, 'action': action})
    return report

def resource_backend_note(backend='local'):
    """State the boundary honestly; `github` registration is advisory only (§7)."""
    if backend == 'github':
        return ('github stores resource claims as an advisory registration in the Issue ORCH-TASK-REGISTRY '
                'block; it is a coordination record, not an atomic lock, and two concurrent writers can '
                'overwrite each other (TOCTOU).')
    return ('local acquires named resource locks atomically inside one SQLite BEGIN IMMEDIATE transaction, '
            'scoped to this database file on this host; it claims no cross-host or shared-filesystem guarantee.')

def _reject_backup_link(path, label):
    """Isolated-layout backup link check with the historical symlink wording.

    Only the isolated layout runs this: the published flat CLI keeps its
    historical link-following behaviour byte for byte. In isolated layout the
    destination name has one-second granularity, so a pre-existing entry can be
    a link before the call. Only ``st_nlink`` can reveal a hard link; neither
    kind may be written through, because both would replace bytes outside the
    state directory.
    """
    try:
        info=os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise LayoutError(f'refusing to write a backup through a symlinked {label}')
    if not stat.S_ISDIR(info.st_mode) and info.st_nlink > 1:
        raise LayoutError(f'refusing to write a backup through a hard-linked {label}')

def backup(root, state_root=None):
    src=db_path(root, state_root)
    if state_root is not None:
        # Direct library callers bypass the CLI's up-front validation; refuse a
        # linked state slot here, before SQLite opens it and writes the WAL.
        _validate_isolated_components(root, state_root)
    if not src.exists(): raise RuntimeError('task database is not initialized')
    outdir=state_dir(root, state_root)/'backups'
    if state_root is not None:
        # Isolated layout only. The destination name has one-second granularity,
        # so a hostile or stale entry can exist before this call. Refuse to
        # follow a symlink or a hard link: writing through either would replace
        # a database outside the state directory. These are pre-write checks,
        # not an atomic guarantee against a concurrent same-UID swap. Flat
        # backup keeps the historical link-following behaviour (CLI parity).
        _reject_backup_link(outdir, 'backup directory')
    outdir.mkdir(parents=True,exist_ok=True)
    name='state-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3'; dest=outdir/name
    if state_root is not None:
        _reject_backup_link(dest, 'backup destination')
    a=sqlite3.connect(src); b=sqlite3.connect(dest)
    try: a.backup(b)
    finally: b.close(); a.close()
    print(f'Backup created: {dest}')

def _script_under_agentorch(path):
    """True when *path* has the isolated scheduler shape (a hint, not auth)."""
    path=Path(path)
    return (path.name=='orchestrator.py'
            and path.parent.name=='scripts'
            and path.parent.parent.name==ISOLATED_DIRNAME)

def _reject_component_link(path, label):
    """Refuse a symlink or a multiply-linked entry before any read/write.

    lstat does not follow the final entry, so a symlink is always visible. A
    hard link is *not*: it looks like an ordinary file while sharing its inode
    and bytes with a name outside this tree, so only ``st_nlink`` reveals it.
    Directories are exempt because their link count includes child directories,
    not outside aliases. These are pre-write checks, not atomic isolation: a
    same-UID writer can still swap the entry between this check and the write.
    Diagnostic text is path-free because it travels to public Issue evidence.
    """
    try:
        info=os.lstat(path)
    except FileNotFoundError:
        return
    except NotADirectoryError:
        # A non-directory ancestor means the entry cannot exist as a link.
        # Git's linked-worktree layout is exactly this shape: `.git` is a
        # regular file holding `gitdir: ...`, so `.git/info` has no lstat.
        # The ancestor itself is still checked by its own label.
        return
    if stat.S_ISLNK(info.st_mode):
        raise LayoutError(f'isolated layout refuses a symlinked {label}')
    if not stat.S_ISDIR(info.st_mode) and info.st_nlink > 1:
        raise LayoutError(f'isolated layout refuses a hard-linked {label}')

def _validate_isolated_components(kit, state):
    """Fail closed before any read/write when an owned path is a component link.

    These are pre-write checks, not atomic isolation: a same-UID writer can still
    change the tree between this check and the write. The claim is only that a
    recognisably isolated CLI invocation does not follow an existing link.
    """
    kit=Path(kit); state=Path(state)
    for label,path in (
        ('AgentOrch root', kit),
        ('resource dir scripts', kit/'scripts'),
        ('resource dir docs', kit/'docs'),
        ('resource dir templates', kit/'templates'),
        ('state dir .local', kit/'.local'),
        ('state root', state),
    ):
        _reject_component_link(path, label)
    for label,path in (
        ('state database', state/'state.sqlite3'),
        # SQLite writes the WAL and shared-memory files next to the database.
        # A preset hard link there is written through just like the database.
        ('state database write-ahead log', state/'state.sqlite3-wal'),
        ('state database shared memory', state/'state.sqlite3-shm'),
        ('runtime config', state/'config.json'),
        ('backup dir', state/'backups'),
        # `init` appends its ignore rule to Git metadata. A linked .git tree or
        # exclude file would send that write into the host repository instead.
        ('git metadata directory .git', kit/'.git'),
        ('git metadata directory .git/info', kit/'.git'/'info'),
        ('git exclude file', kit/'.git'/'info'/'exclude'),
        ('project config', kit/'docs'/'PROJECT.md'),
        ('plan of record', kit/'docs'/'PLAN.md'),
        ('plan template', kit/'templates'/'plan.md'),
    ):
        _reject_component_link(path, label)

class _RootOptionAction(argparse.Action):
    """Record every --root value argparse actually accepted.

    argparse resolves long-option prefixes (``--ro``) and ``--root=VALUE``
    itself, so scanning raw argv for one spelling let an accepted out-of-bounds
    or empty value bypass the isolated validation. Recording here keeps the
    custom check and the parser in agreement for every accepted form.
    """
    def __call__(self, parser, namespace, values, option_string=None):
        history=list(getattr(namespace, 'root_values', None) or [])
        history.append(values)
        setattr(namespace, 'root_values', history)
        setattr(namespace, self.dest, values)

def resolve_cli_layout(layout, args):
    """Return ``(root, state_root_or_None)`` for the requested CLI layout.

    ``auto`` keeps every historical flat invocation unchanged, but refuses a
    bare invocation of a scheduler that is itself located under
    ``*/agentorch/scripts/orchestrator.py``: that is exactly the shape where a
    damaged isolated install and a legacy flat copy can be byte-identical.
    ``flat`` is the explicit legacy compatibility override. ``isolated`` requires
    that recognisable shape and pins the root to the script's AgentOrch root;
    state/config/backups move under ``<agentorch>/.local/orchestration``.
    """
    lexical=Path(os.path.abspath(__file__))
    actual=Path(__file__).resolve()
    recognised=_script_under_agentorch(lexical) or _script_under_agentorch(actual)
    if layout=='auto':
        if recognised:
            raise LayoutError(
                'this scheduler is located under an isolated layout; refusing the bare '
                'invocation because it cannot be told apart from a flat copy. Re-run with '
                '`--layout isolated` for an isolated install, or `--layout flat` only for '
                'a legacy flat checkout that deliberately lives at that path.')
        return root_path(args.root), None
    if layout=='flat':
        return root_path(args.root), None
    if not recognised:
        raise LayoutError(
            '--layout isolated requires a scheduler located at '
            '<host>/agentorch/scripts/orchestrator.py. Run the flat CLI without '
            '--layout, or install the kit under the host\'s agentorch/ directory first.')
    kit=actual.parent.parent
    if _script_under_agentorch(lexical):
        lexical_kit=lexical.parent.parent
        if lexical_kit.is_symlink():
            raise LayoutError('isolated layout refuses a symlinked AgentOrch root')
        if lexical_kit.resolve()!=kit:
            raise LayoutError(
                'the scheduler path is ambiguous: its lexical path and resolved path '
                'point at different AgentOrch roots')
    if kit.is_symlink():
        raise LayoutError('isolated layout refuses a symlinked AgentOrch root')
    root_values=list(getattr(args, 'root_values', None) or [])
    if len(root_values)>1:
        raise LayoutError(
            f'--root may be given at most once in isolated layout; got {len(root_values)} values.')
    if root_values:
        raw=root_values[0]
        if raw is None or not raw.strip():
            raise LayoutError(
                '--root must not be empty in isolated layout; omit it to use the AgentOrch root.')
        candidate=Path(raw)
        if candidate.is_symlink():
            raise LayoutError('--root must not be a symlink in isolated layout')
        if candidate.resolve()!=kit:
            raise LayoutError(
                '--root is outside the AgentOrch root; isolated layout refuses a root '
                'it did not resolve from the scheduler location.')
    state=kit/'.local'/'orchestration'
    _validate_isolated_components(kit, state)
    return kit, state

def main(argv=None):
    raw=list(sys.argv[1:] if argv is None else argv)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout',choices=LAYOUTS,default='auto',
                        help='root layout: auto keeps the flat CLI unless this script is under */agentorch/scripts, where a bare call is refused; flat is the explicit legacy override; isolated keeps state/config/backups under <agentorch>/.local/orchestration')
    parser.add_argument('--root',default='.',action=_RootOptionAction,
                        help='project root (default: current directory)')
    sub=parser.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init',help='initialize the local task database and .orchestration/config.json (retry_cap, local_dir); seed docs/PLAN.md from templates/plan.md when absent')
    sub.add_parser('backup',help='create a consistent SQLite backup')
    cf=sub.add_parser('config',help='inspect runtime configuration'); cs=cf.add_subparsers(dest='configcmd',required=True)
    cs.add_parser('show',help='print effective config values and their source')
    t=sub.add_parser('task',help='task operations'); ts=t.add_subparsers(dest='taskcmd',required=True)
    # Multi-value options are repeatable: `action='extend'` merges every occurrence
    # instead of letting the last one overwrite the earlier ones (argparse's default
    # `store`). `--acceptance` keeps the given order; task_add() still sorts and
    # de-duplicates `--paths` / `--depends`.
    a=ts.add_parser('add'); a.add_argument('--id',required=True); a.add_argument('--title',required=True); a.add_argument('--description',default=''); a.add_argument('--paths',action='extend',nargs='+',required=True); a.add_argument('--depends',action='extend',nargs='*',default=[]); a.add_argument('--acceptance',action='extend',nargs='+',required=True)
    a.add_argument('--resources',action='extend',nargs='*',default=[],
                   help='optional declared resource set: <resource_id>:<mode>[:<ttl>], comma-free list; repeatable; empty means the task takes no resource lock')
    l=ts.add_parser('list'); l.add_argument('--active-only',action='store_true')
    sh=ts.add_parser('show'); sh.add_argument('id')
    rd=ts.add_parser('ready'); rd.add_argument('id')
    dp=ts.add_parser('dispatch'); dp.add_argument('id')
    dp.add_argument('--resource',action='append',default=[],
                    help='extra <resource_id>:<mode>[:<ttl>] claimed with the declared set (optional; default behaviour unchanged)')
    dp.add_argument('--holder',default='',help='locatable holder ID (default: host:pid or $ORCH_HOLDER)')
    dp.add_argument('--evidence',default='',help='locatable evidence reference recorded on the granted locks')
    st=ts.add_parser('status'); st.add_argument('id'); st.add_argument('status',choices=sorted(TRANSITIONS)); st.add_argument('--head-sha'); st.add_argument('--merge-sha',help='full 40-hex merge commit SHA (required for MERGED)'); st.add_argument('--note',default='')
    am=ts.add_parser('amend',help='correct the merge SHA of a MERGED/VERIFIED task, or re-scope a BLOCKED/NEEDS_HUMAN_DECISION/FAILED task (audited; requires --note)'); am.add_argument('id')
    am.add_argument('--merge-sha',default=None,help='full 40-hex merge commit SHA (required for the merge-SHA mode)')
    am.add_argument('--paths',action='extend',nargs='+',default=None,
                    help='new allowed path set for a BLOCKED / NEEDS_HUMAN_DECISION / FAILED task; rewrites its path locks')
    am.add_argument('--note',default=None,help='audit reason (required; for --paths include the authorisation)')
    ws=ts.add_parser('workspace'); ws.add_argument('id'); ws.add_argument('--branch',default=''); ws.add_argument('--worktree',default=''); ws.add_argument('--head-sha',default=''); ws.add_argument('--note',default='')
    ul=ts.add_parser('unlock'); ul.add_argument('id'); ul.add_argument('--reason',required=True); ul.add_argument('--force',action='store_true')
    ul.add_argument('--resource',default='',help='release/force-unlock one named resource lock (id or lock_id) instead of the path locks')
    ul.add_argument('--evidence',default='',help='locatable evidence for a forced unlock')
    ul.add_argument('--authorized-by',dest='authorized_by',default='',help='authoriser for a forced unlock of a non-terminal task')
    rs=ts.add_parser('resources',help='inspect named resource locks, their leases and blocking effect')
    rs.add_argument('--resource',default='',help='only this resource_id'); rs.add_argument('--all',action='store_true',help='include RELEASED rows')
    rn=ts.add_parser('renew',help='renew this task\'s own resource leases'); rn.add_argument('id')
    rn.add_argument('--holder',default=''); rn.add_argument('--reason',default=''); rn.add_argument('--evidence',default='')
    rc=ts.add_parser('reclaim',help='reclaim one EXPIRED lock after the cool-down with terminal evidence')
    rc.add_argument('id'); rc.add_argument('--resource',required=True); rc.add_argument('--reason',required=True)
    rc.add_argument('--evidence',required=True); rc.add_argument('--actor',default='')
    rf=ts.add_parser('reconcile',help='rebuild resource lock facts from the store; quarantine what cannot be proven')
    rf.add_argument('--reason',default='cross-session recovery'); rf.add_argument('--evidence',default='')
    args=parser.parse_args(raw)
    if args.cmd=='task' and args.taskcmd=='amend' and args.paths is None:
        # --merge-sha / --note were mandatory options before --paths existed. Keep
        # the pre-existing missing-argument error text and exit code 2 exactly.
        missing=[name for name,value in (('--merge-sha',args.merge_sha),('--note',args.note)) if value is None]
        if missing: am.error('the following arguments are required: '+', '.join(missing))
    try:
        root,state=resolve_cli_layout(args.layout,args)
    except LayoutError as e:
        print(f'ERROR: {e}',file=sys.stderr); return 2
    except OSError as e:
        # An isolated-mode stat/resolve failure is still public-facing evidence.
        if args.layout=='isolated':
            base=Path(os.path.abspath(__file__)).parent.parent
            print(f'ERROR: {_public_diagnostic(e, base)}',file=sys.stderr); return 2
        raise
    try:
        if args.cmd=='init': init(root,state_root=state)
        elif args.cmd=='backup': backup(root,state_root=state)
        elif args.cmd=='config': return config_show(root,state_root=state)
        elif args.taskcmd=='add': task_add(root,args,state_root=state)
        elif args.taskcmd=='list': task_list(root,args,state_root=state)
        elif args.taskcmd=='show': task_show(root,args.id,state_root=state)
        elif args.taskcmd=='ready': task_ready(root,args.id,state_root=state)
        elif args.taskcmd=='dispatch': task_dispatch(root,args.id,args,state_root=state)
        elif args.taskcmd=='status': task_status(root,args,state_root=state)
        elif args.taskcmd=='amend': task_amend(root,args,state_root=state)
        elif args.taskcmd=='workspace': set_workspace(root,args,state_root=state)
        elif args.taskcmd=='unlock': unlock(root,args,state_root=state)
        elif args.taskcmd=='resources': task_resources(root,args,state_root=state)
        elif args.taskcmd=='renew': task_renew(root,args,state_root=state)
        elif args.taskcmd=='reclaim': task_reclaim(root,args,state_root=state)
        elif args.taskcmd=='reconcile': task_reconcile(root,args,state_root=state)
    except Exception as e:
        message=_public_diagnostic(e, root) if state is not None else str(e)
        print(f'ERROR: {message}',file=sys.stderr); return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
