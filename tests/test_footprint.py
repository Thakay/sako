"""The local footprint: one kit folder in the main checkout, out of Git, shared by worktrees."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sako
import selftest as fixture


class KitCase(unittest.TestCase):
    """A committed repository with a product file; helpers run SAKO the way clients do."""

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="sako-footprint-")
        self.base = Path(self.workspace.name)
        self.root = self.base / "app"
        self.root.mkdir()
        fixture.git(self.root, "init", "-q", "-b", "main")
        fixture.write(self.root, {"src/app.py": "print(1)\n", "README.md": "# App\n"})
        fixture.commit_all(self.root, "product")
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait()
        for path in (self.root / ".git", self.root / ".sako"):
            if path.exists():
                subprocess.run(["chmod", "-R", "u+w", str(path)])
        self.workspace.cleanup()

    def host(self):
        process = subprocess.Popen(["sleep", "60"])
        self.processes.append(process)
        return process.pid

    def install(self, *flags):
        return fixture.succeeds(fixture.seed(self.root, *flags))

    def worktree(self, name):
        fixture.git(self.root, "worktree", "add", "-q", str(self.base / name))
        return self.base / name

    def hook(self, checkout, agent, event, session, pid=None):
        """Run the exact command init wrote, as the client would, from that checkout."""
        entries = json.loads((checkout / sako.hook_file(agent)).read_text())["hooks"][event]
        command = entries[-1]["hooks"][0]["command"]
        env = dict(fixture.ENV, **({"SAKO_AGENT_PID": str(pid)} if pid else {}))
        return subprocess.run(["sh", "-c", command], cwd=checkout, env=env, capture_output=True, text=True,
                              input=json.dumps({"session_id": session, "hook_event_name": event}))

    def status(self, cwd=None):
        return fixture.succeeds(subprocess.run([sys.executable, str(self.root / ".sako/sako.py"), "status"],
                                               cwd=cwd or self.root, capture_output=True, text=True,
                                               env=fixture.ENV))

class Footprint(KitCase):
    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_fresh_install_changes_nothing_git_sees_and_says_where_everything_is(self):
        printout = self.install("--client", "claude", "--client", "codex").splitlines()
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")
        excluded = (self.root / ".git/info/exclude").read_text().splitlines()
        written = [p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                   if p.is_file() and ".git" not in p.relative_to(self.root).parts and ".sako" not in p.parts]
        self.assertIn("/.sako/", excluded)
        for relative in written:
            if relative not in ("src/app.py", "README.md"):
                self.assertIn("/" + relative, excluded, relative)
        self.assertEqual(printout[0], "SAKO installed in .sako/ (local). Nothing tracked changed; "
                                      "the folder is listed in .git/info/exclude.")
        self.assertEqual(printout[1], "Records: .sako/work/TASKS.md and DONE.md. They are not in Git yet; "
                                      "git clean -x deletes them.")
        self.assertEqual(printout[2], "  Keep their history, or share them privately:  uvx sako init --repo [url]")
        self.assertEqual(printout[3], "  Commit them with the code:                    uvx sako init --shared")
        self.assertIn("Claude Code (.claude/settings.local.json)", printout[4])
        self.assertIn("only after you trust them in Codex", printout[4])
        self.assertEqual(len(printout), 5)

    def test_status_repeats_the_footprint_and_tells_written_hooks_from_running_ones(self):
        self.install("--client", "claude")
        lines = self.status().splitlines()
        self.assertIn("(local)", lines[0])
        self.assertIn(".sako/work/TASKS.md", lines[1])
        self.assertIn("not in Git", lines[1])
        self.assertIn("--repo [url]", lines[2])
        self.assertEqual(lines[4], "Hooks: Claude Code not seen yet.")
        fixture.succeeds(self.hook(self.root, "claude", "SessionStart", "status-session", self.host()))
        self.assertRegex(self.status().splitlines()[4], r"Claude Code last ran \d{4}-\d\d-\d\d \d\d:\d\d \(SessionStart\)")

    def test_status_from_a_linked_worktree_names_the_records_by_absolute_path(self):
        self.install("--client", "none")
        lines = self.status(cwd=self.worktree("linked")).splitlines()
        self.assertTrue(lines[1].startswith(f"Records: {(self.root.resolve() / '.sako').as_posix()}/work/TASKS.md and DONE.md"),
                        lines[1])

    def test_hooks_work_from_the_main_checkout_and_a_wired_worktree(self):
        self.install("--client", "claude", "--client", "codex")
        feature = self.worktree("feature")
        wiring = fixture.succeeds(fixture.seed(feature))
        self.assertIn(f"This worktree uses SAKO in {(self.root.resolve() / '.sako').as_posix()}/", wiring)
        self.assertFalse((feature / ".sako").exists())
        for checkout, agent in [(c, a) for c, a in ((self.root, "claude"), (feature, "codex"), (feature, "claude"))
                                if not (fixture.WINDOWS and a == "codex")]:  # on Windows Codex gets no hooks
            with self.subTest(checkout=checkout.name, agent=agent):
                session = f"{checkout.name}-{agent}"
                context = fixture.succeeds(self.hook(checkout, agent, "SessionStart", session, self.host()))
                kit = ".sako" if checkout == self.root else (self.root.resolve() / ".sako").as_posix()
                self.assertIn(f"method {kit}/SAKO.md; records {kit}/work/TASKS.md and {kit}/work/DONE.md", context)
                self.assertIn(f"commands: {sako.PYTHON} {(self.root.resolve() / '.sako/sako.py').as_posix()} <verb>", context)
                fixture.succeeds(fixture.installed(self.root, "add", f"Work in {session}", "--scope", f"src/{session}/"))
                task = [line.split()[1] for line in (self.root / ".sako/work/TASKS.md").read_text().splitlines()
                        if session in line][0]
                fixture.succeeds(subprocess.run([sys.executable, str(self.root / ".sako/sako.py"), "claim", task,
                                                 "--session", session], cwd=checkout, env=fixture.ENV,
                                                capture_output=True, text=True))
                fixture.write(checkout, {f"src/{session}/change.py": "x = 1\n"})
                fixture.succeeds(subprocess.run([sys.executable, str(self.root / ".sako/sako.py"), "close", task,
                                                 "--evidence", "Probe change", "--session", session], cwd=checkout,
                                                env=fixture.ENV, capture_output=True, text=True))
                self.assertEqual(self.hook(checkout, agent, "Stop", session).returncode, 2, "the gate runs at stop")
                fixture.commit_all(checkout, f"land {session}")
                fixture.succeeds(self.hook(checkout, agent, "Stop", session))
                fixture.succeeds(self.hook(checkout, agent, "SessionEnd", session))

    def test_worktrees_share_one_ledger_and_numbering(self):
        self.install("--client", "none")
        first, second = self.worktree("first"), self.worktree("second")
        runtime = str(self.root / ".sako/sako.py")

        def add(args):
            checkout, number = args
            return subprocess.run([sys.executable, runtime, "add", f"Task {checkout.name} {number}",
                                   "--done-when", "It exists", "--scope", f"src/{checkout.name}-{number}/"], cwd=checkout,
                                  env=fixture.ENV, capture_output=True, text=True)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(add, [(checkout, n) for checkout in (first, second) for n in range(6)]))
        ids = [fixture.succeeds(result).split(":")[0] for result in results]
        self.assertEqual(len(set(ids)), 12, ids)
        listing = fixture.succeeds(subprocess.run([sys.executable, runtime, "next"], cwd=second,
                                                  env=fixture.ENV, capture_output=True, text=True))
        self.assertEqual(sorted(set(re.findall(r"\bT-\d+\b", listing))), sorted(ids), "either worktree sees every task")
        self.assertFalse((first / ".sako").exists() or (second / ".sako").exists())

    def test_a_stray_kit_folder_in_a_worktree_never_shadows_the_main_one(self):
        self.install("--client", "none")
        fixture.succeeds(fixture.installed(self.root, "add", "Main task", "--scope", "src/"))
        linked = self.worktree("linked")
        fixture.write(linked, {".sako/work/TASKS.md": "# A stray ledger\n"})
        output = fixture.cli(linked, "check")[1]
        self.assertIn(f"stray .sako/ in this worktree is ignored; SAKO uses {(self.root.resolve() / '.sako').as_posix()}", output)
        self.assertIn("Main task", fixture.cli(linked, "next")[1])

    def test_git_clean_x_deletes_a_local_kit_folder_as_the_printout_says(self):
        self.install("--client", "none")
        fixture.git(self.root, "clean", "-fdx", "-q")
        self.assertFalse((self.root / ".sako").exists())

    def test_remove_keeps_configuration_and_records_byte_for_byte(self):
        self.install("--client", "claude", "--client", "codex")
        fixture.succeeds(fixture.installed(self.root, "add", "Keep this task", "--scope", "src/"))
        kept = {p: p.read_bytes() for p in (self.root / ".sako").rglob("*")
                if p.is_file() and ("work" in p.parts or p.name == "config.json")}
        fixture.succeeds(fixture.installed(self.root, "remove"))
        self.assertEqual({p: p.read_bytes() for p in kept}, kept)
        remaining = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                     if p.is_file() and ".git" not in p.relative_to(self.root).parts}
        self.assertEqual(remaining, {"src/app.py", "README.md", ".sako/config.json", ".sako/work/TASKS.md"})

    @unittest.skipIf(fixture.WINDOWS or os.geteuid() == 0, "file modes do not bind root, nor on Windows")
    def test_no_state_under_git_and_a_read_only_kit_folder_refuses_plainly(self):
        self.install("--client", "none")
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"],
                                                                   "verify_paths": ["src"]})})
        subprocess.run(["chmod", "-R", "a-w", str(self.root / ".git")], check=True)
        pid = self.host()
        for args in (("add", "Probe", "--done-when", "It passes", "--scope", "src/"), ("start",), ("claim", "T-1"), ("verify",),
                     ("close", "T-1", "--evidence", "Probe passed"), ("check",), ("status",)):
            with self.subTest(args=args):
                session = ["--session", "read-only"] if args[0] in ("start", "claim", "close", "status") else []
                fixture.succeeds(subprocess.run([sys.executable, str(self.root / ".sako/sako.py"), *args, *session],
                                                cwd=self.root, capture_output=True, text=True,
                                                env=dict(fixture.ENV, SAKO_AGENT_PID=str(pid))))
        subprocess.run(["chmod", "-R", "u+w", str(self.root / ".git")], check=True)
        self.assertFalse((self.root / ".git/sako").exists())
        subprocess.run(["chmod", "-R", "a-w", str(self.root / ".sako")], check=True)
        refusal = fixture.installed(self.root, "add", "Blocked", "--scope", "src/")
        self.assertEqual(refusal.returncode, 3, refusal.stderr)
        self.assertIn("--add-dir", refusal.stderr)
        fixture.succeeds(fixture.installed(self.root, "check"))

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_worktree_wiring_gives_codex_a_writable_root_without_changing_git(self):
        self.install("--client", "codex")
        loose = self.worktree("loose")
        fixture.succeeds(fixture.seed(loose))
        config = (loose / ".codex/config.toml").read_text()
        self.assertIn("[sandbox_workspace_write]", config)
        self.assertIn(json.dumps(str(self.root.resolve() / ".sako")), config)
        self.assertEqual(fixture.git(loose, "status", "--porcelain"), "")
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")
        fixture.write(self.root, {".codex/config.toml": "# Project Codex settings\n"})
        fixture.git(self.root, "add", "-f", ".codex/config.toml")
        fixture.git(self.root, "commit", "-qm", "project Codex settings")
        pinned = self.worktree("pinned")
        printout = fixture.succeeds(fixture.seed(pinned))
        self.assertEqual((pinned / ".codex/config.toml").read_text(), "# Project Codex settings\n")
        self.assertIn(f"--add-dir {self.root.resolve() / '.sako'}", printout)
        fixture.succeeds(fixture.installed(self.root, "remove"))
        self.assertFalse((loose / ".codex/config.toml").exists(), "removal clears the worktree's wiring")

    def test_hooks_exit_1_never_2_when_the_kit_is_gone(self):
        self.install("--client", "claude")
        (self.root / ".sako/sako.py").unlink()
        stop = self.hook(self.root, "claude", "Stop", "orphan")
        self.assertEqual(stop.returncode, 1, stop.stderr)
        self.assertIn("run: uvx sako init", stop.stderr)


class ReviewRegressions(KitCase):
    """One test per defect found in the footprint review."""

    def hooks_in(self, checkout, agent):
        path = checkout / sako.hook_file(agent)
        commands = [h["command"] for entries in json.loads(path.read_text()).get("hooks", {}).values()
                    for entry in entries for h in entry["hooks"]] if path.exists() else []
        return any(c.startswith(sako.hook_command(agent)) for c in commands)

    def test_opting_out_reaches_worktrees_and_worktree_init_takes_no_options(self):
        self.install("--client", "claude")
        linked = self.worktree("linked")
        fixture.succeeds(fixture.seed(linked))
        self.assertTrue(self.hooks_in(linked, "claude"))
        self.install("--client", "none")
        self.assertFalse(self.hooks_in(linked, "claude") or self.hooks_in(self.root, "claude"))
        for flags in (("--client", "none",), ("--client", "codex"), ("--update",)):
            refusal = fixture.seed(linked, *flags)
            self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
            self.assertIn("run init with options in", refusal.stderr)

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_codex_worktree_gets_its_writable_root_and_loses_it_with_codex(self):
        self.install("--client", "codex")
        linked = self.worktree("linked")
        fixture.succeeds(fixture.seed(linked))
        self.assertIn("writable_roots", (linked / ".codex/config.toml").read_text())
        self.assertTrue(self.hooks_in(linked, "codex"))
        self.install("--client", "none")
        self.assertFalse((linked / ".codex/config.toml").exists() or self.hooks_in(linked, "codex"))

    @unittest.skipIf(fixture.WINDOWS or os.geteuid() == 0, "file modes do not bind root, nor on Windows")
    def test_init_with_a_read_only_git_folder_prints_the_exclude_lines(self):
        subprocess.run(["chmod", "-R", "a-w", str(self.root / ".git")], check=True)
        printout = self.install("--client", "claude")
        self.assertIn("Could not write .git/info/exclude", printout)
        self.assertIn("/.sako/", printout)
        self.assertIn("add the line /.sako/ to .git/info/exclude", printout)

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_codex_block_stays_valid_and_is_refreshed(self):
        self.install("--client", "codex")
        dotted = self.worktree("dotted")
        fixture.write(dotted, {".codex/config.toml": "sandbox_workspace_write.network_access = true\n"})
        printout = fixture.succeeds(fixture.seed(dotted))
        self.assertEqual((dotted / ".codex/config.toml").read_text(), "sandbox_workspace_write.network_access = true\n")
        self.assertIn("--add-dir", printout)
        moved = self.worktree("moved")
        fixture.succeeds(fixture.seed(moved))
        stale = (moved / ".codex/config.toml").read_text().replace(str(self.root.resolve()), "/old/place")
        (moved / ".codex/config.toml").write_text(stale)
        fixture.succeeds(fixture.seed(moved))
        refreshed = (moved / ".codex/config.toml").read_text()
        self.assertNotIn("/old/place", refreshed)
        self.assertEqual(refreshed.count("[sandbox_workspace_write]"), 1)
        try:
            import tomllib
        except ImportError:
            return
        tomllib.loads(refreshed)

    def test_tracked_skill_files_are_never_changed_or_deleted(self):
        canonical = (fixture.SAKO.parent / "skills/sako/SKILL.md").read_text()
        fixture.write(self.root, {".claude/skills/sako/SKILL.md": canonical + "Project copy.\n"})
        fixture.commit_all(self.root, "a tracked skill")
        printout = self.install("--client", "claude")
        self.assertIn(".claude/skills/sako/SKILL.md is tracked", printout)
        fixture.succeeds(fixture.seed(self.root, "--update"))
        fixture.succeeds(fixture.installed(self.root, "remove"))
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")

    def test_printed_commands_work_from_worktrees_and_paths_with_spaces(self):
        spaced = self.base / "my project"
        spaced.mkdir()
        fixture.git(spaced, "init", "-q", "-b", "main")
        fixture.commit_all(spaced, "empty")
        fixture.succeeds(fixture.seed(spaced, "--client", "none"))
        context = fixture.succeeds(subprocess.run([sys.executable, str(spaced / ".sako/sako.py"), "start", "--session",
                                                   "spaced"], cwd=spaced, env=dict(fixture.ENV, SAKO_AGENT_PID=str(self.host())),
                                                  capture_output=True, text=True))
        command = next(line for line in context.splitlines() if line.startswith("commands: "))[10:]
        self.assertIn("'", command, "the path with a space is quoted")
        runnable = command.replace("<verb>", "status")
        fixture.succeeds(subprocess.run(["sh", "-c", runnable], cwd=spaced, env=fixture.ENV, capture_output=True, text=True))
        fixture.git(spaced, "worktree", "add", "-q", str(self.base / "spaced-wt"))
        printout = fixture.succeeds(fixture.seed(self.base / "spaced-wt"))
        self.assertIn((spaced.resolve() / ".sako/sako.py").as_posix(), printout)

    def test_a_tracked_kit_folder_outside_the_shared_footprint_is_refused_with_the_way_out(self):
        fixture.write(self.root, {".sako/install.json": "{}\n"})
        fixture.git(self.root, "add", "-f", ".sako/install.json")
        fixture.git(self.root, "commit", "-qm", "an older release committed its kit")
        refusal = fixture.seed(self.root, "--client", "none")
        self.assertEqual(refusal.returncode, 3, refusal.stderr)
        self.assertIn("git rm -r --cached .sako", refusal.stderr)
        fixture.git(self.root, "rm", "-rq", "--cached", ".sako")
        fixture.git(self.root, "commit", "-qm", "untrack the old kit")
        (self.root / ".sako/install.json").unlink()
        self.assertIn("(local)", self.install("--client", "none"))

    @unittest.skipIf(fixture.WINDOWS or os.geteuid() == 0, "file modes do not bind root, nor on Windows")
    def test_a_failed_first_install_leaves_no_hooks_behind(self):
        (self.root / ".codex").mkdir()
        subprocess.run(["chmod", "a-w", str(self.root / ".codex")], check=True)
        failed = fixture.seed(self.root, "--client", "claude", "--client", "codex")
        subprocess.run(["chmod", "u+w", str(self.root / ".codex")], check=True)
        self.assertEqual(failed.returncode, 3, failed.stdout + failed.stderr)
        self.assertFalse((self.root / ".sako").exists())
        self.assertFalse((self.root / ".claude/settings.local.json").exists())
        self.assertFalse((self.root / ".agents/skills/sako/SKILL.md").exists())

    def test_a_bare_repository_kept_as_dot_git_refuses_plainly(self):
        holder = self.base / "holder"
        fixture.git(self.base, "clone", "-q", "--bare", str(self.root), str(holder / ".git"))
        fixture.git(holder / ".git", "worktree", "add", "-q", str(holder / "main"), "main")
        refusal = fixture.seed(holder / "main", "--client", "none")
        self.assertEqual(refusal.returncode, 3, refusal.stderr)
        self.assertIn("bare repository", refusal.stderr)

    def test_remove_without_a_kit_creates_nothing(self):
        refusal = fixture.cli(self.root, "remove")
        self.assertEqual(refusal[0], 3, refusal)
        self.assertFalse((self.root / ".sako").exists())

    def test_non_utf8_git_and_ledger_files_are_plain_refusals_or_kept(self):
        exclude = self.root / ".git/info/exclude"
        exclude.write_bytes(b"/caf\xe9.log\n")
        self.install("--client", "none")
        self.assertTrue(exclude.read_bytes().startswith(b"/caf\xe9.log\n/.sako/") or b"/caf\xe9.log" in exclude.read_bytes())
        self.assertIn(b"/.sako/", exclude.read_bytes())
        (self.root / ".sako/work/TASKS.md").write_bytes(b"| T-1 | caf\xe9 |\n")
        code, _, err = fixture.cli(self.root, "next")
        self.assertEqual(code, 3, err)
        self.assertIn("not UTF-8", err)


def records(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / ".sako/work").rglob("*") if p.is_file()}


def sh(root, command):
    subprocess.run(["sh", "-c", command], cwd=root, env=fixture.ENV, check=True, capture_output=True)


class RepoFootprint(KitCase):
    def test_the_records_commit_line_works_as_printed_from_a_linked_worktree(self):
        self.install("--client", "none", "--repo")
        linked = self.worktree("linked")
        line = next(x for x in self.status(cwd=linked).splitlines() if x.startswith("Records: "))
        self.assertIn(f"git -C {(self.root.resolve() / '.sako').as_posix()} add -A", line)
        sh(linked, line.split("Commit them with: ")[1])
        self.assertIn("SAKO records", fixture.git(self.root / ".sako", "log", "--oneline"))

    def test_repo_without_url_nests_a_repository_that_git_clean_keeps(self):
        printout = self.install("--client", "none", "--repo")
        self.assertTrue((self.root / ".sako/.git").is_dir())
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")
        self.assertIn("/.sako/", (self.root / ".git/info/exclude").read_text())
        self.assertEqual((self.root / ".sako/.gitignore").read_text(), "/sako.py\n/SAKO.md\n/install.json\n/state/\n")
        self.assertIn("(repo)", printout.splitlines()[0])
        self.assertIn('git -C .sako add -A && git -C .sako commit -m "SAKO records"', printout)
        sh(self.root, 'git -C .sako add -A && git -C .sako commit -qm "SAKO records"')
        self.assertEqual(set(fixture.git(self.root / ".sako", "ls-files").splitlines()),
                         {".gitignore", "config.json", "work/TASKS.md"})
        self.assertIn("nothing changed", self.install("--client", "none"))
        fixture.git(self.root, "clean", "-fdx", "-q")
        self.assertTrue((self.root / ".sako/work/TASKS.md").exists())
        self.assertEqual(json.loads((self.root / ".sako/install.json").read_text())["footprint"], "repo")

    def test_repo_with_url_clones_fresh_adds_a_remote_to_existing_records_and_refuses_conflicts(self):
        self.install("--client", "none", "--repo")
        fixture.succeeds(fixture.installed(self.root, "add", "Shared task", "--scope", "src/"))
        remote = self.base / "records.git"
        fixture.git(self.base, "init", "-q", "--bare", str(remote))
        printout = self.install("--client", "none", "--repo", str(remote))
        self.assertIn(f"origin {remote}", printout)
        self.assertEqual(fixture.git(self.root / ".sako", "remote", "get-url", "origin"), str(remote))
        sh(self.root, "git -C .sako add -A && git -C .sako commit -qm records && git -C .sako push -q -u origin HEAD")
        clone = self.base / "clone"
        fixture.git(self.base, "clone", "-q", str(self.root), str(clone))
        fixture.succeeds(fixture.seed(clone, "--client", "none", "--repo", str(remote)))
        self.assertIn("Shared task", (clone / ".sako/work/TASKS.md").read_text())
        self.assertTrue((clone / ".sako/sako.py").exists())
        self.assertEqual(fixture.git(clone, "status", "--porcelain"), "")
        self.assertEqual(fixture.git(clone / ".sako", "status", "--porcelain"), "")
        self.assertEqual(records(clone), records(self.root))
        self.assertIn("(repo)", fixture.succeeds(fixture.installed(clone, "status")))
        other = fixture.make_repo(self.base / "other")
        fixture.succeeds(fixture.seed(other, "--client", "none"))
        refusal = fixture.seed(other, "--client", "none", "--repo", str(remote))
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("already holds records", refusal.stderr)
        self.assertFalse((other / ".sako/.git").exists())

    def test_the_gate_wants_the_records_committed_in_the_repo_footprint(self):
        self.install("--client", "none", "--repo")
        sh(self.root, "git -C .sako add -A && git -C .sako commit -qm records")
        pid = self.host()
        fixture.succeeds(fixture.installed(self.root, "add", "Change app", "--scope", "src/"))
        fixture.succeeds(fixture.installed(self.root, "start", session="repo-s", pid=pid))
        fixture.succeeds(fixture.installed(self.root, "claim", "T-1", session="repo-s", pid=pid))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed(self.root, "close", "T-1", "--evidence", "done", session="repo-s", pid=pid))
        fixture.commit_all(self.root, "T-1")
        gate = fixture.installed(self.root, "check", "--gate", session="repo-s", pid=pid)
        self.assertEqual(gate.returncode, 2, gate.stdout + gate.stderr)
        fix = "git -C .sako add -A, then git -C .sako commit -m 'T-1 closed'"
        self.assertIn(fix, gate.stderr)
        sh(self.root, fix.replace(", then ", " && "))  # the two commands, in order
        fixture.succeeds(fixture.installed(self.root, "check", "--gate", session="repo-s", pid=pid))

    def test_remove_warns_about_commits_that_exist_only_here_and_keeps_the_history(self):
        self.install("--client", "none", "--repo")
        sh(self.root, "git -C .sako add -A && git -C .sako commit -qm records")
        kept = records(self.root)
        out = fixture.succeeds(fixture.installed(self.root, "remove"))
        self.assertIn("1 commit(s) that exist only here", out)
        self.assertTrue((self.root / ".sako/.git").is_dir())
        self.assertEqual(records(self.root), kept)
        refusal = fixture.seed(self.root, "--client", "none")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("mv .sako/.git ../app-records.git", refusal.stderr)
        self.install("--client", "none", "--repo")
        remote = self.base / "records.git"
        fixture.git(self.base, "init", "-q", "--bare", str(remote))
        sh(self.root, f"git -C .sako remote add origin {remote.as_posix()} && git -C .sako push -q -u origin HEAD")
        self.assertNotIn("exist only here", fixture.succeeds(fixture.installed(self.root, "remove")))

    def test_a_missing_nested_repo_is_a_finding_not_a_guess(self):
        self.install("--client", "none", "--repo")
        subprocess.run(["rm", "-rf", str(self.root / ".sako/.git")], check=True)
        out = fixture.cli(self.root, "check")[1]
        self.assertIn("install.json records repo, but Git shows local", out)
        self.assertIn("uvx sako init --repo", out)

    def test_the_empty_git_folder_codex_shows_in_a_writable_kit_is_not_a_records_repository(self):
        self.install("--client", "none")
        mask = self.root / ".sako/.git"
        mask.mkdir()
        mask.chmod(0o555)  # Codex's sandbox puts an empty, read-only .git in every writable folder
        pid = self.host()
        fixture.succeeds(fixture.installed(self.root, "start", session="codex-s", pid=pid))
        self.assertNotIn("footprint:", self.status())
        fixture.succeeds(fixture.installed(self.root, "check", "--gate", session="codex-s", pid=pid))
        mask.chmod(0o755)
        mask.rmdir()
        self.install("--client", "none", "--repo")
        self.assertNotIn("footprint:", self.status())


class SharedFootprint(KitCase):
    def share(self):
        printout = self.install("--shared")
        self.assertIn("Commit them with: " + sako.SHARE, printout)
        sh(self.root, sako.SHARE)
        return printout

    def test_shared_commits_nothing_and_the_printed_command_commits_the_folder(self):
        self.install("--client", "none")
        fixture.succeeds(fixture.installed(self.root, "add", "Kept task", "--scope", "src/"))
        kept = records(self.root)
        printout = self.install("--client", "none", "--shared")
        self.assertEqual(len(fixture.git(self.root, "log", "--oneline").splitlines()), 1)
        self.assertNotIn("/.sako/", (self.root / ".git/info/exclude").read_text())
        self.assertIn("so an agent on a fresh clone finds SAKO before init: This project uses SAKO", printout)
        self.assertFalse((self.root / "AGENTS.md").exists(), "SAKO writes nothing into the project's instructions")
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "?? .sako/")
        self.assertIn("records shared, but Git shows local", fixture.cli(self.root, "check")[1])
        sh(self.root, sako.SHARE)
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")
        self.assertEqual(set(fixture.git(self.root, "ls-files", ".sako").splitlines()),
                         {".sako/.gitignore", ".sako/config.json", ".sako/install.json", ".sako/sako.py",
                          ".sako/SAKO.md", ".sako/work/TASKS.md"})
        self.assertEqual(records(self.root), kept)
        lines = self.status().splitlines()
        self.assertIn("(shared)", lines[0])
        self.assertIn("each branch has its own copy", lines[1])
        self.assertNotIn("footprint:", fixture.cli(self.root, "check")[1])
        self.assertIn("nothing changed", self.install("--client", "none"))
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")

    def test_shared_refuses_while_a_nested_repo_exists_and_names_the_move(self):
        self.install("--client", "none", "--repo")
        refusal = fixture.seed(self.root, "--client", "none", "--shared")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("mv .sako/.git ../app-records.git", refusal.stderr)
        sh(self.root, "git -C .sako add -A && git -C .sako commit -qm records")
        kept = records(self.root)
        sh(self.root, "mv .sako/.git ../app-records.git")
        self.install("--client", "none", "--shared")
        self.assertEqual((self.root / ".sako/.gitignore").read_text(), "/state/\n")
        self.assertEqual(records(self.root), kept)

    def test_each_worktree_uses_its_own_copy_in_shared_and_a_branch_without_one_refuses(self):
        self.install("--client", "claude")
        old = self.worktree("old")
        self.share()
        feature = self.worktree("feature")
        printout = fixture.succeeds(fixture.seed(feature))
        self.assertIn("uses its own .sako/ (shared)", printout)
        self.assertTrue((feature / ".sako/sako.py").exists())
        fixture.succeeds(subprocess.run([sys.executable, str(feature / ".sako/sako.py"), "add", "Feature task",
                                         "--done-when", "x", "--scope", "src/"], cwd=feature, env=fixture.ENV,
                                        capture_output=True, text=True))
        self.assertIn("Feature task", (feature / ".sako/work/TASKS.md").read_text())
        self.assertNotIn("Feature task", (self.root / ".sako/work/TASKS.md").read_text())
        context = fixture.succeeds(self.hook(feature, "claude", "SessionStart", "feature-s", self.host()))
        self.assertIn("records .sako/work/TASKS.md", context)
        self.assertIn("M .sako/work/TASKS.md", fixture.git(feature, "status", "--porcelain"))
        refusal = fixture.seed(old)
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("this branch has none", refusal.stderr)

    def test_the_gate_wants_the_records_committed_with_the_code(self):
        self.share()
        pid = self.host()
        fixture.succeeds(fixture.installed(self.root, "add", "Change app", "--scope", "src/"))
        fixture.succeeds(fixture.installed(self.root, "start", session="sh-s", pid=pid))
        fixture.succeeds(fixture.installed(self.root, "claim", "T-1", session="sh-s", pid=pid))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed(self.root, "close", "T-1", "--evidence", "done", session="sh-s", pid=pid))
        sh(self.root, "git add src && git commit -qm T-1")
        gate = fixture.installed(self.root, "check", "--gate", session="sh-s", pid=pid)
        self.assertEqual(gate.returncode, 2, gate.stdout + gate.stderr)
        self.assertNotIn("Recorded:", gate.stderr)
        fix = "git add -A -- .sako, then git commit -m 'T-1 closed'"
        self.assertIn(fix, gate.stderr)
        sh(self.root, fix.replace(", then ", " && "))  # the two commands, in order
        fixture.succeeds(fixture.installed(self.root, "check", "--gate", session="sh-s", pid=pid))

    def test_leaving_shared_keeps_the_folder_and_the_records_on_the_way_to_local_and_repo(self):
        self.share()
        fixture.succeeds(fixture.installed(self.root, "add", "Kept", "--scope", "src/"))
        sh(self.root, "git add -A .sako && git commit -qm task")
        kept = records(self.root)
        out = fixture.succeeds(fixture.installed(self.root, "remove"))
        self.assertIn("git rm -r --cached .sako", out)
        refusal = fixture.seed(self.root, "--client", "none")
        self.assertIn("git rm -r --cached .sako", refusal.stderr)
        sh(self.root, "git rm -rq --cached .sako && git commit -qm unshare")
        self.assertIn("(local)", self.install("--client", "none"))
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")
        self.assertEqual(records(self.root), kept)
        self.install("--client", "none", "--repo")
        self.assertEqual(records(self.root), kept)
        self.assertEqual((self.root / ".sako/.gitignore").read_text(), "/sako.py\n/SAKO.md\n/install.json\n/state/\n")
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")



class FootprintReview(KitCase):
    """One regression per defect the footprint review found."""

    def close_and_claim_next(self, records_commit):
        pid = self.host()
        for task in ("Change app", "Next change"):
            fixture.succeeds(fixture.installed(self.root, "add", task, "--scope", "src/"))
        fixture.succeeds(fixture.installed(self.root, "start", session="s", pid=pid))
        fixture.succeeds(fixture.installed(self.root, "claim", "T-1", session="s", pid=pid))
        fixture.write(self.root, {"src/app.py": "print(2)\n"})
        fixture.succeeds(fixture.installed(self.root, "close", "T-1", "--evidence", "Changed", session="s", pid=pid))
        sh(self.root, "git add src && git commit -qm T-1 && " + records_commit)
        fixture.succeeds(fixture.installed(self.root, "claim", "T-2", session="s", pid=pid))
        fixture.write(self.root, {".sako/work/T-2-next-change/handoff.md": "Where it stands: started\n"})
        return fixture.installed(self.root, "check", "--gate", session="s", pid=pid)

    def test_pausing_after_a_close_stays_free_in_the_repo_footprint(self):
        self.install("--client", "none", "--repo")
        sh(self.root, "git -C .sako add -A && git -C .sako commit -qm records")
        fixture.succeeds(self.close_and_claim_next("git -C .sako add -A && git -C .sako commit -qm closed"))

    def test_pausing_after_a_close_stays_free_in_the_shared_footprint(self):
        self.install("--client", "none", "--shared")
        sh(self.root, sako.SHARE)
        fixture.succeeds(self.close_and_claim_next("git add -A -- .sako && git commit -qm closed"))

    def test_a_missing_records_repository_never_offers_to_commit_the_code(self):
        self.install("--client", "none", "--repo")
        subprocess.run(["rm", "-rf", str(self.root / ".sako/.git")], check=True)
        fixture.write(self.root, {"notes.txt": "unrelated work\n"})
        gate = self.close_and_claim_next("true")
        self.assertNotIn("git -C .sako add", gate.stdout + gate.stderr)
        self.assertIn("records repo, but Git shows local", gate.stdout + gate.stderr)

    def test_a_worktree_on_a_branch_with_an_older_kit_uses_the_main_ledger(self):
        self.install("--client", "none")
        legacy = self.worktree("legacy")
        fixture.write(legacy, {".sako/install.json": '{"version": "0.3.0", "files": {}}\n', ".sako/work/TASKS.md": "# Old\n"})
        fixture.git(legacy, "add", "-f", ".sako")
        fixture.git(legacy, "commit", "-qm", "an older release committed its kit")
        check = subprocess.run([sys.executable, str(self.root / ".sako/sako.py"), "check"], cwd=legacy,
                               env=fixture.ENV, capture_output=True, text=True)
        self.assertIn(f"stray .sako/ in this worktree is ignored; SAKO uses {(self.root.resolve() / '.sako').as_posix()}",
                      check.stdout)

    @unittest.skipUnless(fixture.symlinks(), "creating symlinks needs a privilege here")
    def test_shared_leaves_instruction_files_and_their_links_alone(self):
        fixture.write(self.root, {"CLAUDE.md": "# Rules\n"})
        (self.root / "AGENTS.md").symlink_to("CLAUDE.md")
        fixture.commit_all(self.root, "rules")
        self.install("--client", "none", "--shared")
        self.assertTrue((self.root / "AGENTS.md").is_symlink())
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "# Rules\n")

    @unittest.skipUnless(fixture.symlinks(), "creating symlinks needs a privilege here")
    def test_remove_keeps_a_tracked_file_behind_a_linked_folder(self):
        (self.root / "config/claude").mkdir(parents=True)
        (self.root / ".claude").symlink_to("config/claude", target_is_directory=True)
        self.install("--client", "claude")
        fixture.git(self.root, "add", "-f", "config/claude/skills/sako/SKILL.md")
        fixture.git(self.root, "commit", "-qm", "share the skill")
        fixture.succeeds(fixture.installed(self.root, "remove"))
        self.assertTrue((self.root / "config/claude/skills/sako/SKILL.md").is_file())

    def test_a_cloned_records_repository_keeps_its_own_gitignore(self):
        self.install("--client", "none", "--repo")
        with (self.root / ".sako/.gitignore").open("a") as ignore:
            ignore.write("/scratch/\n")
        remote = self.base / "records.git"
        fixture.git(self.base, "init", "-q", "--bare", str(remote))
        sh(self.root, f"git -C .sako add -A && git -C .sako commit -qm records && git -C .sako remote add origin {remote.as_posix()}"
                      " && git -C .sako push -q -u origin HEAD")
        clone = self.base / "clone"
        fixture.git(self.base, "clone", "-q", str(self.root), str(clone))
        fixture.succeeds(fixture.seed(clone, "--client", "none", "--repo", str(remote)))
        self.assertIn("/scratch/", (clone / ".sako/.gitignore").read_text())
        self.assertEqual(fixture.git(clone / ".sako", "status", "--porcelain"), "")

    def test_shared_records_that_git_ignores_are_a_finding(self):
        exclude = self.root / ".git/info/exclude"
        exclude.write_text(exclude.read_text().replace("\n", "\r\n"))
        self.install("--client", "none", "--shared")
        self.assertNotIn("/.sako/", exclude.read_text(), "a line ending in CRLF is dropped too")
        sh(self.root, sako.SHARE)
        with exclude.open("a") as lines:
            lines.write("/.sako/\n")
        self.assertIn("Git ignores .sako/ here", fixture.cli(self.root, "check")[1])

if __name__ == "__main__":
    unittest.main()
