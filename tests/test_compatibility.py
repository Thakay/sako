"""Shared entry points and conversation identity across agent hosts."""

from pathlib import Path
import contextlib
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


class Compatibility(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="sako-compatibility-")
        self.root = fixture.make_repo(Path(self.workspace.name) / "project")
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
        return process

    def cli(self, *args, session=None, pid=None, expected=0):
        code, out, err = fixture.cli(self.root, *args, session=session, pid=pid)
        self.assertEqual(code, expected, out + err)
        return out + err

    def files(self):
        return fixture.snapshot(self.root)

    def fresh(self, name, files=None, **env):
        """A new repository with the given client signals, installed with no flags."""
        self.root = fixture.make_repo(Path(self.workspace.name) / name, files)
        result = subprocess.run([sys.executable, str(fixture.SAKO), "init"], cwd=self.root,
                                env=dict(fixture.ENV, **env), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_no_signal_installs_the_shared_skill_and_records_no_choice(self):
        printout = self.fresh("plain", {".agents/skills/existing/SKILL.md": "Another workflow\n"})
        self.assertIn("Hooks: none, no client found here. Agents read .agents/skills/sako/SKILL.md;", printout)
        self.assertIn("Change the client with: uvx sako init --client claude|codex|none.", printout)
        self.assertIsNone(sako.installation(self.root)["clients"])
        self.assertTrue((self.root / ".agents/skills/sako/SKILL.md").is_file())
        for path in (".claude", ".codex"):
            self.assertFalse((self.root / path).exists(), path)

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_every_client_with_a_signal_is_wired_and_the_reason_printed(self):
        both = ["claude", "codex"]
        cases = [("claude-folder", {".claude/settings.json": "{}"}, {}, ["claude"], ".claude/"),
                 ("claude-rules", {"CLAUDE.md": "rules\n"}, {}, ["claude"], "CLAUDE.md"),
                 ("codex-folder", {".codex/config.toml": "# settings\n"}, {}, ["codex"], ".codex/"),
                 ("shared-rules", {"AGENTS.md": "rules\n"}, {}, both, "AGENTS.md (read by both)"),
                 ("both-folders", {".claude/settings.json": "{}", ".codex/config.toml": "#\n"}, {}, both,
                  ".claude/ and .codex/"),
                 ("claude-session", {}, {"CLAUDECODE": "1"}, ["claude"], "this Claude Code session"),
                 ("codex-session", {}, {"CODEX_THREAD_ID": "thread"}, ["codex"], "this Codex session"),
                 ("session-and-rules", {"AGENTS.md": "rules\n"}, {"CLAUDECODE": "1"}, both,
                  "this Claude Code session and AGENTS.md (read by both)")]
        for name, files, env, clients, reason in cases:
            with self.subTest(name):
                printout = self.fresh(name, files, **env)
                self.assertIn(f", chosen from {reason}. Start a session to begin. Change the client with: "
                              "uvx sako init --client claude|codex|none.", printout)
                self.assertEqual(sako.installation(self.root)["clients"], clients)
                for client in both:
                    self.assertEqual((self.root / sako.hook_file(client)).exists(), client in clients, client)
                self.assertEqual((self.root / ".claude/skills/sako/SKILL.md").exists(), "claude" in clients)
                self.assertTrue((self.root / ".agents/skills/sako/SKILL.md").is_file())
        linked = fixture.make_repo(Path(self.workspace.name) / "linked-rules", {"AGENTS.md": "rules\n"})
        (linked / "CLAUDE.md").symlink_to("AGENTS.md")
        self.root = linked
        self.assertIn("chosen from CLAUDE.md and AGENTS.md (read by both).", fixture.succeeds(fixture.seed(linked)))

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_a_missing_choice_is_detected_again_and_a_recorded_one_is_kept(self):
        self.fresh("later")
        fixture.write(self.root, {"AGENTS.md": "rules\n"})
        self.assertIn("chosen from AGENTS.md (read by both)", fixture.succeeds(fixture.seed(self.root)))
        self.assertEqual(sako.installation(self.root)["clients"], ["claude", "codex"])
        (self.root / "AGENTS.md").unlink()
        for flags in ((), ("--update",)):
            fixture.succeeds(fixture.seed(self.root, *flags))
            self.assertEqual(sako.installation(self.root)["clients"], ["claude", "codex"])

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_the_client_flag_replaces_the_choice_and_clears_what_it_no_longer_needs(self):
        printout = self.fresh("switch", {"AGENTS.md": "rules\n"})
        self.assertIn("Hooks written: Claude Code (.claude/settings.local.json) and Codex (.codex/hooks.json, "
                      "which runs them only after you trust them in Codex), chosen from AGENTS.md", printout)
        claude_skill = self.root / ".claude/skills/sako/SKILL.md"
        self.assertIn("chosen from --client.", fixture.succeeds(fixture.seed(self.root, "--client", "codex")))
        self.assertEqual(sako.installation(self.root)["clients"], ["codex"])
        self.assertFalse((self.root / sako.hook_file("claude")).exists())
        self.assertFalse(claude_skill.exists(), "a skill the choice no longer needs is removed")
        self.assertTrue((self.root / sako.hook_file("codex")).exists())
        fixture.succeeds(fixture.seed(self.root, "--client", "claude"))
        claude_skill.write_text("A local edit\n")
        before = self.files()
        refusal = fixture.seed(self.root, "--client", "codex")
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn(".claude/skills/sako/SKILL.md is no longer part of this kit and has local content", refusal.stderr)
        self.assertEqual(self.files(), before, "an edited file is left to its owner")
        claude_skill.unlink()
        self.assertIn("Hooks: none, your choice.", fixture.succeeds(fixture.seed(self.root, "--client", "none")))
        self.assertEqual(sako.installation(self.root)["clients"], [])
        self.assertFalse((self.root / sako.hook_file("codex")).exists())

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_named_clients_install_working_hooks_by_default(self):
        host = self.host()
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent):
                self.root = fixture.make_repo(Path(self.workspace.name) / agent)
                fixture.succeeds(fixture.seed(self.root, "--client", agent))
                self.assertEqual(sako.installation(self.root)["clients"], [agent])
                other = "claude" if agent == "codex" else "codex"
                self.assertFalse((self.root / sako.hook_file(other)).exists())
                before = self.files()
                fixture.succeeds(fixture.seed(self.root))
                self.assertEqual(self.files(), before)
                fixture.commit_all(self.root, "install the selected adapter")
                events = json.loads((self.root / sako.hook_file(agent)).read_text())["hooks"]
                self.assertEqual(set(events), {"SessionStart", "Stop", "SessionEnd"})

                def event(name):
                    hooks = events[name][0]["hooks"]
                    self.assertEqual(len(hooks), 1)
                    return subprocess.run(["sh", "-c", hooks[0]["command"]], cwd=self.root,
                                          env=dict(fixture.ENV, SAKO_AGENT_PID=str(host.pid),
                                                   CLAUDE_PROJECT_DIR=str(self.root)),
                                          input=json.dumps({"hook_event_name": name, "session_id": "hook-session"}),
                                          capture_output=True, text=True)

                self.assertIn("your marker", fixture.succeeds(event("SessionStart")))
                fixture.succeeds(fixture.installed(self.root, "claim", "T-1", session="hook-session"))
                fixture.write(self.root, {"src/app.py": "print(2)\n"})
                fixture.succeeds(fixture.installed(self.root, "close", "T-1", "--evidence",
                                                  "Inspected the sample output", session="hook-session"))
                self.assertEqual(event("Stop").returncode, 2, "uncommitted closed work produces feedback")
                fixture.commit_all(self.root, "record the inspected output")
                fixture.succeeds(event("Stop"))
                fixture.succeeds(event("SessionEnd"))
                self.assertEqual(sako.read_presence(sako.resolve_roots(self.root)), [])

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_opt_out_preserves_unrelated_settings_and_survives_updates(self):
        unrelated = {".codex/hooks.json": '{"hooks": {"Stop": [{"hooks": [{"command": "echo existing", "type": "command"}]}]}}\n',
                     ".claude/settings.json": '{"permissions": {"allow": []}}\n',
                     ".codex/config.toml": "# Existing client configuration\n"}
        fixture.write(self.root, unrelated)
        fixture.succeeds(fixture.seed(self.root, "--client", "none"))
        fixture.succeeds(fixture.seed(self.root, "--update"))
        self.assertEqual(sako.installation(self.root)["clients"], [])
        for name, text in unrelated.items():
            self.assertEqual((self.root / name).read_text(), text)
        before = self.files()
        fixture.succeeds(fixture.seed(self.root))
        self.assertEqual(self.files(), before)
        fixture.succeeds(fixture.seed(self.root, "--client", "claude", "--client", "codex"))
        self.assertEqual(sako.installation(self.root)["clients"], ["claude", "codex"])
        fixture.succeeds(fixture.seed(self.root, "--client", "none"))
        for name, text in unrelated.items():
            actual = (self.root / name).read_text()
            self.assertEqual(actual if name.endswith(".toml") else json.loads(actual),
                             text if name.endswith(".toml") else json.loads(text))
        before = self.files()
        fixture.succeeds(fixture.seed(self.root, "--update"))
        self.assertEqual(self.files(), before, "updates preserve a later opt-out too")
        fixture.succeeds(fixture.installed(self.root, "remove"))
        for name, text in unrelated.items():
            actual = (self.root / name).read_text()
            self.assertEqual(actual if name.endswith(".toml") else json.loads(actual),
                             text if name.endswith(".toml") else json.loads(text))

    def test_older_release_manifest_refuses_before_any_write(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "codex"))
        path = self.root / ".sako/install.json"
        current = json.loads(path.read_text())
        previous = {"version": "0.4.0", "files": {"scripts/sako.py": "0" * 64, ".sako/SAKO.md": "0" * 64},
                    "routers": {}, "agents": ["codex"], "hooks": ["codex"], "no_hooks": False}
        before_clients = dict({k: v for k, v in current.items() if k != "clients"},
                              agents=["codex"], hooks=["codex"], no_hooks=False)
        older = (before_clients, previous)
        for manifest in older:
            with self.subTest(keys=sorted(manifest)):
                path.write_text(json.dumps(manifest))
                before = self.files()
                for outcome in (fixture.seed(self.root, "--update"), fixture.installed(self.root, "remove")):
                    self.assertEqual(outcome.returncode, 3, outcome.stdout + outcome.stderr)
                    self.assertIn("older SAKO release", outcome.stderr)
                    self.assertEqual(self.files(), before)

    def test_version_change_needs_a_deliberate_update(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "codex"))
        path = self.root / ".sako/install.json"
        path.write_text(json.dumps(dict(json.loads(path.read_text()), version="0.3.9")))
        before = self.files()
        refusal = fixture.seed(self.root)
        self.assertEqual(refusal.returncode, 3, refusal.stdout + refusal.stderr)
        self.assertIn("init --update", refusal.stderr)
        self.assertEqual(self.files(), before)
        fixture.succeeds(fixture.seed(self.root, "--update"))
        self.assertEqual(sako.installation(self.root)["version"], sako.VERSION)

    def test_repeat_install_restores_a_missing_kit_file(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "codex"))
        method = self.root / ".sako/SAKO.md"
        original = method.read_bytes()
        method.unlink()
        fixture.succeeds(fixture.seed(self.root))
        self.assertEqual(method.read_bytes(), original)

    def test_removal_keeps_a_user_command_sharing_the_hook_entry(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "claude"))
        path = self.root / sako.hook_file("claude")
        settings = json.loads(path.read_text())
        for entries in settings["hooks"].values():
            entries[0]["hooks"].append({"type": "command", "command": "echo existing"})
        path.write_text(json.dumps(settings))
        fixture.succeeds(fixture.installed(self.root, "remove"))
        remaining = json.loads(path.read_text())["hooks"]
        self.assertEqual(set(remaining), {"SessionStart", "Stop", "SessionEnd"})
        for entries in remaining.values():
            self.assertEqual([h["command"] for entry in entries for h in entry["hooks"]], ["echo existing"])

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_hook_opt_out_preflights_removal_before_changing_any_files(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "claude", "--client", "codex"))
        path = self.root / ".codex/hooks.json"
        settings = json.loads(path.read_text())
        settings["hooks"]["Stop"] = False
        path.write_text(json.dumps(settings))
        before = self.files()
        result = fixture.seed(self.root, "--client", "none")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("invalid hook settings", result.stderr)
        self.assertEqual(self.files(), before)

    def test_invalid_client_records_refuse_before_writing(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "codex"))
        path = self.root / ".sako/install.json"
        manifest = json.loads(path.read_text())
        for record in (True, "codex", ["cursor"], 0):
            with self.subTest(record=record):
                path.write_text(json.dumps(dict(manifest, clients=record)))
                before = self.files()
                result = fixture.seed(self.root, "--update")
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn("invalid", result.stderr)
                self.assertEqual(self.files(), before)

    def test_client_none_stands_alone(self):
        before = self.files()
        result = fixture.seed(self.root, "--client", "codex", "--client", "none")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("--client none stands alone", result.stderr)
        self.assertEqual(self.files(), before)
        for retired in (("--agent", "claude"), ("--hooks",), ("--no-hooks",), ("--skill",), ("--target", ".")):
            outcome = fixture.seed(self.root, *retired)
            self.assertEqual(outcome.returncode, 2, retired)
            self.assertIn("unrecognized arguments", outcome.stderr)

    @unittest.skipIf(fixture.WINDOWS, fixture.LEASE)
    def test_conversations_sharing_a_host_keep_separate_presence_and_ownership(self):
        host = self.host()
        self.cli("start", session="conversation-one", pid=host.pid)
        self.cli("claim", "T-1", session="conversation-one")
        roots = sako.resolve_roots(self.root)
        first = sako.my_entry(roots, "conversation-one")
        self.cli("start", session="conversation-two", pid=host.pid)
        self.assertEqual({entry["session_id"] for entry in sako.read_presence(roots)},
                         {"conversation-one", "conversation-two"})
        refusal = self.cli("claim", "T-1", "--takeover", "Inspect earlier work",
                           session="conversation-two", expected=3)
        self.assertIn("held by a live session", refusal)
        self.cli("add", "Record the sample", "--scope", "docs/")
        self.cli("claim", "T-10", session="conversation-two")
        self.cli("start", session="conversation-one", pid=host.pid)
        resumed = sako.my_entry(roots, "conversation-one")
        for field in ("marker", "started", "work_start_rev"):
            self.assertEqual(resumed[field], first[field])
        self.cli("end", session="conversation-two")
        self.assertEqual([entry["session_id"] for entry in sako.read_presence(roots)], ["conversation-one"])
        self.cli("close", "T-1", "--evidence", "Recorded the requested result", session="conversation-one")
        self.cli("end", session="conversation-one")
        self.assertEqual(sako.read_presence(roots), [])

    def test_a_claude_code_session_needs_no_session_flag(self):
        host = self.host()

        def run(*args, **env):
            return subprocess.run([sys.executable, str(fixture.SAKO), *args], cwd=self.root, text=True,
                                  capture_output=True, env=dict(fixture.ENV, SAKO_AGENT_PID=str(host.pid), **env))

        fixture.succeeds(run("start", CLAUDE_CODE_SESSION_ID="conversation-main"))
        fixture.succeeds(run("claim", "T-1", CLAUDE_CODE_SESSION_ID="conversation-main"))
        self.assertIn(sako.marker_for("conversation-main"), (self.root / ".sako/work/TASKS.md").read_text())
        refusal = run("close", "T-1", "--evidence", "Checked", CLAUDE_CODE_SESSION_ID="subagent-own-id")
        self.assertEqual(refusal.returncode, 3, refusal.stderr)
        self.assertIn("session 'subagent-own-id' has not started here; pass --session", refusal.stderr)
        explicit = run("close", "T-1", "--evidence", "Checked", "--session", "conversation-main",
                       CLAUDE_CODE_SESSION_ID="subagent-own-id")
        fixture.succeeds(explicit)
        fixture.succeeds(run("status", SAKO_SESSION="conversation-main", CLAUDE_CODE_SESSION_ID="other"))
        self.assertIn("needs --session <id>, SAKO_SESSION, or the session ID of Claude Code or Codex", run("end").stderr)

    def test_a_codex_session_needs_no_session_flag_unless_it_runs_inside_another_client(self):
        host = self.host()

        def run(*args, **env):
            return subprocess.run([sys.executable, str(fixture.SAKO), *args], cwd=self.root, text=True,
                                  capture_output=True, env=dict(fixture.ENV, SAKO_AGENT_PID=str(host.pid), **env))

        fixture.succeeds(run("start", CODEX_THREAD_ID="codex-main"))
        fixture.succeeds(run("claim", "T-1", CODEX_THREAD_ID="codex-main"))
        self.assertIn(sako.marker_for("codex-main"), (self.root / ".sako/work/TASKS.md").read_text())
        nested = run("close", "T-1", "--evidence", "Checked", CODEX_THREAD_ID="codex-main", CLAUDE_CODE_SESSION_ID="outer")
        self.assertEqual(nested.returncode, 3, nested.stderr)
        self.assertIn("(not both at once, as when one client runs inside the other)", nested.stderr)
        fixture.succeeds(run("close", "T-1", "--evidence", "Checked", "--session", "codex-main",
                             CODEX_THREAD_ID="codex-main", CLAUDE_CODE_SESSION_ID="outer"))

    def test_a_codex_subagent_uses_the_session_its_hooks_use(self):
        """Codex hooks and CODEX_SESSION_ID carry the root session; a subagent's CODEX_THREAD_ID is its own."""
        env = dict(fixture.ENV, SAKO_AGENT_PID=str(self.host().pid), CODEX_SESSION_ID="codex-root")
        for args, extra in ((["start"], {}), (["claim", "T-1"], {"CODEX_THREAD_ID": "codex-subagent"})):
            fixture.succeeds(subprocess.run([sys.executable, str(fixture.SAKO), *args], cwd=self.root, text=True,
                                            capture_output=True, env=dict(env, **extra)))
        self.assertIn(sako.marker_for("codex-root"), (self.root / ".sako/work/TASKS.md").read_text())
        nested = subprocess.run([sys.executable, str(fixture.SAKO), "status"], cwd=self.root, text=True, capture_output=True,
                                env=dict(env, CLAUDE_CODE_SESSION_ID="outer"))  # one client inside the other: no default
        self.assertIn("your rows: none", fixture.succeeds(nested))

    def hidden(self, session, seen):
        """A presence entry whose host another PID namespace hides, as a client's sandbox shows the hook's entry."""
        path = sako.presence_dir(sako.resolve_roots(self.root)) / f"{sako.session_key(session)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"marker": sako.marker_for(session), "session_id": session, "pid": 2 ** 22,
                                    "ns": "pid:[elsewhere]", "seen": seen, "started": "2026-01-01T00:00:00+0000",
                                    "started_at": seen, "work_root": str(self.root), "branch": "main",
                                    "work_start_rev": fixture.git(self.root, "rev-parse", "HEAD"), "dirty_at_start": {}}))

    def test_hook_entries_are_known_by_pattern_whatever_their_python_or_redirect(self):
        other = {"type": "command", "command": "echo mine"}
        old = {"type": "command", "command": 'exec python "/x/.sako/sako.py" hook claude >/dev/null'}
        for command in ("python /x/.sako/sako.py.bak hook claude", "echo x.sako/sako.py hook claude", "sako.py hook claude-extra"):
            self.assertIsNone(re.search(sako.HOOK_OWNED.format("claude"), command), command)
        events = ("SessionStart", "SessionEnd", "Stop")
        fixture.write(self.root, {".claude/settings.local.json": json.dumps(
            {"hooks": {event: [{"hooks": [dict(old), dict(other)]}] for event in events}})})
        fixture.succeeds(fixture.seed(self.root, "--client", "claude"))
        hooks = json.loads((self.root / ".claude/settings.local.json").read_text())["hooks"]
        for event in events:
            commands = [h["command"] for entry in hooks[event] for h in entry["hooks"]]
            self.assertEqual(sorted(commands), sorted(["echo mine", sako.hook_command("claude")]), event)
        fixture.succeeds(fixture.installed(self.root, "remove"))
        hooks = json.loads((self.root / ".claude/settings.local.json").read_text())["hooks"]
        self.assertEqual([h["command"] for e in events for entry in hooks[e] for h in entry["hooks"]], ["echo mine"] * 3)

    def test_on_windows_codex_gets_no_hooks_and_a_note_says_why(self):
        self.root, printed = fixture.make_repo(Path(self.workspace.name) / "windows", {"AGENTS.md": "rules\n"}), []
        with patch.object(sako.sys, "platform", "win32"), patch.dict(os.environ, {"CLAUDECODE": "", "CODEX_THREAD_ID": ""}), \
                patch.object(sako, "ledger_lock", lambda *args, **kwargs: contextlib.nullcontext()):
            sako.init(self.root, [], out=printed.append)
            linked = self.root.parent / "windows-linked"
            fixture.git(self.root, "worktree", "add", "-q", str(linked))
            sako.init(linked, [], out=printed.append)
        self.assertEqual(sum("Codex on Windows: no hooks written; run start, check --gate and end yourself." in line
                             for line in printed), 2, "the main checkout and the linked worktree both say so")
        self.assertEqual(sako.installation(self.root)["clients"], ["claude", "codex"], "the recorded choice stays whole")
        for checkout in (self.root, linked):
            self.assertFalse((checkout / ".codex/hooks.json").exists(), checkout.name)
            self.assertTrue((checkout / ".claude/settings.local.json").is_file(), checkout.name)

    def test_a_session_whose_host_is_hidden_keeps_working_and_is_taken_over_only_with_a_reason(self):
        self.hidden("sandboxed", int(time.time()))
        self.cli("add", "Record the sample", "--scope", "docs/")
        for task in ("T-1", "T-10"):
            self.cli("claim", task, session="sandboxed")
        host = self.host()
        self.cli("start", session="host-session", pid=host.pid)
        self.assertIn("unverified from here (counts as live until", self.cli("status", session="host-session"))
        self.assertIn("ledger: consistent", self.cli("check"), "within its lease it counts as live")
        waiting = self.cli("next")
        self.assertIn(f"T-10  claimed by {sako.marker_for('sandboxed')}", waiting)
        self.assertNotIn("(not live)", waiting)
        fixture.write(self.root, {"docs/sample.md": "the sandboxed session's edit\n"})
        self.assertIn("in T-10's scope, claimed by unverified peer", self.cli("check", "--gate", session="host-session"))
        (self.root / "docs/sample.md").unlink()
        refusal = self.cli("claim", "T-1", session="host-session", expected=3)
        self.assertIn("which SAKO cannot verify from here (a sandbox, or Windows, hides its process); if it stopped", refusal)
        taken = self.cli("claim", "T-1", "--takeover", "its sandbox session ended; inspected its work", session="host-session")
        self.assertIn("T-1: claimed; its previous holder could not be verified from here", taken)
        self.assertIn(f"(took over from {sako.marker_for('sandboxed')}, unverified: its sandbox session ended",
                      (self.root / ".sako/work/TASKS.md").read_text())
        self.hidden("sandboxed", int(time.time()) - sako.LEASE - 1)
        self.assertIn("T-10 is claimed by", self.cli("check", expected=1), "past its lease the claim is stale")
        self.assertTrue(sako.my_entry(sako.resolve_roots(self.root), "sandboxed"), "and its starting facts stay on disk")

    @unittest.skipUnless(shutil.which("bwrap") and subprocess.run(
        ["bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc", "true"], capture_output=True).returncode == 0,
        "needs bubblewrap with unprivileged PID namespaces, as Codex's sandbox uses")
    def test_presence_a_hook_wrote_survives_commands_in_a_pid_namespace(self):
        host = self.host()
        payload = json.dumps({"session_id": "codex-thread", "hook_event_name": "SessionStart"})
        self.assertEqual(fixture.cli(self.root, "hook", pid=host.pid, stdin=payload)[0], 0)

        def boxed(*args):
            return subprocess.run(["bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc", "--chdir",
                                   str(self.root), sys.executable, str(fixture.SAKO), *args], capture_output=True,
                                  text=True, env=dict(fixture.ENV, CODEX_THREAD_ID="codex-thread"))

        fixture.succeeds(boxed("claim", "T-1"))
        fixture.succeeds(boxed("start"))
        fixture.succeeds(boxed("start", "--session", "codex-other"))
        roots = sako.resolve_roots(self.root)
        tokens = [subprocess.run(["bwrap", "--dev-bind", "/", "/", "--unshare-pid", "--proc", "/proc", sys.executable, "-c",
                                  "import sako; print(sako.PID_NS)"], capture_output=True, text=True,
                                 cwd=fixture.SAKO.parent).stdout for _ in range(2)]
        self.assertNotEqual(tokens[0], tokens[1], "two sandboxes never share a token, even when the kernel reuses the number")
        self.assertEqual(sako.my_entry(roots, "codex-thread")["pid"], host.pid, "a start inside keeps the hook's host")
        self.assertEqual(sako.my_entry(roots, "codex-other")["pid"], 0, "PID 1 inside is never recorded")
        host.terminate()
        host.wait()
        self.assertIn("T-1 is claimed by", self.cli("check", expected=1), "the host judges the hook's entry by its PID")

    @unittest.skipIf(fixture.WINDOWS, fixture.LEASE)
    def test_dead_shared_host_sweeps_all_its_conversations(self):
        host = self.host()
        for session in ("conversation-one", "conversation-two"):
            self.cli("start", session=session, pid=host.pid)
        self.cli("claim", "T-1", session="conversation-one")
        roots = sako.resolve_roots(self.root)
        self.assertEqual(len(sako.read_presence(roots)), 2)
        host.terminate()
        host.wait()
        self.assertEqual(sako.read_presence(roots), [])
        self.assertIn("not a live session", self.cli("check", expected=1))

    def test_install_leaves_project_and_client_instructions_untouched(self):
        untouched = {"CLAUDE.md": "# Project rules\nUse the current specification.\n",
                     "AGENTS.md": "# Project rules\nKeep the public interface stable.\n",
                     ".cursor/rules/project.mdc": "---\nalwaysApply: true\n---\nKeep the public interface stable.\n",
                     ".cursor/hooks.json": '{"version": 1, "hooks": {}}\n',
                     ".claude/settings.json": '{"permissions": {"allow": []}}\n'}
        fixture.write(self.root, untouched)
        fixture.commit_all(self.root, "project rules")
        for flags in (("--client", "none",), ("--client", "claude", "--client", "codex")):
            fixture.succeeds(fixture.seed(self.root, *flags))
            self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "", "nothing tracked changes")
            before = self.files()
            fixture.succeeds(fixture.seed(self.root))
            self.assertEqual(self.files(), before)
        self.cli("remove")
        for relative, content in untouched.items():
            self.assertEqual((self.root / relative).read_text(), content)
        self.assertEqual(fixture.git(self.root, "status", "--porcelain"), "")

    def test_shared_skill_lifecycle_needs_no_named_agent(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "none"))
        shared = self.root / ".agents/skills/sako/SKILL.md"
        canonical = (fixture.SAKO.parent / "skills/sako/SKILL.md").read_text()
        self.assertEqual(shared.read_text(), canonical)
        manifest_path = self.root / ".sako/install.json"
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["clients"], [])
        self.assertFalse((self.root / ".codex").exists())
        self.assertFalse((self.root / ".claude").exists())
        before = self.files()
        fixture.succeeds(fixture.seed(self.root))
        self.assertEqual(self.files(), before, "repeat install retains the shared selection")
        previous = "Previously managed shared entry\n"
        shared.write_text(previous)
        manifest["files"][".agents/skills/sako/SKILL.md"] = sako.digest(previous)
        manifest_path.write_text(json.dumps(manifest))
        fixture.succeeds(fixture.seed(self.root, "--update"))
        self.assertEqual(shared.read_text(), canonical, "update refreshes a previously selected shared entry")
        shared.unlink()
        fixture.succeeds(fixture.seed(self.root))
        self.assertEqual(shared.read_text(), canonical, "repeat install restores a missing owned entry")
        self.cli("remove")
        self.assertFalse(shared.exists())
        self.assertTrue((self.root / ".sako/work/TASKS.md").exists())
        self.assertTrue((self.root / ".sako/config.json").exists())

    def test_shared_skill_conflict_refuses_before_changing_project_files(self):
        fixture.write(self.root, {".agents/skills/sako/SKILL.md": "Local workflow that must be preserved\n"})
        before = self.files()
        outcome = fixture.seed(self.root, "--client", "none")
        self.assertEqual(outcome.returncode, 3, outcome.stdout + outcome.stderr)
        self.assertIn("local content", outcome.stderr)
        self.assertEqual(self.files(), before)

    @unittest.skipUnless(fixture.symlinks(), "creating symlinks needs a privilege here")
    def test_client_files_are_judged_by_their_real_paths(self):
        fixture.write(self.root, {"CLAUDE.md": "rules\n", "team/skills/README.md": "Shared skills\n"})
        (self.root / ".agents/skills").mkdir(parents=True)
        (self.root / ".claude").mkdir()
        (self.root / ".claude/skills").symlink_to("../.agents/skills", target_is_directory=True)
        fixture.succeeds(fixture.seed(self.root))
        shared = self.root / ".agents/skills/sako/SKILL.md"
        fixture.succeeds(fixture.seed(self.root, "--client", "codex"))
        self.assertTrue(shared.is_file(), "dropping Claude keeps the shared skill behind its linked folder")
        team = fixture.make_repo(Path(self.workspace.name) / "team", {"team/skills/README.md": "Shared skills\n"})
        (team / ".claude").mkdir()
        (team / ".claude/skills").symlink_to("../team/skills", target_is_directory=True)
        fixture.succeeds(fixture.seed(team, "--client", "claude"))
        fixture.git(team, "add", "-f", "team/skills/sako/SKILL.md")
        fixture.git(team, "commit", "-qm", "share the skill with the team")
        fixture.succeeds(fixture.seed(team, "--client", "codex"))
        self.assertTrue((team / "team/skills/sako/SKILL.md").is_file(), "a tracked file is never deleted")
        self.assertNotIn("team/skills", fixture.git(team, "status", "--porcelain"))

    @unittest.skipUnless(fixture.symlinks(), "creating symlinks needs a privilege here")
    def test_client_files_never_land_outside_the_repository(self):
        outside = Path(self.workspace.name) / "shared-claude"
        outside.mkdir()
        (self.root / ".claude").symlink_to(outside, target_is_directory=True)
        printout = fixture.succeeds(fixture.seed(self.root, "--client", "claude"))
        for relative in (".claude/settings.local.json", ".claude/skills/sako/SKILL.md"):
            self.assertIn(f"{relative} leads outside this repository, so SAKO left it alone.", printout)
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / ".claude").unlink()
        fixture.succeeds(fixture.seed(self.root))
        self.assertTrue((self.root / ".claude/skills/sako/SKILL.md").is_file())
        (self.root / ".claude/skills/sako/SKILL.md").unlink()
        (self.root / ".claude/skills/sako").rmdir()
        (self.root / ".claude/skills").rmdir()
        (self.root / ".claude/skills").symlink_to(outside, target_is_directory=True)
        self.assertIn("leads outside this repository", fixture.succeeds(fixture.seed(self.root)))
        self.assertNotIn(".claude/skills/sako/SKILL.md", sako.installation(self.root)["files"])
        fixture.succeeds(fixture.installed(self.root, "remove"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_client_that_is_not_chosen_keeps_its_own_hook_files(self):
        linked = Path(self.workspace.name) / "linked"
        fixture.git(self.root, "worktree", "add", "-q", str(linked))
        fixture.write(linked, {".codex/hooks.json": '{"hooks": {"Stop": false}}'})
        fixture.write(self.root, {".codex/hooks.json": "not json"})
        fixture.succeeds(fixture.seed(self.root, "--client", "claude"))
        fixture.succeeds(fixture.installed(self.root, "remove"))
        self.assertEqual((linked / ".codex/hooks.json").read_text(), '{"hooks": {"Stop": false}}')
        self.assertEqual((self.root / ".codex/hooks.json").read_text(), "not json")

    @unittest.skipIf(fixture.WINDOWS, fixture.NO_CODEX)
    def test_named_clients_share_the_one_canonical_skill(self):
        fixture.succeeds(fixture.seed(self.root, "--client", "codex"))
        shared = self.root / ".agents/skills/sako/SKILL.md"
        specific = self.root / ".claude/skills/sako/SKILL.md"
        self.assertTrue(shared.is_file())
        self.assertFalse(specific.exists())
        fixture.succeeds(fixture.seed(self.root, "--client", "claude", "--client", "codex"))
        self.assertEqual(shared.read_bytes(), specific.read_bytes())
        self.assertEqual(sako.installation(self.root)["clients"], ["claude", "codex"])
        self.assertTrue((self.root / ".codex/hooks.json").exists())
        self.assertTrue((self.root / ".claude/settings.local.json").exists())



if __name__ == "__main__":
    unittest.main()
