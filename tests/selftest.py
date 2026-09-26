#!/usr/bin/env python3
"""SAKO scenario suite: builds throwaway repositories and proves the flows.

Run from a SAKO checkout:  python3 tests/selftest.py
Options: --keep  keep the fixture workspace for inspection.

Every scenario builds its own repositories under a temporary workspace, drives
sako.py through its command line exactly as an agent or a hook would, and asserts
the observable result: exit codes, printed lines, files in the Git directory.
Fake long-lived processes stand in for agent sessions so presence sweeps are real.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import suppress
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAKO = HERE.parent / "sako.py"
WINDOWS = sys.platform == "win32"
LEASE = "on Windows presence is the 24-hour lease: SAKO reads no process there"
NO_CODEX = "on Windows Codex gets no hooks: it runs them in PowerShell"

ENV = dict(os.environ,
           GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
           GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
           GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
           PYTHONDONTWRITEBYTECODE="1")
for _name in [n for n in ENV if n.startswith(("SAKO_", "CLAUDE", "CODEX_")) or n == "AI_AGENT"]:
    del ENV[_name]  # a client's own shell would make every "no signal" install detect it

FAKE_AGENTS: list[subprocess.Popen] = []


def fake_agent() -> int:
    """A long-lived process standing in for an agent session."""
    proc = subprocess.Popen(["sleep", "3600"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    FAKE_AGENTS.append(proc)
    return proc.pid


def symlinks() -> bool:
    """Whether this machine lets the tests make symbolic links; Windows needs a privilege for them."""
    with tempfile.TemporaryDirectory(prefix="sako-link-") as scratch:
        try:
            (Path(scratch) / "link").symlink_to(scratch, target_is_directory=True)
            return True
        except OSError:
            return False


def remove_tree(path: Path) -> None:
    """Delete a fixture tree; Windows keeps Git's object files read-only, so each is made writable first."""
    def writable(function, name, _):
        os.chmod(name, 0o700)
        function(name)

    with suppress(OSError):
        shutil.rmtree(path, **{"onexc" if sys.version_info >= (3, 12) else "onerror": writable})


def need(condition: bool, reason: str) -> None:
    """Skip a scenario whose premise this platform does not have."""
    if not condition:
        raise unittest.SkipTest(reason)


def kill(pid: int) -> None:
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))  # Windows has no SIGKILL; SIGTERM ends a process there
    for proc in FAKE_AGENTS:
        if proc.pid == pid:
            proc.wait()
    for _ in range(50):
        if not Path(f"/proc/{pid}").exists():
            return
        time.sleep(0.05)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", *args],
                            capture_output=True, text=True, env=ENV)
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def commit_all(root: Path, message: str = "change") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


HEADER = "| ID | Pri | Task | Done when | Status | Scope | After | Source |\n|---|---|---|---|---|---|---|---|\n"
DONE_HEADER = "| ID | Task | Done when | Receipt | Scope | Source |\n|---|---|---|---|---|---|\n"
LATER = "| T-9 | P3 | An idea for later | Decided later | parked: not now | - | - | - |"


def row(task_id: str, task: str = "Invented task", status: str = "open", claim: str = "-",
        touches: str = "`src/`", source: str = "-", after: str = "-", evidence: str = "-",
        pri: str = "P2", done_when: str = "The sample output matches") -> str:
    """A tasks-file row; status "claimed" puts the claim marker in Status, and "done" gives
    a done-file row whose receipt names the claim marker."""
    if status == "done":
        receipt = evidence if evidence != "-" else "Recorded"
        if claim != "-":
            receipt += f" [{claim}; closed 2026-01-01; no automated check]"
        return f"| {task_id} | {task} | {done_when} | {receipt} | {touches} | {source} |"
    state = claim if status == "claimed" and claim != "-" else "" if status == "claimed" else status
    return f"| {task_id} | {pri} | {task} | {done_when} | {state or '-'} | {touches} | {after} | {source} |"


def ledger(*rows: str, later: str = LATER) -> str:
    return f"# Tasks\n\n{HEADER}" + "\n".join(rows + ((later,) if later else ())) + "\n"


def done_file(*rows: str) -> str:
    return f"# Done\n\n{DONE_HEADER}" + "\n".join(rows) + "\n"


def make_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    """A committed repository whose kit folder holds a ledger, kept out of Git as init does."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    write(path, {".git/info/exclude": "# sako\n/.sako/\n"})
    write(path, {".sako/work/TASKS.md": ledger(row("T-1")), ".sako/work/DONE.md": "# Done\n",
                 "src/app.py": "print(1)\n", ".gitignore": "__pycache__/\n"})
    if files:
        write(path, files)
    commit_all(path, "fixture")
    return path


def with_done_when(args: tuple[str, ...]) -> tuple[str, ...]:
    """Fixtures that do not care about the completion condition get a stock one."""
    return args + ("--done-when", "The result is observed") if args[:1] == ("add",) and "--done-when" not in args else args


def cli(root: Path, *args: str, session: str | None = None, pid: int | None = None,
        stdin: str | None = None) -> tuple[int, str, str]:
    """Run the checkout's runtime from root, as an agent in that folder would."""
    env = dict(ENV)
    if pid:
        env["SAKO_AGENT_PID"] = str(pid)
    command = [sys.executable, str(SAKO), *with_done_when(args)]
    if session:
        command += ["--session", session]
    result = subprocess.run(command, capture_output=True, text=True, env=env, input=stdin, cwd=str(root))
    return result.returncode, result.stdout, result.stderr


def hook(root: Path, event: str, session: str, pid: int | None = None,
         stop_hook_active: bool = False) -> tuple[int, str, str]:
    payload = {"session_id": session, "hook_event_name": event, "cwd": str(root),
               "stop_hook_active": stop_hook_active}
    return cli(root, "hook", pid=pid, stdin=json.dumps(payload))


def marker(session: str) -> str:
    sys.path.insert(0, str(HERE.parent))
    import sako  # noqa: E402  (the file under test)
    return sako.marker_for(session)


def claim(root: Path, task_id: str, session: str, touches: str = "`src/`") -> str:
    """Hand-edit a claim into the tasks file, as an agent without the command could."""
    path = root / ".sako/work/TASKS.md"
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"| {task_id} |"):
            lines[index] = row(task_id, "Invented task", "claimed", marker(session), touches)
    path.write_text("\n".join(lines) + "\n")
    return marker(session)


def close(root: Path, task_id: str, session: str, touches: str = "`src/`") -> None:
    """Hand-move a claimed row to the done file with a receipt naming this session."""
    active = root / ".sako/work/TASKS.md"
    active.write_text("\n".join(l for l in active.read_text().splitlines()
                                if not l.startswith(f"| {task_id} |")) + "\n")
    done = root / ".sako/work/DONE.md"
    if "| ID |" not in done.read_text():
        done.write_text(done.read_text() + "\n" + DONE_HEADER)
    done.write_text(done.read_text() + row(task_id, "Invented task (landed)", "done", marker(session), touches) + "\n")


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file outside Git internals and SAKO's disposable state."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not {".git", "state"} & set(p.relative_to(root).parts)}


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ----------------------------------------------------------------------------
# scenarios: each takes a fresh workspace directory


def scenario_invalid_configuration_never_passes(ws: Path) -> None:
    repo = make_repo(ws / "repo")
    for text, fragment in (('{"verify_command": "bash"}', "verify_command"),
                           ('{"nope": 1}', "unknown keys"),
                           ('{"product_dir": "."}', "unknown keys"),
                           ('{"decisions": "docs/DECISIONS.md"}', "unknown keys"),
                           ('{"tasks": "../outside.md"}', "stay inside"),
                           ('not json', "invalid JSON"),
                           ('{"verify_paths": ["../x"]}', "stay inside")):
        (repo / ".sako/config.json").write_text(text)
        code, _, err = cli(repo, "check")
        expect(code == 3 and fragment in err, f"config {text!r}: exit {code}, stderr {err!r}")
        code, _, err = cli(repo, "verify")
        expect(code == 3 and not (repo / ".sako/state/stamp.json").exists(),
               "verify must refuse under invalid configuration and write no stamp")
    code, _, err = hook(repo, "Stop", "s1")
    expect(code == 2 and "config.json" in err, "the stop gate must block once on invalid configuration")
    code, _, _ = hook(repo, "Stop", "s1", stop_hook_active=True)
    expect(code == 0, "stop_hook_active must end the retry chain")
    code, out, _ = hook(repo, "SessionStart", "s1")
    expect(code == 0 and "config.json" in out, "session start reports the configuration error into context")


