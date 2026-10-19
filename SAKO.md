# Working with SAKO

The SAKO method, a task ledger and finish gate for coding agents, installed in this
project. Project instructions and the owner's choices come first; they also name
the planner or source of work. This file holds no project state.

Use SAKO for project tasks here, code and documentation changes and bounded
investigations alike, without being asked each time. Questions need no task record
until they change a file; `add` and `claim` come before that change. Read this file
down to its **Read when needed** table. Run `python3 .sako/sako.py <command>`
(`python` on Windows) from the main checkout's top folder; elsewhere, use the
command line the start context prints.

## Daily loop

1. **Check in.** The session-start hook registers your existing agent session
   with SAKO and prints context and your session ID. Without the hook, run
   `start --session <id>` with an ID unique to this conversation; it launches no
   agent.
   Pass the same ID to every command; in Claude Code and Codex, a command
   without `--session` uses the client's session ID, the one the hook used.
2. **Choose and claim.** Follow the owner's priority. `next` names the task to
   start and why. For new work, run
   `add "task" --done-when "observable result" --scope src/`, then
   `claim T-1 --session <id>` with the returned ID. Keep scopes narrow and widen
   them in the ledger before the work grows. If nothing fits, return to the
   selected planner.
3. **Work and check.** Use the project's own tools and checks. When a check is
   configured, `verify` runs it, so it replaces a separate run, and records a
   fingerprint of the checked content. For discovery or a manual check, keep the
   actual findings or procedure and result. A passing suite alone is not
   acceptance, so inspect the requested behavior.
4. **Close and commit.** `close T-1 --session <id> --evidence "result and proof"`
   moves the row to the done record with a receipt: your evidence, then the
   claim's marker and time, the close time, and what the check said and at which
   commit. State the outcome and its limits, not just "done", and cite proof that
   outlives the session, never a `/tmp` path. Commit the task's work within the
   owner's authorization, staging explicit paths, or report it as uncommitted.
   SAKO never commits, pushes or deploys.
5. **Finish.** Run `check --gate --session <id>` (the Stop hook runs it too) and
   fix what it names, or report what remains. If you ran `start` yourself, run
   `end --session <id>` last. With hooks, the client ends the session; do not run
   `end`, since the Stop gate needs your start record. Report the result, the
   evidence and the next decision.

Keep a claim only for work an agent will resume, and write its handoff. A session
that keeps a claim to resume it does not run `end`, since `end` makes its claims
read as stale. A satisfied goal needs no new task.

## Done means

The gate checks this session against three rules. Each finding names its rule and
its fix.

- **Recorded:** every file changed in this checkout since the session started,
  outside `.sako/`, belongs to a task it claimed or closed.
- **Proven:** a task closed after checked content changed has a passing check.
- **Committed:** closed work is committed; in the repo and shared footprints, its receipt too.

A duplicate ID, a source mapped twice, a faulty row, an overlap with your own
claim and a footprint finding also block. Claimed, unfinished work, advisories and
notes, such as another session's stale claim, never block. A clear gate means these
rules passed, not that the owner accepts the result. With a null `verify_command`,
the receipt says "no automated check", never a pass; do not call that work
verified.

## Records

| Information | Home |
|---|---|
| Direction, purpose, acceptance | The project's instructions, brief or README; a task's Source points to its planner item |
| Active work and ownership | `.sako/work/TASKS.md` |
| Completed work with receipts | `.sako/work/DONE.md`, created on first close |
| A paused task's handoff and notes | `.sako/work/T-<n>-<slug>/`, printed by `claim`; you delete it at close |
| Unresolved decisions | The affected task or the project's context: the question, any assumption, who decides |
| The owner holds agreed work ("wait until I try it") | That work's row, `parked` with the reason, so a fresh session waits too |

```markdown
| ID | Pri | Task | Done when | Status | Scope | After | Source |
|---|---|---|---|---|---|---|---|
| T-1 | P1 | A named visitor receives a greeting | The sample prints Hello, Ada | open | `src/`, `tests/` | - | PLAN.md#N1 |
```

This row is an example, not live work. `Pri` is P1, P2 (empty reads P2) or P3.
Status's first word is `open` (empty reads open), `parked` with its reason, or the
claiming session's marker. `After` lists prerequisite IDs. Scopes are
repository-relative path prefixes; `.` covers everything. The done record keeps
`ID | Task | Done when | Receipt | Scope | Source`. IDs are never reused; park a
dropped row instead of deleting it. Columns are read by name, in any order, and
columns you add are kept. Keep a cell on one line, without literal pipes. Hand
edits are fine, and commands change only their own rows.

The records' footprint, which `status` names, is local (out of Git, so
`git clean -dx` deletes them), repo (their own repository inside `.sako/`) or shared
(committed with the code, one copy per branch).

