#!/usr/bin/env python3
"""crux_spawner.py — Spawn crux into any repository.

This tool creates a customized crux instance in any repository:

1. Analyzes the target repo structure
2. Copies the crux runtime (`crux/scripts/crux/...`) into the
   target repo under `.crux-runtime/`
3. Generates a customized AGENTS.md with repo-specific hints
4. Writes the prime directive and a repo analysis under
   `.crux-runtime/box/`

Design notes:

- The spawner copies crux's runtime (`crux/scripts/crux/`).
- Spawned tree lives at `.crux-runtime/`. The name deliberately avoids
  `.crux`, which is the repo-root
  crux CONFIG FILE (per ADR-0032); the spawner never touches that file.
  Pre-rename spawned repos carrying a `.crux/` runtime directory are
  migrated manually: `mv .crux .crux-runtime`.
- The spawner refuses to deploy when its target path is occupied by
  anything it did not create (a file, or a non-empty directory without
  the spawner's marker / legacy signature). See ADR-0035 §4.
- API-key bootstrap is delegated to the `crux-env` CLI: the spawner
  no longer writes any dotenv template into the target. Instead AGENTS.md
  tells operators to run `crux-env init` in the target repo.

Usage:
    # From CLI
    python -m crux.spawner /path/to/target/repo

    # Or programmatically
    from crux.spawner import spawn_crux
    spawn_crux("/path/to/target/repo", prime_directive="Build feature X")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Path to the crux repo root — three levels up from this file:
#   .../crux/scripts/crux/spawner/crux_spawner.py
#   parent = spawner/
#   parent.parent = crux/scripts/crux/
#   parent.parent.parent = crux/scripts/
#   parent.parent.parent.parent = crux/   (the inner plugin root)
#   parent.parent.parent.parent.parent = repo root  (containing this `crux/`)
#
# We anchor on the inner plugin root because that's what holds the runtime
# subtree (`scripts/crux/...`) we want to copy.
CRUX_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class SpawnTargetConflictError(RuntimeError):
    """Raised when the spawn target path is occupied by something the
    spawner did not create (e.g. the repo-root `.crux` config file, or a
    foreign directory)."""


@dataclass
class RepoAnalysis:
    """Analysis of a target repository."""

    path: Path
    name: str
    languages: List[str]
    frameworks: List[str]
    file_count: int
    structure: Dict[str, Any]
    key_files: List[str]
    tech_stack: str
    hints: List[str]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "name": self.name,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "file_count": self.file_count,
            "structure": self.structure,
            "key_files": self.key_files,
            "tech_stack": self.tech_stack,
            "hints": self.hints,
            "timestamp": self.timestamp,
        }


class RepoAnalyzer:
    """Analyzes a repository to understand its structure and tech stack."""

    LANG_MAP = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript/React",
        ".jsx": "JavaScript/React",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".rb": "Ruby",
        ".php": "PHP",
        ".swift": "Swift",
        ".kt": "Kotlin",
        ".cs": "C#",
        ".cpp": "C++",
        ".c": "C",
        ".ex": "Elixir",
        ".exs": "Elixir",
    }

    FRAMEWORK_PATTERNS = {
        "package.json": ["React", "Vue", "Angular", "Next.js", "Express", "Nest"],
        "requirements.txt": ["Django", "Flask", "FastAPI", "Celery"],
        "pyproject.toml": ["Django", "Flask", "FastAPI"],
        "Cargo.toml": ["Actix", "Rocket", "Tokio"],
        "go.mod": ["Gin", "Echo", "Fiber"],
        "Gemfile": ["Rails", "Sinatra"],
        "composer.json": ["Laravel", "Symfony"],
        "mix.exs": ["Phoenix", "Ecto"],
    }

    KEY_FILE_PATTERNS = [
        "README.md",
        "AGENTS.md",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "docker-compose.yml",
        "Dockerfile",
        ".env.example",
        "tsconfig.json",
        "webpack.config.js",
        "vite.config.ts",
        "mix.exs",
    ]

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repo_path}")

    def analyze(self) -> RepoAnalysis:
        logger.info("Analyzing repository: %s", self.repo_path)

        languages = self._detect_languages()
        frameworks = self._detect_frameworks()
        structure = self._analyze_structure()
        key_files = self._find_key_files()
        file_count = self._count_files()
        tech_stack = self._summarize_tech_stack(languages, frameworks)
        hints = self._generate_hints(languages, frameworks, structure)

        return RepoAnalysis(
            path=self.repo_path,
            name=self.repo_path.name,
            languages=languages,
            frameworks=frameworks,
            file_count=file_count,
            structure=structure,
            key_files=key_files,
            tech_stack=tech_stack,
            hints=hints,
        )

    def _detect_languages(self) -> List[str]:
        languages: Dict[str, int] = {}
        for ext, lang in self.LANG_MAP.items():
            count = len(list(self.repo_path.rglob(f"*{ext}")))
            if count > 0:
                languages[lang] = count
        return sorted(languages.keys(), key=lambda x: languages[x], reverse=True)

    def _detect_frameworks(self) -> List[str]:
        frameworks: List[str] = []
        for config_file, framework_list in self.FRAMEWORK_PATTERNS.items():
            config_path = self.repo_path / config_file
            if config_path.exists():
                try:
                    content = config_path.read_text()
                    for framework in framework_list:
                        if framework.lower() in content.lower():
                            frameworks.append(framework)
                except Exception:
                    pass
        return list(set(frameworks))

    def _analyze_structure(self) -> Dict[str, Any]:
        structure: Dict[str, Any] = {"directories": [], "patterns": []}
        for item in self.repo_path.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                if item.name not in {
                    "node_modules",
                    "__pycache__",
                    "venv",
                    ".git",
                    "dist",
                    "build",
                }:
                    structure["directories"].append(item.name)

        if (self.repo_path / "src").exists():
            structure["patterns"].append("src/ directory")
        if (self.repo_path / "tests").exists() or (self.repo_path / "test").exists():
            structure["patterns"].append("Has tests")
        if (self.repo_path / "docs").exists():
            structure["patterns"].append("Has documentation")
        if (self.repo_path / ".github").exists():
            structure["patterns"].append("GitHub Actions/CI")
        if (self.repo_path / "docker-compose.yml").exists():
            structure["patterns"].append("Docker Compose")

        return structure

    def _find_key_files(self) -> List[str]:
        key_files: List[str] = []
        for pattern in self.KEY_FILE_PATTERNS:
            if (self.repo_path / pattern).exists():
                key_files.append(pattern)
        return key_files

    def _count_files(self) -> int:
        count = 0
        ignore_dirs = {
            "node_modules",
            "__pycache__",
            "venv",
            ".git",
            "dist",
            "build",
            ".next",
        }
        for ext in self.LANG_MAP.keys():
            for f in self.repo_path.rglob(f"*{ext}"):
                if not any(ignored in f.parts for ignored in ignore_dirs):
                    count += 1
        return count

    def _summarize_tech_stack(
        self,
        languages: List[str],
        frameworks: List[str],
    ) -> str:
        parts: List[str] = []
        if languages:
            parts.append(f"Languages: {', '.join(languages[:3])}")
        if frameworks:
            parts.append(f"Frameworks: {', '.join(frameworks[:3])}")
        return " | ".join(parts) if parts else "Unknown stack"

    def _generate_hints(
        self,
        languages: List[str],
        frameworks: List[str],
        structure: Dict[str, Any],
    ) -> List[str]:
        hints: List[str] = []
        if "TypeScript" in languages or "TypeScript/React" in languages:
            hints.append("Use TypeScript types — check tsconfig.json for compiler options")
        if "Python" in languages:
            hints.append("Check for type hints in existing code — maintain consistency")
        if "Go" in languages:
            hints.append("Follow Go idioms — check existing code for patterns")
        if "Elixir" in languages:
            hints.append("Phoenix/Elixir conventions — run mix format and mix credo")

        if "React" in frameworks or "Next.js" in frameworks:
            hints.append("React codebase — look for component patterns in src/components/")
        if "Django" in frameworks or "Flask" in frameworks:
            hints.append("Python web framework — check for existing patterns in views/routes")
        if "FastAPI" in frameworks:
            hints.append("FastAPI codebase — use Pydantic models for request/response")
        if "Phoenix" in frameworks:
            hints.append("Phoenix codebase — use scopes (phx.gen.auth) and binary IDs")

        if "src/ directory" in structure.get("patterns", []):
            hints.append("Source code in src/ — follow existing organization")
        if "Has tests" in structure.get("patterns", []):
            hints.append("Tests exist — add tests for new functionality")

        return hints


class Spawner:
    """Spawns a customized crux instance into a target repository.

    Layout notes:

    - Source tree:  `crux/scripts/crux/` (crux's runtime root)
    - Target tree:  `.crux-runtime/` inside the target repo (NOT `.crux`,
      which is the repo-root crux config FILE per ADR-0032 — the spawner
      never touches it)
    - No dotenv template is written; key bootstrap is delegated to the
      `crux-env` CLI, which manages `~/.crux/env`.
    """

    # Directory inside this crux checkout that holds the runtime we copy.
    SOURCE_RUNTIME_DIR = "scripts/crux"

    # Marker file written into the spawned tree so re-runs (upgrades) can
    # prove the target directory was created by this spawner.
    SPAWNER_MARKER = ".spawned-by-crux.json"

    def __init__(
        self,
        target_repo: Path,
        crux_dir: str = ".crux-runtime",
        analyze_repo: bool = True,
    ) -> None:
        """Initialize the spawner.

        Args:
            target_repo: Path to the target repository.
            crux_dir: Directory name for the spawned tree inside the
                target repo. Defaults to `.crux-runtime`.
            analyze_repo: Whether to analyze the repo for customization.
        """
        self.target_repo = Path(target_repo).resolve()
        self.crux_dir = crux_dir
        self.analyze_repo = analyze_repo
        self.target_path = self.target_repo / crux_dir
        # Containment: the spawner's write boundary is the target repo. An
        # absolute or `..`-traversing crux_dir would silently relocate that
        # boundary; refuse instead of guessing.
        candidate = self.target_path
        if not candidate.is_absolute():  # pragma: no cover — `/` keeps it absolute
            candidate = self.target_repo / candidate
        resolved_parent = candidate.parent.resolve() / candidate.name
        if Path(crux_dir).is_absolute() or not str(resolved_parent).startswith(
            str(self.target_repo) + os.sep
        ):
            raise SpawnTargetConflictError(
                f"crux_dir {crux_dir!r} escapes the target repo "
                f"{self.target_repo} — the spawner only writes inside the "
                f"target repository. Pass a repo-relative directory name."
            )
        self.analysis: Optional[RepoAnalysis] = None

    def spawn(
        self,
        prime_directive: Optional[str] = None,
    ) -> Path:
        """Spawn crux into the target repository.

        Args:
            prime_directive: Initial task description.

        Returns:
            Path to the spawned `.crux-runtime/` directory.

        Raises:
            SpawnTargetConflictError: if the target path is occupied by
                anything this spawner did not create.
        """
        logger.info("=" * 60)
        logger.info("Spawning crux into target repo")
        logger.info("=" * 60)
        logger.info("   Target: %s", self.target_repo)
        logger.info("   crux dir: %s", self.crux_dir)

        self._refuse_foreign_target()

        if self.analyze_repo:
            analyzer = RepoAnalyzer(self.target_repo)
            self.analysis = analyzer.analyze()
            logger.info("   Tech Stack: %s", self.analysis.tech_stack)
            logger.info("   Files: %d", self.analysis.file_count)

        self._create_directories()
        self._copy_runtime()
        self._generate_agents_md(prime_directive)
        if self.analysis:
            self._generate_repo_knowledge()
        if prime_directive:
            self._generate_prime_directive(prime_directive)
        self._create_gitignore()

        logger.info("Spawn complete: %s", self.target_path)
        logger.info("Next steps:")
        logger.info(
            '   1. Run: python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-env.py" init '
            "to set up API keys (stored in ~/.crux/, never in the repo)"
        )
        logger.info("   2. Edit %s/box/prime_directive.md", self.crux_dir)
        logger.info(
            "   3. Open the runbook: uv run %s/scripts/crux/runbook/__main__.py "
            '"<your task>" --ai-plan (self-contained via its PEP 723 header; bare python lacks the deps)',
            self.target_path,
        )
        return self.target_path

    def _refuse_foreign_target(self) -> None:
        """Refuse to deploy over anything this spawner did not create.

        Per ADR-0035 §4: the target path may be (a) absent, (b) an empty
        directory, or (c) a directory previously created by this spawner —
        recognized by the SPAWNER_MARKER file, or (for pre-marker spawned
        trees) by the legacy signature of a spawner-written `AGENTS.md`
        alongside a `scripts/crux/` runtime copy. Anything else — a FILE
        at the target path (e.g. the repo-root `.crux` config file), or a
        non-empty foreign directory — is a conflict.
        """
        path = self.target_path
        if path.is_symlink():
            raise SpawnTargetConflictError(
                f"Spawn target {path} is a SYMLINK — refusing to deploy "
                f"through it (writes would land outside the declared target "
                f"directory). Replace the link with a real directory or pass "
                f"a different --dir / crux_dir=."
            )
        if not path.exists():
            return
        if path.is_file():
            raise SpawnTargetConflictError(
                f"Spawn target {path} exists and is a FILE — refusing to deploy. "
                f"If this is the repo-root `.crux` config file: the spawner's "
                f"runtime directory is `.crux-runtime/` by default and never "
                f"touches the `.crux` config file — pass a different --dir "
                f"instead of pointing the spawner at it."
            )
        if not any(path.iterdir()):
            return  # Empty directory — nothing to clobber.
        if (path / self.SPAWNER_MARKER).exists():
            return  # Our own earlier spawn — re-running upgrades in place.
        if (path / "AGENTS.md").exists() and (path / "scripts" / "crux").is_dir():
            return  # Legacy (pre-marker) spawned tree — also ours.
        raise SpawnTargetConflictError(
            f"Spawn target {path} exists but was not created by the crux "
            f"spawner — refusing to deploy over it. If you need its "
            f"contents, move it aside (e.g. `mv {path} {path}.bak`) and "
            f"re-run; otherwise choose a different directory "
            f"(--dir / crux_dir=). Do not delete it blindly. (A genuine "
            f"pre-rename spawned tree — AGENTS.md + scripts/crux/ — is "
            f"recognized automatically and never hits this error.)"
        )

    def _create_directories(self) -> None:
        dirs = [
            self.target_path,
            self.target_path / "scripts" / "crux",
            self.target_path / "box" / "templates",
            self.target_path / "box" / "knowledge",
            self.target_path / "logs",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        self._write_marker()

    def _write_marker(self) -> None:
        """Write the spawned-by-crux marker file into the target tree."""
        marker_path = self.target_path / self.SPAWNER_MARKER
        marker = {
            "created_by": "crux-install-runtime",
            "spawned_at": datetime.now(timezone.utc).isoformat(),
        }
        marker_path.write_text(json.dumps(marker, indent=2) + "\n")

    def _copy_runtime(self) -> None:
        """Copy the crux runtime into `.crux-runtime/scripts/crux/`, plus the
        sibling `scripts/crux_env.py` — the substrate imports `crux_env` at
        module load, so a tree without it cannot `import crux.*` at all."""
        runtime_src = CRUX_PLUGIN_ROOT / self.SOURCE_RUNTIME_DIR
        runtime_dst = self.target_path / self.SOURCE_RUNTIME_DIR
        if runtime_src.exists():
            shutil.copytree(
                runtime_src,
                runtime_dst,
                dirs_exist_ok=True,
                ignore=self._ignore_runtime_tree,
            )
        env_src = CRUX_PLUGIN_ROOT / "scripts" / "crux_env.py"
        if env_src.exists():
            shutil.copy2(env_src, self.target_path / "scripts" / "crux_env.py")

    @staticmethod
    def _ignore_python_cache_tree(_dir: str, names: List[str]) -> set:
        ignored: set = set()
        for name in names:
            if name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}:
                ignored.add(name)
            if name.endswith(".pyc"):
                ignored.add(name)
        return ignored

    def _ignore_runtime_tree(self, dirpath: str, names: List[str]) -> set:
        """Skip caches, node_modules, and other non-portable directories."""
        ignored = self._ignore_python_cache_tree(dirpath, names)
        for name in names:
            if name in {"node_modules", "artifacts", ".DS_Store"}:
                ignored.add(name)
            if name in {"testing.db"}:
                ignored.add(name)
        return ignored

    def _generate_agents_md(self, prime_directive: Optional[str]) -> None:
        """Write a customized AGENTS.md for the target repo."""
        agents_content = (
            f"# AGENTS.md — crux for {self.target_repo.name}\n\n"
            f"This crux instance was spawned specifically for this repository.\n\n"
            "## Repository Context\n\n"
            f"**Repo Name:** {self.target_repo.name}\n"
            f"**Spawned:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        if self.analysis:
            agents_content += (
                f"\n**Tech Stack:** {self.analysis.tech_stack}\n"
                f"**Source Files:** {self.analysis.file_count}\n"
                f"**Key Files:** {', '.join(self.analysis.key_files[:5])}\n\n"
                "## Repo-Specific Hints\n\n"
                "These hints were auto-generated by analyzing this repository:\n\n"
            )
            for hint in self.analysis.hints:
                agents_content += f"- {hint}\n"

            agents_content += (
                f"\n## Directory Structure\n\n"
                f"Top-level directories: `{', '.join(self.analysis.structure.get('directories', []))}`\n\n"
                "Detected patterns:\n"
            )
            for pattern in self.analysis.structure.get("patterns", []):
                agents_content += f"- {pattern}\n"

        if prime_directive:
            agents_content += f"\n## Prime Directive\n\n> {prime_directive}\n"

        agents_content += (
            "\n## Golden Rules\n\n"
            "### 1. The 3-Hypotheses Rule\n"
            "Every hypothesis loop MUST produce exactly 3 testable hypotheses.\n\n"
            "### 2. Confidence Thresholds\n"
            "- >= 85%: ship it\n"
            "- >= 70%: proceed with caveats noted\n"
            "- >= 50%: get an independent check before proceeding\n"
            "- <  50%: STOP and escalate to a human\n\n"
            "### 3. Respect This Codebase\n"
            "- Follow existing patterns and conventions\n"
            "- Check `box/knowledge/repo_analysis.json` for repo context\n"
            "- Read existing code before making changes\n"
            "- Add tests if tests exist\n\n"
            "## API Keys\n\n"
            "crux reads API keys via `crux_env.require(...)` from\n"
            "`~/.crux/env`. Run the `crux-env` CLI once to bootstrap your\n"
            'keys: `python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-env.py" init`\n'
            "(it ships with the installed crux plugin).\n\n"
            "## Usage\n\n"
            "```python\n"
            "from crux.council import council_vote\n\n"
            'result = council_vote("Your question here", context="Relevant context")\n'
            "print(result.final_decision)\n"
            "```\n\n"
            "## Files\n\n"
            "- `box/prime_directive.md` — current task\n"
            "- `box/knowledge/` — repo-specific knowledge\n"
            "- `logs/agent_runs/` — per-run artifacts and traces\n"
        )

        agents_path = self.target_path / "AGENTS.md"
        agents_path.write_text(agents_content)

        # Also copy to repo root if not present.
        root_agents = self.target_repo / "AGENTS.md"
        if not root_agents.exists():
            root_agents.write_text(agents_content)

    def _generate_repo_knowledge(self) -> None:
        if not self.analysis:
            return
        knowledge_path = self.target_path / "box" / "knowledge" / "repo_analysis.json"
        with open(knowledge_path, "w") as f:
            json.dump(self.analysis.to_dict(), f, indent=2)

        summary_path = self.target_path / "box" / "knowledge" / "repo_summary.md"
        summary = (
            f"# Repository Analysis: {self.analysis.name}\n\n"
            "## Overview\n"
            f"- **Path:** {self.analysis.path}\n"
            f"- **Files:** {self.analysis.file_count} source files\n"
            f"- **Languages:** {', '.join(self.analysis.languages)}\n"
            f"- **Frameworks:** {', '.join(self.analysis.frameworks) if self.analysis.frameworks else 'None detected'}\n\n"
            "## Structure\nTop-level directories:\n"
            + "\n".join(f"- `{d}/`" for d in self.analysis.structure.get("directories", []))
            + "\n\n## Key Files\n"
            + "\n".join(f"- `{f}`" for f in self.analysis.key_files)
            + "\n\n## Hints\n"
            + "\n".join(f"- {h}" for h in self.analysis.hints)
            + f"\n\n## Analyzed\n{self.analysis.timestamp}\n"
        )
        summary_path.write_text(summary)

    def _generate_prime_directive(self, prime_directive: str) -> None:
        directive_path = self.target_path / "box" / "prime_directive.md"
        content = (
            "# Prime Directive\n\n"
            f"> {prime_directive}\n\n"
            "## Context\n\n"
            f"This crux was spawned on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.\n\n"
            "## Constraints\n\n"
            "- Follow existing code patterns in this repository\n"
            "- Check `knowledge/repo_analysis.json` for tech stack info\n"
            "- Generate exactly 3 testable hypotheses per hypothesis loop pass\n\n"
            "## Success Criteria\n\n"
            '_Define what "done" looks like here._\n'
        )
        directive_path.write_text(content)

    def _create_gitignore(self) -> None:
        gitignore_content = (
            "# crux local state\n"
            "logs/\n"
            "\n# Python caches\n"
            "__pycache__/\n"
            "*.pyc\n"
        )
        gitignore_path = self.target_path / ".gitignore"
        gitignore_path.write_text(gitignore_content)


def spawn_crux(
    target_repo: str,
    prime_directive: Optional[str] = None,
    crux_dir: str = ".crux-runtime",
    analyze: bool = True,
) -> Path:
    """Convenience function to spawn crux into a repository.

    Args:
        target_repo: Path to target repository.
        prime_directive: Initial task (optional).
        crux_dir: Directory name for the spawned tree
            (default `.crux-runtime`).
        analyze: Whether to analyze the repo before spawn.

    Returns:
        Path to the spawned `.crux-runtime/` directory.
    """
    spawner = Spawner(
        target_repo=Path(target_repo),
        crux_dir=crux_dir,
        analyze_repo=analyze,
    )
    return spawner.spawn(prime_directive=prime_directive)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Spawn crux into a target repository",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s /path/to/my/repo\n"
            '  %(prog)s /path/to/repo --task "Build auth system"\n'
            "  %(prog)s /path/to/repo --dir .crux-agents --no-analyze\n"
        ),
    )
    parser.add_argument("target_repo", help="Path to target repository")
    parser.add_argument(
        "--task",
        "-t",
        help="Prime directive (initial task)",
        default=None,
    )
    parser.add_argument(
        "--dir",
        "-d",
        help="Spawned-tree directory name (default: .crux-runtime)",
        default=".crux-runtime",
    )
    parser.add_argument(
        "--no-analyze",
        action="store_true",
        help="Skip repository analysis",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    try:
        spawn_crux(
            target_repo=args.target_repo,
            prime_directive=args.task,
            crux_dir=args.dir,
            analyze=not args.no_analyze,
        )
    except Exception as e:
        logger.error("Failed to spawn crux: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
