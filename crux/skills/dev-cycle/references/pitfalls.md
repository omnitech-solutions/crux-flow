# dev-cycle — rationalization table and common mistakes (reference)

Reinforcement for dev-cycle. The "Red flags" list in SKILL.md is the primary
stop-list; read this for the fuller excuse→reality mapping and the
recurring-mistake catalog.

## Rationalization table

| Excuse | Reality |
|---|---|
| "User wants a 10-prompt cycle to save time on a small change." | The cycle contract sets 13 as the floor. A 10-prompt sequence isn't a cycle; it's a custom promptbook. Use `author-promptbook` and don't tag it `dev-cycle`. |
| "User wants two ADRs but said 'just bolt the second one onto the first ADR module' — I'll skip the second council prompt." | Every ADR needs its own council pass. Two ADRs = two complete ADR modules (8 prompts of planning total). Refusing this is what makes the cycle worth more than `author-promptbook`. |
| "The dev module's internal review is redundant when the external review module exists — I'll cut it." | Internal review catches finding-classes the external review wouldn't (private invariants, work-in-progress flags). Both are load-bearing; both stay. |
| "User said the feature is simple — I'll cut to 1 ADR, 0 dev loops, 1 review." | M=0 violates the contract. Every cycle implements something; even a docs-only cycle has a dev module (the docs are the artifact). M ≥ 1, always. |
| "User didn't give a real goal; I'll write 'TBD' as a placeholder." | Planning agents have no memory of this conversation. A TBD goal yields a TBD ADR. Block until you have a real paragraph. |
| "I'll re-use PB-NNNN since the user deleted it." | Numbers are forever, even for deleted books. Allocate fresh. |
| "I'll increment `manifest.yml` first, then write — feels cleaner." | A failed write would burn the number. Write-then-increment, same as every other allocator in the project. |
| "I'll skip `docs/index.md`'s Promptbooks count — `audit-docs` will fix it." | Don't author drift on purpose. Update both files now. |
| "Two review modules feels like overkill; let me collapse them into one." | If the user requested two, the reason is probably "I want to review after each dev loop, not just at the end." Honor the request; the cost is 3 prompts. |

## Common mistakes

- **Authoring a cycle that omits the top-level `run_autonomy` field.**
  The composed (N/M/K > 1) path MUST carry it just as
  the canonical template does — `run-promptbook` reads the *book* at execution
  time, not this skill, so the autonomy contract has to live in the produced
  artifact's `run_autonomy` field and reference `docs/AGENTS.md` §11.
- **Leaving the run-autonomy paragraph in `## Strategy`** (the legacy
  location): there is no `## Strategy` Markdown section anymore — the
  book is structured YAML and the autonomy contract lives in the top-level
  `run_autonomy` field. Putting it in `strategy` instead duplicates it and
  hides it from `run-promptbook`'s structured read.
- **Forgetting to ask about module counts** when the user signals
  multi-step scope ("we'll need to migrate the schema AND update the UI").
  Always prompt for counts when scope sounds compound.
- **Computing `total_prompts` wrong**: the formula is `4N + 4M + 3K + 2`.
  Off-by-one in the +2 forgets the prep+summary pair.
- **Numbering prompts wrong after assembly**: `prompts[].n` is sequential
  starting at 1 across the fully concatenated list, regardless of which module
  produced each element. Assign `n:` AFTER concatenation and BEFORE writing the
  file; a gap or duplicate in `n:` creates hard-to-debug `run-promptbook`
  failures.
- **Hand-injecting a prompt number into a `module_tag`** or restructuring the
  YAML to renumber: `module_tag` is `^(adr|dev|review)-\d+$` where the integer
  is the module occurrence, NOT the prompt number. Numbering is `n:` only.
- **Dropping or mistyping a `module_tag`**: the three invariant checks count
  `module_tag`-tagged elements. A prep/summary prompt that accidentally gets a
  tag, or a module prompt missing its tag, breaks the count and the audit.
- **Forgetting the `modules:` block**: `audit-docs`'s cycle-coverage check
  cross-references it against the `module_tag` counts. Without it, audit reports
  the book as "cycle-shaped but unverifiable."
- **Forgetting `tags: [cycle, ...]`**: the `dev-cycle` tag is how `audit-docs`
  identifies cycle books for staleness AND invariant checks. Without it,
  an abandoned cycle won't be flagged.
- **Hand-editing the module templates** at `${CRUX_PLUGIN_ROOT}/templates/cycle-module-*.yaml`
  on a per-project basis: those are plugin source. Edits there affect every
  future cycle. If you need a project-specific cycle, copy the resulting
  book per-project; don't edit the templates.
- **Allocating `PB-NNNN` from `adr.next_number`**: those are separate
  counters. Promptbooks use `promptbook.next_number`. Never crossed.
- **Skipping the per-cycle prompt — eliding a fresh goal because "this is
  the second cycle for the same feature"**: each cycle gets its own
  goal. Even successor cycles have their own scope; don't paste the
  previous goal verbatim.

