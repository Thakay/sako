# Glossary

The words SAKO uses, in one place. The other pages use them in this sense.

| Word | Meaning here |
|---|---|
| Check | The project's own command, such as its tests, set as `verify_command` in `.sako/config.json`; `verify` runs it |
| Check-in | Register an existing agent session with SAKO and get its context, through a session-start hook or the `start` command |
| Claim | A session's ownership of a task, in the task's Status. A live claim cannot be replaced |
| Client | The program an agent runs in. SAKO wires hooks for Claude Code and Codex; with others, the agent runs the commands |
| Completion condition | The observable result that makes a task done: its "Done when" cell |
| Coordinate | Keep sessions someone else started from colliding, with claims, scope checks, one writer per checkout and a shared lock, in one repository and its worktrees |
| Finish gate | The check at a session's stop against three rules, Recorded, Proven and Committed, and against faults in the records. It refuses a stop once and names the fix; findings about other sessions are only notes |
| Footprint | Where the records live: **local** (out of Git, the default), **repo** (their own Git repository inside `.sako/`) or **shared** (committed with the code, one copy per branch) |
| Handoff | A paused task's notes, in `.sako/work/T-<n>-<slug>/handoff.md` |
| Hook | A client's automatic call into SAKO when a session starts, stops or ends |
| Kit | The files SAKO owns and updates: the runtime `.sako/sako.py`, the method `.sako/SAKO.md`, `.sako/install.json` and the skill copies |
| Last pass | What each checked file held at a checkout's last passing check, kept in its state beside the stamp. A failing run leaves it, and a close there whose receipt shows a pass, even another checkout's, replaces it. It never makes the check fresh; while a claim you still hold covers a file you moved, the Proven rule compares your other moved files with it |
| Lease | The two hours a session counts as live after its last hook or command that acts as it; `status`, `next` and a plain `check` renew nothing, and `end` or the end hook ends it |
| Ledger | The task records in `.sako/work/`: `TASKS.md` for active work, `DONE.md` for finished work |
| Marker | A session's name in claims and receipts, such as `sk-0115-7a281dacac30`, made of the month and day of its start and a key derived from its session ID; the start context prints yours |
| Method | `SAKO.md`, the instructions agents follow in a project |
| Owner | The person the agent works for, who sets direction, allows commits and accepts results |
| Parked | A task held on purpose, with its reason in Status; `next` skips it |
| Peer | Another live session that runs SAKO in this repository, shown with its claims and checkout in `start` and `status` |
| Planner | Whatever decides what to build: a spec, a plan, an issue tracker, or the method's default planner |
| Presence | SAKO's record of which sessions are live, which protects live claims |
| Receipt | What `close` writes for a finished task: the completion condition, the evidence, the claim's marker and time, the close time and what the check said |
| Scope | The path prefixes a task may change |
| Seal | A fingerprint of each uncommitted file in a task's scope that counts as the session's change, which `close` keeps in the checkout's state. The closed task covers such a file while it still matches. The close is listed there even when it seals no file, which tells Recorded that the task closed in that checkout |
| Session | A conversation opened in an agent client; `start` registers it with SAKO and `end` removes its presence record |
| Source | The exact reference to the planner item a task came from, such as `PLAN.md#N1` |
| Stamp | The record of a passing check on a fingerprint of the checked content |
| Takeover | Claiming a task whose holder is no longer live, with a reason |
| Task | One ledger row: an outcome, its completion condition, a scope, prerequisites and a source |
| Task authority | The one place that decides which tasks exist and their status: the ledger, or a tracker after a move |
| Task intake | Turning a planner's items into tasks, each keeping its item as the source |
| Writer | The live session that last started in a checkout and holds a product claim, one scoped outside `.sako/`. A checkout has one writer |
