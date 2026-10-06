#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml>=6.0",
# ]
# ///
"""visualize-run-progress — render a promptbook run snapshot's progress.

Per ADR-0025. READ-ONLY: derives a progress view from a run snapshot (the
ADR-0023 `.yaml` format, or a legacy `.md` snapshot) and its book, and renders
it two ways:

  * terminal (default) — a progress bar + a colored per-prompt checklist;
  * markdown (--markdown) — a byte-stable artifact written next to the snapshot
    at `run-RUN-NNN-progress.md` (no timestamps in the body; deterministic).

It never mutates run state (derive, don't decide — the link-adr-graph principle).
`module_tag` lives only on the *book* (`promptbook.schema.json`), not the run's
`prompts[]` (`run.schema.json`), so it is joined from the book by `n` after the
run's `book_content_hash` verifies the plan still matches; on a mismatch (a
plan edited in place) or a legacy `.md` run it renders `—`.

Exit codes: 0 clean; 1 not-found / validation error (the repo convention);
2 environment cannot supply a real YAML parser (PB-0026 capability error —
remediation on stderr, nothing on stdout).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_sibling(modname: str, filename: str):
    """Import a sibling script (incl. hyphenated names) by path."""
    spec = importlib.util.spec_from_file_location(modname, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The vendored promptbook validator owns load_yaml + compute_book_hash +
# validate_file; reuse it so parsing/hashing/validation stay in lock-step.
_vp = _load_sibling("validate_promptbook", "validate-promptbook.py")
load_yaml = _vp.load_yaml
compute_book_hash = _vp.compute_book_hash
# SINGLE-INSTANCE RULE (PB-0026 review): the capability guard + exception
# class must come from the SAME _yaml_min instance _vp.load_yaml raises from
# — a fresh sibling load would create a distinct class our except clauses
# silently fail to catch. _vp re-exports both names.
ensure_real_yaml = _vp.ensure_real_yaml
YamlCapabilityError = _vp.YamlCapabilityError

# Repo-root .crux config (ADR-0032) — supplies the docs tree location.
_crux_config = _load_sibling("crux_config", "crux_config.py")
validate_file = _vp.validate_file

# ----------------------------------------------------------------------------
# Rendering constants
# ----------------------------------------------------------------------------
STATES = ["done", "skipped", "blocked", "running", "pending"]
TERMINAL_STATES = {"done", "skipped", "blocked"}
GLYPH = {"done": "✓", "skipped": "⊘", "blocked": "✗", "running": "▸", "pending": "·"}
# state -> ANSI SGR code (per ADR-0025): done=green, skipped=yellow, blocked=red,
# running=cyan(bold), pending=dim.
COLOR = {"done": "32", "skipped": "33", "blocked": "31", "running": "36;1", "pending": "2"}

# Strip C0 (except tab) + DEL + C1 control bytes — defends the terminal renderer
# against ANSI/escape injection from author-controlled title/result, and keeps
# the markdown cells clean.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _sanitize(s: str) -> str:
    """Remove control/escape bytes from author-controlled text."""
    return _CONTROL_RE.sub("", s or "")


def _md_cell(s: str) -> str:
    """Sanitize + escape a value for a Markdown table cell (no pipe/newline breaks)."""
    return _sanitize(s).replace("|", r"\|").replace("\n", " ").strip()


# ----------------------------------------------------------------------------
# Loading / format detection
# ----------------------------------------------------------------------------
class ProgressError(Exception):
    """Surfaced to the user with exit code 1."""


def _detect_and_load(path: Path, kind: str) -> dict:
    """Load a `.yaml` (validated) or legacy `.md` promptbook/run document.

    `kind` is "run" or "promptbook" — used for `.yaml` schema validation.
    """
    if not path.exists():
        raise ProgressError(f"not found: {path}")
    if path.suffix == ".yaml":
        try:
            doc = load_yaml(path.read_text(encoding="utf-8"))
        except YamlCapabilityError:
            raise  # environment problem -> main()'s exit-2 lane, not a parse finding
        except Exception as exc:  # malformed YAML must surface as a clean exit-1, not a traceback
            raise ProgressError(f"could not parse {path}: {exc}")
        if not isinstance(doc, dict) or "format_version" not in doc:
            raise ProgressError(f"malformed new-format document (no format_version): {path}")
        code, errors = validate_file(path, kind)
        if code != 0:
            msgs = "; ".join(e.get("message", str(e)) for e in errors[:5])
            raise ProgressError(f"{path} fails {kind}.schema.json: {msgs}")
        return doc
    if path.suffix == ".md":
        try:
            return _parse_legacy(path.read_text(encoding="utf-8"), kind)
        except YamlCapabilityError:
            raise  # environment problem -> main()'s exit-2 lane
        except Exception as exc:
            raise ProgressError(f"could not parse legacy {path}: {exc}")
    raise ProgressError(f"unrecognized extension (expected .yaml/.md): {path}")


_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_LEGACY_PROMPT_RE = re.compile(r"^##+ +Prompt +(\d+) +[—-] +(.*?) *$", re.MULTILINE)
_LEGACY_STATE_RE = re.compile(r"\*\*State:\*\* *(\w+)")


def _parse_legacy(text: str, kind: str) -> dict:
    """Minimal legacy-`.md` parse: frontmatter scalars + `## Prompt N — title` blocks."""
    doc: dict = {"format_version": None}
    m = _FM_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition(":")
                doc[k.strip()] = v.strip().strip('"') or None
    prompts = []
    matches = list(_LEGACY_PROMPT_RE.finditer(text))
    for i, mt in enumerate(matches):
        block = text[mt.end():(matches[i + 1].start() if i + 1 < len(matches) else len(text))]
        sm = _LEGACY_STATE_RE.search(block)
        prompts.append({
            "n": int(mt.group(1)),
            "title": mt.group(2).strip(),
            "state": (sm.group(1) if sm else "pending"),
        })
    doc["prompts"] = prompts
    return doc


# ----------------------------------------------------------------------------
# Resolution: target (path | PB-NNNN) -> (run_path, book_path|None)
# ----------------------------------------------------------------------------
# Accepts an optional non-reserved artifact prefix per ADR-0032 (CRX-PB-0040).
# The reserved alternation must stay in lock-step with crux_config.RESERVED_PREFIXES.
_PB_RE = re.compile(r"^(?:(?!(?:PB|ADR|RUN|BRIEF)-)[A-Z][A-Z0-9]{1,9}-)?PB-\d{4}$")


_DOCS_ROOT_CACHE: Path | None = None


def _docs_root() -> Path:
    # Resolved exactly once per process (the ADR-0032 resolve-once rule);
    # this function is hit multiple times per invocation.
    global _DOCS_ROOT_CACHE
    if _DOCS_ROOT_CACHE is not None:
        return _DOCS_ROOT_CACHE
    try:
        cfg = _crux_config.load_config()
    except _crux_config.CruxConfigError as exc:
        raise ProgressError(f".crux configuration error: {exc}")
    root = cfg.docs_root / "promptbooks"
    if not root.is_dir():
        raise ProgressError(f"{cfg.docs_dir}/promptbooks/ not found (run from the project root)")
    _DOCS_ROOT_CACHE = root
    return root


def _find_book(pb_id: str) -> Path | None:
    root = _docs_root()
    for sub in ("active", "archive"):
        for ext in (".yaml", ".md"):
            hits = sorted((root / sub).glob(f"{pb_id}-*{ext}")) if (root / sub).is_dir() else []
            if hits:
                return hits[0]
    return None


def _highest_run(run_dir: Path) -> Path | None:
    if not run_dir.is_dir():
        return None
    runs = list(run_dir.glob("run-RUN-*.yaml")) + list(run_dir.glob("run-RUN-*.md"))
    if not runs:
        return None

    def _key(p: Path) -> tuple[int, int]:
        m = re.search(r"RUN-(\d+)", p.name)
        # highest run number wins; for the SAME number prefer .yaml (the
        # authoritative post-migration format) over a coexisting legacy .md.
        return (int(m.group(1)) if m else -1, 1 if p.suffix == ".yaml" else 0)

    return max(runs, key=_key)


def resolve(target: str, run_override: str | None) -> tuple[Path, Path | None]:
    """Return (run_path, book_path). `target` is an explicit run-file path or a PB-NNNN id."""
    p = Path(target)
    if p.exists() and p.is_file():
        # explicit run path; locate its book by id from the doc.
        doc = _detect_and_load(p, "run")
        book = _find_book(doc.get("book_id", "")) if doc.get("book_id") else None
        return p, book
    if not _PB_RE.match(target):
        raise ProgressError(f"target is neither an existing file nor a PB-NNNN id: {target}")
    book = _find_book(target)
    if book is None:
        raise ProgressError(f"no book found for {target} under {_docs_root()}/{{active,archive}}/")
    slug_dir = _docs_root() / "runs" / book.stem
    if run_override:
        for ext in (".yaml", ".md"):
            cand = slug_dir / f"run-{run_override}{ext}"
            if cand.exists():
                return cand, book
        raise ProgressError(f"run {run_override} not found under {slug_dir}")
    # default: book.current_run, else highest run in the dir
    bdoc = _detect_and_load(book, "promptbook")
    cur = bdoc.get("current_run")
    if cur:
        for ext in (".yaml", ".md"):
            cand = slug_dir / f"run-{cur}{ext}"
            if cand.exists():
                return cand, book
    hi = _highest_run(slug_dir)
    if hi is None:
        raise ProgressError(f"no runs found under {slug_dir}")
    return hi, book


# ----------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------
def build_model(run_path: Path, book_path: Path | None) -> dict:
    """Derive the progress model: rows of (n, title, module_tag, state) + counts."""
    run = _detect_and_load(run_path, "run")
    run_prompts = run.get("prompts", []) or []

    # module_tag join: only valid for a .yaml book whose hash matches the run.
    tag_by_n: dict[int, str] = {}
    joined = False
    if book_path is not None and book_path.suffix == ".yaml" and run_path.suffix == ".yaml":
        book = _detect_and_load(book_path, "promptbook")
        if run.get("book_content_hash") == compute_book_hash(book):
            for bp in book.get("prompts", []) or []:
                if bp.get("module_tag"):
                    tag_by_n[bp["n"]] = bp["module_tag"]
            joined = True

    rows = []
    for p in run_prompts:
        rows.append({
            "n": p["n"],
            "title": p.get("title", ""),
            "module_tag": tag_by_n.get(p["n"], "—") if joined else "—",
            "state": p.get("state", "pending"),
        })
    counts = Counter(r["state"] for r in rows)
    total = len(rows)
    terminal = sum(counts.get(s, 0) for s in TERMINAL_STATES)
    pct = round(100 * terminal / total) if total else 0
    return {
        "run_id": run.get("run_id", run_path.stem),
        "book_id": run.get("book_id", ""),
        "status": run.get("status", ""),
        "rows": rows,
        "counts": counts,
        "total": total,
        "terminal": terminal,
        "done": counts.get("done", 0),
        "pct": pct,
        "tag_joined": joined,
    }


# ----------------------------------------------------------------------------
# Renderers
# ----------------------------------------------------------------------------
def _bar(pct: int, width: int = 24) -> str:
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def render_terminal(model: dict, color: bool) -> str:
    def c(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if color else s

    out = []
    out.append(f"{model['book_id']} · {model['run_id']} · {model['status']}")
    counts = model["counts"]
    breakdown = "  ".join(
        f"{GLYPH[s]} {counts.get(s, 0)} {s}" for s in STATES if counts.get(s, 0)
    )
    out.append(f"[{_bar(model['pct'])}] {model['pct']}%  ({model['terminal']}/{model['total']} terminal, {model['done']} done)")
    if breakdown:
        out.append(breakdown)
    out.append("")
    for r in model["rows"]:
        glyph = c(COLOR.get(r["state"], "2"), GLYPH.get(r["state"], "?"))
        tag = "" if r["module_tag"] == "—" else f" [{r['module_tag']}]"
        title = _sanitize(r["title"])
        out.append(f"  {glyph} {r['n']:>2}. {title}{tag}")
    return "\n".join(out) + "\n"


def render_markdown(model: dict) -> str:
    """Byte-stable artifact body — NO timestamps (the 'when' lives in docs/log.md)."""
    out = []
    out.append(f"# Run progress — {_sanitize(model['book_id'])} / {_sanitize(model['run_id'])}")
    out.append("")
    out.append(
        "> Regenerated by `run-promptbook` status. Body wholly rewritten "
        "on every run; hand-edits are blown away. No timestamps in the body — "
        "byte-stable given the same run state."
    )
    out.append("")
    out.append(f"**Status:** {_sanitize(model['status'])} · "
               f"**Progress:** {model['terminal']}/{model['total']} terminal ({model['pct']}%) · "
               f"**done:** {model['done']}")
    out.append("")
    counts = model["counts"]
    out.append("| state | count |")
    out.append("|-------|-------|")
    for s in STATES:
        out.append(f"| {s} | {counts.get(s, 0)} |")
    out.append("")
    if not model["tag_joined"]:
        out.append("_`module_tag` unavailable (legacy `.md` run or `book_content_hash` "
                   "mismatch on a plan edited in place); rendered as `—`._")
        out.append("")
    out.append("| n | state | module_tag | title |")
    out.append("|---|-------|------------|-------|")
    for r in model["rows"]:
        out.append(f"| {r['n']} | {GLYPH.get(r['state'], '?')} {r['state']} "
                   f"| {_md_cell(r['module_tag'])} | {_md_cell(r['title'])} |")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _use_color(force: bool | None) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if force is not None:
        return force
    return sys.stdout.isatty()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="visualize-run-progress",
        description="Render a promptbook run snapshot's progress (terminal view or byte-stable Markdown artifact).",
    )
    ap.add_argument("target", help="A PB-NNNN id or a path to a run snapshot (.yaml/.md).")
    ap.add_argument("--run", dest="run", default=None, metavar="RUN-NNN",
                    help="Select a specific run when target is a PB-NNNN id (default: current_run, else highest).")
    ap.add_argument("--markdown", action="store_true", help="Write the byte-stable progress.md artifact.")
    ap.add_argument("--terminal", action="store_true", help="Print the terminal view (default when neither flag is given).")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI color (also honors NO_COLOR / non-TTY).")
    args = ap.parse_args(argv)
    # Correctness-critical: the module_tag join verifies book_content_hash, so
    # parse fidelity matters — require real YAML (PB-0026), uv-repair if
    # possible. After parse_args so --help works without PyYAML.
    try:
        ensure_real_yaml(__file__)
    except YamlCapabilityError as exc:
        print(f"visualize-run-progress: {exc}", file=sys.stderr)
        return 2

    try:
        run_path, book_path = resolve(args.target, args.run)
        model = build_model(run_path, book_path)
        wrote = None
        if args.markdown:
            artifact = run_path.with_name(run_path.stem + "-progress.md")
            artifact.write_text(render_markdown(model), encoding="utf-8")
            wrote = artifact
        if args.terminal or not args.markdown:
            color = _use_color(False if args.no_color else None)
            sys.stdout.write(render_terminal(model, color))
        if wrote is not None:
            print(f"wrote {wrote}", file=sys.stderr)
    except ProgressError as e:
        # _sanitize: an error can embed an attacker-adjacent filename; strip
        # control bytes so a crafted name can't smuggle terminal escapes.
        print(f"visualize-run-progress: {_sanitize(str(e))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
