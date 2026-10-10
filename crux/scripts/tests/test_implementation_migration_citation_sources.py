"""PB-0142 item 1: the migration citation scan reads every managed instruction surface.

Tracked instruction files at any depth and tracked files under the four project-local skill
roots are citation sources, refused exactly as root AGENTS.md is. Historical brief mentions stay
accepted. Fixture trees live in temp dirs; the live repository is never read or written.
"""
import os
import sys
import unittest
from pathlib import Path

import _council_gate_support as sup
import implementation_migration as migration
import test_implementation_migration_shared_reads as shared

SCRIPTS = str(Path(__file__).resolve().parent.parent)

GOVERNING = "# Skill\n\nKeep this implementation because rule:rotation requires it.\n"
CHANGELOG = "# Changelog\n\n## [3.10.0] — 2026-08-01\n\nrule:rotation\n"
SURFACES = ["AGENTS.md", "CLAUDE.md", "app/AGENTS.md", "app/CLAUDE.md",
            ".claude/skills/demo/SKILL.md", ".agents/skills/demo/SKILL.md",
            ".opencode/skills/demo/SKILL.md", ".opencode/skill/demo/SKILL.md"]


class _Base(unittest.TestCase):
    setUp = shared.CitationKinds.setUp
    prepare = shared.CitationKinds.prepare


