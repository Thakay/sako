# Glossary

The words SAKO uses, in one place. The other pages use them in this sense.

| Word | Meaning here |
|---|---|
| Check | The project's own command, such as its tests, set as `verify_command` in `.sako/config.json`; `verify` runs it |
| Check-in | Register an existing agent session with SAKO and get its context, through a session-start hook or the `start` command |
| Claim | A session's ownership of a task, in the task's Status. A live claim cannot be replaced |
| Client | The program an agent runs in. SAKO wires hooks for Claude Code and Codex; with others, the agent runs the commands |
| Completion condition | The observable result that makes a task done: its "Done when" cell |
| Coordinate | Keep sessions someone else started from colliding, with claims, scope checks and a shared lock, in one repository and its worktrees |
| Finish gate | The check at a session's stop against three rules, Recorded, Proven and Committed. It refuses a stop once and names the fix |
| Footprint | Where the records live: **local** (out of Git, the default), **repo** (their own Git repository inside `.sako/`) or **shared** (committed with the code, one copy per branch) |
| Handoff | A paused task's notes, in `.sako/work/T-<n>-<slug>/handoff.md` |
| Hook | A client's automatic call into SAKO when a session starts, stops or ends |
| Kit | The files SAKO owns and updates: the runtime `.sako/sako.py`, the method `.sako/SAKO.md`, `.sako/install.json` and the skill copies |
| Ledger | The task records in `.sako/work/`: `TASKS.md` for active work, `DONE.md` for finished work |
| Method | `SAKO.md`, the instructions agents follow in a project |
| Owner | The person the agent works for, who sets direction, allows commits and accepts results |
| Parked | A task held on purpose, with its reason in Status; `next` skips it |
| Planner | Whatever decides what to build: a spec, a plan, an issue tracker, or the method's default planner |
| Presence | SAKO's record of which sessions are live, which protects live claims |
| Receipt | What `close` writes for a finished task: the completion condition, the evidence, the closing session, the date and what the check said |
| Scope | The path prefixes a task may change |
| Session | A conversation opened in an agent client; `start` registers it with SAKO and `end` removes its presence record |
| Source | The exact reference to the planner item a task came from, such as `PLAN.md#N1` |
| Stamp | The record of a passing check on a fingerprint of the checked content |
| Takeover | Claiming a task whose holder is no longer live, with a reason |
| Task | One ledger row: an outcome, its completion condition, a scope, prerequisites and a source |
| Task authority | The one place that decides which tasks exist and their status: the ledger, or a tracker after a move |
| Task intake | Turning a planner's items into tasks, each keeping its item as the source |
