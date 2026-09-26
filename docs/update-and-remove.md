# Updating and leaving SAKO

What `init` changes in your project, how to update SAKO, and how to remove it. Do
this between working sessions, and look at the current diff first so unrelated work
stays safe.

## What init changes

Everything SAKO writes in the default local footprint:

| Path | What it holds | In Git | After `remove` |
|---|---|---|---|
| `.sako/sako.py`, `.sako/SAKO.md`, `.sako/install.json` | The runtime, the method, and the installed version, file hashes, client choice and footprint | No: `.sako/` is listed in `.git/info/exclude` | Deleted |
| `.sako/config.json` | Your check command and the paths it covers | No | Kept |
| `.sako/work/` | The records: `TASKS.md`, `DONE.md` and handoff folders | No | Kept |
| `.sako/state/` | Locks, presence and check stamps | No | Deleted |
| `.git/info/exclude` | The lines that keep `.sako/` and the client files out of Git | Git's own file, never committed | The lines stay until you delete them |
| `.claude/settings.local.json`, `.claude/skills/sako/` | Claude Code's hooks and its copy of the skill | No | SAKO's hook entries and the skill copy removed; a settings file that held nothing else is deleted |
| `.agents/skills/sako/` | The shared skill, for Codex and other clients | No | Deleted |
| `.codex/hooks.json`, and `.codex/config.toml` in linked worktrees | Codex's hooks, and a writable root for the main checkout's `.sako/` | No | SAKO's entries removed |

In the repo footprint `.sako/` also holds the records' own Git repository; in the
shared footprint `.sako/` is committed with the code. SAKO writes nothing into
project instructions such as `CLAUDE.md` or `AGENTS.md`: the start context names the
method and the records. It recognizes only its exact hook commands in the client
settings files it writes, and leaves a settings file the project tracks unchanged,
with a note.

SAKO owns the kit (the runtime, the method, `install.json` and the skill files); the
project owns `config.json`, the records, its instructions and its tool settings. If
you modify a kit file, keep its diff: update and removal refuse to overwrite such
changes. Do not edit `install.json` to make an unexplained local change look managed.

## Update

Review the new release, then run from the project root:

```sh
uvx sako@latest init --update
```

From a reviewed checkout, `python3 ../sako/sako.py init --update` does the same.
Plain `uvx sako` can reuse a cached copy. Daily work needs neither: commands and
hooks use the project's own copy.

The installer checks for conflicts and invalid hook settings before writing any
file. It replaces unmodified kit files, keeps the records and the configuration,
and merges only the chosen integrations. The client choice from the first install is
kept; `--client claude`, `--client codex` or `--client none` replaces it. Dropping a
client removes only SAKO's hook entries and deletes its unmodified skill copy; an
edited copy refuses until you keep what you need and delete it. A different
installed version refuses a plain `init`; review the change and pass `--update`.
Repeating the same install changes nothing.

On a refusal over a local change, keep the change in project conventions or a
patch, restore that file from the installed release, then retry. After an
interrupted update, rerun the same release: files it already wrote are accepted.
Installation is not a whole-directory transaction; review the diff after an
interruption.

## Validate the result

Run `check`, `verify` when a check is configured, and a fresh session pickup.
`check` reports the ledger and the automated evidence separately; an absent or
failing check never becomes a pass, and a new runtime version makes old stamps
stale. New hook definitions may need the client's trust again
([compatibility](compatibility.md)); watch a hook run before relying on it.

## Remove or roll back

Run `python3 .sako/sako.py remove` in the main checkout. It refuses changed kit
files before deleting anything, then removes what the table in
[What init changes](#what-init-changes) says, in every worktree. Git history
remains. Delete `.sako/` to leave no trace.

In the repo footprint `remove` also keeps `.sako/.git` and warns when the records
repository has commits that exist only there. In the shared footprint it deletes the
tracked kit files and prints the commit command, or `git rm -r --cached .sako` to take
the records out of Git as well. `remove` also forgets the client choice with
`install.json`, so the next `init` detects the clients again.

Switching footprints keeps the records byte for byte; the method's
[install, update and remove](../SAKO.md#install-update-and-remove) section has the
steps.

The local footprint keeps no Git history of the records, so a rollback restores
only the kit: install the older release again with its own `init`. A restored
runtime must support the current record format; keep newer task data when you
consider a rollback.
