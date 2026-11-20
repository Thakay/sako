"""The finish gate: Recorded, Proven and Committed, each finding with its rule and its fix."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import contextlib
import io
import json
import os
import re
import shlex
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
        fixture.started_ago(self.root, "s2")  # so a task closed in this minute is closed since it started
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
        self.assertIn(f"not counted: notes/z.md: committed under T-10, closed by {sako.marker_for('peer2')}", out,
                      "once its task closed, merged work is not this session's to record")

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
            fixture.row("T-3", touches="`.sako/work/`"))})
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
        fixture.started_ago(self.root, "s1")
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
        self.assertIn(f"changed this session in {there}: 1 ({elsewhere})", self.run_in(linked, "check", session="s1")[1])
        code, _, err = self.run_in(linked, "check", "--gate", session="s1")
        self.assertEqual(code, 2, err)
        self.assertIn("SAKO gate: 1 problem(s) before you stop:\n  Committed: T-1 is closed but its changes are not committed: "
                      "src/app.py", err, "run there, only Committed blocks, since the change is recorded and that stamp is fresh")
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
        fixture.started_ago(self.root, "s1")
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
        fixture.started_ago(self.root, "s1")
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

    def test_a_task_closed_before_this_session_started_or_in_its_first_minute_covers_no_commit(self):
        self.start("s1")
        path = fixture.entry_path(self.root, "s1")
        entry = json.loads(path.read_text())
        first = entry["started_at"] // 60 * 60  # the start of the minute the session started in, in every zone
        path.write_text(json.dumps(dict(entry, started_at=first + 30)))
        fixture.write(self.root, {"src/app.py": "print(5)\n"})
        fixture.git(self.root, "commit", "-qam", "work with no task")
        for when, moment in (("three minutes before the start", first - 150), ("after the start, in its minute", first + 45)):
            with self.subTest(when):
                closed = time.strftime("%Y-%m-%dT%H:%M%z", time.localtime(moment))
                fixture.write(self.root, {".sako/work/DONE.md": fixture.done_file(
                    f"| T-5 | Broad import | Imported | Imported [sk-0101-aaaaaaaaaaaa; closed {closed}; no automated check] "
                    "| `src/` | - |")})
                code, _, err = self.stop("s1")
                self.assertEqual(code, 2, err)
                self.assertRegex(err, r'covered by no task you claimed or closed: src/app\.py \(in commit [0-9a-f]+ of ')
                row = sako.parse_rows({"done": (self.root / ".sako/work/DONE.md").read_text()})[0]
                self.assertEqual(sako.closed_at(row), moment // 60 * 60, "the start of the receipt's minute")
        row.evidence = "Imported [sk-0101-aaaaaaaaaaaa]"
        self.assertEqual(sako.closed_at(row), 0, "a receipt that names no close excuses nothing")

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

    def test_closed_at_reads_the_start_of_the_receipts_minute_in_the_receipts_own_offset(self):
        moment = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc).timestamp()
        for closed in ("2026-01-01T09:00+0000", "2026-01-01T14:30+0530", "2026-01-01T02:00-0700"):
            with self.subTest(closed=closed):
                row = sako.parse_rows({"done": fixture.done_file(f"| T-5 | Notes | Written | Wrote it [sk-0101-aaaaaaaaaaaa; "
                                                                 f"closed {closed}; no automated check] | `docs/` | - |")})[0]
                self.assertEqual(sako.closed_at(row), moment, "one moment, whatever zone the closing session was in")

    def test_a_live_writer_is_named_before_a_closed_task_that_also_covers_the_commit(self):
        self.start("s1")
        fixture.started_ago(self.root, "s1")
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
        fixture.started_ago(self.root, "s1")
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
                                                          "even when the clock puts the close in this session's first minute")
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
        self.assertEqual(list(self.seals()), ["T-10"], "a close drops the seals whose files were committed or changed since")
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
        fixture.started_ago(self.root, "s1")
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
        for session in ("s1", "s3"):
            fixture.started_ago(self.root, session)
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
        fixture.started_ago(self.root, "s1")  # so the second session's task closes since it started
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
        self.assertEqual(list(self.seals()), ["T-2"])
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