def scenario_root_refusals(ws: Path) -> None:
    repo = make_repo(ws / "repo")
    code, out, err = cli(repo / "src", "check")
    expect(code == 0 and "ledger: consistent" in out, f"a subfolder finds the kit through Git: {out + err!r}")
    bare = ws / "bare.git"
    git(ws, "init", "-q", "--bare", "-b", "main", str(bare))
    git(ws, "-C", str(repo), "push", "-q", str(bare), "main")
    git(bare, "worktree", "add", "-q", str(ws / "bare-wt"), "main")
    code, _, err = cli(ws / "bare-wt", "check")
    expect(code == 3 and "bare repository" in err, f"a bare layout refuses plainly: {err!r}")
    plain = ws / "plain"
    plain.mkdir()
    code, _, err = cli(plain, "check")
    expect(code == 3 and "not inside a Git worktree" in err, f"plain directory: {err!r}")
    code, _, err = cli(repo, "start")
    expect(code == 3 and "needs --session" in err, f"start without a session id refuses plainly: {err!r}")


def scenario_ledger_findings(ws: Path) -> None:
    repo = make_repo(ws / "repo", {".sako/work/TASKS.md": ledger(
        row("T-1"), row("T-1", "duplicate"), row("T-2", status="Done"),
        row("T-3", status="blocked by T-8"), row("T-4", pri="high"), row("T-5", "cites D-1 in its text"))})
    code, out, _ = cli(repo, "check")
    expect(code == 1, "findings must exit 1")
    for fragment in ("T-1 is defined by 2 rows", 'T-2 has status "Done"', 'T-3 has status "blocked by T-8"',
                     'T-4 has Pri "HIGH"; use P1, P2 or P3'):
        expect(fragment in out, f"missing finding {fragment!r} in {out!r}")
    expect("D-1" not in out and "T-8" not in out.split("T-3 has status")[0], "citations are not policed")
    write(repo, {".sako/work/TASKS.md": ledger(row("T-1", status="Open"), row("T-2", status="**parked: soon**"))})
    code, out, _ = cli(repo, "check")
    expect(code == 0, f"the state word may carry case, emphasis and a reason: {out!r}")
    write(repo, {".sako/work/TASKS.md": ledger(row("T-1")) + "\n```\n" + row("T-7") + "\n```\n",
                 ".sako/work/DONE.md": "# Done\n"})
    code, out, _ = cli(repo, "check")
    expect(code == 0, f"rows inside a code fence are ignored: {out!r}")
    succeeds(installed_copy(repo, "add", "After the fence", "--done-when", "Numbered", "--scope", "src/"))
    expect("| T-10 |" in (repo / ".sako/work/TASKS.md").read_text(),
           "numbering counts every cited ID, fenced or not (the parked T-9 included)")
    write(repo, {".sako/work/TASKS.md": ledger(row("T-1", status="claimed",
                 claim="sk-0101-bbbbbbbbbbbb (took over from sk-0101-aaaaaaaaaaaa: that session is gone)"))})
    code, out, _ = cli(repo, "check")
    expect("T-1 is claimed by sk-0101-bbbbbbbbbbbb" in out, "the holder is the first marker; the old one in the note does not count")
    (repo / ".sako/work/TASKS.md").write_bytes((HEADER + "| T-1 | P2 | caf\xe9 | x | open | `src/` | - | - |\n").encode("latin-1"))
    code, _, err = cli(repo, "check")
    expect(code == 3 and "not UTF-8" in err, f"a non-UTF-8 ledger is a refusal: {err!r}")
    for malformed in ("| T-1 | four | cells | only |", "| T-1 | P2 | five | x | open |"):
        write(repo, {".sako/work/TASKS.md": ledger(malformed)})
        code, _, err = cli(repo, "check")
        expect(code == 3 and "tasks:5: a task row needs" in err, f"malformed row: {err!r}")
    code, _, err = hook(repo, "Stop", "s1")
    expect(code == 2 and "a task row needs" in err, "a malformed tasks file blocks the stop gate")
    write(repo, {".sako/work/TASKS.md": "| ID | Task | Status | Claim |\n|---|---|---|---|\n| T-1 | old | open | - |\n"})
    code, _, err = cli(repo, "check")
    expect(code == 3 and "tasks:1: the table misses Pri, Done when, Scope, After, Source" in err, err)


def scenario_presence_peers_and_stale_claims(ws: Path) -> None:
    need(not WINDOWS, LEASE)
    repo = make_repo(ws / "repo", {".sako/work/TASKS.md": ledger(row("T-1"), row("T-2", touches="`docs/`"))})
    one, two = fake_agent(), fake_agent()
    code, out, _ = cli(repo, "start", session="s1", pid=one)
    expect(code == 0 and "peers: none" in out and marker("s1") in out, out)
    claim(repo, "T-1", "s1")
    code, out, _ = cli(repo, "start", session="s2", pid=two)
    expect(f"peer {marker('s1')}" in out and "T-1 (claimed) touches src/" in out
           and "[from the claim column]" in out, out)
    code, out, _ = cli(repo, "start", session="s1", pid=one)
    expect(out.count(marker("s1")) >= 1 and f"peer {marker('s2')}" in out, "resume keeps the marker and sees peers")
    entries = list((repo / ".sako/state/live").glob("*.json"))
    expect(len(entries) == 2, f"two live entries expected, found {len(entries)}")
    kill(one)
    code, out, _ = cli(repo, "check", session="s2", pid=two)
    expect("T-1 is claimed by" in out and "not a live session" in out, f"stale claim expected: {out!r}")
    code, out, _ = cli(repo, "start", session="s2", pid=two)
    expect("peers: none" in out, "a dead session is swept from the roster")
    cli(repo, "end", session="s2", pid=two)
    expect(not list((repo / ".sako/state/live").glob("*.json")), "end removes the entry")
    code, out, _ = cli(repo, "start", session="s3", pid=two)
    expect(code == 0, out)
    (repo / ".sako/state/live" / "deadbeef0000.json").write_text(json.dumps({"marker": "sk-0101-deadbeef0000", "pid": 2 ** 22}))
    code, out, _ = cli(repo, "start", session="s3", pid=two)
    expect("deadbeef" not in out and not (repo / ".sako/state/live/deadbeef0000.json").exists(),
           "an entry whose process is gone is swept")


def scenario_next_frontier(ws: Path) -> None:
    text = ledger(row("T-1", "First P1", pri="P1", touches="`src/a/`"), row("T-2", "Second P2", touches="`src/a2/`"),
                  row("T-3", "Waiting P1", pri="P1", touches="`src/b/`", after="T-1"),
                  row("T-4", "Claimed", "claimed", "sk-0101-aaaaaaaaaaaa", "`src/c/`"),
                  row("T-5", "Collides", pri="P1", touches="`src/c/deep/`"), row("T-6", "Free P3", pri="P3", touches="`src/d/`"),
                  later=row("T-7", "Later", "parked: not now", touches="-"))
    repo = make_repo(ws / "repo", {".sako/work/TASKS.md": text})
    code, out, _ = cli(repo, "next")
    expect("next: T-1 (P1, the only ready P1 task): First P1" in out, out)
    expect("also ready: T-2 (P2), T-6 (P3)" in out, out)
    for fragment in ("T-3  after T-1", "T-4  claimed by sk-0101-aaaaaaaaaaaa", "T-5  scope overlaps T-4",
                     "T-7  parked: not now"):
        expect(fragment in out, f"missing {fragment!r}: {out!r}")
    write(repo, {".sako/work/DONE.md": done_file(row("T-1", "done one", "done", "sk-0101-aaaaaaaaaaaa")),
                 ".sako/work/TASKS.md": text.replace(row("T-1", "First P1", pri="P1", touches="`src/a/`") + "\n", "")})
    code, out, _ = cli(repo, "next")
    expect("next: T-3 (P1, the only ready P1 task): Waiting P1" in out, f"a finished prerequisite releases its dependent: {out!r}")


