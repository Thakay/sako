# Changelog

Notable changes to SAKO, one entry per release. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). SAKO is alpha software: commands and file
formats can change in any 0.x release.

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
