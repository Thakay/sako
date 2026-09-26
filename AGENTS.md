# Working on SAKO

Rules and checks for agents that change this repository. They extend
[CONTRIBUTING.md](CONTRIBUTING.md), which people read first.

SAKO is a small execution kit: [SAKO.md](SAKO.md) is the method with its default
planner, `sako.py` is the runtime, `skills/` is the optional agent entry,
`templates/` holds the empty task table, and `docs/` holds the demo, the glossary,
the recipes, compatibility, limits, the lifecycle, and the guarantees with their
evidence (validation).

## Read path

1. This file, then [CONTRIBUTING.md](CONTRIBUTING.md) for the design budgets and the
   [principles](README.md#why-it-stays-small) it points to.
2. [SAKO.md](SAKO.md) before changing behavior or planning, and the
   [guarantees](docs/validation.md#guarantees) a change must keep.
3. `tests/selftest.py` to see what is proven.

## Laws

- One runtime file. `sako.py` stays standard-library Python 3.10 or newer, in one
  file within the runtime budget in CONTRIBUTING.md. Count before adding (`wc -l
  sako.py`). Over the budget, remove something or reopen the question of a heavier
  tool; never add a second script.
- The principles, the guarantees and the maintainer's direction govern changes to
  project conventions.
- Machinery only after an observed need. A new check, command or hook shows first
  that the ledger row, the method or an existing command cannot hold it.
- Documents claim only what exists. A command that is not implemented is not
  documented.
- Examples are synthetic: no real product, person, account, path or session identifier
  in examples, fixtures or tests. Comparisons, recipes, install steps and client wiring
  may name real tools; check what they say against each tool's current documentation.
- Plain language, no em dashes, in files, replies and commit messages.
- Git is the archive: delete retired material and commit the deletion.

## Traps

- `init` needs this checkout or the package built from it (`pyproject.toml` installs
  `sako.py` as `sako/__init__.py`). The runtime copied into a project cannot seed
  another project.
- The scenario suite starts `sleep` processes as stand-ins for agent sessions. An
  interrupted run leaves them until they exit on their own, within an hour.

## Checks

From the checkout root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 tests/selftest.py
git diff --check
```

Run both suites on the oldest supported Python when it is available. A change to
`sako.py` without a green suite does not land.

## Commits

Commit under the author and committer identity already configured for this
repository; never replace it with a tool identity or change global settings.

Every agent-created commit ends, after a blank line, with one trailer per tool that
did the work, each exactly once:

```text
Co-authored-by: Codex <noreply@openai.com>
Co-authored-by: Claude <noreply@anthropic.com>
```

Use the Codex trailer for Codex work and the Claude trailer for Claude work. Credit both
only when both materially contributed. Review-only involvement belongs in review
evidence, not co-authorship. Commits made by a person alone carry no tool trailer.
Keep each trailer exactly once when amending or squashing. These trailers are the
whole attribution: no `Claude-Session` trailer, no session links.

Write the message in a file and commit with `git commit -F <file>`. Check the
resulting author, committer and message with `git show -s --format=full HEAD`, and the
parsed trailers with `git show -s --format='%(trailers:key=Co-authored-by,valueonly)' HEAD`.

`.claude/settings.json` supplies Claude Code's trailer text for this repository. A
Claude session that commits here from outside this project must receive that setting
explicitly. Codex follows this section; no automatic attribution setting is assumed.
