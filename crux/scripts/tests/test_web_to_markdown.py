"""web-to-markdown.py: a Markdown (or plain-text) response is passed through, never
parsed as HTML.

The night-gardener's 2026-09-02 news pass found that `.md`-suffixed doc URLs
(Anthropic's platform and code docs serve `text/markdown`) came out of the
script as one garbled line: the body went through BeautifulSoup + html2text,
which treats bare text as a single text node and collapses its whitespace.
`read-news` worked around it by reading `source.html` instead of stdout.

The script is PEP 723 (requests, beautifulsoup4, html2text) and those are not
project dependencies, so it is driven end to end through `uv run --script`
against a loopback HTTP server, with `--allow-private` to pass the SSRF guard.
Guarded skip when `uv` is absent — an honest skip, never a pass.
"""
from __future__ import annotations

import http.server
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "crux" / "scripts" / "web-to-markdown.py"

try:
    from ._dev_surface import require_dev_surface
except ImportError:  # pragma: no cover - run as a bare module
    from _dev_surface import require_dev_surface

MARKDOWN_DOC = (
    "# Claude Code hooks reference\n"
    "\n"
    "Hooks run shell commands at lifecycle points.\n"
    "\n"
    "## PreToolUse\n"
    "\n"
    "- fires before a tool call\n"
    "- can block the call\n"
    "\n"
    "```json\n"
    '{"hooks": {"PreToolUse": []}}\n'
    "```\n"
    "\n"
    "## PostToolUse\n"
    "\n"
    "Fires after the call returns.\n"
    "\n"
    "## Notes\n"
    "\n"
    # Padding past the script's thin-content threshold, so the Markdown path
    # is judged on its lines and not on the fixture's size.
    + ("Each hook receives the tool input on stdin and may print a decision.\n" * 60)
)


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[str, bytes]] = {}

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        ctype, body = self.routes.get(self.path, ("text/html; charset=utf-8", b"<html><body><p>nope</p></body></html>"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:  # quiet
        pass


@unittest.skipUnless(shutil.which("uv"), "uv is required to run the PEP 723 script")
class MarkdownPassthroughTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _Handler.routes = {
            "/docs/hooks.md": ("text/markdown; charset=utf-8", MARKDOWN_DOC.encode("utf-8")),
            "/docs/plain.txt": ("text/plain; charset=utf-8", MARKDOWN_DOC.encode("utf-8")),
            "/docs/page.html": (
                "text/html; charset=utf-8",
                b"<html><head><title>Page</title></head><body><main><h1>Page</h1>"
                b"<p>one</p><p>two</p></main></body></html>",
            ),
        }
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        require_dev_surface(self, SCRIPT, "web-to-markdown.py")

    def _run(self, path: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as td:
            return subprocess.run(
                ["uv", "run", "--script", str(SCRIPT),
                 f"http://127.0.0.1:{self.port}{path}",
                 "--allow-private", "--no-images", "--output-dir", td],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
            )

    def test_a_markdown_response_keeps_its_lines(self):
        proc = self._run("/docs/hooks.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Positive control for the defect: before the fix the whole document
        # came back on one line, so the newline count is the discriminator.
        self.assertGreaterEqual(proc.stdout.count("\n"), 12, proc.stdout)
        self.assertIn("\n## PreToolUse\n", proc.stdout)
        self.assertIn("- can block the call\n", proc.stdout)
        self.assertIn('{"hooks": {"PreToolUse": []}}', proc.stdout)
        self.assertIn('"content_type": "text/markdown', proc.stderr)
        self.assertIn('"title": "Claude Code hooks reference"', proc.stderr)

    def test_a_plain_text_response_is_passed_through_too(self):
        proc = self._run("/docs/plain.txt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("\n## PostToolUse\n", proc.stdout)

    def test_html_still_goes_through_the_converter(self):
        """Control: the HTML path is unchanged — headings become Markdown."""
        proc = self._run("/docs/page.html")
        self.assertEqual(proc.returncode, 2, proc.stderr)  # thin content, by design
        self.assertIn("# Page", proc.stdout)
        self.assertNotIn("<p>", proc.stdout)


if __name__ == "__main__":
    sys.exit(unittest.main())
