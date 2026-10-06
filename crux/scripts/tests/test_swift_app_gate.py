"""The Xcode membership gate over the pinned app (ADR-0130 clauses 6 and 9).

This is the run's STOP RULE gate made permanent: a real derive of the pinned
NetNewsWire checkout (MIT) must establish membership statically. It asserts:

- every `source-membership`, Xcode `target` and `target-dependency` fact in
  `expectations/netnewswire.yml` holds against the rendered spine, and every
  `entry-declaration` fact holds with its owning target;
- the `Shared` root postcondition: `Shared/ShareExtension/ExtensionContainers.swift`
  is a folder-synced member of both app targets, declared at the two
  `fileSystemSynchronizedGroups` list items, and no `unresolved-reference`
  line names the root;
- every exception-set entry is applied: each resolves to a file in the
  checkout, and each `.swift` entry renders the excluded or added row its
  owning relation predicts;
- every `.swift` file under an attached synced root is accounted for as a
  member, an exclusion, or a file inside a bundle directory or explicit
  folder.

The test reads the checkout and never executes anything from it. It skips,
naming the fetch command, when the corpus cache is absent.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
CORPUS = HERE / "arch-corpus"
CHECKOUT = CORPUS / ".cache" / "netnewswire"
PBXPROJ_REL = "NetNewsWire.xcodeproj/project.pbxproj"
FETCH_HINT = "run `uv run crux/scripts/tests/arch-corpus/fetch.py` to fetch the pinned corpus"

for p in (str(SCRIPTS), str(CORPUS)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _cells(line: str) -> list[str]:
    return [c.strip().strip("`") for c in line.strip().strip("|").split(" | ")]


def _membership_rows(module_graph: str) -> list[dict]:
    rows, inside = [], False
    for line in module_graph.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Membership"
            continue
        if inside and line.startswith("| `"):
            f, t, route, cond, declared = _cells(line)
            rows.append({"file": f, "target": t, "route": route, "declared": declared})
    return rows


@unittest.skipUnless((CHECKOUT / PBXPROJ_REL).is_file(), f"netnewswire not fetched — {FETCH_HINT}")
class NetNewsWireMembershipGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import yaml

        derive_corpus = _load("derive_corpus", CORPUS / "derive_corpus.py")
        facts_matcher = _load("facts_matcher", CORPUS / "facts_matcher.py")
        tree = derive_corpus.derive_one("netnewswire", CHECKOUT)["tree"]
        cls.module_graph = tree["module-graph.md"]
        golden = {k: tree[f"{k}.md"] for k in ("data-model", "api-surface", "module-graph")}
        cls.facts = yaml.safe_load((CORPUS / "expectations" / "netnewswire.yml").read_text())["facts"]
        cls.problems = facts_matcher.match({"facts": cls.facts}, golden)
        cls.rows = _membership_rows(cls.module_graph)

        from crux.arch.packs import swift_pbxproj

        cls.doc = swift_pbxproj.read_pbxproj((CHECKOUT / PBXPROJ_REL).read_bytes())

    def _facts(self, kind: str, pbxproj_only: bool = False) -> list[dict]:
        out = [f for f in self.facts if f["kind"] == kind]
        if pbxproj_only:
            out = [f for f in out if PBXPROJ_REL in str(f.get("evidence", ""))]
        return out

    def test_membership_target_dependency_and_entry_facts_hold(self):
        counts = {k: len(self._facts(k, pbxproj_only=(k == "target")))
                  for k in ("source-membership", "target", "target-dependency", "entry-declaration")}
        self.assertEqual(counts["source-membership"], 54)
        self.assertEqual(counts["target"], 8)
        self.assertEqual(counts["target-dependency"], 6)
        gated = {"source-membership", "target", "target-dependency", "entry-declaration"}
        failing = [p for p in self.problems if p["kind"] in gated]
        self.assertEqual(failing, [])

    def test_shared_root_postcondition(self):
        file = "Shared/ShareExtension/ExtensionContainers.swift"
        synced = {(r["target"], r["declared"]) for r in self.rows
                  if r["file"] == file and r["route"] == "folder-synced root Shared"}
        self.assertEqual(synced, {
            ("NetNewsWire", f"{PBXPROJ_REL}:806-806"),
            ("NetNewsWire-iOS", f"{PBXPROJ_REL}:761-761"),
        })
        main_group = self.doc.objects[self.doc.root_id]["mainGroup"]
        shared = [oid for oid, o in self.doc.objects.items()
                  if o.get("isa") == "PBXFileSystemSynchronizedRootGroup" and o.get("path") == "Shared"]
        self.assertEqual(len(shared), 1)
        children = self.doc.objects[main_group]["children"]
        self.assertIn(shared[0], children)
        child_lines = self.doc.list_item_lines[(main_group, "children")]
        self.assertEqual(child_lines[children.index(shared[0])], 606)
        # A line names the root when its path cell or its detail mentions
        # `Shared`, or when it is declared at the root's main-group line.
        naming_root = [line for line in self.module_graph.splitlines()
                       if line.startswith(("- `unresolved-reference`", "- `path-escape`"))
                       and ("Shared" in line or f"`{PBXPROJ_REL}` lines 606-" in line)]
        self.assertEqual(naming_root, [])

    def _synced_roots(self):
        objs = self.doc.objects
        targets = {oid: o for oid, o in objs.items() if o.get("isa") == "PBXNativeTarget"}
        owners: dict[str, set] = {}
        for tid, t in targets.items():
            for rid in t.get("fileSystemSynchronizedGroups") or []:
                owners.setdefault(rid, set()).add(tid)
        return objs, targets, owners

    def test_every_exception_entry_is_applied(self):
        objs, targets, owners = self._synced_roots()
        row_set = {(r["file"], r["target"], r["route"]) for r in self.rows}
        sets = entries = 0
        unresolved, missing_rows = [], []
        for rid, root in sorted(objs.items()):
            if root.get("isa") != "PBXFileSystemSynchronizedRootGroup":
                continue
            base = root.get("path") or root.get("name")
            for eid in root.get("exceptions") or []:
                exc = objs[eid]
                if exc.get("isa") != "PBXFileSystemSynchronizedBuildFileExceptionSet":
                    continue
                sets += 1
                tid = exc["target"]
                route = ("excluded by an exception set" if tid in owners.get(rid, set())
                         else "added by an exception set")
                for entry in exc.get("membershipExceptions") or []:
                    entries += 1
                    if entry.startswith("/Localized/"):
                        d, name = entry[len("/Localized/"):].rsplit("/", 1)
                        parent = CHECKOUT / base / d
                        hits = sorted(parent.glob(f"*.lproj/{name}")) if parent.is_dir() else []
                        resolved = [h.relative_to(CHECKOUT).as_posix() for h in hits]
                    else:
                        p = CHECKOUT / base / entry
                        resolved = [f"{base}/{entry}"] if p.exists() else []
                    if not resolved:
                        unresolved.append(entry)
                    for rel in resolved:
                        if rel.endswith(".swift") and (rel, targets[tid]["name"], route) not in row_set:
                            missing_rows.append((rel, targets[tid]["name"], route))
        self.assertEqual((sets, entries), (13, 67))
        self.assertEqual(unresolved, [])
        self.assertEqual(missing_rows, [])

    def test_every_swift_file_under_an_attached_root_is_accounted_for(self):
        from crux.arch.packs import swift_xcode

        objs, _targets, owners = self._synced_roots()
        members = {r["file"] for r in self.rows}
        total, unaccounted = 0, []
        for rid in sorted(owners):
            root = objs[rid]
            base = root.get("path") or root.get("name")
            explicit = {f"{base}/{e}" for e in root.get("explicitFolders") or []}
            for dirpath, dirnames, filenames in os.walk(CHECKOUT / base):
                dirnames.sort()
                rel_dir = Path(dirpath).relative_to(CHECKOUT).as_posix()
                in_bundle = any(seg.endswith(tuple(swift_xcode.BUNDLE_SUFFIXES))
                                for seg in rel_dir.split("/"))
                in_explicit = any(rel_dir == e or rel_dir.startswith(e + "/") for e in explicit)
                for name in sorted(filenames):
                    if not name.endswith(".swift"):
                        continue
                    total += 1
                    rel = f"{rel_dir}/{name}"
                    if rel in members or in_bundle or in_explicit:
                        continue
                    unaccounted.append(rel)
        self.assertEqual(unaccounted, [])
        self.assertEqual(total, 299)


if __name__ == "__main__":
    unittest.main()
