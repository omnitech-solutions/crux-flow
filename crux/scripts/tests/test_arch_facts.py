"""Tests for the cache-free Swift fact grammar and matcher (ADR-0129 clause 9(c)).

Four parts:

  * The facts shape — `fetch.py`'s `_validate_expect` gains an optional
    `expect.facts` shape: `{path, sha256}`.
  * The fact grammar — `facts_matcher.py`'s one-parser-per-kind grammar.
    Every fact of both committed expectations files must parse; an
    unparseable value fails.
  * The matcher — `facts_matcher.match()`, implementing ADR-0129 clause 9(c)'s
    must-render / limitation-expected / out-of-scope statuses against a
    golden set (concern -> markdown text).
  * The corpus gate — every corpus entry that names a facts file: its
    recorded SHA-256 equals the file's bytes, and every fact holds against the
    entry's committed goldens on each interpreter a golden set exists for.

No test here reads `.cache/`. The expectations files and the committed
goldens are read as fixtures, so the corpus gate below runs with no clone.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
CORPUS_DIR = TESTS_DIR / "arch-corpus"
EXPECTATIONS_DIR = CORPUS_DIR / "expectations"

sys.path.insert(0, str(TESTS_DIR.parent))  # crux/scripts


def _load(name: str):
    """Import a module from `arch-corpus/` by path (mirrors test_arch_corpus.py)."""
    spec = importlib.util.spec_from_file_location(f"_arch_corpus_{name}", CORPUS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    _fetch = _load("fetch")
    _matcher = _load("facts_matcher")
    _IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    _fetch = _matcher = None
    _IMPORT_ERROR = exc

import yaml  # noqa: E402  (after sys.path setup, matches fetch.py's own PEP 723 dep)


def _load_facts_doc(name: str) -> dict:
    return yaml.safe_load((EXPECTATIONS_DIR / name).read_text(encoding="utf-8"))


class ModuleImportTests(unittest.TestCase):
    def test_fetch_and_matcher_import_cleanly(self):
        self.assertIsNone(_IMPORT_ERROR, f"import failed: {_IMPORT_ERROR}")


# ---------------------------------------------------------------------------
# The facts shape — fetch.py's expect.facts
# ---------------------------------------------------------------------------

class ExpectFactsShapeTests(unittest.TestCase):
    """`_validate_expect` accepts an optional `facts: {path, sha256}` block."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def _base_expect(self, **overrides):
        expect = {
            "pack": "swift",
            "packages": ["Package.swift"],
            "concerns": {
                "data-model": {"populated": {"min_entities": 1}},
                "api-surface": {"populated": {"min_entities": 1}},
                "module-graph": {"populated": {"min_entities": 1}},
                "decision-index": {"stubbed": {"reason": "precondition_missing"},
                                    "because": "no decision-index probe reads Swift"},
            },
        }
        expect.update(overrides)
        return expect

    def test_valid_facts_block_produces_no_problems(self):
        expect = self._base_expect(facts={
            "path": "expectations/swift-argument-parser.yml",
            "sha256": "a" * 64,
        })
        problems = _fetch._validate_expect(expect, "repos[0]")
        self.assertEqual(problems, [])

    def test_absent_facts_block_is_still_valid_for_a_pre_swift_pack(self):
        expect = self._base_expect(pack="node")
        problems = _fetch._validate_expect(expect, "repos[0]")
        self.assertEqual(problems, [])

    def test_a_swift_entry_without_a_facts_block_is_refused(self):
        # ADR-0129 clause 9(a): each Swift entry's record names its facts
        # file and SHA-256, so no Swift entry can sit outside the matcher.
        expect = self._base_expect()
        problems = _fetch._validate_expect(expect, "repos[0]")
        self.assertEqual(len(problems), 1)
        self.assertIn("facts", problems[0])

    def test_facts_path_must_match_expectations_slug_pattern(self):
        for bad_path in (
            "expectations/../etc/passwd.yml",
            "/etc/passwd",
            "expectations/Swift.yml",       # uppercase not allowed
            "expectations/swift_argument.yml",  # underscore not allowed
            "expectations/swift-argument-parser.yaml",  # wrong extension
            "swift-argument-parser.yml",    # missing expectations/ prefix
        ):
            with self.subTest(bad_path=bad_path):
                expect = self._base_expect(facts={"path": bad_path, "sha256": "a" * 64})
                problems = _fetch._validate_expect(expect, "repos[0]")
                self.assertTrue(problems, f"expected a problem for path {bad_path!r}")

    def test_facts_sha256_must_be_64_lowercase_hex(self):
        for bad_sha in (
            "a" * 63,               # too short
            "a" * 65,               # too long
            "A" * 64,               # uppercase
            "g" * 64,               # non-hex
            "",
        ):
            with self.subTest(bad_sha=bad_sha):
                expect = self._base_expect(facts={
                    "path": "expectations/swift-argument-parser.yml",
                    "sha256": bad_sha,
                })
                problems = _fetch._validate_expect(expect, "repos[0]")
                self.assertTrue(problems, f"expected a problem for sha {bad_sha!r}")

    def test_facts_block_rejects_unknown_keys(self):
        expect = self._base_expect(facts={
            "path": "expectations/swift-argument-parser.yml",
            "sha256": "a" * 64,
            "extra": "nope",
        })
        problems = _fetch._validate_expect(expect, "repos[0]")
        self.assertTrue(problems)

    def test_facts_block_rejects_wrong_types(self):
        for bad_facts in ("a string", 5, ["path", "sha"], None):
            with self.subTest(bad_facts=bad_facts):
                expect = self._base_expect(facts=bad_facts)
                problems = _fetch._validate_expect(expect, "repos[0]")
                self.assertTrue(problems, f"expected a problem for facts={bad_facts!r}")

    def test_facts_block_requires_both_keys(self):
        for partial in (
            {"path": "expectations/swift-argument-parser.yml"},
            {"sha256": "a" * 64},
            {},
        ):
            with self.subTest(partial=partial):
                expect = self._base_expect(facts=partial)
                problems = _fetch._validate_expect(expect, "repos[0]")
                self.assertTrue(problems, f"expected a problem for facts={partial!r}")

    def test_existing_corpus_entries_with_no_facts_block_still_validate(self):
        """No network: `--verify`/`--list` exercise `load_manifest` on the
        committed `corpus.yml`, whose entries (as of this loop) carry no
        `facts` key at all — the absent-block path must stay green."""
        repos = _fetch.load_manifest()
        self.assertTrue(repos)


