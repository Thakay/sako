# SAKO

[![Latest release on PyPI](https://img.shields.io/pypi/v/sako)](https://pypi.org/project/sako/)
[![Status of the checks on main](https://github.com/Thakay/sako/actions/workflows/checks.yml/badge.svg?branch=main)](https://github.com/Thakay/sako/actions/workflows/checks.yml)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/sako)](https://pypi.org/project/sako/)

**Simple Agentic Kit for Operations**

**Start simple. Keep your agents coordinated.**

- **Get started with coding agents.** Begin with one agent, one task, and a
  simple workflow.
- **Work with multiple agents in parallel.** Give each agent a clear piece of
  work and keep the effort coordinated.
- **Bring clarity to an existing project.** Work with your agent to organize
  what is unfinished and choose a useful next step.

One command to install. Plain files in your project. Works alongside your agents,
planner, and tests, with hooks for Claude Code and Codex on
[supported clients](https://github.com/Thakay/sako/blob/main/docs/compatibility.md).

**Alpha** · [Get started](#get-started) · [See it in use](#see-it-in-use) ·
[Documentation](#explore-further) · [MIT](https://github.com/Thakay/sako/blob/main/LICENSE)

Commands and file formats can change in any 0.x release.

## See it in use

Open two agent sessions in your usual coding tool for a small change. One
handles the code; the other writes the usage guide. SAKO keeps their tasks and
progress visible, so the next session has a place to continue.

![A request to add a greeting and usage guide becomes two separately claimed tasks. Both finish with passing checks and receipts. A fresh session picks up the next task.](https://raw.githubusercontent.com/Thakay/sako/main/docs/assets/readme/working-together.svg)

Illustrated from a [scripted run with real SAKO output](https://github.com/Thakay/sako/blob/main/docs/coordination-demo.md).
The sessions share one repository. You choose the work and review what they
build.

## Get started

You need a Git project, Python 3.10+, Git 2.31+,
[uv](https://docs.astral.sh/uv/getting-started/installation/), and a coding agent
that can run commands. From your project:

```sh
uvx sako init
```

Set `verify_command` in `.sako/config.json`, keeping its other settings.
For a Python project using unittest:

```json
{
  "verify_command": [
    "python3", "-B",
    "-m", "unittest"
  ]
}
```

Use your own project's command. On Windows, use `python` and follow the
[platform notes](https://github.com/Thakay/sako/blob/main/docs/limits.md#environment).

Open a fresh session in your coding tool and ask for one small change. The
agent checks in with SAKO, then follows the installed instructions to record,
claim, check, and hand back the work. You decide whether the result meets your
needs.

The installer selects hooks for detected clients. Codex hooks need your trust;
Codex on Windows uses explicit commands. The
[client guide](https://github.com/Thakay/sako/blob/main/docs/compatibility.md)
explains setup and how to confirm the hooks ran.

## Work your way

- **Start from an idea.** The
  [small default planner](https://github.com/Thakay/sako/blob/main/SAKO.md#no-planner-start-here)
  helps you and your agent choose a useful first task.
- **Bring an existing project.** Turn your notes, issues, or plan into
  [clear tasks](https://github.com/Thakay/sako/blob/main/docs/intake-recipes.md).
- **Add parallel work.** Use separate task scopes to coordinate sessions in
  [one repository and its worktrees](https://github.com/Thakay/sako/blob/main/SAKO.md#working-beside-other-sessions).
  For work across machines, follow the
  [tracker migration guide](https://github.com/Thakay/sako/blob/main/docs/move-to-a-tracker.md).
- **Choose where records live.** Keep them local, give them a separate Git
  repository, or share them with your code. See the
  [storage choices](https://github.com/Thakay/sako/blob/main/SAKO.md#install-update-and-remove).

## Why it stays small

1. **Keep evidence visible.** Review the task, its check result, and the changes
   together. You decide what good work means.
2. **Give each fact one home.** Your plan holds intent, the task records hold
   ownership and evidence, and Git holds the changes.
3. **Keep everyday use simple.** Bring your agent, planner, and checks. The
   runtime is one Python file using the standard library, with no service or
   telemetry.
4. **Make adoption reversible.** The default install changes no tracked files.
   [Update or remove SAKO](https://github.com/Thakay/sako/blob/main/docs/update-and-remove.md)
   while keeping your task records.

## How it works

SAKO coordinates existing agent sessions. You or your agent platform launch
and direct them. The agents share a Markdown **task ledger**. A **finish gate**
checks their work
against three rules: **Recorded**, **Proven**, and **Committed**. Closed tasks
leave receipts for review and future sessions.

![You direct agent sessions. They invoke SAKO through commands or hooks. SAKO reads and writes task records, runs your configured checks, and reads Git state. It returns context and findings to the agents.](https://raw.githubusercontent.com/Thakay/sako/main/docs/assets/readme/how-it-works.svg)

The gate flags missing records, stale or missing check evidence, and finished
work awaiting a commit. It prompts correction; you still review the result.
A repeated stop or a hook error can let a session end. Read the
[exact rules](https://github.com/Thakay/sako/blob/main/SAKO.md#done-means) or
[walk through a finish](https://github.com/Thakay/sako/blob/main/docs/demo.md).

## Explore further

[Method](https://github.com/Thakay/sako/blob/main/SAKO.md) ·
[Compatibility](https://github.com/Thakay/sako/blob/main/docs/compatibility.md) ·
[Limits](https://github.com/Thakay/sako/blob/main/docs/limits.md) ·
[Validation](https://github.com/Thakay/sako/blob/main/docs/validation.md) ·
[Glossary](https://github.com/Thakay/sako/blob/main/docs/glossary.md)

**Help wanted:** start with an open
[good first issue](https://github.com/Thakay/sako/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

Questions and discoveries are welcome in
[issues](https://github.com/Thakay/sako/issues). See
[contributing](https://github.com/Thakay/sako/blob/main/CONTRIBUTING.md) to help,
[security](https://github.com/Thakay/sako/blob/main/SECURITY.md) to report a
vulnerability, and the [MIT license](https://github.com/Thakay/sako/blob/main/LICENSE).
