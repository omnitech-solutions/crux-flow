"""Owned start parsing preserves interpretation and binds reached dependencies."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _yaml_min as shared
import council_yaml_v3 as owned
import council_history_v3 as policy
import implementation_approval as approval


class OwnedStartYamlTests(unittest.TestCase):
    def documents(self):
        fixtures = Path(__file__).parent / 'fixtures'
        return [path.read_text() for path in sorted(fixtures.glob('*valid.yaml'))] + [
            (fixtures / 'run-hand-formatted.yaml').read_text(),
            'date: 2026-05-29\ntime: 2026-05-29T12:00:00Z\n',
            'yes: true\nnumber: 12\nfloat: 1.5\nnull: null\nquoted: "001"\n',
            'text: |\n  first\n  second\nitems: [one, "two"]\n',
            '[unfinished', 'a: [one,\n', 'a:\n  b: c\n d: e\n']

    def outcome(self, module, text):
        try:
            return ('value', module.load_yaml(text))
        except Exception as exc:
            return ('error', type(exc).__name__, str(exc))

    def test_real_yaml_values_and_errors_match_existing_loader(self):
        for text in self.documents():
            with self.subTest(text=text):
                self.assertEqual(self.outcome(owned, text), self.outcome(shared, text))

    def test_isolated_fallback_values_and_errors_match_existing_loader(self):
        with mock.patch.dict(sys.modules, {'yaml': None}):
            for text in self.documents():
                with self.subTest(text=text):
                    self.assertEqual(self.outcome(owned, text), self.outcome(shared, text))

    def test_reachable_owned_declarations_are_unchanged_copies(self):
        names = {'load_yaml', '_normalize', '_parse_minimal_yaml', '_parse_scalar',
            '_strip_comment', '_looks_like_flow_or_quoted', '_split_flow',
            '_unescape_double_quoted', '_try_parse_timestamp', 'YamlCapabilityError',
            'CatalogYamlError', 'REMEDIATION', '_DATE_RE', '_DATETIME_RE'}
        def declarations(module):
            raw = Path(module.__file__).read_text()
            result = {}
            for node in ast.parse(raw).body:
                name = getattr(node, 'name', None)
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                if name in names:
                    result[name] = ast.dump(node, include_attributes=False)
            return result
        self.assertEqual(set(declarations(owned)), names)
        self.assertEqual(declarations(owned), declarations(shared))

    def test_normalizer_parser_helper_and_constant_mutations_change_seal(self):
        raw = Path(owned.__file__).read_text()
        expected = approval.production_contract_bytes('3')
        mutations = {
            '_normalize': ('def _normalize(obj: Any) -> Any:\n', '    raise RuntimeError("owned-mutation")\n',
                           lambda module: module.load_yaml('date: 2026-05-29\n')),
            '_parse_minimal_yaml': ('def _parse_minimal_yaml(text: str, *, strict: bool = False) -> Any:\n',
                           '    raise RuntimeError("owned-mutation")\n',
                           lambda module: module._parse_minimal_yaml('a: one\n')),
            '_strip_comment': ('def _strip_comment(line: str) -> str:\n',
                           '    raise RuntimeError("owned-mutation")\n',
                           lambda module: module._parse_minimal_yaml('a: one # comment\n')),
        }
        with tempfile.TemporaryDirectory() as folder:
            def load(text):
                # Same-size variants written within one second can reuse a .pyc.
                # A unique source path makes each mutation execute its own bytes.
                path = Path(folder) / f'owned_yaml_variant_{len(list(Path(folder).glob("*.py")))}.py'
                path.write_text(text)
                spec = importlib.util.spec_from_file_location('owned_yaml_variant', path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                return module
            try:
                baseline = load(raw)
                with mock.patch.object(policy, 'start_snapshot_yaml', baseline):
                    self.assertEqual(approval.gate_contract_bytes('3'), expected)
                for name, (line, addition, exercise) in mutations.items():
                    with self.subTest(name=name):
                        self.assertIn(line, raw)
                        module = load(raw.replace(line, line + addition, 1))
                        with mock.patch.object(policy, 'start_snapshot_yaml', module):
                            self.assertNotEqual(approval.gate_contract_bytes('3'), expected)
                            with self.assertRaises(approval.Refused): approval.production_contract_bytes('3')
                            with self.assertRaisesRegex(RuntimeError, 'owned-mutation'): exercise(module)
                module = load(raw.replace('_DATE_RE = re.compile(', '_DATE_RE = re.compile("NEVER" + ', 1))
                with mock.patch.object(policy, 'start_snapshot_yaml', module):
                    self.assertNotEqual(approval.gate_contract_bytes('3'), expected)
                    with self.assertRaises(approval.Refused): approval.production_contract_bytes('3')
                    self.assertIsNone(module._try_parse_timestamp('2026-05-29'))
                    self.assertIsNotNone(owned._try_parse_timestamp('2026-05-29'))
            finally:
                sys.modules.pop('owned_yaml_variant', None)

    def test_shared_loader_mutation_does_not_change_owned_interpretation_or_seal(self):
        expected = approval.production_contract_bytes('3')
        with mock.patch.object(shared, 'load_yaml', side_effect=RuntimeError('shared mutation')), \
             mock.patch.object(shared, '_normalize', return_value={'wrong': True}):
            self.assertEqual(owned.load_yaml('date: 2026-05-29\n'), {'date': '2026-05-29'})
            self.assertEqual(approval.production_contract_bytes('3'), expected)

    def test_issued_profile_two_is_byte_identical(self):
        expected = Path(__file__).parent / 'fixtures/issued-policy-two/contract.json'
        content = approval.production_contract_bytes('2')
        self.assertEqual(content, expected.read_bytes())
        self.assertEqual(len(content), 51308)
        self.assertEqual(hashlib.sha256(content).hexdigest(),
                         'c44a85a6160a9fb5ddc6ac938ce7b15d462c9d40b2808d2db92699ad15b8f4d2')