def scenario_scopes_and_overlap(ws: Path) -> None:
    repo = make_repo(ws / "repo", {".sako/work/TASKS.md": ledger(
        row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa", touches="`src/`"),
        row("T-2", status="claimed", claim="sk-0101-bbbbbbbbbbbb", touches="`src/app/`, `docs/`"),
        row("T-3", status="claimed", claim="sk-0101-cccccccccccc", touches="`tests/`"))})
    code, out, _ = cli(repo, "check")
    expect("T-1 and T-2 are both claimed and their scopes overlap" in out, out)
    expect("T-3" not in out or "overlap" not in out.split("T-3")[0][-40:], "T-3 does not overlap")


def scenario_verify_evidence(ws: Path) -> None:
    repo = make_repo(ws / "repo", {
        "check.py": "import pathlib, sys\nsys.exit(int(pathlib.Path('control').read_text() or 0))\n",
        "control": "0\n",
        ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src"]})})
    stamp = repo / ".sako/state/stamp.json"
    code, out, _ = cli(repo, "verify")
    expect(code == 0 and stamp.exists() and "PASS" in out, out)
    code, out, _ = cli(repo, "check")
    expect("evidence: fresh" in out, out)
    write(repo, {"src/app.py": "print(2)\n"})
    code, out, _ = cli(repo, "check")
    expect("evidence: stale" in out and "moved since the last pass" in out, out)
    cli(repo, "verify")
    commit_all(repo, "same content committed")
    code, out, _ = cli(repo, "check")
    expect("evidence: fresh" in out, "committing identical content keeps the evidence valid")
    write(repo, {"src/new_file.py": "x = 1\n"})
    code, out, _ = cli(repo, "check")
    expect("evidence: stale" in out, "a new untracked file under the checked paths counts")
    cli(repo, "verify")
    write(repo, {"docs/guide.md": "Operational guidance\n", ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src", "docs"]})})
    code, out, _ = cli(repo, "check")
    expect("check command or paths changed" in out, out)
    write(repo, {"control": "1\n"})
    code, out, _ = cli(repo, "verify")
    expect(code == 1 and not stamp.exists() and "FAILED" in out, "a failing check removes the stamp")
    write(repo, {"control": "0\n", "check.py": "import pathlib\npathlib.Path('src/generated.txt').write_text('changed during run')\n"})
    code, out, _ = cli(repo, "verify")
    expect(code == 1 and not stamp.exists() and "changed during the run" in out and "The run created src/generated.txt: "
           "ignore generated files in .gitignore" in out, f"the run's own files are named with the fix: {out!r}")
    write(repo, {".sako/config.json": json.dumps({"verify_command": None})})
    code, _, err = cli(repo, "verify")
    expect(code == 3 and "no verify_command" in err, err)
    write(repo, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["srcc"]})})
    code, _, err = cli(repo, "verify")
    expect(code == 3 and "does not exist" in err and not stamp.exists(), f"a verify_paths typo is a refusal: {err!r}")
    write(repo, {".sako/config.json": json.dumps({"verify_command": ["no-such-command-xyz"], "verify_paths": ["src"]})})
    code, _, err = cli(repo, "verify")
    expect(code == 3 and "cannot run" in err, f"a missing command is a refusal: {err!r}")
    write(repo, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": []}),
                 "control": "0\n", "check.py": "raise SystemExit(0)\n"})
    code, out, _ = cli(repo, "verify")
    expect(code == 0 and "covered" in out, out)
    write(repo, {".sako/work/TASKS.md": ledger(row("T-1", "edited after the check"))})
    code, out, _ = cli(repo, "check")
    expect("evidence: fresh" in out, "the ledger files are never covered content in single mode")


