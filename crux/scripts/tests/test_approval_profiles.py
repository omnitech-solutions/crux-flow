"""The approval profiles: 2 and 3 stay issued, and 4 is the current one.

* `gate_contract_bytes("2")` and `("3")` equal the digests issued approvals pin. Mutation: edit any
  byte profile 3 reaches (the profile-3 policy module, `council_records`, a record schema, or the
  schema engine) -> the digest differs and an issued approval stops validating.
* `production_contract_bytes("4")` equals its own pinned digest and reaches no reviewer-report
  format-2 name. Mutation: edit the profile-4 policy module without re-pinning -> the digest
  differs.
* A new close retains context format 3 and names contract 4. A context naming 4 with another
  format, or naming 5, refuses. Mutation: write format 2 under contract 4.
* The approval PB-0141 RUN-001 holds under profile 3 still validates. Mutation: dispatch every
  context through the current profile.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dev_surface import require_dev_surface  # noqa: E402

import implementation_approval as ap  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
PROFILE_TWO = "c44a85a6160a9fb5ddc6ac938ce7b15d462c9d40b2808d2db92699ad15b8f4d2"
PROFILE_THREE = "62a4760de1e477fb13c78f9162c49c4da620b5ace304ca72ce6fc45496d16d23"
PROFILE_FOUR = "b1bda66c8667b1c9a8bbaa39c47d58fd55245b718e6d025f71f7221d47035167"
PB0141 = "bionic/promptbooks/runs/PB-0141-implementation-migration-pilot/run-RUN-001.yaml"
BATCH = "bionic/adrs/migrations/implementation-pilot-001.yaml"
BATCH_SHA = "2dba2caee1f625fd0f96b68f6a43cb7039eb3d5114bcc6f2cf1c678edd3973af"


def digest(version: str) -> str:
    return hashlib.sha256(ap.gate_contract_bytes(version)).hexdigest()


class IssuedProfileBytes(unittest.TestCase):
    def test_profiles_two_and_three_are_byte_unchanged(self):
        self.assertEqual(digest("2"), PROFILE_TWO)
        self.assertEqual(digest("3"), PROFILE_THREE)
        self.assertEqual(ap.supported_profile("2").contract_sha256, PROFILE_TWO)
        self.assertEqual(ap.supported_profile("3").contract_sha256, PROFILE_THREE)

    def test_profile_four_is_pinned_by_its_own_digest(self):
        self.assertEqual(digest("4"), PROFILE_FOUR)
        self.assertEqual(ap.supported_profile("4").contract_sha256, PROFILE_FOUR)
        self.assertEqual(hashlib.sha256(ap.production_contract_bytes("4")).hexdigest(), PROFILE_FOUR)

    def test_profile_four_reaches_the_current_policy_and_no_reviewer_report_format_two_name(self):
        text = ap.production_contract_bytes("4").decode("utf-8")
        self.assertIn("council-policy-4._evaluate_council_history", text)
        self.assertIn("council-policy-4.committed_order", text)
        self.assertNotIn("reviewer-report-format-2", text)
        self.assertNotIn("council-policy-3.", text)

    def test_the_current_profile_is_four_and_five_is_unsupported(self):
        self.assertEqual(ap.CONTRACT_VERSION, "4")
        import council_history_v4 as v4
        self.assertEqual(ap.supported_profile("4").kernel, v4)
        with self.assertRaises(ap.Refused):
            ap.supported_profile("5")


class ContextFormats(unittest.TestCase):
    def context(self, format_version: str, version: str) -> dict:
        ref = {"path": "a/b", "sha256": "0" * 64}
        return {"record_type": "implementation-gate-context", "format_version": format_version,
                "contract": {"version": version, **ref}, "registry": ref, "records": [], "artifacts": [],
                "start_identity": {"base_commit": "a" * 40, "commit": "b" * 40, "run": ref}}

    def test_profile_four_owns_context_format_three_exclusively(self):
        import council_history_v4 as v4
        v4.validate_context(self.context("3", "4"))
        for context in (self.context("2", "4"), self.context("3", "5"), self.context("3", "3")):
            with self.subTest(format=context["format_version"], contract=context["contract"]["version"]):
                with self.assertRaises(Exception) as caught:
                    v4.validate_context(context)
                self.assertEqual(getattr(caught.exception, "message", ""), "context-version-unsupported")

    def test_profile_three_keeps_context_format_two(self):
        import council_history_v3 as v3
        v3.validate_context(self.context("2", "3"))
        with self.assertRaises(Exception):
            v3.validate_context(self.context("3", "3"))

    def test_a_new_close_retains_context_format_three_naming_contract_four(self):
        import inspect
        source = inspect.getsource(ap._prepare_close)
        self.assertIn('{"3": "2", "4": "3"}[profile.version]', source)
        self.assertEqual(ap.supported_profile(ap.CONTRACT_VERSION).version, "4")


class IssuedApprovalStillValidates(unittest.TestCase):
    def test_pb0141_run_001_profile_three_approval_validates(self):
        require_dev_surface(self, REPO / PB0141, "PB-0141 RUN-001")
        binding = ap.validate_historical_migration_binding(
            REPO, REPO / PB0141, slot="implementation-1", batch_path=BATCH, batch_sha256=BATCH_SHA)
        context = json.loads((REPO / binding.binding["context"]["path"]).read_bytes())
        self.assertEqual(context["contract"]["version"], "3")
        self.assertEqual(context["format_version"], "2")


if __name__ == "__main__":
    unittest.main()
