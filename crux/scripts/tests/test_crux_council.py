"""Smoke tests for the ported `crux.council` package.

Per PB-0003, Prompt 6. Verifies the package imports and the public surface
declared in `crux/scripts/crux/council/__init__.py` is wired up.

DOES NOT make any API calls. Imports + instance construction (no .deliberate()).

Stdlib unittest only.
"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

try:
    import httpx  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"LLM SDK deps unavailable (uv lane required): {exc}")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux repo root
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"


class TestCruxCouncilImports(unittest.TestCase):
    """The package imports cleanly and exposes the documented public surface."""

    @classmethod
    def setUpClass(cls):
        # Put scripts dir on sys.path so `import crux.council` works.
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))

    def test_import_create_async_council(self):
        from crux.council import create_async_council  # noqa: F401

        self.assertTrue(callable(create_async_council))

    def test_import_async_council_class(self):
        from crux.council import AsyncCouncil

        self.assertTrue(inspect.isclass(AsyncCouncil))

    def test_import_async_council_config(self):
        from crux.council import AsyncCouncilConfig

        self.assertTrue(inspect.isclass(AsyncCouncilConfig))

    def test_create_async_council_returns_async_council_instance(self):
        from crux.council import AsyncCouncil, create_async_council

        council = create_async_council()
        self.assertIsInstance(council, AsyncCouncil)

    def test_async_council_has_deliberate(self):
        from crux.council import create_async_council

        council = create_async_council()
        self.assertTrue(
            hasattr(council, "deliberate"),
            "AsyncCouncil should expose .deliberate (async API).",
        )
        self.assertTrue(callable(council.deliberate))

    def test_async_council_has_deliberate_sync(self):
        from crux.council import create_async_council

        council = create_async_council()
        self.assertTrue(
            hasattr(council, "deliberate_sync"),
            "AsyncCouncil should expose .deliberate_sync (sync wrapper).",
        )
        self.assertTrue(callable(council.deliberate_sync))

    def test_sync_council_exports_callable(self):
        from crux.council import council_vote, get_opinion, quick_council

        self.assertTrue(callable(council_vote))
        self.assertTrue(callable(quick_council))
        self.assertTrue(callable(get_opinion))


if __name__ == "__main__":
    unittest.main()


class SyncConfidenceValidationTests(unittest.TestCase):
    """Hostile model-output confidence on the SYNC council must neutralize,
    never reach weighted aggregation (the ingest gate's sync sibling)."""

    @staticmethod
    def _opinion(body):
        import json
        from unittest import mock

        from crux.council import council as sync_council

        def _call(raw):
            payload = json.dumps(
                {"position": "p", "reasoning": "r",
                 "confidence": raw, "considerations": []}
            )
            with mock.patch.object(sync_council, "call_model", return_value=payload):
                return sync_council.get_opinion("any-model", "a question")

        return _call(body)

    def test_hostile_confidence_degrades_to_the_fallback(self):
        opinion = self._opinion(99.0)
        # The validator's ValueError matches the non-JSON fallback path: the
        # whole opinion degrades, the 0.5 fallback confidence is used.
        self.assertEqual(opinion.confidence, 0.5)

    def test_bool_confidence_degrades_to_the_fallback(self):
        # A bool is an int in Python: True used to coerce through float() to
        # 1.0 and pass the range check. Rejection raises ConfidenceRejectedError
        # — a ValueError — so the parse fallback degrades the opinion to 0.5.
        for raw in (True, False):
            opinion = self._opinion(raw)
            self.assertEqual(opinion.confidence, 0.5)

    def test_valid_confidence_flows_through(self):
        opinion = self._opinion(0.9)
        self.assertEqual(opinion.confidence, 0.9)


class RefusalFinishReasonTests(unittest.TestCase):
    """A refusal must raise, never return a silent empty string.

    The council's per-seat wrappers catch the exception and degrade that seat to
    an error vote (reduced quorum -> DEFER_TO_HUMAN when consensus cannot form)
    — a refused seat must not register as an empty APPROVE-shaped opinion.

    The wire shape moved with the transport: an Anthropic-native
    `stop_reason: "refusal"` is now the OpenAI-compatible
    `finish_reason: "content_filter"` (the documented ChatFinishReasonEnum
    member) or a passed-through `native_finish_reason: "refusal"`. The
    REQUIREMENT is unchanged — only the field the gateway carries it in.
    """

    @staticmethod
    def _fake_client(body):
        from unittest import mock

        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = body
        fake_client = mock.MagicMock()
        fake_client.__enter__.return_value.post.return_value = fake_response
        return fake_client

    def _call(self, body):
        from unittest import mock

        from crux.core import llm_caller

        with mock.patch.object(llm_caller.httpx, "Client",
                               return_value=self._fake_client(body)), \
             mock.patch.object(llm_caller, "require", return_value=("test-key",)):
            return llm_caller.call_model("claude-opus-5.5", "hello")

    def test_content_filter_finish_reason_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "refusal"):
            self._call({"choices": [{"finish_reason": "content_filter",
                                     "message": {"content": ""}}]})

    def test_native_refusal_finish_reason_raises(self) -> None:
        """The passthrough field is checked too: a provider whose native
        refusal signal survives translation must not read as a silent stop."""
        with self.assertRaisesRegex(RuntimeError, "refusal"):
            self._call({"choices": [{"finish_reason": "stop",
                                     "native_finish_reason": "refusal",
                                     "message": {"content": ""}}]})

    def test_normal_response_still_returns_text(self) -> None:
        self.assertEqual(
            self._call({"choices": [{"finish_reason": "stop",
                                     "message": {"content": "hi"}}]}),
            "hi",
        )


