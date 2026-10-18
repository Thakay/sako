"""Observable guarantees added after the first adoption and concurrency review."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import argparse
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest

import sako
import selftest as fixture

MINUTE = r"\d{4}-\d\d-\d\dT\d\d:\d\d[+-]\d{4}"  # a claim or close time, to the minute with its zone


class Workflow(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="sako-workflow-")
        self.root = fixture.make_repo(Path(self.workspace.name) / "project")
        self.agents = []

    def tearDown(self):
        for proc in self.agents:
            if proc.poll() is None:
                proc.terminate()
                proc.wait()
        self.workspace.cleanup()

    def start(self, session):
        proc = subprocess.Popen(["sleep", "60"])
        self.agents.append(proc)
        code, out, err = fixture.cli(self.root, "start", session=session, pid=proc.pid)
        self.assertEqual(code, 0, out + err)
        return proc

    def run_cli(self, *args, session=None):
        return fixture.cli(self.root, *args, session=session)

    def assert_ok(self, result):
        code, out, err = result
        self.assertEqual(code, 0, out + err)
        return out

    def test_spacing_and_fences_do_not_hide_tasks(self):
        text = "# Work\n" + fixture.HEADER + "|T-1|P1|A real task|Runs|open|`src/`|-|-|\n"
        text += "  | T-2 | P2 | Another | Runs | open | `docs/` | - | - |\n"
        text += "~~~markdown\n| T-3 | sample | open | - | `src/` |\n```\n~~~\n"
        rows = sako.parse_rows({"tasks": text})
        self.assertEqual([r.id for r in rows], ["T-1", "T-2"])
        for malformed in ("|t-1|P2|bad|x|open|-|-|-|", "| T-x | P2 | bad | x | open | - | - | - |", "|T-1|missing|"):
            with self.subTest(malformed=malformed), self.assertRaises(sako.SakoError):
                sako.parse_rows({"tasks": fixture.HEADER + malformed})

    def test_parallel_adds_allocate_and_persist_distinct_tasks(self):
        def add(i):
            return self.run_cli("add", f"Task {i}", "--scope", f"src/part{i}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(add, range(16)))
        ids = [self.assert_ok(result).split(":")[0] for result in results]
        self.assertEqual(len(set(ids)), 16)
        rows = sako.parse_rows({"tasks": (self.root / ".sako/work/TASKS.md").read_text()})
        self.assertEqual({r.id for r in rows}, set(ids) | {"T-1", "T-9"})
        before = (self.root / ".sako/work/TASKS.md").read_bytes()
        self.assertEqual(self.run_cli("next")[1], self.run_cli("next")[1])
        self.assertEqual((self.root / ".sako/work/TASKS.md").read_bytes(), before, "next only reads")

    def test_source_intake_is_atomic_and_survives_completion(self):
        task = "Preview note names; complete when order and unchanged inputs are checked"
        args = ("add", task, "--source", "PLAN.md#preview", "--scope", "src/")
        active = self.root / ".sako/work/TASKS.md"
        original = sako.parse_rows({"tasks": active.read_text()})
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.run_cli(*args), range(16)))
        ids = {self.assert_ok(result).split(":")[0] for result in results}
        self.assertEqual(ids, {"T-10"})
        retained = [r for r in sako.parse_rows({"tasks": active.read_text()}) if r.id != "T-10"]
        self.assertEqual([(r.id, r.task, r.status, r.claim, r.touches) for r in retained],
                         [(r.id, r.task, r.status, r.claim, r.touches) for r in original],
                         "adding a task keeps existing work and parked ideas")
        before = active.read_bytes()
        self.assert_ok(self.run_cli(*args))
        self.assertEqual(active.read_bytes(), before)
        self.start("intake-session")
        self.assert_ok(self.run_cli("claim", "T-10", session="intake-session"))
        claimed = active.read_bytes()
        self.assertIn("claimed", self.assert_ok(self.run_cli(*args)))
        self.assertEqual(active.read_bytes(), claimed, "repeat intake keeps ownership")
        evidence = "Observed the planned order and unchanged inputs"
        self.assert_ok(self.run_cli("close", "T-10", "--evidence", evidence, session="intake-session"))
        closed = self.root / ".sako/work/DONE.md"
        rows = sako.parse_rows({"done": closed.read_text()})
        self.assertEqual((rows[0].task, rows[0].source), (task, "PLAN.md#preview"))
        self.assertTrue(rows[0].evidence.startswith(evidence + " [sk-"), rows[0].evidence)
        self.assertIn("; no automated check]", rows[0].evidence)
        before = (active.read_bytes(), closed.read_bytes())
        self.assertIn("T-10: done", self.assert_ok(self.run_cli(*args)))
        self.assertEqual((active.read_bytes(), closed.read_bytes()), before)

    def test_repeated_source_intake_refuses_conflicts_without_changing_records(self):
        base = ("add", "Preview notes; compare the sample", "--source", "PLAN.md#preview", "--scope", "src/")
        self.assert_ok(self.run_cli(*base))
        active = self.root / ".sako/work/TASKS.md"
        before = active.read_bytes()
        for args in (("add", "Upload notes", *base[2:]), (*base, "--scope", "other/"),
                     (*base, "--after", "T-1"), (*base, "--done-when", "Something else")):
            with self.subTest(args=args):
                code, _, err = self.run_cli(*args)
                self.assertEqual(code, 3, err)
                self.assertIn("already maps to T-10", err)
                self.assertEqual(active.read_bytes(), before)
        duplicate = next(r for r in sako.parse_rows({"tasks": active.read_text()}) if r.source)
        active.write_text(active.read_text() + sako.row_line(duplicate.titles, dict(duplicate.cells, id="T-11")) + "\n")
        before = active.read_bytes()
        self.assertIn("mapped more than once", self.run_cli("check")[1])
        self.assertEqual(self.run_cli(*base)[0], 3)
        self.assertEqual(active.read_bytes(), before)

    def test_intake_dependencies_release_without_manual_record_edits(self):
        self.assert_ok(self.run_cli("add", "Discover local notes", "--source", "PLAN.md#discovery", "--scope", "src/"))
        args = ("add", "Print sorted names", "--source", "PLAN.md#preview", "--scope", "preview.py", "--after", "T-1", "--after", "T-10")
        self.assert_ok(self.run_cli(*args))
        self.start("dependency-session")
        code, _, err = self.run_cli("claim", "T-11", session="dependency-session")
        self.assertEqual(code, 3, err)
        self.assertIn("T-11 waits for T-1, T-10; finish those first", err)
        for task_id in ("T-10", "T-1"):
            self.assert_ok(self.run_cli("claim", task_id, session="dependency-session"))
            self.assert_ok(self.run_cli("close", task_id, "--evidence", "Recorded the requested findings",
                                        session="dependency-session"))
            if task_id == "T-10":
                self.assertEqual(self.run_cli("claim", "T-11", session="dependency-session")[0], 3)
                self.assertIn("T-11  after T-1", self.run_cli("next")[1])
        self.assertIn("next: T-11 (P2", self.run_cli("next")[1])
        self.assertIn("T-11: open", self.assert_ok(self.run_cli(*args)))
        self.assert_ok(self.run_cli("check"))
        fixture.commit_all(self.root, "retain completed prerequisites and the dependent action")
        self.assert_ok(self.run_cli("check", "--gate", session="dependency-session"))
        self.assert_ok(self.run_cli("claim", "T-11", session="dependency-session"))

    def test_invalid_intake_references_leave_the_ledger_unchanged(self):
        active = self.root / ".sako/work/TASKS.md"
        before = active.read_bytes()
        for dependency in ("T-999", "T-10", "not-a-task"):
            with self.subTest(dependency=dependency):
                code, _, err = self.run_cli("add", "Dependent work", "--scope", "src/", "--after", dependency)
                self.assertEqual(code, 3, err)
                self.assertIn("existing task", err)
                self.assertEqual(active.read_bytes(), before)
        for source in ("-", "", "two|cells", "two\nlines"):
            self.assertEqual(self.run_cli("add", "Work", "--scope", "src/", "--source", source)[0], 3)
            self.assertEqual(active.read_bytes(), before)

    def test_interrupted_source_close_preserves_identity_and_original_task(self):
        task = "Investigate accepted note names; record examples"
        intake = ("add", task, "--source", "PLAN.md#investigation", "--scope", "src/")
        self.assert_ok(self.run_cli(*intake))
        self.start("source-session")
        self.assert_ok(self.run_cli("claim", "T-10", session="source-session"))
        args = argparse.Namespace(command="close", session="source-session", task_id="T-10",
                                  evidence="Recorded accepted and rejected examples", resolved=(
                                      sako.resolve_roots(self.root), sako.load_config(self.root / ".sako")))
        original = sako.atomic_write
        def interrupt(path, content):
            if path == self.root.resolve() / ".sako/work/TASKS.md":
                raise OSError("simulated interrupted close")
            original(path, content)
        with patch.object(sako, "atomic_write", side_effect=interrupt), self.assertRaises(OSError):
            sako.cmd_task(args)
        self.assertEqual(self.run_cli(*intake)[0], 3, "intake must not guess across an interrupted close")
        self.assert_ok(self.run_cli("close", "T-10", "--evidence", args.evidence, session="source-session"))
        rows = sako.parse_rows({"done": (self.root / ".sako/work/DONE.md").read_text()})
        self.assertEqual([(r.task, r.source, r.evidence.split(" [sk-")[0]) for r in rows],
                         [(task, "PLAN.md#investigation", args.evidence)])
        self.assertIn("T-10: done", self.assert_ok(self.run_cli(*intake)))

    def test_user_columns_survive_every_write_and_close_names_what_it_drops(self):
        active, done = self.root / ".sako/work/TASKS.md", self.root / ".sako/work/DONE.md"
        fixture.write(self.root, {".sako/work/TASKS.md": "# Tasks\n\n| ID | Area | Pri | Task | Done when | Status | Scope | After | Source | Estimate |\n"
                                  "|---|---|---|---|---|---|---|---|---|---|\n"
                                  "| T-1 | ui | P2 | Theme | Survives reload | open | `src/` | - | - | 2d |\n",
                                  ".sako/work/DONE.md": "# Done\n\n| ID | Task | Done when | Receipt | Scope | Source | Area |\n"
                                  "|---|---|---|---|---|---|---|\n"})
        self.assert_ok(self.run_cli("add", "Export", "--done-when", "Reads back", "--scope", "src/export/"))
        self.assertIn("| T-2 | - | P2 | Export | Reads back | open | `src/export/` | - | - | - |", active.read_text())
        self.start("columns")
        self.assert_ok(self.run_cli("claim", "T-1", session="columns"))
        self.assertRegex(active.read_text(), rf"\| T-1 \| ui \| P2 \| Theme \| Survives reload \| {sako.marker_for('columns')} "
                         rf"since {MINUTE} \| `src/` \| - \| - \| 2d \|")
        output = self.assert_ok(self.run_cli("close", "T-1", "--evidence", "Kept after reload", session="columns"))
        self.assertIn("not carried, the done file has no such column: Estimate=2d", output)
        row = sako.parse_rows({"done": done.read_text()})[0]
        self.assertEqual((row.cells["area"], row.task), ("ui", "Theme"))

    def test_repeat_intake_updates_only_the_priority_and_says_so(self):
        base = ("add", "Preview notes", "--done-when", "Sample matches", "--source", "PLAN.md#p", "--scope", "src/")
        self.assert_ok(self.run_cli(*base))
        self.assertIn("T-10: priority P2 -> P1", self.assert_ok(self.run_cli(*base, "--pri", "P1")))
        self.assertIn("| T-10 | P1 | Preview notes |", (self.root / ".sako/work/TASKS.md").read_text())
        self.assertIn("reused source", self.assert_ok(self.run_cli(*base, "--pri", "p1")))
        self.assertEqual(self.run_cli(*base, "--pri", "urgent")[0], 3)

    def test_receipts_say_what_the_check_said_at_close(self):
        cases = ((None, None, "no automated check"), ([sys.executable, "-c", "pass"], None, "no passing check recorded"),
                 ([sys.executable, "-c", "pass"], "committed", "check pass "),
                 ([sys.executable, "-c", "pass"], "uncommitted", "check pass "))
        for number, (command, verified, expected) in enumerate(cases, 1):
            with self.subTest(expected=expected, verified=verified):
                fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": command, "verify_paths": ["src"]})})
                session = f"receipt-{number}"
                self.start(session)
                self.assert_ok(self.run_cli("add", f"Receipt {number}", "--scope", f"docs/{number}/"))
                task = [r.id for r in sako.parse_rows({"tasks": (self.root / ".sako/work/TASKS.md").read_text()})
                        if r.task == f"Receipt {number}"][0]
                self.assert_ok(self.run_cli("claim", task, session=session))
                claimed = re.search(rf"\| {sako.marker_for(session)} since ({MINUTE}) \|",
                                    (self.root / ".sako/work/TASKS.md").read_text())
                self.assertTrue(claimed, "claim writes its time after the marker")
                since = claimed[1]
                fixture.write(self.root, {f"docs/{number}/notes.md": "outside the checked paths\n"})
                if verified == "uncommitted":
                    fixture.write(self.root, {"src/draft.py": "print(0)\n"})
                if verified:
                    self.assert_ok(self.run_cli("verify"))
                self.assert_ok(self.run_cli("close", task, "--evidence", "Observed", session=session))
                receipt = next(r.evidence for r in sako.parse_rows({"done": (self.root / ".sako/work/DONE.md").read_text()})
                               if r.id == task)
                self.assertRegex(receipt, rf"^Observed \[{sako.marker_for(session)} since {since}; closed {MINUTE}; {expected}")
                if verified:
                    stamp = json.loads((self.root / ".sako/state/stamp.json").read_text())
                    at = fixture.git(self.root, "rev-parse", "HEAD")[:12]
                    at += " plus uncommitted changes" if verified == "uncommitted" else ""
                    self.assertIn(f"check pass {stamp['time']} on {stamp['content_id']} at {at}]", receipt,
                                  "the stamp's own time and commit, never the close's; only checked files count")
        fixture.write(self.root, {"src/app.py": "print(5)\n"})
        fixture.commit_all(self.root, "covered content moves on")
        self.start("receipt-stale")
        self.assert_ok(self.run_cli("add", "Receipt stale", "--scope", "docs/stale/"))
        task = [r.id for r in sako.parse_rows({"tasks": (self.root / ".sako/work/TASKS.md").read_text()})
                if r.task == "Receipt stale"][0]
        self.assert_ok(self.run_cli("claim", task, session="receipt-stale"))
        self.assert_ok(self.run_cli("close", task, "--evidence", "Docs only", session="receipt-stale"))
        self.assertIn("; check stale: last pass ", (self.root / ".sako/work/DONE.md").read_text())

    def test_a_check_before_the_first_commit_names_no_commit(self):
        root = Path(self.workspace.name) / "fresh"
        root.mkdir()
        fixture.git(root, "init", "-q", "-b", "main")
        fixture.write(root, {".git/info/exclude": "/.sako/\n", ".sako/work/TASKS.md": fixture.ledger(fixture.row("T-1")),
                             ".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"],
                                                              "verify_paths": ["src"]}), "src/app.py": "print(1)\n"})
        agent = subprocess.Popen(["sleep", "60"])
        self.agents.append(agent)
        self.assert_ok(fixture.cli(root, "start", session="fresh", pid=agent.pid))
        for args in (("claim", "T-1"), ("verify",), ("close", "T-1", "--evidence", "Prints 1")):
            self.assert_ok(fixture.cli(root, *args, session="fresh"))
        self.assertIn(" at no commit plus uncommitted changes]", (root / ".sako/work/DONE.md").read_text())

    def test_claims_without_a_claim_time_still_close(self):
        marker = sako.marker_for("upgraded")
        claims = {"T-1": marker, "T-2": f"{marker} (took over from sk-0101-bbbbbbbbbbbb: gone)",
                  "T-3": f"{marker} since [owner-ok]"}
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(*(
            fixture.row(task, status="claimed", claim=claim, touches=f"`src/{task}/`") for task, claim in claims.items()))})
        self.start("upgraded")
        for task in claims:
            with self.subTest(claim=claims[task]):
                self.assert_ok(self.run_cli("close", task, "--evidence", "Observed", session="upgraded"))
                done = next(r for r in sako.parse_rows({"done": (self.root / ".sako/work/DONE.md").read_text()})
                            if r.id == task)
                self.assertRegex(done.evidence, rf"^Observed \[{marker}; closed {MINUTE}; no automated check\]$",
                                 "a claim written before claim times, or a hand edit after the marker, leaves the marker alone")
                self.assertEqual(done.claim, marker, "the done row keeps its closer")
        self.assertIn("your rows: T-1 (done); T-2 (done); T-3 (done)", self.assert_ok(self.run_cli("status", session="upgraded")))

    def test_ready_and_the_id_preview_are_gone(self):
        self.assertEqual(self.run_cli("ready")[0], 2)
        self.assertIn("next: T-1", self.assert_ok(self.run_cli("next")))

    def test_every_verb_takes_the_session_the_start_context_prints(self):
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"]})})
        self.start("session-a")
        line = ("--session", "session-a")  # before the task text too, as an agent copies it
        for args in (("add", *line, "Printed line", "--scope", "docs/"), ("next", *line), ("verify", *line),
                     ("claim", "T-10", *line), ("status", *line), ("check", *line),
                     ("close", "T-10", "--evidence", "Recorded", *line), ("end", *line)):
            with self.subTest(verb=args[0]):
                self.assert_ok(self.run_cli(*args))
        self.assertIn("| T-10 | Printed line |", (self.root / ".sako/work/DONE.md").read_text())

    @unittest.skipIf(fixture.WINDOWS, fixture.LEASE)
    def test_parallel_claims_have_one_winner(self):
        for session in ("session-a", "session-b"):
            self.start(session)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda name: self.run_cli("claim", "T-1", session=name),
                                    ("session-a", "session-b")))
        self.assertEqual(sorted(r[0] for r in results), [0, 3])
        self.assertIn("live session", next(r[2] for r in results if r[0] == 3))
        self.assert_ok(self.run_cli("check"))

    def test_overlap_is_refused_and_stale_takeover_is_explicit(self):
        first = self.start("session-a")
        self.start("session-b")
        self.assert_ok(self.run_cli("claim", "T-1", session="session-a"))
        holder = fixture.marker("session-a")
        out = self.assert_ok(self.run_cli("add", "Overlapping work", "--scope", "src/nested/"))
        self.assertEqual(out.strip(), f"T-10: open; recorded locally, not committed; its scope overlaps claimed T-1 ({holder}), "
                                      "so a claim of it by another session is refused until that closes or a scope narrows")
        self.assertEqual(self.assert_ok(self.run_cli("add", "Separate work", "--scope", "docs/")).strip(),
                         "T-11: open; recorded locally, not committed")
        code, _, err = self.run_cli("claim", "T-10", session="session-b")
        self.assertEqual(code, 3)
        self.assertIn(f"T-10's scope overlaps T-1 (src/), claimed by {holder}; wait for it to close, "
                      "or narrow a Scope in .sako/work/TASKS.md", err)
        first.terminate()
        first.wait()
        code, _, err = self.run_cli("claim", "T-1", session="session-b")
        self.assertEqual(code, 3)
        self.assertRegex(err, rf"T-1 is held by {sako.marker_for('session-a')}, (a stale claim;|which SAKO cannot verify from here "
                         r"\(a sandbox, or Windows, hides its process\); if it stopped,) inspect its handoff and use --takeover")
        self.assert_ok(self.run_cli("claim", "T-1", "--takeover", "previous session ended; inspected its work", session="session-b"))
        text = (self.root / ".sako/work/TASKS.md").read_text()
        self.assertIn("took over from", text)
        self.assertIn("previous session ended", text)
        self.assertRegex(text, rf"\| {sako.marker_for('session-b')} since {MINUTE} \(took over from "
                         rf"{sako.marker_for('session-a')}(, unverified)?: previous session ended; inspected its work\) \|",
                         "the taker's own claim time; the note names the previous marker alone")

    def test_a_takeover_reason_on_an_open_task_is_no_crash(self):
        self.start("session-a")
        self.assert_ok(self.run_cli("claim", "T-1", "--takeover", "nobody held it", session="session-a"))

    def test_a_takeover_moves_an_ended_sessions_overlapping_claims_one_after_another(self):
        ended, gone, lost = (fixture.marker(name) for name in ("ended-session", "gone-session", "lost-session"))
        active = self.root / ".sako/work/TASKS.md"
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            fixture.row("T-1", status="claimed", claim=ended),
            fixture.row("T-2", status="claimed", claim=ended, touches="`src/app.py`"),
            fixture.row("T-3", status="claimed", claim=ended, touches="`src/`, `tests/`"),
            fixture.row("T-4", status="claimed", claim=gone, touches="`notes/`"),
            fixture.row("T-5", status="claimed", claim=lost, touches="`notes/plan.md`"),
            fixture.row("T-6", touches="`notes/plan.md`")),
            ".sako/work/DONE.md": fixture.done_file(fixture.row("T-7", status="done", claim=ended, touches="`src/`"))})
        self.start("session-b")
        self.start("session-c")
        taker, reason = fixture.marker("session-b"), ("--takeover", "its session ended; inspected its work")
        out = self.assert_ok(self.run_cli("claim", "T-1", *reason, session="session-b"))
        self.assertIn("Its previous holder also holds overlapping T-2, T-3; take them over too before you stop.", out)
        code, _, err = self.run_cli("claim", "T-2", *reason, session="session-c")
        self.assertEqual(code, 3, "a third session is refused against a claim the taker now holds")
        self.assertIn(f"T-2's scope overlaps T-1 (src/), claimed by {taker}; wait for it to close, "
                      "or narrow a Scope in .sako/work/TASKS.md", err)
        out = self.assert_ok(self.run_cli("claim", "T-2", *reason, session="session-b"))
        self.assertIn("Its previous holder also holds overlapping T-3; take it over too before you stop.", out)
        self.assertNotIn("also holds", self.assert_ok(self.run_cli("claim", "T-3", *reason, session="session-b")),
                         "a row the ended session already closed is not named")
        rows = {r.id: r for r in sako.parse_rows({"tasks": active.read_text()})}
        for task_id in ("T-1", "T-2", "T-3"):
            self.assertRegex(rows[task_id].claim, rf"^{taker} since {MINUTE} \(took over from {ended}: ")
        before = active.read_bytes()
        code, _, err = self.run_cli("claim", "T-6", session="session-b")
        self.assertEqual(code, 3)
        self.assertIn(f"T-6's scope overlaps T-4 (notes/), claimed by {gone}; if it stopped, inspect its handoff and take it "
                      "over first (claim T-4 --takeover REASON), or narrow a Scope in .sako/work/TASKS.md", err)
        code, _, err = self.run_cli("claim", "T-4", *reason, session="session-b")
        self.assertEqual(code, 3, "overlapping claims of two different ended sessions still refuse each other")
        self.assertIn(f"T-4's scope overlaps T-5 (notes/plan.md), claimed by {lost}; neither claim can be taken "
                      "over while they overlap, so narrow a Scope in .sako/work/TASKS.md", err)
        self.assertEqual(active.read_bytes(), before)
        out = self.assert_ok(self.run_cli("add", "Shared follow-up", "--scope", "src/lib/", "--scope", "notes/"))
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertIn(f"its scope overlaps claimed T-1 ({taker}), T-3 ({taker}), T-4 ({gone}), T-5 ({lost}), so a claim "
                      "of it by another session is refused until those close or a scope narrows", out)

    def test_a_parked_task_names_how_to_reopen_it_from_any_checkout(self):
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(fixture.row("T-1", status="parked: the owner decides"))})
        self.start("session-a")
        code, _, err = self.run_cli("claim", "T-1", session="session-a")
        self.assertEqual(code, 3)
        self.assertIn("T-1 is parked; once its reason is resolved, set its Status to open in .sako/work/TASKS.md", err)
        linked = Path(self.workspace.name) / "linked"
        fixture.git(self.root, "worktree", "add", "-q", str(linked))
        code, _, err = fixture.cli(linked, "claim", "T-1", session="session-a")
        self.assertEqual(code, 3, "a linked worktree has no records folder of its own, so the path is absolute")
        ledger = (self.root.resolve() / ".sako/work/TASKS.md").as_posix()
        self.assertIn(f"set its Status to open in {ledger}", err)
        self.assertIn(f"T-99 needs exactly one row in {ledger}", fixture.cli(linked, "claim", "T-99", session="session-a")[2])

    def test_handoff_folders_are_found_by_id_listed_and_never_checked(self):
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"], "verify_paths": []})})
        self.start("session-a")
        out = self.assert_ok(self.run_cli("claim", "T-1", session="session-a"))
        self.assertIn("keep them in .sako/work/T-1-invented-task/ (handoff.md for where it stands)", out)
        self.assert_ok(self.run_cli("verify"))
        self.assert_ok(self.run_cli("add", "Other work", "--scope", "docs/"))
        fixture.write(self.root, {".sako/work/T-1-invented-task/handoff.md": "Where it stands: started\n",
                                  ".sako/work/T-1-a-note.md": "not a folder\n",
                                  ".sako/work/T-10-renamed-by-hand/notes.md": "found by its ID\n"})
        out = self.assert_ok(self.run_cli("check", "--session", "session-a"))
        self.assertIn("evidence: fresh", out)
        self.assertIn("changed this session: 0", out)
        listed = "handoffs: T-1 .sako/work/T-1-invented-task/, T-10 .sako/work/T-10-renamed-by-hand/"
        self.assertIn(listed, self.assert_ok(self.run_cli("status", "--session", "session-a")))
        agent = subprocess.Popen(["sleep", "60"])
        self.agents.append(agent)
        self.assertIn(listed, self.assert_ok(fixture.cli(self.root, "start", session="session-b", pid=agent.pid)))

    def test_close_needs_fresh_check_and_commit(self):
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "from pathlib import Path; assert Path('src/app.py').read_text() == 'print(2)\\n'"], "verify_paths": ["src"]})})
        fixture.commit_all(self.root, "configure real assertion")
        self.start("session-a")
        self.assert_ok(self.run_cli("claim", "T-1", session="session-a"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        args = ("close", "T-1", "--evidence", "The application prints the updated value; check verifies the source")
        code, _, err = self.run_cli(*args, session="session-a")
        self.assertEqual(code, 3)
        self.assertIn("evidence is none", err)
        self.assert_ok(self.run_cli("verify"))
        self.assert_ok(self.run_cli(*args, session="session-a"))
        code, _, err = self.run_cli("check", "--gate", session="session-a")
        self.assertEqual(code, 2)
        self.assertIn("Committed: T-1 is closed but its changes are not committed", err)
        fixture.commit_all(self.root, "land verified task")
        self.assert_ok(self.run_cli("check", "--gate", session="session-a"))

    def test_interrupted_close_can_finish_without_losing_work(self):
        self.start("session-a")
        self.assert_ok(self.run_cli("claim", "T-1", session="session-a"))
        config = sako.load_config(self.root / ".sako")
        roots = sako.resolve_roots(self.root)
        args = argparse.Namespace(command="close", session="session-a", task_id="T-1",
                                  evidence="Verified the existing behavior", resolved=(roots, config))
        original = sako.atomic_write
        def interrupt(path, content):
            if path == self.root / ".sako/work/TASKS.md":
                raise OSError("simulated interruption after durable closed record")
            original(path, content)
        with patch.object(sako, "atomic_write", side_effect=interrupt), self.assertRaises(OSError):
            sako.cmd_task(args)
        self.assertIn("T-1", (self.root / ".sako/work/TASKS.md").read_text())
        self.assertIn("T-1", (self.root / ".sako/work/DONE.md").read_text())
        self.assert_ok(self.run_cli("close", "T-1", "--evidence", args.evidence, session="session-a"))
        self.assertNotIn("| T-1 |", (self.root / ".sako/work/TASKS.md").read_text())
        self.assertEqual((self.root / ".sako/work/DONE.md").read_text().count("| T-1 |"), 1)

    @unittest.skipIf(fixture.WINDOWS, "Windows keeps no executable bit")
    def test_content_evidence_includes_executable_mode(self):
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"], "verify_paths": ["src"]})})
        self.assert_ok(self.run_cli("verify"))
        os.chmod(self.root / "src/app.py", 0o755)
        self.assertIn("evidence: stale", self.run_cli("check")[1])

    def test_a_running_verify_keeps_the_last_pass_until_the_run_fails_or_moves(self):
        seen, stamp = Path(self.workspace.name) / "seen.txt", self.root / ".sako/state/stamp.json"
        fixture.write(self.root, {"control": "probe\n", "gen/out.txt": "generated\n", "check.py": (  # outside the checked paths
            "import pathlib, shutil, subprocess, sys\nmode = pathlib.Path('control').read_text().strip()\n"
            "if mode == 'probe':  # a peer reads the evidence while the check runs\n"
            f"    pathlib.Path({str(seen)!r}).write_text(subprocess.run([sys.executable, {str(fixture.SAKO)!r}, 'status'], "
            "capture_output=True, text=True).stdout)\n"
            "if mode == 'move':\n    pathlib.Path('src/made.txt').write_text('written during the run')\n"
            "if mode == 'vanish':  # a failing check that also takes a checked folder away\n"
            "    shutil.rmtree('gen')\n    print('the generator broke', flush=True)\n"
            "sys.exit(mode in ('fail', 'vanish'))\n"),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src", "gen"]})})
        self.assert_ok(self.run_cli("verify"))
        self.assertIn("evidence: none", seen.read_text())
        self.assert_ok(self.run_cli("verify"))
        self.assertIn("evidence: fresh", seen.read_text(), "a second run on unchanged content keeps the last pass")
        for mode, why in (("fail", "a failing run deletes the last pass"), ("move", "so does a run whose content moved"),
                          ("vanish", "and a failing run whose checked folder is gone afterwards")):
            with self.subTest(mode=mode):
                fixture.write(self.root, {"control": "probe\n", "gen/out.txt": "generated\n"})
                self.assert_ok(self.run_cli("verify"))
                self.assertTrue(stamp.exists())
                fixture.write(self.root, {"control": f"{mode}\n"})
                code, out, err = self.run_cli("verify")
                self.assertEqual(code, 1, out + err)
                self.assertFalse(stamp.exists(), why)
                (self.root / "src/made.txt").unlink(missing_ok=True)
        self.assertIn("the generator broke\ncheck: FAILED (exit 1)", out, "the folder it took away hides neither output nor failure")
        self.assertEqual((self.root / ".sako/state/verify-failed.log").read_text(), "the generator broke\n")

    def test_verify_keeps_its_output_and_a_pass_leaves_the_failed_output_in_place(self):
        fixture.write(self.root, {"control": "fail\n", "check.py": (
            "import pathlib, sys\nmode = pathlib.Path('control').read_text().strip()\nprint(f'checked in mode {mode}', flush=True)\n"
            "sys.exit('the parser rejects an empty name' if mode == 'fail' else 0)\n"),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src"]})})
        log, failed = (self.root.resolve() / ".sako/state" / name for name in ("verify.log", "verify-failed.log"))
        code, out, _ = self.run_cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("checked in mode fail\nthe parser rejects an empty name\ncheck: FAILED (exit 1)", out,
                      "you still see the check's output, then the result")
        self.assertIn(f"no stamp written; output in {failed.as_posix()}", out)
        self.assertEqual(failed.read_text(), "checked in mode fail\nthe parser rejects an empty name\n", "standard output and error")
        fixture.write(self.root, {"control": "pass\n"})
        out = self.assert_ok(self.run_cli("verify"))
        self.assertTrue(out.startswith("checked in mode pass\ncheck: PASS"), out)
        self.assertIn(f"; output in {log.as_posix()}", out)
        self.assertEqual(log.read_text(), "checked in mode pass\n")
        self.assertIn("the parser rejects an empty name", failed.read_text(), "a later pass leaves the failed run's output")

    def test_a_process_the_check_leaves_running_neither_holds_verify_nor_keeps_it_from_failing(self):
        release, done = (Path(self.workspace.name) / name for name in ("release", "done"))
        failed = self.root.resolve() / ".sako/state/verify-failed.log"
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src"]})})
        for chatty in (False, True):  # either way the server holds the check's output open until released
            server = (f"import os, pathlib, time\nend = time.time() + 20\n"
                      f"while not pathlib.Path({str(release)!r}).exists() and time.time() < end:\n"
                      + ("    print('a server line', flush=True)\n" if chatty else "") + "    time.sleep(0.02)\n"
                      f"os.close(1)\nos.close(2)\npathlib.Path({str(done)!r}).write_text('done')\n")
            fixture.write(self.root, {"check.py": (
                "import subprocess, sys, tempfile\nprint('the parser rejects an empty name', flush=True)\n"
                f"subprocess.Popen([sys.executable, '-c', {server!r}], stdout=sys.stdout, stderr=sys.stderr, cwd=tempfile.gettempdir())\n"
                "sys.exit(1)\n")})
            with self.subTest(chatty=chatty):
                code, out, err = self.run_cli("verify")
                running = not done.exists()
                release.write_text("released")
                for _ in range(1000):  # the server ends before the next run and before the workspace goes
                    if done.exists() and done.read_text() == "done":
                        break
                    time.sleep(0.02)
                release.unlink()
                done.unlink(missing_ok=True)
                self.assertEqual(code, 1, out + err)
                self.assertIn("check: FAILED (exit 1)", out)
                self.assertIn(f"no stamp written; output in {failed.as_posix()}", out)
                self.assertTrue(failed.read_text().startswith("the parser rejects an empty name\n"),
                                "the failed run's output is kept while the server still holds verify.log")
                self.assertTrue(running, "verify returns when the check ends, while the server it started still runs")

    @unittest.skipIf(fixture.WINDOWS, "an interrupt reaches a single process only on POSIX")
    def test_an_interrupted_verify_stops_its_check(self):
        started, release, finished = (Path(self.workspace.name) / name for name in ("started", "release", "finished"))
        fixture.write(self.root, {"check.py": (
            f"import pathlib, time\npathlib.Path({str(started)!r}).write_text('started')\nend = time.time() + 20\n"
            f"while not pathlib.Path({str(release)!r}).exists() and time.time() < end:\n    time.sleep(0.02)\n"
            f"pathlib.Path({str(finished)!r}).write_text('finished')\n"),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src"]})})
        run = subprocess.Popen([sys.executable, str(fixture.SAKO), "verify"], cwd=self.root, env=fixture.ENV,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(1000):
            if started.exists() or run.poll() is not None:
                break
            time.sleep(0.02)
        run.send_signal(signal.SIGINT)
        out, err = run.communicate(timeout=30)
        release.write_text("released")
        time.sleep(0.5)  # a check still running would finish within a few hundredths of a second
        self.assertEqual(run.returncode, 130, out + err)
        self.assertFalse(finished.exists(), "an interrupted verify stops its check; none runs on without it")

    def test_verify_shows_the_check_output_on_any_text_stream(self):
        config = dict(sako.DEFAULTS, verify_command=[sys.executable, "check.py"], verify_paths=["src"])
        fixture.write(self.root, {"check.py": "import sys\nsys.stdout.buffer.write('naïve café ✓\\n'.encode())\n",
                                  ".sako/config.json": json.dumps(config)})
        with patch.object(sys, "stdout", io.StringIO()) as shown:  # as under unittest -b: no byte buffer behind it
            self.assertEqual(sako.verify(sako.resolve_roots(self.root), config), 0)
        self.assertTrue(shown.getvalue().startswith("naïve café ✓\ncheck: PASS"), shown.getvalue())

    def test_every_verification_path_must_cover_something(self):
        (self.root / "empty").mkdir()
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"], "verify_paths": ["src", "empty"]})})
        code, _, err = self.run_cli("verify")
        self.assertEqual(code, 3)
        self.assertIn("empty", err)
        self.assertFalse((self.root / ".sako/state/stamp.json").exists())

    def test_unsupported_platform_is_explicit(self):
        with patch.object(sako.sys, "platform", "freebsd14"), \
                self.assertRaisesRegex(sako.SakoError, "Linux, WSL, macOS and Windows"):
            sako.resolve_roots(self.root)
        for platform in ("darwin", "win32"):
            with patch.object(sako.sys, "platform", platform):
                self.assertEqual(sako.resolve_roots(self.root).work, self.root.resolve(), platform)

    def test_a_replace_that_windows_holds_for_a_moment_is_retried(self):
        target, real, calls = self.root / "notes.md", os.replace, []

        def held_twice(source, destination):
            calls.append(destination)
            if len(calls) < 3:
                raise PermissionError(13, "Access is denied")  # a scanner or a reader holds the file
            real(source, destination)

        with patch.object(sako.os, "replace", side_effect=held_twice), patch.object(sako.time, "sleep"):
            sako.atomic_write(target, "kept\n")
        self.assertEqual((target.read_text(), len(calls)), ("kept\n", 3))
        with patch.object(sako.os, "replace", side_effect=PermissionError(13, "Access is denied")), \
                patch.object(sako.time, "sleep"), self.assertRaises(PermissionError):
            sako.atomic_write(target, "lost\n")
        self.assertEqual(target.read_text(), "kept\n")
        self.assertEqual(list(self.root.glob(".sako-*")), [], "no temporary file is left behind")

    def test_a_read_that_windows_refuses_for_a_moment_is_retried(self):
        (self.root / "state.json").write_text('{"kept": 1}')
        real, calls = Path.read_text, []

        def refused_twice(path, *args, **kwargs):
            calls.append(path)
            if len(calls) < 3:
                raise PermissionError(13, "Access is denied")  # a replace in flight on Windows
            return real(path, *args, **kwargs)

        with patch.object(Path, "read_text", refused_twice), patch.object(sako.time, "sleep"):
            self.assertEqual(sako.read_json(self.root / "state.json"), {"kept": 1}, "not read as empty, so never swept")

    def test_on_windows_reads_take_no_lock_and_writes_take_a_byte_lock(self):
        roots, calls = sako.resolve_roots(self.root), []

        def locking(fd, mode, size):  # how Windows refuses a lock another process holds
            calls.append((mode, size))
            if len(calls) == 1:
                raise PermissionError(13, "Permission denied")

        stand_in = types.SimpleNamespace(LK_NBLCK=2, locking=locking)
        shutil.rmtree(roots.state, ignore_errors=True)
        with patch.object(sako.sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": stand_in}):
            with sako.ledger_lock(roots):
                self.assertFalse(roots.state.exists(), "a read takes no lock, since Windows locks are exclusive")
            with sako.ledger_lock(roots, write=True):
                self.assertEqual(calls, [(2, 1), (2, 1)], "a held lock is retried")

    def test_output_is_utf8_whatever_the_locale(self):
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Show a → b", "--pri", "P1", "--scope", "src/"))
        env = dict(fixture.ENV, LC_ALL="C", PYTHONCOERCECLOCALE="0", PYTHONUTF8="0")  # Windows pipes are cp1252 alike
        result = subprocess.run([sys.executable, str(fixture.SAKO), "next"], cwd=self.root, env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Show a → b".encode(), result.stdout)

    def test_renamed_dirty_file_keeps_the_original_scope_visible(self):
        (self.root / "other").mkdir()
        fixture.git(self.root, "mv", "src/app.py", "other/app.py")
        self.assertEqual(set(sako.dirty_paths(self.root)), {"src/app.py", "other/app.py"})

    @unittest.skipUnless(fixture.symlinks(), "creating symlinks needs a privilege here")
    def test_record_paths_stay_inside_the_kit_folder_apart_from_its_files(self):
        (self.root / ".sako/git-link").symlink_to(self.root / ".git", target_is_directory=True)
        (self.root / ".sako/out-link").symlink_to(self.root / "src", target_is_directory=True)
        for path in ("git-link/config", "out-link/app.py", "../AGENTS.md", "sako.py", "config.json",
                     "install.json", "state/TASKS.md", "work", "."):
            with self.subTest(path=path):
                fixture.write(self.root, {".sako/config.json": json.dumps({"tasks": path})})
                result = self.run_cli("check")
                self.assertEqual(result[0], 3, result)

    def test_invalid_manifest_and_settings_refuse_under_optimized_python(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "none"))
        path = self.root / ".sako/install.json"
        path.write_text(json.dumps(dict(json.loads(path.read_text()), clients="codex")))
        result = subprocess.run([sys.executable, "-O", str(fixture.SAKO), "init"], cwd=self.root,
                                text=True, capture_output=True, env=fixture.ENV)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("invalid", result.stderr)
        for name in ("install.json", "sako.py", "SAKO.md"):
            (self.root / ".sako" / name).unlink()
        fixture.write(self.root, {".claude/settings.local.json": '{"hooks": {"Stop": false}}'})
        result = subprocess.run([sys.executable, "-O", str(fixture.SAKO), "init", "--client", "claude"],
                                cwd=self.root, text=True, capture_output=True, env=fixture.ENV)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse((self.root / ".sako/sako.py").exists())

    def test_failed_git_status_is_not_clean_work(self):
        error = subprocess.CompletedProcess([], 1, b"", b"simulated Git failure")
        with patch.object(sako.subprocess, "run", return_value=error), self.assertRaisesRegex(sako.SakoError, "cannot inspect"):
            sako.dirty_paths(self.root)

    def test_adding_work_ignores_lane_headings_inside_examples(self):
        example = "# Work\n\n```markdown\n## Next\n\n| ID | Task | Status | Claim | Touches |\n|---|---|---|---|---|\n| T-7 | Example | open | - | `src/` |\n```\n"
        fixture.write(self.root, {".sako/work/TASKS.md": example})
        self.assert_ok(self.run_cli("add", "Actual project work", "--scope", "src/"))
        rows = sako.parse_rows({"tasks": (self.root / ".sako/work/TASKS.md").read_text()})
        self.assertEqual([(r.id, r.task) for r in rows], [("T-8", "Actual project work")])
        self.assertTrue((self.root / ".sako/work/TASKS.md").read_text().startswith(example))
        fixture.write(self.root, {".sako/work/TASKS.md": example.rstrip()[:-3]})
        result = self.run_cli("add", "Must remain visible", "--scope", "src/")
        self.assertEqual(result[0], 3, result)
        self.assertIn("unclosed fenced example", result[2])

    @unittest.skipUnless(fixture.symlinks(), "creating symlinks needs a privilege here")
    def test_unselected_external_skill_directory_does_not_block_core(self):
        shared = Path(self.workspace.name) / "shared-skills"
        shared.mkdir()
        (self.root / ".agents").mkdir()
        (self.root / ".agents/skills").symlink_to(shared, target_is_directory=True)
        self.assert_ok(self.run_cli("check"))
        for selection in (("--client", "none"), ("--client", "codex")):
            printout = fixture.succeeds(fixture.seed(self.root, *selection))
            self.assertIn(".agents/skills/sako/SKILL.md leads outside this repository, so SAKO left it alone.",
                          printout)
        self.assertEqual(list(shared.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