Put each discovery where the next reader needs it: an invariant in the code or test
that enforces it, a product choice in the project's context, progress and traps in
the task's handoff. Reference that home from the receipt rather than copying it.

## Read when needed

Stop here; open a section below only when its situation applies:

| Situation | Section |
|---|---|
| Work comes from a plan, checklist, issue or planning skill | Task intake |
| No planner is selected, direction is unclear, or nothing is ready | No planner? Start here |
| Pausing, leaving, resuming, or a stale claim | Pause, handoff and takeover |
| Another session or a subagent works in this repository | Working beside other sessions |
| A gate finding or check result needs explaining | The gate and checks in detail |
| Installing, updating, removing, or wiring a worktree | Install, update and remove |

## Task intake

Read this when work comes from the project's plan, checklist, issue or planning
skill. Translate the selected work into local rows without replacing its planner or
copying its backlog. Keep a concrete outcome, a completion condition, a bounded
scope and known prerequisites; add only details that change execution.

1. **Name the source.** Keep the item's authoritative location and a stable
   reference such as `PLAN.md#N1` for `--source`. Give each local action from one
   upstream item its own reference, and reuse it exactly on later intake.
2. **Record or reuse it.** Put the outcome in the task text and the completion
   condition in `--done-when`. Declare the files with `--scope`, including a source
   document the task will update. Pass the planner's priority with `--pri` and
   prerequisites with `--after`:

   ```sh
   python3 .sako/sako.py add "Discover note names" --done-when "Filtering and unchanged inputs are checked" --source PLAN.md#N1 --pri P1 --scope src/ --scope tests/
   python3 .sako/sako.py add "Print sorted names" --done-when "The agreed sample matches" --source PLAN.md#N2 --after T-1 --scope preview.py
   ```

   Use the IDs the command returns. Prerequisites release when all are done;
   `next` ranks what is ready by priority, then row order.
3. **Handle repeat or changed input.** The same source, task, completion
   condition, scope and prerequisites reuse the existing ID, completed work
   included. A new `--pri` updates an unfinished task's priority and says so.
   Any other difference refuses instead of replacing work; inspect the source and
   the row, then reconcile deliberately. When an item changed after its task
   was done, record the new work as a new task with its own source, such as
   `PLAN.md#N1-v2`; the done row stays as history. Without `--source`, every `add`
   is a new task.
4. **Return the result.** Before starting, check that the source item still
   applies. When returning the result edits a file the check covers, such as
   ticking the item's box, make that edit before `verify` and `close`; an edit
   after close needs a fresh check. After close, compare the result with the
   upstream acceptance and return the evidence through the project's authorized
   procedure, or report that update as pending. Local completion does not complete
   a larger upstream item.

Source references are exact identifiers; the runtime does not fetch, interpret,
watch or synchronize them. When a source changes, you judge what it means.

## No planner? Start here

Read this when no planner or work source is selected and the direction is unclear,
or nothing is ready. Begin with the owner's request and only as much evidence as
the next useful action needs: instructions, local changes, entry points, notes and
checks. Code shows what exists; it does not decide what the owner wants. An old
checklist is evidence, not an order.

| Starting point | Establish first | Preserve |
|---|---|---|
| An idea, little or no code | Who it helps, the first useful outcome, a constraint that changes the approach | The owner's uncertainty; direction can be provisional |
| Code with notes, TODOs or a backlog | Which source governs current intent; whether the work is still needed | Existing authority, IDs, conventions, unrelated changes |
| Code with little explanation | How the relevant behavior works today, what is unknown, what the owner wants next | Working behavior until a change is intended |

Ask a small, concrete question only when the answer changes the outcome, scope or
acceptance; find file locations and commands yourself. Record consequential
answers and label assumptions. Put just enough in an existing README or brief to
answer: the **direction** (who it serves, the current outcome), the **starting
point** (what works, what is unknown), the **boundaries**, the **next action**, and
what counts as **success**. A short paragraph and one task are often enough; a PRD,
phases or a full backlog are optional.

If intent or feasibility is too uncertain to build, add a bounded investigation:
the question, the evidence to collect, and when to stop. "Run the import with a
synthetic sample and record the accepted columns, the failure and the next
decision" is actionable; "try things until clear" is not. With no automated check,
leave `verify_command` null and say how the result will be judged; never add an
always-passing command.

When nothing is ready or evidence changes the plan:

| The evidence says | Next move |
|---|---|
| The outcome is unfinished and nothing is ready | Prepare the smallest justified action |
| Work is held by a session or a prerequisite | Inspect the owner or blocker; continue your own claim or leave a precise handoff |
| A decision is missing | Record the question and ask the owner instead of inventing direction |
| Feedback invalidates planned work | Revise the task or park it with a reason, keeping IDs and references |
| The outcome is satisfied | Report completion; an empty queue is a valid finish |

