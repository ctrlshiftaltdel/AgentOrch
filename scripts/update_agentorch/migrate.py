"""The one legacy path move the kit has shipped, and how an update treats it.

Formats up to 1.7 shipped the core orchestration contract at
``kit/orchestration.md``. It now lives at ``docs/orchestration.md`` and is the
only ``docs/`` path that ships. Two consequences are handled here, in one place:

1. **Ownership.** ``docs/`` is otherwise a project-owned prefix (the project
   fills in ``docs/PROJECT.md`` / ``docs/PLAN.md`` and keeps its own documents
   there). The contract is not project-owned: the kit ships it, and an update
   must be able to install a new version of it.
2. **Migration.** An old install still has ``kit/orchestration.md``. The kit
   never deletes it and never writes the new path next to it, because that would
   leave two editable authorities or silently drop a locally edited copy. The
   update stops, reports exactly what it found, and asks for the migration to be
   authorised (HD-09) or for the local conflict to be resolved (HD-10).

The mapping is a closed list, not a pattern: only this documented move is
recognised. Any other removal/rename keeps the generic classification rules in
``classify.py`` (``docs/self-improvement/update-safety.md`` §4).
"""
from __future__ import annotations

LEGACY_CONTRACT_PATH = 'kit/orchestration.md'
CORE_CONTRACT_PATH = 'docs/orchestration.md'
LEGACY_MOVES = {LEGACY_CONTRACT_PATH: CORE_CONTRACT_PATH}

# Decisions an unmigrated / conflicting contract raises. The questions are
# specific to this move; the ids come from the existing human-decision catalogue.
CONTRACT_OPTIONS = {
    'HD-09': [
        'keep kit/orchestration.md and migrate it manually (default)',
        'delete kit/orchestration.md after reviewing its content, then rerun',
    ],
    'HD-10': [
        'keep the local docs/orchestration.md (default)',
        'review the difference and accept the shipped contract',
    ],
}

# Decisions that mean "the install path is clear": APPLIED (APPLY mode),
# DEFERRED (PLAN mode) and NOT_APPLICABLE (already current).
CLEAR_DECISIONS = ('APPLIED', 'DEFERRED', 'NOT_APPLICABLE')


def is_legacy_contract(path: str) -> bool:
    return path == LEGACY_CONTRACT_PATH


def is_core_contract(path: str) -> bool:
    return path == CORE_CONTRACT_PATH


def target_of(path: str) -> str | None:
    """New path for a legacy path, or ``None`` for anything else."""
    return LEGACY_MOVES.get(path)


def legacy_note(old: str, new: str) -> str:
    """Report note for the old side of the move (kept, never deleted)."""
    return (f'the kit moved this contract to {new!r}; the old copy is kept and '
            'nothing is written until the move is authorised (HD-09)')


def new_side_note(old: str, new: str) -> str:
    """Report note for the new side while the old copy is still on disk."""
    return (f'not created while {old!r} still exists: the project must keep exactly '
            'one authoritative contract (HD-09)')


def contract_conflict(*, current_paths, target_paths, entries) -> tuple[str, str] | None:
    """Return ``(decision_id, question)`` when the contract cannot be installed.

    ``current_paths`` / ``target_paths`` are the path keys of the current and the
    target snapshot; ``entries`` are the classification entries produced for this
    run. An empty result means the install path is clear: either the package does
    not ship the contract, the legacy path is still part of the package (older
    release), the old copy is gone and the new path is already current, or the new
    path is safe to write as ``UPSTREAM_UNCHANGED``.
    """
    current = set(current_paths)
    target = set(target_paths)
    if CORE_CONTRACT_PATH not in target:
        # Not a package that ships the contract: nothing to enforce here.
        return None
    if LEGACY_CONTRACT_PATH in target:
        # This release still ships the old path; the generic classification
        # already covers it and there is no move to police.
        return None
    if LEGACY_CONTRACT_PATH in current:
        return (
            'HD-09',
            'The kit now ships the core contract at docs/orchestration.md, but '
            'kit/orchestration.md is still in this project. Nothing was written: '
            'keeping both would leave two editable authorities. Review the old '
            'copy, then delete it (or authorise the migration) and rerun.',
        )
    entry = next((item for item in entries if item.get('path') == CORE_CONTRACT_PATH), None)
    if entry is None:
        return (
            'HD-09',
            'The package ships docs/orchestration.md but the update report has no '
            'classification for that path. Refusing to apply an unexplained contract.',
        )
    if entry.get('decision') in CLEAR_DECISIONS:
        return None
    return (
        'HD-10',
        'docs/orchestration.md already exists locally with different bytes and is '
        'not part of the installed baseline, so it is not overwritten. Review the '
        'difference and decide which contract stays authoritative.',
    )
