"""Test suite for crux-env.py (CLI) and crux_env.py (module).

Per PB-0002, Prompt 5. Covers the eleven required behaviours pinned in the
prompt-book: file-mode invariants, set/get round-trip, special-character
handling, `check` JSON output, `rm`, value-secrecy in `list` and the log,
the module's `require` exception shape, bool casting, and CLI/module parity.

Uses only stdlib (unittest, tempfile, subprocess, pathlib, os, json, stat,
shutil, sys). Each test isolates CRUX_HOME into a fresh tempdir and
cleans up in tearDown.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # crux/
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
CLI = SCRIPTS_DIR / "crux-env.py"


def run_cli(*args, env=None, check=False):
    """Run crux-env.py with the given args. Returns CompletedProcess."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=full_env,
        check=check,
    )


def write_required_yml(home: str, body: str) -> None:
    """Write a required.yml at the given CRUX_HOME."""
    rp = Path(home) / "required.yml"
    rp.write_text(body, encoding="utf-8")
    os.chmod(rp, 0o600)


# ─── shell-export parser for the parity test ────────────────────────────────
_EXPORT_RE = re.compile(r"^export ([A-Z][A-Z0-9_]*)=(.*)$")


def _parse_shell_exports(stdout: str) -> dict[str, str]:
    """Parse `export KEY=value` lines (as emitted by `load`) back into a dict.

    Uses shlex.split to undo shlex.quote's single-quote escaping.
    """
    import shlex

    result: dict[str, str] = {}
    for line in stdout.splitlines():
        m = _EXPORT_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        rhs = m.group(2)
        # shlex.split treats the RHS as a single shell-quoted token.
        parts = shlex.split(rhs)
        result[key] = parts[0] if parts else ""
    return result


