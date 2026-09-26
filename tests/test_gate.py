"""The finish gate: Recorded, Proven and Committed, each finding with its rule and its fix."""

from pathlib import Path
import argparse
import contextlib
import io
import json
import os
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

    def test_a_session_that_changed_nothing_passes(self):
        fixture.write(self.root, {"src/wip.py": "draft\n"})
        self.start("s1")
        self.assertEqual(self.stop("s1")[0], 0)

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
        self.assertRegex(err, r'src/app\.py \(in commit [0-9a-f]+ "an unrecorded change"\)')
        fixture.git(self.root, "reset", "-q", "--hard", "HEAD~1")
        fixture.git(self.root, "checkout", "-qb", "side")
        fixture.write(self.root, {"src/side.py": "x = 1\n"})
        env = dict(fixture.ENV, GIT_COMMITTER_DATE="2001-01-01T00:00:00Z", GIT_AUTHOR_DATE="2001-01-01T00:00:00Z")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], env=env, check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "old work"], env=env, check=True)
        fixture.git(self.root, "checkout", "-q", "main")
        fixture.git(self.root, "merge", "-q", "--ff-only", "side")
        self.assertEqual(self.stop("s1")[0], 0, "a pulled commit made before the session is not this session's change")

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
        self.assertIn("a shared checkout shows what changed, not who changed it", out)
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
        self.assertIn("in another worktree, which cannot have changed this checkout", err)

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
        self.assertRegex(err, r'src/app\.py \(in commit [0-9a-f]+ "work in the worktree"\)', "merged work still counts")
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

    def test_a_commit_recorded_under_another_sessions_closed_task_is_not_this_sessions(self):
        self.start("s1")
        self.start("s2")
        fixture.succeeds(fixture.installed_copy(self.root, "claim", "T-2", "--session", "s2"))
        fixture.write(self.root, {"docs/x.md": "notes\n"})
        fixture.succeeds(fixture.installed_copy(self.root, "close", "T-2", "--evidence", "Wrote notes", "--session", "s2"))
        fixture.commit_all(self.root, "T-2: notes")
        fixture.succeeds(fixture.installed_copy(self.root, "end", "--session", "s2"))
        code, out, err = self.run_in(self.root, "check", "--gate", session="s1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("not counted: docs/x.md: committed under T-2, closed by sk-", out)

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
        self.assertIn("git add -- src/app.py, then git commit -m 'T-1: Invented task' when the owner has allowed commits", err)
        self.assertFalse(set("<>&") & set(err), err)
        self.assertNotIn("src/wip.py", err.split("Committed:")[1].split("\n")[0])
        fixture.git(self.root, "add", "src/app.py")
        fixture.git(self.root, "commit", "-qm", "T-1: Invented task")
        self.assertEqual(self.stop("s1")[0], 0)
        fixture.write(self.root, {"src/wip.py": "someone else's draft, now edited\n"})
        code, _, err = self.stop("s1")
        self.assertEqual(code, 2, err)
        self.assertIn("Committed: T-1 is closed but its changes are not committed: src/wip.py", err)

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
        self.assertIn("advisory: T-2 is claimed and unfinished; if you stop before closing it, write "
                      ".sako/work/T-2-invented-task/handoff.md: where it stands", out)
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
        self.assertEqual(self.run_in(self.root, "check", "--gate", session="s1")[1], "SAKO gate: clear\n")

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
        self.assertIn("changed this session: 1; unrecorded: src/app.py", out)

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