# ---------------------------------------------------------------------------
# The fact grammar
# ---------------------------------------------------------------------------

class EntryDeclarationTargetExtractionOverBothFilesTests(unittest.TestCase):
    """ADR-0130 clause 16: pins how many real entry-declaration
    facts of each committed file yield a `target <Name>` component — 3 of
    NetNewsWire's, none of swift-argument-parser's (package-only)."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def _targets(self, name):
        doc = _load_facts_doc(name)
        out = []
        for raw in doc["facts"]:
            if raw["kind"] != "entry-declaration" or raw["status"] != "must-render":
                continue
            fact = _matcher.parse_fact(raw)
            target = fact.parts[0] if fact.parts else None
            if target is not None:
                out.append(target)
        return out

    def test_netnewswire_entry_declarations_yield_three_targets(self):
        targets = self._targets("netnewswire.yml")
        self.assertEqual(sorted(targets), sorted([
            "NetNewsWire", "NetNewsWire-iOS", "NetNewsWire iOS Widget Extension"]))

    def test_swift_argument_parser_entry_declarations_yield_no_targets(self):
        self.assertEqual(self._targets("swift-argument-parser.yml"), [])


class FactGrammarRealCorpusTests(unittest.TestCase):
    """Every fact of both committed expectations files parses."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def test_every_fact_of_netnewswire_parses(self):
        doc = _load_facts_doc("netnewswire.yml")
        facts = doc["facts"]
        parsed = [_matcher.parse_fact(f) for f in facts]
        self.assertEqual(len(parsed), len(facts))
        self.assertEqual(len(parsed), 924)

    def test_every_fact_of_swift_argument_parser_parses(self):
        doc = _load_facts_doc("swift-argument-parser.yml")
        facts = doc["facts"]
        parsed = [_matcher.parse_fact(f) for f in facts]
        self.assertEqual(len(parsed), len(facts))
        self.assertEqual(len(parsed), 104)

    def test_per_kind_counts_match_both_files(self):
        import collections
        for name, total in (("netnewswire.yml", 924), ("swift-argument-parser.yml", 104)):
            doc = _load_facts_doc(name)
            facts = doc["facts"]
            parsed = [_matcher.parse_fact(f) for f in facts]
            raw_kinds = collections.Counter(f["kind"] for f in facts)
            parsed_kinds = collections.Counter(p.kind for p in parsed)
            with self.subTest(file=name):
                self.assertEqual(raw_kinds, parsed_kinds)
                self.assertEqual(sum(parsed_kinds.values()), total)
                for kind, count in raw_kinds.items():
                    self.assertGreaterEqual(count, 1, kind)


