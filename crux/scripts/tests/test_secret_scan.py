"""Tests for secret_scan.py: key shapes, exact key, field paths, input paths.

The module has no CLI. Its public functions are the entry points the council
runner calls, so every refusal below goes through them. Every seeded secret is
synthetic. Temp directories only; the real ~/.crux is never touched.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import secret_scan  # noqa: E402
from secret_scan import KEY_SHAPES, check_input_path, scan_fields, scan_text  # noqa: E402

_BODY_CHAR = {
    "[0-9a-f]": "0",
    "[A-Za-z0-9_-]": "a",
    "[A-Za-z0-9]": "a",
    "[A-Za-z0-9_]": "a",
    "[A-Z0-9]": "A",
    "[A-Za-z0-9-]": "a",
}
GATEWAY_KEY = "sk-or-test-dummy-" + "z" * 24


def body(shape, n):
    return _BODY_CHAR[shape.body_class] * n


def shape_cases():
    return [s for s in KEY_SHAPES if s.name != "pem-private-key"]


class KeyShapeTests(unittest.TestCase):
    def test_required_shapes_are_declared(self):
        prefixes = {s.prefix for s in KEY_SHAPES}
        for prefix in ("sk-or-v1-", "sk-ant-", "sk-proj-", "sk-", "ghp_", "gho_",
                       "ghu_", "ghs_", "ghr_", "github_pat_", "AKIA", "ASIA",
                       "AIza", "xoxb-", "xoxp-", "xoxa-", "xoxr-", "xoxs-",
                       "-----BEGIN "):
            self.assertIn(prefix, prefixes)

    def test_each_shape(self):
        for shape in shape_cases():
            with self.subTest(prefix=shape.prefix):
                bare = shape.prefix
                short = shape.prefix + body(shape, shape.min_body - 1)
                full = shape.prefix + body(shape, shape.min_body)
                self.assertNotIn(shape.name, scan_text(bare))
                self.assertNotIn(shape.name, scan_text(short))
                # positive control: the fixture does trigger the shape
                self.assertIn(shape.name, scan_text(full))
                self.assertNotIn(shape.name, scan_text("x" + full))
                self.assertNotIn(shape.name, scan_text("a_" + full))
                for wrapped in (f'"{full}"', f"key={full};", f" {full}\n", f"({full})"):
                    self.assertIn(shape.name, scan_text(wrapped))

    def test_aws_requires_right_boundary(self):
        full = "AKIA" + "A" * 16
        self.assertIn("aws-access-key-id", scan_text(full))
        self.assertNotIn("aws-access-key-id", scan_text(full + "A"))
        self.assertIn("aws-access-key-id", scan_text(full + " tail"))

    def test_pem_private_key(self):
        b64 = "A" * 40
        header = "-----BEGIN RSA PRIVATE KEY-----"
        self.assertIn("pem-private-key", scan_text(f"{header}\n{b64}\n"))
        spaced = "\n".join(["AAAAAAAAAA"] * 4)
        self.assertIn("pem-private-key", scan_text(f"{header}\n{spaced}"))
        self.assertNotIn("pem-private-key", scan_text(header))
        self.assertNotIn("pem-private-key", scan_text(f"{header}\n" + "A" * 39))
        self.assertNotIn("pem-private-key", scan_text("-----BEGIN PUBLIC KEY-----\n" + b64))
        self.assertNotIn("pem-private-key", scan_text("x" + header + "\n" + b64))

    def test_plain_text_matches_nothing(self):
        self.assertEqual(scan_text("sk-learn is a library; ghp_ is a prefix"), [])
        self.assertEqual(scan_text(""), [])

    def test_names_are_sorted_and_deduplicated(self):
        a = "ghp_" + "a" * 36
        b = "ghs_" + "a" * 36
        c = "sk-or-v1-" + "0" * 64
        result = scan_text(f"{c} {a} {b}")
        self.assertEqual(result, sorted(set(result)))
        self.assertEqual(result.count("github-token"), 1)
        self.assertIn("openrouter-key", result)

    def test_non_str_raises_fixed_message(self):
        class Holder:
            def __repr__(self):
                return GATEWAY_KEY

        for bad in (b"bytes", 12, None, Holder()):
            with self.assertRaises(TypeError) as ctx:
                scan_text(bad)
            self.assertNotIn(GATEWAY_KEY, str(ctx.exception))

    def test_patterns_are_linear_on_adversarial_input(self):
        text = "-----BEGIN PRIVATE KEY----- " + "A " * 50000 + "-" * 50000
        start = time.monotonic()
        scan_text(text)
        scan_text("sk-" + "a" * 200000 + "!")
        self.assertLess(time.monotonic() - start, 5)


class SerializedTextTests(unittest.TestCase):
    """The runner scans serialized JSON before it writes, so escapes are boundaries."""

    OR_KEY = "sk-or-v1-" + "0" * 64
    GH = "ghp_" + "a" * 36

    def test_key_after_json_newline_escape(self):
        raw = "key:\n" + self.OR_KEY
        self.assertEqual(scan_text(raw), ["openrouter-key"])  # raw control
        dumped = json.dumps({"r": raw})
        self.assertIn("\\n" + self.OR_KEY, dumped)  # the fixture is escaped
        self.assertEqual(scan_text(dumped), ["openrouter-key"])

    def test_token_after_json_tab_escape(self):
        raw = "tok:\t" + self.GH
        self.assertEqual(scan_text(raw), ["github-token"])
        dumped = json.dumps([raw])
        self.assertIn("\\t" + self.GH, dumped)
        self.assertEqual(scan_text(dumped), ["github-token"])

    def test_pem_block_in_json(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(["A" * 20] * 3) + "\n-----END RSA PRIVATE KEY-----\n"
        self.assertEqual(scan_text(pem), ["pem-private-key"])
        dumped = json.dumps({"k": "x\n" + pem})
        self.assertIn("\\nAAAA", dumped)
        self.assertEqual(scan_text(dumped), ["pem-private-key"])

    def test_key_after_non_ascii_or_control_char_in_default_dumps(self):
        # json.dumps escapes non-ASCII as \\uXXXX and controls as \\b, \\f, \\u00XX.
        cases = {
            "em dash": ("\u2014", "\\u2014"),
            "no-break space": ("\u00a0", "\\u00a0"),
            "fullwidth colon": ("\uff1a", "\\uff1a"),
            "form feed": ("\x0c", "\\f"),
            "backspace": ("\x08", "\\b"),
            "control 0x01": ("\x01", "\\u0001"),
        }
        for label, (char, escaped) in cases.items():
            with self.subTest(char=label):
                raw = "k" + char + self.OR_KEY
                self.assertEqual(scan_text(raw), ["openrouter-key"])  # raw control
                dumped = json.dumps({"r": raw})
                self.assertIn(escaped + self.OR_KEY, dumped)  # the fixture is escaped
                self.assertEqual(scan_text(dumped), ["openrouter-key"])
                unescaped = json.dumps({"r": raw}, ensure_ascii=False)
                self.assertEqual(scan_text(unescaped), ["openrouter-key"])

    def test_pem_after_em_dash_in_default_dumps(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(["A" * 20] * 3)
        raw = "key \u2014" + pem
        self.assertEqual(scan_text(raw), ["pem-private-key"])
        dumped = json.dumps({"k": raw})
        self.assertIn("\\u2014-----BEGIN", dumped)
        self.assertEqual(scan_text(dumped), ["pem-private-key"])

    def test_pem_body_with_tab_separators_in_default_dumps(self):
        pem = "-----BEGIN PRIVATE KEY-----\n" + "\t".join(["A" * 20] * 3)
        self.assertEqual(scan_text(pem), ["pem-private-key"])
        dumped = json.dumps({"k": pem})
        self.assertIn("A\\tA", dumped)
        self.assertEqual(scan_text(dumped), ["pem-private-key"])

    def test_negative_controls_survive_escaped_boundaries(self):
        for prefix in ("x", "deadbeef", "\\x"):
            with self.subTest(prefix=prefix):
                dumped = json.dumps({"r": prefix + self.OR_KEY})
                self.assertEqual(scan_text(dumped), [])
        # control: the same key behind an escaped em dash is found
        self.assertEqual(scan_text(json.dumps({"r": "\u2014" + self.OR_KEY})), ["openrouter-key"])

    def test_escape_boundary_does_not_loosen_word_boundary(self):
        # positive control above; here a letter before the prefix still blocks
        self.assertEqual(scan_text("xn" + self.OR_KEY), [])
        self.assertEqual(scan_text("\\x" + self.OR_KEY), [])


class ExactKeyTests(unittest.TestCase):
    def test_exact_str_or_bytes_is_refused(self):
        text = "x " + GATEWAY_KEY
        self.assertEqual(scan_text(text, exact=[GATEWAY_KEY]), ["exact-key"])  # control
        for bad in (GATEWAY_KEY, GATEWAY_KEY.encode()):
            with self.assertRaises(TypeError) as ctx:
                scan_text(text, exact=bad)
            self.assertNotIn(GATEWAY_KEY, str(ctx.exception))
            with self.assertRaises(TypeError):
                scan_fields({"a": text}, exact=bad)

    def test_exact_value_found_as_substring(self):
        self.assertEqual(scan_text(f"token is {GATEWAY_KEY}.", exact=[GATEWAY_KEY]), ["exact-key"])

    def test_absent_exact_value_not_reported(self):
        self.assertEqual(scan_text("nothing here", exact=[GATEWAY_KEY]), [])

    def test_short_and_empty_exact_values_ignored(self):
        self.assertEqual(scan_text("abcdefg " * 3, exact=["abcdefg"]), [])
        self.assertEqual(scan_text("anything", exact=[""]), [])
        # positive control: 8 characters is long enough
        self.assertEqual(scan_text("xx abcdefgh xx", exact=["abcdefgh"]), ["exact-key"])

    def test_exact_alongside_shape(self):
        text = f"{GATEWAY_KEY} sk-or-v1-{'0' * 64}"
        self.assertEqual(scan_text(text, exact=[GATEWAY_KEY]), ["exact-key", "openrouter-key"])


class ScanFieldsTests(unittest.TestCase):
    SEED = "sk-or-v1-" + "1" * 64

    def test_paths_name_fields_never_values(self):
        record = {
            "seats": [
                {"reasoning": "fine"},
                {"reasoning": f"leaked {self.SEED} here", "role": "x"},
            ],
            "subjects": ({"path": GATEWAY_KEY},),
            self.SEED: {"nested": "ok"},
            "count": 3,
            "none": None,
        }
        result = scan_fields(record, exact=[GATEWAY_KEY])
        self.assertIn(("seats[1].reasoning", "openrouter-key"), result)
        self.assertIn(("subjects[0].path", "exact-key"), result)
        self.assertIn(("<key>", "openrouter-key"), result)
        self.assertEqual(len(result), 3)
        for secret in (self.SEED, GATEWAY_KEY):
            self.assertNotIn(secret, repr(result))

    def test_children_of_a_matching_key_do_not_leak_the_key(self):
        record = {self.SEED: {"inner": self.SEED}}
        result = scan_fields(record)
        self.assertEqual(result, [("<key>", "openrouter-key"), ("<key>.inner", "openrouter-key")])
        self.assertNotIn(self.SEED, repr(result))

    def test_clean_record_and_path_prefix(self):
        self.assertEqual(scan_fields({"a": ["b", {"c": "d"}]}), [])
        result = scan_fields(["x", self.SEED], path="root")
        self.assertEqual(result, [("root[1]", "openrouter-key")])

    def test_non_str_key_is_scanned_and_not_leaked(self):
        record = {(self.SEED,): "ok", 7: self.SEED, (1, 2): "fine"}
        result = scan_fields(record)
        self.assertIn(("<key>", "openrouter-key"), result)  # the tuple key hit
        self.assertIn(("7", "openrouter-key"), result)  # control: clean int key kept
        self.assertNotIn(self.SEED, repr(result))
        self.assertEqual(scan_fields({(1, 2): "fine"}), [])

    def test_top_level_string_has_empty_path(self):
        self.assertEqual(scan_fields(self.SEED), [("", "openrouter-key")])


class InputPathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        self.repo = base / "repo"
        self.repo.mkdir()
        self.outside = base / "outside"
        self.outside.mkdir()
        (self.repo / "sub").mkdir()
        self.good = self.repo / "sub" / "subject.md"
        self.good.write_text("ok")
        (self.outside / "file.md").write_text("x")
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("CRUX_HOME", None)

    def check(self, path, **kw):
        return check_input_path(path, self.repo, **kw)

    def make_home(self):
        # A home inside the repo, so only the crux-home rule can refuse it.
        inner = self.repo / "home"
        inner.mkdir()
        (inner / "notes.md").write_text("x")
        return inner

    def test_regular_file_accepted(self):
        self.assertIsNone(self.check(self.good))
        self.assertIsNone(self.check("sub/subject.md"))

    def test_symlink_to_in_repo_file(self):
        link = self.repo / "link.md"
        link.symlink_to(self.good)
        self.assertEqual(self.check(link), "symlink")

    def test_dangling_symlink_is_symlink_not_missing(self):
        link = self.repo / "dangling.md"
        link.symlink_to(self.repo / "nowhere.md")
        self.assertEqual(self.check(link), "symlink")

    def test_symlink_to_outside_file_is_symlink(self):
        link = self.repo / "esc.md"
        link.symlink_to(self.outside / "file.md")
        self.assertEqual(self.check(link), "symlink")

    def test_file_under_symlinked_directory(self):
        (self.repo / "dirlink").symlink_to(self.repo / "sub")
        self.assertEqual(self.check(self.repo / "dirlink" / "subject.md"), "symlink")
        self.assertEqual(self.check("dirlink/subject.md"), "symlink")

    def test_dotdot_escape(self):
        self.assertEqual(self.check("../outside/file.md"), "outside-repo")

    def test_dotdot_through_symlink_file_is_refused(self):
        (self.repo / "link.md").symlink_to(self.good)
        # control: the direct link is refused as a symlink
        self.assertEqual(self.check("link.md"), "symlink")
        self.assertEqual(self.check("sub/../link.md"), "outside-repo")

    def test_dotdot_through_symlinked_dir_is_refused(self):
        (self.repo / "insub").symlink_to(self.repo / "sub")
        self.assertEqual(self.check("insub/subject.md"), "symlink")  # control
        self.assertEqual(self.check("insub/../sub/subject.md"), "outside-repo")

    def test_dotdot_staying_inside_repo_is_refused(self):
        self.assertIsNone(self.check("sub/subject.md"))  # control
        self.assertEqual(self.check("sub/../sub/subject.md"), "outside-repo")
        self.assertEqual(self.check(self.repo / "sub" / ".." / "sub" / "subject.md"), "outside-repo")

    def test_absolute_path_through_outside_symlink_is_refused(self):
        entry = self.outside / "entry"
        entry.symlink_to(self.repo)
        self.assertIsNone(self.check(self.good))  # control: the real path is fine
        self.assertEqual(self.check(entry / "sub" / "subject.md"), "outside-repo")

    def test_absolute_path_outside_repo(self):
        self.assertEqual(self.check(self.outside / "file.md"), "outside-repo")

    def test_crux_home_by_argument(self):
        inner = self.make_home()
        self.assertIsNone(self.check(inner / "notes.md"))
        self.assertEqual(self.check(inner / "notes.md", crux_home=inner), "crux-home")

    def test_crux_home_by_environment(self):
        inner = self.make_home()
        with mock.patch.dict(os.environ, {"CRUX_HOME": str(inner)}):
            self.assertEqual(self.check(inner / "notes.md"), "crux-home")
        self.assertIsNone(self.check(inner / "notes.md"))

    def test_default_crux_home_under_user_home(self):
        fake_home = self.repo / "fakehome"
        (fake_home / ".crux").mkdir(parents=True)
        target = fake_home / ".crux" / "notes.md"
        target.write_text("x")
        self.assertIsNone(self.check(target))
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            self.assertEqual(self.check(target), "crux-home")

    def test_all_three_homes_refuse_when_set_together(self):
        user = self.repo / "fakehome"
        (user / ".crux").mkdir(parents=True)
        explicit = self.repo / "explicit"
        explicit.mkdir()
        from_env = self.repo / "fromenv"
        from_env.mkdir()
        files = {}
        for name, d in (("user", user / ".crux"), ("explicit", explicit), ("env", from_env)):
            files[name] = d / "n.md"
            files[name].write_text("x")
        with mock.patch.dict(os.environ, {"HOME": str(user), "CRUX_HOME": str(from_env)}):
            self.assertEqual(self.check(files["user"], crux_home=explicit), "crux-home")
            self.assertEqual(self.check(files["explicit"], crux_home=explicit), "crux-home")
            self.assertEqual(self.check(files["env"], crux_home=explicit), "crux-home")
        # control: with nothing set, the same files are accepted
        with mock.patch.dict(os.environ, {"HOME": str(self.repo / "elsewhere")}):
            os.environ.pop("CRUX_HOME", None)
            for f in files.values():
                self.assertIsNone(self.check(f))

    def test_crux_home_by_identity_when_spelled_in_another_case(self):
        fake_home = self.repo / "fakehome"
        (fake_home / ".crux").mkdir(parents=True)
        (fake_home / ".crux" / "env").write_text("x")
        (fake_home / ".crux" / "notes.md").write_text("x")
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            # positive control: the exact spelling is refused on every volume
            self.assertEqual(self.check(fake_home / ".crux" / "env"), "env-file")
            self.assertEqual(self.check(fake_home / ".crux" / "notes.md"), "crux-home")
            swapped = fake_home / ".CRUX"
            if not swapped.exists():
                self.skipTest("temp volume is case-sensitive; .CRUX is a different directory")
            self.assertEqual(self.check(swapped / "env"), "env-file")
            self.assertEqual(self.check(swapped / "notes.md"), "crux-home")

    def test_crux_home_identity_covers_explicit_home_spelled_in_another_case(self):
        inner = self.make_home()
        swapped = self.repo / "HOME"
        if not swapped.exists():
            self.skipTest("temp volume is case-sensitive; HOME is a different directory")
        self.assertEqual(self.check(inner / "notes.md", crux_home=inner), "crux-home")  # control
        self.assertEqual(self.check(swapped / "notes.md", crux_home=inner), "crux-home")

    def test_file_named_env_under_crux_home(self):
        inner = self.make_home()
        (inner / "env").write_text("x")
        self.assertEqual(self.check(inner / "env", crux_home=inner), "env-file")

    def test_env_file_names(self):
        for name in (".env", ".env.local", "prod.env", ".envrc"):
            with self.subTest(name=name):
                f = self.repo / name
                f.write_text("x")
                self.assertEqual(self.check(f), "env-file")
        for name in ("environment.md", "env.py", ".environment"):
            with self.subTest(name=name):
                f = self.repo / name
                f.write_text("x")
                self.assertIsNone(self.check(f))

    def test_missing_and_non_regular(self):
        self.assertEqual(self.check("sub/absent.md"), "missing")
        self.assertEqual(self.check("nodir/absent.md"), "missing")
        self.assertEqual(self.check(self.repo / "sub"), "missing")
        self.assertEqual(self.check(self.good / "below.md"), "missing")

    def test_symlink_reported_before_env_name(self):
        link = self.repo / ".env"
        link.symlink_to(self.good)
        self.assertEqual(self.check(link), "symlink")

    def test_no_value_leak_anywhere(self):
        secret = "sk-or-v1-" + "2" * 64
        (self.repo / secret).write_text("x")
        link = self.repo / (secret + ".lnk")
        link.symlink_to(self.good)
        targets = [link, "../" + secret, "sub/../" + secret, self.repo / (secret + "-gone"),
                   self.outside / secret, self.repo / secret]
        for target in targets:
            result = self.check(target)
            self.assertNotIn(secret, str(result))
        outputs = [scan_text(secret), scan_fields({secret: secret, "k": [secret]}),
                   scan_fields({(secret,): 1}), scan_text(secret, exact=[secret])]
        for out in outputs:
            self.assertNotIn(secret, repr(out))
        for call in (lambda: scan_text(secret.encode()), lambda: scan_text(secret, exact=secret),
                     lambda: scan_fields(secret, exact=secret)):
            with self.assertRaises(TypeError) as ctx:
                call()
            self.assertNotIn(secret, str(ctx.exception))
        # positive control: the shape scan does see this value
        self.assertEqual(scan_text(secret), ["openrouter-key"])


class SecretLocationTests(unittest.TestCase):
    """`check_secret_location` is the crux-home and env-file half of `check_input_path`, with no
    in-repository containment: a scratch file outside any repository is accepted."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        self.repo = base / "repo"
        self.repo.mkdir()
        self.outside = base / "outside"
        self.outside.mkdir()
        (self.outside / "file.md").write_text("x")
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("CRUX_HOME", None)

    def check(self, path):
        return check_input_path(path, self.repo)

    def test_a_file_outside_any_repository_is_accepted(self):
        from secret_scan import check_secret_location
        self.assertIsNone(check_secret_location(self.outside / "file.md"))
        self.assertEqual(self.check(self.outside / "file.md"), "outside-repo")  # the full check refuses it

    def test_crux_home_and_env_names_are_refused_anywhere(self):
        from secret_scan import check_secret_location
        inner = self.outside / "home"
        inner.mkdir()
        (inner / "notes.md").write_text("x")
        (inner / "env").write_text("x")
        self.assertIsNone(check_secret_location(inner / "notes.md"))  # control: not yet a crux home
        self.assertEqual(check_secret_location(inner / "notes.md", crux_home=inner), "crux-home")
        self.assertEqual(check_secret_location(inner / "env", crux_home=inner), "env-file")
        dotenv = self.outside / ".env"
        dotenv.write_text("x")
        self.assertEqual(check_secret_location(dotenv), "env-file")


class ModuleInvariantTests(unittest.TestCase):
    def test_module_is_stdlib_only(self):
        tree = ast.parse(Path(secret_scan.__file__).read_text())
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertTrue(roots)
        self.assertLessEqual(roots, set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
