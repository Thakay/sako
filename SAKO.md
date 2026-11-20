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
   `start --session <id>` with an ID unique to this conversation. Pass the same ID
   to every command; in Claude Code and Codex, a command without `--session` uses
   the client's session ID, the one the hook used.
2. **Choose and claim.** Follow the owner's priority. `next` names the task to
   start and why. For new work, run
   `add "task" --done-when "observable result" --scope src/`, then
   `claim T-1 --session <id>` with the returned ID. Keep scopes narrow and widen
   them with `edit` before the work grows.
3. **Work and check.** Use the project's own tools and checks. When a check is
   configured, `verify` runs it and records a fingerprint of the checked content.
   For discovery or a manual check, keep the actual findings or procedure and
   result. A passing suite alone is not acceptance, so inspect the requested
   behavior.
4. **Close and commit.** `close T-1 --session <id> --evidence "result and proof"`
   moves the row to the done record with a receipt: your evidence, then the
   claim's marker and time, the close time, and what the check said and at which
   commit. State the outcome and its limits, not just "done", and cite proof that
   outlives the session, never `/tmp` or the handoff folder. An unmet or waived
   part of the completion condition becomes a new task; the receipt names both.
   Commit the task's work before the next task writes its files, within the
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
claim, a second writer in your checkout and a footprint finding also block.
Claimed, unfinished work, advisories and notes, such as another session's stale
claim, never block. A clear gate means these rules passed, not that the owner
accepts the result. With a null `verify_command`, the receipt says "no automated
check" in place of a pass; do not call that work verified.

## Records

| Information | Home |
|---|---|
| Direction, purpose, acceptance | The project's instructions, brief or README |
| Active work and ownership | `.sako/work/TASKS.md` |
| Completed work with receipts | `.sako/work/DONE.md`, created on first close |
| A paused task's handoff and notes | `.sako/work/T-<n>-<slug>/`, printed by `claim` |
| Unresolved decisions | The affected task or the project's context, with the question, any assumption and who decides |
| Where things stand, for the owner | The planner for its items, `next` for ready and waiting rows, `status` for live peers; a progress tracker names task IDs and never copies their state |

In a task row, `Pri` is P1, P2 (empty reads P2) or P3. Status's first word is `open`
(empty reads open), `parked` with its reason, or the claiming session's marker.
`After` lists prerequisite IDs. Scopes are repository-relative path prefixes; `.`
covers everything. IDs are never reused, so park a dropped row instead of deleting
it. Columns are read by name, in any order, and columns you add are kept. Keep a
cell on one line, without literal pipes. Change Scope, After, Done when and Status
with `edit`, Task and Pri by hand; commands change only their own rows.

Put each discovery where the next reader needs it, such as an invariant in the test
that enforces it, a product choice in the project's context, or a trap in the task's
handoff, and point to it from the receipt.

## Read when needed

Stop here; open a section below only when its situation applies:

| Situation | Section |
|---|---|
| Work comes from a plan, checklist, issue or planning skill | Task intake |
| No planner is selected, direction is unclear, or nothing is ready | No planner? Start here |
| Pausing, leaving, resuming, or a stale claim | Pause, handoff and takeover |
| Another session works here, or you hand work to subagents | Working beside other sessions |
| A gate finding or check result needs explaining | The gate and checks in detail |
| Installing, updating, removing, or wiring a worktree | Install, update and remove |

## Task intake

