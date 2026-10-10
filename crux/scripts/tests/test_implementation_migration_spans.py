"""Exact body-only locations share canonical migration source validation."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import implementation_migration as migration
import test_implementation_migration as foundation


class ClauseSpans(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.Foundation()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.source = self.fixture.source
        self.entry = copy.deepcopy(foundation.document()["entries"][0])

    def select(self, text, *, body_only=False):
        self.entry["clause"] = {"text": text, "sha256": migration._digest(text.encode())}
        self.entry["historical_destination"]["clause_sha256"] = self.entry["clause"]["sha256"]
        if body_only: self.entry["affected_governs"] = []
        self.entry["source_identity"] = migration.source_identity(self.entry)

    def snapshot(self):
        return self.fixture.snapshot()

    def span(self, entry=None):
        before = self.snapshot()
        try: return migration.resolve_clause_span(self.root, entry or self.entry)
        finally: self.assertEqual(self.snapshot(), before)

    def assert_location(self, span, clause):
        raw = self.source.read_bytes()
        self.assertEqual(raw[span["byte_start"]:span["byte_end"]], clause.encode())
        self.assertEqual(span["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(span["line_start"], raw[:span["byte_start"]].count(b"\n") + 1)
        self.assertEqual(span["line_end"], raw[:span["byte_end"] - 1].count(b"\n") + 1)
        self.assertEqual(set(span), {"source_identity", "source_adr", "path", "clause_sha256",
                                   "source_sha256", "byte_start", "byte_end", "line_start", "line_end"})

    def test_mixed_body_span_preserves_existing_reference_serialization(self):
        expected = {"source_adr": "ADR-0110", "path": "knowledge/adrs/ADR-0110-source.md",
                    "tier": "active", "status": "Accepted", "source_identity": self.entry["source_identity"],
                    "clause_sha256": self.entry["clause"]["sha256"],
                    "affected_governs": self.entry["affected_governs"]}
        self.assertEqual(migration.resolve_clause(self.root, self.entry), expected)
        span = self.span()
        self.assert_location(span, self.entry["clause"]["text"])
        self.assertEqual(span["source_identity"], expected["source_identity"])
        self.assertNotIn("authority", span)
        self.assertEqual(migration.resolve_clause(self.root, self.entry), expected)

    def test_unicode_partial_lines_terminal_newline_and_frontmatter_occurrence(self):
        prefix = self.source.read_text().split("# Synthetic source")[0]
        for clause in ("é中", "é中\nnext line", "é中\nnext line\n", "\n"):
            with self.subTest(clause=clause):
                self.select(clause, body_only=True)
                # Identical frontmatter scalar must not win over the located body match.
                front = prefix.replace("status: Accepted", 'status: Accepted\nquoted: "é中"')
                body = "First λ line\nstart " + clause + ("" if clause.endswith("\n") else "suffix")
                if clause == "\n": body = "last" + clause
                self.source.write_text(front + body)
                span = self.span()
                self.assert_location(span, clause)
                self.assertGreaterEqual(span["byte_start"], len(front.encode()))
                base = front.count("\n")
                self.assertEqual(span["line_start"], base + (1 if clause == "\n" else 2))
                self.assertEqual(span["line_end"], base + (1 if clause == "\n" else 3 if "next line" in clause else 2))
                if clause.endswith("\n"): self.assertEqual(span["byte_end"], len(self.source.read_bytes()))
                if clause == "é中":
                    self.assertGreater(span["byte_start"], self.source.read_text().rfind(clause))

    def test_archive_and_lifecycle_relocate_without_changing_identity(self):
        initial = self.span()
        archive = self.source.parent / "archive"; archive.mkdir()
        self.source.rename(archive / self.source.name); self.source = archive / self.source.name
        self.source.write_text(self.source.read_text().replace("status: Accepted", "status: Superseded\ndate: 2027-01-01"))
        later = self.span()
        self.assert_location(later, self.entry["clause"]["text"])
        self.assertEqual(later["source_identity"], initial["source_identity"])
        self.assertEqual(later["clause_sha256"], initial["clause_sha256"])
        self.assertNotEqual(later["source_sha256"], initial["source_sha256"])
        self.assertNotEqual(later["byte_start"], initial["byte_start"])
        self.assertIn("/archive/", later["path"])

    def test_schema_identity_and_governs_refusals_match_existing_resolver(self):
        for field, value in (("source_identity", "0" * 64), ("extra", True),
                             ("source_adr", "ADR-0093"), ("clause", {"text": "wrong", "sha256": "0" * 64})):
            with self.subTest(field=field):
                bad = copy.deepcopy(self.entry); bad[field] = value
                with self.assertRaises(migration.Refused) as original:
                    migration.resolve_clause(self.root, bad)
                with self.assertRaises(migration.Refused) as span: self.span(bad)
                self.assertEqual(span.exception.code, original.exception.code)
        self.source.write_text(self.source.read_text().replace("Preserve assessments.", "Drop assessments."))
        with self.assertRaisesRegex(migration.Refused, "migration-governs-mismatch"): self.span()

    def test_duplicate_missing_overlapping_body_and_frontmatter_only_refuse(self):
        raw = self.source.read_bytes(); clause = self.entry["clause"]["text"]
        for fault in ("duplicate", "missing", "repeated", "frontmatter-only", "overlap"):
            with self.subTest(fault=fault):
                self.source.write_bytes(raw); self.entry = copy.deepcopy(foundation.document()["entries"][0])
                duplicate = self.source.parent / "ADR-0110-other.md"
                if fault == "duplicate": duplicate.write_bytes(raw)
                if fault == "missing": self.source.unlink()
                if fault == "repeated": self.source.write_bytes(raw + clause.encode())
                if fault == "frontmatter-only":
                    self.select("Preserve assessments.", body_only=True)
                if fault == "overlap":
                    self.source.write_bytes(raw + b"aaaa"); self.select("aaa", body_only=True)
                with self.assertRaises(migration.Refused): self.span()
                duplicate.unlink(missing_ok=True)

    def test_invalid_utf8_symlink_source_and_wrong_root_refuse_without_writes(self):
        raw = self.source.read_bytes()
        self.source.write_bytes(raw + b"\xff")
        with self.assertRaises(migration.Refused): self.span()
        self.source.write_bytes(raw)
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / self.source.name; outside.write_bytes(raw)
            self.source.unlink(); self.source.symlink_to(outside)
            with self.assertRaises(migration.Refused): self.span()
            self.assertEqual(outside.read_bytes(), raw)
            with self.assertRaises(migration.Refused): migration.resolve_clause_span(Path(other), self.entry)

    def test_span_and_legacy_resolver_read_selected_source_once(self):
        for resolver in (migration.resolve_clause, migration.resolve_clause_span):
            with self.subTest(resolver=resolver.__name__), patch.object(migration, "_read", wraps=migration._read) as read:
                resolver(self.root, self.entry)
                self.assertEqual(sum(Path(c.args[1]) == self.source for c in read.call_args_list), 1)


if __name__ == "__main__": unittest.main()