Do not create tasks to keep agents busy. Bring anything that would widen the agreed
outcome to the owner first.

## Pause, handoff and takeover

Read this when you pause unfinished work, leave, resume, or meet a stale claim.
Before pausing a claimed task, write `handoff.md` in the folder `claim` printed,
`.sako/work/T-<n>-<slug>/`: where the task stands, what landed, what is left, the
next step, and traps. Other working notes can sit beside it; plans stay in their
own home, linked. The folder is found by task ID and is never checked content or an
unrecorded change. On close, move its lasting facts to their real home and delete
it.

A background subagent that a message can resume is paused, not finished. When the
next step is the owner's, close what you finished. The owner's act, or deferred
work, is its own row, parked by hand with its reason; whoever finishes what it
waits for sets it to `open`, then claims and closes it.

A claim left on purpose for another session reads as stale to every other session
until it is taken over. Two rows avoid that, the second listing the first in
After.

A new session reads the records, the handoff and the relevant Git diff before it
continues. For an ended session, `status --session <its ID>`, not its marker, shows
its rows and this checkout's uncommitted changes but not its commits. A live owner
cannot be replaced. For a stale claim, inspect its work, then take it over with
`claim T-1 --session <new-id> --takeover "reason"` or park it. Until a resume or
takeover, the holder's uncommitted files count against sessions started before
they changed, even once parked. Take over the holder's other overlapping claims
too, one after another, as the claim line asks. A takeover moves the claim, not the
previous holder's uncommitted files, which count once they differ from your start
record.

Leaving releases nothing, so close finished work and hand off the rest first. A
resume keeps the session ID, marker key and claims, but after an end, as when the
client exits, it takes a new start record and the gate counts only later changes.
In Claude Code, `/clear` and a fork start a new session with its own ID.

Presence judges liveness by the host process where that process is visible, and
on Linux and WSL a restart ends it. A client's sandbox (Codex runs agent commands
in one) hides the host's processes, so there, and for a session started inside
one, presence counts as live for 24 hours after its last `start`, marked
unverified. A claim held by an unverified session can be taken over once you have
checked that it stopped. Without `end`, presence stays until its host process
exits or its 24 hours pass. Do not end a peer's presence without confirming it
stopped. `SAKO_AGENT_PID` can name a long-lived host process, never a temporary
shell.

An interrupted close can leave the same row in both records. Repeat the exact
close command with the same evidence and session. Conflicting closed content is
refused; never delete completed evidence blindly.

## Working beside other sessions

Read this when another session or a subagent works in this repository. Every
worktree uses the main checkout's `.sako/`, so sessions share one ledger, one
numbering and one lock. A claim refuses a live owner and a scope that overlaps
another session's claim. The lock serializes `add`, `claim` and `close` only, not
application edits, hand edits of the records, Git's index or other clones.
Keep scopes disjoint, coordinate direct record edits, and stage explicit paths.
SAKO does not schedule agents or merge their edits.

In Claude Code, as in Codex, a subagent's shell carries its parent's session ID, so
a command there without `--session` acts as the parent. A helper inside the
parent's claim runs no SAKO command, or uses the parent's ID as the same holder and
never runs `end`, which would end the parent's presence; the parent's close
evidence names it with its commits. An agent holding its own claim gets its own ID
in its brief and passes `--session` on every command, `end` included. Claude Code
sends a subagent's stop to SubagentStop, which SAKO does not wire, and in use
subagents ran no session hooks, so a worktree only they use needs no `init`.

## The gate and checks in detail

Read this when a gate finding or a check result needs explaining. `verify` runs the
configured argument array at the top of the checkout; use `["bash",
"scripts/check.sh"]` for shell features. It stamps a pass only when the checked
content and the command stay unchanged during the run. The last pass stays while
the check runs; a finished run that earns no stamp deletes it. Output goes to
`verify.log` beside the stamp, and a failure's also to `verify-failed.log`. The
stamp covers the content, paths, symlink text and executable bit of unignored
files under `verify_paths`; an empty list covers everything outside `.sako/`,
documentation included. Name product subtrees if documentation-only work should
not invalidate the check. Stamps are local, one per worktree; rerun `verify` after
a fresh clone.

The gate judges the checkout it runs in, for a hook the session's current
checkout. It counts no file under `.sako/`, so a scope there only locks paths
against other claims. Work in another linked worktree, even a nested one, counts
here once merged; until then, `check --gate --session <id>` run there judges it.

