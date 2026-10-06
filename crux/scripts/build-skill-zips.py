#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Package each crux skill into a wrapped, allowlisted zip per ADR-0006-release-skill-zips-via-github-actions.

Walks <skills-dir>/*/SKILL.md and emits one <output-dir>/<name>-<version>.zip
per skill plus one aggregate <output-dir>/crux-skills-<version>.zip.

Packaging rules (ADR-0006 §"Packaging rules (locked)"):
  - Per-skill zip's root contains exactly one directory <name>/ (wrapped layout).
  - Closed allowlist of paths included: SKILL.md (required), plus everything
    recursively under scripts/, reference/, references/, assets/, examples/.
  - Dotfiles, symlinks, and any other path are excluded.
  - File paths inside the zip are ASCII-only; UTF-8 paths reject loudly.
  - Per-skill zip ≤ 30 MiB (Anthropic Skills API cap). The aggregate has no
    size cap because it is NOT directly uploadable to Anthropic.
  - The aggregate zip ships a `README.md` at its root carrying the upload-
    incompatibility warning (per ADR-0009 — moved here from the GitHub
    Release body's static prose). The README entry uses the same
    deterministic date_time and external_attr as per-skill files so the
    aggregate zip is byte-stable across builds.

Stdlib only (Python 3.13). Exit 0 clean; exit 1 on validation failure with a
clear stderr message and partial dist/ contents cleaned up.

Stdout: one line per zip written, then a summary line.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

# `\A` / `\Z` (not `^` / `$`) so a trailing newline in --version doesn't slip
# past the gate — `$` matches before a final `\n` by default.
SEMVER_RE = re.compile(r"\A[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?\Z")

ALLOWED_SUBDIRS = ("scripts", "reference", "references", "assets", "examples")

PER_SKILL_SIZE_CAP_BYTES = 31_457_280  # 30 MiB, per ADR-0006 §"Packaging rules (locked)".

# Fixed mtime so zip bytes are reproducible across runs / hosts.
DETERMINISTIC_DATE_TIME = (1980, 1, 1, 0, 0, 0)


class PackagingError(Exception):
    """Validation failure during zip construction. Surface message on stderr."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build-skill-zips",
        description=(
            "Package each crux skill into a wrapped zip per ADR-0006. "
            "Emits dist/<name>-<version>.zip per skill and an aggregate "
            "dist/crux-skills-<version>.zip."
        ),
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Semver string used in output filenames. Validated against ADR-0006 grammar.",
    )
    parser.add_argument(
        "--skills-dir",
        default=None,
        type=Path,
        help="Directory containing skill subdirectories (default: <repo>/crux/skills).",
    )
    parser.add_argument(
        "--output-dir",
        default=Path("dist"),
        type=Path,
        help="Directory to write zips into (default: ./dist).",
    )
    return parser.parse_args(argv)


def _is_dotname(name: str) -> bool:
    return name.startswith(".")


def _collect_skill_files(skill_dir: Path) -> list[Path]:
    """Return the closed allowlist of files for a skill directory, sorted.

    Rules: SKILL.md plus every file recursively under any of ALLOWED_SUBDIRS.
    Dotfiles and symlinks are excluded at every level (including inside an
    allowed directory's recursive walk).
    """
    collected: list[Path] = []

    skill_md = skill_dir / "SKILL.md"
    # SKILL.md presence is asserted by the caller (discover_skills already
    # filtered for it); re-check here so the helper is safe to call alone.
    if not skill_md.is_file() or skill_md.is_symlink():
        raise PackagingError(f"{skill_dir.name}: SKILL.md missing or is a symlink")
    collected.append(skill_md)

    # Enumerate actual entries case-sensitively (don't use `(skill_dir / sub).exists()`
    # — that resolves through the FS and on macOS/Windows would treat `Assets/`
    # and `assets/` as equal, breaking the case-sensitive closed allowlist).
    actual_entries = set(os.listdir(skill_dir))
    for sub in ALLOWED_SUBDIRS:
        if sub not in actual_entries:
            continue
        sub_path = skill_dir / sub
        if sub_path.is_symlink() or not sub_path.is_dir():
            continue
        for root, dirs, files in os.walk(sub_path, followlinks=False):
            root_path = Path(root)
            dirs[:] = sorted(d for d in dirs if not _is_dotname(d) and not (root_path / d).is_symlink())
            for fname in sorted(files):
                if _is_dotname(fname):
                    continue
                fpath = root_path / fname
                if fpath.is_symlink() or not fpath.is_file():
                    continue
                collected.append(fpath)

    return collected


def _assert_ascii_path(rel_posix: str, skill_name: str) -> None:
    try:
        rel_posix.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PackagingError(f"{skill_name}: non-ASCII path inside zip not permitted: {rel_posix!r} ({exc})") from exc


def _write_zip_entries(
    zf: zipfile.ZipFile,
    skill_dir: Path,
    skill_name: str,
    arc_prefix: PurePosixPath,
    files: list[Path],
) -> None:
    """Write each file under arc_prefix/<rel-to-skill-dir> with deterministic metadata."""
    for fpath in files:
        rel = fpath.relative_to(skill_dir)
        arcname = arc_prefix / PurePosixPath(*rel.parts)
        arcname_str = str(arcname)
        _assert_ascii_path(arcname_str, skill_name)
        info = zipfile.ZipInfo(arcname_str, date_time=DETERMINISTIC_DATE_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        # Fixed 0644 mode so zip bytes are identical across hosts with
        # different umasks — required by the deterministic-bytes invariant.
        info.external_attr = (0o644 & 0xFFFF) << 16
        zf.writestr(info, fpath.read_bytes())


def _build_per_skill_zip(
    skill_dir: Path,
    skill_name: str,
    version: str,
    output_dir: Path,
    written: list[Path],
) -> Path:
    files = _collect_skill_files(skill_dir)
    out_path = output_dir / f"{skill_name}-{version}.zip"
    # Track the candidate path BEFORE the write so cleanup catches partials
    # produced when _write_zip_entries or the size check raises mid-stream.
    written.append(out_path)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _write_zip_entries(zf, skill_dir, skill_name, PurePosixPath(skill_name), files)
    size = out_path.stat().st_size
    if size > PER_SKILL_SIZE_CAP_BYTES:
        raise PackagingError(
            f"{skill_name}: zip is {size} bytes, exceeds {PER_SKILL_SIZE_CAP_BYTES}-byte per-skill cap"
        )
    return out_path


AGGREGATE_README_FILENAME = "README.md"


def _build_aggregate_readme(version: str) -> str:
    """Build the warning README that ships at the root of the aggregate zip.

    The packaging contract — wrapped-not-flat per-skill layout, one-zip-per-skill
    as the unit of Anthropic-side distribution — comes from
    [[adrs/ADR-0006-release-skill-zips-via-github-actions]]. The README
    inside the aggregate zip is the relocation home for the upload-
    incompatibility warning that previously lived in the GitHub Release
    body's static prose, per
    [[adrs/ADR-0009-automate-changelog-promotion-during-release]].
    """
    return (
        f"# crux-skills-{version}.zip\n"
        "\n"
        "This aggregate zip contains every crux skill for convenience —\n"
        "**it is NOT directly uploadable** to `claude.ai/Skills` or the\n"
        "Anthropic `POST /v1/skills` API endpoint. Those surfaces accept\n"
        'exactly one zip containing exactly one skill (the "wrapped, not\n'
        'flat" layout) per ADR-0006.\n'
        "\n"
        f"To upload an individual skill, grab the per-skill zip `<skill-name>-{version}.zip`\n"
        "from the same GitHub Release page (replace `<skill-name>` with the\n"
        "directory name of the skill you want).\n"
        "\n"
        "See [ADR-0006](https://github.com/bionic-coding/crux/blob/"
        f"v{version}/docs/adrs/ADR-0006-release-skill-zips-via-github-actions.md)\n"
        "and [ADR-0009](https://github.com/bionic-coding/crux/blob/"
        f"v{version}/docs/adrs/ADR-0009-automate-changelog-promotion-during-release.md)\n"
        "for the full distribution model.\n"
    )


def _build_aggregate_zip(
    skill_dirs: list[Path],
    version: str,
    output_dir: Path,
    written: list[Path],
) -> Path:
    out_path = output_dir / f"crux-skills-{version}.zip"
    written.append(out_path)
    readme_content = _build_aggregate_readme(version)
    readme_info = zipfile.ZipInfo(AGGREGATE_README_FILENAME, date_time=DETERMINISTIC_DATE_TIME)
    readme_info.compress_type = zipfile.ZIP_DEFLATED
    readme_info.external_attr = (0o644 & 0xFFFF) << 16
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(readme_info, readme_content.encode("utf-8"))
        for skill_dir in skill_dirs:
            skill_name = skill_dir.name
            files = _collect_skill_files(skill_dir)
            _write_zip_entries(zf, skill_dir, skill_name, PurePosixPath(skill_name), files)
    return out_path


def _discover_skill_dirs(skills_dir: Path) -> list[Path]:
    if not skills_dir.is_dir():
        raise PackagingError(f"skills-dir not found or not a directory: {skills_dir}")
    out: list[Path] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        if _is_dotname(entry.name):
            continue
        if not (entry / "SKILL.md").is_file():
            continue
        out.append(entry)
    return out


def _cleanup_partial(written: list[Path]) -> None:
    """Remove only zips THIS run actually wrote — never glob by version, since
    a glob would clobber prior successful runs that share the version string."""
    for entry in written:
        if entry.is_file():
            try:
                entry.unlink()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    version: str = args.version
    if not SEMVER_RE.match(version):
        print(
            f"build-skill-zips: --version {version!r} is not valid semver per ADR-0006 §'Semver input policy'",
            file=sys.stderr,
        )
        return 1

    repo_root = Path(__file__).resolve().parent.parent.parent
    skills_dir: Path = args.skills_dir if args.skills_dir is not None else (repo_root / "crux" / "skills")
    skills_dir = skills_dir.resolve() if skills_dir.exists() else skills_dir
    output_dir: Path = args.output_dir

    written: list[Path] = []
    try:
        skill_dirs = _discover_skill_dirs(skills_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for skill_dir in skill_dirs:
            per_skill_zip = _build_per_skill_zip(skill_dir, skill_dir.name, version, output_dir, written)
            print(str(per_skill_zip))

        aggregate = _build_aggregate_zip(skill_dirs, version, output_dir, written)
        print(str(aggregate))

    except PackagingError as exc:
        print(f"build-skill-zips: {exc}", file=sys.stderr)
        _cleanup_partial(written)
        return 1

    print(f"build-skill-zips: wrote {len(written)} zip(s) for version {version} to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
