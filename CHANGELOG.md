# Changelog

Notable changes to SAKO, one entry per release. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). SAKO is alpha software, so commands and
file formats can change in any 0.x release.

## [0.6.0] - 2026-10-18

Sessions that share a checkout hold each other up less, and the records say more.
Another session's stale claim or overlap no longer blocks a stop as a ledger finding,
a closed task no longer covers later edits in its scope, records-only tasks close
without the product check, receipts carry times and the checked commit,
`verify` keeps its last pass and the check's output, and Claude Code's hooks find
the kit from a nested repository. The method, the skill and the docs follow the
runtime, and task records from 0.5.1 still parse. SAKO remains alpha software.

From your project's Git repository, install with `uvx sako@0.6.0 init`, or update an
existing installation with `uvx sako@0.6.0 init --update`.

To update from 0.5.1, follow these steps:

1. Update between working sessions, in the main checkout. There `init --update`
   also gives each linked worktree that has SAKO's hooks the new hook command and
   keeps its other entries. A worktree without them gets none until you run
   `uvx sako@0.6.0 init` in it, and a tracked settings file there is left unchanged,
   with a note that names the file in full and gives the content to write.
2. The hook command changed for both clients. When the shell's folder leads to no
   kit, it looks again from the folder Claude Code names in `$CLAUDE_PROJECT_DIR`.
   Codex records trust against each hook's hash and skips changed hooks until you
   trust them, so open `/hooks` in Codex and trust SAKO's hooks again.
3. End any session left open across the update, then start it again with the same
   ID, which keeps its claims and takes a new start record, or start a new one.
   Until it ends, it keeps its start record, and every file with the owner's
   executable bit that was already uncommitted when it started reads as changed by
   it. On Windows, Python reports `.bat`, `.cmd`, `.com` and `.exe` files as
   executable.
4. Run `verify` when a check is configured. The new version makes every recorded
   pass stale, with the reason "the check command or paths changed since the last
   pass".
5. If a skills tool or a hand edit left a copy of the skill in `.agents/skills/sako/`
   or `.claude/skills/sako/` that differs from the one SAKO installed and from this
   release's, `init --update` refuses with "has local content" and changes nothing.
   Keep what you need from that copy, delete it, and update again. A copy tracked in
   Git is left unchanged, with a note, so update it yourself.

### Added

- Records-only tasks, whose every scope entry lies in `.sako/`, close without a
  fresh check while only others moved checked content. Their receipt says "no
  automated check (records only)", and the Proven rule skips them while that holds;
  Recorded and Committed still apply. Others' changes are edits left uncommitted
  before your session started, committed since or not, and edits or commits in the
  scope of a task that a live peer in this checkout claimed or closed. Any other
  commit since your start, and a merge, pull or checkout of older commits, counts
  as yours.
- `verify` writes the check's output to `verify.log` beside the stamp and prints it
  when the check ends, instead of as it runs. A failed run's output also goes to
  `verify-failed.log`, which a later pass leaves in place. The PASS and FAILED lines
  name the log.
- `add` names each claimed row that the new task's scope overlaps, with its holder's
  marker.
- One `--scope` takes several paths, and the repeated form still works. A task text
  placed right after a scope path fails with a usage error and adds nothing.
- Each verb's `--help` says what the verb does.

### Changed

- Of the ledger findings, the finish gate blocks only on a duplicate ID, a source
  mapped twice, a faulty row and an overlap with one of the session's own claims.
  Footprint findings and the three rules block as before. Other sessions' stale
  claims and overlaps print as `note:` lines after the verdict of `check --gate`,
  each with its fix, and a clear stop through a hook stays silent. `start`,
  `status` and plain `check` still list every finding, those about other sessions
  last.
- `claim` writes "since" and the time, to the minute with its zone, after the marker
  in Status. `close` carries that marker and time into the receipt, so a session that
  resumes on a later day closes under the marker it claimed with, and writes the
  close time where it wrote the date. A check pass in a receipt names the commit the
  check ran at, with "plus uncommitted changes" when the checked files differed from
  it.
- An overlap refusal from `claim` names the other claim's scope and holder and the
  ledger file where a Scope can be narrowed, with a next step that fits that holder,
  such as waiting for a live one or taking a stale one over first. A parked row's
  refusal names the Status edit that reopens it. From a linked worktree, these
  refusals and the one for a missing row give the ledger's absolute path.
- The claim printout and the handoff advisory say that if this session ends before
  closing the task, its claim reads as stale and another session may take it over
  with a reason. The stale-claim finding also offers parking the row.
- Peer lines show the last 8 characters of each peer's session ID and list only its
  claimed rows, under one header with the advice for a peer with no claim. `start`
  and `status` list each handoff folder once, and a closed task's remaining one for
  every session, marked "(closed: delete)".