class FactGrammarPerKindTests(unittest.TestCase):
    """One test per kind: a real-shaped value parses; a malformed one raises."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def _fact(self, kind, value, status="must-render", concern="data-model",
              evidence="Sources/X.swift:1-1"):
        return {"concern": concern, "kind": kind, "value": value,
                "evidence": evidence, "status": status}

    def test_type_parses(self):
        f = _matcher.parse_fact(self._fact("type", "Assets (struct, internal)"))
        self.assertEqual(f.key, "Assets")
        self.assertEqual(f.row_kind, "type")

    def test_type_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("type", "no parens here"))

    def test_stored_property_parses(self):
        f = _matcher.parse_fact(self._fact(
            "stored-property", "WidgetData.totalUnreadCount: Int"))
        self.assertEqual(f.key, "WidgetData.totalUnreadCount")
        self.assertEqual(f.row_kind, "stored-property")

    def test_stored_property_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("stored-property", "no colon here"))

    def test_public_symbol_product_shape_library(self):
        f = _matcher.parse_fact(self._fact(
            "public-symbol", "ArgumentParser (library product)", concern="api-surface"))
        self.assertEqual(f.row_kind, "product")
        self.assertEqual(f.key, "ArgumentParser")

    def test_public_symbol_product_shape_plugin(self):
        f = _matcher.parse_fact(self._fact(
            "public-symbol", "GenerateDoccReference (command plugin product)",
            concern="api-surface"))
        self.assertEqual(f.row_kind, "product")
        self.assertEqual(f.key, "GenerateDoccReference")

    def test_public_symbol_provides_shape(self):
        f = _matcher.parse_fact(self._fact(
            "public-symbol", "Modules/Account provides library product Account",
            concern="api-surface"))
        self.assertEqual(f.row_kind, "product")
        self.assertEqual(f.key, "Account")

    def test_public_symbol_interface_shape_drops_throws_and_parenthetical(self):
        f = _matcher.parse_fact(self._fact(
            "public-symbol", "SyncDatabase.selectForProcessing(limit:) throws (public)",
            concern="api-surface"))
        self.assertEqual(f.row_kind, "interface")
        self.assertEqual(f.key, "SyncDatabase.selectForProcessing(limit:)")

    def test_public_symbol_interface_shape_property(self):
        f = _matcher.parse_fact(self._fact(
            "public-symbol", "HTMLMetadataDatabase.shared (public static let, @MainActor)",
            concern="api-surface"))
        self.assertEqual(f.row_kind, "interface")
        self.assertEqual(f.key, "HTMLMetadataDatabase.shared")

    def test_public_symbol_interface_shape_bare_type(self):
        f = _matcher.parse_fact(self._fact(
            "public-symbol", "ArticleThemeDownloader (public final class)",
            concern="api-surface"))
        self.assertEqual(f.row_kind, "interface")
        self.assertEqual(f.key, "ArticleThemeDownloader")

    def test_public_symbol_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("public-symbol", "", concern="api-surface"))

    def test_protocol_requirement_strips_type_and_get_clause(self):
        f = _matcher.parse_fact(self._fact(
            "protocol-requirement",
            "ExtensionContainer.name: String { get }", concern="api-surface"))
        self.assertEqual(f.row_kind, "requirement")
        self.assertEqual(f.key, "ExtensionContainer.name")

    def test_protocol_requirement_no_selector(self):
        f = _matcher.parse_fact(self._fact(
            "protocol-requirement",
            "ParsableCommand.run (mutating func; default implementation throws a help request)",
            concern="api-surface"))
        self.assertEqual(f.row_kind, "requirement")
        self.assertEqual(f.key, "ParsableCommand.run")

    def test_protocol_requirement_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("protocol-requirement", "no dot", concern="api-surface"))

    def test_entry_declaration_parses(self):
        f = _matcher.parse_fact(self._fact(
            "entry-declaration", "AppDelegate (class, macOS app target NetNewsWire)",
            concern="api-surface"))
        self.assertEqual(f.row_kind, "main")
        self.assertEqual(f.key, "AppDelegate")

    def test_entry_declaration_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("entry-declaration", "no parens", concern="api-surface"))

    def test_entry_declaration_extracts_target_from_last_comma_component(self):
        # ADR-0130 clause 16: the target is read from the last comma
        # component matching `target <Name>` — here "macOS app target
        # NetNewsWire" ends with that shape.
        f = _matcher.parse_fact(self._fact(
            "entry-declaration", "AppDelegate (class, macOS app target NetNewsWire)",
            concern="api-surface"))
        self.assertEqual(f.parts, ("NetNewsWire",))

    def test_entry_declaration_extracts_multi_word_target_name(self):
        f = _matcher.parse_fact(self._fact(
            "entry-declaration",
            "NetNewsWireWidgets (struct, WidgetBundle, target NetNewsWire iOS Widget Extension)",
            concern="api-surface"))
        self.assertEqual(f.parts, ("NetNewsWire iOS Widget Extension",))

    def test_entry_declaration_with_no_target_component_yields_no_target(self):
        # swift-argument-parser's entry declarations name a tool/example kind,
        # never a `target <Name>` component — the matcher must not invent one.
        f = _matcher.parse_fact(self._fact(
            "entry-declaration", "ChangelogAuthors (@main entry, AsyncParsableCommand tool)",
            concern="api-surface"))
        self.assertEqual(f.parts, (None,))

    def test_entry_declaration_target_slash_word_is_not_a_target_component(self):
        # "plugin target/product" must not be mistaken for a `target <Name>`
        # component — there is no space after "target" in "target/product".
        f = _matcher.parse_fact(self._fact(
            "entry-declaration",
            "GenerateDoccReference (@main entry, ParsableCommand tool executable; "
            "distinct from the plugin target/product of the same name)",
            concern="api-surface"))
        self.assertEqual(f.parts, (None,))

    def test_target_parses(self):
        f = _matcher.parse_fact(self._fact(
            "target", "ArgumentParser (library target)", concern="module-graph"))
        self.assertEqual(f.row_kind, "target")
        self.assertEqual(f.key, "ArgumentParser")

    def test_target_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("target", "no parens", concern="module-graph"))

    def test_target_dependency_parses(self):
        f = _matcher.parse_fact(self._fact(
            "target-dependency", "ArgumentParser -> ArgumentParserToolInfo",
            concern="module-graph"))
        self.assertEqual(f.row_kind, "edge")
        self.assertEqual(f.edge, ("ArgumentParser", "ArgumentParserToolInfo"))

    def test_target_dependency_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("target-dependency", "no arrow here", concern="module-graph"))

    def test_package_dependency_local_shape_is_edge(self):
        f = _matcher.parse_fact(self._fact(
            "package-dependency", "NetNewsWire -> Account (local package Modules/Account)",
            concern="module-graph"))
        self.assertEqual(f.row_kind, "edge")
        self.assertEqual(f.edge, ("NetNewsWire", "Account"))

    def test_package_dependency_path_shape_is_dependency_row(self):
        f = _matcher.parse_fact(self._fact(
            "package-dependency", "Modules/Account -> local package ../ActivityLog (path dependency)",
            concern="module-graph"))
        self.assertEqual(f.row_kind, "dependency")

    def test_package_dependency_target_remote_shape_is_dependency_row(self):
        f = _matcher.parse_fact(self._fact(
            "package-dependency",
            "NetNewsWire -> Sparkle (remote package https://github.com/sparkle-project/Sparkle.git)",
            concern="module-graph"))
        self.assertEqual(f.row_kind, "dependency")

    def test_package_dependency_project_remote_shape_is_dependency_row(self):
        f = _matcher.parse_fact(self._fact(
            "package-dependency",
            "project references remote package https://github.com/sparkle-project/Sparkle.git "
            "(upToNextMajorVersion 2.9.5)",
            concern="module-graph"))
        self.assertEqual(f.row_kind, "dependency")

    def test_package_dependency_container_remote_shape_is_dependency_row(self):
        f = _matcher.parse_fact(self._fact(
            "package-dependency",
            "Modules/RSParser -> remote package https://github.com/brentsimmons/Tidemark from 1.0.0",
            concern="module-graph"))
        self.assertEqual(f.row_kind, "dependency")

    def test_package_dependency_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("package-dependency", "nonsense", concern="module-graph"))

    def test_import_parses(self):
        f = _matcher.parse_fact(self._fact(
            "import", "ArgumentParser imports ArgumentParserToolInfo (internal import)",
            concern="module-graph"))
        self.assertEqual(f.row_kind, "import")

    def test_import_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("import", "nonsense", concern="module-graph"))

    def test_source_membership_in_form_parses(self):
        f = _matcher.parse_fact(self._fact(
            "source-membership", "Mac/AppDelegate.swift in NetNewsWire (folder-synced root Mac)",
            concern="module-graph"))
        self.assertEqual(f.row_kind, "membership")
        self.assertEqual(f.parts, ("Mac/AppDelegate.swift", "NetNewsWire", "folder-synced root Mac"))

    def test_source_membership_excluded_form_parses(self):
        f = _matcher.parse_fact(self._fact(
            "source-membership",
            "Mac/SafariExtension/SafariExtensionHandler.swift excluded from NetNewsWire "
            "by an exception set", concern="module-graph"))
        self.assertEqual(f.row_kind, "membership")
        # The excluded form maps to the rendered route wording verbatim
        # (ADR-0130 render contract), not a synthetic "excluded" token.
        self.assertEqual(f.parts, (
            "Mac/SafariExtension/SafariExtensionHandler.swift", "NetNewsWire",
            "excluded by an exception set"))

    def test_source_membership_added_by_exception_form_parses(self):
        f = _matcher.parse_fact(self._fact(
            "source-membership",
            "Mac/SafariExtension/SafariExtensionHandler.swift in Subscribe to Feed "
            "(added by an exception set)", concern="module-graph"))
        self.assertEqual(f.row_kind, "membership")
        self.assertEqual(f.parts, (
            "Mac/SafariExtension/SafariExtensionHandler.swift", "Subscribe to Feed",
            "added by an exception set"))

    def test_source_membership_malformed_raises(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(self._fact("source-membership", "nonsense", concern="module-graph"))

    def test_limitation_parses_evidence_only(self):
        f = _matcher.parse_fact(self._fact(
            "limitation", "xcconfig #include of ./common/X.xcconfig", status="limitation-expected"))
        self.assertEqual(f.row_kind, "residual")

    def test_residual_parses_evidence_only(self):
        f = _matcher.parse_fact(self._fact(
            "residual", "@Observable on Foo: macro-generated observation members",
            status="limitation-expected"))
        self.assertEqual(f.row_kind, "residual")

    def test_out_of_scope_splits_on_colon(self):
        f = _matcher.parse_fact(self._fact(
            "entry-declaration",
            "SceneDelegate: scene delegate, deferred (only @main is an entry declaration)",
            status="out-of-scope", concern="api-surface"))
        self.assertEqual(f.key, "SceneDelegate")
        # row_kind stays the entity's normal shape (here "main") even under
        # out-of-scope status: the matcher needs it to know which table to
        # check "no row of that kind" against.
        self.assertEqual(f.row_kind, "main")

    def test_out_of_scope_splits_on_paren_when_earlier_than_colon(self):
        f = _matcher.parse_fact(self._fact(
            "public-symbol", "AboutView SwiftUI view hierarchy: deferred",
            status="out-of-scope", concern="api-surface"))
        self.assertEqual(f.key, "AboutView SwiftUI view hierarchy")

    def test_out_of_scope_residual_kind_parses(self):
        f = _matcher.parse_fact(self._fact(
            "residual",
            "shell script build phase Delete Unnecessary Frameworks: never executed",
            status="out-of-scope"))
        self.assertEqual(f.key, "shell script build phase Delete Unnecessary Frameworks")

    def test_evidence_splits_on_last_colon_for_paths_with_spaces(self):
        f = _matcher.parse_fact(self._fact(
            "type", "Assets (struct, internal)",
            evidence="Sources/Parsable Properties/Assets.swift:25-25"))
        self.assertEqual(f.evidence.path, "Sources/Parsable Properties/Assets.swift")
        self.assertEqual(f.evidence.start, 25)
        self.assertEqual(f.evidence.end, 25)

    def test_evidence_range_parses_two_numbers(self):
        f = _matcher.parse_fact(self._fact(
            "type", "Assets (struct, internal)", evidence="Shared/Assets.swift:25-31"))
        self.assertEqual(f.evidence.start, 25)
        self.assertEqual(f.evidence.end, 31)


# ---------------------------------------------------------------------------
# The matcher, against synthetic goldens
# ---------------------------------------------------------------------------

def _fact(kind, value, status="must-render", concern="data-model",
          evidence="Sources/X.swift:10-10"):
    return {"concern": concern, "kind": kind, "value": value,
            "evidence": evidence, "status": status}


# A small synthetic golden that follows the render contract addendum exactly
# (R1-R8), covering one row of every row kind the matcher recognises.
SYNTHETIC_DATA_MODEL = """# Data model

