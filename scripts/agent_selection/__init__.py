"""Local agent-selection: per-terminal scope, Markdown store and role matching.

Phase D of ``docs/design/agent-role-discovery-and-model-selection.md``:

``scope``            (S1, #120) resolves a stable, per-terminal scope id from a
                     runtime session id, an interactive TTY identity and the
                     explicit ``AGENTORCH_SCOPE_ID`` namespace. Raw terminal
                     paths are hashed and never stored; when no stable identity
                     exists the resolver fails closed instead of falling back to
                     a shared directory.
``local_store``      (S1, #120) persists records as Markdown under
                     ``.local/agent-orch/<scope-id>/`` with atomic writes, an
                     explicit concurrency conflict and a privacy scan.
``runtime_adapter``  (S2, #121) is the adapter contract: runtime identity,
                     C1-C8 evidence consumption, the C1-C4 dispatch gate and the
                     candidate record. It defines no capability of its own.
``discovery``        (S2, #121) prefers the runtime's formal agent enumeration
                     and falls back to an explicit registry; with neither it
                     reports ``NO_ADAPTER`` / ``EMPTY`` instead of inventing an
                     agent.
``roles``            (S2, #121) applies Master / Execution / Reviewer hard
                     filters before an explainable ranking and reports the
                     selection, alternatives, missing evidence and degradation.

C1-C4 stay authoritative in ``kit/orchestration.md`` §5 and C5-C8 in
``templates/runtime-capability.md``. This package consumes and references them;
it never redefines them and never grants a permission.

Standard library only; no network, no third-party dependency.
"""

__all__ = ['scope', 'local_store', 'runtime_adapter', 'discovery', 'roles']