Read this when work comes from the project's plan, checklist, issue or planning
skill. Turn the selected items into rows without replacing the planner or copying
its backlog. The
[intake recipes](https://github.com/Thakay/sako/blob/main/docs/intake-recipes.md)
show common sources.

1. **Name the source.** Give each local action a stable reference to its item, such
   as `PLAN.md#N1`, for `--source`, and reuse it exactly on later intake.
2. **Record or reuse it.** Put the outcome in the task text and the completion
   condition in `--done-when`. A completion condition names what the closing
   session can observe; a later verdict, such as a reviewer's or the owner's, goes
   on the row that waits for it. Pass the files with `--scope`, including a source
   document the task updates, the priority with `--pri` and prerequisites with
   `--after`, using the IDs `add` printed.
3. **Handle repeat or changed input.** Repeating an intake reuses its task,
   completed work included, and a new `--pri` updates an unfinished task's
   priority. A changed item refuses, so reconcile the row deliberately, and give new
   work on an item whose task is done a new source, such as `PLAN.md#N1-v2`.
4. **Return the result.** Before starting, check that the source item still
   applies. Make an edit that returns the result, such as ticking the item's box,
   before `verify` and `close`; an edit after close is new work. A parked row whose
   reason waits on a plan item is closed or reopened in the step that ticks that
   item. After close, compare the result with the upstream acceptance, then return
   the evidence through the project's authorized procedure or report that update as
   pending. Local completion does not complete a larger upstream item.

SAKO never fetches, watches or syncs a source, so you judge what a change in one
means.

## No planner? Start here

Read this when no planner or work source is selected and the direction is unclear,
or nothing is ready. Begin with the owner's request and only as much evidence as
the next useful action needs. Code shows what exists, not what the owner wants, and
an old checklist is evidence, not an order.

For an idea, establish who it helps, the first useful outcome and any constraint
that changes the approach; direction can stay provisional. For code with notes,
TODOs or a backlog, establish which source governs current intent and whether the
work is still needed, and keep its IDs, conventions and unrelated changes. For code
with little explanation, learn how the relevant behavior works today and what the
owner wants next, and keep working behavior until a change is intended.

Ask a small, concrete question only when the answer changes the outcome, scope or
acceptance; find file locations and commands yourself. Record consequential answers,
label assumptions, and put just enough in an existing README or brief to give the
**direction**, the **starting point**, the **boundaries**, the **next action** and
what counts as **success**. A short paragraph and one task are often enough. When
intent or feasibility is too uncertain to build, add a bounded investigation that
names the question, the evidence to collect and when to stop. With no automated
check, leave `verify_command` null and say how the result will be judged; never add
an always-passing command.

When nothing is ready, `next` says why. Ask the owner for a missing decision rather
than invent direction, and revise or park planned work that feedback invalidates,
keeping IDs and references. Create no tasks to keep agents busy, and bring
anything that widens the agreed outcome to the owner first.

## Pause, handoff and takeover

Read this when you pause unfinished work, leave, resume, or meet a stale claim.
Before pausing a claimed task, write `handoff.md` in the folder `claim` printed,
with where the task stands, what landed, what is left, the next step, and traps. The
folder is never checked content.

A background subagent that a message can resume is paused, not finished. When the
next step is the owner's, close what you finished. The owner's act, or deferred
work, is its own row, parked with its reason (`edit T-1 --park "reason"`); whoever
finishes what it waits for opens it (`edit T-1 --open`), then claims and closes it.
A claim left on purpose for another session reads as stale to every other session
until it is taken over. Two rows avoid that, the second listing the first in
After.

To release a claim no agent will resume, open or park it with `edit`. A resume keeps
the session ID, marker key and claims, but after an end, as when the client exits, or
a start in another checkout, it takes a new start record and the gate counts only
later changes. In Claude Code, `/clear` and a fork start a new session with its own
ID.

A session counts as live for two hours, its lease, after its last hook or command
that acts as it, on every platform. `status`, `next` and a plain `check` also show
other sessions, so they renew nothing. `end` or the end hook ends it. `start` deletes
the presence of a session unseen for a week, so that session takes a new start record
if it comes back.

Before a takeover, read the handoff and the Git diff; `status --session <its ID>`,
not its marker, shows an ended session's rows. After the holder's lease, take the
claim over with `claim T-1 --takeover "reason"`. Within the lease, only once the
owner confirms that session stopped, end it with `end --session <its ID>`, then take
the claim over. A takeover moves the claim, not the previous holder's uncommitted
files, so commit what belongs to the task yourself. An interrupted close can leave
the same row in both records; repeat the close with the same session, and never
delete completed evidence blindly.

## Working beside other sessions

Read this when another session works in this repository or you hand work to
subagents. Outside the shared footprint, all worktrees share the main checkout's
`.sako/`, with one ledger, one numbering and one lock. Work runs one of three ways:

- **Solo.** One session in one checkout. Its Stop hook gates it.
- **Peers.** Two or more sessions that each run SAKO, each under its own claims in
  its own worktree, the main checkout being one. A session in another client is
  always a peer, since that client's hooks run SAKO for it. A claim refuses a live
  holder and a scope that overlaps another session's claim. A checkout holds one
  check result and one set of uncommitted files, so it has one writer, the live
  session that last started there and holds a product claim. A second session's
  product claim there is refused with the Git command for a worktree of its own;
  `uvx sako init` there wires its hooks. A claim scoped inside `.sako/` makes no
  writer, so records work shares a checkout. After a merge, the gate asks for a fresh
  check.
- **A coordinator with a fleet.** One session coordinates and holds the claims. Its
  workers are subagents in its own client. They run no SAKO command, and nothing
  gates them. Paste each worker its row as the brief and tell it to run no SAKO
  command. Read what it returns, then run the check and close. The Stop hook gates
  the coordinator. SAKO does not see the workers, so keep them apart yourself, with
  one writing worker per checkout at a time or a worktree each that you merge.

Workers run no SAKO command because a subagent's shell carries its parent's session
ID, so a command there without `--session` acts as the parent, and its `end` would
end the coordinator's presence. In use, subagents ran no session hooks, so a
worktree only they use needs no `init`. Start with one worker. The most writers at
once is the number of ready rows whose scopes do not overlap; claim the row `next`
names and run `next` again, since a row whose scope overlaps a claim waits. Workers
that only read can fan out freely. You read every diff, so you are the cap. When you
close work you did not build, name its builder in the receipt.

## The gate and checks in detail

Read this when a gate finding or a check result needs explaining. The stamp of
`verify` covers the content, paths, symlink text and executable bit of unignored
files under `verify_paths`. An empty list covers everything outside `.sako/`,
documentation included, so name product subtrees if documentation-only work should
not invalidate the check. A pass in another checkout counts here on identical
checked content and command. A close lists each fresh named check in its receipt,
and a named check is evidence, never a gate rule.

The gate judges the checkout it runs in, for a hook the session's current
checkout. It counts no file under `.sako/`, so a scope there only locks paths
against other claims. Work in another linked worktree, even a nested one, counts
here once merged; until then, a gate run there judges its uncommitted files, and its
commits if you last started there.

**Recorded** counts files edited since the session started or committed since then,
deletions and both sides of a rename; a file already dirty at start counts only when
it changes again. While another live session is this checkout's writer and you are
not, the changes here are its to answer for. A change that a task another session
closed since you started covers does not count either. `close` seals each
uncommitted file in the task's scope that counts as your change. The closed task
covers a committed file by its scope and an uncommitted one while it holds the
sealed bytes, so a stash and its pop change nothing.

**Proven** wants a fresh check once checked content moved since your start. A close
whose receipt shows the gate's check pass stays proven while a claim you still hold
covers moved files and your other moved files match the last pass here, or while
only others moved them, meaning edits left uncommitted before your start or this
checkout's writer when you are not. Any other commit, merge, pull or checkout counts
as yours. Records work, scoped only inside `.sako/`, closes without a fresh check on
the same terms; its receipt says "no automated check (records only)".
**Committed** skips files inside a claim you still hold.

The first line of `check --gate` is its verdict, and its exit code decides. Exit
codes: 0 success, 1 findings or a failed check, 2 a gate needing attention or a
usage error, 3 an actionable refusal, 4 a bug.

## Install, update and remove

Read this when installing, updating or removing the kit, or wiring a worktree. Run
`uvx sako init`, or `python3 <checkout>/sako.py init` from a reviewed checkout, in
the project's main checkout. It needs no flags, never edits project instructions,
and in the default local footprint changes no tracked file. It wires hooks for each
client it finds a sign of. `--client claude`, `--client codex` (repeat for both) or
`--client none` replaces that choice for later runs too. In a new linked worktree,
run `init` there to wire its hooks.

Set the check in `.sako/config.json`. `verify_command` is an argument array run at
the top of the checkout, such as `["python3", "-B", "-m", "unittest"]`, or
`["bash", "scripts/check.sh"]` for shell features. `verify_paths` names what it
covers, and `checks` maps names, such as `browser`, to more arrays that
`verify NAME` runs.

The records' footprint is local by default, out of Git. `init --repo [url]` gives
them their own Git repository inside `.sako/`, and `init --shared` keeps them in the
code's repository, one copy per branch. Both print the command that commits the
records. To go back to local, run `remove`, then `init`, which names any Git command
to run first.

To update, review the new release and run its `init --update` with no active work,
such as `uvx sako@latest init --update`. `python3 .sako/sako.py remove` in the main
checkout removes the kit and its hooks in every worktree, and keeps `config.json` and
`work/`.
