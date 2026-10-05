# Contributing to SAKO

SAKO is a task ledger and finish gate for coding agents. A useful change makes task
ownership, evidence, recovery, or adoption more dependable while preserving the
project's chosen workflow. A bug report, a result from your client, a documentation
fix and code are all welcome.

## Report a bug

Open an issue with the bug form. Give the smallest reproduction, the expected and
actual behavior, the versions the form asks for, and the command output that
explains the failure. Use synthetic examples and remove private project details. To
report a vulnerability, follow [SECURITY.md](SECURITY.md) instead.

## Propose a change

For new machinery, show the observed problem and why the existing task record,
instructions, or commands cannot handle it. Keep planning practices replaceable.
An external integration must define source identity, status ownership, and recovery.
A change serves the finish gate, the ledger, installing, or leaving cleanly, and
keeps to the [principles](README.md#why-it-stays-small); a change that serves none of
these waits.

A pull request explains the problem, the resulting behavior, how it was checked, and
any compatibility limits. Follow the writing and commit rules in
[AGENTS.md](AGENTS.md).

## Design budgets

Tests hold these limits. Over one, remove something before adding.

| Budget | Limit |
|---|---|
| Install | One command, no required flags |
| Reading for a normal task | About 1,500 words: the skill, the task template's preamble, a start context and the method's required part |
| Required ledger columns | 8 at most |
| Runtime | One standard-library file of at most 1,800 lines, counted with `wc -l sako.py` |

## Work from a checkout

Use Linux, WSL, macOS or Windows, Python 3.10 or newer, and Git 2.31 or newer; on
Windows, run the checks from Git Bash, with `python` where they say `python3`. The
runtime and the tests use Python's standard library; no package installation is
needed. Read [SAKO.md](SAKO.md) before changing behavior.

| Path | Purpose |
|---|---|
| [README.md](README.md) | The entry for people: what SAKO is, the quickstart, the footprints |
| [SAKO.md](SAKO.md) | The method installed in each project, with its default planner |
| [AGENTS.md](AGENTS.md) | Rules for agents changing this repository; `CLAUDE.md` imports it |
| [SECURITY.md](SECURITY.md) | How to report a vulnerability, and what SAKO reads and sends |
| `sako.py` | One Python file for records, presence, evidence, and installation |
| `pyproject.toml` | Builds the `sako` package from this checkout |
| `skills/sako/` | Optional agent entry to the installed method |
| `templates/` | The empty task table |
| `docs/` | Compatibility, the demo, the glossary, intake recipes, limits, moving to a tracker, update and removal, validation |
| `tests/` | Unit, concurrent-process, adoption, recovery and documentation checks |
| `.github/` | CI, the bug form, the pull request template and Dependabot |

## Check the change

From the checkout root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 tests/selftest.py
git diff --check
```

Run both suites on Python 3.10 when available; CI runs them on Linux, Windows and
macOS. The scenario suite is also part of discovery, so the counts overlap. Tests use
temporary repositories and process stand-ins: they do not establish live-client
behavior or newcomer usability. Add a focused regression for a behavior bug.

## Documentation

- Each fact has one home; elsewhere, a sentence or two and a link. SAKO.md, the only
  document installed in a project, is the home of behavior.
- Document only behavior that exists, in the words the
  [glossary](docs/glossary.md) defines: plain language, second person, present
  tense. History belongs in Git, not in the docs; owner deliberations and internal
  planning notes stay out.
- The `Links` test in `tests/test_release.py` checks that every relative link and
  heading anchor resolves, that the README's links to this repository point at real
  files, and that no page has an em dash.
- The [demo](docs/demo.md) is generated: after a change to what it shows, run
  `PYTHONDONTWRITEBYTECODE=1 python3 tests/demo.py > docs/demo.md` and never edit
  the page by hand.
- Update [validation](docs/validation.md) when measured results or their scope
  change; `PYTHONDONTWRITEBYTECODE=1 python3 tests/measure.py` reproduces the
  measurements.

## Build and try the package

`pyproject.toml` installs the one runtime file as `sako/__init__.py` beside the
method, the task template and the skill; nothing moves in the checkout. The wheel
carries no tests or contributor documents. Building publishes nothing. To build it
and try it in a fresh Git repository:

```sh
uv build
wheel=$(ls -t "$PWD"/dist/sako-*.whl | head -1)
sako_example=$(mktemp -d)
git init "$sako_example" && cd "$sako_example"
uvx --from "$wheel" sako init
python3 .sako/sako.py next
```

A new repository has no sign of a client, so `init` installs the shared skill and no
hooks, and says how to add them:

```text
Hooks: none, no client found here. Agents read .agents/skills/sako/SKILL.md; run start, check --gate and end yourself (python3 .sako/sako.py start --session <id>). Change the client with: uvx sako init --client claude|codex|none.
```

`next` then says nothing is ready and points to **No planner? Start here** in
`.sako/SAKO.md`. [Update and removal](docs/update-and-remove.md) covers the rest of
the kit's lifecycle.

## AI-assisted contributions

Pull requests made with AI tools are welcome, including those an agent opens with no
person in the loop. They are reviewed like any other change, under three rules:

- **Name the tool.** Each commit ends with one `Co-authored-by:` trailer per AI tool
  that did the work, as [AGENTS.md](AGENTS.md#commits) describes, and the pull
  request's AI tool field names the same tools. Work by a person alone carries no
  trailer, and that field says "none".
- **Show the checks.** Both suites pass, and the pull request carries their output.
- **Answer review.** Someone, the agent or the person running it, answers review
  questions on the pull request.

No session links. Agents working from a fork follow
[AGENTS.md](AGENTS.md#work-from-a-fork).

## How the project runs

One maintainer reviews and decides; decisions live in issues and pull requests.
Expect a first reply within 7 days, and within 14 days for a security report. There
is no contributor license agreement: under GitHub's terms of service, a contribution
is licensed under the repository's MIT license.