Summary.

## Types

| type | kind | access | declared at | conditional |
|---|---|---|---|---|
| `Assets` | struct | internal | `Sources/X.swift:10-10` | — |

## Stored properties

| owner | property | declared type | access | declared at |
|---|---|---|---|---|
| `WidgetData` | `totalUnreadCount` | `Int` | internal | `Sources/X.swift:10-10` |

## Relationships

| from type | relation | to type | declared at |
|---|---|---|---|

## Residuals

- `parse-error` `Sources/Leftover.swift` lines 5-8 — location top level; effect no enclosing declaration
"""

SYNTHETIC_API_SURFACE = """# API surface

Summary.

## Interfaces

| interface | kind | access | declared at | attributes | conditional |
|---|---|---|---|---|---|
| `ArticleThemeDownloader` | class | public | `Sources/X.swift:10-10` | — | — |
| `SyncDatabase.selectForProcessing(limit:)` | func | public | `Sources/X.swift:10-10` | — | — |

## Protocol requirements

| requirement | protocol | kind | declared at |
|---|---|---|---|
| `run()` | `ParsableCommand` | func | `Sources/X.swift:10-10` |

## Products

| product | product kind | container | declared at |
|---|---|---|---|
| `ArgumentParser` | library | `Package.swift` | `Sources/X.swift:10-10` |

