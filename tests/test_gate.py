"""The finish gate: Recorded, Proven and Committed, each finding with its rule and its fix."""

from pathlib import Path
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sako
import selftest as fixture


class Gate(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="sako-gate-")
        self.base = Path(self.workspace.name)
        self.root = fixture.make_repo(self.base / "project", {
            ".sako/work/TASKS.md": fixture.ledger(fixture.row("T-1", touches="`src/`"), fixture.row("T-2", touches="`docs/`")),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"], "verify_paths": ["src"]})})
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait()
        self.workspace.cleanup()

    def host(self):
        process = subprocess.Popen(["sleep", "60"])
        self.processes.append(process)
        return process.pid

    def run_in(self, where, *args, session=None, pid=None):
        return fixture.cli(where, *args, session=session, pid=pid)

    def stop(self, session, where=None, active=False):
        payload = {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": active}
        return fixture.cli(where or self.root, "hook", stdin=json.dumps(payload))

    def start(self, session, where=None):
        pid = self.host()
        code, out, err = self.run_in(where or self.root, "start", session=session, pid=pid)
        self.assertEqual(code, 0, out + err)
        return pid

    def backdate(self, task, minutes=5, east=None):
        """Date a receipt's close some minutes back, so the edits after it come in a later minute, as in real use; east, in
        hours from UTC, writes it in that zone instead of the local one."""
        done, then = self.root / ".sako/work/DONE.md", time.time() - 60 * minutes
        earlier = time.strftime("%Y-%m-%dT%H:%M%z", time.localtime(then)) if east is None else \
            time.strftime("%Y-%m-%dT%H:%M", time.gmtime(then + 3600 * east)) + f"{east:+03d}00"
        text, count = re.subn(rf"(?m)^(\| {task} \|.*; closed )[^;]+;", rf"\g<1>{earlier};", done.read_text())
        self.assertEqual(count, 1, text)
        done.write_text(text)

    def test_unrecorded_work_blocks_once_and_a_claim_clears_it(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/app.py", err)
        self.assertIn("claim T-1 --session s1", err)
        self.assertEqual(self.stop("s1", active=True)[0], 0, "a repeated stop callback fails open")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        code, out, err = self.stop("s1")
        self.assertEqual((code, err), (0, ""), "claimed work is recorded work; pausing is free")

    def test_the_recorded_fix_offers_a_covering_task_as_a_question_and_always_the_add(self):
        self.start("s1")
        fixture.write(self.root, {"notes.txt": "unrelated\n"})
        add = f'{sako.PYTHON} .sako/sako.py add "WHAT CHANGED" --done-when "HOW TO CHECK IT" --scope notes.txt, then claim it'
        err = self.stop("s1")[2]
        self.assertIn(f"fix: {add}; for a change", err)
        self.assertFalse(set("<>&") & set(err), "Codex escapes <, > and & in the Stop feedback it hands the agent")
        (self.root / "notes.txt").unlink()
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        self.assertIn(f"fix: if this change is T-1 (Invented task), claim it: {sako.PYTHON} .sako/sako.py claim T-1 --session s1; "
                      f"otherwise record it: {sako.PYTHON} .sako/sako.py add", self.stop("s1")[2])

    def test_the_recorded_fix_says_to_delete_or_ignore_generated_files(self):
        self.start("s1")
        fixture.write(self.root, {"build/app.o": "compiled\n"})
        err = self.stop("s1")[2]
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: "
                      "build/app.o", err)
        self.assertIn("New files among them (build/app.o): delete or ignore any that is generated", err)

    def test_a_session_that_paused_and_ended_is_not_stale_to_itself(self):
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {".sako/work/T-1-invented-task/handoff.md": "Where it stands: started\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s1"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("not a live session", out + err)
        self.assertIn("ledger: consistent", self.run_in(self.root, "status", session="s1")[1])
        self.assertIn("T-1 is claimed by", self.run_in(self.root, "check", session="s2")[1], "to others it is stale")

    def test_a_resume_after_session_end_keeps_the_claim_and_closes_under_its_marker(self):
        self.start("s1")
        today = sako.marker_for("s1")
        earlier = sako.marker_for("s1", "0101" if today.split("-")[1] != "0101" else "0102")
        presence = sako.presence_dir(sako.resolve_roots(self.root)) / f"{sako.session_key('s1')}.json"
        presence.write_text(json.dumps(dict(json.loads(presence.read_text()), marker=earlier)))  # first started that day
        self.assertEqual(self.run_in(self.root, "claim", "T-1", session="s1")[0], 0)
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        self.assertEqual(fixture.hook(self.root, "SessionEnd", "s1")[0], 0)
        out = fixture.hook(self.root, "SessionStart", "s1", pid=self.host())[1]
        self.assertIn(f"your marker: {today}", out, "the resume starts a new start record under a new marker day")
        out = self.run_in(self.root, "status", session="s1")[1]
        self.assertIn("your rows: T-1 (claimed, no handoff) touches src/\n", out, "the claim outlives the session")
        self.assertIn(f"changed this session in {self.root.resolve().as_posix()}: 0", out,
                      "the start window begins again at the resume")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        self.assertEqual(self.run_in(self.root, "verify")[0], 0)
        self.assertEqual(self.run_in(self.root, "close", "T-1", "--evidence", "Prints 3", session="s1")[0], 0)
        receipt = sako.parse_rows({"done": (self.root / ".sako/work/DONE.md").read_text()})[0].evidence
        self.assertRegex(receipt, rf"^Prints 3 \[{earlier} since [^;]+; closed ", "one holder name for the whole task")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Committed: T-1 is closed but its changes are not committed: src/app.py", err)
        self.assertNotIn("Recorded:", err, "the gate counts the row closed under the earlier marker as this session's")
        fixture.commit_all(self.root, "T-1: prints 3")
        self.assertEqual(self.stop("s1")[0], 0)

    def test_a_resume_after_session_end_gets_its_marker_and_claims_back(self):
        self.start("s1")
        marker = sako.marker_for("s1")
        self.assertEqual(self.run_in(self.root, "claim", "T-1", session="s1")[0], 0)
        self.assertEqual(fixture.hook(self.root, "SessionEnd", "s1")[0], 0)
        self.assertIn(f"T-1 is claimed by {marker}, which is not a live session", self.run_in(self.root, "check")[1],
                      "while the session is gone, its claim reads as stale to everyone")
        self.assertIn(f"your marker: {marker}\n", fixture.hook(self.root, "SessionStart", "s1", pid=self.host())[1])
        self.assertIn("your rows: T-1 (claimed, no handoff) touches src/\n", self.run_in(self.root, "status", session="s1")[1])
        self.assertIn("ledger: consistent", self.run_in(self.root, "check")[1], "the same ID brings its claim back to life")

    def test_a_session_that_changed_nothing_passes(self):
        fixture.write(self.root, {"src/wip.py": "draft\n", ".sako/work/TASKS.md": fixture.ledger(
            fixture.row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa"), fixture.row("T-2", touches="`docs/`"))})
        stale = ("T-1 is claimed by sk-0101-aaaaaaaaaaaa, which is not a live session; resume it, take it over (claim T-1 "
                 "--takeover REASON), or park it (set its Status to parked with the reason)")
        code, out, err = self.run_in(self.root, "start", session="s1", pid=self.host())
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"  {stale}", out, "start lists another session's stale claim")
        self.assertEqual(self.stop("s1"), (0, "", ""), "another session's stale claim is not this session's to fix")
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1"),
                         (0, f"SAKO gate: clear in {self.root.resolve().as_posix()}\nnote: {stale}\n", ""))
        for view in ("check", "status"):
            self.assertIn(f"  {stale}", self.run_in(self.root, view, session="s1")[1], f"{view} still lists it")
        self.assertIn("T-1  claimed by sk-0101-aaaaaaaaaaaa (not live)", self.run_in(self.root, "next")[1])

    def test_a_bystander_parks_a_stale_row_by_hand_and_no_finding_names_it_after(self):
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            fixture.row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa"), fixture.row("T-2", touches="`docs/`"))})
        self.start("s1")
        self.assertIn("note: T-1 is claimed by sk-0101-aaaaaaaaaaaa, which is not a live session; resume it, take it over "
                      "(claim T-1 --takeover REASON), or park it (set its Status to parked with the reason)",
                      self.run_in(self.root, "check", "--gate", session="s1")[1])
        tasks = self.root / ".sako/work/TASKS.md"  # its handoff says it waits on the owner
        tasks.write_text(tasks.read_text().replace("| sk-0101-aaaaaaaaaaaa |", "| parked: owner: approves the copy, 2026-01-02 |"))
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1"),
                         (0, f"SAKO gate: clear in {self.root.resolve().as_posix()}\n", ""), "an edit of the records is no change")
        self.assertEqual(self.stop("s1"), (0, "", ""))
        views = {"start": self.run_in(self.root, "start", session="s2", pid=self.host())[1],
                 **{view: self.run_in(self.root, view, session="s1")[1] for view in ("check", "status")}}
        for view, out in views.items():
            with self.subTest(view=view):
                self.assertIn("ledger: consistent", out)
                self.assertNotIn("T-1 is claimed", out)
        self.assertIn("T-1  parked: owner: approves the copy, 2026-01-02", self.run_in(self.root, "next")[1])

    def test_findings_about_this_sessions_claims_or_the_records_block_and_the_rest_are_notes(self):
        row, mine, other = fixture.row, fixture.marker("s1"), "sk-0101-bbbbbbbbbbbb"
        self.start("s1")
        peers = (row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa"),
                 row("T-2", status="claimed", claim=other, touches="`src/app.py`"))
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            *peers, row("T-3", status="claimed", claim=mine, touches="`docs/`"))})
        self.assertEqual(self.stop("s1"), (0, "", ""), "other sessions' stale claims and overlaps never block")
        code, out, _ = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out)
        self.assertIn("note: T-1 and T-2 are both claimed and their scopes overlap; finish one first or narrow the scopes", out)
        self.assertIn(f"note: T-2 is claimed by {other}, which is not a live session; resume it, take it over (claim T-2 "
                      "--takeover REASON), or park it (set its Status to parked with the reason)", out)
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            *peers, row("T-3", status="claimed", claim=mine, touches="`src/app.py`"), row("T-4"), row("T-4", touches="`etc/`"))})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        for blocking in ("Ledger: T-1 and T-3 are both claimed and their scopes overlap",
                         "Ledger: T-2 and T-3 are both claimed and their scopes overlap", "Ledger: T-4 is defined by 2 rows"):
            self.assertIn(blocking, err)
        self.assertIn("SAKO gate: 3 problem(s) before you stop:", err)
        self.assertGreater(err.index("note: T-1 and T-2 are both claimed"), err.index("Ledger: T-4"), "notes follow the problems")
        self.assertNotIn("Ledger: T-1 is claimed by", err)

    def test_a_row_fault_or_a_source_mapped_twice_blocks_even_on_rows_the_session_does_not_hold(self):
        row, other = fixture.row, "sk-0101-bbbbbbbbbbbb"
        self.start("s1")
        for name, rows, blocking in (
                ("fault on an open row", (row("T-1", pri="P9"),), 'Ledger: T-1 has Pri "P9"; use P1, P2 or P3'),
                ("fault on another's claim", (row("T-1", status="claimed", claim=other, after="T-7"),),
                 "Ledger: T-1 names T-7 in After, which is no other recorded task; fix the After cell"),
                ("source mapped twice", (row("T-1", source="PLAN.md#intro"), row("T-2", status="claimed", claim=other,
                                                                                   touches="`docs/`", source="PLAN.md#intro")),
                 "Ledger: source 'PLAN.md#intro' is mapped more than once: T-1, T-2; keep one row per source")):
            with self.subTest(name):
                fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(*rows)})
                code, _, err = self.stop("s1")
                self.assertEqual(code, 2, err)
                self.assertIn(blocking, err)

    def test_an_own_overlap_blocks_when_two_other_sessions_overlap_under_the_same_ids(self):
        row, mine = fixture.row, fixture.marker("s1")
        self.start("s1")
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(  # T-1 twice, as a hand edit or a bad merge can leave it
            row("T-1", status="claimed", claim=mine, touches="`src/app.py`"),
            row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa", touches="`src/lib/`"),
            row("T-2", status="claimed", claim="sk-0101-bbbbbbbbbbbb", touches="`src/`"))})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        overlap = "T-1 and T-2 are both claimed and their scopes overlap; finish one first or narrow the scopes"
        self.assertIn(f"  Ledger: {overlap}", err, "this session's own overlap blocks")
        self.assertIn(f"note: {overlap}", err, "the same words about the two other sessions stay a note")
        self.assertIn("SAKO gate: 2 problem(s) before you stop:", err, "the repeated ID and the own overlap")

    def test_files_dirty_at_start_count_only_when_they_change_again(self):
        fixture.write(self.root, {"src/pre.py": "draft\n"})
        self.start("s1")
        self.assertEqual(self.stop("s1")[0], 0)
        fixture.write(self.root, {"src/pre.py": "draft, edited\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("src/pre.py (edited after start; already dirty at start)", err)
        fixture.write(self.root, {"src/pre.py": "draft\n"})
        self.assertEqual(self.stop("s1")[0], 0, "back to its start bytes, it is not this session's change")
        code, out, _ = self.run_in(self.root, "start", session="s1", pid=self.host())
        self.assertEqual(self.stop("s1")[0], 0, "a second start keeps the first snapshot")

    def test_commits_since_start_count_and_older_merged_commits_do_not(self):
        self.start("s1")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        fixture.commit_all(self.root, "an unrecorded change")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r'src/app\.py \(in commit [0-9a-f]+ of \d{4}-\d\d-\d\d "an unrecorded change"\)')
        fixture.git(self.root, "reset", "-q", "--hard", "HEAD~1")
        fixture.git(self.root, "checkout", "-qb", "side")
        fixture.write(self.root, {"src/side.py": "x = 1\n"})
        env = dict(fixture.ENV, GIT_COMMITTER_DATE="2001-01-01T00:00:00Z", GIT_AUTHOR_DATE="2001-01-01T00:00:00Z")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], env=env, check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "old work"], env=env, check=True)
        fixture.git(self.root, "checkout", "-q", "main")
        fixture.git(self.root, "merge", "-q", "--ff-only", "side")
        self.assertEqual(self.stop("s1")[0], 0, "a pulled commit made before the session is not this session's change")

    def test_a_commit_that_a_clock_stepping_back_dates_before_the_start_still_counts(self):
        self.start("s1")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        started = sako.my_entry(sako.resolve_roots(self.root), "s1")["started_at"]
        stepped = f"@{started - 2} +0000"  # the date a commit gets from a clock that stepped back two seconds
        env = dict(fixture.ENV, GIT_COMMITTER_DATE=stepped, GIT_AUTHOR_DATE=stepped)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qam", "just after the start"], env=env, check=True)
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r'src/app\.py \(in commit [0-9a-f]+ of [\d-]+ "just after the start"\)')

    def test_renames_and_deletions_name_both_sides(self):
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Move the app", "--done-when", "Runs", "--scope", "src/main.py"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.git(self.root, "mv", "src/app.py", "src/main.py")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("src/app.py (deleted)", err)

    @unittest.skipIf(fixture.WINDOWS, fixture.LEASE)
    def test_a_live_peer_in_this_checkout_is_not_counted_but_one_in_another_worktree_is(self):
        self.start("s1")
        peer = self.start("peer")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "peer"))
        fixture.write(self.root, {"docs/x.md": "notes\n"})
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: docs/x.md: in T-2's scope, claimed by live peer", out)
        self.assertTrue(out.endswith(" in this checkout\nnote: a shared checkout shows what changed, not who changed it.\n"), out)
        os.kill(peer, 15)
        for process in self.processes:
            if process.pid == peer:
                process.wait()
        self.assertEqual(self.stop("s1")[0], 2, "without a live peer the change is unrecorded")
        linked = self.base / "linked"
        fixture.git(self.root, "worktree", "add", "-q", str(linked))
        self.start("s2", where=linked)
        self.start("peer2")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Linked docs", "--done-when", "x", "--scope", "notes/"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "peer2"))
        fixture.write(linked, {"notes/y.md": "notes\n"})
        code, _, err = self.stop("s2", where=linked)
        self.assertEqual(code, 2, err)
        held = f"claimed by {sako.marker_for('peer2')} in another worktree, whose work counts here once merged"
        self.assertIn(f"notes/y.md ({held})", err)
        fixture.write(self.root, {"notes/z.md": "the peer's notes\n"})
        fixture.commit_all(self.root, "peer notes")
        fixture.git(linked, "merge", "-q", "main")
        code, _, err = self.stop("s2", where=linked)
        self.assertEqual(code, 2, err)
        self.assertRegex(err, rf'notes/z\.md \(in commit [0-9a-f]+ of [\d-]+ "peer notes"; {held}\)', "the peer's work, merged in")

    def test_not_counted_notes_name_three_paths_then_how_many_more_under_the_verdict_with_one_caveat_after_them(self):
        self.start("s1")
        self.start("peer")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Peer notes", "--done-when", "Written", "--scope", "notes/"))
        for task in ("T-2", "T-10"):
            fixture.succeeds(fixture.installed_copy(self.root, "claim", task, "--session", "peer"))
        fixture.write(self.root, {f"docs/{name}.md": "notes\n" for name in "abcde"} | {"notes/x.md": "notes\n"})
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        peer = f"claimed by {'unverified' if fixture.WINDOWS else 'live'} peer {sako.marker_for('peer')} in this checkout"
        notes = (f"not counted: docs/a.md, docs/b.md, docs/c.md and 2 more: in T-2's scope, {peer}\n"
                 f"not counted: notes/x.md: in T-10's scope, {peer}\nnote: a shared checkout shows what changed, not who changed it.\n")
        self.assertEqual(out, f"SAKO gate: clear in {self.root.resolve().as_posix()}\n{notes}", "the caveat once, after the last note")
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("SAKO gate: 1 problem(s) before you stop:\n  Recorded:"), "the verdict comes first")
        self.assertTrue(err.endswith(f"\n{notes}"), err)

    def test_a_worktree_nested_in_the_checkout_is_another_checkout_not_a_change(self):
        self.start("s1")
        nested = self.root / ".claude" / "worktrees" / "agent-1"  # where Claude Code puts a subagent's worktree
        fixture.git(self.root, "worktree", "add", "-q", "-b", "agent-1", str(nested))
        self.assertEqual(self.stop("s1")[:2], (0, ""), "a harness's worktree is not this session's change")
        self.start("s2")  # this one starts with the worktree already there
        fixture.write(nested, {"src/app.py": "print(4)\n"})
        fixture.commit_all(nested, "work in the worktree")
        self.assertEqual(self.stop("s2")[:2], (0, ""), "that checkout's own commits are judged there")
        fixture.git(self.root, "merge", "-q", "--ff-only", "agent-1")
        code, _, err = self.stop("s2")
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r'src/app\.py \(in commit [0-9a-f]+ of \d{4}-\d\d-\d\d "work in the worktree"\)', "merged work still counts")
        fixture.git(self.root, "worktree", "remove", "--force", str(nested))
        code, _, err = self.stop("s2")
        self.assertEqual(code, 2, err)
        self.assertNotIn(".claude/worktrees", err, "a nested checkout removed after start is gone, not edited")

    def test_a_nested_worktree_tracked_as_a_gitlink_still_counts(self):
        nested = self.root / "wt"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "wt", str(nested))
        fixture.git(self.root, "add", "wt")  # Git warns, then records the embedded checkout as a gitlink
        fixture.commit_all(self.root, "track the worktree")
        self.start("s1")
        fixture.write(nested, {"src/app.py": "print(8)\n"})
        fixture.commit_all(nested, "moved on")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: wt", err)

    @unittest.skipIf(sys.platform in ("win32", "darwin"), "macOS and Windows file names are Unicode: no byte name exists")
    def test_an_odd_worktree_path_never_makes_the_gate_skip(self):
        self.start("s1")
        fixture.git(self.root, "worktree", "add", "-q", "-b", "odd", str(self.base / os.fsdecode(b"wt-\xff")))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/app.py", err)

    def test_a_nested_worktree_is_no_checked_content(self):
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"]})})
        self.start("s1")
        fixture.git(self.root, "worktree", "add", "-q", "-b", "agent-1", str(self.root / ".claude/worktrees/café"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s1"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Read only", "--session", "s1"))
        self.assertEqual(self.stop("s1")[:2], (0, ""), "no checked content moved, so no fresh check was needed")

    def test_closed_work_left_uncommitted_in_a_sibling_worktree_blocks_only_the_gate_run_there(self):
        fixture.write(self.root, {".gitignore": "__pycache__/\n*.log\n", "notes.txt": "untracked\n"})  # uncommitted before the start
        self.start("s1")  # its start record lists those files of this checkout, so it holds for no other checkout
        linked = self.base / "linked"
        fixture.git(self.root, "worktree", "add", "-q", str(linked))
        self.assertEqual(self.run_in(linked, "claim", "T-1", session="s1")[0], 0)
        fixture.write(linked, {"src/app.py": "print(2)\n"})  # work done by path in the sibling; the session's hooks run here
        for args in (("verify",), ("close", "T-1", "--evidence", "Prints 2")):
            code, out, err = self.run_in(linked, *args, session="s1")
            self.assertEqual(code, 0, out + err)
        here, there = self.root.resolve().as_posix(), linked.resolve().as_posix()
        self.assertIn(f"changed this session in {here}: 0", self.run_in(self.root, "check", session="s1")[1])
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1"), (0, f"SAKO gate: clear in {here}\n", ""))
        self.assertEqual(self.stop("s1"), (0, "", ""), "the gate judges the checkout it runs in, and a clear stop is silent")
        self.assertIn(f"changed this session in {there}: 1", self.run_in(linked, "check", session="s1")[1])
        code, _, err = self.run_in(linked, "check", "--gate", session="s1")
        self.assertEqual(code, 2, err)
        self.assertIn("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-1 is closed but its changes are not committed: "
                      "src/app.py", err, "run there, only Committed blocks, since the change is recorded and that stamp is fresh")
        fixture.commit_all(linked, "Invented task")
        self.assertEqual(self.run_in(linked, "check", "--gate", session="s1"), (0, f"SAKO gate: clear in {there}\n", ""))

    def test_a_pass_in_the_worktree_that_did_the_work_proves_its_merge_here_on_identical_content(self):
        self.start("s1")  # a coordinator in the main checkout holds the task; a helper works in a linked worktree
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        linked = self.base / "linked"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(linked))
        fixture.write(linked, {"src/app.py": "print(2)\n"})
        fixture.commit_all(linked, "Invented task")
        self.assertEqual(self.run_in(linked, "verify")[0], 0)
        fixture.git(self.root, "merge", "-q", "--ff-only", "side")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        close = ("close", "T-1", "--evidence", "Prints 2", "--session", "s1")
        refusal = fixture.installed_copy(self.root, *close)
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is none", refusal.stderr, "the pass there covers other content than this")
        fixture.git(self.root, "checkout", "--", "src/app.py")
        fixture.succeeds(fixture.installed_copy(self.root, *close))
        self.assertRegex((self.root / ".sako/work/DONE.md").read_text(), r"; check pass [^;]+ in worktree linked\]")
        self.assertEqual(self.stop("s1"), (0, "", ""), "the merged content is the content checked there")

    def test_a_commit_recorded_under_another_sessions_closed_task_is_not_this_sessions(self):
        self.start("s1")
        self.start("s2")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s2"))
        fixture.write(self.root, {"docs/x.md": "notes\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Wrote notes", "--session", "s2"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        kind = "unverified" if fixture.WINDOWS else "live"  # Windows sees no process, so a live peer there is unverified
        self.assertIn(f"not counted: docs/x.md: in T-2's scope, closed by {kind} peer", out, "closed before it was committed")
        fixture.commit_all(self.root, "T-2: notes")
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s2"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertRegex(out, rf"(?m)not counted: docs/x\.md: committed under T-2, closed by {sako.marker_for('s2')}(;|$)",
                         "the closer's marker alone, without its claim time")

    def test_only_a_task_closed_on_or_after_the_day_of_a_commit_covers_it(self):
        fixture.write(self.root, {".sako/work/DONE.md": fixture.done_file(fixture.row(
            "T-5", "Broad import", status="done", claim=sako.marker_for("s2", "0101"), touches="`src/`, `docs/`"))})
        self.start("s1")
        self.start("s2")
        fixture.write(self.root, {"src/app.py": "print(5)\n"})
        fixture.commit_all(self.root, "work with no task")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, "an old close excuses no later commit, even while its closer is live\n" + err)
        self.assertRegex(err, r'covered by no task you claimed or closed: src/app\.py \(in commit [0-9a-f]+ of '
                              r'\d{4}-\d\d-\d\d "work with no task"\)')
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s2"))
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 5", "--session", "s2"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: src/app.py: committed under T-1, closed by sk-", out, "a live closer, the same day")

    def test_a_commits_day_is_read_in_the_local_zone_that_dates_a_receipt(self):
        fixture.write(self.root, {".sako/work/DONE.md": fixture.done_file(
            fixture.row("T-5", "Broad import", status="done", claim="sk-0101-aaaaaaaaaaaa", touches="`src/`"))})
        self.start("s1")
        fixture.write(self.root, {"src/app.py": "print(5)\n"})
        env = dict(fixture.ENV, GIT_AUTHOR_DATE="2026-01-02T01:00:00+0000")  # 17:00 on January 1, eight hours west
        subprocess.run(["git", "-C", str(self.root), "commit", "-qam", "evening work"], env=env, check=True)
        with patch.dict(fixture.ENV, TZ="UTC0"):  # POSIX zone values without a daylight rule, which Windows reads too
            code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 2, "January 2 in this zone, a day after the close\n" + out + err)
        with patch.dict(fixture.ENV, TZ="PST8"):
            code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: src/app.py: committed under T-5, closed by sk-0101-aaaaaaaaaaaa", out)

    def test_a_live_peers_claim_is_named_before_a_closed_task_that_also_covers_the_commit(self):
        self.start("s1")
        self.start("s2")
        self.start("s3")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s3"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Outlined docs", "--session", "s3"))
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s3"))
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Peer notes", "--done-when", "x.md says so", "--scope", "docs/x.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s2"))
        fixture.write(self.root, {"docs/x.md": "peer notes\n"})
        fixture.commit_all(self.root, "peer notes")
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: docs/x.md: in T-10's scope, claimed by ", out)
        self.assertNotIn("committed under T-2", out)
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Notes read back", "--session", "s2"))
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s2"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: docs/x.md: committed under T-10, closed by sk-", out, "of two closes that day, the later")

    def test_a_close_scoped_dot_takes_in_an_ended_peers_uncommitted_edit(self):
        """A documented limit (docs/limits.md). The gate sees what changed in a shared checkout, not who changed it, and a
        task scoped . covers every file, so its close counts a peer's leftover edit as this session's closed work."""
        self.start("s1")
        self.start("peer")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "peer"))
        fixture.write(self.root, {"docs/draft.md": "the peer's draft\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Drafted", "--session", "peer"))
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "peer"))  # it ends with its edit uncommitted
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: docs/draft.md", err)
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Tidy the project", "--done-when", "Tidy", "--scope", "."))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Tidied", "--session", "s1"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-10 is closed but its changes are not committed: "
                      "docs/draft.md", err, "the close covers the peer's edit, so this session is asked to commit it")

    def test_without_a_start_record_every_dirty_path_counts_and_the_gate_says_so(self):
        fixture.write(self.root, {"src/new.py": "x = 1\n"})
        code, _, err = self.run_in(self.root, "check", "--gate", session="never-started")
        self.assertEqual(code, 2, err)
        self.assertIn("no start record for this session", err)
        self.assertIn("commits since you started were not checked", err)
        (self.root / "src/new.py").unlink()
        code, out, _ = self.run_in(self.root, "check", "--gate", session="never-started")
        self.assertEqual(code, 0, out)
        self.assertIn("were not checked", out)

    def test_commits_with_an_empty_message_are_read_like_any_other(self):
        self.start("s1")
        fixture.git(self.root, "commit", "-q", "--allow-empty", "--allow-empty-message", "-m", "")  # the oldest since the start
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/app.py\n", err)
        fixture.git(self.root, "add", "-A")
        fixture.git(self.root, "commit", "-q", "--allow-empty-message", "-m", "")
        code, _, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r'covered by no task you claimed or closed: src/app\.py \(in commit [0-9a-f]+ of \d{4}-\d\d-\d\d ""\)')

    def test_committed_covers_only_this_sessions_own_changes(self):
        fixture.write(self.root, {"src/wip.py": "someone else's draft\n"})
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(4)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 4", "--session", "s1"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Committed: T-1 is closed but its changes are not committed: src/app.py", err)
        self.assertIn("git add -- src/app.py, then git commit -m 'Invented task' when the owner has allowed commits", err)
        self.assertNotIn("commit -m 'T-1", err, "a task ID stays out of the product's history")
        self.assertFalse(set("<>&") & set(err), err)
        self.assertNotIn("src/wip.py", err.split("Committed:")[1].split("\n")[0])
        fixture.git(self.root, "add", "src/app.py")
        fixture.git(self.root, "commit", "-qm", "Invented task")
        self.assertEqual(self.stop("s1")[0], 0)
        self.backdate("T-1")
        fixture.write(self.root, {"src/wip.py": "someone else's draft, now edited\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/wip.py "
                      "(edited after start; already dirty at start)", err, "an edit after the close is not the closed task's")
        self.assertNotIn("Committed:", err)

    def test_a_live_peers_closed_task_excuses_no_edit_made_after_it_closed(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        fixture.git(self.root, "commit", "-qam", "Invented task")
        self.backdate("T-1")
        self.start("s2")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})  # with no task, in the scope of T-1, which s1 closed earlier
        unrecorded = "Recorded: changed this session but covered by no task you claimed or closed: src/app.py\n"
        code, _, err = self.stop("s2")
        self.assertEqual(code, 2, err)
        self.assertIn(unrecorded, err, "a live peer's closed task no longer excuses a later edit")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(unrecorded, err, "the closer sees what changed, not who changed it")
        self.assertNotIn("Committed:", err, "and is not told to commit the edit under its closed task's title")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Plan", "--done-when", "Written", "--scope", ".sako/work/plan.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s2"))
        refusal = fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Planned", "--session", "s2")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr, "the edit is s2's own, so its records work needs the check")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Print three", "--done-when", "Prints 3", "--scope", "src/app.py"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s2"))
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s2")[0], 0, "once recorded, it is s2's claimed work")

    def test_a_closed_and_committed_task_is_not_named_for_the_next_tasks_change_in_its_scope(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        fixture.git(self.root, "commit", "-qam", "Invented task")
        self.backdate("T-1")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Print three", "--done-when", "Prints 3", "--scope", "src/"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Prints 3", "--session", "s1"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-10 is closed but its changes are not committed: "
                      "src/app.py\n    fix: git add -- src/app.py, then git commit -m 'Print three'", err)
        self.assertNotIn("T-1 ", err, "T-1's work is in its commit; the change came after it closed")

    def test_the_close_minute_is_read_in_the_receipts_zone_wherever_the_gate_runs(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        with patch.dict(fixture.ENV, TZ="NST3:30"):  # three and a half hours west of UTC, as POSIX writes it
            fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        self.assertRegex((self.root / ".sako/work/DONE.md").read_text(), r"; closed [\dT:-]{16}-0330;")
        zones = ("NST3:30", "UTC0", "JST-9", "PST8")
        for zone in zones:
            with patch.dict(fixture.ENV, TZ=zone):
                err = self.run_in(self.root, "check", "--gate", session="s1")[2]
            self.assertIn("Committed: T-1 is closed but its changes are not committed: src/app.py", err,
                          f"an edit made before the close is the closed task's, judged in {zone}")
        self.backdate("T-1", east=9)
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        for zone in zones:
            with patch.dict(fixture.ENV, TZ=zone):
                err = self.run_in(self.root, "check", "--gate", session="s1")[2]
            self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/app.py", err, zone)
            self.assertNotIn("Committed:", err, f"an edit after the close is new work, judged in {zone}")

    def test_work_under_a_new_claim_does_not_reopen_earlier_closes(self):
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(8)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 8", "--session", "s1"))
        fixture.commit_all(self.root, "T-1: prints 8")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Next change", "--done-when", "Prints 9", "--scope", "src/"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(9)\n"})
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("advisory: T-10 is claimed and unfinished", out)

    def test_handoff_advisories_never_block(self):
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s1"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("advisory: T-2 is claimed and unfinished; if this session ends before closing it, the claim reads as "
                      "stale and another session may take it over with a reason, so write .sako/work/T-2-invented-task/"
                      "handoff.md with where it stands", out)
        self.assertEqual(self.stop("s1")[:2], (0, ""), "a clear stop stays silent; pausing is free")
        fixture.write(self.root, {".sako/work/T-2-invented-task/handoff.md": "Where it stands: drafted\n"})
        self.assertNotIn("advisory", self.run_in(self.root, "check", "--gate", session="s1")[1])
        fixture.write(self.root, {"docs/notes.md": "notes\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Notes read back", "--session", "s1"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Committed: T-2 is closed", err)
        self.assertIn("advisory: T-2 is closed and its handoff folder remains; move lasting facts to their home, "
                      "then delete .sako/work/T-2-invented-task/.", err)
        fixture.commit_all(self.root, "T-2: notes")
        code, out, _ = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out)
        self.assertIn("advisory: T-2 is closed and its handoff folder remains", out)
        shutil.rmtree(self.root / ".sako/work/T-2-invented-task")
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1")[1],
                         f"SAKO gate: clear in {self.root.resolve().as_posix()}\n")

    def test_a_merge_or_a_takeover_needs_a_fresh_check_before_close(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.git(self.root, "checkout", "-qb", "side")
        fixture.write(self.root, {"src/app.py": "print(9)\n"})
        fixture.commit_all(self.root, "side change")
        fixture.git(self.root, "checkout", "-q", "main")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s1"))
        fixture.git(self.root, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        refusal = fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Notes", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("verify before closing", refusal.stderr)
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(10)\n"})
        for process in self.processes:
            process.terminate()
            process.wait()
        self.start("s2")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s2", "--takeover", "s1 ended"))
        refusal = fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Taken over", "--session", "s2")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("verify before closing", refusal.stderr)

    def test_a_records_only_task_needs_no_check_while_only_others_moved_checked_content(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.write(self.root, {"src/app.py": "print('a draft from before this session')\n"})
        self.start("s1")
        for number in (1, 2, 3):
            fixture.succeeds(fixture.installed_copy(self.root, "add", f"Plan step {number}", "--done-when", "Written",
                                                    "--scope", f".sako/work/plan-{number}.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s1"))
        refusal = fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Docs", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr, "a product task still needs the check")
        done = self.root / ".sako/work/DONE.md"
        for task, stamp in (("T-10", "stale"), ("T-11", "missing"), ("T-12", "fresh")):
            with self.subTest(stamp=stamp):
                if stamp == "missing":
                    (self.root / ".sako/state/stamp.json").unlink()
                elif stamp == "fresh":
                    fixture.succeeds(fixture.installed_copy(self.root, "verify"))
                self.assertIn(f"evidence: {'none' if stamp == 'missing' else stamp}", self.run_in(self.root, "check")[1])
                fixture.succeeds(fixture.installed_copy(self.root, "claim", task, "--session", "s1"))
                fixture.succeeds(fixture.installed_copy(self.root, "close", task, "--evidence", "Written", "--session", "s1"))
                receipt = next(r.evidence for r in sako.parse_rows({"done": done.read_text()}) if r.id == task)
                self.assertRegex(receipt, rf"\[{sako.marker_for('s1')} since [^;]+; closed [^;]+; no automated check \(records only\)\]$")
                if stamp == "stale":
                    code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
                    self.assertEqual(code, 0, out + err)
        fixture.write(self.root, {"notes.txt": "this session's own change\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: notes.txt", err)
        self.assertNotIn("Proven:", err)

    def test_edits_left_uncommitted_before_the_start_stay_others_once_their_author_commits_them(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.write(self.root, {"src/app.py": "print('a draft from before this session')\n"})
        self.start("s1")
        for number in (1, 2, 3):
            fixture.succeeds(fixture.installed_copy(self.root, "add", f"Plan step {number}", "--done-when", "Written",
                                                    "--scope", f".sako/work/plan-{number}.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Written", "--session", "s1"))
        fixture.commit_all(self.root, "The draft, committed by its author")
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        done = self.root / ".sako/work/DONE.md"
        for task, stamp in (("T-11", "stale"), ("T-12", "fresh")):
            with self.subTest(stamp=stamp):
                if stamp == "fresh":
                    fixture.succeeds(fixture.installed_copy(self.root, "verify"))
                fixture.succeeds(fixture.installed_copy(self.root, "claim", task, "--session", "s1"))
                close = fixture.installed_copy(self.root, "close", task, "--evidence", "Written", "--session", "s1")
                self.assertEqual(close.returncode, 0, close.stdout + close.stderr)
                receipt = next(r.evidence for r in sako.parse_rows({"done": done.read_text()}) if r.id == task)
                self.assertTrue(receipt.endswith("; no automated check (records only)]"), receipt)
        fixture.write(self.root, {"src/app.py": "print('edited again, by this session')\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-10, T-11, T-12 closed after checked content changed since you started", err,
                      "an edit since the start is this session's, on a path that was dirty at start too")

    @unittest.skipIf(fixture.WINDOWS, "Windows keeps no executable bit")
    def test_a_committed_change_to_only_the_executable_bit_of_a_file_dirty_at_start_is_this_sessions(self):
        fixture.write(self.root, {"src/app.py": "print('a draft from before this session')\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        for number in (1, 2):
            fixture.succeeds(fixture.installed_copy(self.root, "add", f"Plan step {number}", "--done-when", "Written",
                                                    "--scope", f".sako/work/plan-{number}.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Written", "--session", "s1"))
        os.chmod(self.root / "src/app.py", 0o755)
        fixture.commit_all(self.root, "Make the app executable")
        self.assertIn("evidence: stale", self.run_in(self.root, "check")[1], "the stamp counts the executable bit")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: src/app.py (edited after start; already dirty at start)", err)
        self.assertIn("Proven: T-10 closed after checked content changed since you started", err)
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        refusal = fixture.installed_copy(self.root, "close", "T-11", "--evidence", "Written", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr, "so the session's own mode change needs the check")

    @unittest.skipIf(fixture.WINDOWS, "Windows keeps no executable bit")
    def test_a_change_to_executable_bits_git_does_not_track_moves_neither_the_check_nor_the_session(self):
        fixture.write(self.root, {"src/lib.py": "x = 1\n"})
        fixture.commit_all(self.root, "A library")
        fixture.write(self.root, {"src/app.py": "print('a draft from before this session')\n", "src/new.py": "y = 2\n"})
        for name, mode in (("app.py", 0o744), ("new.py", 0o644), ("lib.py", 0o644)):
            os.chmod(self.root / "src" / name, mode)
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Plan", "--done-when", "Written", "--scope", ".sako/work/plan.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        for name, mode in (("app.py", 0o755), ("new.py", 0o654), ("lib.py", 0o654)):  # dirty, new and clean at start
            os.chmod(self.root / "src" / name, mode)
        self.assertIn("evidence: fresh", self.run_in(self.root, "check")[1], "the stamp counts only the owner's bit, as Git does")
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Written", "--session", "s1"))
        self.assertIn("; no automated check (records only)]", (self.root / ".sako/work/DONE.md").read_text(), "so does the start record")
        code, out, err = self.stop("s1")
        self.assertEqual((code, err), (0, ""), out)

    def test_a_records_only_task_needs_the_check_once_this_session_moved_checked_content(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.git(self.root, "checkout", "-qb", "side")
        fixture.write(self.root, {"src/app.py": "print(9)\n"})
        fixture.commit_all(self.root, "side change")
        fixture.git(self.root, "checkout", "-q", "main")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Record the merge", "--done-when", "Logged",
                                                "--scope", ".sako/work/merges.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.git(self.root, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        refusal = fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Merged", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("verify before closing", refusal.stderr)
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Merged", "--session", "s1"))
        self.assertIn("Merged [sk-", (self.root / ".sako/work/DONE.md").read_text())
        self.assertIn("; check pass ", (self.root / ".sako/work/DONE.md").read_text())
        fixture.write(self.root, {"src/app.py": "print(10)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-10 closed after checked content changed since you started", err)

    def test_an_own_edit_or_a_merge_of_commits_older_than_the_session_needs_the_check_for_records_only_tasks(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.git(self.root, "checkout", "-qb", "side")
        fixture.write(self.root, {"src/app.py": "print(9)\n"})
        env = dict(fixture.ENV, GIT_COMMITTER_DATE="2001-01-01T00:00:00Z", GIT_AUTHOR_DATE="2001-01-01T00:00:00Z")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], env=env, check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "old work"], env=env, check=True)
        fixture.git(self.root, "checkout", "-q", "main")
        self.start("s1")
        for number in (1, 2):
            fixture.succeeds(fixture.installed_copy(self.root, "add", f"Log merge {number}", "--done-when", "Logged",
                                                    "--scope", f".sako/work/merge-{number}.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/extra.py": "x = 1\n"})
        refusal = fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Logged", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr, "this session's own uncommitted edit counts")
        (self.root / "src/extra.py").unlink()
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Logged", "--session", "s1"))
        self.assertIn("no automated check (records only)]", (self.root / ".sako/work/DONE.md").read_text())
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        fixture.git(self.root, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        refusal = fixture.installed_copy(self.root, "close", "T-11", "--evidence", "Merged", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr, "a merge here is this session's, whenever its commits were made")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-10 closed after checked content changed since you started", err)
        self.assertNotIn("Recorded:", err, "a merged commit older than the session is not its change to record")

    def test_a_records_only_close_then_a_claim_still_held_can_pause_mid_change(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Write the plan", "--done-when", "Written",
                                                "--scope", ".sako/work/plan.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {".sako/work/plan.md": "the plan\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Written", "--session", "s1"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual((code, err), (0, ""), "a claim still held explains the stale check; pausing is free")

    @unittest.skipIf(fixture.WINDOWS, fixture.LEASE)
    def test_a_records_only_task_needs_no_check_while_a_live_peer_here_edits_under_its_claim(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        peer = self.start("peer")
        for number in (1, 2):
            fixture.succeeds(fixture.installed_copy(self.root, "add", f"Plan step {number}", "--done-when", "Written",
                                                    "--scope", f".sako/work/plan-{number}.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "peer"))
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Written", "--session", "s1"))
        self.assertIn("Written [sk-", (self.root / ".sako/work/DONE.md").read_text())
        self.assertIn("no automated check (records only)]", (self.root / ".sako/work/DONE.md").read_text())
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: src/app.py: in T-1's scope, claimed by live peer", out)
        os.kill(peer, 15)
        for process in self.processes:
            if process.pid == peer:
                process.wait()
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        refusal = fixture.installed_copy(self.root, "close", "T-11", "--evidence", "Written", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr, "without the live peer the change counts as this session's")

    def test_a_records_only_task_needs_no_check_while_a_live_peer_here_commits_its_claimed_work(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        self.start("peer")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Peer work", "--done-when", "Prints 3", "--scope", "src/app.py"))
        for number in (1, 2):
            fixture.succeeds(fixture.installed_copy(self.root, "add", f"Plan step {number}", "--done-when", "Written",
                                                    "--scope", f".sako/work/plan-{number}.md"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "peer"))
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        fixture.commit_all(self.root, "Peer work")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-11", "--evidence", "Written", "--session", "s1"))
        self.assertIn("Written [sk-", (self.root / ".sako/work/DONE.md").read_text(), "the peer committed before this close")
        self.assertIn("no automated check (records only)]", (self.root / ".sako/work/DONE.md").read_text())
        fixture.write(self.root, {"src/app.py": "print(4)\n"})
        fixture.commit_all(self.root, "More peer work")
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: src/app.py: in T-10's scope, claimed by ", out, "and after it")
        fixture.write(self.root, {"src/other.py": "x = 1\n"})
        fixture.commit_all(self.root, "Work outside the peer's claim")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-11 closed after checked content changed since you started", err,
                      "a commit no live peer here claims is this session's")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-12", "--session", "s1"))
        refusal = fixture.installed_copy(self.root, "close", "T-12", "--evidence", "Written", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr)

    def test_committed_names_new_files_so_generated_ones_are_not_committed(self):
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(11)\n", "src/cache.pyc": "generated\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 11", "--session", "s1"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("New files among them (src/cache.pyc): delete or ignore any that is generated", err)

    def test_proven_names_its_rule_and_fix(self):
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(5)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 5", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(6)\n"})
        code, _, err = self.stop("s1")
        self.assertIn("Proven: T-1 closed after checked content changed since you started, and the check is stale", err)
        self.assertFalse(set("<>&") & set(err), err)
        self.assertIn("verify, fix what it reports, then stop again", err)

    def test_status_counts_this_sessions_changes(self):
        self.start("s1")
        fixture.write(self.root, {"src/app.py": "print(7)\n"})
        out = fixture.succeeds(fixture.installed_copy(self.root, "status", "--session", "s1"))
        self.assertIn(f"changed this session in {self.root.resolve().as_posix()}: 1; unrecorded: src/app.py", out)

    def test_a_hook_that_fails_on_its_own_lets_the_session_go(self):
        self.start("s1")
        fixture.write(self.root, {"src/app.py": "print(2)\n"})  # a working gate would block this stop
        args = argparse.Namespace(event=None, session=None, client=None,
                                  resolved=(sako.resolve_roots(self.root), sako.load_config(self.root / ".sako")))
        payload = io.StringIO(json.dumps({"session_id": "s1", "hook_event_name": "Stop"}))
        with patch.object(sako, "gate", side_effect=RuntimeError("a bug")), patch.object(sys, "stdin", payload), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(sako.cmd_hook(args), 0, "a bug in the bones lets the session go")
        self.assertIn("unexpected error during Stop, skipped: RuntimeError('a bug')", err.getvalue())
        held, shown = PermissionError(13, "The process cannot access the file", "src\\app.py"), []  # another program holds it
        for active, code in ((False, 2), (True, 0)):
            payload = io.StringIO(json.dumps({"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": active}))
            with patch.object(sako, "gate", side_effect=held), patch.object(sys, "stdin", payload), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(sako.cmd_hook(args), code, "an unreadable file blocks one stop and names itself")
            shown.append(err.getvalue())
        self.assertIn("SAKO gate: [Errno 13] The process cannot access the file: 'src\\\\app.py'\nFix what it names", shown[0])
        self.assertEqual(shown[1], "", "the repeated stop goes")

    def test_a_session_end_whose_note_fails_leaves_no_presence(self):
        roots = sako.resolve_roots(self.root)
        args = argparse.Namespace(event=None, session=None, client="claude", resolved=(roots, sako.load_config(roots.kit)))
        for session, failure in (("s1", RuntimeError("a bug")), ("s2", SystemExit(143))):  # 143: the client ended the hook
            self.start(session)
            payload = io.StringIO(json.dumps({"session_id": session, "hook_event_name": "SessionEnd"}))
            with patch.object(sako, "note_hook", side_effect=failure), patch.object(sys, "stdin", payload), \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.suppress(SystemExit):
                sako.cmd_hook(args)
            self.assertIsNone(sako.my_entry(roots, session), f"presence goes before the hook is noted: {failure!r}")

    @unittest.skipUnless(fixture.symlinks() and Path("/bin/sh").exists(), "builds a PATH of symlinks for /bin/sh")
    def test_a_hook_command_that_cannot_start_python_exits_1_never_2(self):
        bin_dir = self.base / "bin"  # the kit and git, but no Python: the hook command itself fails
        bin_dir.mkdir()
        shutil.copyfile(fixture.SAKO, self.root / ".sako/sako.py")
        (bin_dir / "git").symlink_to(shutil.which("git"))
        result = subprocess.run(["/bin/sh", "-c", sako.hook_command("claude")], cwd=self.root, capture_output=True,
                                text=True, input="{}", env={"PATH": str(bin_dir)})
        self.assertNotIn(result.returncode, (0, 2), result.stderr)
        self.assertIn("python3", result.stderr)


if __name__ == "__main__":
    unittest.main()