- `status` names the command `verify` runs, and so do the evidence line, `close`'s
  refusal and the gate's Proven finding when no pass is recorded. The
  changed-this-session line and the clear line of `check --gate` name the checkout
  they judged.
- The start context, and the line `init` gives for project instructions, say to
  read SAKO.md down to its Read when needed table and later sections only when they
  apply, and that questions need no task record until they change a file, with
  `add` and `claim` before that change. The start context labels its pick "next, if
  no task was named for you", and its marker line drops its hint.
- Each "not counted" note of the gate names at most three paths, then "and N more",
  and the sentence that a shared checkout shows what changed, not who changed it,
  comes once, as its own note.
- The Committed fix for product files suggests the task's title as the commit
  message, without the task ID.
- `end` prints `sako: presence ended`, or
  `sako: no presence entry for this session ID; nothing ended`. When removing the
  entry fails, as in a kit folder a sandbox keeps read-only, it exits 3 with the
  reason instead of passing silently. The session end hook still exits 0.
- `init --update` in the main checkout also refreshes SAKO's hook entries in each
  linked worktree that has them, keeping the other entries there. A linked
  worktree's settings file that holds SAKO's entries but does not parse stops the
  update before anything is written.
- A tracked client settings file is still left unchanged, and the note `init`
  prints for it now gives the content to write there, SAKO's hooks included, and
  names the file in full when it is in another checkout. The line that lists the
  wired clients begins `Hooks:` instead of `Hooks written:`, since a tracked file
  gets none.
- `remove` also deletes the client folders it leaves empty, such as
  `.claude/skills/`, so a later `init` no longer detects a client from them, and
  its last line says that SAKO's lines in `.git/info/exclude` remain to delete.
- The finding for a stray `.sako/` in a linked worktree says to move or delete it.
- The method and the skill say to read SAKO.md down to its Read when needed table
  and that questions need no task record until they change a file, with `add` and
  `claim` before that change. The skill names `uvx sako init` for a project without
  `.sako/` and drops the check-in paragraph that the daily loop holds. The method
  follows the runtime throughout, including how to park an owner's step or deferred
  work as its own row, what leaving, a resume, `/clear`, a fork and a restart do to
  presence and claims, and which session ID a subagent uses.
- The limits page names the limits that remain in local coordination. The
  compatibility page says that a subagent's shell carries its parent's session ID,
  that Codex runs hooks only after you trust them in `/hooks`, again after each
  change, and records what background `/exit` and resume did in use. The update page
  covers linked worktrees, a session left open across an update, and rollback.
  SECURITY.md lists everything a hook or `init` reads, and the glossary, the
  validation page and the README match the gate and its receipts.
- CONTRIBUTING.md welcomes pull requests that agents open on their own, when they
  name the tool, show the checks and answer review. AGENTS.md covers work from a
  fork, a pull request template asks for the issue, the change, the check output and
  the AI tool used, and the README shows badges and points to good first issues.

### Removed

- The ledger finding that a task was claimed before its prerequisites were done.
  `claim` already refuses such a row, and `next` lists waiting rows with their
  reasons.

### Fixed

- Claude Code's hooks work from inside a nested repository, such as the repo
  footprint's `.sako/` repository. The hook command looks for the kit from the
  current folder, then from `$CLAUDE_PROJECT_DIR`, and runs SAKO from where it found
  the kit. Before, the hook exited 1 there and the stop went ungated. Codex names no
  project folder, so a Codex hook there still exits 1, now naming the folder it
  searched and how to recover.
- On Linux and WSL a restart ends presence. A presence entry records the machine's
  boot, so after a restart it no longer counts as live through a reused process ID
  or a sandbox entry's 24-hour lease, and its claims read as stale at once. Its file
  stays, so a resumed conversation keeps its start record.
- The session end hook removes presence first, before it reads `config.json`, so a
  client that cuts the hook short, or a broken config, no longer leaves the session
  live. The start hook also records presence before it reads the config.
- The Recorded rule lets another session's closed task excuse a commit only when the
  task closed on or after the day the commit was authored, so a broad task closed
  early no longer excuses every later commit under its scope. A live peer's claim in
  this checkout is checked first, and the gate labels a commit with its day.
- A takeover can move several overlapping claims of one ended session, one after
  another, where it was refused before, and the claim line names those still to
  take over. A third session is still refused against the claims the taker holds.
- `verify` keeps the last pass while the check runs, so a peer in the same checkout
  no longer reads "no passing check recorded" meanwhile. A failing run deletes it,
  as does a run whose checked content or command changed during it.
- The Committed finding for receipts names only this session's closed rows that the
  committed done file lacks, so a peer's pending receipt no longer blocks a session
  whose receipts are committed. Its fix stages the two ledger files by name.