class CruxEnvTestCase(unittest.TestCase):
    """Base class: each test gets a fresh CRUX_HOME tempdir."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="crux-test-")
        self.env = {"CRUX_HOME": self.tmpdir}

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ───────────────────────────── CLI tests ────────────────────────────────────


class TestCLI(CruxEnvTestCase):
    def test_init_creates_tree_with_correct_modes(self):
        result = run_cli("init", env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)

        env_file = Path(self.tmpdir) / "env"
        required = Path(self.tmpdir) / "required.yml"
        logd = Path(self.tmpdir) / "log"
        secretsd = Path(self.tmpdir) / "secrets"

        self.assertTrue(env_file.exists(), "env file should exist")
        self.assertTrue(required.exists(), "required.yml should exist")
        self.assertTrue(logd.is_dir(), "log/ should exist as dir")
        self.assertTrue(secretsd.is_dir(), "secrets/ should exist as dir")

        # Low 12 mode bits (suid/sgid/sticky + perms). chmod() set 0700/0600.
        self.assertEqual(stat.S_IMODE(os.stat(env_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(required).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(logd).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(secretsd).st_mode), 0o700)

    def test_set_then_get_returns_value(self):
        run_cli("init", env=self.env, check=True)
        r1 = run_cli("set", "FOO", "bar", env=self.env)
        self.assertEqual(r1.returncode, 0, r1.stderr)

        r2 = run_cli("get", "FOO", env=self.env)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(r2.stdout.strip(), "bar")

    def test_set_with_special_chars_preserves_value(self):
        run_cli("init", env=self.env, check=True)

        # Value containing a space.
        r_set1 = run_cli("set", "BAZ", "hello world", env=self.env)
        self.assertEqual(r_set1.returncode, 0, r_set1.stderr)
        r_get1 = run_cli("get", "BAZ", env=self.env)
        self.assertEqual(r_get1.returncode, 0, r_get1.stderr)
        self.assertEqual(r_get1.stdout.strip(), "hello world")

        # Value containing a `#`.
        r_set2 = run_cli("set", "QUUX", "value with #hash", env=self.env)
        self.assertEqual(r_set2.returncode, 0, r_set2.stderr)
        r_get2 = run_cli("get", "QUUX", env=self.env)
        self.assertEqual(r_get2.returncode, 0, r_get2.stderr)
        self.assertEqual(r_get2.stdout.strip(), "value with #hash")

    def test_check_passes_when_required_keys_present(self):
        run_cli("init", env=self.env, check=True)
        write_required_yml(
            self.tmpdir,
            "projects:\n  test:\n    required:\n      - FOO\n    optional: []\n",
        )
        run_cli("set", "FOO", "bar", env=self.env, check=True)

        r = run_cli("check", "--project", "test", env=self.env)
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout!r} stderr={r.stderr!r}")

    def test_check_fails_with_json_on_missing(self):
        run_cli("init", env=self.env, check=True)
        write_required_yml(
            self.tmpdir,
            "projects:\n  test:\n    required:\n      - FOO\n    optional: []\n",
        )
        # Deliberately do not set FOO.

        r = run_cli("check", "--project", "test", env=self.env)
        self.assertEqual(r.returncode, 1, f"stderr={r.stderr!r}")

        data = json.loads(r.stdout)
        self.assertIn("missing", data)
        self.assertEqual(
            data["missing"],
            [{"project": "test", "keys": ["FOO"]}],
        )

    def test_rm_then_get_returns_missing(self):
        run_cli("init", env=self.env, check=True)
        run_cli("set", "X", "y", env=self.env, check=True)

        r_rm = run_cli("rm", "X", env=self.env)
        self.assertEqual(r_rm.returncode, 0, r_rm.stderr)

        r_get = run_cli("get", "X", env=self.env)
        self.assertEqual(r_get.returncode, 1, "get on removed key should exit 1")

    def test_list_never_prints_values(self):
        run_cli("init", env=self.env, check=True)
        write_required_yml(
            self.tmpdir,
            "projects:\n  test:\n    required:\n      - SECRET\n    optional: []\n",
        )
        run_cli("set", "SECRET", "sensitive_value", env=self.env, check=True)

        r = run_cli("list", "--project", "test", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        # The key name MAY appear; the value MUST NOT.
        self.assertNotIn("sensitive_value", r.stdout)
        self.assertNotIn("sensitive_value", r.stderr)
        self.assertIn("SECRET", r.stdout)  # sanity: key name listed

    def test_log_never_records_values(self):
        run_cli("init", env=self.env, check=True)
        run_cli("set", "TOKEN", "super_secret", env=self.env, check=True)

        log_file = Path(self.tmpdir) / "log" / "crux-env.log"
        self.assertTrue(log_file.exists(), "log file should exist after set")
        contents = log_file.read_text(encoding="utf-8")
        self.assertNotIn("super_secret", contents, f"log leaked value: {contents!r}")
        self.assertIn("TOKEN", contents, "log should record the key name")


# ───────────────────────────── module tests ─────────────────────────────────


class TestModule(CruxEnvTestCase):
    def setUp(self):
        super().setUp()
        # Place the scripts dir on sys.path so `import crux_env` works.
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        # The module reads CRUX_HOME from os.environ at call time.
        self._prev_home = os.environ.get("CRUX_HOME")
        os.environ["CRUX_HOME"] = self.tmpdir
        # Init the tree so the module has something well-formed to read.
        run_cli("init", env=self.env, check=True)

        import crux_env  # type: ignore

        self.crux_env = crux_env
        self.crux_env._reset_cache()

    def tearDown(self):
        # Reset cache so other tests don't see this tempdir's data.
        try:
            self.crux_env._reset_cache()
        except AttributeError:
            pass
        # Restore CRUX_HOME.
        if self._prev_home is None:
            os.environ.pop("CRUX_HOME", None)
        else:
            os.environ["CRUX_HOME"] = self._prev_home
        super().tearDown()

    def test_module_require_raises_on_missing(self):
        # No key set — require should raise EnvNotConfigured.
        self.crux_env._reset_cache()
        with self.assertRaises(self.crux_env.EnvNotConfigured) as ctx:
            self.crux_env.require("MISSING_KEY")
        exc = ctx.exception
        self.assertEqual(exc.missing_keys, ["MISSING_KEY"])
        self.assertIn("crux-env set MISSING_KEY", exc.remediation)

    def test_module_get_optional_bool_casting(self):
        # Truthy strings (case-insensitive).
        for truthy in ("true", "TRUE", "True", "1", "yes", "YES", "on", "ON"):
            run_cli("set", "FLAG", truthy, env=self.env, check=True)
            self.crux_env._reset_cache()
            self.assertIs(
                self.crux_env.get_optional("FLAG", default=False, cast=bool),
                True,
                f"value {truthy!r} should cast to True",
            )

        # Falsy strings (case-insensitive).
        for falsy in ("false", "FALSE", "False", "0", "no", "NO", "off", "OFF"):
            run_cli("set", "FLAG", falsy, env=self.env, check=True)
            self.crux_env._reset_cache()
            self.assertIs(
                self.crux_env.get_optional("FLAG", default=True, cast=bool),
                False,
                f"value {falsy!r} should cast to False",
            )

        # Garbage values should raise ValueError.
        run_cli("set", "FLAG", "garbage", env=self.env, check=True)
        self.crux_env._reset_cache()
        with self.assertRaises(ValueError):
            self.crux_env.get_optional("FLAG", default=False, cast=bool)

    def test_module_and_cli_produce_identical_dict(self):
        # Write a deliberately tricky env file by hand — comments, blank
        # lines, unquoted, quoted-with-space, quoted-with-hash, quoted with
        # both escape sequences.
        env_file = Path(self.tmpdir) / "env"
        env_file.write_text(
            "# header comment\n"
            "\n"
            "PLAIN=simple\n"
            "# midfile comment\n"
            'QUOTED_SPACE="hello world"\n'
            'QUOTED_HASH="value with #hash"\n'
            'QUOTED_ESC="quote \\" inside"\n'
            'QUOTED_BACKSLASH="back\\\\slash"\n'
            "\n",
            encoding="utf-8",
        )
        os.chmod(env_file, 0o600)

        # Module side.
        self.crux_env._reset_cache()
        mod_dict = self.crux_env.load_all()

        # CLI side.
        r = run_cli("load", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        cli_dict = _parse_shell_exports(r.stdout)

        self.assertEqual(
            mod_dict,
            cli_dict,
            f"\nmodule: {mod_dict!r}\ncli:    {cli_dict!r}",
        )

    # ───────── parser lock-step: divergence cases must match ─────────
    #
    # For each malformed-fixture case, the module and the CLI MUST agree —
    # both raise (or both accept and produce the same dict). The CLI's
    # `load` subcommand returns nonzero stdout-on-error when the env file
    # is malformed (it crashes with an uncaught ValueError, which surfaces
    # on stderr and yields a nonzero exit). What matters here is parity:
    # if the module rejects, the CLI must reject too.

    def _module_parses_or_raises(self) -> tuple[bool, str]:
        """Return (accepted, detail). accepted=True if module returned a
        dict; False with detail=error message if it raised ValueError."""
        self.crux_env._reset_cache()
        try:
            self.crux_env.load_all()
            return True, ""
        except ValueError as exc:
            return False, str(exc)

    def _cli_parses_or_raises(self) -> tuple[bool, str]:
        """Return (accepted, detail). accepted=True if CLI `load` exited 0."""
        r = run_cli("load", env=self.env)
        if r.returncode == 0:
            return True, ""
        return False, r.stderr or r.stdout

    def _assert_parser_parity(self, env_text: str, label: str) -> None:
        env_file = Path(self.tmpdir) / "env"
        env_file.write_text(env_text, encoding="utf-8")
        os.chmod(env_file, 0o600)

        mod_accepted, mod_detail = self._module_parses_or_raises()
        cli_accepted, cli_detail = self._cli_parses_or_raises()

        self.assertEqual(
            mod_accepted,
            cli_accepted,
            f"{label}: parser divergence — module accepted={mod_accepted} "
            f"({mod_detail!r}), CLI accepted={cli_accepted} ({cli_detail!r})",
        )

    def test_parser_parity_leading_whitespace_before_key(self):
        # "  KEY=value" — both parsers must reject leading whitespace.
        self._assert_parser_parity(
            "  KEY=value\n",
            "leading whitespace before key",
        )

    def test_parser_parity_padding_between_eq_and_value(self):
        # "KEY= value" — both parsers must reject padding after `=`.
        # (Quoting the value lets a user store leading whitespace
        # intentionally, so this padding is unambiguous to reject.)
        self._assert_parser_parity(
            "KEY= value\n",
            "padding between = and value",
        )

    def test_parser_parity_unknown_escape(self):
        # \n is not a recognized escape — both parsers must reject.
        self._assert_parser_parity(
            'KEY="back\\nslash"\n',
            "unknown escape sequence",
        )

    def test_parser_parity_unquoted_embedded_whitespace(self):
        # "KEY=foo bar" — both parsers must reject embedded whitespace
        # in unquoted values.
        self._assert_parser_parity(
            "KEY=foo bar\n",
            "unquoted embedded whitespace",
        )


# ─────────────── additional CLI tests for the bug fixes ─────────────────────


class TestCLIBugFixes(CruxEnvTestCase):
    def test_cmd_set_rejects_empty_value(self):
        run_cli("init", env=self.env, check=True)
        r = run_cli("set", "FOO", "", env=self.env)
        self.assertEqual(r.returncode, 1, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        # Helpful message points to `rm`.
        self.assertIn("rm FOO", r.stderr)
        # Nothing should have been written.
        env_file = Path(self.tmpdir) / "env"
        contents = env_file.read_text(encoding="utf-8")
        self.assertNotIn("FOO=", contents, f"unexpected write: {contents!r}")

    def test_cmd_set_records_rotate_on_replacement(self):
        run_cli("init", env=self.env, check=True)
        # First set → logs `set`.
        run_cli("set", "FOO", "first", env=self.env, check=True)
        # Replace → logs `rotate`.
        run_cli("set", "FOO", "second", env=self.env, check=True)

        log_file = Path(self.tmpdir) / "log" / "crux-env.log"
        log_lines = log_file.read_text(encoding="utf-8").splitlines()
        # Find the lines mentioning FOO. There should be exactly two: one
        # `set` and one `rotate`.
        foo_lines = [ln for ln in log_lines if "  FOO" in ln]
        self.assertEqual(len(foo_lines), 2, f"expected 2 FOO ops, got: {foo_lines!r}")
        ops = [ln.split("  ")[1] for ln in foo_lines]
        self.assertEqual(ops, ["set", "rotate"], f"ops: {ops!r}")
        # No value ever appears.
        contents = log_file.read_text(encoding="utf-8")
        self.assertNotIn("first", contents)
        self.assertNotIn("second", contents)

    def test_cmd_check_unknown_project_emits_json_and_exits_1(self):
        run_cli("init", env=self.env, check=True)
        write_required_yml(
            self.tmpdir,
            "projects:\n  test:\n    required:\n      - FOO\n    optional: []\n",
        )
        r = run_cli("check", "--project", "nonexistent", env=self.env)
        self.assertEqual(r.returncode, 1, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        # Must emit JSON on stdout per exit-code convention.
        data = json.loads(r.stdout)
        self.assertEqual(data.get("error"), "unknown_project")
        self.assertEqual(data.get("project"), "nonexistent")
        self.assertIn("test", data.get("known_projects", []))
        # Additive first-run UX hint (existing keys unchanged).
        self.assertIn("required.yml", data.get("hint", ""))
        self.assertIn("--project", data.get("hint", ""))

    def test_cmd_list_unknown_project_exits_1_with_json(self):
        run_cli("init", env=self.env, check=True)
        write_required_yml(
            self.tmpdir,
            "projects:\n  test:\n    required:\n      - FOO\n    optional: []\n",
        )
        r = run_cli("list", "--project", "nonexistent", env=self.env)
        # MUST agree with cmd_check: unknown project is a user error → exit 1.
        self.assertEqual(r.returncode, 1, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data.get("error"), "unknown_project")
        self.assertEqual(data.get("project"), "nonexistent")
        # Additive first-run UX hint — list mirrors check.
        self.assertIn("required.yml", data.get("hint", ""))

    def test_cmd_check_required_yml_parse_error_emits_json(self):
        run_cli("init", env=self.env, check=True)
        # Write a malformed required.yml that the loader can't parse.
        write_required_yml(
            self.tmpdir,
            "projects:\n  test:\n    bogus_field: oops\n",
        )
        r = run_cli("check", env=self.env)
        self.assertEqual(r.returncode, 1, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data.get("error"), "required.yml parse failure")
        self.assertIn("detail", data)

    # ───── MF-1: file modes never permissive, even mid-write ─────────────

    def test_cmd_set_creates_env_file_at_0600_from_first_write(self):
        # No prior init — cmd_set initializes the env file on the fly.
        # The file must be 0600 from the moment it exists (no umask window).
        r = run_cli("set", "FOO", "bar", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        env_file = Path(self.tmpdir) / "env"
        self.assertTrue(env_file.exists())
        mode = stat.S_IMODE(os.stat(env_file).st_mode)
        self.assertEqual(
            oct(mode),
            "0o600",
            f"env file mode is {oct(mode)}, expected 0o600",
        )

    def test_cmd_init_creates_required_yml_at_0600(self):
        # MF-1 regression: required.yml created via cmd_init must be 0600
        # from creation, not from post-write chmod.
        r = run_cli("init", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        rp = Path(self.tmpdir) / "required.yml"
        mode = stat.S_IMODE(os.stat(rp).st_mode)
        self.assertEqual(oct(mode), "0o600", f"required.yml mode is {oct(mode)}")

    def test_log_file_created_at_0600(self):
        # MF-1 regression: log file (previously created via .touch() at default
        # mode) must be 0600 from creation.
        run_cli("init", env=self.env, check=True)
        log_file = Path(self.tmpdir) / "log" / "crux-env.log"
        self.assertTrue(log_file.exists())
        mode = stat.S_IMODE(os.stat(log_file).st_mode)
        self.assertEqual(oct(mode), "0o600", f"log mode is {oct(mode)}")

    # ───── SC-2: rm-then-set must log as `set`, not `rotate` ─────────────

    def test_rm_then_set_logs_as_set_not_rotate(self):
        run_cli("init", env=self.env, check=True)
        run_cli("set", "FOO", "bar", env=self.env, check=True)
        run_cli("rm", "FOO", env=self.env, check=True)
        run_cli("set", "FOO", "baz", env=self.env, check=True)

        log_file = Path(self.tmpdir) / "log" / "crux-env.log"
        lines = log_file.read_text(encoding="utf-8").splitlines()
        foo_lines = [ln for ln in lines if "  FOO" in ln]
        # Expect: set FOO, rm FOO, set FOO  (NOT rotate on the third).
        self.assertEqual(len(foo_lines), 3, f"expected 3 FOO ops, got: {foo_lines!r}")
        ops = [ln.split("  ")[1] for ln in foo_lines]
        self.assertEqual(
            ops,
            ["set", "rm", "set"],
            f"third op should be 'set' (key was previously removed), got ops={ops!r}",
        )


if __name__ == "__main__":
    unittest.main()
