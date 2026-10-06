"""Tests for the corpus golden-diff classifier (ADR-0096 clause 12's re-bless gate).

`golden_diff_classify.py` is the one mechanical thing standing between "the
three coverage keys came out" and "a route table silently lost nine rows in the
same commit". Its whole value is the `stub-line` / `extraction-content`
boundary, so that boundary is what these tests pin.

The boundary was once a prefix pair — `("> _no extractor for this stack",
"> _")` — whose second entry subsumed the first and classified EVERY
blockquote-italic line as a stub line. Every spine renderer emits
blockquote-italic prose (`> _Derived from db/schema.rb._` is not a stub line;
it is a rendered prose cell), so that prefix could not tell a moved stub line
from moved extraction content. `test_blockquote_italic_prose_is_extraction_content`
is the regression pin for it.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
CORPUS_DIR = TESTS_DIR / "arch-corpus"

sys.path.insert(0, str(TESTS_DIR.parent))          # crux/scripts


def _load(name: str):
    """Import a module from `arch-corpus/` by path (the dir name has a hyphen)."""
    spec = importlib.util.spec_from_file_location(
        f"_arch_corpus_{name}", CORPUS_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GDC = _load("golden_diff_classify")

MD = "crux/scripts/tests/arch-corpus/golden/codetriage/data-model.md"
COV = "crux/scripts/tests/arch-corpus/golden/codetriage/_meta/coverage.json"


class StubLineGrammarTests(unittest.TestCase):
    """The `stub-line` class matches the renderer's grammar, and only it."""

    def test_legacy_no_extractor_line_is_a_stub_line(self):
        line = "> _no extractor for this stack — empty-but-valid spine file._"
        self.assertEqual(GDC.classify_line(MD, line), "stub-line")

    def test_every_clause_4_reason_renders_a_recognized_stub_line(self):
        for reason in GDC.STUB_REASONS:
            line = f"> _stub: {reason} — expected a thing, found nothing._"
            with self.subTest(reason=reason):
                self.assertEqual(GDC.classify_line(MD, line), "stub-line")

    def test_blockquote_italic_prose_is_extraction_content(self):
        """The defect this file exists for.

        These are real blockquote-italic lines a spine file can carry that are
        NOT stub lines. Under the old `"> _"` prefix every one of them
        classified as `stub-line`, so a unit allowed to move stub lines could
        have moved any of them unremarked.
        """
        prose = [
            "> _Derived from `db/schema.rb` (ActiveRecord's committed schema dump)._",
            "> _Static parse of `config/routes.rb`; the DSL is not executed._",
            "> _No resolved import edges detected._",
            "> _stubbed: something the renderer never emits._",
            "> _stub without a colon token._",
            "> _stub: not_a_real_reason — expected x, found y._",
        ]
        for line in prose:
            with self.subTest(line=line):
                self.assertEqual(GDC.classify_line(MD, line), "extraction-content")

    def test_a_table_row_and_a_graph_edge_stay_extraction_content(self):
        self.assertEqual(
            GDC.classify_line(MD, "| doc_classes | `created_at` | datetime | no | — | — |"),
            "extraction-content",
        )
        self.assertEqual(
            GDC.classify_line(MD, '  Repo["Repo"] --> User["User"]'),
            "extraction-content",
        )

    def test_stub_reason_vocabulary_matches_the_core_enum(self):
        """The classifier's copy of the six reasons cannot drift from the enum.

        The classifier is stdlib-only and standalone on purpose (importing
        `crux.arch` pulls the package's eager httpx chain), so it restates the
        vocabulary. This is the assertion that keeps the copy honest.
        """
        from crux.arch.core import StubReason

        self.assertEqual(
            sorted(GDC.STUB_REASONS),
            sorted(r.value for r in StubReason),
        )


class CoverageVocabularyTests(unittest.TestCase):
    """`coverage.json` keys: vocabulary vs. what the extractor actually read."""

    def test_verdict_keys_are_vocabulary(self):
        for key in ("verdict", "stub_reason", "expected", "found",
                    "n_sources", "n_entities", "status", "reason"):
            with self.subTest(key=key):
                self.assertEqual(
                    GDC.classify_line(COV, f'      "{key}": "x",'),
                    "coverage-vocabulary",
                )

    def test_inputs_found_is_not_vocabulary(self):
        self.assertEqual(
            GDC.classify_line(COV, '      "inputs_found": ['),
            "extraction-content",
        )

    def test_a_key_changed_on_both_sides_is_never_vocabulary(self):
        """Membership in the key set is necessary, not sufficient."""
        for key in ("verdict", "n_entities", "stub_reason"):
            with self.subTest(key=key):
                self.assertEqual(
                    GDC.classify_line(COV, f'      "{key}": "x",', frozenset({key})),
                    "extraction-content",
                )


def _diff(*files: tuple[str, str, list[str]]) -> str:
    """A unified diff over `(a_path, b_path, hunk_lines)` triples.

    `a_path` or `b_path` may be `/dev/null` for a creation or a deletion. The
    hunk lines carry their own leading `+`/`-`/space.
    """
    out: list[str] = []
    for a_path, b_path, lines in files:
        named = b_path if b_path != "/dev/null" else a_path
        out.append(f"diff --git a/{named} b/{named}")
        out.append("index 1111111..2222222 100644")
        out.append(f"--- {a_path if a_path == '/dev/null' else 'a/' + a_path}")
        out.append(f"+++ {b_path if b_path == '/dev/null' else 'b/' + b_path}")
        out.append("@@ -1,4 +1,4 @@")
        out.extend(lines)
    return "\n".join(out) + "\n"