class Item1Sources(_Base):
    def put(self, rel, text=GOVERNING, track=True):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if track:
            sup.git(self.root, "add", "-f", rel)
        return path

    def governing(self, batch):
        _, kinds = migration._citation_inspection(self.root, batch)
        return [row["path"] for row in kinds["governing"]]

    def test_tracked_surfaces_are_refused(self):
        batch = self.prepare(CHANGELOG)
        results = {}
        for rel in SURFACES:
            path = self.put(rel)
            results[rel] = "REFUSED" if rel in self.governing(batch) else "PASSES"
            sup.git(self.root, "rm", "-q", "--cached", rel)
            path.unlink()
        for rel, verdict in results.items():
            print(f"  tracked {verdict:8} {rel}", file=sys.stderr)
        self.assertEqual({rel: "REFUSED" for rel in SURFACES}, results)

    def test_redirecting_git_environment_does_not_change_membership(self):
        # A hook or wrapper may export GIT_INDEX_FILE; membership must still be this repo's index.
        from unittest import mock
        batch = self.prepare(CHANGELOG)
        self.put("app/AGENTS.md")
        self.assertIn("app/AGENTS.md", self.governing(batch))    # positive control
        empty = self.root.parent / "redirected-index"
        with mock.patch.dict(os.environ, {"GIT_INDEX_FILE": str(empty)}):
            self.assertIn("app/AGENTS.md", self.governing(batch))

    def test_untracked_and_ignored_skill_files_do_not_change_the_verdict(self):
        batch = self.prepare(CHANGELOG)
        self.put(".claude/skills/local/SKILL.md", track=False)
        (self.root / ".gitignore").write_text(".agents/skills/ignored/\n")
        self.put(".agents/skills/ignored/SKILL.md", track=False)
        ignored = sup.git(self.root, "check-ignore", ".agents/skills/ignored/SKILL.md").strip()
        self.assertEqual(ignored, ".agents/skills/ignored/SKILL.md")
        self.assertEqual([], [p for p in self.governing(batch) if "skills" in p])
        # Positive control: the same file, once tracked, is refused.
        sup.git(self.root, "add", "-f", ".claude/skills/local/SKILL.md")
        self.assertIn(".claude/skills/local/SKILL.md", self.governing(batch))

    def symlink_refused(self, rel):
        batch = self.prepare(CHANGELOG)
        outside = self.root.parent / "outside.md"
        outside.write_text(GOVERNING)
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            path.unlink()
        os.symlink(outside, path)
        sup.git(self.root, "add", "-f", rel)
        mode = sup.git(self.root, "ls-files", "-s", rel).split()[0]
        self.assertEqual(mode, "120000")
        with self.assertRaises(migration.Refused) as caught:
            migration._citation_inspection(self.root, batch)
        print(f"  tracked symlink {rel}: Refused {caught.exception.code}", file=sys.stderr)
        self.assertEqual(caught.exception.code, "migration-symlink-refused")

    def test_tracked_symlinked_root_agents_md_is_refused(self):
        self.symlink_refused("AGENTS.md")

    def test_tracked_symlinked_nested_agents_md_is_refused(self):
        self.symlink_refused("app/AGENTS.md")

    def test_tracked_symlinked_skill_file_is_refused(self):
        self.symlink_refused(".claude/skills/demo/SKILL.md")

    def test_skill_root_swapped_for_a_symlink_is_refused(self):
        batch = self.prepare(CHANGELOG)
        self.put(".claude/skills/demo/SKILL.md", text="# Skill\n")
        sup.git(self.root, "commit", "-q", "-m", "skill")
        outside = self.root.parent / "outside-skills"
        (outside / "demo").mkdir(parents=True)
        (outside / "demo/SKILL.md").write_text(GOVERNING)
        for child in sorted((self.root / ".claude/skills").rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        (self.root / ".claude/skills").rmdir()
        os.symlink(outside, self.root / ".claude/skills")
        with self.assertRaises(migration.Refused) as caught:
            migration._citation_inspection(self.root, batch)
        print(f"  ancestor symlink .claude/skills: Refused {caught.exception.code}", file=sys.stderr)
        # An ancestor link is refused by the bounded reader itself, which reports a scope refusal.
        self.assertEqual(caught.exception.code, "migration-citation-scope-refused")

    # ---- M1: a tracked link (mode 120000) at any depth under a skill root, whatever its suffix --
    def link_refused(self, rel, target):
        """Track `rel` as a link to `target`; the scan must refuse it with the symlink remedy."""
        batch = self.prepare(CHANGELOG)
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)
        sup.git(self.root, "add", "-f", rel)
        self.assertEqual(sup.git(self.root, "ls-files", "-s", rel).split()[0], "120000")
        with self.assertRaises(migration.Refused) as caught:
            migration._citation_inspection(self.root, batch)
        print(f"  tracked link {rel} -> {target}: Refused {caught.exception.code}", file=sys.stderr)
        self.assertEqual(caught.exception.code, "migration-symlink-refused")
        self.assertEqual(getattr(caught.exception, "remedy", None), migration.SYMLINK_SOURCE_REMEDY)

    def test_skill_directory_link_with_in_repo_target_is_refused(self):
        # Positive control: the same skill as a real tracked directory governs.
        batch = self.prepare(CHANGELOG)
        self.put(".claude/skills/demo/SKILL.md")
        self.assertIn(".claude/skills/demo/SKILL.md", self.governing(batch))
        sup.git(self.root, "rm", "-q", "-r", "--cached", ".claude/skills/demo")
        (self.root / ".claude/skills/demo/SKILL.md").unlink(); (self.root / ".claude/skills/demo").rmdir()
        self.put("shared/demo/SKILL.md")    # tracked target outside every skill root and scan root
        self.link_refused(".claude/skills/demo", "../../shared/demo")

    def test_skill_directory_link_with_out_of_repo_target_is_refused(self):
        outside = self.root.parent / "outside-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text(GOVERNING)
        self.link_refused(".agents/skills/demo", str(outside))

    def test_suffixless_link_deeper_under_a_skill_root_is_refused(self):
        outside = self.root.parent / "outside-references"
        outside.mkdir()
        (outside / "notes.md").write_text(GOVERNING)
        self.put(".opencode/skills/demo/SKILL.md", text="# Skill\n")
        self.link_refused(".opencode/skills/demo/references", str(outside))

    def test_skill_root_tracked_as_a_link_is_refused(self):
        # A tracked `.agents/skills -> ../.claude/skills`: git tracks nothing beneath the link,
        # so only the entry AT the root can send it to the refusal.
        self.put(".claude/skills/demo/SKILL.md", text="# Skill\n")
        self.link_refused(".agents/skills", "../.claude/skills")

    def test_skill_root_parent_tracked_as_a_link_is_refused(self):
        # A tracked `.claude -> shared-claude`: git tracks nothing beneath the link, so only the
        # entry AT the skill root's parent can send it to the refusal.
        self.put("shared-claude/skills/demo/SKILL.md", text="# Skill\n")
        self.link_refused(".claude", "shared-claude")

    # ---- a submodule (gitlink, index mode 160000) at or under a skill root -------------------
    def submodule_refused(self, rel):
        """Add a real submodule at `rel` whose SKILL.md cites the rule; the scan must refuse it."""
        batch = self.prepare(CHANGELOG)
        upstream = self.root.parent / "vendor-upstream"
        upstream.mkdir()
        (upstream / "SKILL.md").write_text(GOVERNING)
        sup.git(upstream, "init", "-q")
        sup.git(upstream, "add", "SKILL.md")
        sup.git(upstream, "commit", "-q", "-m", "vendor skill")
        sup.git(self.root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
                str(upstream), rel)
        self.assertEqual(sup.git(self.root, "ls-files", "-s", rel).split()[0], "160000")
        self.assertTrue((self.root / rel / "SKILL.md").is_file())    # a harness would load it
        with self.assertRaises(migration.Refused) as caught:
            migration._citation_inspection(self.root, batch)
        print(f"  submodule {rel}: Refused {caught.exception.code}", file=sys.stderr)
        self.assertEqual(caught.exception.code, "migration-citation-scope-refused")
        self.assertEqual(getattr(caught.exception, "remedy", None), migration.SUBMODULE_SOURCE_REMEDY)

    def test_submodule_under_a_skill_root_is_refused_with_remedy(self):
        self.submodule_refused(".claude/skills/vendor")

    def test_submodule_at_a_skill_root_is_refused_with_remedy(self):
        self.submodule_refused(".agents/skills")

    def test_submodule_remedy_is_path_free_and_names_the_way_out(self):
        remedy = migration.SUBMODULE_SOURCE_REMEDY
        for step in ("160000", "regular files", "git ls-files -s"):
            self.assertIn(step, remedy)
        self.assertNotIn(".claude", remedy)
        self.assertNotIn("Vendor the skill", remedy)
        # The `.git` entry is moved, never deleted: an embedded clone keeps its only history there.
        self.assertNotIn("or delete it", remedy)
        self.assertIn("loses any history not pushed elsewhere", remedy)
        # Only an embedded clone's `.git` directory holds history; a gitfile holds none, and a
        # pushed clone's history survives elsewhere, so the warning claims neither.
        self.assertNotIn("the only history", remedy)
        self.assertIn("moving it keeps the nested repository's history", remedy)
        self.assertIn("deleting the `.git` directory of an embedded clone loses any history not pushed elsewhere", remedy)
        # `<name>` is defined, with the command that finds it.
        self.assertIn("name of the `.gitmodules` section whose `path` is `<path>`", remedy)
        self.assertIn("git config -f .gitmodules --get-regexp '\\.path$'", remedy)
        untrack = remedy.index("git rm --cached <path>")
        section = remedy.index("git config -f .gitmodules --remove-section submodule.<name>")
        stage = remedy.index("git add .gitmodules")
        entry = remedy.index("`.git` entry")
        track = remedy.index("git add <path>")
        self.assertLess(untrack, section)
        self.assertLess(section, stage)
        self.assertLess(stage, entry)
        self.assertLess(entry, track)

    def _git_ok(self, *args):
        env = sup.scrubbed_env()
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        done = sup.subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, f"git {args}: {done.stderr}")
        return done.stdout

    def _make_submodule(self, shape, rel):
        upstream = self.root.parent / "vendor-upstream"
        upstream.mkdir()
        (upstream / "SKILL.md").write_text(GOVERNING)
        sup.git(upstream, "init", "-q")
        sup.git(upstream, "add", "SKILL.md")
        sup.git(upstream, "commit", "-q", "-m", "vendor skill")
        if shape == "gitfile":
            sup.git(self.root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
                    str(upstream), rel)
        else:
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            sup.git(self.root, "clone", "-q", str(upstream), rel)
            sup.git(self.root, "add", "-f", rel)
        sup.git(self.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "add submodule")
        self.assertEqual(sup.git(self.root, "ls-files", "-s", rel).split()[0], "160000")

    def test_submodule_remedy_followed_clears_the_refusal(self):
        import shutil
        import subprocess
        rel = ".claude/skills/vendor"
        for shape in ("gitfile", "embedded"):
            with self.subTest(shape=shape):
                self.setUp()
                batch = self.prepare(CHANGELOG)
                self._make_submodule(shape, rel)
                with self.assertRaises(migration.Refused) as caught:
                    migration._citation_inspection(self.root, batch)
                self.assertEqual(caught.exception.remedy, migration.SUBMODULE_SOURCE_REMEDY)
                # run the remedy's steps literally, in its order
                for step in ("git rm --cached <path>",
                             "git config -f .gitmodules --remove-section submodule.<name>",
                             "git add .gitmodules", "`.git` entry", "git add <path>"):
                    self.assertIn(step, caught.exception.remedy)
                self._git_ok("rm", "--cached", rel)
                gitmodules = self.root / ".gitmodules"
                if gitmodules.is_file():
                    names = [line.split()[0][len("submodule."):-len(".path")] for line in
                             self._git_ok("config", "-f", ".gitmodules", "--get-regexp",
                                          r"\.path$").splitlines()    # the command the remedy gives
                             if line.split()[1] == rel]
                    self.assertEqual(len(names), 1)
                    self._git_ok("config", "-f", ".gitmodules", "--remove-section", f"submodule.{names[0]}")
                    self._git_ok("add", ".gitmodules")
                dot_git = self.root / rel / ".git"
                # move the `.git` entry out of the repository; the remedy never deletes it
                kept = self.root.parent / f"moved-dot-git-{shape}"
                shutil.move(str(dot_git), str(kept))
                self.assertTrue(kept.exists())
                self._git_ok("add", rel)
                modes = [line.split()[0] for line in
                         self._git_ok("ls-files", "-s", "--", rel).splitlines()]
                self.assertEqual(sup.git(self.root, "ls-files", "-s", rel + "/SKILL.md").split()[0],
                                 "100644")
                self.assertNotIn("160000", modes)
                if gitmodules.is_file():
                    self.assertNotIn(rel, gitmodules.read_text())
                    self.assertNotIn(rel, self._git_ok("show", ":.gitmodules"))
                _, kinds = migration._citation_inspection(self.root, batch)
                self.assertIn(rel + "/SKILL.md", [row["path"] for row in kinds["governing"]])

    def test_submodule_copy_that_keeps_the_dot_git_entry_stays_a_submodule(self):
        # Positive control: the step the remedy names matters. Round-tripping the files while the
        # .git entry stays leaves git recording the submodule again, and the rerun refuses.
        import shutil
        rel = ".claude/skills/vendor"
        batch = self.prepare(CHANGELOG)
        self._make_submodule("embedded", rel)
        self._git_ok("rm", "--cached", rel)
        shutil.copytree(self.root / rel, self.root / "tmp-copy", symlinks=True)
        shutil.rmtree(self.root / rel)
        shutil.copytree(self.root / "tmp-copy", self.root / rel, symlinks=True)
        shutil.rmtree(self.root / "tmp-copy")
        self._git_ok("add", rel)
        self.assertEqual(sup.git(self.root, "ls-files", "-s", rel).split()[0], "160000")
        with self.assertRaises(migration.Refused) as caught:
            migration._citation_inspection(self.root, batch)
        self.assertEqual(caught.exception.remedy, migration.SUBMODULE_SOURCE_REMEDY)

    def test_directory_link_to_a_tracked_in_repo_target_governs_at_the_real_path(self):
        # Positive control for the limitation below: a tracked target is scanned at its real path.
        batch = self.prepare(CHANGELOG)
        self.put("shared/app/AGENTS.md")
        os.symlink("shared/app", self.root / "app")
        sup.git(self.root, "add", "-f", "app")
        self.assertEqual(sup.git(self.root, "ls-files", "-s", "app").split()[0], "120000")
        self.assertIn("shared/app/AGENTS.md", self.governing(batch))

    def test_directory_link_outside_skill_roots_holding_agents_md_known_limitation(self):
        # Recorded, not endorsed: a tracked directory link outside the skill roots is not followed.
        # A tracked in-repo target is already scanned at its real path (the test above). What
        # remains is a target outside this repository's index: out of the repository, ignored or
        # untracked, or under a directory name the linter excludes. The scan's membership rule
        # is the git index, so such content never changes the verdict.
        batch = self.prepare(CHANGELOG)
        outside = self.root.parent / "outside-app"
        outside.mkdir()
        (outside / "AGENTS.md").write_text(GOVERNING)
        os.symlink(outside, self.root / "app")
        sup.git(self.root, "add", "-f", "app")
        self.assertEqual(sup.git(self.root, "ls-files", "-s", "app").split()[0], "120000")
        self.assertEqual([], [p for p in self.governing(batch) if p.startswith("app")])

    def test_binary_skill_asset_is_not_scanned_and_a_helper_script_is(self):
        batch = self.prepare(CHANGELOG)
        asset = self.root / ".claude/skills/demo/logo.png"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe" + GOVERNING.encode())
        sup.git(self.root, "add", "-f", ".claude/skills/demo/logo.png")
        self.assertEqual([], [p for p in self.governing(batch) if "skills" in p])
        self.put(".claude/skills/demo/helper.py", text="# rule:rotation requires this helper.\n")
        self.assertIn(".claude/skills/demo/helper.py", self.governing(batch))

    def test_oversize_tracked_skill_file_is_refused(self):
        batch = self.prepare(CHANGELOG)
        self.put(".claude/skills/big/SKILL.md", text="x" * (migration.MAX_SOURCE_BYTES + 1))
        with self.assertRaises(migration.Refused) as caught:
            migration._citation_inspection(self.root, batch)
        self.assertEqual(caught.exception.code, "migration-source-oversize")

    def test_root_agents_md_is_read_once(self):
        batch = self.prepare(CHANGELOG)
        self.put("AGENTS.md")
        self.assertEqual(1, self.governing(batch).count("AGENTS.md"))

    def test_brief_mention_stays_historical(self):
        batch = self.prepare(CHANGELOG)
        brief = self.root / "docs/briefs/x.md"
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text("A brief may mention rule:rotation.\n")
        sup.git(self.root, "add", "-f", "docs/briefs/x.md")
        _, kinds = migration._citation_inspection(self.root, batch)
        self.assertFalse(kinds["governing"])
        self.assertIn("docs/briefs/x.md", {row["path"] for row in kinds["historical"]})


