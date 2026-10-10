"""Delivery-scope regression matrix for implementation results (items 4, 5 and 6).

Synthetic fixtures built with the shipped Fixture harness; no council, model or reviewer is
convened. Each row builds a repository, applies its preimage and delivery file operations, and
records the outcome of write_result or query.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup
import test_implementation_decisions as tdm
import implementation_decisions as ids

W = ["widget.txt"]
LONG = "stable content that git will detect as a rename\n" * 4


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def vcs(r, *a):
    sup.git(r, *a)


def build(root, scope, pre, deliver):
    real_commit = sup.commit_all

    def commit(r, msg="c"):
        if msg == "synthetic draft and preimage":
            pre(Path(r))
        elif msg == "synthetic delivery":
            deliver(Path(r))
        real_commit(r, msg)

    base_slot, base_decision = tdm.slot, tdm.decision
    with mock.patch.object(tdm, "slot", lambda: {**base_slot(), "scope": list(scope)}), \
         mock.patch.object(tdm, "decision", lambda run="RUN-001": {**base_decision(run), "scope": list(scope)}), \
         mock.patch.object(sup, "commit_all", commit):
        return tdm.Fixture(root)


def make(tmp, scope, pre, deliver, listed, state="complete", rng=False):
    f = build(Path(tmp) / "repo", scope, pre, deliver)
    report = json.loads(f.report.read_text())
    known = {e["path"] for e in report["subject"]["paths"]}
    for rel in listed:
        if rel not in known and os.path.lexists(f.root / rel) and (f.root / rel).is_file():
            report["subject"]["paths"].append(f.ref(f.root / rel))
    f.report.write_text(json.dumps(report))
    reviews = [f.ref(f.report)]
    if rng:
        rpath = f.env.run_dir / "reviews/range.json"
        doc = sup.fixture("reviewer-report-paths.json")
        doc.update(book={"id": f.run["book_id"], "content_hash": f.run["book_content_hash"]},
                   run_id=f.run["run_id"],
                   subject={"form": "commit-range", "range": f.preimage + ".." + f.delivery})
        rpath.write_text(json.dumps(doc))
        reviews.append(f.ref(rpath))
    if sup.git(f.root, "status", "--porcelain").strip():
        sup.commit_all(f.root, "synthetic reviews")
    reviews = [f.ref(f.root / r["path"]) for r in reviews]
    srcs = [{"path": rel, "preimage": ids.source_hash(f.root, f.preimage, rel),
             "delivered": ids.source_hash(f.root, f.delivery, rel)} for rel in listed]
    doc = {**f.result, "delivery_state": state, "scope": list(listed), "sources": srcs, "reviews": reviews}
    return f, doc


def attempt(fn):
    try:
        fn()
        return "accepted"
    except ids.Refused as exc:
        return "refused:" + exc.code
    except Exception as exc:  # a harness or source error is an outcome, never a pass
        return "error:" + type(exc).__name__ + ":" + str(exc)[:120]


def write_row(scope, pre, deliver, listed, state="complete", rng=False):
    def run(tmp):
        f, doc = make(tmp, scope, pre, deliver, listed, state, rng)
        return attempt(lambda: ids.write_result(f.root, f.path, doc))
    return run


def query_outcome(f):
    answer = ids.query(f.root, f.path)
    hist = answer["historical_delivery"][0]
    return "query:" + str(answer["current_state"]["state"]) + (":" + hist["limit"] if hist.get("limit") else "")


def pre_src(r):
    write(r, "src/a.txt", "a0\n")


def deliver_ab(r):
    write(r, "src/a.txt", "a1\n")
    write(r, "src/b.txt", "b1\n")


def pre_old(r):
    write(r, "src/old.txt", LONG)


def mv_in_dir(r):
    vcs(r, "mv", "src/old.txt", "src/new.txt")


def move_out(r):
    (r / "lib").mkdir()
    vcs(r, "mv", "src/old.txt", "lib/old.txt")


def move_in(r):
    (r / "src").mkdir()
    vcs(r, "mv", "lib/x.txt", "src/x.txt")


def pre_ac(r):
    write(r, "src/a.txt", "a0\n")
    write(r, "src/c.txt", "c0\n")


def del_a(r):
    (r / "src/a.txt").unlink()
    write(r, "src/c.txt", "c1\n")


def pre_docs(r):
    pre_src(r)
    write(r, "docs/d.txt", "d\n")


def deliver_a1(r):
    write(r, "src/a.txt", "a1\n")


MAGIC = ":(exclude)src/b.txt"


def cross(scope, pre, deliver, listed, rng=False):
    """A record the old set-equality rule accepted, committed, then queried under the current rule."""
    def run(tmp):
        f, doc = make(tmp, scope, pre, deliver, listed, "complete", rng)
        # The old rule required equality; this record satisfies it, so it is a base-written shape.
        assert set(doc["scope"]) == set(scope)
        ids._write_new(f.path.with_name("result-001.yaml"), doc)
        sup.commit_all(f.root, "synthetic result")
        return query_outcome(f)
    return run


def dangling(kind):
    def run(tmp):
        f, doc = make(tmp, W + ["gone.txt"], lambda r: write(r, "gone.txt", "bye\n"),
                      lambda r: (r / "gone.txt").unlink(), W + ["gone.txt"], rng=True)
        link = f.root / "gone.txt"
        if kind == "write-symlink":
            os.symlink("nowhere-target", link)
            return attempt(lambda: ids.write_result(f.root, f.path, doc))
        got = attempt(lambda: ids.write_result(f.root, f.path, doc))
        if got != "accepted":
            return "write-" + got
        sup.commit_all(f.root, "synthetic result")
        if kind == "query-symlink":
            os.symlink("nowhere-target", link)
        else:
            link.write_text("squatter\n")
        return query_outcome(f)
    return run


def pre_magic(r):
    write(r, "src/a.txt", "a0\n")
    write(r, "src/b.txt", "b0\n")
    write(r, MAGIC, "m0\n")


def deliver_magic(r):
    write(r, "src/a.txt", "a1\n")
    write(r, "src/b.txt", "b1\n")
    write(r, MAGIC, "m1\n")


def pre_glob(r):
    write(r, "src/[a].txt", "lit0\n")
    write(r, "src/a.txt", "a0\n")


def deliver_glob(r):
    write(r, "src/[a].txt", "lit1\n")
    write(r, "src/a.txt", "a1\n")


def pre_star(r):
    write(r, "*", "s0\n")
    write(r, "other.txt", "o0\n")


def deliver_star(r):
    write(r, "*", "s1\n")
    write(r, "other.txt", "o1\n")


ROWS = {
    "R1-file-scope-exact": (write_row(W, lambda r: None, lambda r: None, W), "accepted"),
    "R2-dir-scope-complete": (write_row(W + ["src"], pre_src, deliver_ab, W + ["src/a.txt", "src/b.txt"]),
                              "accepted"),
    "R3-dir-scope-omits-changed-file": (write_row(W + ["src"], pre_src, deliver_ab, W + ["src/a.txt"]),
                                        "refused:complete-scope-missing"),
    "R4-out-of-scope-file": (write_row(
        W + ["src"], pre_src, lambda r: (deliver_ab(r), write(r, "srcx.txt", "x\n")),
        W + ["src/a.txt", "src/b.txt", "srcx.txt"]), "refused:result-scope-refused"),
    "R5-partial": (write_row(W + ["src"], pre_src, deliver_ab, W + ["src/a.txt"], state="partial"),
                   "accepted"),
    "R6a-unchanged-authorized-entry-omitted": (write_row(
        W + ["src", "docs"], pre_docs, deliver_a1, W + ["src/a.txt"]), "refused:complete-scope-missing"),
    "R6b-unchanged-authorized-entry-listed": (write_row(
        W + ["src", "docs"], pre_docs, deliver_a1, W + ["src/a.txt", "docs/d.txt"]), "accepted"),
    "R7-rename-file-entries": (write_row(
        W + ["old.txt", "new.txt"], lambda r: write(r, "old.txt", LONG),
        lambda r: vcs(r, "mv", "old.txt", "new.txt"), W + ["old.txt", "new.txt"], rng=True), "accepted"),
    "R8-rename-in-dir-omits-old-name": (write_row(
        W + ["src"], pre_old, mv_in_dir, W + ["src/new.txt"], rng=True), "refused:complete-scope-missing"),
    "R9-rename-in-dir-both-names": (write_row(
        W + ["src"], pre_old, mv_in_dir, W + ["src/old.txt", "src/new.txt"], rng=True), "accepted"),
    "R10-rename-out-of-scope-complete": (write_row(
        W + ["src"], pre_old, move_out, W + ["src/old.txt"], rng=True), "accepted"),
    "R10p-rename-out-of-scope-partial": (write_row(
        W + ["src"], pre_old, move_out, W + ["src/old.txt"], state="partial", rng=True), "accepted"),
    "R11-rename-into-scope": (write_row(
        W + ["src"], lambda r: write(r, "lib/x.txt", LONG), move_in, W + ["src/x.txt"], rng=True),
        "accepted"),
    "R12-deletion-listed": (write_row(W + ["src"], pre_ac, del_a, W + ["src/a.txt", "src/c.txt"], rng=True),
                            "accepted"),
    "R13-deletion-omitted": (write_row(W + ["src"], pre_ac, del_a, W + ["src/c.txt"], rng=True),
                             "refused:complete-scope-missing"),
    "R14-glob-bracket-name": (write_row(W + ["src/[a].txt"], pre_glob, deliver_glob, W + ["src/[a].txt"]),
                              "accepted"),
    "R15-star-name": (write_row(W + ["*"], pre_star, deliver_star, W + ["*"]), "accepted"),
    "R16-pathspec-magic-entry-hides-changed-file": (write_row(
        W + ["src", MAGIC], pre_magic, deliver_magic, W + ["src/a.txt", MAGIC]),
        "refused:complete-scope-missing"),
    "R17a-old-equality-record-file-scope": (cross(
        W + ["a.txt"], lambda r: write(r, "a.txt", "a0\n"), lambda r: write(r, "a.txt", "a1\n"),
        W + ["a.txt"]), "query:delivered"),
    "R17b-old-equality-record-rename-new-name-only": (cross(
        W + ["new.txt"], lambda r: write(r, "old.txt", LONG), lambda r: vcs(r, "mv", "old.txt", "new.txt"),
        W + ["new.txt"], rng=True), "query:delivered"),
    "R18-query-dangling-symlink-at-deleted-path": (dangling("query-symlink"),
                                                   "query:UNOBSERVED:queried-source-dirty"),
    "R19-write-dangling-symlink-at-deleted-path": (dangling("write-symlink"),
                                                   "refused:delivered-source-dirty"),
    "R20-query-untracked-regular-file-control": (dangling("query-file"),
                                                 "query:UNOBSERVED:queried-source-dirty"),
}


class DeliveryScopeMatrix(unittest.TestCase):
    def test_matrix_rows(self):
        self.assertEqual(len(ROWS), 23)
        for name, (run, expected) in ROWS.items():
            with self.subTest(row=name), tempfile.TemporaryDirectory() as tmp, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run(tmp), expected)

    def test_file_only_scope_acceptance_equals_old_set_equality(self):
        """Legs (a)+(b) equal the old set equality for file-only scopes; leg (c) adds nothing.

        Acceptance widens only for directory entries.
        """
        scope = W + ["a.txt"]
        cases = [(W + ["a.txt"], "accepted"), (["a.txt"] + W, "accepted"),
                 (W, "refused:complete-scope-missing"), (["a.txt"], "refused:complete-scope-missing")]
        for listed, expected in cases:
            with self.subTest(listed=listed), tempfile.TemporaryDirectory() as tmp, \
                    contextlib.redirect_stdout(io.StringIO()):
                run = write_row(scope, lambda r: write(r, "a.txt", "a0\n"), lambda r: write(r, "a.txt", "a1\n"),
                                listed)
                self.assertEqual(run(tmp), expected)


if __name__ == "__main__":
    unittest.main()