def scenario_stop_gate(ws: Path) -> None:
    repo = make_repo(ws / "repo", {
        ".sako/work/TASKS.md": ledger(row("T-1"), row("T-2", touches="`docs/`")),
        ".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "raise SystemExit(0)"], "verify_paths": ["src"]})})
    pid = fake_agent()
    cli(repo, "start", session="s1", pid=pid)
    claim(repo, "T-1", "s1")
    code, _, err = hook(repo, "Stop", "s1", pid=pid)
    expect(code == 0, f"nothing closed, nothing to gate: {err!r}")
    write(repo, {"src/app.py": "print(3)\n"})
    close(repo, "T-1", "s1")
    code, _, err = hook(repo, "Stop", "s1", pid=pid)
    expect(code == 2 and "Proven: T-1 closed after checked content changed" in err and "the check is none" in err, err)
    code, _, _ = hook(repo, "Stop", "s1", pid=pid, stop_hook_active=True)
    expect(code == 0, "the retry guard ends the chain")
    cli(repo, "verify")
    code, _, err = hook(repo, "Stop", "s1", pid=pid)
    expect(code == 2 and "Committed: T-1 is closed but its changes are not committed: src/app.py" in err,
           f"fresh evidence alone does not land work: {err!r}")
    commit_all(repo, "land checked task")
    code, _, err = hook(repo, "Stop", "s1", pid=pid)
    expect(code == 0, f"fresh evidence and committed work clear the gate: {err!r}")
    write(repo, {"src/app.py": "print(4)\n"})
    code, _, err = hook(repo, "Stop", "s1", pid=pid)
    expect(code == 2 and "the check is stale" in err, err)
    cli(repo, "verify")
    commit_all(repo, "land T-1")
    code, _, err = hook(repo, "Stop", "s1", pid=pid)
    expect(code == 0, f"evidence survives the commit of identical content: {err!r}")
    # documentation-only close never asks for product evidence
    two = fake_agent()
    cli(repo, "start", session="s2", pid=two)
    claim(repo, "T-2", "s2", touches="`docs/`")
    write(repo, {"docs/notes.md": "notes\n"})
    close(repo, "T-2", "s2", touches="`docs/`")
    (repo / ".sako/state/stamp.json").unlink()
    commit_all(repo, "land documentation task")
    code, _, err = hook(repo, "Stop", "s2", pid=two)
    expect(code == 0, f"docs-only close with untouched checked paths: {err!r}")
    # a change outside every task this session claimed is unrecorded work
    write(repo, {".sako/work/TASKS.md": ledger(row("T-3", touches="`docs/`")), "elsewhere/file.txt": "x\n"})
    claim(repo, "T-3", "s2", touches="`docs/`")
    code, _, err = hook(repo, "Stop", "s2", pid=two)
    expect(code == 2 and "Recorded: changed this session but covered by no task" in err
           and "elsewhere/file.txt" in err, err)


def scenario_interruption_recovery(ws: Path) -> None:
    repo = make_repo(ws / "repo", {".sako/config.json": json.dumps(
        {"verify_command": [sys.executable, "-c", "raise SystemExit(0)"], "verify_paths": ["src"]})})
    pid = fake_agent()
    cli(repo, "start", session="s1", pid=pid)
    claim(repo, "T-1", "s1")
    write(repo, {"src/app.py": "print(5)\n"})
    commit_all(repo, "half the work landed")
    shutil.rmtree(repo / ".sako/state/live")
    code, out, _ = cli(repo, "status", session="s1")
    expect("no presence entry" in out and "your rows: T-1 (claimed)" in out, out)
    close(repo, "T-1", "s1")
    code, _, err = hook(repo, "Stop", "s1")
    expect(code == 2 and "Proven: T-1 closed after checked content changed" in err and "the check is none" in err, err)
    cli(repo, "verify")
    commit_all(repo, "record recovered work")
    code, _, err = hook(repo, "Stop", "s1")
    expect(code == 0, f"a cold session closes with fresh evidence: {err!r}")


def scenario_init_and_hook_dispatch(ws: Path) -> None:
    repo = ws / "adopter"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write(repo, {".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(ls *)"]}})})
    shared = (repo / ".claude/settings.json").read_bytes()
    result = subprocess.run([sys.executable, str(SAKO), "init", "--client", "claude"], cwd=repo,
                            capture_output=True, text=True, env=ENV)
    expect(result.returncode == 0, result.stderr)
    for relative in (".sako/sako.py", ".sako/work/TASKS.md", ".sako/config.json", ".sako/SAKO.md",
                     ".claude/settings.local.json"):
        expect((repo / relative).exists(), f"init must create {relative}")
    expect(not (repo / "AGENTS.md").exists(), "the local footprint writes no instruction pointer")
    expect(not (repo / ".codex/hooks.json").exists(), "only the selected client gets hooks")
    expect(not (repo / ".sako/work/DONE.md").exists(), "completed record appears with first completion")
    expect((repo / ".claude/settings.json").read_bytes() == shared, "shared client settings stay untouched")
    again = seed(repo)
    expect(again.returncode == 0 and "nothing changed" in again.stdout, again.stdout + again.stderr)
    settings = json.loads((repo / ".claude/settings.local.json").read_text())
    expect(all(len(settings["hooks"][event]) == 1 for event in ("SessionStart", "SessionEnd", "Stop")), settings)
    expect(seed(repo, "--client", "claude").returncode == 0, "repeat installation succeeds")
    commit_all(repo, "adopt")
    vendored = repo / ".sako/sako.py"
    pid = fake_agent()
    env = dict(ENV, SAKO_AGENT_PID=str(pid))
    start = subprocess.run([sys.executable, str(vendored), "hook", "claude"], env=env, cwd=repo,
                           input=json.dumps({"session_id": "x1", "hook_event_name": "SessionStart"}),
                           capture_output=True, text=True)
    expect(start.returncode == 0 and "your marker" in start.stdout, start.stdout + start.stderr)
    expect(list((repo / ".sako/state/live").glob("*.json")), "the hook recorded presence")
    end = subprocess.run([sys.executable, str(vendored), "hook", "claude"], env=env, cwd=repo,
                         input=json.dumps({"session_id": "x1", "hook_event_name": "SessionEnd"}),
                         capture_output=True, text=True)
    expect(end.returncode == 0 and not list((repo / ".sako/state/live").glob("*.json")), "session end cleans up")
    nothing = subprocess.run([sys.executable, str(vendored), "hook"], env=env, cwd=repo,
                             input="{}", capture_output=True, text=True)
    expect(nothing.returncode == 0 and "no session_id" in nothing.stderr and not nothing.stdout, nothing.stderr)
    other = subprocess.run([sys.executable, str(vendored), "hook"], env=env, cwd=repo,
                           input=json.dumps({"session_id": "x1", "hook_event_name": "PreToolUse"}),
                           capture_output=True, text=True)
    expect(other.returncode == 0, "unknown events are ignored")
    fresh = subprocess.run([sys.executable, str(vendored), "init"], cwd=repo,
                           capture_output=True, text=True, env=ENV)
    expect(fresh.returncode == 3 and "uvx sako init" in fresh.stderr, "a vendored copy cannot init")
    (repo / "docs").mkdir()
    sub = subprocess.run([sys.executable, str(SAKO), "init"], cwd=repo / "docs",
                         capture_output=True, text=True, env=ENV)
    expect(sub.returncode == 0 and "nothing changed" in sub.stdout, f"init from a subfolder finds the main kit: {sub.stderr!r}")
    expect(git(repo, "status", "--porcelain") == "", "nothing SAKO wrote shows as a change")


def scenario_worktrees_share_presence(ws: Path) -> None:
    repo = make_repo(ws / "repo")
    git(repo, "worktree", "add", "-q", str(ws / "repo-b"))
    expect(not (ws / "repo-b/.sako").exists(), "a linked worktree has no kit folder of its own")
    one, two = fake_agent(), fake_agent()
    cli(repo, "start", session="s1", pid=one)
    code, out, _ = cli(ws / "repo-b", "start", session="s2", pid=two)
    expect(f"peer {marker('s1')}" in out, "worktrees of one repository share the roster")
    write(repo, {".sako/config.json": json.dumps({"verify_command": ["true"], "verify_paths": ["src"]})})
    code, out, err = cli(ws / "repo-b", "verify")
    expect(code == 0 and (repo / ".sako/state/worktrees/repo-b/stamp.json").exists()
           and not (repo / ".sako/state/stamp.json").exists(), f"each worktree keeps its own stamp: {out + err}")
    expect(not (ws / "repo-b/.sako").exists(), "no command creates a kit folder in a linked worktree")


def package_map() -> dict[str, str]:
    """The wheel's file map from pyproject.toml: checkout path -> installed path."""
    text = (HERE.parent / "pyproject.toml").read_text()
    section = text.split("[tool.hatch.build.targets.wheel.force-include]", 1)[1].split("\n[", 1)[0]
    return dict(re.findall(r'^"([^"]+)" = "([^"]+)"$', section, re.M))


def packaged_kit(root: Path) -> Path:
    """The installed package layout, as uvx unpacks the wheel; returns its runtime file."""
    for source, target in package_map().items():
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE.parent / source, root / target)
    return root / "sako" / "__init__.py"


def installed_copy(root: Path, *args: str) -> subprocess.CompletedProcess:
    """The checkout's runtime run inside root, for fixtures that never installed the kit."""
    return subprocess.run([sys.executable, str(SAKO), *with_done_when(args)], cwd=root, env=ENV,
                          capture_output=True, text=True)


def seed(root: Path, *args: str, source: Path = SAKO) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(source), "init", *args], cwd=root,
                          capture_output=True, text=True, env=ENV)


def installed(root: Path, *args: str, session: str | None = None, pid: int | None = None):
    env = dict(ENV, PYTHONPATH="")
    if pid:
        env["SAKO_AGENT_PID"] = str(pid)
    command = [sys.executable, str(root / ".sako/sako.py"), *with_done_when(args)]
    if session:
        command += ["--session", session]
    return subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)


def succeeds(result: subprocess.CompletedProcess) -> str:
    expect(result.returncode == 0, result.stdout + result.stderr)
    return result.stdout


