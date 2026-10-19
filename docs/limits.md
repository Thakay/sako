# Limits

The boundaries of the runtime and its evidence, and what SAKO does not build.
Behavior is defined in [the method](../SAKO.md); observed results are in
[validation](validation.md).

## Environment

The runtime runs in local Git worktrees on Linux, WSL, macOS and Windows, with
Python 3.10+ and Git 2.31+; other platforms refuse. The evidence differs by
platform:

- **Linux and WSL:** both test suites on Python 3.10 and 3.12, on a WSL2 host
  (Ubuntu 24.04), and live Claude Code and Codex sessions
  ([compatibility](compatibility.md)).
- **Windows:** both test suites natively from Git Bash, with tests whose premise
  Windows lacks skipped by name ([validation](validation.md#native-windows)). In Git
  Bash, `python --version` must show 3.10 or newer. The python.org installer leaves
  Python off the path unless you tick that box, and the Microsoft Store's
  placeholder only prints a message. A failed `init --repo URL` there can leave
  read-only Git files in `.sako/`; delete the folder before trying again. `verify`
  finds a bare command on the path, so from PowerShell `bash` can be WSL's; give
  Git's `bash.exe` by its full path there. How Claude Code and Codex run hooks on
  Windows is in [compatibility](compatibility.md#windows).
- **macOS:** both test suites in CI on GitHub's macOS runner with Python 3.10 and
  3.14, on the same POSIX code as Linux; four tests skip by name, each saying why.
  No live client session has run on a Mac. A stock Mac's `python3` is 3.9, below
  the floor; install Python 3.10 or newer.

Parent discovery reads `/proc` and falls back to `ps`. Liveness uses a zero-signal
process check, treats permission denial as alive, and applies only in the process
namespace that recorded the process, which `/proc` names. On Windows SAKO reads no
process, so presence there is the 24-hour lease the method describes, and
`SAKO_AGENT_PID` has no effect.

Commands run from any folder of the repository or its linked worktrees. Git finds
the main checkout, whose `.sako/` holds the kit. Bare repositories, a Git folder kept
apart from its checkout, and submodules are refused. `init` can seed an unborn Git
repository; establish an initial commit before daily work so revision comparisons
have a meaningful baseline.

SAKO writes client files only inside the repository, judged by their real paths; a
client file whose folder leads outside it is skipped with a note. Record paths stay
inside `.sako/`, outside `state/`, including through symlinks. In the local
footprint the records are not in Git; `.sako/` is listed in `.git/info/exclude`, so
`git clean -dx` deletes them.

## Local coordination

`add`, `claim`, `edit` and `close` share an advisory file lock in `.sako/state/`,
which every worktree of the repository uses. This prevents duplicate allocations and
competing successful claims among cooperating SAKO commands. Complete file
replacements avoid partial rows. A busy lock refuses after two seconds so the
caller can retry. On Windows, whose locks are exclusive, only writes take the lock;
reads see whole files, and a read or replacement refused while another process holds
the file is retried. A read during another session's `close` there can show the task
in both records for a moment; check again before merging anything. SAKO keeps no
state under `.git/`, which a client's sandbox can make read-only for agent commands.

It does not isolate code edits, arbitrary record edits, commits, the staging area,
or other clones. A Git push is not a task lock. Keep one writer for shared files,
avoid overlapping scopes, and serialize shared Git operations. Use separate
worktrees when you need that isolation.

`edit` changes a task's Scope, After, Done when and Status, and only its holder
edits a claimed task. Task and Pri stay hand edits, as does untangling two ended
sessions' overlapping claims, since neither can be taken over while they overlap.
`edit --park` replaces the whole Status cell, a takeover note included.

Worktrees share one ledger, one numbering, one ledger lock and one presence roster.
Each keeps its own stamps and verify lock, so one checkout runs one check at a time,
named or not. A pass from another checkout counts here on identical checked content
and the same command, and the receipt names that checkout and its commit; that
checkout's ignored files and dependencies are not proven here. A failing run removes
only its own checkout's stamp, so another's pass still counts, even a removed
worktree's while its folder in `.sako/state/` remains. The gate judges only the
checkout it runs in; work in another worktree counts once merged, or when
`check --gate --session <id>` runs there. How Codex's sandbox treats linked
worktrees is in [compatibility](compatibility.md#client-behavior-worth-knowing).

In a shared checkout the gate sees what changed, not who changed it. Closing a task
scoped `.` makes the Committed fix stage every uncommitted change made here between
your start and its close, a peer's included; check `git status` first, or narrow the
scope. Recorded cannot tell who made a commit either, so another session's closed
task can excuse your commit, while a peer's commit of its closed work on a later
day, even just after midnight or in another time zone, counts against you. Recorded
reads the commits after your start revision from a minute before your start, since
a wall clock can step back. A closed task still covers edits made in the minute it
closed, so read `git diff` before following a Committed fix. A peer's edits to
checked content also make your check stale.

Claim refuses overlap only with claimed rows, so a live peer in your checkout can
claim a scope over your closed files, and `.` covers them all. While that peer is
live, which is up to 24 hours after its last start when SAKO cannot verify it, edits
and commits in its scope count as its own under Recorded and Proven, whoever made
them. Committed still names your uncommitted closed files, except one written again
in a later minute than your close. The backstop is the peer's own close, which needs
a fresh check.

In the shared footprint each branch keeps its own `.sako/`, so worktrees on
different branches keep separate ledgers. They can allocate the same task ID and
cannot see each other's claims, and merging branches can conflict in the records
like any tracked file. In the repo and shared footprints, clones and colleagues
share records through Git, one writer at a time. Numbering and claims are judged
locally, so two machines writing at once collide on numbers and see each other's
claims as stale. A team that needs more moves to a tracker; see
[move the task authority to a tracker](move-to-a-tracker.md).

Presence only estimates liveness; the method explains
[how it works](../SAKO.md#pause-handoff-and-takeover). PID reuse, containers and
unexpected wrappers, such as a `timeout` around the command, can misrepresent it,
and a session without an end event can appear live until its host exits or its 24
hours pass. On Linux and WSL a restart ends presence. SAKO reads all peers'
progress for `start` and `status` within 2 seconds, so a slow checkout's line says
"not read in time". A peer in your own checkout lists your uncommitted paths too. A
claim left on purpose for another session reads as stale to every other session
until it is taken over; close your part and leave the rest as a second row. Do not
give concurrent workers the same session ID. A helper on its parent's ID is the
parent, so the ledger shows no separate holder for it.

## Records and evidence

This is a deliberately narrow Markdown table parser. It recognizes `T-<number>`
rows in tables whose header names the required columns, and fenced examples. It
detects task-like malformed IDs and shapes; it cannot recognize arbitrary prose
as a lost task. Task cells can contain references; they are not a full issue
schema. SAKO does not check the commits, paths or links a receipt cites, and keeps
no lasting map from a marker to its session.

Of a file's mode, the [fingerprint](../SAKO.md#the-gate-and-checks-in-detail)
counts only the owner's executable bit, the one Git tracks. It leaves out ignored
artifacts, submodule internals, symlink target content outside the covered files,
services, secrets, environment, and dependencies not represented there. Include
relevant tests, manifests, and lockfiles; independently check external behavior when
needed. A check that writes files where the fingerprint looks, such as Python's
`__pycache__` or a build folder, changes the content it checks. `verify` names those
files, and the gate says to delete or ignore generated ones. List them in
`.gitignore`, or keep the check from writing them (`python3 -B -m unittest`).

The gate and `close` read a session's changes from Git, and from the start record for
files already uncommitted when the session started. With `core.fileMode` set to false,
Git ignores the executable bit. A change to only that bit, on a file with no
uncommitted changes at the start, then makes the check stale without counting as the
session's change. Run `verify` after such a change.

Whether a closed task still covers an uncommitted file goes by when the file was
last written, not by what it holds. A stash and its pop, a Git hook, a formatter or
an editor save that rewrites the file in a later minute than the close makes
Recorded list it as "changed after you closed T-n", or after a live peer in your
checkout closed it; the fix then starts with committing it under that task's title
when the content is still its work. Deletions and renames after the close stay the
closed task's, since a missing file has no write time and a rename keeps the old one.

The before/after fingerprint detects persistent changes during a check, not an
edit that changes and returns to identical bytes during the run. Verification in a
checkout is serialized with other SAKO verification there, not with application
editors. Large trees increase hashing and start/gate cost. Narrow coverage
responsibly; no large-repository benchmark has been established.

`check` can be consistent while evidence is absent or stale. It checks the
ledger's structure and reports automated evidence separately. Its exit status does
not require a passing baseline or judge the project's direction or a task's
acceptance condition; planning and those judgments belong to the agent and owner.
The finish gate checks only recorded ownership, scoped changes, and its evidence
conditions, judging ownership at the stop, not at each write, so a late claim covers
earlier edits. A named check, run by `verify NAME`, is evidence, not a gate rule,
since `close` and the Proven rule read only the pass of `verify_command`; a receipt
lists a named check's pass only while it is fresh. The gate cannot prove the
requested behavior, honest task wording, complete scope declarations, test quality,
deployment health, or that another process will not edit after the check.

The Committed fix for receipts stages only the two ledger files, whole, so it can
carry another session's pending rows and leaves other files you keep in `.sako/` to
you.

## Hooks

Hooks are feedback, not a security boundary. The gate refuses a stop once and names
the fix, and a session can still stop after saying why; a missing session identity or
a bug in the handler also lets it go. Hook failures and timeouts are the client's to
handle, and project policy can turn hooks off.

A hook that finds no kit from its folder, as in a nested repository such as the repo
footprint's `.sako/`, falls back to `$CLAUDE_PROJECT_DIR`, where the Claude Code
session started, and judges that checkout. A Codex hook there exits 1 instead,
naming the folder it searched, and that stop goes ungated, as does any hook in a
linked worktree of the shared footprint while the main checkout's branch has no
`.sako/`. A hook runs the `.sako/sako.py` of the repository its folder is in, so a
nested clone that holds one runs its own.

Written hook settings do not prove that hooks run, so `status` says when each
client's hook last ran, or "not seen yet"; a recent time does not prove every stop
was gated. Each client's hook reference
([Codex](https://learn.chatgpt.com/docs/hooks),
[Claude Code](https://code.claude.com/docs/en/hooks)) has its current configuration
rules; [compatibility](compatibility.md) says what each client needs.

## Why a Python file in your project

Each project gets its own pinned, readable runtime, `.sako/sako.py`, with no package
installed in the project and no service to run. Daily commands and hooks use that
copy, offline. The cost is an explicit update step and one folder in the project.
Python's standard library handles the structured records, atomic writes, locks and
fingerprints in one file; your application can use any language, and its check can
be any command. The [principles](../README.md#simple-not-small) give the reasoning.

That copy cannot install SAKO into another project; installing and updating need
the package or a checkout. SAKO has no update service; review what you install, and
update on purpose ([update and removal](update-and-remove.md)).

## What SAKO does not do

Beyond [how SAKO works](../README.md#how-it-works), SAKO does not start,
direct or message subagents, merge their edits, fetch issues, sync trackers, create
applications, or make branches, worktrees, commits, pushes or deployments. The gate
names the commit a finished task needs, and you or your agent make it.

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
| Automatic records commits in the repo footprint | Real use shows that the manual records commit at finish hurts |
| A records location outside the project | Someone needs ledgers that the repo footprint's remote cannot serve |
| User-level hooks for worktrees a harness creates | Harness-made worktrees need the gate and a per-worktree `init` is not possible |
| Sync through a hidden ref on the code's remote | Sharing through a second remote proves too heavy |
| Hooks in tracked client settings, for the shared footprint | A shared clone needs the gate before its own `init`, and both clients' review of committed hooks is verified |
| Bare-repository layouts | Someone with a bare or separate-Git-folder layout asks for it; until then `init` refuses plainly |
| A `sako` command on your PATH that runs the project's copy | People often type `uvx sako <verb>` or install the package as a tool |
| Reading processes on Windows, so presence there is judged like on Linux | A Windows user finds the 24-hour lease misleading in real use |
| Hooks for Codex on Windows, which runs them in PowerShell | Codex for Windows can be tried, or a Windows Codex user asks |
| Python 3.9, which a stock Mac ships | A macOS user on Apple's `python3` asks for it |

A proposal for more machinery should show the observed need and its ownership and
recovery rules; see [contributing](../CONTRIBUTING.md#propose-a-change).