## @main declarations

| @main type | kind | owning target | declared at |
|---|---|---|---|
| `AppDelegate` | class | `NetNewsWire (NetNewsWire.xcodeproj)` | `Sources/X.swift:10-10` |
| `WidgetBundle` | struct | `NetNewsWire iOS Widget Extension (NetNewsWire.xcodeproj), Other (NetNewsWire.xcodeproj)` | `Sources/X.swift:10-10` |
| `NoOwner` | class | — | `Sources/X.swift:10-10` |

## Residuals

_None._
"""

SYNTHETIC_MODULE_GRAPH = """# Module graph

Summary.

## Containers

| container | container kind |
|---|---|
| `Package.swift` | swift package |

## Targets

| target | container | target kind | conditional | declared at |
|---|---|---|---|---|
| `ArgumentParser` | `Package.swift` | library | — | `Sources/X.swift:10-10` |

## Graph

```mermaid
graph LR
  n1["ArgumentParser (Package.swift)"] --> n2["ArgumentParserToolInfo (Package.swift)"]
```

## Dependencies

| from | dependency | dependency kind | location | requirement | declared at |
|---|---|---|---|---|---|
| `Modules/Account` | `../ActivityLog` | path dependency | — | — | `Sources/X.swift:10-10` |

## Imports

