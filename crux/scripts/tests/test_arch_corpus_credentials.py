"""Credential-format scan over the published arch-corpus outputs.

The arch-corpus goldens (`golden/`, `golden-by-python/`) and the fact
expectations (`expectations/`) under crux/scripts/tests/arch-corpus/ are
derived from pinned third-party checkouts and ARE republished to the public
repo (tools/sync_stage.py grants the whole crux subtree, and none of the three
is excluded by STAGE_IGNORE_NAMES or DENY_FILES). A scan found ZERO
credentials present today -- this is a LATENT channel, not a live leak. This
test keeps it that way: it fails the build the day any published file starts
matching a credential format, so a future re-derive from a re-cloned upstream
cannot silently reintroduce a leaked secret.

The scan's time is linear in the text it reads. Two formats, `jwt` and
`url-userinfo`, are regular expressions whose search backtracks over a long
run once per candidate start, which is quadratic in the run; each is scanned
by a linear twin (`_LINEAR_TWINS`) that a randomized test holds to the
expression's verdict.

Stdlib-only, read-only, no network.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CORPUS = Path(__file__).resolve().parent / "arch-corpus"
GOLDEN = CORPUS / "golden"

#: Every arch-corpus directory the release publishes whose files are derived
#: from a third-party checkout.
PUBLISHED_ROOTS = (GOLDEN, CORPUS / "golden-by-python", CORPUS / "expectations")

# Files that are unambiguously binary in this corpus (images etc.). The
# corpus is markdown-only today, but this list exists so a future binary
# asset added to the goldens is skipped deliberately (with a stated reason)
# rather than silently, and never skipped by extension-sniffing alone.
_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".zip"}

# Each pattern is named so a failure message says which credential FORMAT
# matched. Patterns are deliberately format-shaped (prefix/structure), not
# semantic, since this scan runs over prose/markdown, not live config.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai-sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws-akia", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-pat", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    # Scanned by `_jwt_match`, its linear twin.
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("pem-block", re.compile(r"-----BEGIN")),
    # Assignment-shaped secret: SECRET|PASSWORD|TOKEN|API_KEY|PRIVATE_KEY
    # followed by `[:=]` then >=6 non-whitespace chars.
    #
    # Case-SENSITIVE and word-boundary-anchored on the left, and both
    # choices were MEASURED against the 50 committed goldens rather than
    # assumed. Counting line matches over the real corpus:
    #
    #   this pattern (uppercase, \b-anchored)          0 matches
    #   the same pattern with re.IGNORECASE            1 match
    #
    # The single case-insensitive match is
    # `golden/rubygems-org/module-graph.md:632`, on the Ruby constant
    # `Password::CompromisedComponent` — the `::` scope operator supplies
    # the `:` the pattern reads as an assignment. Derived module-graph prose
    # is full of such constants, so a case-insensitive variant would report
    # class names as credentials.
    #
    # Uppercase is the env-var and config-key spelling convention, so
    # anchoring there keeps the pattern aimed at assignment-shaped text
    # (`API_KEY=...`, `SECRET: ...`) without narrowing it below what the
    # finding asked for. It is the only pattern in the table with a
    # meaningful false-positive risk against markdown prose.
    #
    # WHAT IT DOES NOT REACH: an all-caps Ruby or Elixir constant of the
    # form `SECRET::Foo` would match. None exists in the corpus today. If
    # one appears, the honest fix is to exempt that path with the constant
    # named, never to relax the pattern.
    # A dependency location that still carries userinfo: the shape it takes
    # when its credential is not stripped. The Swift pack strips it upstream
    # (`rule:swift-dependency-location-strips-credentials`); this is the
    # backstop over the published goldens. Two alternatives:
    #
    #   `://\S*@`                any `@` after a `://` in one token. It
    #                            covers `https://user:pass@host`, a `://`
    #                            inside the userinfo (`user:pa://ss@host`),
    #                            and an `@` inside a query (`?q=1@SECRET`).
    #   `[^/\s@:]+:[^/\s@]+@`    a scp-style `user:secret@`, which a later
    #                            `://` in the query does not hide
    #                            (`deploy:SECRET@host:org/a.git?ref=https://x`).
    #
    # MEASURED against the committed goldens: 0 matches over golden/,
    # golden-by-python/ and expectations/, while golden/ holds 9 lines with
    # a `://` and 552 lines with an `@`. A stripped location
    # (`git@github.com:org/repo.git`, `https://host/org/repo.git`) does not
    # match; `test_url_userinfo_is_silent_on_stripped_locations` pins that.
    # Scanned by `_url_userinfo_match`, its linear twin.
    ("url-userinfo", re.compile(r"://\S*@|[^/\s@:]+:[^/\s@]+@")),
    (
        "assignment-secret",
        re.compile(r"\b(SECRET|PASSWORD|TOKEN|API_KEY|PRIVATE_KEY)\s*[:=]\s*\S{6,}"),
    ),
]


#: Dependency locations as they read before their credential is stripped.
#: Every one must fire `url-userinfo` (see the per-shape positive control).
_URL_USERINFO_CONTROLS = (
    "https://user:pass@git.example.com/org/repo.git",
    "deploy:CANARYSECRET1@git.example.com:org/a.git?ref=https://x",
    "user:pa://ss@host:r",
    "https://git.example.com/org/b.git?q=1@CANARYSECRET2",
)


_TOKEN = re.compile(r"\S+")
_JWT_RUN = re.compile(r"[A-Za-z0-9_.-]+")
#: A maximal run of characters other than `/`, whitespace and `@` that ends
#: at an `@`. The lookbehind starts it only where the run starts, so each run
#: is read once.
_AT_SEGMENT = re.compile(r"(?<![^/\s@])[^/\s@]*+@")
_SCP_COLON = re.compile(r"[^:]:.")


def _jwt_match(line: str) -> str | None:
    """The `jwt` expression's verdict in linear time. Its three parts lie in
    one run of `[A-Za-z0-9_.-]`, and the first between the run's dots, so a
    match needs a dot-separated piece holding `eyJ` with a character after
    it, followed by two non-empty pieces. The first `eyJ` in a piece leaves
    the most characters after it."""
    for run in _JWT_RUN.finditer(line):
        pieces = run.group(0).split(".")
        for i in range(len(pieces) - 2):
            k = pieces[i].find("eyJ")
            if k != -1 and k + 3 < len(pieces[i]) and pieces[i + 1] and pieces[i + 2]:
                return pieces[i][k:] + "." + pieces[i + 1]
    return None


def _url_userinfo_match(line: str) -> str | None:
    r"""The `url-userinfo` expression's verdict in linear time.

    `://\S*@`: a token with an `@` after its first `://`; a later `://`
    leaves fewer characters after it. `[^/\s@:]+:[^/\s@]+@`: a run of
    characters other than `/`, whitespace and `@` that ends at an `@` and
    holds a `:` with a character other than `:` before it and any character
    after it."""
    for token in _TOKEN.finditer(line):
        text = token.group(0)
        i = text.find("://")
        if i != -1:
            j = text.find("@", i + 3)
            if j != -1:
                return text[i:j + 1]
    for segment in _AT_SEGMENT.finditer(line):
        if _SCP_COLON.search(segment.group(0)[:-1]):
            return segment.group(0)
    return None


#: The formats scanned by a linear function instead of their expression.
_LINEAR_TWINS = {"jwt": _jwt_match, "url-userinfo": _url_userinfo_match}


def _find(name: str, pattern: re.Pattern[str], line: str) -> str | None:
    twin = _LINEAR_TWINS.get(name)
    if twin is not None:
        return twin(line)
    match = pattern.search(line)
    return match.group(0) if match else None


def _scan_for_credentials(root: Path) -> list[str]:
    """Scan every file under `root` for credential-shaped matches.

    Returns a list of human-readable finding strings: "<name> in <path>:<line>
    -> <truncated match>". Never raises on decode errors -- files are read
    with errors="replace" so a stray non-UTF8 byte cannot hide a real match
    (and cannot crash the scan either).
    """
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        # Corpus is markdown-only; skip only the known-binary suffixes, and
        # do so loudly here (in findings-free silence there is nothing to
        # report, but the exclusion itself is stated, not implicit).
        if path.suffix.lower() in _BINARY_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS:
                found = _find(name, pattern, line)
                if found is not None:
                    findings.append(f"{name} in {path}:{lineno} -> {found[:12]}...")
    return findings


def _count_scanned_files(root: Path) -> int:
    return sum(
        1
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() not in _BINARY_SUFFIXES
    )


class ArchCorpusCredentialScanTests(unittest.TestCase):
    def test_golden_dir_exists_and_is_committed(self) -> None:
        # The goldens are committed on disk regardless of any .cache/ state;
        # this scan must never silently skip because of that.
        self.assertTrue(
            GOLDEN.is_dir(),
            f"expected committed golden corpus at {GOLDEN}; "
            "if this is genuinely absent, the scan below is vacuous",
        )

    def test_no_credentials_in_the_published_corpus(self) -> None:
        # The real gate: no credential-shaped text anywhere in the goldens,
        # the per-Python goldens or the fact expectations.
        self.assertEqual([r.name for r in PUBLISHED_ROOTS], ["golden", "golden-by-python", "expectations"])
        for root in PUBLISHED_ROOTS:
            with self.subTest(root=root.name):
                self.assertTrue(root.is_dir(), f"published corpus root {root} is missing")
                self.assertEqual(_scan_for_credentials(root), [])

        # Non-vacuity guard: prove the scan actually walked real files and
        # the pattern table did not collapse to nothing. There are 60
        # golden files as of this writing; 30 is a floor safely below that,
        # so a future corpus shrink still trips this guard before the corpus
        # could vanish out from under the glob. Each other root holds at
        # least one file today.
        scanned = _count_scanned_files(GOLDEN)
        self.assertGreater(scanned, 30, f"only scanned {scanned} files")
        for root in PUBLISHED_ROOTS[1:]:
            self.assertGreaterEqual(_count_scanned_files(root), 1, root)
        self.assertGreater(len(PATTERNS), 5)

    def test_positive_control_a_credential_seeded_into_each_published_root_is_found(
        self,
    ) -> None:
        # One real file from each published root, copied with one synthetic
        # credential line appended, yields exactly that one finding: the
        # scan reaches each root's real file shape, and the real content
        # adds nothing.
        for root in PUBLISHED_ROOTS:
            with self.subTest(root=root.name), tempfile.TemporaryDirectory() as tmp:
                real = next(p for p in sorted(root.rglob("*"))
                            if p.is_file() and p.suffix.lower() not in _BINARY_SUFFIXES)
                seeded = Path(tmp) / real.name
                seeded.write_text(real.read_text(encoding="utf-8", errors="replace")
                                  + "\nid = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
                findings = _scan_for_credentials(Path(tmp))
                self.assertEqual([f.split(" in ", 1)[0] for f in findings], ["aws-akia"], findings)

    def test_each_linear_twin_agrees_with_its_expression(self) -> None:
        # Random lines over the characters both formats turn on: the linear
        # twin and the expression it stands for return the same verdict.
        import random
        rng = random.Random(20260926)
        alphabets = {
            "jwt": ["a", "-", "_", ".", ".", "eyJ", "eyJ", " ", "/"],
            "url-userinfo": ["a", "Z", "9", "-", ".", ":", ":", "/", "@", "@", " ", "\t", "://"],
        }
        self.assertEqual(set(alphabets), set(_LINEAR_TWINS))
        patterns = dict(PATTERNS)
        for name, twin in _LINEAR_TWINS.items():
            pattern, alphabet = patterns[name], alphabets[name]
            hits = 0
            for trial in range(20_000):
                line = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
                expected = pattern.search(line) is not None
                hits += expected
                with self.subTest(name=name, line=line):
                    self.assertEqual(twin(line) is not None, expected)
            # Non-vacuity: both verdicts occur often.
            self.assertGreater(hits, 500, name)
            self.assertLess(hits, 19_500, name)

    def test_the_scan_time_is_linear_in_a_pathological_line(self) -> None:
        # Each line is a long run on which the raw expression backtracks
        # once per candidate start: `a:` repeated, `://` repeated and `eyJ`
        # repeated, none completing a match.
        import _timing

        def seconds_for(unit):
            def seconds(size):
                line = unit * size
                started = _timing.clock()
                for name, pattern in PATTERNS:
                    self.assertIsNone(_find(name, pattern, line))
                return _timing.clock() - started
            return seconds

        for unit in ("a:", "://", "eyJ"):
            with self.subTest(unit=unit):
                _timing.assert_grows_linearly(self, seconds_for(unit), 40_000,
                                              f"a line of {unit!r} repeated")

    def test_positive_control_every_pattern_fires_on_a_synthetic_credential(
        self,
    ) -> None:
        # Mandatory positive control: prove _scan_for_credentials actually
        # detects each pattern, not merely that it returns nothing on the
        # (currently clean) real corpus. Without this, the absence
        # assertion above would pass vacuously if the golden glob ever
        # stopped matching real files (e.g. the dir moved or was renamed).
        #
        # All values below are obviously-fake synthetic test fixtures,
        # modeled on each vendor's well-known public "this is a fake key"
        # example format (e.g. AWS's own docs use AKIAIOSFODNN7EXAMPLE).
        synthetic = {
            "openai-sk.md": "key = sk-000000000000000000000000example\n",
            "aws-akia.md": "id = AKIAIOSFODNN7EXAMPLE\n",
            "github-pat.md": "token = ghp_000000000000000000000000000000000000\n",
            # Vendor-prefix plus a same-length run of "x": fires each PATTERNS
            # entry above without matching GitHub's push-protection detectors,
            # which refused a push carrying a digit-shaped Slack value.
            "slack-token.md": "slack = xoxb-xxxxxxxxxxxxxxxxxxxxxxxxxxx\n",
            "google-api-key.md": "key = AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n",
            "jwt.md": (
                "jwt = eyJxxxxxxxxxxxxxxxxx.xxxxxxxxxxxxxxxxxxxxxxxxxxx."
                "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
            ),
            "pem-block.md": "-----BEGIN RSA PRIVATE KEY-----\nMIIExample\n",
            "assignment-secret.md": "API_KEY = abcdef1234567890\n",
            "url-userinfo.md": "url = https://user:pass@git.example.com/org/repo.git\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for filename, content in synthetic.items():
                (tmp_path / filename).write_text(content, encoding="utf-8")

            findings = _scan_for_credentials(tmp_path)

        matched_names = {finding.split(" in ", 1)[0] for finding in findings}
        for name, _pattern in PATTERNS:
            self.assertIn(
                name,
                matched_names,
                f"positive control fixture for {name!r} did not fire; "
                f"findings were: {findings}",
            )

    def test_url_userinfo_fires_on_every_unstripped_location_shape(self) -> None:
        # Positive control per shape, not per pattern: the test above passes
        # when ANY url-userinfo fixture fires, so it cannot show that each
        # shape is reached. Each value is a dependency location as it reads
        # before `_strip_location` removes its credential. The last three are
        # the review's leak shapes for the two `_strip_location` twins: a
        # scp-style credential followed by a later `://`, a `://` inside the
        # userinfo, and an `@` inside the query.
        # Each control goes through `_scan_for_credentials`, the scan the
        # golden gate runs, one file per shape.
        for index, location in enumerate(_URL_USERINFO_CONTROLS):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / f"shape-{index}.md").write_text(
                    f"| `Dep` | `{location}` |\n", encoding="utf-8")
                findings = _scan_for_credentials(Path(tmp))
                self.assertIn("url-userinfo", {f.split(" in ", 1)[0] for f in findings})

    def test_url_userinfo_is_silent_on_stripped_locations(self) -> None:
        # The paired negative: what `_strip_location` renders for each
        # control above carries no userinfo, so the widened pattern must
        # not fire on it. A pattern that fired on every URL would pass the
        # positive control and fail every golden that names a location.
        pattern = dict(PATTERNS)["url-userinfo"]
        for location in (
            "https://git.example.com/org/repo.git",
            "git.example.com:org/a.git",
            "host:r",
            "https://",
            "git@github.com:org/repo.git",
        ):
            with self.subTest(location=location):
                self.assertIsNone(pattern.search(location))


if __name__ == "__main__":
    unittest.main()