class SyncArbiterRoleTests(unittest.TestCase):
    """The sync council's default arbiter resolves its own `council_arbiter`
    role, not `anthropic_top`.

    The two roles name the same model today, so a resolved-model check alone
    could not tell which role was read. A role table in which the two roles
    name different sentinels makes the role read visible.
    """

    SENTINELS = {"council_arbiter": "arbiter-sentinel",
                 "anthropic_top": "anthropic-top-sentinel"}

    def _resolve(self, role):
        return self.SENTINELS[role]

    def test_synthesize_opinions_default_arbiter_reads_council_arbiter(self):
        from unittest import mock

        from crux.council import council as sync_council

        opinion = sync_council.Opinion(model="m", position="p", reasoning="r",
                                       confidence=0.9, considerations=[])
        with mock.patch.object(sync_council, "get_default_model",
                               side_effect=self._resolve), \
             mock.patch.object(sync_council, "call_model",
                               return_value="synthesis") as call:
            sync_council.synthesize_opinions("Question?", [opinion])
        self.assertEqual(call.call_args.args[0], "arbiter-sentinel")

    def test_council_vote_default_arbiter_reads_council_arbiter(self):
        from unittest import mock

        from crux.council import council as sync_council

        with mock.patch.object(sync_council, "get_default_model",
                               side_effect=self._resolve), \
             mock.patch.object(sync_council, "call_model",
                               return_value="synthesis") as call:
            sync_council.council_vote("Question?", models=["member-a"],
                                      arbiter=None, tracer=mock.Mock())
        called = [c.args[0] for c in call.call_args_list]
        # POSITIVE CONTROL: the member call went through the same mock, so the
        # recorded calls are the council's real call sequence.
        self.assertEqual(called, ["member-a", "arbiter-sentinel"])

    def test_explicit_arbiter_still_overrides_the_role(self):
        from unittest import mock

        from crux.council import council as sync_council

        with mock.patch.object(sync_council, "get_default_model",
                               side_effect=self._resolve) as resolve, \
             mock.patch.object(sync_council, "call_model",
                               return_value="synthesis") as call:
            sync_council.synthesize_opinions("Question?", [], arbiter_model="chosen")
        self.assertEqual(call.call_args.args[0], "chosen")
        resolve.assert_not_called()

    def test_council_arbiter_resolves_to_the_same_model_as_before(self):
        from crux.core import llm_caller

        # No behaviour change: the role the arbiter now reads names the model
        # the arbiter already used through anthropic_top.
        self.assertEqual(llm_caller.get_default_model("council_arbiter"),
                         "claude-opus-5.5-xhigh")
        self.assertEqual(llm_caller.get_default_model("council_arbiter"),
                         llm_caller.get_default_model("anthropic_top"))
