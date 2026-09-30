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
  Bash, `python --version` must show 3.10 or newer: the python.org installer leaves
  Python off the path unless you tick that box, and the Microsoft Store's
  placeholder only prints a message. A failed `init --repo URL` there can leave
  read-only Git files in `.sako/`; delete the folder before trying again. How Claude
  Code and Codex run hooks on Windows is in [compatibility](compatibility.md#windows).
- **macOS:** both test suites in CI on GitHub's macOS runner with Python 3.10 and
  3.14, on the same POSIX code as Linux; one test skips by name, since macOS refuses
  a file name that is not UTF-8. No live client session has run on a Mac. A stock
  Mac's `python3` is 3.9, below the floor; install Python 3.10 or newer.

SAKO's hooks and printed commands use `python3`, or `python` on Windows. Parent
discovery reads `/proc` and falls back to `ps`, including when the shell is `fish`.
Liveness uses a zero-signal process check, treats permission denial as alive, and
applies only in the process namespace that recorded the process, which `/proc`
names. On Windows SAKO reads no process: presence there is the 24-hour lease the
method describes, and `SAKO_AGENT_PID` has no effect. A task can use any language
whose check command is available in that environment.

Commands run from any folder of the repository or its linked worktrees: Git finds
the main checkout, whose `.sako/` holds the kit. Bare repositories, a Git folder kept
apart from its checkout, and submodules are refused. `init` can seed an unborn Git
repository; establish an initial commit before daily work so revision comparisons
have a meaningful baseline.

SAKO writes client files only inside the repository, judged by their real paths; a
client file whose folder leads outside it is skipped with a note. Record paths stay
inside `.sako/`, outside `state/`, including through symlinks. In the local
footprint `.sako/` is listed in `.git/info/exclude`: the records are not in Git, and
`git clean -x` deletes them.

## Local coordination

`add`, `claim`, and `close` share an advisory file lock in `.sako/state/`, which
every worktree of the repository uses. This prevents duplicate allocations and
competing successful claims among cooperating SAKO commands. Complete file
replacements avoid partial rows. A busy lock refuses after two seconds so the
caller can retry. Windows locks are exclusive only, so there only writes take the
lock: reads see whole files, and a read or replacement that Windows refuses for a
moment, while a scanner, a reader or a writer holds the file, is retried. A read
during another session's `close` there can show the task in both records for a
moment; check again before merging anything. SAKO keeps no state under `.git/`,
which a client's sandbox can make read-only for agent commands.

It does not isolate code edits, arbitrary record edits, commits, the staging area,
or other clones. A Git push is not a task lock. Keep one writer for shared files,
avoid overlapping scopes, and serialize shared Git operations. Use separate
worktrees when that isolation is more useful; SAKO does not create them. Claims
across worktrees are as strict as in one checkout: overlapping scopes refuse.

Worktrees share one ledger, one numbering, one lock and one presence roster; each
keeps its own check stamp. Other machines share no live roster. How Codex's sandbox
treats linked worktrees is in [compatibility](compatibility.md#client-behavior-worth-knowing).

In the shared footprint each branch keeps its own `.sako/`, so worktrees on
different branches keep separate ledgers: they can allocate the same task ID and
cannot see each other's claims, and merging branches can conflict in the records
like any tracked file. In the repo and shared footprints, clones and colleagues
share records through Git, one writer at a time: numbering and claims are judged
locally, so two machines writing at once collide on numbers and see each other's
claims as stale. A team that needs more moves to a tracker; see
[move the task authority to a tracker](move-to-a-tracker.md).

Presence only estimates liveness; the method explains
[how it works](../SAKO.md#pause-handoff-and-takeover), inside a sandbox too. Process
IDs count only where the process is visible. PID reuse, containers and unexpected
wrappers, such as a `timeout` around the command, can misrepresent it, and a session
without an end event can appear live until its host exits or its 24 hours pass. Do
not give concurrent workers the same session ID.

## Records and evidence

This is a deliberately narrow Markdown table parser. It recognizes `T-<number>`
rows in tables whose header names the required columns, and fenced examples. It
detects task-like malformed IDs and shapes; it cannot recognize arbitrary prose
as a lost task. Literal cell pipes and multiline cells are unsupported. Task
cells can contain references; they are not a full issue schema.

The fingerprint includes tracked and untracked unignored files in selected paths,
file bytes, symlink text, and executable bits. Ignored artifacts, submodule internals,
symlink target content outside the covered files, services, secrets, environment,
and dependencies not represented there are not evidence-covered. Include relevant
tests, manifests, and lockfiles; independently check external behavior when needed.
A check that writes files where the fingerprint looks, such as Python's `__pycache__`
or a build folder, changes the content it checks: `verify` names those files, and
the gate says to delete or ignore generated ones. List them in `.gitignore`, or keep
the check from writing them (`python3 -B -m unittest`).

The before/after fingerprint detects persistent changes during a check, not an
edit that changes and returns to identical bytes during the run. Verification is
serialized with other SAKO verification commands, not with application editors.
Large trees increase hashing and start/gate cost. Narrow coverage responsibly;
no large-repository benchmark has been established.

`check` can be consistent while evidence is absent or stale. It checks the
ledger's structure and reports automated evidence separately. Its exit status does
not require a passing baseline, or prove that the project's notes define a good
direction or that task text has a useful acceptance condition. Planning and those
judgments belong to the agent and owner. The finish gate checks only
recorded ownership, scoped changes, and its evidence conditions. It cannot prove
the requested behavior, honest task wording, complete scope declarations, test
quality, deployment health, or that another process will not edit after the check.

## Hooks

Hooks are feedback, not a security boundary. The gate refuses a stop once and names
the fix; a session can still stop after saying why. SAKO's hook handler also lets a
session go on a repeated stop callback and on its own errors, such as a missing
session identity or an unexpected bug. Hook failures and timeouts are the client's
to handle, and project policy can turn hooks off.

Written hook settings do not prove that hooks run: `status` says when each
client's hook last ran, or "not seen yet". Each client's hook reference
([Codex](https://learn.chatgpt.com/docs/hooks),
[Claude Code](https://code.claude.com/docs/en/hooks)) has its current configuration
rules; [compatibility](compatibility.md) says what each client needs.

## Why a Python file in your project

Each project gets its own pinned, readable runtime, `.sako/sako.py`, with no package
installed in the project and no service to run: daily commands and hooks use that
copy, offline. The cost is an explicit update step and one folder in the project.
Python's standard library handles the structured records, atomic writes, locks and
fingerprints in one file; your application can use any language, and its check can
be any command. The [principles](../README.md#why-it-stays-small) give the reasoning.

That copy cannot install SAKO into another project: installing and updating need
the package or a checkout. The package's wheel carries the runtime, the method, the
task template and the skill, built from this checkout. SAKO has no update service:
review what you install, and update on purpose
([update and removal](update-and-remove.md)).

## What SAKO does not do

Beyond [how SAKO works](../README.md#how-it-works): SAKO does not start,
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