- In the repo and shared footprints, the records commit lines that `init` and
  `status` print stage by name the records files that exist (`.gitignore`,
  `config.json` and the two ledger files) and commit only those, and the first-share
  line commits only `.sako/`. Other files, and anything already staged, stay out.
- The check's stamp and a session's start record count only the owner's executable
  bit, the one Git tracks. A change to that bit counts as the session's own change
  when Git shows it or when the file was already uncommitted at the start, and a
  change to only a group or other bit no longer makes the check stale. With
  `core.fileMode` set to false, Git ignores the bit, so such a change on a file that
  was clean at the start still goes uncounted, as the [limits](docs/limits.md) say.
- A closed task no longer covers an uncommitted file that changed in a later minute
  than its close, the precision of its receipt. Before, an edit made with no task in
  the scope of a task a live peer had closed passed the editor's gate, and the
  closer's gate asked to commit it under the closed task's title. A task closed and
  committed earlier was also named for a later task's change in the same scope. Now
  such an edit counts under Recorded unless a claim covers it, and Committed names
  a closed task only for files that last changed no later than the minute it closed.
- A gate run in a linked worktree other than the one where the session started no
  longer names files that were uncommitted in the starting checkout. A presence
  entry records the checkout of its start record, and in any other checkout every
  uncommitted file there counts.
- Repeating the intake of a done task that was added with `--after` reuses it, as
  the method says. The done record keeps no After column, so the repeat was refused.
- On Windows, `verify` looks a bare command name up on the path before it runs it.
  Windows' own search tries System32 first, so `["bash", ...]` ran WSL's bash there,
  even from Git Bash.
- The local footprint's records line says `git clean -dx`, which deletes `.sako/`,
  instead of `git clean -x`, which keeps the folder.

### Validation

- Both suites pass on Python 3.10 and 3.12 locally, with 246 tests and 22
  scenarios; these counts overlap.
- Natively on Windows from Git Bash, with Python 3.14.4 and Git for Windows 2.52.0,
  246 tests ran with 46 skipped and the scenario suite passed 17 and skipped 5, with
  none failed.
- CI checks Python 3.10 and 3.14 on Linux, macOS and Windows, plus package building
  and installation. The known [platform limits](docs/limits.md) remain.

## [0.5.1] - 2026-09-29

A clearer first page and session check-in wording. Runtime behavior, commands
and task record formats are unchanged. SAKO remains alpha software.

From your project's Git repository, install with `uvx sako@0.5.1 init`, or
update an existing installation with `uvx sako@0.5.1 init --update`.

### Added

- A README storyboard from a reproducible two-session example, with its full
  command transcript and checks that keep it current.
- A compact diagram of how SAKO fits a project, with editable visual sources.

### Changed

- A shorter README organized around getting started, working with parallel
  agents, and bringing clarity to an existing project.
- The method, skill and demos describe `start` as registering an existing agent
  session. Your coding tool launches agents; SAKO coordinates their task records
  and checks. `end` clears presence without stopping the agent process.

### Validation

- Both suites pass on Python 3.10 and 3.12 locally, with 180 tests and 22
  scenarios; these counts overlap.
- CI checks Python 3.10 and 3.14 on Linux, macOS and Windows, plus package
  building and installation. The known [platform limits](docs/limits.md) remain.

## [0.5.0] - 2026-09-25

The first public release.

Install from your project's Git repository with `uvx sako init`. To update later, run
`uvx sako@latest init --update`.

### Added

- A task ledger in Markdown, `.sako/work/TASKS.md`, with a done record that keeps a
  receipt for every closed task.
- A finish gate that checks, when a session stops, that its changes belong to a
  claimed task, that closed work has a passing check for its content, and that
  finished work is committed.
- Hooks for Claude Code and Codex, chosen by `init`; any other agent runs `start`,
  `check --gate` and `end` itself.
- Claims and scope checks for parallel sessions in one repository and its linked
  worktrees.
- Three footprints for the records: local (the default), their own repository, or
  committed with the code.
- The method, `SAKO.md`, with a small default planner, and a `sako` skill for agents.

### Known issues

- Codex gets no hooks on Windows; there the agent runs the commands itself.
- No live client session has run on a Mac yet; macOS is tested in CI.
- See [limits](docs/limits.md) for the rest.

### Tested with

- Python 3.10 and 3.14 on Linux, macOS and Windows in CI; Python 3.10 and 3.12 on
  WSL2 (Ubuntu 24.04).
- Git 2.31 or newer.
- Claude Code 2.1.280 on Linux and Codex 0.154.0 on Linux; Claude Code 2.1.220 hooks
  on Windows.

[0.5.0]: https://github.com/Thakay/sako/releases/tag/v0.5.0
[0.5.1]: https://github.com/Thakay/sako/releases/tag/v0.5.1
[0.6.0]: https://github.com/Thakay/sako/releases/tag/v0.6.0
