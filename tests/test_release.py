"""Release boundaries: portable assets, readiness and compatibility."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sako
import selftest as fixture


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
    """The product's budgets: one runtime file of at most 4,000 lines, each at most 100 columns
    wide, at most about 1,500 words of reading for a normal task, and caps on the method and
    the limits page."""

    def test_runtime_line_budget(self):
        self.assertLessEqual(fixture.SAKO.read_text().count("\n"), 4000)

    def test_the_method_and_the_limits_page_stay_within_their_word_caps(self):
        for name, cap in (("SAKO.md", 3000), ("docs/limits.md", 1500)):  # words as wc -w counts them
            self.assertLessEqual(len((fixture.SAKO.parent / name).read_text(encoding="utf-8").split()), cap, name)

    def test_runtime_lines_fit_in_100_columns(self):
        lines = fixture.SAKO.read_text(encoding="utf-8").splitlines()
        self.assertEqual([n for n, line in enumerate(lines, 1) if len(line) > 100], [],
                         "these sako.py lines are longer than 100 characters")

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
        counts, context = fixture.reading()  # tests/measure.py prints the same counts
        for door in (context, sako.POINTER):  # the start context, and the line init gives for project instructions
            self.assertIn("read .sako/SAKO.md down to its Read when needed table, later sections only when they apply", door)
            self.assertIn("Questions need no task record until they change a file; add and claim come before that change.", door)
        skill = (fixture.SAKO.parent / "skills/sako/SKILL.md").read_text()
        self.assertEqual(counts["method"], len("".join(required).split()), "the helper counts the same required part")
        self.assertLessEqual(sum(counts.values()), 1500, counts)
        stops, guide = " ".join("".join(required).split()), " ".join(skill.split())  # folded, so a rewrap never breaks a pin
        self.assertIn("Read this file down to its **Read when needed** table.", stops)
        self.assertIn("Stop here; open a section below only when its situation applies:", stops)
        self.assertIn("down to its **Read when needed** table and follow its daily loop; open a later section only when its "
                      "situation applies.", guide)
        for text in (stops, guide):
            self.assertIn("need no task record until they change a file; `add` and `claim` come before that change.", text)
        self.assertIn("If `.sako/` is absent and the owner asks to adopt SAKO, run `uvx sako init`", guide)

    def test_the_loop_keeps_end_for_sessions_that_ran_start(self):
        """With hooks, an agent that ran end itself left its own Stop gate without a start record."""
        loop = (fixture.SAKO.parent / "SAKO.md").read_text().split("## Daily loop")[1].split("\n## ")[0]
        self.assertIn("If you ran `start` yourself, run\n   `end --session <id>` last.", loop)
        self.assertIn("With hooks, the client ends the session; do not run\n   `end`", loop)


class Words(unittest.TestCase):
    """Sentences the documents must keep, compared with whitespace folded so a rewrap never breaks a pin."""

    ROOT = fixture.SAKO.parent

    def text(self, name):
        return " ".join((self.ROOT / name).read_text(encoding="utf-8").split())

    def section(self, title):
        """One section of SAKO.md, from its heading to the next one."""
        return self.text("SAKO.md").split(f"## {title} ")[1].split(" ## ")[0]

    def test_the_gate_documents_claim_only_what_it_reads(self):
        done = self.section("Done means")
        for words in ("every file changed in this checkout since the session started, outside `.sako/`, belongs to a task it "
                      "claimed or closed", "in the repo and shared footprints, its receipt too",
                      "A duplicate ID, a source mapped twice, a faulty row, an overlap with your own claim, a second writer in "
                      "your checkout and a footprint finding also block", "notes, such as another session's stale claim, never "
                      "block"):
            self.assertIn(words, done)
        detail = self.section("The gate and checks in detail")
        self.assertIn("It counts no file under `.sako/`, so a scope there only locks paths against other claims", detail)
        self.assertIn("The first line of `check --gate` is its verdict, and its exit code decides", detail)
        limits = self.text("docs/limits.md")
        for words in ("The gate judges only the checkout it runs in", "In a shared checkout the gate sees what changed, not who "
                      "changed it.", "Closing a task scoped `.` makes the Committed fix stage every uncommitted change made "
                      "here between your start and its close, a peer's included", "Recorded cannot tell who made a commit",
                      "A close seals what each of its files holds, whoever wrote it, so read `git diff` before following a "
                      "Committed fix.", "Whether a closed task still covers an uncommitted file goes by what the file "
                      "holds, not by when it was written."):
            self.assertIn(words, limits)
        self.assertIn("and against faults in the records. It refuses a stop once and names the fix; findings about other "
                      "sessions are only notes", self.text("docs/glossary.md"))
        validation = self.text("docs/validation.md")
        self.assertIn("| Who made a change, which Git does not show |", validation)
        for words in ("apply to the session's own changes", "a hook's own error"):
            self.assertNotIn(words, validation)
        self.assertIn("and checks the ledger for faults", self.text("README.md"))

    def test_receipts_are_described_by_their_times_and_the_records_only_form(self):
        for name in ("SAKO.md", "docs/glossary.md", "docs/validation.md"):
            self.assertIn("the claim's marker and time, the close time", self.text(name))
        for name in ("SAKO.md", "docs/validation.md"):
            self.assertIn("(records only)", self.text(name))
        self.assertNotIn("the closing session, the date", self.text("docs/validation.md"))

    def test_security_names_every_environment_variable_a_hook_or_init_reads(self):
        source = (self.ROOT / "sako.py").read_text(encoding="utf-8")
        names = set(re.findall(r'os\.environ\.get\("(\w+)"\)', source)) | set(re.findall(r"\$\{([A-Z_]+)",
                                                                                          sako.hook_command("claude")))
        names |= set(re.findall(r'\("(?:claude|codex)", "([A-Z_]+)"\)', source))  # the session signals init's detect reads
        self.assertIn("CLAUDECODE", names)
        self.assertIn("CLAUDE_PROJECT_DIR", names)
        security = self.text("SECURITY.md")
        for name in sorted(names):
            self.assertIn(f"`{name}`", security)

    def test_a_subagent_carries_its_parents_session_id(self):
        beside = self.section("Working beside other sessions")
        for words in ("Workers run no SAKO command because a subagent's shell carries its parent's session ID, so a command "
                      "there without `--session` acts as the parent, and its `end` would end the coordinator's presence.",
                      "Paste each worker its row as the brief and tell it to run no SAKO command.",
                      "When you close work you did not build, name its builder in the receipt.",
                      "a worktree only they use needs no `init`"):
            self.assertIn(words, beside)
        self.assertIn("a subagent's shell carries its parent's session ID, so a command there without `--session` acts as the "
                      "parent, and its `end` ends the parent's presence", self.text("docs/compatibility.md"))
        self.assertIn("A helper on its parent's ID is the parent, so the ledger shows no separate holder for it.",
                      self.text("docs/limits.md"))
        for page in Links().pages():  # no page may say the opposite again
            self.assertNotIn("carries its own", " ".join(page.read_text(encoding="utf-8").split()), page)

    def test_the_method_names_three_ways_of_working_and_what_gates_each(self):
        beside = self.section("Working beside other sessions")
        for words in ("- **Solo.** One session in one checkout. Its Stop hook gates it.",
                      "- **Peers.** Two or more sessions that each run SAKO, each under its own claims in its own worktree",
                      "A session in another client is always a peer, since that client's hooks run SAKO for it.",
                      "- **A coordinator with a fleet.** One session coordinates and holds the claims.",
                      "They run no SAKO command", "and nothing gates them.", "The Stop hook gates the coordinator.",
                      "Start with one worker. The most writers at once is the number of ready rows whose scopes do not overlap",
                      "Workers that only read can fan out freely. You read every diff, so you are the cap."):
            self.assertIn(words, beside)

    def test_presence_is_a_lease_and_the_limits_state_both_ways_it_errs(self):
        self.assertEqual((sako.LEASE, sako.KEPT), (2 * 3600, 7 * 24 * 3600), "the documents say two hours and a week")
        pause = self.section("Pause, handoff and takeover")
        for words in ("A session counts as live for two hours, its lease, after its last hook or command that acts as it, on "
                      "every platform. `status`, `next` and a plain `check` also show other sessions, so they renew nothing.",
                      "end it with `end --session <its ID>`, then take the claim over",
                      "`start` deletes the presence of a session unseen for a week",
                      "after an end, as when the client exits, or a start in another checkout, it takes a new start record"):
            self.assertIn(words, pause)
        limits = self.text("docs/limits.md")
        for words in ("A live session that fires no hook and runs no such command for two hours reads as ended",
                      "the ledger check names them, and each gate shows the other's files until one moves.",
                      "Each start in another checkout takes a new start record",
                      "A crashed session, or one in a client without hooks that never ran `end`, reads as live for two hours "
                      "after its last such command. Under the one-writer rule it locks product claims in its checkout until "
                      "then"):
            self.assertIn(words, limits)

    def test_a_writer_is_the_session_that_last_started_in_a_checkout_and_a_new_worktree_is_wired_by_init(self):
        beside = self.section("Working beside other sessions")
        for words in ("so it has one writer, the live session that last started there and holds a product claim.",
                      "refused with the Git command for a worktree of its own; `uvx sako init` there wires its hooks."):
            self.assertIn(words, beside)
        self.assertIn("While another live session is this checkout's writer and you are not, the changes here are its to "
                      "answer for.", self.section("The gate and checks in detail"))
        self.assertIn("WSL and native Windows on one clone count as two checkouts whose locks do not interlock",
                      self.text("docs/limits.md"))

    def test_a_claim_left_on_purpose_reads_as_stale_until_taken_over(self):
        self.assertIn("A claim left on purpose for another session reads as stale to every other session until it is taken "
                      "over. Two rows avoid that,", self.section("Pause, handoff and takeover"))
        self.assertIn("A claim left on purpose for another session reads as stale to every other session until it is taken "
                      "over; close your part and leave the rest as a second row.", self.text("docs/limits.md"))

    def test_a_claim_is_kept_only_for_work_an_agent_will_resume(self):
        loop = self.section("Daily loop")
        self.assertIn("Keep a claim only for work an agent will resume", loop)
        self.assertIn("A session that keeps a claim to resume it does not run `end`", loop)
        pause = self.section("Pause, handoff and takeover")
        for words in ("A background subagent that a message can resume is paused",
                      "When the next step is the owner's, close what you finished.",
                      "The owner's act, or deferred work, is its own row, parked with its reason (`edit T-1 --park "
                      "\"reason\"`); whoever finishes what it waits for opens it (`edit T-1 --open`), then claims and "
                      "closes it."):
            self.assertIn(words, pause)

    def test_an_unmet_part_becomes_a_new_task_and_a_later_verdict_goes_on_the_row_that_waits_for_it(self):
        self.assertIn("An unmet or waived part of the completion condition becomes a new task; the receipt names both.",
                      self.section("Daily loop"))
        self.assertIn("A completion condition names what the closing session can observe; a later verdict, such as a "
                      "reviewer's or the owner's, goes on the row that waits for it.", self.section("Task intake"))

    def test_the_owner_learns_where_things_stand_from_the_planner_next_and_status(self):
        self.assertIn("| Where things stand, for the owner | The planner for its items, `next` for ready and waiting rows, "
                      "`status` for live peers; a progress tracker names task IDs and never copies their state |",
                      self.section("Records"))
        self.assertIn("A parked row whose reason waits on a plan item is closed or reopened in the step that ticks that item.",
                      self.section("Task intake"))


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
