# Client compatibility

What each client needs to run SAKO, and how it behaves there. Behavior is defined in
[the method](../SAKO.md); boundaries are in [limits](limits.md).

SAKO needs an agent that can read project files and run commands, in a local Git
repository ([environment](limits.md#environment)). Hooks are optional adapters that
run three commands for the agent: SessionStart prints the start context, Stop runs
the finish gate, and SessionEnd removes the session's presence. Without hooks, the
agent runs `start`, `check --gate` and `end` itself, as the method says.

## Per client

| | Claude Code | Codex | Another agent |
|---|---|---|---|
| Last checked with | 2.1.280 on Linux (WSL2, Ubuntu 24.04), 2026-09-22; 2.1.220 on Windows, hooks only, 2026-09-23 | 0.154.0 on Linux (WSL2, Ubuntu 24.04), 2026-09-23 | Not checked, Cursor included |
| `init` detects it from | Its session running `init`, `.claude/`, `CLAUDE.md` or `AGENTS.md` | Its session running `init`, `.codex/` or `AGENTS.md` | Not detected; use `init --client none` |
| Hooks | `.claude/settings.local.json`, untracked | `.codex/hooks.json`, untracked; Codex runs them only after you trust them in Codex and skips untrusted ones silently | None; the agent runs the commands |
| How the agent finds SAKO | The start context, and the `sako` skill in `.claude/skills/` | The start context, and the `sako` skill in `.agents/skills/` | Point its instructions at `.sako/SAKO.md`; a client that reads `.agents/skills/` finds the skill |
| A command without `--session` | Uses `CLAUDE_CODE_SESSION_ID`, the ID its hooks use | Uses `CODEX_SESSION_ID`, the ID its hooks use, in a subagent too; before Codex 0.148, `CODEX_THREAD_ID` | Pass `--session`, or set `SAKO_SESSION` |
| Where agent commands run | On the host; Claude Code's own sandbox is optional and unchecked | In Codex's sandbox (checked in its workspace-write mode): their own process namespace, writes only in the checkout and the roots you add | Where the client runs them |

`init` prints which clients it wired and why; `--client claude`, `--client codex`
or `--client none` replaces the choice, and `status` says when each client's hook
last ran, or "not seen yet". A client settings file the project tracks is left
unchanged, with a note: add the hooks there yourself or run the commands. A new
linked worktree needs `init` there for its own hook settings; for Codex it also adds
the main checkout's `.sako/` as a writable root, because Codex's sandbox lets agent
commands write only their own checkout. Codex reads that setting only once it trusts
the project, so its first run in a new repository may lack it.

## Checked live

Headless sessions (`claude -p` and `codex exec`) in scratch repositories, with the
versions in the table above. The Codex sessions passed
`--dangerously-bypass-hook-trust`, so hooks ran without a lasting trust change.

| Check | Claude Code | Codex |
|---|---|---|
| Hooks load from local-only settings | Yes | Yes, with hook trust bypassed for each run |
| The start context reaches the agent | Yes | Yes |
| Stop blocks unrecorded work | Yes; a second stop passes | Yes; a second stop passes |
| SessionEnd clears presence | Yes | Yes |
| A linked worktree uses the main checkout's `.sako/` | Yes | Yes |
| Footprints | Local and repo | Local and shared |
| Explicit commands, no hooks | Yes: start, add, claim, close, commit, a clear gate, end | Yes, the same |

The agents found SAKO without being told: Claude Code through the skill, Codex
through the start context.

## Client behavior worth knowing

- Claude Code shows the hook's whole shell command before the gate's message and
  posts "Stop hook error occurred" for a block: exit 2 is how a Stop hook blocks.
- `codex exec --json` shows no hook events; its plain output does, such as
  `hook: Stop Blocked`.
- Codex escapes `<`, `>` and `&` in the Stop feedback it hands the agent, so gate
  messages write placeholders in capitals, such as `add "WHAT CHANGED"`, and join
  two commands with ", then" rather than `&&`.
- In a linked worktree, Claude Code lists the `sako` skill from the main checkout.
  Codex does not, because the untracked skill folder is not in the worktree; its
  agent still finds the method through the start context.
- In a linked worktree, Codex writes the main checkout's `.sako/` only through the
  writable root `init` adds, which takes effect once Codex trusts the project
  (`codex exec` records that trust during its first run in the repository), or when
  Codex starts with `--add-dir`; until then a write refuses with that fix. A commit
  there needs an approval to leave the sandbox, because the main checkout's `.git/`,
  which holds the worktree's Git data, is read-only in it.
- Claude Code's worktree isolation for subagents puts their worktrees under
  `.claude/worktrees/` inside the repository. The gate treats a linked worktree
  nested in the checkout as another checkout and counts its work once it is merged.
  Claude Code's [worktree guide](https://code.claude.com/docs/en/worktrees) suggests
  adding `.claude/worktrees/` to `.gitignore`, which keeps the folders out of
  `git status`.
- `codex exec` records each new checkout it runs in as trusted in your
  `~/.codex/config.toml`.

## Not verified

- Codex hooks trusted the normal way, in Codex, instead of bypassed per run.
- Claude Code with its own sandbox turned on.
- The shared footprint in Claude Code and the repo footprint in Codex.
- Interactive sessions, resume, and a hook that fails.
- An agent's turn under the hooks on Windows, and Codex for Windows.
- Cursor or any other client.

## Windows

Claude Code on Windows runs hooks through Git Bash, which Git for Windows includes,
so SAKO writes the same hook command as on Linux, with `python`. Codex on Windows
runs hooks in the session's shell, PowerShell by default, so `init` there writes no
Codex hooks and says so; its agent runs `start`, `check --gate` and `end` itself.
Hook files belong to one clone on one machine: a clone used from both WSL and
Windows needs `init` on the side you switch to.

Last checked on 2026-09-23 with Claude Code 2.1.220: SessionStart and SessionEnd ran
through Git Bash, with UTF-8 intact in a folder named `café-repo`; driven by the same
hook command, the Stop gate blocked an unrecorded change once with exit 2 and passed
the repeated stop; the printed commands ran as printed in Git Bash and PowerShell.

## Sessions without hooks

Follow the [daily loop](../SAKO.md#daily-loop) with explicit commands, `start`
first and `end` last. In Claude Code and Codex, a command without `--session` uses
the client's own session ID; when both clients' IDs are set, as when one client runs
inside the other, neither is used, so pass `--session`. `SAKO_SESSION` takes
precedence over both. In Claude Code, a subagent's shell carries its own ID, so it
passes the parent's `--session` explicitly; in Codex, `CODEX_SESSION_ID` names the
parent session, as its hooks do.

## Try it in your client

Use a disposable project and record the client version and operating system with
the result.

1. Install and open a new conversation. Confirm that the start context appears, or
   run `start`, and that the agent finds the method. Complete one task with
   evidence, a commit and a clear gate.
2. Make an unrecorded change and try to stop: the gate refuses once and names the
   fix.
3. Open a second conversation with its own ID. Confirm that each sees the other in
   `status` and that a live claim cannot be taken over; use disjoint scopes. From
   inside Codex's sandbox a peer shows as unverified, and its claim can be taken
   over only with a reason.
4. End one conversation and confirm that only its presence disappears. `status`
   also says when each client's hook last ran.

Record a failure against discovery, the lifecycle or a command, so a fix targets
the boundary that failed. Passing the automated tests does not replace this trial.
