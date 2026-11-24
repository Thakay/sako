# Limits

What SAKO cannot do or can get wrong, and what to do about it. Behavior is in
[the method](../SAKO.md), evidence in [validation](validation.md).

## Environment

SAKO runs in local Git worktrees on Linux, WSL, macOS and Windows, with Python 3.10+
and Git 2.31+. Other platforms, bare repositories, a Git folder kept apart from its
checkout, and submodules are refused. In an unborn repository, make a first commit
before daily work, so the gate has a start revision.

A stock Mac's `python3` is 3.9, so install Python 3.10 or newer. On Windows, check
that `python --version` in Git Bash shows 3.10 or newer, since the python.org
installer leaves Python off the path unless you tick that box and the Microsoft
Store's placeholder only prints a message. From PowerShell, `verify` can find WSL's
`bash`, so give Git's `bash.exe` by its full path. A failed `init --repo URL` on
Windows can leave read-only Git files in `.sako/`; delete the folder and retry. WSL
and native Windows on one clone count as two checkouts whose locks do not interlock,
so use one of them per clone.

## Local coordination

The ledger lock serializes SAKO's own writes across worktrees, not code edits, hand
edits of the records, commits, the staging area or other clones, so run shared Git
operations one at a time. On Windows, a read during another session's `close` can
briefly show the task in both records, so check again.

A pass from another checkout that counts here, even a removed worktree's kept in
`.sako/state/`, proves nothing about that checkout's ignored files and dependencies.
The gate judges only the checkout it runs in.

In a shared checkout the gate sees what changed, not who changed it. Only the
checkout's writer is asked about every product change there, whoever made it.
Closing a task scoped `.` makes the Committed fix stage every uncommitted change made
here between your start and its close, a peer's included, so check `git status`
first or narrow the scope. A close seals what each of its files holds, whoever wrote
it, so read `git diff` before following a Committed fix. Recorded cannot tell who
made a commit here, so a task another session closed here since you started excuses
your commit in its scope. A cherry-pick, rebase, `git am` or squash merge makes a
commit here, and where Git's reflog does not reach your start, as for a start by
0.8.0, every commit counts as merged, with a note.

`claim` and `edit` refuse a second writer, but a hand edit, a writer's start in
another checkout, a session back from an end or a lapsed lease, or an update from
0.7.1 can leave two in one; the ledger check names them, and each gate shows the
other's files until one moves. Each start in another checkout takes a new start
record. A start hook counts as a start where its shell is, since what folder its
event names after a `cd`, a compact or a resume is not verified.

In the shared footprint each branch keeps its own `.sako/`, so worktrees on
different branches can allocate the same task ID, cannot see each other's claims,
and can conflict in the records at a merge. Clones share records through Git one
writer at a time; two machines writing at once collide on numbers and see each
other's claims as stale. A team that needs more can
[move to a tracker](move-to-a-tracker.md).

A live session quiet for two hours reads as ended, so others can take over its
claims with a reason. A crashed session, or one without hooks that never ran
`end`, reads as live for two hours and locks product claims in its checkout until
then, or until the owner confirms it stopped and someone runs
`end --session <its ID>`. A copy of `.sako/state/` with new file times revives its
sessions.

A claim left on purpose for another session reads as stale to every other session
until it is taken over; close your part and leave the rest as a second row. A
helper on its parent's ID is the parent, so the ledger shows no separate holder for
it.

## Records and evidence

The ledger parser reads only `T-<number>` rows in tables with the required columns,
so it cannot find a task lost in prose. SAKO does not check the commits, paths or
links a receipt cites.

The fingerprint leaves out ignored files, submodule internals, symlink targets
outside the covered files, services, secrets and the environment, so cover
manifests and lockfiles, and check external behavior yourself. With
`core.fileMode` set to false, a change to only the executable bit of a file clean at
the start makes the check stale without counting as your change, so run `verify`
after one. Large trees slow hashing, `start` and the gate, and none was benchmarked;
narrow `verify_paths` if they are slow.

Whether a closed task still covers an uncommitted file goes by what the file holds,
not by when it was written. A task closed by hand, by an earlier release, in or
from another checkout or before `.sako/state/` was deleted has no seal here, so it
covers its files only once committed. A file stashed while another task closes here
loses its seal and counts as unrecorded once popped.

The gate judges ownership at the stop, not at each write, so a late claim covers
earlier edits and a task you closed covers your later commits in its scope. It
cannot prove the requested behavior, honest task wording, complete scopes, test
quality or deployment health, nor that nothing edits after the check. The Committed
fix for receipts stages the two ledger files whole, so it can carry another
session's pending rows, and the first commit of the shared footprint takes all of
`.sako/` but `state/`, handoff folders included.

## Hooks

Hooks are feedback, not a security boundary. The gate refuses a stop once, and a
repeated stop, a missing session identity or a bug in the handler lets the session
go. Hook failures and timeouts are the client's to handle. A recent hook time in
`status` does not prove every stop was gated.

A hook that finds no kit from its folder, as in the repo footprint's `.sako/`, falls
back to Claude Code's `$CLAUDE_PROJECT_DIR`. A Codex hook there exits 1 and leaves
that stop ungated, as does any hook in a linked worktree of the shared footprint
while the main checkout's branch has no `.sako/`.

## What SAKO does not do

SAKO does not start, direct or message agents, merge their edits, fetch issues,
create applications or commit.

### Not building yet

| Not building | Reconsider when |
|---|---|
| Worktree-aware claims, allowing overlapping scopes across worktrees | Strict claims block parallel agents in worktrees in real use or user reports |
| A gate over an external task store | Someone who moved to a tracker wants to keep the gate |
| Tracker sync or a second task database | A real integration cannot keep one authority through references |
| Distributed claims, scheduling or agent messaging | Remote workers hit a reproduced ownership failure, and SAKO chooses to serve that workload |
| Automatic branches, worktrees, commits, pushes or deploys | A recurring authorized workflow needs it and no existing tool covers it |
| Automatic task generation | A repeatable source supplies authorized work with an explicit identity mapping |
| Dashboards or web boards | Decisions cannot be made from `next` and `status` in a real project |
| A required decision file | Conflicting recorded defaults cause repeated observed errors |
| A global install or host plugin | Several maintained installations need coordinated updates |
| Automatic records commits in the repo footprint | Real use shows the manual records commit at finish hurts |
| A records location outside the project | Someone needs ledgers the repo footprint's remote cannot serve |
| User-level hooks for worktrees a harness creates | Harness-made worktrees need the gate and a per-worktree `init` is not possible |
| Sync through a hidden ref on the code's remote | Sharing through a second remote proves too heavy |
| Hooks in tracked client settings, for the shared footprint | A shared clone needs the gate before its own `init`, and both clients' review of committed hooks is verified |
| Bare or separate-Git-folder layouts | Someone asks for one |
| A `sako` command on your PATH that runs the project's copy | People often type `uvx sako <verb>` or install the package as a tool |
| Liveness read from the agent's process, or a lease length you set | The two-hour lease misjudges live or ended sessions in real use |
| Hooks for Codex on Windows | Codex for Windows can be tried, or a Windows Codex user asks |
| Python 3.9 | A macOS user on Apple's `python3` asks for it |

A [proposal](../CONTRIBUTING.md#propose-a-change) for one shows the observed need.