**Recorded** counts files edited since the session started or committed since then,
deletions and both sides of a rename; a file already dirty at start counts only when
it changes again. A live peer's claimed scope in this checkout is not counted, with
a note, nor are uncommitted files in a task it closed. A closed task no longer
covers an uncommitted file changed in a later minute than its close. A commit is not
counted when a task another session closed on or after the commit's day, in local
time, covers it. Without a start record, every uncommitted change counts and commits
go unchecked; the gate says so. **Proven:** while a claim you still hold covers
changed checked files, a stale check is that claim's work in progress, and a close
whose receipt shows a pass stays proven. Records work, a task whose every scope
entry lies in `.sako/`, closes without a fresh check while only others moved
checked content since your start, by edits left uncommitted before it, even once
committed, or in the scope of a task a live peer in this checkout claimed or
closed. Its receipt then says "no automated check (records only)", and Proven
skips it while that holds. Any other commit, merge, pull or checkout here counts as
yours. **Committed** skips files inside a claim you still hold; its fix applies only
when the owner allows commits, otherwise say the work awaits approval.

The first line of `check --gate` is its verdict, and its exit code decides. Two
advisories never block: a claimed, unfinished task without a `handoff.md`, and a
closed task whose handoff folder remains. A clear stop through the hook stays
silent. A hook fails open on a bug, and a repeated stop passes, so the gate never
traps a session. Exit codes: 0 success, 1 findings or a failed check, 2 a
gate needing attention or a usage error, 3 an actionable refusal, 4 a bug.

## Install, update and remove

Read this when installing, updating or removing the kit, or wiring a worktree. Run
`uvx sako init`, or `python3 <checkout>/sako.py init` from a reviewed checkout,
anywhere in the project's Git repository. It needs no flags. The runtime, this
method, `install.json`, `config.json` and an empty `work/TASKS.md` land in the main
checkout's `.sako/`, which is listed in `.git/info/exclude` with the client files
`init` writes, so nothing tracked changes. The project's instructions stay
untouched.

`init` wires SessionStart, Stop and SessionEnd hooks for each client it finds a
sign of: the Claude Code or Codex session running it, `.claude/` or `CLAUDE.md`
for Claude Code, `.codex/` for Codex, and `AGENTS.md` for both. It prints the choice and why
and records it; later runs and `--update` keep it. `--client claude`, `--client
codex` (repeat for both) or `--client none` replaces it; none means explicit
`start`, `check --gate` and `end`, also the route for clients without an adapter.
With no sign of a client, a later `init` detects one that appears.
The shared skill always installs in `.agents/skills/sako/`, and Claude Code gets a
copy in `.claude/skills/sako/`. Codex runs project hooks only after you trust them;
`status` says when each client's hook last ran. A tracked settings file is never
changed; `init` says what to add by hand. A client that reads neither hooks
nor skills can be pointed to `.sako/SAKO.md` in its instructions.

Configure the check in `.sako/config.json`: `verify_command` as an argument array,
such as `["python3", "-B", "-m", "unittest"]`, and `verify_paths`. In a new linked
worktree, run `init` there. It wires that worktree for the clients the main
checkout chose and, for Codex, a writable root for the main `.sako/`, which Codex
reads once it trusts the project. The records stay in the main checkout.

Two footprints put the records in Git. `init --repo [url]` gives them their own
repository inside `.sako/`, still out of the code's. Its `.gitignore` leaves out
the kit files and `state/`, so it tracks `config.json` and `work/`. With a URL, a
checkout without records clones them, and existing records get it as `origin`;
records on both sides refuse. `init --shared` commits the records with the code. It
takes `.sako/` out of `.git/info/exclude`, prints the commit command, and prints a
line to add to your instructions so an agent on a fresh clone finds SAKO. Each
branch then keeps its own copy, so a linked worktree uses its own `.sako/`, and one
on a branch without a copy refuses. It refuses while `.sako/.git` exists and names
the move. SAKO commits nothing; the gate's Committed fix stages the two ledger
files whole, so it can carry another session's pending rows, and the records line
of `init` and `status` adds `.gitignore` and `config.json`. Other files you keep in
`.sako/` stay yours; only a first share commits the whole folder. `install.json`
records the footprint, and a disagreement with Git is a finding. To go back to
local, run `remove`, then `init`, which names any Git command to run first; the
records stay byte for byte. In the repo footprint `remove` keeps `.sako/.git` and
warns about commits that exist only there; the command `init` then names moves that
history out of `.sako/`, beside the project, where it stays until you delete it.

To update, review the new release and run its `init --update` with no active work,
such as `uvx sako@latest init --update` (plain `uvx sako` can reuse a cached copy).
It also refreshes SAKO's hooks in linked worktrees that have them. Modified kit
files refuse; an unmodified one the new choice or release no longer needs is
deleted. `python3 .sako/sako.py remove` in the main checkout removes unmodified kit
files, SAKO's hook entries in every worktree and the disposable state;
`config.json` and `work/` stay. The client choice goes with `install.json`, so a
later `init` detects the clients again. The copy in a project cannot seed another
project.
