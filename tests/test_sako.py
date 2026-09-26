"""Unit tests for the pure parts of sako.py, plus the scenario suite as test cases.

Run:  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import sako  # noqa: E402
import selftest  # noqa: E402


row = selftest.row
HEADER, DONE_HEADER = selftest.HEADER, selftest.DONE_HEADER


def tasks(*rows: str) -> list:
    return sako.parse_rows({"tasks": HEADER + "\n".join(rows)})


class LedgerParsing(unittest.TestCase):
    def test_columns_are_read_by_name_in_any_order_and_user_columns_are_kept(self) -> None:
        text = ("| Area | status | id | TASK | Done  when | pri | Scope | After | Source |\n|:-|-|-|-|-|-|-|-|-|\n"
                "| ui | open | T-3 | Theme toggle | Survives reload | P1 | `src/a/`, `src/b/` | T-1, T-2 | PLAN.md#N1 |")
        item = sako.parse_rows({"tasks": text})[0]
        self.assertEqual((item.id, item.pri, item.task, item.done_when, item.status, item.touches, item.after,
                          item.source, item.cells["area"]),
                         ("T-3", "P1", "Theme toggle", "Survives reload", "open", ["src/a/", "src/b/"],
                          ["T-1", "T-2"], "PLAN.md#N1", "ui"))
        self.assertEqual(sako.row_line(item.titles, item.cells), text.splitlines()[2])

    def test_hand_edits_are_tolerated(self) -> None:
        text = ("# Work\n\nSome prose.\n\n## Now\n\n" + HEADER + row("T-1", status="**parked: waiting for the owner**")
                + "\n\n## Later\n\n" + HEADER.lower() + row("T-2", pri="`p1`", status="-", touches="\u2014") + "\n"
                + "```markdown\n" + HEADER + row("T-3") + "\n```\n")
        first, second = sako.parse_rows({"tasks": text})
        self.assertEqual((first.status, second.status, second.pri, second.touches), ("parked", "open", "P1", []))

    def test_status_holds_the_state_word_or_the_claim(self) -> None:
        marker = "sk-0101-aaaaaaaaaaaa"
        claimed, took, wip = tasks(row("T-1", status="claimed", claim=marker),
                                   row("T-2", status="claimed", claim=f"{marker} (took over from sk-0101-bbbbbbbbbbbb: gone)"),
                                   row("T-3", status="wip"))
        self.assertEqual((claimed.status, claimed.claim), ("claimed", marker))
        self.assertEqual((took.status, sako.holder_key(took.claim)), ("claimed", "aaaaaaaaaaaa"))
        self.assertEqual(wip.status, "wip")

    def test_done_rows_carry_the_closing_marker_in_the_receipt(self) -> None:
        text = DONE_HEADER + row("T-2", status="done", claim="sk-0101-aaaaaaaaaaaa", evidence="Matched [see notes]")
        item = sako.parse_rows({"done": text})[0]
        self.assertEqual((item.status, item.claim), ("done", "sk-0101-aaaaaaaaaaaa"))
        self.assertTrue(item.evidence.startswith("Matched [see notes] [sk-0101-aaaaaaaaaaaa; closed"))

    def test_structural_faults_stop_with_file_and_line(self) -> None:
        for text, fragment in ((HEADER + "| T-1 | four | cells | only |", "tasks:3: a task row needs"),
                               (row("T-1"), "tasks:1: a task row needs a table header"),
                               (HEADER + row("T-x"), "tasks:3: malformed task ID"),
                               ("| ID | Task | Status |\n|---|---|---|\n| T-1 | a | open |", "tasks:1: the table misses Pri")):
            with self.subTest(text=text), self.assertRaisesRegex(sako.SakoError, fragment):
                sako.parse_rows({"tasks": text})


class Findings(unittest.TestCase):
    def findings(self, *rows: str, live: set | None = None) -> list[str]:
        return sako.ledger_findings(tasks(*rows), live)

    def test_consistent_ledger_is_silent(self) -> None:
        rows = sako.parse_rows({"tasks": HEADER + row("T-1", after="T-2"),
                                "done": DONE_HEADER + row("T-2", status="done", claim="sk-0101-aaaaaaaaaaaa")})
        self.assertEqual(sako.ledger_findings(rows), [])

    def test_value_faults_name_their_fix(self) -> None:
        found = self.findings(row("T-1"), row("T-1", "again"), row("T-2", pri="high"), row("T-3", status="**done**"),
                              row("T-4", after="T-4, T-99"), row("T-5", task="-"), row("T-6", touches="`../x`"),
                              row("T-7", source="PLAN.md#1"), row("T-8", source="PLAN.md#1"))
        for fragment in ("T-1 is defined by 2 rows", 'T-2 has Pri "HIGH"; use P1, P2 or P3',
                         'T-3 has status "**done**"', "T-4 names T-4 in After", "T-4 names T-99 in After",
                         "T-5 needs a Task and a Done when", "T-6: scope '../x' escapes", "mapped more than once"):
            self.assertTrue(any(fragment in f for f in found), (fragment, found))

    def test_claims_and_liveness(self) -> None:
        found = self.findings(row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa"),
                              row("T-2", status="claimed", claim="sk-0101-bbbbbbbbbbbb", after="T-1"),
                              live={"sk-0102-bbbbbbbbbbbb"})
        self.assertTrue(any("T-1 is claimed by sk-0101-aaaaaaaaaaaa, which is not a live session; resume it, or take it "
                            "over: claim T-1 --takeover REASON" in f for f in found))
        self.assertTrue(any("T-2 is claimed before T-1 is done" in f for f in found))
        self.assertFalse(any("live session" in f for f in self.findings(row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa"))),
                         "liveness is not judged when no presence information is supplied")

    def test_overlap(self) -> None:
        found = self.findings(row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa", touches="`src/`"),
                              row("T-2", status="claimed", claim="sk-0101-bbbbbbbbbbbb", touches="`src/app/`"),
                              row("T-3", status="claimed", claim="sk-0101-cccccccccccc", touches="`docs/`"))
        self.assertEqual(len(found), 1)
        self.assertIn("T-1 and T-2 are both claimed and their scopes overlap", found[0])
        self.assertTrue(sako.overlaps(sako.scope("src"), sako.scope("src/app/x.py")))
        self.assertFalse(sako.overlaps(sako.scope("docs/"), sako.scope("src/")))
        with self.assertRaisesRegex(sako.SakoError, "escapes"):
            sako.scope("../x")

    def test_inputs_are_not_modified(self) -> None:
        documents = {"tasks": HEADER + row("T-1")}
        before = json.dumps(documents)
        sako.ledger_findings(sako.parse_rows(documents))
        self.assertEqual(json.dumps(documents), before)


class NextPick(unittest.TestCase):
    def next(self, active="", done="", live=None, brief=False) -> str:
        documents = {"tasks": HEADER + active} | ({"done": DONE_HEADER + done} if done else {})
        return "\n".join(sako.next_lines(sako.parse_rows(documents), live, brief))

    def test_priority_then_row_order_with_reasons(self) -> None:
        output = self.next("\n".join([row("T-1", "Later work", pri="P3"), row("T-2", "First P1", pri="P1", touches="`a/`"),
                                      row("T-3", "Second P1", pri="P1", touches="`b/`"), row("T-4", after="T-2"),
                                      row("T-5", status="parked: owner decides"),
                                      row("T-6", status="claimed", claim="sk-0101-aaaaaaaaaaaa", touches="`c/`"),
                                      row("T-7", touches="`c/deep/`"), row("T-8", pri="high")]))
        self.assertIn("next: T-2 (P1, first in row order): First P1", output)
        self.assertIn("also ready: T-3 (P1), T-1 (P3)", output)
        for fragment in ("T-4  after T-2", "T-5  parked: owner decides", "T-6  claimed by sk-0101-aaaaaaaaaaaa",
                         "T-7  scope overlaps T-6, claimed by sk-0101-aaaaaaaaaaaa", 'T-8  T-8 has Pri "HIGH"'):
            self.assertIn(fragment, output)
        brief = self.next(row("T-1") + "\n" + row("T-2", after="T-1"), brief=True)
        self.assertIn("waiting: 1; run next for the reasons", brief)
        self.assertIn("the only ready task", brief)

    def test_nothing_ready_points_back_to_the_planner(self) -> None:
        self.assertIn("no recorded work", sako.next_lines([])[1])
        self.assertIn("No planner? Start here in .sako/SAKO.md", "\n".join(sako.next_lines([])))
        self.assertIn("work is held", self.next(row("T-1", status="claimed", claim="sk-0101-aaaaaaaaaaaa")))
        self.assertIn("completed work is recorded",
                      self.next("", done=row("T-1", status="done", claim="sk-0101-aaaaaaaaaaaa")))
        self.assertIn("only parked work", self.next(row("T-1", status="parked")))
        self.assertIn("No planner? Start here", self.next(row("T-1", status="parked")))


class Markers(unittest.TestCase):
    def test_marker_is_derived_and_stable(self) -> None:
        self.assertEqual(sako.marker_for("abc", "0915"), "sk-0915-" + sako.session_key("abc"))
        self.assertEqual(len(sako.session_key("abc")), 12)
        self.assertEqual(sako.key_of("sk-0915-deadbeef0000"), "deadbeef0000")
        self.assertIsNone(sako.key_of(None))


class Configuration(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="sako-config-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def load(self, text: str) -> dict:
        (self.work / "config.json").write_text(text)
        return sako.load_config(self.work)

    def test_defaults_and_overrides(self) -> None:
        self.assertEqual(sako.load_config(self.work)["tasks"], "work/TASKS.md")
        config = self.load('{"verify_command": ["true"], "verify_paths": ["src"]}')
        self.assertEqual(config["verify_command"], ["true"])
        self.assertNotIn("product_dir", config)

    def test_refusals(self) -> None:
        for text, fragment in (('{"nope": 1}', "unknown keys"), ('[]', "JSON object"),
                               ('{"verify_command": []}', "non-empty list"),
                               ('{"tasks": "/abs/TASKS.md"}', "stay inside"),
                               ('{"done": "../DONE.md"}', "stay inside"),
                               ('{"tasks": "state/TASKS.md"}', "outside state"),
                               ('{"tasks": "State/TASKS.md"}', "outside state"),  # macOS keeps the typed case
                               ('{"tasks": "sako.py"}', "apart from the kit files"),
                               ('{"done": "sako.md"}', "apart from the kit files"),
                               ('{"tasks": "work/X.md", "done": "work/x.md"}', "distinct"),
                               ('{"tasks": "work/X.md", "done": "work/X.md"}', "distinct"),
                               ('{"verify_paths": ["../x"]}', "stay inside"),
                               ('{"deploy_paths": []}', "unknown keys"),
                               ('{"product_dir": "."}', "unknown keys"),
                               ('{"decisions": "docs/DECISIONS.md"}', "unknown keys")):
            with self.subTest(text=text):
                with self.assertRaisesRegex(sako.SakoError, fragment):
                    self.load(text)


class Scenarios(unittest.TestCase):
    """Every selftest scenario, so one command covers the bones end to end."""


def _make(scenario):
    def test(self: unittest.TestCase) -> None:
        workspace = Path(tempfile.mkdtemp(prefix="sako-scenario-"))
        try:
            scenario(workspace / scenario.__name__)
        finally:
            for proc in selftest.FAKE_AGENTS:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            selftest.FAKE_AGENTS.clear()
            selftest.remove_tree(workspace)
    return test


for _scenario in selftest.SCENARIOS:
    setattr(Scenarios, "test_" + _scenario.__name__.removeprefix("scenario_"), _make(_scenario))


if __name__ == "__main__":
    unittest.main()
