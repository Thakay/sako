"""Observable guarantees added after the first adoption and concurrency review."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

import sako
import selftest as fixture


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
        self.assertEqual(self.run_cli("claim", "T-11", session="dependency-session")[0], 3)
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
        self.assertIn(f"| T-1 | ui | P2 | Theme | Survives reload | {sako.marker_for('columns')} | `src/` | - | - | 2d |",
                      active.read_text())
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
        cases = ((None, None, "no automated check"), ([sys.executable, "-c", "pass"], False, "no passing check recorded"),
                 ([sys.executable, "-c", "pass"], True, "check pass "))
        for number, (command, verified, expected) in enumerate(cases, 1):
            with self.subTest(expected=expected):
                fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": command, "verify_paths": ["src"]})})
                session = f"receipt-{number}"
                self.start(session)
                self.assert_ok(self.run_cli("add", f"Receipt {number}", "--scope", f"docs/{number}/"))
                task = [r.id for r in sako.parse_rows({"tasks": (self.root / ".sako/work/TASKS.md").read_text()})
                        if r.task == f"Receipt {number}"][0]
                self.assert_ok(self.run_cli("claim", task, session=session))
                if verified:
                    self.assert_ok(self.run_cli("verify"))
                self.assert_ok(self.run_cli("close", task, "--evidence", "Observed", session=session))
                receipt = next(r.evidence for r in sako.parse_rows({"done": (self.root / ".sako/work/DONE.md").read_text()})
                               if r.id == task)
                self.assertRegex(receipt, rf"^Observed \[{sako.marker_for(session)}; closed \d{{4}}-\d\d-\d\d; {expected}")
                if verified:
                    stamp = json.loads((self.root / ".sako/state/stamp.json").read_text())
                    self.assertIn(f"check pass {stamp['time']} on {stamp['content_id']}]", receipt,
                                  "the stamp's own time, never the close time")
        fixture.write(self.root, {"src/app.py": "print(5)\n"})
        fixture.commit_all(self.root, "covered content moves on")
        self.start("receipt-stale")
        self.assert_ok(self.run_cli("add", "Receipt stale", "--scope", "docs/stale/"))
        task = [r.id for r in sako.parse_rows({"tasks": (self.root / ".sako/work/TASKS.md").read_text()})
                if r.task == "Receipt stale"][0]
        self.assert_ok(self.run_cli("claim", task, session="receipt-stale"))
        self.assert_ok(self.run_cli("close", task, "--evidence", "Docs only", session="receipt-stale"))
        self.assertIn("; check stale: last pass ", (self.root / ".sako/work/DONE.md").read_text())

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
        self.assert_ok(self.run_cli("add", "Overlapping work", "--scope", "src/nested/"))
        code, _, err = self.run_cli("claim", "T-10", session="session-b")
        self.assertEqual(code, 3)
        self.assertIn("overlap", err)
        first.terminate()
        first.wait()
        code, _, err = self.run_cli("claim", "T-1", session="session-b")
        self.assertEqual(code, 3)
        self.assertIn("--takeover", err)
        self.assert_ok(self.run_cli("claim", "T-1", "--takeover", "previous session ended; inspected its work", session="session-b"))
        text = (self.root / ".sako/work/TASKS.md").read_text()
        self.assertIn("took over from", text)
        self.assertIn("previous session ended", text)

    def test_a_takeover_reason_on_an_open_task_is_no_crash(self):
        self.start("session-a")
        self.assert_ok(self.run_cli("claim", "T-1", "--takeover", "nobody held it", session="session-a"))

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
