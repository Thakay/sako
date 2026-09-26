# Working with SAKO

The SAKO method, a task ledger and finish gate for coding agents, installed in this
project. Project instructions and the owner's choices come first; they also name
the planner or source of work. This file holds no project state.

Use SAKO for project tasks here, code and documentation changes and bounded
investigations alike, without being asked each time. Questions and discussion need
no task record. Run `python3 .sako/sako.py <command>` (`python` on Windows) from
anywhere in the repository; from a linked worktree, use the command line the start
context prints.

## Daily loop

1. **Start.** The session-start hook prints the context and your session ID.
   Without it, run `start --session <id>` with an ID unique to this conversation.
   Pass the same ID to every command; in Claude Code and Codex, a command
   without `--session` uses the client's session ID, the one the hook used.
2. **Choose and claim.** Follow the owner's priority. `next` names the task to
   start and why, what else is ready, and what waits. For new work, run
   `add "task" --done-when "observable result" --scope src/`, then
   `claim T-1 --session <id>` with the returned ID. Keep scopes narrow and widen
   them in the ledger before the work grows. If nothing fits, return to the
   selected planner; the table under **Read when needed** covers the rest.
3. **Work and check.** Use the project's own tools and checks. Run `verify` when a
   check is configured; it records a fingerprint of the checked content. For
   discovery or a manual check, keep the actual findings or procedure and result.
   A passing suite alone is not acceptance: inspect the requested behavior.
4. **Close and commit.** `close T-1 --session <id> --evidence "result and proof"`
   moves the row to the done record with a receipt: your evidence, then the
   closing marker, the date, and what the check said. State the outcome and its
   limits, not just "done". Commit the task's work within the owner's
   authorization, staging explicit paths. SAKO never commits, pushes or deploys.
5. **Finish.** Run `check --gate --session <id>` (the Stop hook runs it too) and
   fix what it names, or report what remains. If you ran `start` yourself, run
   `end --session <id>` last. With hooks, the client ends the session; do not run
   `end`, since the Stop gate needs your start record. Report the result, the
   evidence and the next decision.

You can stop incomplete: keep the claim and write its handoff. If commits are not
authorized, report the reviewed work as uncommitted. A satisfied goal needs no
new task.

## Done means

The gate checks this session against three rules. Each finding names its rule and
ends with the command or edit that fixes it.

- **Recorded:** every file the session changed belongs to a task it claimed or closed.
- **Proven:** a task closed after checked content changed has a passing check.
- **Committed:** closed work is committed; in the repo and shared footprints, its records too.

Pausing is free: claimed, unfinished work passes. Two advisories never block: a
claimed, unfinished task without a `handoff.md`, and a closed task whose handoff
folder remains. A clear gate means these rules passed, not that the owner accepts
the result. A null `verify_command` means no automated evidence: close with the
manual check's procedure and result, and the receipt says "no automated check". It
never becomes a pass; do not call that work verified.

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
`ID | Task | Done when | Receipt | Scope | Source`. IDs are never reused. Columns are
read by name, in any order, and columns you add are kept. Keep a cell on one line,
without literal pipes. Commands change only their own rows; hand edits are fine but
not locked, so coordinate them with other sessions.

The records live where `status` says: local (`.sako/` is listed in
`.git/info/exclude`, so they are not in Git, and `git clean -x` deletes them), in
their own repository inside `.sako/` (repo), or committed with the code (shared,
one copy per branch).

Put each discovery where the next reader needs it: an invariant in the code or test
that enforces it, a product choice in the project's context, progress and traps in
the task's handoff. Reference that home from the receipt rather than copying it.

## Read when needed

The following sections apply only in their situation:

| Situation | Section |
|---|---|
| Work comes from a plan, checklist, issue or planning skill | Task intake |
| No planner is selected, direction is unclear, or nothing is ready | No planner? Start here |
| Pausing, resuming, or a stale claim | Pause, handoff and takeover |
| Another session works in this repository | Working beside other sessions |
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
   Any other difference refuses instead of replacing work: inspect the source and
   the row, then reconcile deliberately. When an item changed after its task was
   done, record the new work as a new task with its own source, such as
   `PLAN.md#N1-v2`; the done row stays as history. Without `--source`, every `add`
   is a new task.
