"""Release boundaries: portable assets, process identity, readiness and compatibility."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sako
import selftest as fixture


class Presence(unittest.TestCase):
    def test_proc_names_with_spaces_and_parentheses_keep_the_right_parent(self):
        with patch.object(Path, "read_text", return_value="57 (a shell) name) S 123 1 0"):
            self.assertEqual(sako.parent_of(57), ("a shell) name", 123))

    def test_parent_lookup_falls_back_to_ps_and_refuses_missing_process(self):
        result = subprocess.CompletedProcess([], 0, " 123 /usr/bin/fish\n", "")
        with patch.object(Path, "read_text", side_effect=FileNotFoundError), \
                patch.object(sako.subprocess, "run", return_value=result) as run:
            self.assertEqual(sako.parent_of(57), ("fish", 123))
            self.assertEqual(run.call_args.args[0], ["ps", "-o", "ppid=,comm=", "-p", "57"])
            result.returncode, result.stdout = 1, ""
            with self.assertRaisesRegex(sako.SakoError, "not found"):
                sako.parent_of(57)

    @unittest.skipIf(fixture.WINDOWS, fixture.LEASE)
    def test_shell_climb_includes_fish(self):
        with patch.dict(os.environ, {"SAKO_AGENT_PID": ""}), \
                patch.object(os, "getppid", return_value=57), \
                patch.object(sako, "parent_of", side_effect=[("fish", 123), ("agent", 1)]):
            self.assertEqual(sako.agent_pid(), 123)

    def test_a_climb_that_ends_at_pid_1_records_no_host(self):
        for ppid, names in ((57, [("bash", 1), ("bwrap", 0)]), (1, [("codex", 0)])):
            with patch.dict(os.environ, {"SAKO_AGENT_PID": ""}), patch.object(os, "getppid", return_value=ppid), \
                    patch.object(sako, "parent_of", side_effect=names):
                self.assertEqual(sako.agent_pid(), 0, "a sandbox's PID 1, or init, is never an agent")

    @unittest.skipIf(fixture.WINDOWS, fixture.LEASE)
    def test_an_entry_is_judged_by_its_process_only_where_that_process_is_visible(self):
        with tempfile.TemporaryDirectory(prefix="sako-presence-") as folder:
            base = Path(folder)
            roots = sako.Roots(base, base, "", base)
            live = sako.presence_dir(roots)
            live.mkdir(parents=True)
            now, gone = int(time.time()), 2 ** 22
            entries = {"hidden": {"pid": gone, "ns": "pid:[2]", "seen": now},  # a host that a sandbox hides
                       "expired": {"pid": gone, "ns": "pid:[2]", "seen": now - sako.LEASE - 1},
                       "no-host": {"pid": 0, "ns": "", "seen": now}, "init": {"pid": 1, "ns": "pid:[1]", "seen": now},
                       "dead": {"pid": gone, "ns": "pid:[1]", "seen": now}, "older": {"pid": gone},
                       "mine": {"pid": os.getpid(), "ns": "pid:[1]", "seen": now - sako.LEASE - 1}}
            for name, entry in entries.items():
                (live / f"{name}.json").write_text(json.dumps(dict(entry, marker=f"sk-0101-{name}")))
            (live / "broken.json").write_text("{")
            with patch.object(sako, "PID_NS", "pid:[1]"):
                found = {e["marker"][8:]: e.get("unverified", False) for e in sako.read_presence(roots)}
            self.assertEqual(found, {"hidden": True, "no-host": True, "init": True, "mine": False})
            self.assertEqual(sorted(p.stem for p in live.glob("*.json")), ["expired", "hidden", "init", "mine", "no-host"],
                             "only an entry judged by a visible process, or an unreadable one, is removed")

    def test_a_start_with_no_visible_host_keeps_the_hooks_pid_and_refreshes_its_lease(self):
        with tempfile.TemporaryDirectory(prefix="sako-presence-") as folder:
            roots = sako.resolve_roots(fixture.make_repo(Path(folder) / "project"))
            hook = {"marker": "sk-0101-aaaaaaaaaaaa", "pid": 4242, "ns": "pid:[host]", "seen": 1, "started_at": 1,
                    "started": "2026-01-01T00:00:00+0000", "work_start_rev": "abc", "dirty_at_start": {}}
            for host in (0, 2 ** 22):  # none visible, or one this namespace cannot see (SAKO_AGENT_PID inherited)
                with patch.object(sako, "agent_pid", return_value=host), patch.object(sako, "PID_NS", "pid:[sandbox]"):
                    entry, fresh = sako.presence_entry(roots, "thread", hook), sako.presence_entry(roots, "other")
                self.assertEqual((entry["pid"], entry["ns"], entry["started_at"]), (4242, "pid:[host]", 1))
                self.assertGreater(entry["seen"], 1, "a start refreshes the lease")
                self.assertEqual((fresh["pid"], fresh["ns"]), (0, ""), "with no host and no earlier entry, no PID")

    def test_on_windows_presence_is_the_lease_and_no_process_is_signalled(self):
        with tempfile.TemporaryDirectory(prefix="sako-presence-") as folder:
            roots = sako.resolve_roots(fixture.make_repo(Path(folder) / "project"))
            with patch.object(sako.sys, "platform", "win32"), patch.dict(os.environ, {"SAKO_AGENT_PID": str(os.getpid())}), \
                    patch.object(os, "kill", side_effect=AssertionError("on Windows os.kill(pid, 0) sends Ctrl+C")):
                self.assertEqual(sako.agent_pid(), 0, "SAKO_AGENT_PID is inert there")
                self.assertFalse(sako.alive(os.getpid()))
                entry = sako.presence_entry(roots, "windows-session")
                self.assertEqual((entry["pid"], entry["ns"]), (0, ""))
                sako.presence_dir(roots).mkdir(parents=True)
                (sako.presence_dir(roots) / "w.json").write_text(json.dumps(entry))
                self.assertEqual([e.get("unverified") for e in sako.read_presence(roots)], [True], "live for the lease")

    @unittest.skipIf(fixture.WINDOWS, fixture.LEASE)
    def test_liveness_distinguishes_missing_and_inaccessible_processes(self):
        self.assertFalse(sako.alive(10**40), "an invalid saved PID is not a live process")
        with patch.object(os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(sako.alive(57))
        with patch.object(os, "kill", side_effect=PermissionError):
            self.assertTrue(sako.alive(57))
        with patch.object(os, "kill") as kill:
            self.assertTrue(sako.alive(57))
            kill.assert_called_once_with(57, 0)
            self.assertFalse(sako.alive(0))


class Release(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="sako-release-")
        self.base = Path(self.workspace.name)

    def tearDown(self):
        self.workspace.cleanup()

    def files(self, root):
        return fixture.snapshot(root)

    def test_package_and_checkout_install_the_same_assets_with_both_integrations(self):
        kit = fixture.packaged_kit(self.base / "site")
        direct, packaged = (fixture.make_repo(self.base / name) for name in ("direct", "packaged"))
        flags = ("--client", "codex", "--client", "claude")
        fixture.succeeds(fixture.seed(direct, *flags))
        fixture.succeeds(fixture.seed(packaged, *flags, source=kit))
        self.assertEqual(self.files(direct), self.files(packaged))
        self.assertEqual((packaged / ".sako/sako.py").read_bytes(), fixture.SAKO.read_bytes())
        self.assertEqual(set(fixture.package_map()), set(sako.ASSETS.values()) | {"templates/TASKS.md"},
                         "the wheel carries exactly the assets init copies")
        before = self.files(packaged)
        fixture.succeeds(fixture.seed(packaged, *flags, source=kit))
        self.assertEqual(self.files(packaged), before, "repeat package installation is idempotent")

    def test_missing_package_asset_refuses_before_changing_project_files(self):
        kit = fixture.packaged_kit(self.base / "site")
        (kit.parent / "SAKO.md").unlink()
        root = fixture.make_repo(self.base / "project")
        before = self.files(root)
        outcome = fixture.seed(root, "--client", "codex", source=kit)
        self.assertEqual(outcome.returncode, 3, outcome.stderr)
        self.assertIn("SAKO.md", outcome.stderr)
        self.assertEqual(self.files(root), before)

    def test_product_and_decision_notes_are_covered_until_explicit_paths_are_selected(self):
        root = fixture.make_repo(self.base / "project")
        config = dict(sako.DEFAULTS, verify_command=[sys.executable, "-c",
            "from pathlib import Path; assert Path('src/app.py').read_text() == 'print(1)\\n'"])
        fixture.write(root, {".sako/config.json": json.dumps(config), "docs/PRODUCT.md": "# Purpose\n",
                             "docs/DECISIONS.md": "# Project choices\n"})
        roots = sako.resolve_roots(root)
        for path in ("docs/PRODUCT.md", "docs/DECISIONS.md"):
            self.assertEqual(sako.verify(roots, config, out=lambda _: None), 0)
            self.assertEqual(sako.evidence_state(roots, config)[0], "fresh")
            with (root / path).open("a") as note:
                note.write("A changed product constraint.\n")
            self.assertEqual(sako.evidence_state(roots, config)[0], "stale", path)
        config["verify_paths"] = ["src"]
        fixture.write(root, {".sako/config.json": json.dumps(config)})
        self.assertEqual(sako.verify(roots, config, out=lambda _: None), 0)
        fixture.write(root, {"docs/PRODUCT.md": "A documentation-only clarification.\n"})
        self.assertEqual(sako.evidence_state(roots, config)[0], "fresh")



class Budgets(unittest.TestCase):
    """The product's budgets: one runtime file of at most 1,800 lines, and at most about
    1,500 words of reading for a normal task."""

    def test_runtime_line_budget(self):
        self.assertLessEqual(fixture.SAKO.read_text().count("\n"), 1800)

    def test_the_active_record_requires_at_most_eight_columns(self):
        self.assertLessEqual(len(sako.COLUMNS["tasks"]), 8)
        template = (fixture.SAKO.parent / "templates/TASKS.md").read_text()
        self.assertIn("| " + " | ".join(sako.COLUMNS["tasks"]) + " |", template, "fresh ledgers start with exactly these")

    def test_reading_for_a_normal_task_stays_under_1500_words(self):
        method = (fixture.SAKO.parent / "SAKO.md").read_text()
        parts = re.split(r"(?m)^(?=## )", method)  # the first part is the title and introduction
        titles = [part.splitlines()[0][3:] for part in parts[1:]]
        self.assertEqual(titles.count("Read when needed"), 1)
        cut = titles.index("Read when needed") + 2
        required, conditional = parts[:cut], parts[cut:]
        for title in ("Daily loop", "Done means", "Records"):
            self.assertIn(title, titles[:cut - 1])
        for part in conditional:
            title = part.splitlines()[0][3:]
            first = next(line for line in part.splitlines()[1:] if line.strip())
            self.assertTrue(first.startswith("Read this when"), title)
            self.assertIn(title, required[-1], f"the router names {title}")
            self.assertNotIn(title, "".join(required[:-1]), f"only the router may send the agent to {title}")
        with tempfile.TemporaryDirectory(prefix="sako-budget-") as workspace:
            root = fixture.make_repo(Path(workspace) / "project")
            fixture.succeeds(fixture.seed(root))
            fixture.write(root, {".sako/config.json": json.dumps({"verify_command": [sys.executable, "-c", "pass"],
                                                                  "verify_paths": ["src"]})})
            fixture.succeeds(fixture.installed(root, "verify"))
            agent = fixture.fake_agent()
            try:
                context = fixture.succeeds(fixture.installed(root, "start", session="budget-session", pid=agent))
            finally:
                fixture.kill(agent)
        skill = (fixture.SAKO.parent / "skills/sako/SKILL.md").read_text()
        template = (fixture.SAKO.parent / "templates/TASKS.md").read_text().split("| ID |")[0]
        counts = {name: len(text.split()) for name, text in
                  (("skill", skill), ("template", template), ("start context", context), ("method", "".join(required)))}
        self.assertLessEqual(sum(counts.values()), 1500, counts)

    def test_the_loop_keeps_end_for_sessions_that_ran_start(self):
        """With hooks, an agent that ran end itself left its own Stop gate without a start record."""
        loop = (fixture.SAKO.parent / "SAKO.md").read_text().split("## Daily loop")[1].split("\n## ")[0]
        self.assertIn("If you ran `start` yourself, run\n   `end --session <id>` last.", loop)
        self.assertIn("With hooks, the client ends the session; do not run\n   `end`", loop)


class Demo(unittest.TestCase):
    @unittest.skipIf(fixture.WINDOWS, "the published demo is a Linux run: python3 and POSIX paths")
    def test_the_published_demos_match_fresh_runs(self):
        for args, path in (([], "docs/demo.md"), (["--coordination"], "docs/coordination-demo.md"),
                           (["--readme-svg"], "docs/assets/readme/working-together.svg")):
            with self.subTest(path=path):
                run = subprocess.run([sys.executable, str(Path(__file__).parent / "demo.py"), *args],
                                     capture_output=True, text=True, env=fixture.ENV, cwd=Path(__file__).parent)
                self.assertEqual(run.returncode, 0, run.stderr)
                page = (fixture.SAKO.parent / path).read_text()
                self.assertEqual(run.stdout, page, f"regenerate: python3 tests/demo.py {' '.join(args)} > {path}")


class Links(unittest.TestCase):
    """Public Markdown: relative links and #anchors resolve, and no page has an em dash.
    The README links to this repository by absolute URL, because PyPI renders it and
    keeps relative links as written; those links are checked against the files here."""

    ROOT = fixture.SAKO.parent

    def repository(self):
        """The Source URL in pyproject.toml, the address the README links through."""
        found = re.search(r'(?m)^Source = "([^"]+)"$', (self.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        return found.group(1) if found else None

    def pages(self):
        """Every Markdown file outside folders whose name starts with a dot, and outside dist/."""
        for folder, children, names in os.walk(self.ROOT):
            top = Path(folder) == self.ROOT
            children[:] = sorted(c for c in children if not c.startswith(".") and not (top and c == "dist"))
            yield from (Path(folder) / name for name in sorted(names) if name.endswith(".md") and not name.startswith("."))

    @staticmethod
    def prose(text):
        """The text without fenced blocks and code spans, where brackets are not links."""
        text = re.sub(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", "", text)
        return re.sub(r"`[^`\n]*`", "", text)

    def anchors(self, path):
        """GitHub's heading anchors: lower case, punctuation other than hyphens and spaces
        dropped, spaces as hyphens, and -1, -2 on repeats."""
        seen, found = {}, set()
        for line in self.prose(path.read_text(encoding="utf-8")).splitlines():
            heading = re.match(r" {0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$", line)
            if heading:
                title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.group(1))
                slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
                found.add(f"{slug}-{seen[slug]}" if slug in seen else slug)
                seen[slug] = seen.get(slug, 0) + 1
        return found

    def test_relative_links_and_anchors_resolve_and_no_page_has_an_em_dash(self):
        problems, checked, absolute = [], [], 0
        repository = self.repository()
        self.assertTrue(repository, "pyproject.toml names the Source URL")
        for page in self.pages():
            name, text = page.relative_to(self.ROOT).as_posix(), page.read_text(encoding="utf-8")
            checked.append(name)
            if "\u2014" in text:
                problems.append(f"{name}: has an em dash")
            for target in re.findall(r"\]\(\s*<?([^)\s>]+)", self.prose(text)):
                local, base = re.match(re.escape(repository) + r"/(?:blob|tree)/main/(.+)", target), page.parent
                raw_base = repository.replace("https://github.com/", "https://raw.githubusercontent.com/")
                local = local or re.match(re.escape(raw_base) + r"/main/(.+)", target)
                if local:
                    absolute += 1
                    target, base = local.group(1), self.ROOT
                elif re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                    continue  # http:, https:, mailto: and other schemes
                path, _, anchor = target.partition("#")
                goal = base / unquote(path) if path else page
                if not goal.exists():
                    problems.append(f"{name}: {target} does not exist")
                elif anchor and not (goal.is_file() and anchor in self.anchors(goal)):
                    problems.append(f"{name}: {target} names no heading there")
        self.assertIn("README.md", checked)
        self.assertIn("docs/limits.md", checked)
        self.assertGreater(absolute, 0, "the README's links to this repository are checked")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
