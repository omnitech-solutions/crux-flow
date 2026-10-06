# /// script
# requires-python = ">=3.11"
# ///
"""generate-readme-footer.py — the vendored regenerator for the README version
footer, a derived REGION of a mostly-authored file.

Third conformance instance of ADR-0056. The footer's version string is a pure
function of `crux/plugin.json.version`; everything else in README.md is authored.

WHY THIS EXISTS, precisely (PB-0052 finding 6). v1.5.0 was published to the
public repo with a `v1.4.2` footer. The chain is:

    crux/plugin.json.version
        -> (was: HAND EDIT, ungated)  -> README.md footer
        -> (tools/generate-public-docs.py, verbatim) -> public/README.md footer

`tools/public-doc-prompts/readme.md` instructs the generator to reproduce the
source footer "exactly as written in the source ... never change it", and
`generate-public-docs.py` contains no version logic at all. So the DOWNSTREAM
link was always faithful — the ungated link was the HEAD of the chain, the hand
edit. This script closes that head.

The provenance-vs-correctness distinction (why the existing gate missed it):
`tools/sync_stage.py check-fingerprints` is fail-closed and works, but it is a
PROVENANCE check — it proves public/README.md matches the README.md it was
generated FROM. It structurally cannot detect that README.md's own footer was
wrong. A fingerprint gate cannot validate its own source.

Modes (shared crux exit-code convention):
  (no flag)   rewrite the README.md footer version from plugin.json; exit 0.
  --dry-run   the DRIFT GATE: exit 0 if the footer already matches, exit 1 + a
              description on drift. (exit 2 = capability error.)

SCOPE: this script owns the SOURCE `README.md` footer only. `public/README.md`
is regenerated at release time by `tools/generate-public-docs.py` and is
deliberately NOT written here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authoring_scope as _scope  # noqa: E402

# The authored footer line, with the version as its one derived field.
FOOTER_RE = re.compile(r"^(MIT licensed \(see \[LICENSE\]\(\./LICENSE\)\)\. )v(\d+\.\d+\.\d+)(.*)$", re.M)


def plugin_version(root: Path) -> str:
    return json.loads((root / "crux" / "plugin.json").read_text(encoding="utf-8"))["version"]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate the README.md version footer from crux/plugin.json."
    )
    ap.add_argument("--repo-root", default=".", help="repo root to inspect (default: the working directory)")
    ap.add_argument(
        "--dry-run", action="store_true", help="drift gate: exit 1 if the footer is stale"
    )
    args = ap.parse_args(argv)

    root = _scope.resolve_repo_root(args.repo_root)

    # SURFACE-ABSENT LANE. The footer this regenerator owns is the plugin's OWN
    # README, and its one source is `crux/plugin.json`. A consuming project has
    # neither, so a run there used to exit 2 on a path that was never going to be
    # present -- an environment crash reported for a check that does not apply.
    if not _scope.is_authoring_checkout(root, __file__):
        return _scope.print_surface_absent()

    readme = root / "README.md"
    if not readme.is_file():
        sys.stderr.write(f"generate-readme-footer.py: {readme} not found\n")
        return 2
    try:
        want = plugin_version(root)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"generate-readme-footer.py: cannot read crux/plugin.json version: {exc}\n")
        return 2

    text = readme.read_text(encoding="utf-8")
    m = FOOTER_RE.search(text)
    if not m:
        sys.stderr.write(
            "generate-readme-footer.py: no recognizable version footer in README.md "
            "(expected a 'MIT licensed (see [LICENSE](./LICENSE)). vX.Y.Z' line)\n"
        )
        return 2

    have = m.group(2)
    # JSON on every lane. Clean used to print nothing at all, and drift printed a
    # sentence, so the one reader of this gate -- `check-drift`, which parses each
    # gate's stdout as JSON regardless of exit code -- could read neither verdict.
    if args.dry_run:
        if have == want:
            print(json.dumps({"drift": False, "path": "README.md", "version": want},
                             sort_keys=True))
            return 0
        print(json.dumps(
            {"drift": True, "paths": ["README.md"], "found": have, "expected": want,
             "fix": "generate-readme-footer.py"},
            sort_keys=True))
        return 1

    if have != want:
        readme.write_text(FOOTER_RE.sub(rf"\g<1>v{want}\g<3>", text, count=1), encoding="utf-8")
        print(json.dumps({"written": "README.md", "version": want}, sort_keys=True))
        return 0
    print(json.dumps({"written": None, "version": want}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
