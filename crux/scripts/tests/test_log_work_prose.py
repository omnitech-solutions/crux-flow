"""Static contracts for task-local workflow instructions.

These tests prove that each route and its critical procedure text ships together.
They do not prove what a host loads; fresh-session checks remain necessary.

Run: uv run python3 -m unittest discover -s crux/scripts/tests -p test_log_work_prose.py
"""

from __future__ import annotations

import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def source(skill: str, reference: str | None = None) -> str:
    root = SKILLS / skill
    path = root / "SKILL.md" if reference is None else root / "references" / f"{reference}.md"
    return path.read_text(encoding="utf-8")


class TaskLocalProcedureTests(unittest.TestCase):
    def test_log_work_routes_to_available_selected_references(self):
        entry = source("log-work")
        for operation in ("journal", "log-only"):
            with self.subTest(operation=operation):
                self.assertIn(f"references/{operation}.md", entry)
                self.assertTrue(source("log-work", operation).startswith("# "))
        self.assertIn("before the first side effect", entry)
        self.assertIn("missing or unreadable", entry)
        self.assertNotIn("## The pipeline", entry)

    def test_run_promptbook_routes_to_available_selected_references(self):
        entry = source("run-promptbook")
        for operation in ("start", "advance", "abandon", "status"):
            with self.subTest(operation=operation):
                self.assertIn(f"references/{operation}.md", entry)
                self.assertTrue(source("run-promptbook", operation).startswith("# "))
        self.assertIn("before the first side effect", entry)
        self.assertIn("missing or unreadable", entry)
        self.assertNotIn("## The pipeline", entry)

    def test_selected_references_do_not_embed_other_operation_pipelines(self):
        for selected in ("start", "advance", "abandon", "status"):
            body = source("run-promptbook", selected)
            for other in ("start", "advance", "abandon"):
                if selected != other:
                    with self.subTest(selected=selected, other=other):
                        self.assertNotIn(f"## The pipeline (mode: {other})", body)
        self.assertNotIn("--mode log-only", source("log-work", "journal"))
        self.assertNotIn("--mode journal", source("log-work", "log-only"))

    def test_defaults_and_assignments_stay_in_shared_entry(self):
        log = source("log-work")
        run = source("run-promptbook")
        self.assertIn("Interactive `log-work` defaults to journaling", log)
        self.assertIn("`--silent` without `--journal`", log)
        self.assertIn("whole Evidence and Constraint", run)
        self.assertIn("Only the stop points in `docs/AGENTS.md` §11 apply", run)

    def test_journal_reference_uses_writer_and_preserves_body_constraints(self):
        journal = source("log-work", "journal")
        self.assertIn("write-journal.py", journal)
        self.assertIn("--mode journal", journal)
        self.assertIn("Body lines must not begin with `## [`", journal)
        self.assertIn("1–10 lines", journal)
        self.assertIn("`Friction:`", journal)
        self.assertIn("`Refs:`", journal)
        self.assertIn("`misc`", journal)
        self.assertIn("partial", journal)
        self.assertIn("does not add a marker", journal.lower())

    def test_log_only_reference_requires_op_and_excludes_journal_writes(self):
        branch = source("log-work", "log-only")
        self.assertIn("write-journal.py", branch)
        self.assertIn("--mode log-only", branch)
        self.assertIn("--category misc", branch)
        self.assertIn("--log-op", branch)
        self.assertIn("missing or invalid operation is refused", branch)
        self.assertIn("changes no monthly journal file or journal index", branch)
        self.assertIn("Never invoke the journal index regenerator", branch)
        self.assertNotIn("generate-journal-index.py", branch)

    def test_retry_references_preserve_caller_offset_and_stop_on_lost_input(self):
        entry = source("log-work")
        journal = source("log-work", "journal")
        log_only = source("log-work", "log-only")
        self.assertIn("caller-supplied", entry)
        self.assertIn("never substitutes the host timezone", entry)
        self.assertIn("month-only", journal)
        self.assertIn("offset unverified", journal)
        self.assertIn("original offset", journal)
        self.assertIn("stop automated replay", journal)
        self.assertIn("different offset", journal)
        self.assertIn("refuses", journal)
        self.assertIn("original offset", log_only)
        self.assertIn("different offset", log_only)

    def test_promptbook_references_preserve_mode_specific_boundaries(self):
        start = source("run-promptbook", "start")
        advance = source("run-promptbook", "advance")
        abandon = source("run-promptbook", "abandon")
        status = source("run-promptbook", "status")
        self.assertIn("kind: superseded", start)
        self.assertIn("shared refusal", start)
        self.assertIn("advance-run.py", advance)
        self.assertIn("For ordinary advances exactly two surfaces changed", advance)
        self.assertIn("formal format-two close additionally retained evaluated gate context", advance)
        self.assertIn("staged context alone has no authority", advance)
        self.assertIn("do not call `log-work`", advance)
        self.assertIn("kind: deliberate", abandon)
        self.assertIn("No index regeneration, no log entry", abandon)
        self.assertIn("visualize-run-progress.py", status)
        self.assertIn("Only when the user asks", status)
        self.assertIn("--markdown", status)
        self.assertIn("writes nothing", status)
        self.assertIn("a YAML `in_progress` run may advance", status)
        self.assertIn("The tag has no verified Markdown-abandon route", status)
        self.assertIn("non-retryable on the current distribution", status)

    def test_run_status_owns_the_progress_contract_after_entry_retirement(self):
        entry = source("run-promptbook")
        status = source("run-promptbook", "status")
        commander = (SKILLS.parent / "agents" / "commander.md").read_text(encoding="utf-8")
        for intent in ("visualize run progress", "where am I", "resume my cycle"):
            self.assertIn(intent, entry)
        self.assertIn("skills: [run-promptbook, dev-cycle", commander)
        self.assertNotIn("skills: [run-promptbook, visualize-run-progress", commander)
        self.assertIn("--run RUN-NNN", status)
        self.assertIn("book_content_hash", status)
        self.assertIn("module_tag", status)
        self.assertIn("abandonment.kind", status)
        self.assertIn("lowest-numbered pending", status)
        self.assertIn("promptbook log operation", status)
        self.assertIn("log-work", status)
        self.assertIn("Only after the Markdown artifact", status)
        self.assertIn("The terminal view writes nothing", status)
        self.assertNotIn("visualize-run-progress` skill remains", status)


if __name__ == "__main__":
    unittest.main()
