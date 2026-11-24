"""Shared entry points, conversation identity across agent hosts, and presence by last seen."""

from pathlib import Path
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
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

    def tearDown(self):
        self.workspace.cleanup()

    def cli(self, *args, session=None, expected=0):
        code, out, err = fixture.cli(self.root, *args, session=session)
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
        self.assertIn("Hooks: Claude Code (.claude/settings.local.json) and Codex (.codex/hooks.json, "
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
                                          env=dict(fixture.ENV, CLAUDE_PROJECT_DIR=str(self.root)),
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

    def test_two_conversations_keep_separate_presence_and_ownership(self):
        self.cli("start", session="conversation-one")
        self.cli("claim", "T-1", session="conversation-one")
        roots = sako.resolve_roots(self.root)
        first = sako.my_entry(roots, "conversation-one")
        self.cli("start", session="conversation-two")
        self.assertEqual({entry["session_id"] for entry in sako.read_presence(roots)},
                         {"conversation-one", "conversation-two"})
        refusal = self.cli("claim", "T-1", "--takeover", "Inspect earlier work",
                           session="conversation-two", expected=3)
        self.assertIn(f"T-1 is held by {sako.marker_for('conversation-one')}, a live session last seen ", refusal)
        self.cli("add", "Record the sample", "--scope", ".sako/work/sample.md")  # records work, beside the checkout's writer
        self.cli("claim", "T-10", session="conversation-two")
        self.cli("start", session="conversation-one")
        resumed = sako.my_entry(roots, "conversation-one")
        for field in ("marker", "started", "work_start_rev"):
            self.assertEqual(resumed[field], first[field])
        self.cli("end", session="conversation-two")
        self.assertEqual([entry["session_id"] for entry in sako.read_presence(roots)], ["conversation-one"])
        self.cli("close", "T-1", "--evidence", "Recorded the requested result", session="conversation-one")
        self.cli("end", session="conversation-one")
        self.assertEqual(sako.read_presence(roots), [])

    def test_a_claude_code_session_and_its_subagents_need_no_session_flag(self):
        """In Claude Code, as in Codex, a subagent's shell carries its parent's session ID, so a command there without
        --session acts as the parent. A helper given an ID of its own passes it on every command."""
        def run(*args, **env):
            return subprocess.run([sys.executable, str(fixture.SAKO), *args], cwd=self.root, text=True,
                                  capture_output=True, env=dict(fixture.ENV, **env))

        parent, main = sako.marker_for("conversation-main"), {"CLAUDE_CODE_SESSION_ID": "conversation-main"}
        subagent = dict(main, CLAUDE_CODE_CHILD_SESSION="1")  # main sessions' shells set it too, so it tells nothing apart
        fixture.succeeds(run("start", **main))
        fixture.succeeds(run("claim", "T-1", **main))
        self.assertIn(parent, (self.root / ".sako/work/TASKS.md").read_text())
        self.assertIn(f"you: {parent} since ", fixture.succeeds(run("status", **subagent)), "the subagent is its parent here")
        self.cli("add", "Record the sample", "--scope", "src/sample/")
        refusal = run("claim", "T-10", "--session", "helper-own-id", **subagent)
        self.assertEqual(refusal.returncode, 3, refusal.stderr)
        self.assertIn("session 'helper-own-id' has not started here; pass --session", refusal.stderr)
        fixture.succeeds(run("start", "--session", "helper-own-id", **subagent))
        refusal = run("claim", "T-10", "--session", "helper-own-id", **subagent)
        self.assertEqual(refusal.returncode, 3, refusal.stderr)
        self.assertIn(f"T-10's scope overlaps T-1 (src/), claimed by {parent}; wait for it to close, or ", refusal.stderr,
                      "a helper on its own ID is another holder, refused inside its parent's claim")
        fixture.succeeds(run("close", "T-1", "--evidence", "Checked", **subagent))
        receipt = sako.parse_rows({"done": (self.root / ".sako/work/DONE.md").read_text()})[0].evidence
        self.assertTrue(receipt.startswith(f"Checked [{parent} since "), "the subagent closes as its parent")
        self.assertEqual(fixture.succeeds(run("end", **subagent)), "sako: presence ended\n")
        self.assertIsNone(sako.my_entry(sako.resolve_roots(self.root), "conversation-main"), "and ends its parent's presence")
        fixture.succeeds(run("status", SAKO_SESSION="conversation-main", CLAUDE_CODE_SESSION_ID="other"))
        self.assertIn("needs --session <id>, SAKO_SESSION, or the session ID of Claude Code or Codex", run("end").stderr)

    def test_a_codex_session_needs_no_session_flag_unless_it_runs_inside_another_client(self):
        def run(*args, **env):
            return subprocess.run([sys.executable, str(fixture.SAKO), *args], cwd=self.root, text=True,
                                  capture_output=True, env=dict(fixture.ENV, **env))

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
        env = dict(fixture.ENV, CODEX_SESSION_ID="codex-root")
        for args, extra in ((["start"], {}), (["claim", "T-1"], {"CODEX_THREAD_ID": "codex-subagent"})):
            fixture.succeeds(subprocess.run([sys.executable, str(fixture.SAKO), *args], cwd=self.root, text=True,
                                            capture_output=True, env=dict(env, **extra)))
        self.assertIn(sako.marker_for("codex-root"), (self.root / ".sako/work/TASKS.md").read_text())
        nested = subprocess.run([sys.executable, str(fixture.SAKO), "status"], cwd=self.root, text=True, capture_output=True,
                                env=dict(env, CLAUDE_CODE_SESSION_ID="outer"))  # one client inside the other: no default
        self.assertIn("your rows: none", fixture.succeeds(nested))

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

    def test_update_replaces_the_previous_release_hook_command(self):
        previous = ('c="$(git rev-parse --path-format=absolute --git-common-dir)"; k="${c%/.git}/.sako/sako.py"; '
                    '[ -f "$k" ] || { echo "sako: no kit at $k; run: uvx sako init" >&2; exit 1; }; '
                    'exec python3 "$k" hook claude')
        fixture.succeeds(fixture.seed(self.root, "--client", "claude"))
        linked, unwired = self.root.parent / "project-linked", self.root.parent / "project-unwired"
        for checkout in (linked, unwired):
            fixture.git(self.root, "worktree", "add", "-q", str(checkout))
        fixture.succeeds(fixture.seed(linked))
        for checkout in (self.root, linked):
            path = checkout / sako.hook_file("claude")
            settings = json.loads(path.read_text())
            for entries in settings["hooks"].values():
                entries[0]["hooks"][0]["command"] = previous
            settings["hooks"]["Stop"].insert(0, {"hooks": [{"type": "command", "command": "echo mine"}]})
            path.write_text(json.dumps(settings))
        fixture.succeeds(fixture.seed(self.root, "--update"))
        for checkout in (self.root, linked):
            for event, entries in json.loads((checkout / sako.hook_file("claude")).read_text())["hooks"].items():
                commands = [h["command"] for entry in entries for h in entry["hooks"]]
                self.assertEqual(commands, ["echo mine"] * (event == "Stop") + [sako.hook_command("claude")],
                                 f"{checkout.name} {event}: a linked worktree's entries are refreshed from the main checkout")
        self.assertFalse((unwired / sako.hook_file("claude")).exists(), "a worktree that has no SAKO hooks gets none")

    def test_a_tracked_settings_file_is_left_unchanged_and_the_note_gives_the_content_to_write(self):
        team, hooks = '{"permissions": {"allow": []}}\n', sako.hook_file("claude")
        fixture.write(self.root, {hooks: team})
        fixture.git(self.root, "add", "-f", hooks)  # forced, since Claude Code may list the file in a global ignore
        fixture.commit_all(self.root, "the team's settings")
        linked = self.root.parent / "project-team"  # a branch whose tracked file holds SAKO's hook from an older release
        fixture.git(self.root, "worktree", "add", "-q", "-b", "team", str(linked))
        settings = json.loads(sako.edit_hooks(team, "claude"))
        for entries in settings["hooks"].values():
            entries[0]["hooks"][0]["command"] = "python3 .sako/sako.py hook claude"
        older = json.dumps(settings, indent=2) + "\n"
        fixture.write(linked, {hooks: older})
        fixture.git(linked, "add", "-f", hooks)
        fixture.commit_all(linked, "the team's hooks")
        printout = fixture.succeeds(fixture.seed(self.root, "--client", "claude"))
        self.assertEqual(((self.root / hooks).read_text(), (linked / hooks).read_text()), (team, older), "tracked files never change")
        self.assertIn("Hooks: Claude Code (.claude/settings.local.json), chosen from --client.", printout)
        lead = ("Claude Code: .claude/settings.local.json is tracked, so SAKO left it unchanged; run start, check --gate and end "
                "explicitly, or give it this content yourself:\n")
        self.assertIn(lead, printout)
        self.assertEqual(json.JSONDecoder().raw_decode(printout.split(lead)[1])[0], json.loads(sako.edit_hooks(team, "claude")),
                         "the file's own settings with SAKO's hooks added")
        self.assertIn(f"Claude Code: {(linked / hooks).resolve().as_posix()} is tracked, so SAKO left it unchanged;", printout,
                      "another checkout's file is named in full")

    def test_remove_or_a_dropped_client_leaves_a_tracked_settings_file_and_says_to_take_sakos_hooks_out(self):
        hooks = sako.hook_file("claude")
        fixture.succeeds(fixture.seed(self.root, "--client", "claude"))
        fixture.git(self.root, "add", "-f", hooks)  # forced, since Claude Code may list the file in a global ignore
        fixture.commit_all(self.root, "the team's hooks")
        wired = (self.root / hooks).read_text()
        note = ("Claude Code: .claude/settings.local.json is tracked, so SAKO left it unchanged; remove SAKO's hooks from it "
                "yourself; without them it reads:\n{}\n")
        self.assertIn(note, fixture.succeeds(fixture.seed(self.root, "--update", "--client", "none")))
        self.assertIn(note, fixture.succeeds(fixture.installed(self.root, "remove")), "remove names the hooks it could not remove")
        self.assertEqual((self.root / hooks).read_text(), wired, "a tracked file never changes")

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

    def test_a_session_counts_as_live_for_the_lease_after_its_last_command(self):
        self.cli("start", session="worker")
        self.cli("claim", "T-1", session="worker")
        self.cli("start", session="reader")
        marker, stale = sako.marker_for("worker"), "which is not a live session"
        for ago, live in ((sako.LEASE - 60, True), (sako.LEASE + 60, False)):
            with self.subTest(live=live):
                fixture.lapse(self.root, "worker", ago)
                status = self.cli("status", session="reader")
                self.assertEqual(f"\n  peer {marker} (session ID ends worker) on main since " in status, live, status)
                self.assertEqual("peers: none" in status, not live)
                waiting = self.cli("next")
                self.assertIn(f"T-1  claimed by {marker} since ", waiting)
                self.assertEqual("(not live)" in waiting, not live)
                self.assertEqual(f"T-1 is claimed by {marker}, {stale}" in self.cli("check", expected=0 if live else 1), not live)
        self.assertTrue(fixture.entry_path(self.root, "worker").exists(), "a lapsed entry stays on disk")

    def test_a_takeover_within_the_lease_is_refused_with_when_the_holder_was_seen_and_needs_a_reason_after_it(self):
        self.cli("start", session="holder")
        self.cli("claim", "T-1", session="holder")
        self.cli("start", session="taker")
        holder, seen = sako.marker_for("holder"), int(fixture.entry_path(self.root, "holder").stat().st_mtime)
        at = lambda moment: time.strftime("%Y-%m-%d %H:%M", time.localtime(moment))
        refusal = (f"sako: T-1 is held by {holder}, a live session last seen {at(seen)}, whose lease ends "
                   f"{at(seen + sako.LEASE)}. Choose another task. After the lease, or once the owner confirms that session "
                   "stopped and you end it (end --session holder), take it over with a reason (claim T-1 --takeover REASON)\n")
        for reason in ((), ("--takeover", "it looks idle")):
            with self.subTest(reason=reason):
                self.assertEqual(fixture.cli(self.root, "claim", "T-1", *reason, session="taker"), (3, "", refusal))
        fixture.lapse(self.root, "holder")
        self.assertEqual(fixture.cli(self.root, "claim", "T-1", session="taker"),
                         (3, "", f"sako: T-1 is held by {holder}, a stale claim; inspect its handoff and use --takeover <reason>\n"))
        self.assertIn("T-1: claimed;", self.cli("claim", "T-1", "--takeover", "quiet past its lease; inspected its work",
                                                session="taker"))
        self.assertRegex((self.root / ".sako/work/TASKS.md").read_text(),
                         rf"\| {sako.marker_for('taker')} since [^|]* \(took over from {holder}: quiet past its lease; "
                         r"inspected its work\) \|")
        self.cli("start", session="third")
        self.assertIn(", a live session last seen ", self.cli("claim", "T-1", "--takeover", "x", session="third", expected=3))
        self.assertEqual(self.cli("end", session="taker"), "sako: presence ended\n")  # once the owner confirms it stopped
        self.assertIn("T-1: claimed;", self.cli("claim", "T-1", "--takeover", "the owner confirmed it stopped",
                                                session="third"), "the override takes effect within the lease")

    def test_a_sessions_own_command_after_its_lease_lapsed_renews_it_and_goes_on(self):
        fixture.write(self.root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"]})})
        self.cli("start", session="paused")
        roots = sako.resolve_roots(self.root)
        started = sako.my_entry(roots, "paused")["started_at"]
        for args in (("verify",), ("add", "Notes", "--scope", "docs/"), ("claim", "T-10"), ("edit", "T-10", "--widen", "notes/"),
                     ("close", "T-10", "--evidence", "Recorded"), ("check", "--gate"), ("start",)):  # each acts as the session
            with self.subTest(verb=" ".join(args[:2])):
                lapsed = fixture.lapse(self.root, "paused")["seen"]
                self.cli(*args, session="paused")
                entry = sako.my_entry(roots, "paused")
                self.assertGreater(entry["seen"], lapsed + sako.LEASE, "the command renewed the lease")
                self.assertEqual(entry["started_at"], started, "and the session kept its start record")
        self.cli("add", "Library", "--scope", "lib/")
        self.cli("claim", "T-11", session="paused")
        fixture.lapse(self.root, "paused")
        self.cli("start", session="taker")
        self.cli("claim", "T-11", "--takeover", "quiet past its lease; inspected its work", session="taker")
        self.assertEqual(fixture.cli(self.root, "close", "T-11", "--evidence", "Done", session="paused"),
                         (3, "", "sako: T-11 must be claimed by this session before closing\n"),
                         "a row taken over in the meantime gets the usual refusal")
        self.assertEqual(fixture.cli(self.root, "edit", "T-11", "--widen", "docs/", session="paused"),
                         (3, "", f"sako: T-11 is claimed by {sako.marker_for('taker')}; its holder edits it\n"))

    def test_status_next_and_a_plain_check_renew_nothing_so_reading_a_crashed_session_before_a_takeover_keeps_it_lapsed(self):
        """They are also how one session looks at another, as the method says to before a takeover."""
        self.cli("start", session="crashed")
        self.cli("claim", "T-1", session="crashed")
        self.cli("start", session="taker")
        roots, lapsed = sako.resolve_roots(self.root), fixture.lapse(self.root, "crashed", 9000)["seen"]
        for args in (("status",), ("next",), ("check",)):
            with self.subTest(verb=args[0]):
                fixture.cli(self.root, *args, session="crashed")
                self.assertEqual(sako.my_entry(roots, "crashed")["seen"], lapsed, "the lease stays lapsed")
        self.assertIn(f"; work started at {sako.my_entry(roots, 'crashed')['work_start_rev'][:9]}; last seen "
                      f"{sako.minute(lapsed)}, past the lease, so your claims read as stale to other sessions until you run "
                      "start, add, claim, edit, close, verify or check --gate\n", self.cli("status", session="crashed"),
                      "its own status says its lease ran out")
        self.assertIn("T-1: claimed;", self.cli("claim", "T-1", "--takeover", "it crashed; inspected its work", session="taker"))
        self.assertNotIn("last seen", self.cli("status", session="taker"), "a live session's status names no lease")

    def test_every_hook_but_the_end_renews_the_lease_and_the_end_removes_presence_at_once(self):
        self.assertEqual(fixture.hook(self.root, "SessionStart", "hooked")[0], 0)
        roots = sako.resolve_roots(self.root)
        started = sako.my_entry(roots, "hooked")["started_at"]
        for event in ("Stop", "SessionStart"):
            with self.subTest(event=event):
                lapsed = fixture.lapse(self.root, "hooked")["seen"]
                self.assertEqual(fixture.hook(self.root, event, "hooked")[0], 0)
                entry = sako.my_entry(roots, "hooked")
                self.assertGreater(entry["seen"], lapsed + sako.LEASE, "the hook renewed the lease")
                self.assertEqual(entry["started_at"], started, "a resume keeps its start record")
        self.assertEqual(fixture.hook(self.root, "SessionEnd", "hooked"), (0, "", ""))
        self.assertIsNone(sako.my_entry(roots, "hooked"))
        self.assertEqual(fixture.hook(self.root, "Stop", "hooked")[0], 0)
        self.assertIsNone(sako.my_entry(roots, "hooked"), "a later hook renews nothing, so the end stays")

    def test_the_one_writer_refusal_lifts_once_the_writer_ends_or_lapses(self):
        self.cli("add", "Guide", "--scope", "docs/")
        self.cli("add", "Library", "--scope", "lib/")
        for session in ("writer", "second", "third"):
            self.cli("start", session=session)
        self.cli("claim", "T-1", session="writer")
        self.assertIn(f"sako: T-10 would make a second writer in this checkout: {sako.marker_for('writer')} holds T-1 (src/) "
                      "here", self.cli("claim", "T-10", session="second", expected=3))
        self.assertEqual(self.cli("end", session="writer"), "sako: presence ended\n")
        self.assertIn("T-10: claimed;", self.cli("claim", "T-10", session="second"), "end frees the checkout at once")
        self.assertIn(f"would make a second writer in this checkout: {sako.marker_for('second')} holds T-10 (docs/)",
                      self.cli("claim", "T-11", session="third", expected=3))
        fixture.lapse(self.root, "second")
        self.assertIn("T-11: claimed;", self.cli("claim", "T-11", session="third"), "a writer past its lease holds no checkout")
        fixture.write(self.root, {"lib/shared.py": "x = 1\n"})
        code, _, err = fixture.cli(self.root, "check", "--gate", session="second")
        self.assertEqual(code, 2, err)
        self.assertIn("Recorded: changed this session but covered by no task you claimed or closed: lib/shared.py", err,
                      "a documented limit, since back within its lease the first writer sees the second one's files")

    def test_an_entry_from_an_earlier_release_is_judged_by_its_last_seen_time_alone(self):
        path, marker = fixture.entry_path(self.root, "upgraded"), sako.marker_for("upgraded", "0101")

        def older(seen):
            """The entry an earlier release wrote when it last saw its session, with the agent's process, gone now, its
            PID namespace and boot, and the last-seen time in the file, which this release reads from the file's time."""
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"marker": marker, "session_id": "upgraded", "pid": 2 ** 22,
                                        "ns": "pid:[4026531836]@1 boot an-earlier-boot", "seen": seen,
                                        "started": "2026-01-01T00:00:00+0000", "started_at": 1, "start_root": str(self.root),
                                        "work_root": str(self.root), "branch": "main",
                                        "work_start_rev": fixture.git(self.root, "rev-parse", "HEAD"), "dirty_at_start": {}}))
            fixture.seen_at(path, seen)

        fixture.write(self.root, {".sako/work/TASKS.md": fixture.ledger(fixture.row("T-1", status="claimed", claim=marker))})
        older(int(time.time()))
        self.cli("start", session="reader")
        self.assertIn(f"\n  peer {marker} ", self.cli("status", session="reader"), "seen within the lease, it is live")
        self.assertIn(f"T-1 is held by {marker}, a live session last seen ",
                      self.cli("claim", "T-1", "--takeover", "its process is gone", session="reader", expected=3))
        older(int(time.time()) - sako.LEASE - 60)
        self.assertIn(f"T-1 is claimed by {marker}, which is not a live session", self.cli("check", expected=1))
        self.cli("start", session="upgraded")
        entry = json.loads(path.read_text())
        self.assertEqual((entry["marker"], entry["started_at"]), (marker, 1), "a resume keeps its marker and start record")
        self.assertEqual((entry["reflog_at_start"], entry["done_at_start"]), (None, None),
                         "which names no reflog entry and no done tasks, so the gate takes the side that names nothing wrongly")
        self.assertFalse({"pid", "ns", "seen", "start_root"} & set(entry), "and writes no process, no last-seen field and "
                                                                           "no second checkout")
        older(int(time.time()) - sako.KEPT - 60)
        self.cli("start", session="reader")
        self.assertFalse(path.exists(), "unseen for a week, the entry is deleted at the next start")
        self.cli("start", session="upgraded")
        self.assertGreater(json.loads(path.read_text())["started_at"], 1, "so its session starts over")

    def test_a_last_seen_time_ahead_of_the_clock_counts_only_within_the_lease_and_far_ahead_is_swept_as_unseen(self):
        self.cli("start", session="ahead")
        self.cli("claim", "T-1", session="ahead")
        self.cli("start", session="taker")
        path = fixture.entry_path(self.root, "ahead")
        for ahead, live in ((sako.LEASE - 60, True), (sako.LEASE + 60, False), (30 * 24 * 3600, False)):
            with self.subTest(ahead=ahead):
                fixture.seen_at(path, time.time() + ahead)  # as a clock that stepped back after it was seen leaves it
                self.assertEqual(f"\n  peer {sako.marker_for('ahead')} " in self.cli("status", session="taker"), live)
        self.assertIn("T-1: claimed;", self.cli("claim", "T-1", "--takeover", "seen a month ahead", session="taker"))
        self.cli("start", session="taker")
        self.assertFalse(path.exists(), "more than a week ahead, the next start deletes it as it does one unseen for a week")

    def test_a_renew_never_brings_back_an_entry_that_end_removed_however_the_two_interleave(self):
        """end and the end hook remove the entry, the hook before it takes any lock, so a command or hook that renews the
        session can run beside either. Each turn ends the session at another moment of the renew."""
        roots = sako.resolve_roots(self.root)
        self.cli("start", session="racing")
        path = fixture.entry_path(self.root, "racing")
        written = path.read_bytes()
        for turn in range(100):
            path.write_bytes(written)
            ready = threading.Barrier(2)

            def renewing():
                ready.wait()
                sako.renew(roots, "racing")

            def ending(delay=turn % 20 / 4000):
                ready.wait()
                time.sleep(delay)
                sako.remove_presence(roots, "racing")

            threads = [threading.Thread(target=renewing), threading.Thread(target=ending)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertFalse(path.exists(), f"turn {turn}: the renew wrote the ended entry back")

    def test_a_broken_presence_entry_is_no_peer_and_its_session_starts_over_or_it_is_swept_as_unseen(self):
        live = self.root / ".sako/state/live"
        self.cli("start", session="reader")
        for name, text in (("broken", "{ not json"), ("listed", "[1, 2]"), ("empty", "")):
            with self.subTest(entry=name):
                path = fixture.entry_path(self.root, name)
                path.write_text(text)
                self.assertEqual(self.cli("status", session="reader").count("\n  peer "), 0, "unreadable, it is no peer")
                self.assertIn(f"session '{name}' has not started here", self.cli("claim", "T-1", session=name, expected=3))
                self.assertIn(f"your marker: {sako.marker_for(name)}\n", self.cli("start", session=name), "it starts over")
                self.assertEqual(json.loads(path.read_text())["session_id"], name)
                self.cli("end", session=name)
        unseen = live / "0123456789ab.json"
        unseen.write_text("{ not json")
        fixture.seen_at(unseen, time.time() - sako.KEPT - 60)
        self.cli("start", session="reader")
        self.assertFalse(unseen.exists(), "a broken entry unseen for a week is deleted at the next start")

    def test_a_claim_overlapping_another_sessions_claim_offers_to_wait_within_its_lease_and_a_takeover_after_it(self):
        self.cli("start", session="holder")
        self.cli("claim", "T-1", session="holder")
        self.cli("add", "Narrow work", "--scope", "src/app.py")
        self.cli("start", session="other")
        takeover = "if it stopped, inspect its handoff and take it over first (claim T-1 --takeover REASON), or "
        for ago, advice in ((sako.LEASE - 60, "wait for it to close, or "), (sako.LEASE + 60, takeover)):
            with self.subTest(live=ago < sako.LEASE):
                fixture.lapse(self.root, "holder", ago)
                self.assertIn(f"T-10's scope overlaps T-1 (src/), claimed by {sako.marker_for('holder')}; {advice}narrow "
                              "T-10's scope (edit T-10 --scope PATH)", self.cli("claim", "T-10", session="other", expected=3))

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
