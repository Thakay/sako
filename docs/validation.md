# Validation: guarantees and evidence

What SAKO guarantees, the evidence behind each guarantee, and what that evidence
cannot show. The evidence comes from synthetic local fixtures, not an outside adopter
study or a guarantee for every environment; the [method](../SAKO.md) defines the
behavior in full.

## Guarantees

| # | Guarantee | Evidence | Missing |
|---|---|---|---|
| 1 | **Three rules and a fix in every finding.** Recorded, Proven and Committed apply to the session's own changes, read from Git and the check stamp; an absent or failing check never passes; every finding ends with its exact fix | [test_gate.py](../tests/test_gate.py): each rule and exemption, the fix in every finding, a close refused without fresh evidence; scenarios `stop_gate`, `verify_evidence`, `invalid_configuration_never_passes`; the [demo](demo.md) | None |
| 2 | **Refuses once, never traps.** A stop is blocked once; a repeated stop, a hook's own error or a missing kit lets the session go; pausing with a held claim passes | A repeated stop fails open; claimed, unfinished work passes; handoff advisories never block; hooks exit 1, never 2, without the kit; a bug in the hook handler and a hook that cannot start Python let the session go | None |
| 3 | **A receipt on every finished task.** The completion condition, the evidence, the closing session, the date, and what the check said on its own fingerprint, or "no automated check"; never a pass the check did not give | Receipts in each form; an interrupted close finishes on repeat; the demo's receipt; scenario `greenfield_discovery_without_automated_check` | None |
| 4 | **Plain, forgiving records.** Markdown tables with at most eight required columns, read by name; your columns and hand edits survive every write; IDs are never reused; a fault names its file and line | Columns by name in any order, your columns kept through close, hand edits, faults with file and line, a bad value held out of `next`; scenario `ledger_findings`; a test holds the required columns at 8 | None |
| 5 | **One explainable next.** Priority, then row order, among ready tasks; it names its pick and why, what else is ready and what waits | Ranking tests; scenario `next_frontier`; the demo's pickup | None |
| 6 | **Writes under one lock, across worktrees.** Parallel adds get distinct IDs, one claim wins, overlaps and live owners are refused, a stale takeover needs a reason, and repeated intake reuses its task | Concurrent adds from two worktrees, claim races, unverified holders, repeat intake; scenarios `presence_peers_and_stale_claims`, `scopes_and_overlap`, `interruption_recovery`, `worktrees_share_presence`, `external_plan_intake_and_completion` | Across machines: one writer at a time, stated, not tested |
| 7 | **One command, nothing tracked changes.** `init` needs no flags, leaves `git status` clean and instructions untouched, names the clients it wired and why, and refuses unsupported platforms and layouts before any write | Detection from each client signal, `--client`, tracked client files left alone, refusals, the package and a checkout installing the same files; scenarios `init_and_hook_dispatch`, `root_refusals`, `invalid_install_has_no_partial_assets`; an offline `uvx` install from the wheel | Live hook delivery beyond the sessions in [compatibility](compatibility.md); Cursor unchecked |
| 8 | **One method document, any planner.** SAKO.md alone, its required part within the 1,500-word reading budget; intake keeps source references; a default planner serves projects without one | The reading-budget and routing test (measured in [Measured fixture overhead](#measured-fixture-overhead)); scenarios `greenfield_adoption_and_fresh_pickup`, `existing_project_reuses_context_and_checks`, `unstructured_project_and_completed_goal`, `replanning_preserves_authority_and_blockers`; the [intake recipes](intake-recipes.md), each run once | Planning judgment by outside users |
| 9 | **The whole loop, shown.** The finish-gate and two-session demos use real SAKO output from scripted runs | Both transcripts and the [README storyboard](assets/readme/working-together.svg), regenerated in a test and compared with the files | Live AI collaboration and a newcomer's own timing |
| 10 | **Records leave with you.** `remove` keeps `config.json` and `work/` byte for byte and clears every worktree's hooks; an update replaces only unmodified kit files; unrelated settings, skills and instructions survive | Removal and update tests, local-change and older-release refusals; scenarios `update_and_removal_preserve_project_data`, `package_adoption_pickup_and_removal`; the [tracker recipes](move-to-a-tracker.md) | No move to a tracker observed |
| 11 | **Three footprints, one folder.** The footprint is recorded and checked against Git; switching keeps the records byte for byte; `--shared` commits nothing; worktrees share the main checkout's folder, except in shared | The repo and shared footprint tests: nested repository, clone and remote, conflict refusal, both gate forms, removal warnings, per-branch copies, switches | None |

## Reproduce the checks

Run the checks in [CONTRIBUTING.md](../CONTRIBUTING.md#check-the-change);
`PYTHONDONTWRITEBYTECODE=1 python3 tests/measure.py` reproduces the measurements,
and `uv build` the package. CI runs both suites on Linux, Windows and macOS.

The scenarios build disposable repositories and drive the command line and the
generated hook commands; long-lived `sleep` processes stand in for agent sessions.
They also run inside discovery, so the counts overlap. The skill meets the Agent
Skills format. No independent agent or outside user has validated adoption
judgment.

Last local run on 2026-09-29, on WSL2 (Linux 6.18) with Git 2.43.0:
both suites pass on Python 3.10.20 and 3.12.3, with 180 tests and 22 scenarios.

## Native Windows

Both suites run natively on Windows from Git Bash, which puts Git's POSIX tools such
as `sleep` on the path. Last run on 2026-09-23 with Python 3.14.4 and Git for
Windows 2.52.0:

| Suite | Result |
|---|---|
| Unit and integration discovery | 180 run: 143 passed, 37 skipped, none failed, in 112 s |
| Standalone scenario suite | 17 passed, 5 skipped, none failed, in 27 s |

Each skip names why its premise does not hold there: Codex gets no hooks on Windows
(12 tests), presence is the 24-hour lease there, since SAKO reads no Windows process
(9), creating symlinks needs a privilege that host did not grant (9), file modes do
not bind (3), and one each for the executable bit, a file name that is not Unicode,
the published demo, which is a Linux run, and the bubblewrap sandbox. No runtime
behavior needed a skip: locking, writes under parallel adds, UTF-8 output and paths
passed.

## What each file covers

| File | What it proves |
|---|---|
| [test_gate.py](../tests/test_gate.py) | The three rules, their exemptions, the rule and fix in every finding, the one-time block, a hook failing on its own, the handoff advisories |
| [test_sako.py](../tests/test_sako.py) | The ledger parser and configuration, hand edits, faults with their file and line, `next` ranking; it also runs the scenarios |
| [test_workflow.py](../tests/test_workflow.py) | Commands end to end: parallel adds and claims, repeat intake, prerequisites, interrupted closes, receipts, handoff folders, `--session` on every command |
| [test_footprint.py](../tests/test_footprint.py) | The three footprints and the switches between them, worktrees, and one regression per defect the reviews found |
| [test_compatibility.py](../tests/test_compatibility.py) | Client detection and `--client`, both clients' hook commands and session defaults, presence across process namespaces, refused older installations, unrelated settings kept |
| [test_release.py](../tests/test_release.py) | Process identity, the package's contents, documentation covered by the check, the budgets, both demo transcripts and the README storyboard against fresh runs, and the links |
| [selftest.py](../tests/selftest.py) | 22 scenarios, from adoption to recovery; they supply the planning choices, so they prove the mechanics, not an agent's judgment |
| [demo.py](../tests/demo.py), [measure.py](../tests/measure.py) | Generate the demo pages, the README storyboard and the measurements |
| [planning-cases.md](../tests/planning-cases.md) | Manual planning trials for outside adopters |

Tests whose premise Windows lacks skip there with a named reason; the Windows-only
paths (the byte lock, reads without it, retried reads and replaces, the lease, no
Codex hooks) have their own tests, run on Linux with the platform patched.

## Measured fixture overhead

Measured on 2026-09-23 with Python 3.10.20 on the WSL2 host, separately from the
test suites. The numbers include process startup and local Git commands, and vary
with the machine, filesystem, repository, cache and check command.

| Measure | Observed |
|---|---|
| No client signal: kit and shared skill, no hooks | 6 files, 122,707 bytes |
| Codex | 7 files, 123,924 bytes |
| Claude Code, with its own skill copy | 8 files, 125,014 bytes |
| Both clients | 9 files, 126,231 bytes |
| Installed method, one document | 3,241 whitespace-separated words |
| Reading for a normal task | 1,341 words: skill 164, template preamble 46, start context 110, the method's required part 1,021 |
| Runtime source | 1,799 lines, one standard-library Python file, within the 1,800-line cap |
| Initial install with no client signal, one sample | 67.9 ms |
| Fingerprint fixture | 101 covered files, 409,609 bytes |
| Trials per command | 7, median reported in the next rows |
| `start` | 52.9 ms |
| `check` | 46.9 ms |
| `next` | 45.8 ms |
| `verify`, including a small source-content assertion | 71.7 ms |
| `check --gate`, no completed rows | 56.6 ms |

`start` is sampled before verification has established evidence, and the gate
fixture has no closed rows, so completed-work fingerprinting is not timed. These are
byte, word and execution measurements, not model token counts, onboarding time, or
proof of low overwhelm.

`uv build` makes a wheel of about 43 KB holding `sako/__init__.py`, the method, the
task template and the skill, with a `sako` command, and `twine check` passes for the
wheel and the source archive. With an empty home folder and uv cache, and
`--offline`, `uvx --from` that wheel ran `init` with no flags in a fresh repository
holding a `CLAUDE.md`: it wired Claude Code's hooks, and their SessionStart, Stop and
SessionEnd commands ran from that repository.