4. **Return the result.** Before starting, check that the source item still
   applies. When returning the result edits a file the check covers, such as
   ticking the item's box, make that edit before `verify` and `close`: an edit after
   close needs a fresh check. After close, compare the result with the upstream
   acceptance and return the evidence through the project's authorized procedure,
   or report that update as pending. Local completion does not complete a larger
   upstream item.

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

Read this when you pause unfinished work, resume, or meet a stale claim. Before
pausing a claimed task, write `handoff.md` in the folder `claim` printed,
`.sako/work/T-<n>-<slug>/`: where the task stands, what landed, what is left, the
next step, and traps. Other working notes can sit beside it; plans stay in their
own home, linked. The folder is found by task ID and is never checked content or an
unrecorded change. On close, move its lasting facts to their real home and delete
it. `start` and `status` list open tasks' folders.

A new session reads the records, the handoff and the relevant Git diff before it
continues; `status --session <id>` shows the changes, ownership, evidence and
commits since that session began. A live owner cannot be replaced. For a stale
claim, inspect its work, then run `claim T-1 --session <new-id> --takeover "reason"`.

Each conversation needs its own session ID, even when several share one host
process; a resumed conversation reuses its ID. A subagent's shell carries its own
Claude Code session ID, so it passes the parent's `--session` explicitly.

Presence judges liveness by the host process where that process is visible. A
client's sandbox (Codex runs agent commands in one) hides the host's processes, so
there, and for a session started inside one, presence counts as live for 24 hours
after its last `start`, marked unverified. A claim held by an unverified session
can be taken over with a reason once you have checked that it stopped; the claim
says its holder was unverified. A session that ran `start` itself runs `end
--session <id>` when it finishes; otherwise its presence stays until its host
process exits or its 24 hours pass. Do not end a peer's presence without
confirming it stopped. `SAKO_AGENT_PID` can name a long-lived host process, never a
temporary shell.

An interrupted close can leave the same row in both records: repeat the exact
close command with the same evidence and session. Conflicting closed content is
refused; never delete completed evidence blindly.

## Working beside other sessions

Read this when another session works in this repository. Every worktree uses the
main checkout's `.sako/`, so sessions share one ledger, one numbering and one lock.
A claim refuses a live owner and a scope that overlaps another session's claim.
The lock serializes `add`, `claim` and `close` only: not application edits, hand
edits of the records, Git's index or other clones. A push is a record, not a lock.
Keep scopes disjoint, coordinate direct record edits, and stage explicit paths.
SAKO does not schedule agents or merge their edits.

## The gate and checks in detail

Read this when a gate finding or a check result needs explaining. `verify` runs the
configured argument array at the top of the checkout; use `["bash",
"scripts/check.sh"]` for shell features. It stamps a pass only when the checked
content and the command stay unchanged during the run; a failure deletes old
evidence. The stamp covers the content, paths, symlink text and executable bits of
unignored files under `verify_paths`; an empty list covers everything outside
`.sako/`, documentation included. Name product subtrees if documentation-only work
should not invalidate the check. Dependencies and services need another check by
judgment. Stamps are local, one per worktree: rerun `verify` after a fresh clone.

**Recorded** counts files edited since the session started or committed since then,
deletions and both sides of a rename; a file already dirty at start counts only when
it changes again. A live peer's claimed scope in the same checkout is not counted,
with a note. A linked worktree nested in the checkout, such as one a harness makes
for a subagent, is another checkout: its work counts once merged. Without a start
record, every uncommitted change counts and commits go unchecked; the gate says so.
**Proven:** while a claim you still hold covers changed checked files, a stale check
is that claim's work in progress, and a close whose receipt shows a pass stays
proven. **Committed** skips files inside a claim you still hold; its fix applies
only when the owner allows commits, otherwise say the work awaits approval.