class SourceRules(_Base):
    put = Item1Sources.put
    governing = Item1Sources.governing

    def refusal(self, batch):
        with self.assertRaises(migration.Refused) as caught:
            migration._citation_inspection(self.root, batch)
        return caught.exception

    def test_restated_skill_roots_equal_adr_signals(self):
        # The module restates the roots adr-signals.py spells; this pins the two together.
        import importlib.util
        spec = importlib.util.spec_from_file_location("adr_signals_pin", os.path.join(SCRIPTS, "adr-signals.py"))
        signals = importlib.util.module_from_spec(spec); spec.loader.exec_module(signals)
        self.assertEqual(migration.LOCAL_SKILLS_DIRS, signals.LOCAL_SKILLS_DIRS)
        self.assertEqual(migration.FORGE_LOGS, {d + "/forge-log.md" for d in signals.LOCAL_SKILLS_DIRS})

    # ---- F4: exclusions ------------------------------------------------------------------
    def test_linter_excluded_locations_are_dropped_with_positive_control(self):
        batch = self.prepare(CHANGELOG)
        dropped = ["crux/scripts/tests/fixtures/corpus/AGENTS.md",
                   "crux/scripts/tests/fixtures/corpus/CLAUDE.md",
                   "docs/research/raw/upstream/AGENTS.md", "docs/inbox/drop/AGENTS.md",
                   "docs/journal/AGENTS.md", "docs/promptbooks/runs/x/AGENTS.md",
                   "node_modules/pkg/AGENTS.md", "vendor/.cache/pkg/CLAUDE.md", "dist/AGENTS.md"]
        for rel in dropped:
            self.put(rel)
        self.assertEqual([], [p for p in self.governing(batch) if p in dropped])
        # Positive control: the same bytes one directory over are refused.
        self.put("crux/scripts/fixtures-not-tests/AGENTS.md")
        self.assertIn("crux/scripts/fixtures-not-tests/AGENTS.md", self.governing(batch))

    def test_discover_write_scope_exclusions_are_not_applied(self):
        batch = self.prepare(CHANGELOG)
        kept = ["CLAUDE.local.md", "app/CLAUDE.local.md", ".claude/CLAUDE.md",
                "app/.claude/CLAUDE.md", "templates/AGENTS.md", "crux/templates/CLAUDE.md",
                "pkg/templates/AGENTS.md"]
        for rel in kept:
            self.put(rel)
        self.assertEqual(sorted(kept), sorted(p for p in self.governing(batch) if p in kept))

    def test_skill_root_files_are_never_dropped_by_directory_name(self):
        batch = self.prepare(CHANGELOG)
        kept = [".claude/skills/build/SKILL.md", ".agents/skills/dist/SKILL.md",
                ".claude/skills/x/.cache/notes.md", ".opencode/skills/x/node_modules/ref.md"]
        for rel in kept:
            self.put(rel)
        self.assertEqual(sorted(kept), sorted(p for p in self.governing(batch) if p in kept))

    def test_forge_log_at_a_skill_root_is_a_dated_record(self):
        # .claude/skills/forge-log.md is append-only; this checkout's copy cites an accepted
        # rule at line 562, so a migration of that rule would otherwise refuse on history.
        batch = self.prepare(CHANGELOG)
        logs = [d + "/forge-log.md" for d in (".claude/skills", ".agents/skills",
                                              ".opencode/skills", ".opencode/skill")]
        for rel in logs:
            self.put(rel)
        self.put(".claude/skills/demo/forge-log.md")    # positive control: not at a root
        found = self.governing(batch)
        self.assertEqual([], [p for p in found if p in logs])
        self.assertIn(".claude/skills/demo/forge-log.md", found)

    def test_vendor_tree_outside_linter_names_is_scanned_known_limitation(self):
        # Recorded, not endorsed: `vendor/` is no linter-excluded name, so a vendored
        # instruction file citing this batch's exact token would refuse. A finding would have
        # to name a crux rule slug of this repository; the cost is recorded in the Diagnosis.
        batch = self.prepare(CHANGELOG)
        self.put("vendor/pkg/AGENTS.md")
        self.assertIn("vendor/pkg/AGENTS.md", self.governing(batch))

    # ---- F4: a tracked candidate absent from the working tree ----------------------------
    def committed(self, rel):
        self.put(rel, text="# App\n")
        sup.git(self.root, "commit", "-q", "-m", "app")

    def test_unstaged_deletion_is_refused_with_remedy(self):
        batch = self.prepare(CHANGELOG)
        self.committed("app/AGENTS.md")
        (self.root / "app/AGENTS.md").unlink()
        self.assertIn("app/AGENTS.md", sup.git(self.root, "ls-files"))
        exc = self.refusal(batch)
        self.assertEqual(exc.code, "migration-source-unreadable")
        self.assertEqual(getattr(exc, "remedy", None), migration.ABSENT_SOURCE_REMEDY)

    def test_unstaged_deletion_of_a_skill_file_is_refused(self):
        batch = self.prepare(CHANGELOG)
        self.committed(".claude/skills/demo/SKILL.md")
        (self.root / ".claude/skills/demo/SKILL.md").unlink()
        self.assertEqual(self.refusal(batch).code, "migration-source-unreadable")

    def test_skip_worktree_absence_is_refused(self):
        batch = self.prepare(CHANGELOG)
        self.committed("app/AGENTS.md")
        sup.git(self.root, "update-index", "--skip-worktree", "app/AGENTS.md")
        (self.root / "app/AGENTS.md").unlink()
        self.assertTrue(sup.git(self.root, "ls-files", "-t", "app/AGENTS.md").startswith("S "))
        self.assertEqual(sup.git(self.root, "status", "--porcelain", "app"), "")
        self.assertEqual(self.refusal(batch).code, "migration-source-unreadable")

    def test_skip_worktree_present_bytes_are_read(self):
        batch = self.prepare(CHANGELOG)
        self.committed("app/AGENTS.md")
        sup.git(self.root, "update-index", "--skip-worktree", "app/AGENTS.md")
        (self.root / "app/AGENTS.md").write_text(GOVERNING)
        self.assertIn("app/AGENTS.md", self.governing(batch))

    def test_sparse_checkout_absence_is_refused(self):
        batch = self.prepare(CHANGELOG)
        self.committed("app/AGENTS.md")
        sup.git(self.root, "add", "-A"); sup.git(self.root, "commit", "-q", "--allow-empty", "-m", "all")
        sup.git(self.root, "sparse-checkout", "set", "--no-cone", "/*", "!/app/")
        self.assertFalse((self.root / "app").exists())
        self.assertTrue(sup.git(self.root, "ls-files", "-t", "app/AGENTS.md").startswith("S "))
        exc = self.refusal(batch)
        self.assertEqual(exc.code, "migration-source-unreadable")
        self.assertEqual(getattr(exc, "remedy", None), migration.ABSENT_SOURCE_REMEDY)

    def test_staged_deletion_is_no_candidate(self):
        batch = self.prepare(CHANGELOG)
        self.committed("app/AGENTS.md")
        sup.git(self.root, "rm", "-q", "app/AGENTS.md")
        self.assertEqual([], [p for p in self.governing(batch) if p.startswith("app/")])

    def test_absent_linter_excluded_fixture_is_not_refused(self):
        batch = self.prepare(CHANGELOG)
        self.committed("crux/scripts/tests/fixtures/corpus/AGENTS.md")
        (self.root / "crux/scripts/tests/fixtures/corpus/AGENTS.md").unlink()
        migration._citation_inspection(self.root, batch)

    # ---- F5: CLAUDE.md tracked as a link to AGENTS.md ------------------------------------
    def link_claude_to_agents(self):
        batch = self.prepare(CHANGELOG)
        self.put("AGENTS.md", text="# Agents\n")
        os.symlink("AGENTS.md", self.root / "CLAUDE.md")
        sup.git(self.root, "add", "-f", "CLAUDE.md")
        self.assertEqual(sup.git(self.root, "ls-files", "-s", "CLAUDE.md").split()[0], "120000")
        return batch

    def test_claude_md_link_refusal_code_is_unchanged_and_names_the_remedy(self):
        exc = self.refusal(self.link_claude_to_agents())
        self.assertEqual(exc.code, "migration-symlink-refused")
        self.assertEqual(str(exc), "migration-symlink-refused")
        self.assertEqual(getattr(exc, "remedy", None), migration.SYMLINK_SOURCE_REMEDY)
        for step in ("regular file", "real directory", "git rm --cached", "120000"):
            self.assertIn(step, exc.remedy)
        # Untracking cannot clear a link at a fixed root name: the scan reads those whether tracked or not.
        for name in ("AGENTS.md", "README.md", "USER_GUIDE.md", "CHANGELOG.md"):
            self.assertIn(name, exc.remedy)
        # The exception is named before the general case, so no sentence promises too much.
        self.assertLess(exc.remedy.index("CHANGELOG.md"), exc.remedy.index("At any other path"))
        self.assertNotIn("At most paths", exc.remedy)

    def test_absent_source_remedy_names_the_command_that_lists_an_unstaged_deletion(self):
        self.assertIn("git ls-files --deleted", migration.ABSENT_SOURCE_REMEDY)
        self.assertIn("git ls-files -t", migration.ABSENT_SOURCE_REMEDY)

    def test_claude_md_link_remedy_followed_clears_the_refusal(self):
        batch = self.link_claude_to_agents()
        sup.git(self.root, "rm", "-q", "--cached", "CLAUDE.md")    # untrack; the local link stays
        self.assertTrue((self.root / "CLAUDE.md").is_symlink())
        migration._citation_inspection(self.root, batch)
        sup.git(self.root, "add", "-f", "CLAUDE.md")
        (self.root / "CLAUDE.md").unlink()
        (self.root / "CLAUDE.md").write_text("@AGENTS.md\n")       # replace with a regular file
        sup.git(self.root, "add", "-f", "CLAUDE.md")
        migration._citation_inspection(self.root, batch)

    def test_cli_stdout_is_unchanged_and_remedy_goes_to_stderr(self):
        import contextlib, importlib.util, io, json
        self.link_claude_to_agents()
        spec = importlib.util.spec_from_file_location("im_cli", os.path.join(SCRIPTS, "implementation-migration.py"))
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["inventory", "--batch", str(self.f.f.path), "--repo-root", str(self.root)])
        print(f"  cli exit={code} stdout={out.getvalue().strip()} stderr={err.getvalue().strip()[:60]}...",
              file=sys.stderr)
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), '{"authority": "none", "limit": "migration-symlink-refused"}\n')
        self.assertEqual(json.loads(err.getvalue()), {"remedy": migration.SYMLINK_SOURCE_REMEDY})

    def test_cli_absent_source_remedy_goes_to_stderr_stdout_unchanged(self):
        import contextlib, importlib.util, io, json
        self.prepare(CHANGELOG)
        self.committed("app/AGENTS.md")
        (self.root / "app/AGENTS.md").unlink()
        spec = importlib.util.spec_from_file_location("im_cli_absent", os.path.join(SCRIPTS, "implementation-migration.py"))
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["inventory", "--batch", str(self.f.f.path), "--repo-root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), '{"authority": "none", "limit": "migration-source-unreadable"}\n')
        self.assertEqual(json.loads(err.getvalue()), {"remedy": migration.ABSENT_SOURCE_REMEDY})


if __name__ == "__main__":
    unittest.main()
