# SAKO

SAKO helps you track coding-agent tasks and review the evidence behind finished
work. It keeps tasks in a Markdown **ledger** and uses a **finish gate** to flag
unrecorded changes, missing or stale check results, and finished work that needs a
commit.

If you use coding agents such as Claude Code or Codex in a Git repository, SAKO
helps you keep track of who is working on what, what has been checked, and what a
later session needs to pick up. You can use it with one agent or several sessions
working in the same repository.

SAKO fits alongside your planner, coding agent, and tests. Its focus is the link
between a task and the evidence that it is finished: the gate reads Git and the
latest check result, then points out gaps when a session stops. You choose the
work and decide whether the result meets your needs. The records are plain
Markdown, and the runtime is one Python file with no service or account to set up.

SAKO stands for Simple Agentic Kit for Operations, pronounced "sah-ko".

**Alpha:** commands and file formats can change in any 0.x release.

## Quickstart

You need Python 3.10 or newer, Git 2.31 or newer,
[uv](https://docs.astral.sh/uv/getting-started/installation/), and a coding agent
that can run commands. SAKO runs on Linux, WSL, macOS, and Windows.

On Windows, use `python` wherever these instructions say `python3`. Check that
`python --version` reports 3.10 or newer in Git Bash, where Claude Code runs
SAKO's hooks
([platform notes](https://github.com/Thakay/sako/blob/main/docs/limits.md#environment)).

From your project's Git repository, install SAKO:

```sh
uvx sako init
```

In a project containing `CLAUDE.md`, the installer sets up Claude Code's hooks and
prints:

```text
SAKO installed in .sako/ (local). Nothing tracked changed; the folder is listed in .git/info/exclude.
Records: .sako/work/TASKS.md and DONE.md. They are not in Git yet; git clean -x deletes them.
  Keep their history, or share them privately:  uvx sako init --repo [url]
  Commit them with the code:                    uvx sako init --shared
Hooks written: Claude Code (.claude/settings.local.json), chosen from CLAUDE.md. Start a session to begin. Change the client with: uvx sako init --client claude|codex|none.
```

Next, set your project's check command in `.sako/config.json`. For example, a
Python project using unittest would set
`"verify_command": ["python3", "-B", "-m", "unittest"]`. Use the command that checks
your own project; `-B` in this example prevents Python from creating cache files
during the check.

Start a session in your coding client and ask for a change as usual. The start
context gives your agent the instructions for recording and checking the work.
SAKO sets up hooks for Claude Code and Codex when it detects them. Without hooks,
the agent runs `start`, `check --gate`, and `end` itself. See
[client compatibility](https://github.com/Thakay/sako/blob/main/docs/compatibility.md)
for setup details.

[What `init` changes](https://github.com/Thakay/sako/blob/main/docs/update-and-remove.md#what-init-changes)
lists every file the installer writes. To uninstall while keeping your task
records, run `python3 .sako/sako.py remove`.

## Done means

When a session stops, its hook checks that session's changes against three rules.
The agent can also run the gate with `check --gate`:

- **Recorded:** every changed file belongs to a task the session claimed or closed.
- **Proven:** a task closed after checked content changed has a passing check for
  that content. A missing or failed check never counts as a pass.
- **Committed:** finished work is committed. If you keep the task records in Git,
  they are committed too.

Each finding explains how to fix the problem. You can pause unfinished work while
keeping its task claimed.

Every closed task gets a **receipt** in `.sako/work/DONE.md`, recording its
completion condition, evidence, closing session, date, and check result. If no
automated check is configured, the receipt says so.

You still review and accept the result. A passing gate confirms that SAKO's rules
were met; the [method](https://github.com/Thakay/sako/blob/main/SAKO.md#done-means)
explains what they cover.

## See a complete task

[The demo](https://github.com/Thakay/sako/blob/main/docs/demo.md) follows a task
from an unclaimed change through a failed check, a fix, a receipt, and a commit.
A fresh session then picks up the next task from the records. It is a scripted
run with real command output, kept in sync by a test.

## SAKO is and is not

SAKO handles task ownership and checks the evidence behind finished work. Here
is how it fits with the rest of your tools:

| SAKO provides | Where other tools fit |
|---|---|
| Task records in Markdown, linked to the source of the work | Your planner or spec process; SAKO includes a small default planner |
| A finish gate connecting changed files, check results, and commits | Your tests and CI to check whether the code works |
| Claims and scope checks for sessions in one repository and its worktrees | A scheduler if you want agents launched or assigned automatically |
| Local records you can keep private or commit | A tracker for team sync or work across clones and machines |

SAKO uses only Python's standard library and sends no telemetry. The gate prompts
agents to fix gaps; it is not a security boundary. It blocks a stop once, and a
repeated stop or a hook's own error lets the session end.

## Where your records live

Your task records live in `.sako/`. The **footprint** determines how you keep and
share them:

| Footprint | Command | What you get |
|---|---|---|
| Local, the default | `init` | Records stay outside Git. Useful for trying SAKO or working on one machine |
| Repo | `init --repo [url]` | Records have their own Git history inside `.sako/`, with an optional remote for private sharing |
| Shared | `init --shared` | Records live with your code, with a separate copy on each branch. The installer prints the commit command |

Switching footprints preserves the records byte for byte.

## As your project grows

Start with one agent, one task, and the default planner. You can add more when
your work calls for it:

- **Bring an existing plan.** Use
  [task intake](https://github.com/Thakay/sako/blob/main/docs/intake-recipes.md)
  to turn your plan into tasks with priorities and references to their source.
- **Work in parallel.** Claims and scope checks help coordinate sessions in the
  same repository, including linked worktrees.
- **Work across clones or machines.**
  [Move task authority to a tracker](https://github.com/Thakay/sako/blob/main/docs/move-to-a-tracker.md).
  Add a scheduler if you also need agents started and assigned for you.

## Why it stays small

1. **Keep evidence visible.** The gate reads Git and check results. A passing
   check, a commit, and your acceptance each mean something different. You decide
   whether the checks cover what the task needs to prove.
2. **Give each fact one home.** Your planner holds intent, the ledger holds
   ownership and evidence, and Git holds the changes. Source references connect
   them without copying the plan. Plain Markdown keeps the records readable by
   people, agents, and other tools.
3. **Keep everyday use simple.** Install with one command and a short read. Bring
   your own planner, agent, and checks; the agent runs the commands while you
   guide the work and review the result.
4. **Make adoption reversible.** The default install leaves tracked files alone.
   Add history and sharing when you need them. Removal and migration instructions
   help you keep your records when you move on.

## Learn more

- [SAKO.md](https://github.com/Thakay/sako/blob/main/SAKO.md): the method your agent follows
- [Compatibility](https://github.com/Thakay/sako/blob/main/docs/compatibility.md): client setup and tested behavior
- [Limits](https://github.com/Thakay/sako/blob/main/docs/limits.md): supported environments and boundaries
- [Update and removal](https://github.com/Thakay/sako/blob/main/docs/update-and-remove.md): installation details, updates, and removal
- [Validation](https://github.com/Thakay/sako/blob/main/docs/validation.md): guarantees and their evidence
- [Glossary](https://github.com/Thakay/sako/blob/main/docs/glossary.md): terms used in the docs

## Contributing, security and license

Bug reports, client test results, and improvements are welcome. See
[CONTRIBUTING.md](https://github.com/Thakay/sako/blob/main/CONTRIBUTING.md) to get
started, and [SECURITY.md](https://github.com/Thakay/sako/blob/main/SECURITY.md) to
report a vulnerability. SAKO is released under the
[MIT license](https://github.com/Thakay/sako/blob/main/LICENSE).
