# Autonomous delivery: experimental execution slice

This is not a complete delivery loop. The opt-in entry point is
`scripts/autonomous_delivery.py`: provide `--repo`, `--plan`, `--state` and
`--execute`. The repository and every task worktree must already exist; each
task worktree must share the repository's Git common directory, be clean, and
use a non-main branch distinct from the repository's current branch. State is a
private local journal outside those worktrees, not the GitHub or local edition's
authoritative task carrier.

This experimental entry and this document are **repository-only**: they are
not in the release `REQUIRED` manifest. Installation and use from a Kit
release are not implemented or verified.

The plan is trusted JSON with a `tasks` array. Each task has a unique `id`,
an absolute `worktree`, a `prompt`, a nonempty `paths` array of relative
file/directory names, a `depends` array of task IDs, and a nonempty `verify`
array of argument arrays. For example, a verification entry could be
`["python3", "-m", "unittest"]`; it is an example, not a claim that this
command was run in the target project. Commands run without a shell, but are
otherwise fully trusted and execute with the operator's permissions.

The runner uses a private OpenCode server, sets its worktree and `PWD`, and does
not use `--auto`. It records the actual session IDs, changed paths, base and
head, diff digest and command exit codes, then invokes a new review session.
Review text is evidence for a human, not approval. The journal contains model
text and may contain sensitive information; protect it
accordingly. No branch is merged and no task becomes `VERIFIED`: independent
approval, GitHub/local carrier updates, merge, post-merge verification, and
automatic dependent-task continuation are **not implemented**. Independent
tasks can still proceed after a blocked task.

A task recorded as running after interruption becomes `UNKNOWN` and is never
automatically restarted. Other existing task states are not automatically
retried; reconcile manually. Plan changes are refused against an existing
journal. The per-journal lock serializes only callers using that same journal;
it is not a repository-wide or GitHub task lock. Do not run competing writers.

**Security boundary:** worktrees, prompts, path checks, and `--standalone` are
not sandboxes. OpenCode V2's existing configuration may allow shell, external
directory access, network, credentials and writes outside the task paths.
Do not use this on untrusted plans or in environments where the existing
OpenCode authority exceeds the user's authorization. Path checks observe Git
changes after execution; they cannot undo external actions, ignored files or
data disclosed during execution. The caller must authorize the repository,
commands and current runtime permissions. This tool does not change permissions.
