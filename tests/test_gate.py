"""The finish gate: Recorded, Proven and Committed, each finding with its rule and its fix."""

from pathlib import Path
import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
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

    def tearDown(self):
        self.workspace.cleanup()

    def run_in(self, where, *args, session=None):
        return fixture.cli(where, *args, session=session)

    def stop(self, session, where=None, active=False):
        payload = {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": active}
        return fixture.cli(where or self.root, "hook", stdin=json.dumps(payload))

    def start(self, session, where=None):
        code, out, err = self.run_in(where or self.root, "start", session=session)
        self.assertEqual(code, 0, out + err)

    def seals(self):
        """What the closes made in the main checkout sealed, by task."""
        return json.loads((self.root / ".sako/state/seals.json").read_text())

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
        out = fixture.hook(self.root, "SessionStart", "s1")[1]
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
        self.assertIn(f"your marker: {marker}\n", fixture.hook(self.root, "SessionStart", "s1")[1])
        self.assertIn("your rows: T-1 (claimed, no handoff) touches src/\n", self.run_in(self.root, "status", session="s1")[1])
        self.assertIn("ledger: consistent", self.run_in(self.root, "check")[1], "the same ID brings its claim back to life")

    def test_closed_work_left_uncommitted_through_an_end_and_a_resume_is_still_asked_to_be_committed(self):
        """The new start record lists the closed task's file as dirty at start, so the seal is what shows it is that
        task's work."""
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        for event in ("SessionEnd", "SessionStart"):
            self.assertEqual(fixture.hook(self.root, event, "s1")[0], 0)
        self.assertIn("src/app.py", sako.my_entry(sako.resolve_roots(self.root), "s1")["dirty_at_start"])
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-1 is closed but its changes "
                                       "are not committed: src/app.py\n"), err)
        self.start("s2")  # its own closed task covers the file by its scope, but its close sealed none of it
        fixture.succeeds(fixture.installed_copy(self.root, "add", "More app", "--done-when", "Runs", "--scope", "src/"))
        for args in (("claim", "T-10"), ("close", "T-10", "--evidence", "Read the app")):
            fixture.succeeds(fixture.installed_copy(self.root, *args, "--session", "s2"))
        self.assertEqual(self.stop("s2")[:2], (0, ""), "another session is not asked")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: src/app.py (edited after start; already dirty at start; "
                      "changed after you closed T-1)\n", err, "content moved since the seal is Recorded's business")
        self.assertNotIn("Committed:", err)

    def test_a_session_that_changed_nothing_passes(self):
        fixture.write(self.root, {"src/wip.py": "draft\n", ".sako/work/TASKS.md": fixture.ledger(
            fixture.row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa"), fixture.row("T-2", touches="`docs/`"))})
        stale = ("T-1 is claimed by sk-0101-aaaaaaaaaaaa, which is not a live session; resume it, or take it over (claim T-1 "
                 "--takeover REASON), then finish or park it (edit T-1 --park REASON)")
        code, out, err = self.run_in(self.root, "start", session="s1")
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
        self.assertIn("note: T-1 is claimed by sk-0101-aaaaaaaaaaaa, which is not a live session; resume it, or take it over "
                      "(claim T-1 --takeover REASON), then finish or park it (edit T-1 --park REASON)",
                      self.run_in(self.root, "check", "--gate", session="s1")[1])
        tasks = self.root / ".sako/work/TASKS.md"  # its handoff says it waits on the owner
        tasks.write_text(tasks.read_text().replace("| sk-0101-aaaaaaaaaaaa |", "| parked: owner: approves the copy, 2026-01-02 |"))
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1"),
                         (0, f"SAKO gate: clear in {self.root.resolve().as_posix()}\n", ""), "an edit of the records is no change")
        self.assertEqual(self.stop("s1"), (0, "", ""))
        views = {"start": self.run_in(self.root, "start", session="s2")[1],
                 **{view: self.run_in(self.root, view, session="s1")[1] for view in ("check", "status")}}
        for view, out in views.items():
            with self.subTest(view=view):
                self.assertIn("ledger: consistent", out)
                self.assertNotIn("T-1 is claimed", out)
        self.assertIn("T-1  parked: owner: approves the copy, 2026-01-02", self.run_in(self.root, "next")[1])

    def test_a_bystander_parks_a_stale_row_with_the_commands_its_note_prints(self):
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            fixture.row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa"), fixture.row("T-2", touches="`docs/`"))})
        self.start("s1")
        note = self.run_in(self.root, "check", "--gate", session="s1")[1]
        printed = re.findall(r"\(((?:claim|edit) T-1 [^)]*)\)", note)
        self.assertEqual(printed, ["claim T-1 --takeover REASON", "edit T-1 --park REASON"], note)
        reasons = iter(("its session ended; its handoff says it waits on the owner", "owner: approves the copy"))
        for command in printed:  # in order, each REASON filled in
            code, out, err = self.run_in(self.root, *[next(reasons) if word == "REASON" else word for word in shlex.split(command)],
                                         session="s1")
            self.assertEqual(code, 0, out + err)
        self.assertEqual(out, "T-1: parked, your claim released; recorded locally, not committed\n")
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1"),
                         (0, f"SAKO gate: clear in {self.root.resolve().as_posix()}\n", ""), "no finding or note names it after")
        self.assertIn("T-1  parked: owner: approves the copy", self.run_in(self.root, "next")[1])

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
        self.assertIn(f"note: T-2 is claimed by {other}, which is not a live session; resume it, or take it over (claim T-2 "
                      "--takeover REASON), then finish or park it (edit T-2 --park REASON)", out)
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            *peers, row("T-3", status="claimed", claim=mine, touches="`src/app.py`"), row("T-4"), row("T-4", touches="`etc/`"))})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        for blocking in ("Ledger: T-1 and T-3 are both claimed and their scopes overlap; finish one first or narrow yours "
                         "(edit T-3 --scope PATH)\n",
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
        overlap = "T-1 and T-2 are both claimed and their scopes overlap; finish one first or narrow "
        self.assertIn(f"  Ledger: {overlap}yours (edit T-1 --scope PATH)\n", err, "this session's own overlap blocks")
        self.assertIn(f"note: {overlap}the scopes", err, "the same overlap between the two other sessions stays a note")
        self.assertIn("SAKO gate: 2 problem(s) before you stop:", err, "the repeated ID and the own overlap")

    def test_an_own_overlap_prints_the_edit_that_narrows_this_sessions_row(self):
        self.start("s1")
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(  # an overlap a hand edit left between two claims
            fixture.row("T-1", status="claimed", claim=fixture.marker("s1")),
            fixture.row("T-2", status="claimed", claim="sk-0101-bbbbbbbbbbbb", touches="`src/lib/`"))})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("  Ledger: T-1 and T-2 are both claimed and their scopes overlap; finish one first or narrow yours "
                      "(edit T-1 --scope PATH)\n", err)
        printed = shlex.split(re.search(r"\((edit [^)]*)\)", err)[1])
        self.assertEqual(self.run_in(self.root, *["src/app.py" if word == "PATH" else word for word in printed], session="s1"),
                         (0, "T-1: claimed; scope src/app.py; recorded locally, not committed\n", ""))
        self.assertEqual(self.stop("s1"), (0, "", ""), "narrowed clear of T-2's src/lib/, the overlap is gone")

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
        code, out, _ = self.run_in(self.root, "start", session="s1")
        self.assertEqual(self.stop("s1")[0], 0, "a second start keeps the first snapshot")

    def test_commits_since_start_count_and_older_merged_commits_do_not(self):
        fixture.git(self.root, "checkout", "-qb", "side")  # made before the session, so it is dated before its start
        fixture.write(self.root, {"src/side.py": "x = 1\n"})
        env = dict(fixture.ENV, GIT_COMMITTER_DATE="2001-01-01T00:00:00Z", GIT_AUTHOR_DATE="2001-01-01T00:00:00Z")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], env=env, check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "old work"], env=env, check=True)
        fixture.git(self.root, "checkout", "-q", "main")
        self.start("s1")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        fixture.commit_all(self.root, "an unrecorded change")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r'src/app\.py \(in commit [0-9a-f]+ of \d{4}-\d\d-\d\d "an unrecorded change"\)')
        fixture.git(self.root, "reset", "-q", "--hard", "HEAD~1")
        fixture.git(self.root, "merge", "-q", "--ff-only", "side")
        self.assertEqual(self.stop("s1")[0], 0, "a merged commit made before the session is not this session's change")

    def test_a_pull_brings_no_older_commit_and_a_pull_that_rebases_names_your_commits_by_their_new_hashes(self):
        upstream = self.base / "upstream"
        fixture.git(self.base, "clone", "-q", str(self.root), str(upstream))
        old = dict(fixture.ENV, GIT_COMMITTER_DATE="2001-01-01T00:00:00Z", GIT_AUTHOR_DATE="2001-01-01T00:00:00Z")
        for name in ("one", "two"):  # made upstream before the session, so dated before its start
            fixture.write(upstream, {f"src/{name}.py": "x = 1\n"})
            subprocess.run(["git", "-C", str(upstream), "add", "-A"], env=old, check=True)
            subprocess.run(["git", "-C", str(upstream), "commit", "-qm", f"upstream {name}"], env=old, check=True)
            if name == "one":
                self.start("s1")
                fixture.git(self.root, "pull", "-q", "--no-rebase", str(upstream), "main")
                self.assertEqual(self.stop("s1")[:2], (0, ""), "a pulled commit made before the start is no one's here")
                fixture.write(self.root, {"src/app.py": "print(2)\n"})
                fixture.commit_all(self.root, "an unrecorded change")
                before = fixture.git(self.root, "rev-parse", "--short", "HEAD")
        fixture.git(self.root, "pull", "-q", "--rebase", str(upstream), "main")
        after = fixture.git(self.root, "rev-parse", "--short", "HEAD")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: src/app.py (in commit ", err)
        self.assertIn(f"{after} of ", err, "the commit the rebase made here")
        self.assertNotIn(before, err, "not the one it replaced")
        self.assertNotIn("src/two.py", err)

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

    def peer_closes_docs(self):
        """A peer in a worktree of its own closes T-2, scoped docs/, and commits its file on its branch, side."""
        side = self.base / "side"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(side))
        self.start("peer", where=side)
        fixture.succeeds(fixture.installed_copy(side, "claim", "T-2", "--session", "peer"))
        fixture.write(side, {"docs/peer.md": "the peer's notes\n"})
        fixture.succeeds(fixture.installed_copy(side, "close", "T-2", "--evidence", "Wrote notes", "--session", "peer"))
        fixture.commit_all(side, "Peer notes")
        return f"\nnot counted: docs/peer.md: merged under T-2, closed by {sako.marker_for('peer')}\n"

    def test_a_task_closed_in_another_checkout_excuses_no_commit_made_here_before_or_after_a_merge_of_its_branch(self):
        """This session's own commit in the scope of a task a peer closed in its own worktree since the start counts,
        with no merge, after a fast-forward and after a merge commit, while the peer's merged file does not."""
        self.start("s1")
        fixture.started_ago(self.root, "s1")
        merged, base = self.peer_closes_docs(), fixture.git(self.root, "rev-parse", "HEAD")
        for merge in ("", "--ff-only", "--no-ff"):
            with self.subTest(merge=merge or "none"):
                fixture.git(self.root, "reset", "-q", "--hard", base)
                if merge:
                    fixture.git(self.root, "merge", "-q", "--no-edit", merge, "side")
                fixture.write(self.root, {"docs/mine.md": f"this session's own notes {merge}\n"})
                fixture.commit_all(self.root, "My notes")  # directly in this checkout, with no task
                code, _, err = self.stop("s1")
                self.assertEqual(code, 2, err)
                self.assertRegex(err, r'covered by no task you claimed or closed: docs/mine\.md \(in commit [0-9a-f]+ of '
                                      r'[\d-]+ "My notes"\)\n')
                self.assertEqual(merged in err, bool(merge), err)
                self.assertNotIn("a shared checkout", err)

    def test_a_merge_of_a_branch_whose_task_closed_since_the_start_counts_none_of_its_files_in_the_same_minute(self):
        self.start("s1")
        merged, base = self.peer_closes_docs(), fixture.git(self.root, "rev-parse", "HEAD")
        for merge in ("--ff-only", "--no-ff"):
            with self.subTest(merge=merge):
                fixture.git(self.root, "reset", "-q", "--hard", base)
                fixture.git(self.root, "merge", "-q", "--no-edit", merge, "side")
                self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1"),
                                 (0, f"SAKO gate: clear in {self.root.resolve().as_posix()}{merged}", ""))

    def test_amended_rebased_picked_and_reverted_commits_name_each_path_once_by_the_commit_that_holds_it(self):
        fixture.git(self.root, "checkout", "-qb", "base")  # made before the session, then rebased onto
        fixture.write(self.root, {"src/base.py": "x = 0\n"})
        env = dict(fixture.ENV, GIT_COMMITTER_DATE="2001-01-01T00:00:00Z", GIT_AUTHOR_DATE="2001-01-01T00:00:00Z")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], env=env, check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "old base"], env=env, check=True)
        fixture.git(self.root, "checkout", "-q", "main")
        self.start("s1")
        fixture.write(self.root, {"src/a.py": "a = 1\n"})
        replaced = [fixture.commit_all(self.root, "one")]
        fixture.git(self.root, "commit", "-q", "--amend", "-m", "one, amended")
        fixture.write(self.root, {"src/b.py": "b = 1\n"})
        replaced += [fixture.git(self.root, "rev-parse", "HEAD"), fixture.commit_all(self.root, "two")]
        fixture.git(self.root, "rebase", "-q", "base")
        fixture.git(self.root, "revert", "--no-edit", "HEAD")
        fixture.git(self.root, "checkout", "-qb", "extra", "HEAD~2")
        fixture.write(self.root, {"src/c.py": "c = 1\n"})
        replaced.append(fixture.commit_all(self.root, "three"))
        fixture.git(self.root, "checkout", "-q", "main")
        fixture.git(self.root, "cherry-pick", replaced[-1])
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        finding = err.split("covered by no task you claimed or closed: ")[1].split("\n")[0]
        self.assertEqual(re.findall(r"src/\w+\.py", finding), ["src/a.py", "src/b.py", "src/c.py"], finding)
        for commit in replaced:
            self.assertNotIn(commit[:7], err, "a commit no longer on this branch is not named")

    def test_where_the_reflog_does_not_reach_the_start_every_commit_counts_as_merged_and_the_gate_says_so(self):
        """An entry 0.8.0 wrote keeps no reflog entry and no done tasks, a reflog can expire, and reflogs can be off."""
        side = self.base / "side"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(side))
        self.start("peer", where=side)
        for args in (("claim", "T-2"), ("close", "T-2", "--evidence", "Outlined the docs")):
            fixture.succeeds(fixture.installed_copy(side, *args, "--session", "peer"))
        self.start("s1")  # T-2 is done at its start
        path = fixture.entry_path(self.root, "s1")
        entry = json.loads(path.read_text())
        fixture.write(self.root, {"docs/mine.md": "this session's own notes\n"})
        fixture.commit_all(self.root, "My notes")
        note = ("note: Git's reflog here does not reach your start, so your commits are judged as merged ones, which a task "
                "another session closed can excuse.\n")
        for way in ("an entry from 0.8.0", "an expired reflog", "reflogs turned off"):
            with self.subTest(way):
                if way == "an entry from 0.8.0":
                    path.write_text(json.dumps({k: v for k, v in entry.items() if k not in ("reflog_at_start",
                                                                                            "done_at_start")}))
                    code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
                    self.assertEqual(code, 0, out + err)
                    self.assertTrue(out.endswith(f"\nnot counted: docs/mine.md: merged under T-2, closed by "
                                                 f"{sako.marker_for('peer')}\n{note}"), "with no list of done tasks, "
                                    "every task another session closed excuses, so nothing is named wrongly")
                    path.write_text(json.dumps(entry))
                    continue
                if way == "an expired reflog":
                    fixture.git(self.root, "reflog", "expire", "--expire=now", "--all")
                else:
                    (self.root / ".git/logs/HEAD").unlink(missing_ok=True)
                    fixture.git(self.root, "config", "core.logAllRefUpdates", "false")
                    fixture.write(self.root, {"docs/later.md": "more notes\n"})
                    fixture.commit_all(self.root, "More notes")
                code, _, err = self.stop("s1")
                self.assertEqual(code, 2, err)
                self.assertRegex(err, r"covered by no task you claimed or closed: (docs/later\.md \(in commit [^)]+\), )?"
                                      r"docs/mine\.md \(in commit ", "T-2 was done at the start, so it excuses nothing")
                self.assertTrue(err.endswith(note), err)

    def test_the_start_record_keeps_the_newest_reflog_entry_and_the_tasks_then_done_through_a_resume(self):
        fixture.write(self.root, {".sako/work/DONE.md": fixture.done_file(
            fixture.row("T-5", status="done", claim="sk-0101-aaaaaaaaaaaa"))})
        newest = fixture.git(self.root, "log", "-g", "-1", "--format=%H %ct %gd %gs", "--date=unix", "HEAD")
        self.start("s1")
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.commit_all(self.root, "Print two")
        self.start("s1")  # a resume keeps the start record
        entry = sako.my_entry(sako.resolve_roots(self.root), "s1")
        self.assertEqual((entry["reflog_at_start"], entry["done_at_start"]), (newest, ["T-5"]))
        self.assertTrue(newest.endswith(" commit (initial): fixture"), newest)
        linked = self.base / "linked"
        fixture.git(self.root, "worktree", "add", "-q", str(linked))
        elsewhere = sako.my_entry(sako.resolve_roots(linked), "s1")
        self.assertFalse({"reflog_at_start", "done_at_start"} & set(elsewhere), "the record applies where it was taken")

    def test_renames_and_deletions_name_both_sides(self):
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Move the app", "--done-when", "Runs", "--scope", "src/main.py"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.git(self.root, "mv", "src/app.py", "src/main.py")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("src/app.py (deleted)", err)

    def test_a_checkouts_writer_answers_for_the_changes_in_it_and_a_claim_made_elsewhere_excuses_none(self):
        self.start("s1")
        self.start("peer")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "peer"))
        fixture.write(self.root, {"docs/x.md": "notes\n", "src/new.py": "x = 1\n"})
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: docs/x.md, src/new.py: this checkout's writer, {sako.marker_for('peer')} with T-2, "
                      "records the product changes here\nnote: a shared checkout shows what changed, not who changed it.\n",
                      out, "in the writer's scope or out of it, a change here is not the visitor's")
        code, _, err = self.stop("peer")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/new.py", err,
                      "the writer is asked about the change outside its claim")
        fixture.lapse(self.root, "peer")
        self.assertEqual(self.stop("s1")[0], 2, "without a live writer the change is unrecorded")
        linked = self.base / "linked"
        fixture.git(self.root, "worktree", "add", "-q", str(linked))
        self.start("s2", where=linked)
        self.start("peer2")
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Linked docs", "--done-when", "x", "--scope", "notes/"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "peer2"))
        fixture.write(linked, {"notes/y.md": "notes\n"})
        code, _, err = self.stop("s2", where=linked)
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: notes/y.md\n", err, "the writer of another checkout wrote none of this one")
        (linked / "notes/y.md").unlink()
        fixture.write(self.root, {"notes/z.md": "the peer's notes\n"})
        fixture.git(self.root, "add", "--", "notes/z.md")
        fixture.git(self.root, "commit", "-qm", "peer notes")
        fixture.git(linked, "merge", "-q", "main")
        code, _, err = self.stop("s2", where=linked)
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r'notes/z\.md \(in commit [0-9a-f]+ of [\d-]+ "peer notes"\)', "merged in before its task closed")
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Notes written", "--session", "peer2"))
        code, out, err = self.run_in(linked, "check", "--gate", session="s2")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: notes/z.md: merged under T-10, closed by {sako.marker_for('peer2')}\n", out,
                      "once its task closed, merged work is not this session's to record, in the minute it started too")
        self.assertNotIn("a shared checkout", out, "the commit came from another checkout")

    def test_a_session_is_a_writer_only_in_the_checkout_it_last_started_in(self):
        side = self.base / "side"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(side))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        self.start("s2", where=side)
        fixture.succeeds(fixture.installed_copy(side, "claim", "T-2", "--session", "s2"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        code, out, err = self.run_in(self.root, "check", "--gate", session="s2")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: src/app.py: this checkout's writer, {sako.marker_for('s1')} with T-1, records the product "
                      "changes here", out, "here, s2 holds a product claim but last started elsewhere, so it is a visitor")
        fixture.write(side, {"src/side.py": "x = 1\n"})
        code, _, err = self.run_in(side, "check", "--gate", session="s2")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/side.py\n", err,
                      "there, it is the writer, so it answers for a change outside its claim")

    def test_a_start_in_another_checkout_takes_a_new_start_record_there(self):
        """The second-writer refusal's way on, after the writer committed, so the new worktree's base holds that commit."""
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        self.start("s2")
        refusal = fixture.installed_copy(self.root, "claim", "T-2", "--session", "s2")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.commit_all(self.root, "App, first part")
        subprocess.run(shlex.split(re.search(r"\((git worktree add .*?),", refusal.stderr)[1]), cwd=self.root, env=fixture.ENV,
                       check=True, capture_output=True)
        side = self.base / "project-t-2"
        code, context, err = self.run_in(side, "start", session="s2")
        self.assertEqual(code, 0, context + err)
        self.assertIn(f" (t-2, started at {fixture.git(side, 'rev-parse', 'HEAD')[:9]})\n", context, "a start revision there")
        fixture.succeeds(fixture.installed_copy(side, "claim", "T-2", "--session", "s2"))
        fixture.write(side, {"docs/guide.md": "Usage\n"})
        code, out, err = self.run_in(side, "check", "--gate", session="s2")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("src/app.py", out + err, "the writer's commit, already in that base, is not this session's")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        code, context, err = self.run_in(self.root, "start", session="s2")  # back, which takes a new record again
        self.assertIn(f"(main, started at {fixture.git(self.root, 'rev-parse', 'HEAD')[:9]})\n", context)
        self.assertIn("this checkout has 2 writers, ", context, "and with its product claim it is a second writer here")
        self.assertIn(f"changed this session in {self.root.resolve().as_posix()}: 0\n",
                      self.run_in(self.root, "check", session="s2")[1], "files already dirty at the new start are not its")

    def test_a_product_claim_run_where_the_session_did_not_last_start_is_refused_with_the_way_on(self):
        """A claim makes its session a writer where it last started, so one run elsewhere would name the wrong checkout."""
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            fixture.row("T-1", touches="`src/`"), fixture.row("T-2", touches="`docs/`"),
            fixture.row("T-3", touches="`.sako/work/`"), fixture.row("T-4", touches="`lib/`"))})
        side = self.base / "side"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(side))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        self.start("s2")
        for where, last in ((side, self.root), (self.root, side)):
            with self.subTest(last=last.name):
                self.start("s2", where=last)
                refused = (f" would make this session a writer in {last.resolve().as_posix()}, where it last started, not "
                           "here. Run the command there, or run start here first (start --session s2)\n")
                refusal = fixture.installed_copy(where, "claim", "T-2", "--session", "s2")
                self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
                self.assertIn("T-2" + refused, refusal.stderr)
                fixture.succeeds(fixture.installed_copy(where, "claim", "T-3", "--session", "s2"))  # records make no writer
                grown = fixture.installed_copy(where, "edit", "T-3", "--widen", "docs/", "--session", "s2")
                self.assertEqual(grown.returncode, 3, grown.stdout + grown.stderr)
                self.assertIn("T-3" + refused, grown.stderr, "nor may a records claim grow into product files from there")
                fixture.succeeds(fixture.installed_copy(where, "edit", "T-3", "--open", "--session", "s2"))
                self.assertNotIn("writers", self.run_in(self.root, "check")[1], "so no second writer arises")
        fixture.succeeds(fixture.installed_copy(side, "claim", "T-2", "--session", "s2"))  # where it last started
        refusal = fixture.installed_copy(self.root, "claim", "T-4", "--session", "s2")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn(f"not here. Run the command there, where T-2 keeps it a writer\n", refusal.stderr,
                      "with a product claim it is not sent to start here, which would make a second writer beside s1")

    def test_a_close_run_where_the_session_did_not_last_start_seals_nothing_there(self):
        """Another session writes there, so a file of its own in the closed task's scope stays its own to record."""
        side = self.base / "side"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(side))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        self.start("s2", where=side)
        fixture.succeeds(fixture.installed_copy(side, "claim", "T-2", "--session", "s2"))
        fixture.write(side, {"src/util.py": "x = 1\n"})  # the writer there, outside its claim
        for args in (("verify",), ("close", "T-1", "--evidence", "Prints 1")):
            code, out, err = self.run_in(side, *args, session="s1")
            self.assertEqual(code, 0, out + err)
        sealed = [json.loads(path.read_text()) for path in (self.root / ".sako/state").rglob("seals.json")]
        self.assertEqual(sealed, [{}], "the close wrote that checkout's seals, with nothing in them")
        code, _, err = self.stop("s2", where=side)
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: src/util.py\n", err)

    def test_a_file_name_with_a_bracket_in_it_is_named_whole_and_still_blocks(self):
        """The gate carries the path itself, not the text it prints, which ends in a bracketed label."""
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app (v2).py": "print(2)\n"})
        for args in (("verify",), ("close", "T-1", "--evidence", "Prints 2")):
            code, out, err = self.run_in(self.root, *args, session="s1")
            self.assertEqual(code, 0, out + err)
        fixture.write(self.root, {"src/app (v2).py": "print(3)\n", "zeta (draft).txt": "later\n"})
        for code, _, err in (self.stop("s1"), self.run_in(self.root, "check", "--gate", session="s1")):
            self.assertEqual(code, 2, err)
            self.assertIn("covered by no task you claimed or closed: src/app (v2).py (changed after you closed T-1), "
                          "zeta (draft).txt\n", err)
            self.assertIn("(git add -- 'src/app (v2).py', then git commit -m 'Invented task'); for new work: ", err)
            self.assertIn("--scope 'zeta (draft).txt', then claim it", err,
                          "the fix for new work names a file no closed task covers")

    def test_a_committed_file_name_that_git_quotes_is_covered_by_its_scope_and_named_whole(self):
        """Git quotes a name with a double quote, a backslash or a control character unless asked for raw names (-z),
        and core.quotePath=false stops that only for bytes past ASCII, such as an accented letter. Run in process, since
        Windows reads a child's output back in its code page."""
        names = ["café.py", "a b.py"] + ([] if fixture.WINDOWS else ['q"t.py', "back\\slash.py", "tab\tx.py", "zz "])
        gate = lambda: sako.gate(sako.resolve_roots(self.root), sako.load_config(self.root / ".sako"), "s1")
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {f"src/{name}": "x = 1\n" for name in names})
        fixture.commit_all(self.root, "Odd names")
        self.assertEqual(self.stop("s1")[:2], (0, ""), "each committed name is in T-1's scope")
        notes = sorted(f"notes/{name}" for name in names)
        fixture.write(self.root, {path: "notes\n" for path in notes})
        code, err = gate()
        self.assertEqual(code, 2, err)
        self.assertIn(f"covered by no task you claimed or closed: {', '.join(notes)}\n", err)
        self.assertIn(f"New files among them ({', '.join(notes[:4])}): delete or ignore", err)
        fixture.commit_all(self.root, "Odd notes")
        short, day = sako.git(self.root, "log", "-1", "--format=%h %ad", "--date=short-local").split()  # as the gate reads it
        code, err = gate()
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: "
                      + ", ".join(f'{path} (in commit {short} of {day} "Odd notes")' for path in notes) + "\n", err,
                      "no task covers them, so each is named as it is")

    def test_the_check_and_proven_read_whole_a_name_that_git_quotes_or_that_starts_with_a_space(self):
        """A listing asked for with -z is kept whole. Trimmed, a name that starts with a space fell out of the check, and a
        merged file outside the claims still held whose name Git quotes or trims was named wrongly under Proven."""
        odd = [" notes.md"] + ([] if fixture.WINDOWS else ['q"t.md'])
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"]}),
                                  **{name: "draft\n" for name in odd}})
        fixture.commit_all(self.root, "Notes")
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.write(self.root, {" notes.md": "edited\n"})
        self.assertIn("evidence: stale", self.run_in(self.root, "check")[1], "the check covers the name with its space")
        fixture.git(self.root, "checkout", "-qb", "side")  # made before the session, so dated before its start
        fixture.write(self.root, {name: "edited\n" for name in odd})
        env = dict(fixture.ENV, GIT_COMMITTER_DATE="2001-01-01T00:00:00Z", GIT_AUTHOR_DATE="2001-01-01T00:00:00Z")
        subprocess.run(["git", "-C", str(self.root), "commit", "-qam", "Edited notes"], env=env, check=True)
        fixture.git(self.root, "checkout", "-q", "main")
        self.start("s1")
        for args in (("claim", "T-2"), ("close", "T-2", "--evidence", "Notes"), ("claim", "T-1")):
            fixture.succeeds(fixture.installed_copy(self.root, *args, "--session", "s1"))
        fixture.git(self.root, "merge", "-q", "--ff-only", "side")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(f"Proven: T-2 closed after checked content changed since you started, in {', '.join(odd)} outside "
                      "the claims you still hold, and the check is stale", err)

    def test_where_a_session_has_no_start_record_only_uncommitted_checked_content_counts_as_moved(self):
        """A session's hooks can run in a checkout it did not last start in, where its start revision does not apply."""
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            fixture.row("T-1", touches="`.sako/work/`"), fixture.row("T-2", touches="`.sako/work/`"))})
        linked = self.base / "linked"
        fixture.git(self.root, "worktree", "add", "-q", str(linked))
        self.start("s1")
        for task in ("T-1", "T-2"):
            fixture.succeeds(fixture.installed_copy(self.root, "claim", task, "--session", "s1"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Planned", "--session", "s1"))
        self.assertEqual(self.stop("s1", where=linked), (0, "", ""), "no checked content is uncommitted there, so nothing moved")
        closing = fixture.installed_copy(linked, "close", "T-2", "--evidence", "Planned", "--session", "s1")
        self.assertEqual(closing.returncode, 0, closing.stdout + closing.stderr)
        self.assertIn("no automated check (records only)", (self.root / ".sako/work/DONE.md").read_text())
        fixture.write(linked, {"src/app.py": "print(2)\n"})
        code, _, err = self.stop("s1", where=linked)
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-1, T-2 closed after checked content changed since you started, and the check is none", err,
                      "an uncommitted change to checked content there still asks for a pass")

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs a file its owner cannot read")
    def test_a_close_reads_only_its_own_files_and_those_sealed_before(self):
        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(
            fixture.row("T-1", touches="`src/`"), fixture.row("T-2", touches="`.sako/work/`"))})
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s1"))
        fixture.write(self.root, {"docs/draft.md": "another writer's\n"})
        (self.root / "docs/draft.md").chmod(0)
        closing = fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Planned", "--session", "s1")
        (self.root / "docs/draft.md").chmod(0o644)
        self.assertEqual(closing.returncode, 0, closing.stdout + closing.stderr)
        self.assertEqual(self.seals(), {"T-2": {}}, "a records task seals no product file, and its close is named")

    def test_two_writers_in_one_checkout_are_named_however_they_arose(self):
        finding = (f"this checkout has 2 writers, {sako.marker_for('s1')} with T-1 and {sako.marker_for('s2')} with T-2, but a "
                   "checkout has one; one of them parks, opens or closes its product claims, or commits and moves to a "
                   "worktree of its own")
        for way in ("an end and a resume", "a lapsed lease", "a hand edit"):
            with self.subTest(way):
                fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(fixture.row("T-1"),
                                                                                fixture.row("T-2", touches="`docs/`"))})
                self.start("s1")
                self.start("s2")
                if way == "a hand edit":  # as two disjoint claims an update from 0.7.1 can bring into one checkout
                    fixture.claim(self.root, "T-1", "s1")
                    fixture.claim(self.root, "T-2", "s2", touches="`docs/`")
                else:
                    fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
                    if way == "an end and a resume":
                        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s1"))
                    else:
                        fixture.lapse(self.root, "s1")
                    fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s2"))
                    self.assertIn(f"  {finding}\n", self.run_in(self.root, "start", session="s1")[1], "the start context")
                fixture.write(self.root, {"src/app.py": "print(2)\n", "docs/x.md": "notes\n"})
                for session in ("s1", "s2"):
                    code, _, err = self.stop(session)
                    self.assertEqual(code, 2, err)
                    self.assertIn(f"SAKO gate: 2 problem(s) before you stop:\n  Ledger: {finding}\n  Recorded: ", err,
                                  "it blocks each writer's stop and comes before the other's files under Recorded")
                self.assertIn(f"  {finding}\n", self.run_in(self.root, "check")[1])
                self.start("s3")
                code, out, err = self.run_in(self.root, "check", "--gate", session="s3")
                self.assertIn(f"\nnote: {finding}\n", out + err, "a note for a session that is neither")
                fixture.succeeds(fixture.installed_copy(self.root, "edit", "T-2", "--park", "moves to a worktree",
                                                        "--session", "s2"))
                self.assertNotIn("writers", self.run_in(self.root, "check")[1], "one writer is left")
                for session in ("s1", "s2", "s3"):
                    fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", session))
                fixture.git(self.root, "checkout", "--", "src/app.py")
                (self.root / "docs/x.md").unlink()

    def test_not_counted_notes_name_three_paths_then_how_many_more_under_the_verdict_with_one_caveat_after_them(self):
        self.start("s1")
        self.start("peer")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "peer"))
        fixture.write(self.root, {f"docs/{name}.md": "notes\n" for name in "abcde"})
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        paths, caveat = "not counted: docs/a.md, docs/b.md, docs/c.md and 2 more: ", \
            "\nnote: a shared checkout shows what changed, not who changed it.\n"
        notes = f"{paths}this checkout's writer, {sako.marker_for('peer')} with T-2, records the product changes here{caveat}"
        self.assertEqual(out, f"SAKO gate: clear in {self.root.resolve().as_posix()}\n{notes}", "the caveat once, after the last note")
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Wrote notes", "--session", "peer"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("SAKO gate: 1 problem(s) before you stop:\n  Recorded:"), "the verdict comes first")
        self.assertTrue(err.endswith(f"\n{paths}in T-2's scope, closed by {sako.marker_for('peer')}{caveat}"), err)

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
        self.assertEqual(self.run_in(self.root, "claim", "T-1", session="s1")[0], 0)  # where it started, as claim requires
        fixture.write(linked, {"src/app.py": "print(2)\n"})  # work done by path in the sibling; the session's hooks run here
        for args in (("verify",), ("close", "T-1", "--evidence", "Prints 2")):
            code, out, err = self.run_in(linked, *args, session="s1")
            self.assertEqual(code, 0, out + err)
        here, there = self.root.resolve().as_posix(), linked.resolve().as_posix()
        self.assertIn(f"changed this session in {here}: 0", self.run_in(self.root, "check", session="s1")[1])
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1"), (0, f"SAKO gate: clear in {here}\n", ""))
        self.assertEqual(self.stop("s1"), (0, "", ""), "the gate judges the checkout it runs in, and a clear stop is silent")
        elsewhere = (f"no start record here, since you last started in {here}, so every uncommitted file here counts "
                     "and commits were not checked")
        self.assertIn(f"changed this session in {there}: 1; unrecorded: src/app.py ({elsewhere})",
                      self.run_in(linked, "check", session="s1")[1])
        code, _, err = self.run_in(linked, "check", "--gate", session="s1")
        self.assertEqual(code, 2, err)
        self.assertIn(f"SAKO gate: 1 problem(s) before you stop:\n  Recorded: {elsewhere}: uncommitted and covered by no task "
                      "you claimed or closed: src/app.py\n    fix: if the content is still T-1's work, commit it under that "
                      "task once commits are allowed (git add -- src/app.py, then git commit -m 'Invented task'); ", err,
                      "a close seals nothing where its session did not last start, so the file counts until it is committed")
        fixture.commit_all(linked, "Invented task")
        self.assertEqual(self.run_in(linked, "check", "--gate", session="s1"),
                         (0, f"SAKO gate: clear in {there}\nnote: {elsewhere}.\n", ""),
                         "the start record of the main checkout does not apply there, and the gate says what it does instead")

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

    def test_named_checks_are_evidence_and_never_a_gate_rule(self):
        checks = {"check": [sys.executable, "-c", "pass"], "browser": [sys.executable, "-c", "raise SystemExit(1)"]}
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"],
                                                                   "verify_paths": ["src"], "checks": checks})})
        self.start("s1")
        for args in (("claim", "T-2"), ("verify", "check"), ("close", "T-2", "--evidence", "Wrote notes"), ("claim", "T-1")):
            fixture.succeeds(fixture.installed_copy(self.root, *args, "--session", "s1"))
        self.assertIn("; no passing check recorded; check pass ", (self.root / ".sako/work/DONE.md").read_text())
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-2 closed after checked content changed since you started, and the check is none", err,
                      "a named check's pass in a receipt never stands in for the gate's check, even one named check")
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.assertEqual(fixture.installed_copy(self.root, "verify", "browser").returncode, 1)
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        fixture.commit_all(self.root, "Invented task")
        status = self.run_in(self.root, "status")[1]
        self.assertIn("\ncheck check: stale; checked content moved since the last pass", status)
        self.assertIn("\ncheck browser: none; no passing check recorded", status)
        self.assertEqual(self.stop("s1"), (0, "", ""), "a stale, failing or missing named check never blocks a stop")

    def test_a_commit_recorded_under_another_sessions_closed_task_is_not_this_sessions(self):
        self.start("s1")
        self.start("s2")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s2"))
        fixture.write(self.root, {"docs/x.md": "notes\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Wrote notes", "--session", "s2"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: docs/x.md: in T-2's scope, closed by {sako.marker_for('s2')}\n", out,
                      "closed since this session started, before it was committed")
        fixture.commit_all(self.root, "T-2: notes")
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s2"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertRegex(out, rf"(?m)not counted: docs/x\.md: committed under T-2, closed by {sako.marker_for('s2')}(;|$)",
                         "the closer's marker alone, without its claim time")

    def test_only_a_task_another_session_closed_since_this_one_started_covers_a_commit(self):
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
        self.assertIn("not counted: src/app.py: committed under T-1, closed by sk-", out, "closed since this session started")

    def test_a_task_done_at_the_start_excuses_no_merged_commit_and_one_closed_in_the_minute_of_the_start_does(self):
        """The start record names the tasks then done, so no clock decides whether a close came since the start."""
        side = self.base / "side"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(side))
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Docs c", "--done-when", "x", "--scope", "docs/c.md"))
        self.start("peer", where=side)
        for args in (("claim", "T-2"), ("close", "T-2", "--evidence", "Outlined the docs")):
            fixture.succeeds(fixture.installed_copy(side, *args, "--session", "peer"))
        self.start("s1")
        fixture.write(side, {"docs/b.md": "more docs, with no task\n"})
        fixture.commit_all(side, "More docs")
        for args in (("claim", "T-10"), ("close", "T-10", "--evidence", "Wrote c")):
            if args[0] == "close":
                fixture.write(side, {"docs/c.md": "c\n"})
            fixture.succeeds(fixture.installed_copy(side, *args, "--session", "peer"))
        fixture.commit_all(side, "Docs c")
        fixture.git(self.root, "merge", "-q", "--ff-only", "side")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn('covered by no task you claimed or closed: docs/b.md (in commit ', err,
                      "T-2 was done at the start, so it excuses nothing made since, even in its scope")
        self.assertIn(f"\nnot counted: docs/c.md: merged under T-10, closed by {sako.marker_for('peer')}\n", err,
                      "T-10 closed since the start, here within its first minute")
        self.assertEqual(sako.my_entry(sako.resolve_roots(self.root), "s1")["done_at_start"], ["T-2"])

    def test_a_task_closed_just_before_a_new_session_started_excuses_none_of_its_commits(self):
        """The everyday case: a close, a new session within the same minute, then that session's own commits."""
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        fixture.commit_all(self.root, "Invented task")
        self.start("s2")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        fixture.commit_all(self.root, "work with no task")
        code, _, err = self.stop("s2")
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r'covered by no task you claimed or closed: src/app\.py \(in commit [0-9a-f]+ of [\d-]+ "work with '
                              r'no task"\)')

    def test_a_live_writer_is_named_before_a_closed_task_that_also_covers_the_commit(self):
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
        self.assertIn(f"not counted: docs/x.md: this checkout's writer, {sako.marker_for('s2')} with T-10, ", out)
        self.assertNotIn("committed under T-2", out)
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Notes read back", "--session", "s2"))
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s2"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: docs/x.md: committed under T-10, closed by sk-", out, "of two closes that day, the later")

    def test_an_ended_peers_closed_edit_is_not_this_sessions_until_a_close_scoped_dot_takes_it_in(self):
        """A documented limit (docs/limits.md). A task scoped . covers every file, so its close counts a peer's closed,
        uncommitted edit as this session's closed work."""
        self.start("s1")
        self.start("peer")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "peer"))
        fixture.write(self.root, {"docs/draft.md": "the peer's draft\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Drafted", "--session", "peer"))
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "peer"))  # it ends with its edit uncommitted
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: docs/draft.md: in T-2's scope, closed by {sako.marker_for('peer')}", out,
                      "closed since this session started, so it is the closer's to commit")
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
        self.assertEqual({task: set(files) for task, files in self.seals().items()}, {"T-1": {"src/app.py"}},
                         "a close seals this session's changes, not a file already uncommitted when it started")
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
        fixture.write(self.root, {"src/wip.py": "someone else's draft, now edited\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/wip.py "
                      "(edited after start; already dirty at start)\n", err, "an edit after the close is not the closed task's")
        self.assertNotIn("Committed:", err)

    def test_a_task_closed_before_a_session_started_excuses_no_edit_it_makes_in_that_scope(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        fixture.git(self.root, "commit", "-qam", "Invented task")
        self.start("s2")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})  # with no task, in the scope of T-1, which s1 closed earlier
        unrecorded = "Recorded: changed this session but covered by no task you claimed or closed: src/app.py "
        add = f'{sako.PYTHON} .sako/sako.py add "WHAT CHANGED" --done-when "HOW TO CHECK IT" --scope src/app.py, '
        fix = f"\n    fix: {add}"
        code, _, err = self.stop("s2")
        self.assertEqual(code, 2, err)
        self.assertIn(f"{unrecorded.rstrip()}{fix}", err, "another session's closed task excuses only what its close sealed, "
                                                          "and T-1 was done at this session's start")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        late = ("\n    fix: if the content is still T-1's work, commit it under that task once commits are allowed (git add -- "
                f"src/app.py, then git commit -m 'Invented task'); for new work: {add}")
        self.assertIn(f"{unrecorded}(changed after you closed T-1){late}", err, "the closer sees what changed, not who "
                      "changed it, so it is offered the commit under its closed task first, then the record of new work")
        self.assertNotIn("Committed:", err, "content other than what the close sealed is not the closed task's to commit")
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
        fixture.succeeds(fixture.installed_copy(self.root, "add", "Print three", "--done-when", "Prints 3", "--scope", "src/"))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Prints 3", "--session", "s1"))
        self.assertEqual({task: set(files) for task, files in self.seals().items()}, {"T-1": set(), "T-10": {"src/app.py"}},
                         "a close drops the seals whose files were committed or changed since, and keeps each close named")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-10 is closed but its changes are not committed: "
                      "src/app.py\n    fix: git add -- src/app.py, then git commit -m 'Print three'", err)
        self.assertNotIn("T-1 ", err, "T-1's work is in its commit; the change came after it closed")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})  # an editor saves the same bytes again
        self.assertEqual(self.stop("s1"), (code, "", err), "of two closes, the later holds what the file has")

    def test_a_stash_and_pop_that_restores_the_same_bytes_leaves_the_committed_finding_and_adds_none(self):
        """A stash and its pop, as a pre-commit hook or a pull with autostash makes them, write a closed task's uncommitted
        files again with the bytes they held, so the gate says what it said before."""
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n", "src/lib.py": "x = 2\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        before = self.stop("s1")
        self.assertEqual(before[0], 2, before)
        self.assertTrue(before[2].startswith("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-1 is closed but its "
                                             "changes are not committed: src/app.py, src/lib.py\n"), before)
        fixture.git(self.root, "stash", "-q", "-u")  # the work set aside and put back
        fixture.git(self.root, "stash", "pop", "-q")
        self.assertEqual(self.stop("s1"), before, "no Recorded finding, and the same bytes keep the check fresh")
        fixture.git(self.root, "add", "--", "src/app.py", "src/lib.py")
        fixture.git(self.root, "commit", "-qm", "Invented task")
        self.assertEqual(self.stop("s1")[:2], (0, ""), "the commit lands the closed work under its own title")

    def test_a_task_another_session_closed_excuses_a_file_only_while_it_holds_what_the_close_sealed(self):
        self.start("s1")
        self.start("s2")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s2"))
        fixture.write(self.root, {"docs/x.md": "notes\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Wrote notes", "--session", "s2"))
        fixture.write(self.root, {"docs/x.md": "notes\n"})  # an editor saves the same bytes
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: docs/x.md: in T-2's scope, closed by {sako.marker_for('s2')}\n", out)
        self.assertIn("Committed: T-2 is closed but its changes are not committed: docs/x.md", self.stop("s2")[2])
        fixture.write(self.root, {"docs/x.md": "notes, edited after the close\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(f'covered by no task you claimed or closed: docs/x.md\n    fix: {sako.PYTHON} .sako/sako.py add "WHAT CHANGED"', err,
                      "another session's close excuses only the content it sealed")
        self.assertIn("; for a change that is not yours or stays unrecorded, say which and why in your reply.", err)
        code, _, err = self.stop("s2")
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: docs/x.md (changed after you closed T-2)\n    fix: ", err)
        self.assertNotIn("Committed:", err, "its closer is not asked to commit content the close did not seal")

    def test_a_task_closed_in_another_checkout_excuses_no_uncommitted_file_here(self):
        self.start("s1")
        linked = self.base / "linked"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(linked))
        for session in ("s2", "s3"):
            self.start(session, where=linked)
        fixture.succeeds(fixture.installed_copy(linked, "claim", "T-2", "--session", "s2"))
        for checkout in (linked, self.root):  # the same bytes in both checkouts
            fixture.write(checkout, {"docs/x.md": "notes\n"})
        fixture.succeeds(fixture.installed_copy(linked, "close", "T-2", "--evidence", "Wrote notes", "--session", "s2"))
        code, out, err = self.run_in(linked, "check", "--gate", session="s3")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: docs/x.md: in T-2's scope, closed by {sako.marker_for('s2')}\n", out,
                      "for a session that started there, the close excuses the file it sealed there")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: docs/x.md\n", err,
                      "the close sealed the file in its own checkout, not this one")

    def test_a_sealed_deletion_stays_its_tasks_until_the_path_holds_a_file_again(self):
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        (self.root / "src/app.py").unlink()
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Removed the app", "--session", "s1"))
        self.assertEqual(self.seals(), {"T-1": {"src/app.py": "absent"}})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-1 is closed but its changes "
                                       "are not committed: src/app.py\n"), err)
        fixture.write(self.root, {"src/app.py": "print(9)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: src/app.py (changed after you closed T-1)\n", err)
        self.assertNotIn("Committed:", err)

    def test_a_file_turned_into_a_package_keeps_its_seal_and_its_start_record_through_later_commits(self):
        """A plain folder in the place of a file is fingerprinted as a folder, never by the repository's HEAD."""
        fixture.write(self.root, {"src/tool": "print(1)\n"})
        fixture.commit_all(self.root, "A tool")
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        (self.root / "src/tool").unlink()  # the module becomes a package of the same name
        fixture.write(self.root, {"src/tool/__init__.py": "print(1)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "A package", "--session", "s1"))
        self.assertEqual(set(self.seals()["T-1"]), {"src/tool", "src/tool/__init__.py"})
        for task, path in (("T-2", "docs/x.md"), ("T-10", "notes/y.md")):
            if task == "T-10":
                self.start("s2")  # with the package uncommitted at its start
                fixture.succeeds(fixture.installed_copy(self.root, "add", "Notes", "--done-when", "x", "--scope", "notes/"))
            session = "s1" if task == "T-2" else "s2"
            fixture.succeeds(fixture.installed_copy(self.root, "claim", task, "--session", session))
            fixture.write(self.root, {path: "notes\n"})
            fixture.succeeds(fixture.installed_copy(self.root, "close", task, "--evidence", "Notes", "--session", session))
            fixture.git(self.root, "add", "--", path)
            fixture.git(self.root, "commit", "-qm", "Notes")  # HEAD moves
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-1 is closed but its changes "
                                       "are not committed: src/tool, src/tool/__init__.py\n"), err)
        self.assertEqual(self.stop("s2")[:2], (0, ""), "nor did the folder change since the second session's start")
        fixture.commit_all(self.root, "A package")
        self.assertEqual(self.stop("s1")[:2], (0, ""))

    def test_a_file_stashed_while_another_task_closes_loses_its_seal(self):
        """A documented limit (docs/limits.md). A close keeps an earlier seal only while its file stays uncommitted with
        the sealed bytes, so the seals stay as many as the uncommitted files."""
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        fixture.git(self.root, "stash", "-q")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s1"))
        fixture.write(self.root, {"docs/x.md": "notes\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Wrote notes", "--session", "s1"))
        self.assertEqual({task: set(files) for task, files in self.seals().items()}, {"T-1": set(), "T-2": {"docs/x.md"}})
        fixture.git(self.root, "stash", "pop", "-q")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/app.py\n", err)

    def test_a_task_closed_with_no_seal_here_covers_its_uncommitted_files_once_they_are_committed(self):
        """A close by hand or by an earlier release, or state deleted since, leaves no seal here."""
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        (self.root / ".sako/state/seals.json").unlink()
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: src/app.py\n    fix: if the "
                      "content is still T-1's work, commit it under that task once commits are allowed (git add -- src/app.py, "
                      f"then git commit -m 'Invented task'); for new work: {sako.PYTHON} .sako/sako.py add ", err,
                      "no seal, so no label that claims the file changed, and the commit the limits page names comes first")
        self.assertNotIn("Committed:", err)
        fixture.git(self.root, "add", "--", "src/app.py")
        fixture.git(self.root, "commit", "-qm", "Invented task")
        self.assertEqual(self.stop("s1")[:2], (0, ""), "a committed file is the closed task's by its scope")

    def test_an_uncommitted_file_rewritten_after_your_close_is_offered_its_commit_under_that_task_first(self):
        """A formatter or a Git hook can rewrite a closed task's file after its close."""
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print( 2 )\n", "src/lib.py": "x = 2\n", "notes.txt": "a draft\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n", "src/lib.py": "x = 2  # formatted\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("covered by no task you claimed or closed: notes.txt, src/app.py (changed after you closed T-1), src/lib.py "
                      "(changed after you closed T-1)\n    fix: if the content is still T-1's work, commit it under that task "
                      "once commits are allowed (git add -- src/app.py src/lib.py, then git commit -m 'Invented task'); for new "
                      f"work: {sako.PYTHON} .sako/sako.py add \"WHAT CHANGED\" --done-when \"HOW TO CHECK IT\" --scope notes.txt, "
                      "then claim it; for a change that is not yours", err, "only the files in the closed task's scope")

    def test_covers_reads_the_scope_first_then_for_an_uncommitted_file_a_done_rows_seal(self):
        row = sako.parse_rows({"done": fixture.done_file("| T-5 | Notes | Written | Wrote it [sk-0101-aaaaaaaaaaaa; closed "
                                                         "2026-01-01T09:00+0000; no automated check] | `docs/` | - |")})[0]
        task = sako.parse_rows({"tasks": fixture.ledger(fixture.row("T-6", touches="`docs/`"))})[0]
        self.assertTrue(sako.covers(row, "docs/x.md"), "without seals, as for a committed file, the scope alone decides")
        self.assertFalse(sako.covers(row, "src/app.py"))
        self.assertFalse(sako.covers(row, "src/app.py", {"T-5": {"src/app.py"}}), "the scope comes first")
        self.assertTrue(sako.covers(row, "docs/x.md", {"T-5": {"docs/x.md"}}), "the file holds what the close sealed")
        self.assertFalse(sako.covers(row, "docs/x.md", {"T-5": {"docs/y.md"}, "T-6": {"docs/x.md"}}),
                         "it holds other bytes, or the close sealed no such file")
        self.assertTrue(sako.covers(task, "docs/x.md", {}), "an open or claimed row covers by its scope alone")

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
        fixture.lapse(self.root, "s1")
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

    def test_a_records_only_task_needs_no_check_while_this_checkouts_writer_edits_under_its_claim(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        self.start("peer")
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
        self.assertIn(f"not counted: src/app.py: this checkout's writer, {sako.marker_for('peer')} with T-1, ", out)
        fixture.lapse(self.root, "peer")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        refusal = fixture.installed_copy(self.root, "close", "T-11", "--evidence", "Written", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr, "without a live writer the change counts as this session's")

    def test_a_records_only_task_needs_no_check_while_this_checkouts_writer_commits_in_or_out_of_its_claim(self):
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
        writer = f"this checkout's writer, {sako.marker_for('peer')} with T-10, records the product changes here"
        self.assertIn(f"not counted: src/app.py: {writer}", out, "and after it")
        fixture.write(self.root, {"src/other.py": "x = 1\n"})
        fixture.commit_all(self.root, "Work outside the peer's claim")
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: src/app.py, src/other.py: {writer}", out, "out of the writer's claim, still not the visitor's")
        code, _, err = self.stop("peer")
        self.assertEqual(code, 2, err)
        self.assertRegex(err, r"Recorded: changed this session but covered by no task you claimed or closed: src/other\.py \(in commit")
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "peer"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-11 closed after checked content changed since you started", err,
                      "with no live writer here, a commit since the start is this session's")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-12", "--session", "s1"))
        refusal = fixture.installed_copy(self.root, "close", "T-12", "--evidence", "Written", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr)

    def test_a_close_with_a_pass_stays_proven_while_this_checkouts_writer_moves_checked_content(self):
        for task, path in (("Print two", "src/app.py"), ("Peer library", "src/lib.py")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Runs", "--scope", path))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Prints 2", "--session", "s1"))
        self.assertIn("; check pass ", (self.root / ".sako/work/DONE.md").read_text())
        fixture.commit_all(self.root, "Print two")
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s1"))
        self.start("s1")  # a resume after its end takes a new start record, after its own work landed
        self.start("peer")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "peer"))
        fixture.write(self.root, {"src/lib.py": "x = 1\n"})
        self.assertIn("evidence: stale", self.run_in(self.root, "check")[1])
        self.assertEqual(self.stop("s1")[:2], (0, ""), "only this checkout's writer moved checked content")
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"not counted: src/lib.py: this checkout's writer, {sako.marker_for('peer')} with T-11, ", out)
        proven = "Proven: T-10 closed after checked content changed since you started, and the check is stale"
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "peer"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(proven, err, "with no live writer, the content here is this session's to prove")
        (self.root / "src/lib.py").unlink()
        self.assertEqual(self.stop("s1")[:2], (0, ""), "the content is the checked content again")
        fixture.write(self.root, {"src/extra.py": "y = 2\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(proven, err, "the session's own checked edit leaves its close unproven")
        (self.root / "src/extra.py").unlink()
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("src/app.py (changed after you closed T-10)", err)
        self.assertIn(proven, err, "an edit to the closed task's file after its close, with no new claim")

    def merge_two_closes(self):
        """T-1 and T-2 each pass the check and close on a branch of their own, and this session merges both. Then it
        claims T-3 and edits its file. The check fails only where both changes meet."""
        fixture.write(self.root, {
            ".sako/work/TASKS.md": fixture.ledger(*(fixture.row(task, touches=f"`src/{name}.py`")
                                                   for task, name in (("T-1", "a"), ("T-2", "b"), ("T-3", "c")))),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src"]}),
            "check.py": "import pathlib, sys\nsys.exit(all('new' in pathlib.Path(f'src/{n}.py').read_text() for n in 'ab'))\n",
            "src/a.py": "old\n", "src/b.py": "old\n", "src/c.py": "old\n"})
        fixture.commit_all(self.root, "Three modules and a check")
        self.start("s1")
        for task, name in (("T-1", "a"), ("T-2", "b")):
            fixture.git(self.root, "checkout", "-qb", task.lower(), "main")
            fixture.succeeds(fixture.installed_copy(self.root, "claim", task, "--session", "s1"))
            fixture.write(self.root, {f"src/{name}.py": "new\n"})
            fixture.succeeds(fixture.installed_copy(self.root, "verify"))
            fixture.succeeds(fixture.installed_copy(self.root, "close", task, "--evidence", "Passes alone", "--session", "s1"))
            fixture.commit_all(self.root, task)
        fixture.git(self.root, "checkout", "-q", "main")
        for branch in ("t-1", "t-2"):
            fixture.git(self.root, "merge", "-q", "--no-edit", branch)
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-3", "--session", "s1"))
        fixture.write(self.root, {"src/c.py": "new\n"})

    def test_two_closes_that_passed_apart_merged_beside_a_claim_still_held_block_when_the_check_fails(self):
        self.merge_two_closes()
        code, out, _ = self.run_in(self.root, "verify")
        self.assertEqual(code, 1, out)
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-1, T-2 closed after checked content changed since you started, in src/a.py outside the "
                      "claims you still hold, and the check is none: no passing check recorded", err,
                      "the merge brought T-1's file to T-2's last pass; the held claim explains only its own file")
        self.assertIn(f"\n    fix: {sako.PYTHON} .sako/sako.py verify, fix what it reports, then stop again.", err)

    def test_two_closes_that_passed_apart_merged_beside_a_claim_still_held_block_before_any_check(self):
        self.merge_two_closes()
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-1, T-2 closed after checked content changed since you started, in src/a.py outside the "
                      "claims you still hold, and the check is stale: checked content moved since the last pass", err)
        self.assertNotIn("Recorded:", err, "each merged file is its closed task's")

    def test_an_edit_or_a_commit_outside_the_claims_still_held_after_a_close_needs_a_fresh_check(self):
        for task, path in (("Print two", "src/app.py"), ("A library", "src/lib.py")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Runs", "--scope", path))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Prints 2", "--session", "s1"))
        fixture.commit_all(self.root, "Print two")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        fixture.write(self.root, {"src/lib.py": "x = 1\n"})
        self.assertEqual(self.stop("s1")[:2], (0, ""), "the claim still held explains its own file")
        proven = ("Proven: T-10 closed after checked content changed since you started, in src/app.py outside the claims "
                  "you still hold, and the check is stale")
        fixture.write(self.root, {"src/app.py": "print(3)\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(proven, err, "an edit outside the claim")
        fixture.git(self.root, "commit", "-qm", "Print three", "--", "src/app.py")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertNotIn("Recorded:", err, "the commit lies in the closed task's scope, so only Proven sees it")
        self.assertIn(proven, err, "a commit outside the claim")
        fixture.write(self.root, {f"src/{name}.py": "y = 2\n" for name in "xyz"})
        self.assertIn("since you started, in src/app.py, src/x.py, src/y.py and 1 more outside the claims you still hold, "
                      "and the check is stale", self.stop("s1")[2], "three files, then how many more")

    def test_a_claim_still_held_explains_its_own_files_through_a_failing_check(self):
        fixture.write(self.root, {"control": "pass\n", ".gitignore": "__pycache__/\ncontrol\n", "check.py": (
            "import pathlib, sys\nsys.exit(pathlib.Path('control').read_text() != 'pass\\n')\n"),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src"]})})
        fixture.commit_all(self.root, "A check that an ignored file steers")
        for task, path in (("Print two", "src/app.py"), ("A library", "src/lib.py")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Runs", "--scope", path))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.assertTrue((self.root / ".sako/state/passed.json").exists(), "verify keeps the record, before any close")
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Prints 2", "--session", "s1"))
        fixture.commit_all(self.root, "Print two")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        fixture.write(self.root, {"src/lib.py": "x = 1\n"})
        self.assertEqual(self.stop("s1")[:2], (0, ""), "pausing mid change is free")
        fixture.write(self.root, {"control": "fail\n"})
        self.assertEqual(self.run_in(self.root, "verify")[0], 1)
        state = self.root / ".sako/state"
        self.assertFalse((state / "stamp.json").exists(), "a failing run leaves no pass")
        self.assertTrue((state / "passed.json").exists(), "but keeps what each checked file held at the last pass")
        self.assertEqual(self.stop("s1")[:2], (0, ""), "so the claim still held explains its file through the failing run")

    def test_a_failing_run_on_unchanged_content_blocks_a_close_while_no_claim_still_held_covers_a_moved_file(self):
        fixture.write(self.root, {"control": "pass\n", ".gitignore": "__pycache__/\ncontrol\n", "check.py": (
            "import pathlib, sys\nsys.exit(pathlib.Path('control').read_text() != 'pass\\n')\n"),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src"]})})
        fixture.commit_all(self.root, "A check that an ignored file steers")
        for task, path in (("Print two", "src/app.py"), ("A library", "src/lib.py")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Runs", "--scope", path))
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Prints 2", "--session", "s1"))
        fixture.commit_all(self.root, "Print two")
        fixture.write(self.root, {"control": "fail\n"})
        self.assertEqual(self.run_in(self.root, "verify")[0], 1)
        proven = "Proven: T-10 closed after checked content changed since you started"
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(proven + ", and the check is none: no passing check recorded", err,
                      "no file differs from the last pass, and the pass still does not outlive the failing run")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(proven + ", in src/app.py outside the claims you still hold, and the check is none", err,
                      "a claim that covers no moved file explains nothing")

    def test_a_stamp_from_0_8_0_reads_without_error_and_a_claim_then_excuses_only_every_file_it_covers(self):
        """0.8.0 kept no record of what each checked file held, so the gate cannot tell which files moved since the pass."""
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        state = self.root / ".sako/state"
        stamp = json.loads((state / "stamp.json").read_text())
        command = json.dumps(["0.8.0", [sys.executable, "-c", "pass"], ["src"]], sort_keys=True)
        stamp.update(command_id=hashlib.sha256(command.encode("utf-8")).hexdigest()[:16], sako="0.8.0")
        (state / "stamp.json").write_text(json.dumps(stamp, indent=1) + "\n")
        (state / "passed.json").unlink(missing_ok=True)
        code, out, err = self.run_in(self.root, "check")
        self.assertEqual(code, 0, out + err)
        self.assertRegex(out, r"^evidence: (fresh|stale); ", "fresh while the version matches, stale after an update")
        self.assertEqual(self.run_in(self.root, "status")[0], 0)
        for task, path in (("Print two", "src/app.py"), ("Write the plan", ".sako/work/plan.md")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Done", "--scope", path))
        self.start("s1")
        for args in (("claim", "T-11"), ("close", "T-11", "--evidence", "Planned"), ("claim", "T-10")):
            fixture.succeeds(fixture.installed_copy(self.root, *args, "--session", "s1"))
        self.assertIn("; no automated check (records only)]", (self.root / ".sako/work/DONE.md").read_text())
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        self.assertEqual(self.stop("s1")[:2], (0, ""), "the claim covers every checked file this session moved")
        fixture.write(self.root, {"src/lib.py": "x = 1\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-11 closed after checked content changed since you started, in src/lib.py outside the "
                      "claims you still hold, and the check is stale", err)

    def test_a_records_close_needs_no_check_where_the_stop_after_it_needs_none(self):
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        self.start("s1")
        for task, path in (("Print two", "src/app.py"), ("Plan one", ".sako/work/plan-1.md"),
                           ("Plan two", ".sako/work/plan-2.md")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Done", "--scope", path))
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        for args in (("claim", "T-11"), ("close", "T-11", "--evidence", "Planned")):
            fixture.succeeds(fixture.installed_copy(self.root, *args, "--session", "s1"))
        self.assertIn("Planned [sk-", (self.root / ".sako/work/DONE.md").read_text())
        self.assertIn("; no automated check (records only)]", (self.root / ".sako/work/DONE.md").read_text())
        self.assertEqual(self.stop("s1")[:2], (0, ""), "the claim still held explains the stale check at the stop too")
        fixture.write(self.root, {"src/lib.py": "x = 1\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-12", "--session", "s1"))
        refusal = fixture.installed_copy(self.root, "close", "T-12", "--evidence", "Planned", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("evidence is stale", refusal.stderr, "a file outside every claim still held needs the check")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-11 closed after checked content changed since you started, in src/lib.py outside the "
                      "claims you still hold", err, "and so does the stop")

    def test_a_close_that_counts_a_pass_in_another_checkout_keeps_it_as_the_last_pass_here(self):
        for task, path in (("Print two", "src/app.py"), ("A library", "src/lib.py")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Runs", "--scope", path))
        self.start("s1")  # a coordinator holds both tasks; a helper works in a linked worktree
        for task in ("T-10", "T-11"):
            fixture.succeeds(fixture.installed_copy(self.root, "claim", task, "--session", "s1"))
        linked = self.base / "linked"
        fixture.git(self.root, "worktree", "add", "-q", "-b", "side", str(linked))
        fixture.write(linked, {"src/app.py": "print(2)\n"})
        fixture.commit_all(linked, "Print two")
        self.assertEqual(self.run_in(linked, "verify")[0], 0)
        fixture.git(self.root, "merge", "-q", "--ff-only", "side")
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Prints 2", "--session", "s1"))
        self.assertRegex((self.root / ".sako/work/DONE.md").read_text(), r"; check pass [^;]+ in worktree linked\]")
        fixture.write(self.root, {"src/lib.py": "x = 1\n"})
        self.assertEqual(self.stop("s1")[:2], (0, ""), "the pass that proved T-10 here is the last pass here")
        fixture.write(self.root, {"src/extra.py": "y = 2\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-10 closed after checked content changed since you started, in src/extra.py outside the "
                      "claims you still hold, and the check is none", err, "no pass was recorded in this checkout")

    def steered_check(self, paths=("src",)):
        """A check that an ignored file steers, two tasks, and T-10 closed with a pass and committed."""
        fixture.write(self.root, {"control": "pass\n", ".gitignore": "__pycache__/\ncontrol\n", "check.py": (
            "import pathlib, sys\nsys.exit(pathlib.Path('control').read_text() != 'pass\\n')\n"),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": list(paths)})})
        fixture.commit_all(self.root, "A check that an ignored file steers")
        for task, path in (("Print two", "src/app.py"), ("A library", "src/lib.py")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Runs", "--scope", path))

    def close_the_first(self):
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-10", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-10", "--evidence", "Prints 2", "--session", "s1"))
        fixture.git(self.root, "commit", "-qm", "Print two", "--", "src/app.py")

    def test_a_merged_rename_counts_its_old_path_outside_the_claim_still_held(self):
        """Git's diff names only the new path of a rename unless it is asked for both."""
        fixture.write(self.root, {"src/required.py": "needed = True\n" * 12, "check.py": (
            "import pathlib, sys\nsys.exit(not pathlib.Path('src/required.py').is_file())\n"),
            ".sako/config.json": json.dumps({"verify_command": [sys.executable, "check.py"], "verify_paths": ["src"]})})
        fixture.commit_all(self.root, "A check that needs one file")
        fixture.git(self.root, "checkout", "-qb", "side")
        fixture.git(self.root, "mv", "src/required.py", "src/replacement.py")
        fixture.commit_all(self.root, "Move the input")
        fixture.git(self.root, "checkout", "-q", "main")
        for task, path in (("Print two", "src/app.py"), ("A replacement", "src/replacement.py")):
            fixture.succeeds(fixture.installed_copy(self.root, "add", task, "--done-when", "Runs", "--scope", path))
        self.start("s1")
        self.close_the_first()
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        fixture.git(self.root, "merge", "-q", "--no-edit", "side")
        self.assertEqual(self.run_in(self.root, "verify")[0], 1, "the merge removed the file the check needs")
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Proven: T-10 closed after checked content changed since you started, in src/required.py outside the "
                      "claims you still hold, and the check is none", err, "the claim covers only the new path")

    def test_a_claim_of_a_file_left_dirty_before_the_start_explains_no_stale_check(self):
        self.steered_check()
        fixture.write(self.root, {"src/lib.py": "left = 'dirty before the start'\n"})
        self.start("s1")
        self.close_the_first()
        fixture.write(self.root, {"control": "fail\n"})
        self.assertEqual(self.run_in(self.root, "verify")[0], 1)
        proven = "Proven: T-10 closed after checked content changed since you started"
        self.assertIn(proven + ", and the check is none", self.stop("s1")[2])
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn(proven + ", in src/app.py outside the claims you still hold, and the check is none", err,
                      "the claim covers a file this session never moved")

    def test_deleting_a_verify_paths_entry_under_a_claim_still_held_does_not_block_the_pause(self):
        self.steered_check(paths=("src/app.py", "src/lib.py"))
        fixture.write(self.root, {"src/lib.py": "x = 0\n"})
        fixture.commit_all(self.root, "A library")
        self.start("s1")
        self.close_the_first()
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-11", "--session", "s1"))
        fixture.write(self.root, {"src/lib.py": "x = 1\n", "control": "fail\n"})
        self.assertEqual(self.run_in(self.root, "verify")[0], 1)
        self.assertEqual(self.stop("s1")[:2], (0, ""))
        (self.root / "src/lib.py").unlink()
        self.assertEqual(self.stop("s1")[::2], (0, ""), "the gate compares only the files outside the claim")

    def test_a_refused_close_leaves_the_last_pass_here_as_it_was(self):
        fixture.write(self.root, {".sako/work/DONE.md": fixture.done_file(
            fixture.row("T-1", status="done", claim="sk-0101-aaaaaaaaaaaa"))})
        self.start("s1")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-1", "--session", "s1"))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "verify"))
        record = self.root / ".sako/state/passed.json"
        self.assertTrue(record.exists(), "verify keeps the record itself")
        record.unlink()
        refusal = fixture.installed_copy(self.root, "close", "T-1", "--evidence", "Prints 2", "--session", "s1")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("already has a done row from another session", refusal.stderr)
        self.assertFalse(record.exists(), "only a close that writes its receipt records the pass it names")

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