`check --gate` prints advisories under a clear result, and a blocked stop lists
them after its problems; a clear stop stays silent. Hooks fail open on their own
errors and on a repeated stop, so the gate never traps a session. Exit codes: 0
success, 1 findings or a failed check, 2 a gate needing attention, 3 an actionable
refusal, 4 a bug.

## Install, update and remove

Read this when installing, updating or removing the kit, or wiring a worktree. Run
`uvx sako init`, or `python3 <checkout>/sako.py init` from a reviewed checkout,
anywhere in the project's Git repository. It needs no flags. The runtime, this
method, `install.json`, `config.json` and an empty `work/TASKS.md` land in the main
checkout's `.sako/`, which is listed in `.git/info/exclude` with the client files
`init` writes, so nothing tracked changes. The project's instructions stay
untouched: the start context names the method, the records and the command line.

`init` wires SessionStart, Stop and SessionEnd hooks for each client it finds a
sign of: the Claude Code or Codex session running it, `.claude/` or `CLAUDE.md`
for Claude Code, `.codex/` for Codex, and `AGENTS.md` for both. It prints the choice and why
and records it; later runs and `--update` keep it. `--client claude`, `--client
codex` (repeat for both) or `--client none` replaces it; none means explicit
`start`, `check --gate` and `end`, also the route for clients without an adapter.
With no sign of a client, a later `init` detects one that appears, including a
Claude Code or Codex session running it.
The shared skill always installs in `.agents/skills/sako/`, and Claude Code gets a
copy in `.claude/skills/sako/`. Codex runs project hooks only after you trust them;
`status` says when each client's hook last ran. A tracked settings file is never
changed; `init` says what to add by hand. A client file whose folder leads
outside the repository is skipped with a note. A client that reads neither hooks
nor skills can be pointed to `.sako/SAKO.md` in its instructions.

Configure the check in `.sako/config.json`: `verify_command` as an argument array,
such as `["python3", "-B", "-m", "unittest"]`, and `verify_paths`. In a new linked
worktree, run `init` there: it wires that worktree for the clients the main
checkout chose and, for Codex, a writable root for the main `.sako/`, which Codex
reads once it trusts the project. The records stay in the main checkout.

Two footprints put the records in Git. `init --repo [url]` gives them their own
repository inside `.sako/`, still out of the code's: its `.gitignore` leaves out the
kit files and `state/`, so it tracks `config.json` and `work/`. With a URL, a
checkout without records clones them, and existing records get it as `origin`;
records on both sides refuse. `init --shared` commits the records with the code: it
takes `.sako/` out of `.git/info/exclude`, prints the commit command, and prints a
line to add to your instructions so an agent on a fresh clone finds SAKO. Each
branch then keeps its own copy, so a linked worktree uses its own `.sako/`, and one
on a branch without a copy refuses. It refuses while `.sako/.git` exists and names
the move. SAKO commits nothing; the gate's Committed rule asks for the records'
commit with its exact command. `install.json` records the footprint, and a
disagreement with Git is a finding. To go back to local, run `remove`, then `init`,
which names any Git command to run first; the records stay byte for byte. In the
repo footprint `remove` keeps `.sako/.git` and warns about commits that exist only
there; the command `init` then names moves that history out of `.sako/`, beside the
project, where it stays until you delete it.

To update, review the new release and run its `init --update` with no active work,
such as `uvx sako@latest init --update` (plain `uvx sako` can reuse a cached copy).
Modified kit files refuse; an unmodified one the new choice or release no longer
needs is deleted. `python3 .sako/sako.py remove` in the main checkout
removes unmodified kit files, SAKO's hook entries in every worktree and the
disposable state; `config.json` and `work/` stay, and deleting `.sako/` leaves no
trace. The client choice goes with `install.json`, so a later `init` detects the
clients again. The copy in a project cannot seed another project.