def scenario_neutral_package_and_shared_host_sessions(ws: Path) -> None:
    """The same installed CLI serves two conversations without selecting a client."""
    need(not WINDOWS, LEASE)
    rules = "# Existing rules\nUse the project brief and preserve observed behavior.\n"
    unrelated = {"CLAUDE.md": rules, ".cursor/hooks.json": '{"version": 1, "hooks": {}}\n',
                 ".agents/skills/existing/SKILL.md": "Existing project workflow\n"}
    repo = make_repo(ws / "project", {**unrelated, "brief.md": "Observe the existing program, then record a follow-up question.\n",
        ".sako/work/TASKS.md": ledger(row("T-1", "Observe the existing program; record its output", touches="`notes/observation.md`"),
                                row("T-2", "Record the next question; retain it in project notes", touches="`notes/question.md`")),
        ".sako/config.json": json.dumps({"verify_command": None})})
    kit = packaged_kit(ws / "site")
    succeeds(seed(repo, "--client", "none", source=kit))
    shutil.rmtree(ws / "site")
    manifest = json.loads((repo / ".sako/install.json").read_text())
    expect(manifest["clients"] == [], "the shared entry selects no named adapter")
    expect(not (repo / "AGENTS.md").exists(), "the local footprint writes no instruction pointer")
    expect((repo / ".agents/skills/sako/SKILL.md").is_file(), "shared skill is present")
    commit_all(repo, "connect the shared entry to existing project work")
    host = fake_agent()
    succeeds(installed(repo, "start", session="host-first", pid=host))
    succeeds(installed(repo, "claim", "T-1", session="host-first"))
    peer = succeeds(installed(repo, "start", session="host-second", pid=host))
    expect(f"peer {marker('host-first')}" in peer, "a shared host retains the earlier conversation")
    refusal = installed(repo, "claim", "T-1", "--takeover", "Inspect existing work", session="host-second")
    expect(refusal.returncode == 3 and "held by a live session" in refusal.stderr, "a live conversation keeps its claim")
    succeeds(installed(repo, "claim", "T-2", session="host-second"))
    observed = succeeds(subprocess.run([sys.executable, "src/app.py"], cwd=repo,
                                      capture_output=True, text=True, env=ENV))
    expect(observed == "1\n", "observe the fixture's actual output")
    write(repo, {"notes/observation.md": "The existing program prints 1. Inputs and product intent still need clarification.\n"})
    succeeds(installed(repo, "close", "T-1", "--evidence", "Ran the existing program and recorded its output; manual discovery only",
                       session="host-first"))
    commit_all(repo, "record the observed program behavior")
    succeeds(installed(repo, "check", "--gate", session="host-first"))
    succeeds(installed(repo, "end", session="host-first"))
    live = [json.loads(p.read_text())["session_id"] for p in (repo / ".sako/state/live").glob("*.json")]
    expect(live == ["host-second"], "ending one conversation preserves the other")
    write(repo, {"notes/question.md": "Which input should determine the printed number? Await project direction before changing behavior.\n"})
    succeeds(installed(repo, "close", "T-2", "--evidence", "Recorded the unresolved input question; no product change or automated pass claimed",
                       session="host-second"))
    commit_all(repo, "leave the next product question in project notes")
    succeeds(installed(repo, "check", "--gate", session="host-second"))
    succeeds(installed(repo, "end", session="host-second"))
    succeeds(installed(repo, "remove"))
    for relative, content in unrelated.items():
        expect((repo / relative).read_text() == content, f"unrelated client or workflow content survives: {relative}")
    expect(not (repo / ".agents/skills/sako/SKILL.md").exists(), "removal cleans the owned shared entry")
    expect((repo / "notes/observation.md").exists() and (repo / "notes/question.md").exists(), "project findings survive")


def scenario_greenfield_adoption_and_fresh_pickup(ws: Path) -> None:
    repo = ws / "new-project"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    expect("Hooks: none, no client found here" in succeeds(seed(repo)), "a new repository has no client signal")
    files = {p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
             and "state" not in p.parts}
    expect(files == {".sako/SAKO.md", ".sako/install.json", ".sako/config.json", ".sako/sako.py",
                     ".sako/work/TASKS.md", ".agents/skills/sako/SKILL.md"}, files)
    expect(git(repo, "status", "--porcelain") == "", "a fresh install changes nothing Git sees")
    expect("no verify_command configured" in succeeds(installed(repo, "check")), "no check is not a pass")
    expect("no recorded work" in succeeds(installed(repo, "next")), "empty setup is not complete adoption")
    write(repo, {"README.md": "# Greeting tool\nPrint a personal greeting for a named visitor.\n",
                 ".gitignore": "__pycache__/\n"})
    config = json.loads((repo / ".sako/config.json").read_text())
    config.update(verify_command=[sys.executable, "-m", "unittest", "test_greeting"],
                  verify_paths=["greeting.py", "test_greeting.py"])
    write(repo, {".sako/config.json": json.dumps(config)})
    succeeds(installed(repo, "add", "A named visitor receives a personal greeting", "--scope", "greeting.py", "--scope", "test_greeting.py"))
    commit_all(repo, "record purpose and first slice")
    first = fake_agent()
    succeeds(installed(repo, "start", session="first-session", pid=first))
    succeeds(installed(repo, "claim", "T-1", session="first-session"))
    write(repo, {"greeting.py": "def greet(name):\n    return f'Hello, {name}!'\n",
                 "test_greeting.py": "import unittest\nfrom greeting import greet\nclass Greeting(unittest.TestCase):\n    def test_name(self):\n        self.assertEqual(greet('Visitor'), 'Hello, Visitor!')\n"})
    succeeds(installed(repo, "verify"))
    succeeds(installed(repo, "check"))
    succeeds(installed(repo, "close", "T-1", "--evidence", "A named greeting is implemented; test_greeting checks its exact output", session="first-session"))
    expect(installed(repo, "check", "--gate", session="first-session").returncode == 2, "uncommitted work cannot complete")
    succeeds(installed(repo, "add", "Reject an empty visitor name with a clear error", "--scope", "greeting.py", "--scope", "test_greeting.py"))
    commit_all(repo, "deliver named greeting and record next slice")
    succeeds(installed(repo, "check", "--gate", session="first-session"))
    succeeds(installed(repo, "end", session="first-session"))
    fresh = succeeds(installed(repo, "start", session="fresh-session", pid=fake_agent()))
    expect("T-2" in fresh and ".sako/SAKO.md" in fresh, fresh)
    expect("test_greeting checks its exact output" in (repo / ".sako/work/DONE.md").read_text(), "completion proof survives")
    succeeds(installed(repo, "claim", "T-2", session="fresh-session"))
    expect((repo / ".sako/SAKO.md").is_file(), "daily method is local")
    expect(not (repo / "templates").exists(), "daily commands need no source resources")