| file | module | import kind | owning target | resolves to |
|---|---|---|---|---|
| `Sources/X.swift:10-10` | `ArgumentParserToolInfo` | internal | — | sdk-or-unresolved |

## Membership

| file | target | route | conditional | declared at |
|---|---|---|---|---|
| `Sources/X.swift` | `ArgumentParser` | `folder-synced root Sources` | — | `Sources/X.swift:10-10` |
| `Sources/Excluded.swift` | `ArgumentParser` | `excluded by an exception set` | — | `Sources/X.swift:10-10` |

## Residuals

_None._
"""

SYNTHETIC_GOLDEN = {
    "data-model": SYNTHETIC_DATA_MODEL,
    "api-surface": SYNTHETIC_API_SURFACE,
    "module-graph": SYNTHETIC_MODULE_GRAPH,
}


class OutOfScopeAndConcernTests(unittest.TestCase):
    """ADR-0129 clause 9(c): no fact passes unchecked, and a fact is checked
    in the concern it names."""

    def test_out_of_scope_edge_import_requirement_membership_fail_as_unparseable(self):
        # The generic out-of-scope grammar yields a name, not an edge or a
        # tuple, so these kinds have no check; they must fail loudly rather
        # than pass (`None in edges`) or crash with a TypeError.
        for kind, value in (
            ("target-dependency", "A -> B"),
            ("package-dependency", "A -> P (local package ../P)"),
            ("import", "A.swift imports B (plain import)"),
            ("protocol-requirement", "P.run()"),
            ("source-membership", "A.swift in T (folder-synced root)"),
        ):
            with self.subTest(kind=kind), self.assertRaises(_matcher.FactParseError):
                _matcher.parse_fact(_fact(kind, value, status="out-of-scope",
                                          concern="module-graph"))

    def test_an_entity_fact_naming_another_concern_fails(self):
        with self.assertRaises(_matcher.FactParseError):
            _matcher.parse_fact(_fact("type", "Assets (struct, internal)", concern="api-surface"))

    def test_an_entity_fact_naming_its_own_concern_parses(self):
        fact = _matcher.parse_fact(_fact("type", "Assets (struct, internal)", concern="data-model"))
        self.assertEqual(fact.key, "Assets")


class MatcherPositiveControlTests(unittest.TestCase):
    """Every fact of a small synthetic facts doc matches its synthetic golden."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def test_type_must_render_matches(self):
        facts = [_fact("type", "Assets (struct, internal)", concern="data-model")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_stored_property_must_render_matches(self):
        facts = [_fact("stored-property", "WidgetData.totalUnreadCount: Int", concern="data-model")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_interface_must_render_matches(self):
        facts = [_fact("public-symbol", "ArticleThemeDownloader (public final class)",
                       concern="api-surface")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_interface_selector_must_render_matches(self):
        facts = [_fact("public-symbol",
                       "SyncDatabase.selectForProcessing(limit:) throws (public)",
                       concern="api-surface")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_protocol_requirement_with_selector_stripped_matches_golden_selector(self):
        facts = [_fact("protocol-requirement",
                       "ParsableCommand.run (mutating func; default implementation)",
                       concern="api-surface")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_product_must_render_matches(self):
        facts = [_fact("public-symbol", "ArgumentParser (library product)", concern="api-surface")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_main_must_render_matches(self):
        facts = [_fact("entry-declaration", "AppDelegate (class, macOS app target NetNewsWire)",
                       concern="api-surface")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_main_with_no_target_component_matches_by_name_only(self):
        # swift-argument-parser's shape — no `target <Name>` component — must
        # still match a row purely by @main type name.
        facts = [_fact("entry-declaration", "NoOwner (class, top-level tool)",
                       concern="api-surface")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_main_matches_one_of_several_sorted_owners(self):
        facts = [_fact(
            "entry-declaration",
            "WidgetBundle (struct, WidgetBundle, target NetNewsWire iOS Widget Extension)",
            concern="api-surface")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_membership_must_render_matches(self):
        facts = [_fact(
            "source-membership", "Sources/X.swift in ArgumentParser (folder-synced root Sources)",
            concern="module-graph")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_membership_excluded_form_matches_rendered_route(self):
        facts = [_fact(
            "source-membership",
            "Sources/Excluded.swift excluded from ArgumentParser by an exception set",
            concern="module-graph")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_target_must_render_matches(self):
        facts = [_fact("target", "ArgumentParser (library target)", concern="module-graph")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_edge_must_render_matches(self):
        facts = [_fact("target-dependency", "ArgumentParser -> ArgumentParserToolInfo",
                       concern="module-graph")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_local_package_edge_must_render_matches(self):
        facts = [_fact("package-dependency",
                       "ArgumentParser -> ArgumentParserToolInfo (local package Sources/ArgumentParserToolInfo)",
                       concern="module-graph")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_dependency_row_must_render_matches(self):
        facts = [_fact("package-dependency",
                       "Modules/Account -> local package ../ActivityLog (path dependency)",
                       concern="module-graph")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_import_row_must_render_matches(self):
        facts = [_fact("import", "ArgumentParser imports ArgumentParserToolInfo (internal import)",
                       concern="module-graph", evidence="Sources/X.swift:10-10")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_residual_limitation_expected_matches(self):
        facts = [_fact("residual", "some macro not expanded", status="limitation-expected",
                       concern="data-model", evidence="Sources/Leftover.swift:6-7")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_out_of_scope_entity_kind_with_no_matching_row_passes(self):
        facts = [_fact("type", "NeverRendered: this type is deferred", status="out-of-scope",
                       concern="data-model")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])

    def test_out_of_scope_residual_kind_asserts_nothing(self):
        facts = [_fact("residual", "anything at all: never checked", status="out-of-scope",
                       concern="data-model")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertEqual(problems, [])


class MatcherNegativeControlTests(unittest.TestCase):
    """Each of these seeds a red finding against the synthetic golden."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def test_seeded_missing_fact_is_red(self):
        facts = [_fact("type", "NeverThere (struct, internal)", concern="data-model")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertTrue(problems)

    def test_limitation_met_only_by_entity_row_is_red(self):
        # `Assets` renders as a plain entity row (## Types), never a residual —
        # an unannotated entity row must never satisfy limitation-expected.
        facts = [_fact("limitation", "Assets (struct, internal)", status="limitation-expected",
                       concern="data-model", evidence="Sources/X.swift:10-10")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertTrue(problems)

    def test_out_of_scope_fact_whose_key_renders_is_red(self):
        # `Assets` DOES render as a type row in the synthetic golden, so
        # asserting it is out-of-scope must fail.
        facts = [_fact("type", "Assets: this type is deferred", status="out-of-scope",
                       concern="data-model")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertTrue(problems)

    def test_must_render_fact_with_wrong_evidence_path_is_red(self):
        facts = [_fact("type", "Assets (struct, internal)", concern="data-model",
                       evidence="Sources/WrongFile.swift:10-10")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertTrue(problems)

    def test_must_render_fact_with_nonoverlapping_range_is_red(self):
        facts = [_fact("type", "Assets (struct, internal)", concern="data-model",
                       evidence="Sources/X.swift:200-200")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertTrue(problems)

    def test_product_fact_checked_only_in_module_graph_shaped_rows_is_red(self):
        # A product's home is api-surface; a product fact must not be
        # satisfied by pointing the matcher at module-graph-only rows.
        golden_without_products = dict(SYNTHETIC_GOLDEN)
        golden_without_products["api-surface"] = SYNTHETIC_API_SURFACE.replace(
            "| `ArgumentParser` | library | `Package.swift` | `Sources/X.swift:10-10` |\n", "")
        facts = [_fact("public-symbol", "ArgumentParser (library product)", concern="api-surface")]
        problems = _matcher.match(facts, golden_without_products)
        self.assertTrue(problems)

    def test_membership_wrong_target_is_red(self):
        facts = [_fact(
            "source-membership", "Sources/X.swift in WrongTarget (folder-synced root Sources)",
            concern="module-graph")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertTrue(problems)

    def test_membership_wrong_route_is_red(self):
        # Same file and target as the golden row, but the wrong route —
        # matching on file and target alone must not be enough.
        facts = [_fact(
            "source-membership", "Sources/X.swift in ArgumentParser (classic Sources build phase)",
            concern="module-graph")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertTrue(problems)

    def test_main_wrong_owning_target_is_red(self):
        # AppDelegate renders, but never owned by "SomethingElse" — matching
        # on the @main type name alone must not be enough.
        facts = [_fact("entry-declaration", "AppDelegate (class, macOS app target SomethingElse)",
                       concern="api-surface")]
        problems = _matcher.match(facts, SYNTHETIC_GOLDEN)
        self.assertTrue(problems)


class MatcherNeverSkipsRealFactsTests(unittest.TestCase):
    """Runs the fact grammar's parser over both real facts files and
    matches against an EMPTY golden: every must-render and
    limitation-expected fact must be
    reported (nothing skipped); out-of-scope facts against an empty golden
    always pass (there is nothing to render regardless)."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def _check(self, name):
        doc = _load_facts_doc(name)
        facts = doc["facts"]
        empty_golden = {"data-model": "# Data model\n\n## Residuals\n\n_None._\n",
                        "api-surface": "# API surface\n\n## Residuals\n\n_None._\n",
                        "module-graph": "# Module graph\n\n## Residuals\n\n_None._\n"}
        problems = _matcher.match(facts, empty_golden)
        expected_reported = sum(
            1 for f in facts if f["status"] in ("must-render", "limitation-expected"))
        reported_evidence = {(p["kind"], p["evidence"], p["status"]) for p in problems}
        self.assertEqual(len(problems), expected_reported,
                         f"{name}: expected {expected_reported} problems, got {len(problems)}")
        for f in facts:
            if f["status"] in ("must-render", "limitation-expected"):
                key = (f["kind"], f["evidence"], f["status"])
                self.assertIn(key, reported_evidence,
                             f"{name}: fact not reported: {f}")

    def test_netnewswire_every_fact_checked_none_skipped(self):
        self._check("netnewswire.yml")

    def test_swift_argument_parser_every_fact_checked_none_skipped(self):
        self._check("swift-argument-parser.yml")


class GoldenForHelperTests(unittest.TestCase):
    """`golden_for` reads the four spine files through `derive_corpus.golden_path`."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def test_golden_for_missing_entry_reports_file_not_found(self):
        # A name with no committed golden must fail loudly rather than
        # silently return partial data.
        with self.assertRaises(FileNotFoundError):
            _matcher.golden_for("no-such-corpus-entry", (3, 13))


# ---------------------------------------------------------------------------
# The corpus gate: every entry's facts file, its digest, and its goldens
# ---------------------------------------------------------------------------

def _entries_with_facts() -> list[dict]:
    return [e for e in _fetch.load_manifest() if (e.get("expect") or {}).get("facts")]


def _interpreters() -> list[tuple[int, int]]:
    """The base interpreter, the running one, and every minor with an overlay
    directory: each is a golden set the matcher must hold against."""
    derive_corpus = _load("derive_corpus")
    versions = {tuple(derive_corpus.BASE_INTERPRETER), tuple(sys.version_info[:2])}
    if derive_corpus.OVERLAY_ROOT.is_dir():
        for d in derive_corpus.OVERLAY_ROOT.iterdir():
            major, _, minor = d.name.partition(".")
            if major.isdigit() and minor.isdigit():
                versions.add((int(major), int(minor)))
    return sorted(versions)


class CorpusFactsDigestTests(unittest.TestCase):
    """ADR-0129 clause 9(a): each recorded SHA-256 equals its file's bytes."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def test_every_recorded_digest_matches_its_facts_file(self):
        import hashlib
        entries = _entries_with_facts()
        self.assertGreaterEqual(len(entries), 1, "no corpus entry names a facts file")
        for entry in entries:
            facts = entry["expect"]["facts"]
            with self.subTest(entry=entry["name"]):
                data = (CORPUS_DIR / facts["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), facts["sha256"])

    def test_positive_control_one_changed_byte_breaks_the_digest(self):
        import hashlib
        facts = _entries_with_facts()[0]["expect"]["facts"]
        data = bytearray((CORPUS_DIR / facts["path"]).read_bytes())
        data[len(data) // 2] ^= 0x01
        self.assertNotEqual(hashlib.sha256(bytes(data)).hexdigest(), facts["sha256"])


class CorpusFactsMatchTests(unittest.TestCase):
    """ADR-0129 clause 9(c): every fact of every facts file a corpus entry
    names holds under its status against that entry's committed goldens, on
    every interpreter a golden set exists for. Cache-free: it reads goldens."""

    def setUp(self):
        if _IMPORT_ERROR:
            self.skipTest(f"module import failed: {_IMPORT_ERROR}")

    def test_every_fact_holds_against_the_goldens(self):
        checked = 0
        for entry in _entries_with_facts():
            doc = yaml.safe_load((CORPUS_DIR / entry["expect"]["facts"]["path"]).read_text(encoding="utf-8"))
            for version in _interpreters():
                with self.subTest(entry=entry["name"], python=version):
                    golden = _matcher.golden_for(entry["name"], version)
                    problems = _matcher.match(doc, golden)
                    self.assertEqual(problems, [], f"{len(problems)} facts do not hold")
                    checked += 1
        self.assertGreaterEqual(checked, 1, "the matcher checked no entry")

    def test_positive_control_a_seeded_missing_fact_turns_the_gate_red(self):
        entry = _entries_with_facts()[0]
        doc = yaml.safe_load((CORPUS_DIR / entry["expect"]["facts"]["path"]).read_text(encoding="utf-8"))
        golden = _matcher.golden_for(entry["name"], _interpreters()[0])
        seeded = dict(doc)
        seeded["facts"] = list(doc["facts"]) + [{
            "concern": "data-model", "kind": "type", "value": "NoSuchModel (struct, public)",
            "evidence": "Package.swift:1-1", "status": "must-render"}]
        problems = _matcher.match(seeded, golden)
        self.assertEqual([p["value"] for p in problems], ["NoSuchModel (struct, public)"])


if __name__ == "__main__":
    unittest.main()
