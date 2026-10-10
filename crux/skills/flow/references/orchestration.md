# Orchestrating delegates

Read this before a run hands work to more than one delegate, or to a delegate on another runtime.
It records what a multi-day, many-delegate session showed to work and what went wrong. It adds no
step, gate or permission: the mode's caps and the acceptance contract still decide.

## The one rule

**One orchestrator; every delegate is a leaf.** The session that owns the run delegates. A
delegate does its unit itself and starts no agent, sub-agent or worker of its own, on any runtime,
however large the unit looks. A delegate that finds its unit too large stops and hands back what
remains as work orders; the orchestrator decides who takes them.

- Say it in every brief, in these words: "You must not start any sub-agent or worker; do all work
  in your own session." A generated role enforces it by having no delegation tool; a
  general-purpose delegate has only the brief.
- In aggressive, balanced and thorough modes the primary session is the only orchestrator. The role
  files Flow generates for those modes grant no delegation tool on any host (`dev-lead` integrates
  its own unit; it does not dispatch), so the mode's caps count every delegate.
- Upstream mode is upstream Crux's own behaviour: its roles and their chain are left exactly as
  upstream wrote them, and this rule does not apply there.
- Why: a host caps concurrent agents and counts nested ones, so a fan-out of delegates that each
  fan out exhausts the cap and blocks the orchestrator from starting the work it most needs;
  nested delegates spend the usage limit several times over on re-reading; and nobody can see,
  stop or re-brief a delegate two levels down.

## What worked

**Briefing**
- Brief a delegate as a peer who knows nothing: the goal in the owner's own words, what is already
  ruled out, the files worth reading first, the exact files it owns, what it must not touch, how
  to verify, and the shape and word limit of the report.
- Audit first. A read-only delegate writes one document; the orchestrator decides from it; a second
  delegate builds. An audit that also implements defends its own first idea.
- Give each delegate disjoint file ownership, and name one steward for any file several units must
  touch (a control inventory, a debt ledger, a lockfile).
- Ask for a scope checkpoint in the report when a unit passes about 1,000 changed lines, broken
  into parts that can be reviewed separately.

**Changing code safely**
- Characterisation tests before a move, passing on the old code first. Move verbatim, then convert
  as a separate step.
- Prove or drop: a mechanism ships only when a measurement shows it helps and loses nothing. Ablate
  each mechanism alone; keep a case only it solves; keep deliberately broken implementations that
  the check must fail.
- A ratchet with one allow-list that fails on a new violation **and** on an entry that is no
  longer needed. Debt can then only go down, and the list cannot rot.
- Decide a default by a sweep and record the sweep beside the constant.

**Running many delegates**
- Broadcast a change of plan by pointing every delegate at one file ("read this file now and
  follow it"): a short message each, one source of truth.
- Give a report time, a "start nothing new after" time, and the rule that a complete, compiling,
  smaller change beats a larger broken one.
- Delegates run their own suites and a typecheck. The orchestrator runs the full gate once, after
  every delegate has stopped.
- Offload to a runtime on a separate quota when the limit is near, and vet its output yourself:
  read the diff, run the tests it could not run, exercise the behaviour, then fix or reject.
- Use a cheaper model for delegates; keep the strongest for orchestration, review and integration.

**Keeping faith with the owner**
- Record a decision the owner makes as a rule or a memory the moment it is made, and apply it
  without asking again.
- Private material and anything derived from it stay outside every repository; a public repository
  receives general terms only.
- Say what a number does not show: in-sample, a single run, a scripted model, a stale tree.

## Pitfalls

**Delegation**
- A delegate that starts its own delegates (see the one rule).
- Two delegates writing one file; a delegate editing a file outside its ownership "to make it
  compile". Stop and report instead.
- Committing while a delegate is still writing. Confirm it has stopped first.
- A delegate that opens the owner's running application, or starts or stops the owner's servers.

**A shared working tree**
- One delegate's half-finished move breaks another's typecheck: each must say when a failure is
  not its own, and nobody "fixes" another's file.
- A full gate run while delegates are editing tests a tree that no longer exists. Its result is
  stale; do not commit on it.
- Many suites at once starve the machine: database fixtures time out in their set-up hook and
  timing assertions fail. Limit test workers, and never raise a shared timeout to get past it.
- Orphaned test processes left by a stopped delegate. Check for them; stop them.

**Gates**
- Reading the wrapper's exit status instead of the gate's. Write the gate's own exit code into its
  log and read that.
- A long gate that stops at its first step. Run the formatter and the linter, and read their exit
  code, before starting it. Better: have formatting applied automatically on write and on commit.
- Retrying a failing check until it passes. A pass after a failure on a deterministic check means
  something is wrong; a gate must name the stage that failed.
- A guard that describes the old layout fails after a refactor. Update it with a specific reason
  per entry; never excuse a genuine violation to make it pass.

**Tests and measurements**
- A test can encode a defect as the requirement. When a safety fix breaks tests, ask whether they
  were the defect written down.
- Mocks that are not restored between tests: a spy from one test is counted in the next.
- A sandbox that cannot reach the database or the network hands back code with tests it never ran.
- A count made by pattern matching can be wrong (a schema declaration counted as a query). Read a
  sample before briefing a delegate on the number.
- A benchmark tuned on its own questions, or a scripted model that returns the expected answer,
  proves the harness, not the result.

**Code a refactor leaves behind**
- A service that only forwards to a repository; a wildcard export that leaks internals; an option
  nobody passes; a component with no tests behind a new dependency. Remove them at integration.
- An inventory that must change with the change (controls, exports, raw statements): name it in the
  brief, or the next gate finds it.

**Hosts and limits**
- A commit hook that reinstalls host tooling: disable it for the commit and run the refresh as its
  own deliberate step.
- A usage limit reached mid-run stops everything. Read it before a large fan-out and during one;
  at the threshold the owner sets, stop starting delegates and finish what is in hand.