def scenario_existing_project_reuses_context_and_checks(ws: Path) -> None:
    need(symlinks(), "creating symlinks needs a privilege here")
    repo = make_repo(ws / "existing")
    (repo / ".sako/work/TASKS.md").unlink()
    (repo / ".sako/work/DONE.md").unlink()
    instructions = "# Project instructions\nUse the existing numeric API and its unittest checks.\n"
    brief = "# Totals\nAdding numeric strings returns a number. Keep the public function named total.\n"
    write(repo, {"AGENTS.md": instructions, "product/brief.md": brief,
                 "src/app.py": "def total(values):\n    return ''.join(values)\n",
                 "tests/test_total.py": "import unittest\nfrom src.app import total\nclass Totals(unittest.TestCase):\n    def test_strings(self):\n        self.assertEqual(total(['2', '3']), 5)\n",
                 ".agents/skills/existing/SKILL.md": "Existing project skill\n",
                 ".sako/work/NOW.md": "# Work\n", ".sako/config.json": json.dumps({
                     "tasks": "work/NOW.md", "done": "work/FINISHED.md",
                     "verify_command": [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                     "verify_paths": ["src", "tests"]})})
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    commit_all(repo, "existing project with a known failing check")
    write(repo, {"owner-notes.txt": "Unrelated work in progress\n"})
    succeeds(seed(repo, "--client", "codex"))
    expect((repo / "AGENTS.md").read_text() == instructions, "project instructions stay untouched")
    expect((repo / "CLAUDE.md").is_symlink(), "router alias survives")
    expect((repo / "product/brief.md").read_text() == brief, "reuse the specification")
    expect(not (repo / ".sako/work/TASKS.md").exists(), "custom records have no duplicate default")
    expect(not (repo / "docs/PRD.md").exists(), "no second specification")
    expect((repo / ".codex/hooks.json").is_file(), "selected clients get hooks by default")
    succeeds(installed(repo, "add", "Numeric strings total as numbers; existing assertion fails with concatenation", "--scope", "src", "--scope", "tests"))
    baseline = installed(repo, "verify")
    expect(baseline.returncode == 1, baseline.stdout + baseline.stderr)
    expect(not (repo / ".sako/state/stamp.json").exists(), "failing baseline is not a pass")
    expect("evidence: none" in succeeds(installed(repo, "check")), "a failing baseline leaves no evidence")
    expect(not (repo / ".sako/state/stamp.json").exists(), "structural readiness never supplies a pass")
    expect(git(repo, "status", "--porcelain") == "?? owner-notes.txt", "adoption changes nothing Git sees")
    succeeds(installed(repo, "start", session="repair-session", pid=fake_agent()))
    succeeds(installed(repo, "claim", "T-1", session="repair-session"))
    write(repo, {"src/app.py": "def total(values):\n    return sum(int(value) for value in values)\n"})
    refusal = installed(repo, "close", "T-1", "--evidence", "Not yet checked", session="repair-session")
    expect(refusal.returncode == 3 and "verify before closing" in refusal.stderr, refusal.stderr)
    succeeds(installed(repo, "verify"))
    succeeds(installed(repo, "close", "T-1", "--evidence", "Numeric strings now produce 5 for 2 and 3; the existing total test passes", session="repair-session"))
    git(repo, "add", "src/app.py")
    git(repo, "commit", "-qm", "repair numeric totals")
    succeeds(installed(repo, "check", "--gate", session="repair-session"))
    expect(git(repo, "status", "--porcelain") == "?? owner-notes.txt", "unrelated work is neither staged nor changed")
    succeeds(installed(repo, "end", session="repair-session"))
    fresh = succeeds(installed(repo, "start", session="pickup-session", pid=fake_agent()))
    expect(".sako/work/NOW.md" in fresh and ".sako/work/FINISHED.md" in fresh, fresh)
    expect("Numeric strings now produce 5" in (repo / ".sako/work/FINISHED.md").read_text(), "new session can inspect the result")
    expect((repo / ".agents/skills/existing/SKILL.md").read_text() == "Existing project skill\n", "existing skill survives")


def scenario_update_and_removal_preserve_project_data(ws: Path) -> None:
    need(symlinks(), "creating symlinks needs a privilege here")
    repo = make_repo(ws / "project")
    write(repo, {"AGENTS.md": "# Existing rules\nKeep the public API stable.\n",
                 ".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(ls *)"]},
                     "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo existing"}]}]}}),
                 ".codex/config.toml": "# Existing configuration\n"})
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    succeeds(seed(repo, "--client", "claude", "--client", "codex"))
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    again = succeeds(seed(repo, "--client", "claude", "--client", "codex"))
    expect("nothing changed" in again, again)
    expect(all(p.read_bytes() == content for p, content in before.items()), "repeat adoption changes nothing")
    release = ws / "next-release"
    release.mkdir()
    for name in ("templates", "skills"):
        shutil.copytree(SAKO.parent / name, release / name)
    shutil.copy2(SAKO, release / "sako.py")
    write(release, {"SAKO.md": (SAKO.parent / "SAKO.md").read_text() + "\nFixture update.\n"})
    succeeds(seed(repo, "--update", source=release / "sako.py"))
    expect((repo / ".sako/SAKO.md").read_text().endswith("Fixture update.\n"), "managed method is updated")
    method = repo / ".sako/SAKO.md"
    method.write_text(method.read_text() + "Local change to preserve.\n")
    refusal = seed(repo, "--update")
    expect(refusal.returncode == 3 and "local content" in refusal.stderr, refusal.stderr)
    expect("Local change" in method.read_text(), "modified asset stays intact")
    removal = installed(repo, "remove")
    expect(removal.returncode == 3 and "local changes" in removal.stderr, removal.stderr)
    method.write_text((release / "SAKO.md").read_text())
    succeeds(installed(repo, "remove"))
    expect(not any((repo / p).exists() for p in (".sako/sako.py", ".sako/install.json", ".sako/SAKO.md")), "managed files removed")
    expect((repo / "AGENTS.md").read_text() == "# Existing rules\nKeep the public API stable.\n", "instructions untouched")
    expect((repo / "CLAUDE.md").is_symlink(), "shared router alias preserved")
    settings = json.loads((repo / ".claude/settings.json").read_text())
    expect(settings["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo existing", settings)
    expect(settings["permissions"]["allow"] == ["Bash(ls *)"], settings)
    expect((repo / ".codex/config.toml").read_text() == "# Existing configuration\n", "shared feature settings untouched")
    for path in (".sako/config.json", ".sako/work/TASKS.md", ".sako/work/DONE.md", "src/app.py"):
        expect((repo / path).read_bytes() == before[repo / path], f"project data preserved: {path}")


def scenario_invalid_install_has_no_partial_assets(ws: Path) -> None:
    repo = ws / "project"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write(repo, {".claude/settings.local.json": '{"hooks": {"Stop": false}}'})
    result = seed(repo, "--client", "claude")
    expect(result.returncode == 3 and "invalid hook settings" in result.stderr, result.stderr)
    expect(not (repo / ".sako").exists(), "preflight avoids partial installation, even an empty kit folder")


def scenario_codex_adapter_uses_the_git_root(ws: Path) -> None:
    need(not WINDOWS, NO_CODEX)
    repo = make_repo(ws / "project with spaces")
    succeeds(seed(repo, "--client", "codex"))
    hooks = json.loads((repo / ".codex/hooks.json").read_text())["hooks"]
    command = hooks["SessionStart"][0]["hooks"][0]["command"]
    result = subprocess.run(["sh", "-c", command], cwd=repo / "src", text=True, capture_output=True,
                            env=dict(ENV, SAKO_AGENT_PID=str(fake_agent())),
                            input=json.dumps({"session_id": "adapter-session", "hook_event_name": "SessionStart"}))
    expect(result.returncode == 0 and "your marker" in result.stdout, result.stdout + result.stderr)
    expect(list((repo / ".sako/state/live").glob("*.json")), "generated adapter works from a subdirectory")


def scenario_greenfield_discovery_without_automated_check(ws: Path) -> None:
    """Scripted planning choices exercise discovery records, not agent judgment."""
    repo = ws / "idea"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    succeeds(seed(repo, "--client", "none"))
    write(repo, {"README.md": "# Local sorter\nIdea: help a builder order a few notes locally.\n"
                 "First investigate whether a preview is enough; no file writes or remote storage.\n"})
    expect("no recorded work" in succeeds(installed(repo, "next")), "direction without work is not seeded")
    succeeds(installed(repo, "add", "Compare two sample note orders; record a preview recommendation and the next decision",
                       "--scope", "README.md"))
    setup = succeeds(installed(repo, "check"))
    expect("evidence: none" in setup and "no verify_command configured" in setup, setup)
    commit_all(repo, "seed a bounded investigation")
    succeeds(installed(repo, "start", session="discovery-session", pid=fake_agent()))
    succeeds(installed(repo, "claim", "T-1", session="discovery-session"))
    with (repo / "README.md").open("a") as brief:
        brief.write("\nFinding: alphabetical preview orders [zeta, alpha] as [alpha, zeta].\n"
                    "This is a paper example, not running software. Recommendation: a local preview.\n"
                    "The owner selected preview; next implement it without changing input files.\n")
    succeeds(installed(repo, "close", "T-1", "--evidence",
                       "Compared the two orders in README; preview selected for the first slice; no software tested",
                       session="discovery-session"))
    expect(installed(repo, "check", "--gate", session="discovery-session").returncode == 2,
           "discovery still needs durable committed records")
    commit_all(repo, "record the investigation and next action")
    succeeds(installed(repo, "check", "--gate", session="discovery-session"))
    succeeds(installed(repo, "end", session="discovery-session"))
    before = (repo / ".sako/work/TASKS.md").read_bytes()
    pickup = succeeds(installed(repo, "start", session="build-session", pid=fake_agent()))
    expect("next: nothing is ready" in pickup and "No planner? Start here" in pickup, pickup)
    expect((repo / ".sako/work/TASKS.md").read_bytes() == before, "pickup does not invent a task")
    succeeds(installed(repo, "check"))
    expect(installed(repo, "verify").returncode == 3, "null verification cannot produce a pass")
    expect(not (repo / ".sako/state/stamp.json").exists(), "manual evidence stays distinct")
    succeeds(installed(repo, "add", "Preview notes alphabetically without modifying input files; compare the sample in README",
                       "--scope", "preview.py", "--scope", "tests"))
    succeeds(installed(repo, "claim", "T-2", session="build-session"))
    expect("no software tested" in (repo / ".sako/work/DONE.md").read_text(), "pickup retains the evidence limit")


def scenario_unstructured_project_and_completed_goal(ws: Path) -> None:
    repo = ws / "unstructured"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write(repo, {"listing.py": "print('zeta\\nalpha')\n", "scratch.txt": "maybe remote sync one day?\n"})
    commit_all(repo, "unstructured working code")
    observed = subprocess.check_output([sys.executable, "listing.py"], cwd=repo, text=True, env=ENV)
    expect(observed == "zeta\nalpha\n", observed)
    succeeds(seed(repo, "--client", "none"))
    expect("no recorded work" in succeeds(installed(repo, "next")), "code alone does not supply intent")
    # The fixture supplies the owner's answer explicitly; code cannot infer it.
    write(repo, {"README.md": "# Listing\nObserved: listing.py prints zeta then alpha.\n"
                 "Owner's chosen outcome: alphabetical output for this local list.\n"
                 "Keep both entries; no remote sync. Complete when output is alpha then zeta.\n",
                 ".sako/config.json": json.dumps({"verify_paths": ["listing.py"],
                     "verify_command": [sys.executable, "-c", "import subprocess, sys; assert subprocess.check_output([sys.executable, 'listing.py'], text=True) == 'alpha\\nzeta\\n'"]})})
    succeeds(installed(repo, "add", "Alphabetical local listing matches README; preserve both entries", "--scope", "listing.py"))
    expect(installed(repo, "verify").returncode == 1, "existing behavior exposes the gap")
    succeeds(installed(repo, "check"))
    commit_all(repo, "record the selected outcome and baseline")
    succeeds(installed(repo, "start", session="listing-session", pid=fake_agent()))
    succeeds(installed(repo, "claim", "T-1", session="listing-session"))
    write(repo, {"listing.py": "print('\\n'.join(sorted(['zeta', 'alpha'])))\n"})
    succeeds(installed(repo, "verify"))
    succeeds(installed(repo, "close", "T-1", "--evidence", "The selected local listing is alphabetical; the configured output assertion passes; no further goal agreed",
                       session="listing-session"))
    commit_all(repo, "deliver the selected listing")
    succeeds(installed(repo, "check", "--gate", session="listing-session"))
    succeeds(installed(repo, "end", session="listing-session"))
    before = (repo / ".sako/work/TASKS.md").read_bytes()
    pickup = succeeds(installed(repo, "start", session="review-session", pid=fake_agent()))
    expect("next: nothing is ready" in pickup and "No planner? Start here" in pickup, pickup)
    succeeds(installed(repo, "next"))
    succeeds(installed(repo, "check"))
    expect((repo / ".sako/work/TASKS.md").read_bytes() == before, "a completed goal leaves no invented work")
    expect((repo / "scratch.txt").read_text() == "maybe remote sync one day?\n", "unselected ideas remain untouched")
    expect(not any((repo / p).exists() for p in ("docs/PRD.md", "docs/PHASES.md", "docs/DECISIONS.md")),
           "adoption needs no empty planning framework")


def scenario_replanning_preserves_authority_and_blockers(ws: Path) -> None:
    repo = make_repo(ws / "planned", {
        "brief.md": "# Export\nCurrent outcome: a local export. Owner feedback rules out remote accounts.\n",
        "TODO.md": "# Earlier ideas\n- Add accounts\n- Export locally\n- Consider remote sync\n",
        "AGENTS.md": "Use the existing planner for intent; brief.md is current. TODO.md is historical.\n",
        ".agents/skills/existing-planner/SKILL.md": "Prepare one action with its intent reference, success condition, scope, and blocking question.\n",
        ".sako/work/TASKS.md": ledger(
            row("T-1", "Historical account work; superseded by local-only feedback", "parked", "-"),
            row("T-2", "Choose export format; waiting for owner's decision in brief.md", "parked", "-", "`brief.md`"),
            row("T-3", "Local export follows the format selected in T-2", after="T-2"), later="")})
    preserved = {p: (repo / p).read_bytes() for p in ("brief.md", "TODO.md", ".agents/skills/existing-planner/SKILL.md")}
    succeeds(seed(repo, "--client", "codex"))
    before = (repo / ".sako/work/TASKS.md").read_bytes()
    ready = succeeds(installed(repo, "next"))
    expect("T-2" in ready and "T-3  after T-2" in ready and "No planner? Start here" in ready, ready)
    expect((repo / ".sako/work/TASKS.md").read_bytes() == before, "held work causes no unrelated task generation")
    commit_all(repo, "adopt around current authority and a real blocker")
    succeeds(installed(repo, "start", session="replan-session", pid=fake_agent()))
    expect(installed(repo, "claim", "T-1", session="replan-session").returncode == 3, "invalidated work stays parked")
    expect(installed(repo, "claim", "T-3", session="replan-session").returncode == 3, "dependency stays blocked")
    # An explicit fixture answer releases the decision. It is not inferred by SAKO.
    with (repo / "brief.md").open("a") as brief:
        brief.write("\nOwner selected one JSON file. T-2 is now resolved by this decision.\n")
    tasks = (repo / ".sako/work/TASKS.md").read_text().replace("waiting for owner's decision in brief.md | The sample output matches | parked",
                                                                 "owner selected JSON in brief.md | The sample output matches | open")
    (repo / ".sako/work/TASKS.md").write_text(tasks)
    succeeds(installed(repo, "claim", "T-2", session="replan-session"))
    succeeds(installed(repo, "close", "T-2", "--evidence", "Owner selected JSON in brief.md; manual decision evidence, no software check",
                       session="replan-session"))
    succeeds(installed(repo, "claim", "T-3", session="replan-session"))
    for path in ("TODO.md", ".agents/skills/existing-planner/SKILL.md"):
        expect((repo / path).read_bytes() == preserved[path], f"existing authority preserved: {path}")
    expect("T-1" in (repo / ".sako/work/TASKS.md").read_text(), "retired intent retains its identity and reason")
    expect("Consider remote sync" not in (repo / ".sako/work/TASKS.md").read_text(), "no copied backlog")


def scenario_external_plan_intake_and_completion(ws: Path) -> None:
    """Replay the phase-plan handoff; the fixture supplies the agent's interpretation."""
    repo = ws / "project"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    plan = ("# Local note preview\n\n## Discovery\n"
            "- [ ] N1: Find regular top-level text-note filenames; preserve names and bytes.\n"
            "  Complete when filtering and a clear missing-folder error are checked.\n"
            "## Preview\n- [ ] N2: Print the filenames alphabetically, after N1.\n"
            "  Complete when alpha.txt precedes zeta.txt without modifying inputs.\n")
    instructions = ("# Project instructions\nPLAN.md is the selected work source. Continue from its plan.\n"
                    "After evaluating a step, update its checkbox and cite the local evidence.\n")
    write(repo, {"PLAN.md": plan, "AGENTS.md": instructions, ".gitignore": "__pycache__/\n",
                 "src/notes.py": "def note_names(folder):\n    raise NotImplementedError\n",
                 "tests/test_notes.py": """from pathlib import Path
import tempfile
import unittest
from src.notes import note_names

class Discovery(unittest.TestCase):
    def test_filtering_preserves_input_names_and_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("zeta.txt", "alpha.txt", "other.md"):
                (root / name).write_text(name)
            (root / "nested").mkdir()
            (root / "nested" / "hidden.txt").write_text("nested")
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(set(note_names(root)), {"zeta.txt", "alpha.txt"})
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(after, before)

    def test_missing_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "existing folder"):
                note_names(Path(folder) / "missing")
"""})
    succeeds(seed(repo, "--client", "none", source=packaged_kit(ws / "site")))
    shutil.rmtree(ws / "site")
    expect((repo / "AGENTS.md").read_text().startswith(instructions), "the selected planner survives installation")
    write(repo, {".sako/config.json": json.dumps({"verify_paths": ["src", "tests"],
                 "verify_command": [sys.executable, "-m", "unittest", "discover", "-s", "tests"]})})
    first = ("add", "Discover text-note names; check filtering, missing folders, and unchanged inputs",
             "--source", "PLAN.md#N1", "--scope", "src/", "--scope", "tests/", "--scope", "PLAN.md")
    second = ("add", "Print names alphabetically; compare the sample and unchanged inputs",
              "--source", "PLAN.md#N2", "--scope", "preview.py", "--after", "T-1")
    expect("T-1:" in succeeds(installed(repo, *first)), "first source identity")
    expect("T-2:" in succeeds(installed(repo, *second)), "dependent source identity")
    original = (repo / ".sako/work/TASKS.md").read_bytes()
    succeeds(installed(repo, *first))
    succeeds(installed(repo, *second))
    expect((repo / ".sako/work/TASKS.md").read_bytes() == original, "repeated intake is read-only")
    succeeds(installed(repo, "check"))
    commit_all(repo, "connect the selected plan and its execution records")
    succeeds(installed(repo, "start", session="intake-first", pid=fake_agent()))
    expect(installed(repo, "claim", "T-2", session="intake-first").returncode == 3, "dependency blocks early work")
    succeeds(installed(repo, "claim", "T-1", session="intake-first"))
    expect(installed(repo, "verify").returncode == 1, "record the failing baseline honestly")
    write(repo, {"src/notes.py": "from pathlib import Path\n\ndef note_names(folder):\n"
                 "    root = Path(folder)\n    if not root.is_dir():\n        raise ValueError('Choose an existing folder.')\n"
                 "    return [p.name for p in root.iterdir() if p.is_file() and not p.is_symlink() and p.suffix == '.txt']\n"})
    proof = "Two discovery tests pass: filtering preserves inputs and missing folders get a clear error"
    expect(installed(repo, "close", "T-1", "--evidence", proof, session="intake-first").returncode == 3,
           "changed code needs a fresh check")
    succeeds(installed(repo, "verify"))
    succeeds(installed(repo, "close", "T-1", "--evidence", proof, session="intake-first"))
    completed = (repo / ".sako/work/DONE.md").read_text()
    expect(all(value in completed for value in (first[1], "PLAN.md#N1", proof)), "intent, source and evidence survive together")
    expect((repo / "PLAN.md").read_text() == plan, "the runtime does not silently change source status")
    # The agent follows the project's chosen return procedure after checking upstream acceptance.
    write(repo, {"PLAN.md": plan.replace("[ ] N1:", "[x] N1:") + "\nN1 evidence: completed T-1; both discovery tests pass.\n"})
    expect(installed(repo, "check", "--gate", session="intake-first").returncode == 2, "uncommitted completion is visible")
    commit_all(repo, "complete the discovery step and return its evidence to the selected plan")
    succeeds(installed(repo, "check", "--gate", session="intake-first"))
    succeeds(installed(repo, "end", session="intake-first"))
    current = {path: (repo / path).read_bytes() for path in ("PLAN.md", ".sako/work/TASKS.md", ".sako/work/DONE.md")}
    expect("T-1: done" in succeeds(installed(repo, *first)), "completed intake reuses its original task")
    succeeds(installed(repo, *second))
    expect(all((repo / path).read_bytes() == data for path, data in current.items()), "repeat after completion changes no records")
    pickup = succeeds(installed(repo, "start", session="intake-pickup", pid=fake_agent()))
    expect("PLAN.md" in pickup and "next: T-2 (P2, the only ready task): Print names" in pickup,
           f"pickup exposes the source and next action: {pickup}")
    succeeds(installed(repo, "claim", "T-2", session="intake-pickup"))
    guidance = succeeds(installed(repo, "next"))
    expect("selected in project instructions" in guidance and "Without one, read No planner? Start here" in guidance,
           "held work returns to the chosen planner, with the method's fallback section")


def scenario_package_adoption_pickup_and_removal(ws: Path) -> None:
    kit = packaged_kit(ws / "site")
    repo = ws / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    succeeds(seed(repo, "--client", "codex", source=kit))
    expect("no recorded work" in succeeds(installed(repo, "next")), "fresh install points to Starting")
    write(repo, {"README.md": "# Answer tool\nThe first useful outcome is a local answer of 42.\n",
                 ".sako/config.json": json.dumps({"verify_paths": ["answer.py"],
                     "verify_command": [sys.executable, "-c", "from answer import answer; assert answer() == 42"]})})
    succeeds(installed(repo, "add", "Local answer is 42 as specified in README.md", "--scope", "answer.py"))
    commit_all(repo, "connect the first useful action")
    shutil.rmtree(ws / "site")
    succeeds(installed(repo, "start", session="package-first", pid=fake_agent()))
    succeeds(installed(repo, "claim", "T-1", session="package-first"))
    write(repo, {"answer.py": "def answer():\n    return 42\n"})
    succeeds(installed(repo, "verify"))
    succeeds(installed(repo, "close", "T-1", "--evidence", "The local answer is 42; the configured assertion passes",
                       session="package-first"))
    expect(installed(repo, "check", "--gate", session="package-first").returncode == 2,
           "package delivery retains the committed-work gate")
    commit_all(repo, "deliver the agreed answer")
    succeeds(installed(repo, "check", "--gate", session="package-first"))
    succeeds(installed(repo, "end", session="package-first"))
    pickup = succeeds(installed(repo, "start", session="package-pickup", pid=fake_agent()))
    expect("completed work is recorded" in pickup and "No planner? Start here" in pickup, pickup)
    succeeds(installed(repo, "check"))
    succeeds(installed(repo, "end", session="package-pickup"))
    refusal = installed(repo, "init")
    expect(refusal.returncode == 3 and "uvx sako init" in refusal.stderr,
           "the copy in a project names the package that seeds projects")
    preserved = {p: (repo / p).read_bytes()
                 for p in ("README.md", "answer.py", ".sako/config.json", ".sako/work/TASKS.md", ".sako/work/DONE.md")}
    kit = packaged_kit(ws / "site")
    expect("nothing changed" in succeeds(seed(repo, source=kit)), "repeat package install changes nothing")
    succeeds(installed(repo, "remove"))
    expect(not (repo / ".sako/sako.py").exists(), "removal deletes the owned runtime")
    expect(all((repo / p).read_bytes() == data for p, data in preserved.items()), "project work survives removal")
    succeeds(seed(repo, "--client", "codex", source=kit))
    succeeds(installed(repo, "check"))
    expect(all((repo / p).read_bytes() == data for p, data in preserved.items()), "reinstall preserves completed work")


SCENARIOS = [value for name, value in sorted(globals().items()) if name.startswith("scenario_")]


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # the report has a middle dot; Windows pipes default to cp1252
    keep = "--keep" in argv
    workspace = Path(tempfile.mkdtemp(prefix="sako-selftest-"))
    results = []
    for scenario in SCENARIOS:
        name = scenario.__name__.removeprefix("scenario_")
        started = time.monotonic()
        try:
            scenario(workspace / name)
            results.append((name, "PASS", f"{time.monotonic() - started:.1f}s"))
        except unittest.SkipTest as reason:
            results.append((name, "SKIP", str(reason)))
        except Exception as error:  # report every failure, keep going
            results.append((name, "FAIL", f"{type(error).__name__}: {error}"))
    for proc in FAKE_AGENTS:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    width = max(len(name) for name, _, _ in results)
    print(f"sako selftest · python {sys.version.split()[0]} · workspace {workspace}")
    for name, state, detail in results:
        print(f"  {state}  {name:<{width}}  {detail}")
    failed, skipped = [r for r in results if r[1] == "FAIL"], [r for r in results if r[1] == "SKIP"]
    print(f"{len(results) - len(failed) - len(skipped)} passed, {len(skipped)} skipped, {len(failed)} failed")
    if not keep and not failed:
        remove_tree(workspace)
    elif keep or failed:
        print(f"fixtures kept at {workspace}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