def _cov(repo: str) -> str:
    return f"crux/scripts/tests/arch-corpus/golden/{repo}/_meta/coverage.json"


def _md(repo: str, name: str = "data-model.md") -> str:
    return f"crux/scripts/tests/arch-corpus/golden/{repo}/{name}"


class ExtractionRegressionIsNotVocabularyTests(unittest.TestCase):
    """Defect (a): the classifier bucketed by key NAME, not by what moved.

    A `populated` concern regressing to `no_entities` moves exactly the lines
    whose keys the vocabulary set names — `verdict`, `n_entities`, `stub_reason`
    — and in the spine file `_apply_stub_line` INSERTS the stub line rather than
    replacing content, so the table rows stay and the only `.md` change is one
    `stub-line`. The gate saw zero `extraction-content` and exited 0 over a
    repository that had just lost its whole extraction.
    """

    REGRESSION = [
        '-      "verdict": "populated",',
        '-      "n_entities": 56,',
        '+      "verdict": "stubbed",',
        '+      "stub_reason": "no_entities",',
        '+      "n_entities": 0,',
    ]

    def test_a_populated_to_no_entities_regression_is_extraction_content(self):
        findings = GDC.classify_diff(_diff(
            (_cov("codetriage"), _cov("codetriage"), self.REGRESSION),
            (_md("codetriage"), _md("codetriage"),
             ['+> _stub: no_entities — expected a schema, found none._', '+']),
        ))
        classes = findings["codetriage"]
        self.assertIn("extraction-content", classes,
                      "an extraction regression classified entirely as vocabulary")
        moved = " ".join(classes["extraction-content"])
        self.assertIn('"verdict"', moved)
        self.assertIn('"n_entities"', moved)

    def test_the_same_keys_are_still_vocabulary_when_they_only_appear(self):
        """The three re-blesses this gate guarded ADD these keys and never
        change their values. That case must stay classified as vocabulary, or
        the repair would make the gate refuse the units it was written for."""
        findings = GDC.classify_diff(_diff((
            _cov("codetriage"), _cov("codetriage"),
            ['-      "status": "ok",',
             '-      "reason": "x",',
             '+      "verdict": "populated",',
             '+      "n_sources": 3,',
             '+      "n_entities": 56,'],
        )))
        self.assertEqual(sorted(findings["codetriage"]), ["coverage-vocabulary"])


class DeletedFileAttributionTests(unittest.TestCase):
    """Defect (b): a deleted file's hunk landed under the PREVIOUS repository.

    `git diff` writes `+++ /dev/null` for a deletion, which matches no `+++ b/`
    prefix, so the walker kept the path it had. Every removed row of repository
    B was attributed to repository A, and an `--allow-content A` licensed a
    deletion in B. The `+++ /dev/null` header was counted as a changed line too.
    """

    DIFF = _diff(
        (_md("codetriage"), _md("codetriage"),
         ['-| old | row |', '+| new | row |']),
        (_md("rubygems-org"), "/dev/null",
         ['-| gone | row |', '-| also gone | row |']),
    )

    def test_the_deletion_is_attributed_to_its_own_repository(self):
        findings = GDC.classify_diff(self.DIFF)
        self.assertIn("rubygems-org", findings,
                      "the deleted file's repository is missing from the findings")
        self.assertEqual(len(findings["rubygems-org"]["extraction-content"]), 2)

    def test_the_previous_repository_is_not_charged_for_the_deletion(self):
        findings = GDC.classify_diff(self.DIFF)
        self.assertEqual(len(findings["codetriage"]["extraction-content"]), 2)
        charged = " ".join(findings["codetriage"]["extraction-content"])
        self.assertNotIn("gone", charged)
        self.assertNotIn("rubygems-org", charged)

    def test_the_dev_null_header_is_not_counted_as_a_changed_line(self):
        for _path, _sign, line in GDC._changed_lines(self.DIFF):
            self.assertNotIn("/dev/null", line)

    def test_a_creation_is_attributed_to_its_own_repository(self):
        findings = GDC.classify_diff(_diff(
            (_md("codetriage"), _md("codetriage"), ['-| old | row |']),
            ("/dev/null", _md("papercups"), ['+| brand | new |']),
        ))
        self.assertEqual(len(findings["papercups"]["extraction-content"]), 1)
        self.assertEqual(len(findings["codetriage"]["extraction-content"]), 1)

    def test_a_content_line_that_looks_like_a_file_header_does_not_retarget(self):
        """A removed line reading `-- x` is written `--- x` in a unified diff.
        Inside a hunk that is content, not a header; reading it as a header
        would silently point every following line at the wrong file."""
        findings = GDC.classify_diff(_diff((
            _md("codetriage"), _md("codetriage"),
            ['--- a dashed list item', '+++ another one', '-| row |'],
        )))
        self.assertEqual(len(findings["codetriage"]["extraction-content"]), 3)
        self.assertEqual(sorted(findings), ["codetriage"])


if __name__ == "__main__":
    unittest.main()
