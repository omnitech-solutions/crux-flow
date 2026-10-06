#!/usr/bin/env python3
"""Inspect the static health of an installed Crux Codex-agent roster."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import codex_agents  # noqa: E402
import models_catalog  # noqa: E402


RUNTIME_UNVERIFIED = {
    "status": "unverified",
    "client_version": None,
    "reason": "fresh-session host evidence has not been recorded",
}


def _plugin(plugin_root: Path) -> dict[str, str]:
    root = plugin_root.resolve()
    manifest = root / ".codex-plugin" / "plugin.json"
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise codex_agents.SpecViolation(f"cannot read plugin manifest {manifest}: {exc}") from exc
    if not isinstance(raw, dict):
        raise codex_agents.SpecViolation(f"plugin manifest root must be an object: {manifest}")
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        raise codex_agents.SpecViolation(f"plugin manifest has no version: {manifest}")
    return {"root": str(root), "version": version}


def _is_contained(candidate: Path, root: Path) -> bool:
    """Resolve a candidate and test it against the resolved project root."""
    try:
        resolved = candidate.resolve()
        return resolved == root or resolved.is_relative_to(root)
    except (OSError, ValueError):
        return False


def _shadow_report(project_context: Path | None, source_names: list[str]) -> dict[str, Any]:
    if project_context is None:
        return {"status": "unverified", "project_context": None, "roles": [], "malformed": [], "unsafe": []}
    context = project_context.resolve()
    if not context.is_dir():
        raise codex_agents.SpecViolation(f"project context is not a directory: {context}")
    result: dict[str, Any] = {
        "status": "clean", "project_context": str(context), "roles": [], "malformed": [], "unsafe": []
    }
    codex_dir = context / ".codex"
    if (codex_dir.exists() or codex_dir.is_symlink()) and not _is_contained(codex_dir, context):
        result["unsafe"].append({"file": ".codex", "reason": "resolves outside project context"})
        result["status"] = "findings"
        return result
    if codex_dir.exists() and not codex_dir.is_dir():
        result["unsafe"].append({"file": ".codex", "reason": "is not a directory"})
        result["status"] = "findings"
        return result
    agents_dir = codex_dir / "agents"
    if not agents_dir.exists() and not agents_dir.is_symlink():
        return result
    if not _is_contained(agents_dir, context):
        result["unsafe"].append({"file": ".codex/agents", "reason": "resolves outside project context"})
        result["status"] = "findings"
        return result
    if not agents_dir.is_dir():
        result["unsafe"].append({"file": ".codex/agents", "reason": "is not a directory"})
        result["status"] = "findings"
        return result
    expected = {codex_agents.codex_agent_name(role): role for role in source_names}
    for path in sorted(agents_dir.glob("*.toml")):
        if path.is_symlink():
            result["unsafe"].append({"file": path.name, "reason": "agent file is a symlink"})
            continue
        status, text, reason = codex_agents.read_managed_file(agents_dir, path.name)
        if status == "unsafe":
            result["unsafe"].append({"file": path.name, "reason": reason})
            continue
        if status != "regular":
            result["malformed"].append({"file": path.name, "reason": reason or status})
            continue
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            result["malformed"].append({"file": path.name, "reason": str(exc)})
            continue
        name = parsed.get("name")
        if not isinstance(name, str):
            result["malformed"].append({"file": path.name, "reason": "missing string name"})
            continue
        role = expected.get(name)
        if role:
            result["roles"].append({"role": role, "file": path.name, "name": name})
    if result["roles"] or result["malformed"] or result["unsafe"]:
        result["status"] = "findings"
    return result


def _binding_state(parsed: dict[str, Any], source: Any, resources: list[Path]) -> tuple[str, list[dict[str, Any]]]:
    """Compare installed bindings with the canonical resources without inference."""
    skills = parsed.get("skills")
    config = skills.get("config") if isinstance(skills, dict) else None
    if not source.skills and config is None:
        return "installed", []
    if not isinstance(config, list) or not all(isinstance(item, dict) for item in config):
        return "malformed", [
            {"skill": skill, "expected_path": str(resource), "installed_path": None, "status": "missing"}
            for skill, resource in zip(source.skills, resources, strict=True)
        ]
    bindings: dict[str, list[dict[str, Any]]] = {}
    for item in config:
        path = item.get("path")
        if isinstance(path, str):
            bindings.setdefault(path, []).append(item)
    report = []
    for skill, resource in zip(source.skills, resources, strict=True):
        expected_path = str(resource)
        configured = bindings.get(expected_path, [])
        if not configured:
            relocated = next((path for path in bindings if path.endswith(f"/skills/{skill}/SKILL.md")), None)
            report.append({"skill": skill, "expected_path": expected_path, "installed_path": relocated, "status": "missing"})
            continue
        enabled = configured[0].get("enabled")
        report.append({
            "skill": skill,
            "expected_path": expected_path,
            "installed_path": expected_path,
            "status": "enabled" if enabled is True else "disabled",
        })
    return "installed", report


def _installed_role(
    target: Path | codex_agents.PinnedDirectory, filename: str, source: Any, resources: list[Path]
) -> dict[str, Any]:
    """Read a managed TOML leaf only after refusing symlinks and malformed data."""
    base = {"role": source.name, "file": filename}
    status, text, reason = codex_agents.read_managed_file(target, filename)
    if status == "unsafe":
        return {
            **base, "status": "unsafe", "model": None, "model_reasoning_effort": None,
            "skill_bindings": [], "reason": reason,
        }
    if status == "malformed":
        return {
            **base, "status": "malformed", "model": None, "model_reasoning_effort": None,
            "skill_bindings": [], "reason": reason,
        }
    if status == "missing":
        return {
            **base, "status": "missing", "model": None, "model_reasoning_effort": None,
            "skill_bindings": [
                {"skill": skill, "expected_path": str(resource), "installed_path": None, "status": "missing"}
                for skill, resource in zip(source.skills, resources, strict=True)
            ],
        }
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return {
            **base, "status": "malformed", "model": None, "model_reasoning_effort": None,
            "skill_bindings": [], "reason": str(exc),
        }
    binding_status, bindings = _binding_state(parsed, source, resources)
    return {
        **base,
        "status": binding_status,
        "model": parsed.get("model") if isinstance(parsed.get("model"), str) else None,
        "model_reasoning_effort": (
            parsed.get("model_reasoning_effort")
            if isinstance(parsed.get("model_reasoning_effort"), str) else None
        ),
        "skill_bindings": bindings,
    }


def inspect_install(
    *, target: Path | codex_agents.PinnedDirectory, plugin_root: Path, scope: str, project_context: Path | None = None
) -> dict[str, Any]:
    """Return stable static state. Runtime validation intentionally stays unverified."""
    plugin = _plugin(plugin_root)
    sources = [codex_agents.parse_source(path) for path in sorted((plugin_root / "agents").glob("*.md"))]
    catalog = models_catalog.load(plugin_root / "catalog" / "models.yml", plugin_root / "agents")
    generated = codex_agents.generate(plugin_root / "agents", catalog, skill_root=plugin_root / "skills")
    added, changed, removed, _problems = codex_agents.safe_diff(target, generated)
    roles = []
    installed_roles = []
    for source in sources:
        runtime = catalog.resolve(source.name).codex
        resources = codex_agents._skill_config(source, plugin_root / "skills")
        roles.append({
            "role": source.name,
            "name": codex_agents.codex_agent_name(source.name),
            "model": runtime.model,
            "model_reasoning_effort": runtime.reasoning_effort,
            "declared_skills": list(source.skills),
            "resolved_skills": [str(resource) for resource in resources],
        })
        installed_roles.append(
            _installed_role(target, codex_agents.codex_agent_filename(source.name), source, resources)
        )
    drift = {"status": "clean" if not (added or changed or removed) else "drift", "added": added, "changed": changed, "removed": removed}
    installed_roles.sort(key=lambda item: item["role"])
    installed_status = "clean"
    if any(role["status"] != "installed" or any(
        binding["status"] != "enabled" for binding in role["skill_bindings"]
    ) for role in installed_roles):
        installed_status = "findings"
    return {
        "scope": scope,
        "target": str(target.path if isinstance(target, codex_agents.PinnedDirectory) else target),
        "plugin": plugin,
        "roles": sorted(roles, key=lambda item: item["role"]),
        "installed": {"status": installed_status, "roles": installed_roles},
        "drift": drift,
        "shadows": _shadow_report(project_context, [source.name for source in sources]),
        "runtime": dict(RUNTIME_UNVERIFIED),
    }


def exit_status(report: dict[str, Any]) -> int:
    return 1 if (
        report["drift"]["status"] == "drift"
        or report["shadows"]["status"] == "findings"
        or report["installed"]["status"] == "findings"
    ) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--plugin-root", required=True, type=Path)
    parser.add_argument("--scope", choices=("personal", "project"), required=True)
    parser.add_argument("--project-context", type=Path)
    args = parser.parse_args(argv)
    try:
        report = inspect_install(target=args.target, plugin_root=args.plugin_root, scope=args.scope, project_context=args.project_context)
    except codex_agents.SpecViolation as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return exit_status(report)


if __name__ == "__main__":
    raise SystemExit(main())
