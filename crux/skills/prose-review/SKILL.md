---
name: prose-review
description: "Review prose against the project's seven writing rules and propose concrete rewrites for violations."
metadata:
  tags: "writing, prose, review, style, editing"
  bundles: "crux-verification"
  risk_level: "low"
  triggers: "review this prose | prose review | tighten this writing | check the writing rules | edit this text"
  routing_note: "Checks text against the seven §16 writing rules and returns a rewrite for every finding (`fix` | `confirm`); a finding without replacement text is not a finding. Mandatory at three points: `release-preflight` over `README.md` + `CHANGELOG.md` (advisory until the waiver path is vendored), the `dev-cycle`/`iterate` prep prompt, and `tend-garden` before the morning note."
---

# Prose review

Review text against seven rules and return findings with rewrites. The rules descend from ASD-STE100 Simplified
Technical English, which constrains grammar and consistency rather than approving a vocabulary. Follow that
approach here: **there is no approved-word list and no banned-word list in this skill.** Every check below is a
test applied to a specific sentence in its context. A word that fails a test in one sentence can pass in the next.

If you catch yourself reaching for a list of "words to avoid," you are doing the wrong thing. Apply the test.

## The seven rules

> **Generated region — do not edit here.** This block is projected from the crux development repo's canonical
> writing-rules text by `crux/scripts/generate-writing-rules.py`. In that repo, edit the canonical file rather
> than this skill. The rules below are the standard this skill applies; the tests that follow are how it applies
> them.

<!-- BEGIN GENERATED: writing-rules -->
Applies to prose: docs, comments, commit messages, PR descriptions, and explanations in chat.
Does not apply to code, identifiers, quoted material, error strings, or anything you are reproducing verbatim.

The principle behind all seven rules: one idea, one sentence, one name. Accuracy outranks every rule below — if
following a rule would make a statement false or imprecise, break the rule and keep the truth.

**1. One name per thing.** Pick one term for each concept and repeat it. Repetition is correct here; variation
signals a new concept and makes the reader look for a difference that isn't there. If two words in a document
mean the same thing, one of them is wrong. The inverse also holds: don't use one word for two concepts.

**2. No hedge without a cause.** Delete any qualifier you cannot justify. If something is genuinely conditional,
name the condition instead of gesturing at it.
> Not: "This may fail under certain conditions."
> But: "This fails when the port is already bound."

**3. Verbs stay verbs.** Write the action as the main verb, not as a noun with a filler verb attached.
> Not: "Perform a validation of the payload."
> But: "Validate the payload."

**4. Adjectives must be checkable.** Drop any adjective the reader cannot verify. Replace it with the number, the
mechanism, or the failure it survives — or cut it. If nobody would ever claim the opposite about their own work,
the word carries no information.
> Not: "a robust, high-performance cache"
> But: "a cache that serves reads during a Redis failover, at ~40k req/s"

**5. One idea per sentence.** Aim for 20 words in instructions, 25 in explanation. Two independent clauses joined
by "and" or "but" are usually two sentences. Length is the symptom; the test is how many ideas are in there.

**6. Single-word verbs.** Prefer "configure" over "set up", "review" over "go through", "delete" over "get rid of",
"start" over "kick off". Keep the two-word form only when it is the established name of the thing — log in, shut
down, roll back, fall back, check out — and then use it consistently, because rule 1 wins over this rule.

**7. ADRs as footnotes.** In human-facing prose, cite a decision by a footnote, never by an inline ADR number or a
link that renders one. The sentence stands on its own. The footnote marker is free text; the footnote definition at
the document foot names the rule as `rule:<slug>` and carries no ADR number. Carve-outs: ADR bodies (their inline
cross-refs are the decision graph) and the journal (a dated record) cite inline. The journal cites `rule:<slug>`
where a rule exists and `ADR-NNNN` where the ADR carries no governs block.
> Not: "Enforced by our sixth ADR on style."
> But: "Enforced by our writing rules.[^rules]" — with `[^rules]: rule:footnote-definition-names-the-rule` at the foot.

When in doubt: write the shorter sentence and state the fact.
<!-- END GENERATED: writing-rules -->

## What to review, and what to leave alone

Review: body prose, headings, doc comments, commit and PR text, error messages the author wrote.

Leave alone, and never file findings against:

- Code, identifiers, file paths, config keys, CLI flags, log output
- Quoted material, citations, and anything attributed to a third party
- Terms fixed by an external API, spec, standard, or legal requirement
- Proper nouns and product names
- Text the user marked as out of scope

When a rule collides with accuracy, accuracy wins and there is no finding. A hedge that reflects real uncertainty,
an adjective backed by a stated measurement, and a long sentence carrying one indivisible idea are all correct.

## Order of work

Do a document pass, then a sentence pass. The order matters because rule 1 can only be judged across the whole
text, and its resolution can change what you flag under rule 6.

1. **Document pass.** Read the entire text. Build a concept inventory (rule 1). Note the register: are these
   instructions, or explanation? Instructions get the tighter sentence budget.
2. **Sentence pass.** Walk sentence by sentence and apply rules 2 through 7.
3. **Resolution pass.** Reconcile findings against each other and against the precedence rules below, then write
   the report.

## The seven checks

### 1. One name per concept

**Test.** For each concept that appears more than once, list every surface form used for it. Flag when one concept
carries two or more forms and the text draws no distinction between them. Flag the inverse too: one word doing
duty for two different concepts.

**Don't flag.** Pronouns and ordinary anaphora. Forms that genuinely differ in meaning or scope. A synonym
introduced once with an explicit equivalence statement ("the run queue, hereafter the queue"). Terms fixed
externally. A heading that restates a term in a different register on purpose.

**Rewrite.** Choose one form and apply it everywhere. Prefer, in order: the name the code or API already uses, the
name the project's existing docs use, then the most specific of the candidates. State the chosen term and list
every location that needs to change.

### 2. Hedging without a cause

**Test.** Two tests, in order.

- *Deletion test.* Remove the qualifier. If the meaning is unchanged, the qualifier was noise — delete it.
- *Naming test.* If removal does change the meaning, the sentence marks real uncertainty. Ask whether the source
  of that uncertainty can be named: a condition, a version, a measurement boundary, a missing source. If it can,
  name it. If it genuinely cannot, keep the hedge and say why it's there.

**Don't flag.** Uncertainty that is attributed ("the RFC doesn't specify the ordering"). Probability given with a
basis. Safety-relevant caution. Conditionals that already state their condition. Deliberate softening in
interpersonal text such as review comments — check the register before flagging.

**Rewrite.** Give the deletion, or give the named condition. Do not offer "consider rephrasing."

### 3. Frozen verbs

A frozen verb is an action trapped in a noun while a low-content verb occupies the verb slot.

**Test.** Find the real action of the sentence. If it sits in a noun derived from a verb — the -tion, -ment, -ance,
-al, -ure, and gerund shapes — and the grammatical verb is a general-purpose one such as perform, conduct, make,
do, provide, achieve, undertake, or effect, the verb is frozen. Confirm by rewriting with the buried verb as the
main verb: if nothing is lost, it was frozen.

**Don't flag.** Nominalizations naming a thing rather than an action — a deployment as an artifact, authentication
as a subsystem, an exception as an object. Cases where the noun is the subject under discussion ("deployment is
the slow part"). Cases where promoting the verb would force you to invent an actor the source doesn't name.

**Rewrite.** Promote the buried verb. Name the actor if the sentence now needs one and the text supports it; if it
doesn't, say so rather than inventing one.

### 4. Uncheckable adjectives

**Test.** Two tests; either one failing is enough.

- *Falsifiability test.* Could a reader check this and find it false? If there is no observation that would
  contradict the adjective, it carries no information.
- *Negation test.* Would anyone ever apply the opposite adjective to their own work? Nobody ships a fragile,
  sluggish, confusing library. An adjective whose opposite is never claimed is decoration.

**Don't flag.** Adjectives with their basis stated in the same breath. Comparatives with data behind them.
Adjectives inside quoted marketing copy being discussed or critiqued. Ordinary descriptive adjectives that are
plainly checkable — a *red* button, a *nullable* column, an *idempotent* handler.

**Rewrite.** Supply the measurement, the mechanism, or the failure survived — drawn from the text if it's there.
If the document contains no supporting fact, say that the claim needs a number the author has and you don't, and
offer the deletion as the fallback.

### 5. More than one idea per sentence

**Test.** Count ideas, not words — but use length as the trigger for looking. Examine any sentence over 20 words in
instructional text or 25 in explanatory text. Then count: independent clauses joined by a coordinator, finite
verbs, and stacked subordinate clauses. Two independent clauses joined by "and", "but", or a semicolon are
usually two sentences. A long sentence expressing one indivisible relation is fine and should pass.

**Don't flag.** Sentences whose length comes from a list of parallel items. Sentences where splitting would strand
a pronoun or break a causal link that the reader needs held together. Deliberate rhythm in a short passage,
provided the sentence is still unambiguous.

**Rewrite.** Give the actual split, with the connective made explicit where the original relied on juxtaposition.
Don't just report the word count.

### 6. Phrasal verbs

**Test.** Find verb-plus-particle constructions. Then:

- Is the meaning compositional, or idiomatic? Idiomatic ones ("the build fell over") are the strongest candidates.
- Does a single-word verb carry the same meaning? If yes, that is the rewrite.
- Is the phrasal form the established name for the operation — log in, shut down, roll back, fall back, check out,
  set up in the sense a CLI uses it? If yes, keep it, and then check it is used consistently, which is rule 1.

Also check the noun/verb spelling split, which is a frequent error and easy to fix: *set up* the server produces a
*setup*; users *log in* at the *login* page; you *roll back* a *rollback*.

**Don't flag.** Established operation names. Verbs where the single-word replacement shifts meaning or raises the
register beyond the document's voice — forcing "ascertain" in place of "find out" makes the text worse, and the
right rewrite there may be "check". Particles that are prepositions attached to a following noun rather than to
the verb.

**Rewrite.** Give the single-word verb. If the best replacement changes the register, say so and offer both.

### 7. ADR numbers in prose and footnote definitions

**Test.** Two tests, in order.

- *Running text.* Find every ADR number in the prose, bare or as the visible text of a link. Each one is a finding.
- *Footnote definition.* Read every `[^…]:` definition line the way you read running text. A definition carrying an
  ADR number, bare or as a link that renders one, is a finding. A definition naming the rule as `rule:<slug>`
  passes. The marker before the colon is free text and is never a finding.

**Don't flag.** An ADR body, whose inline cross-refs are the decision graph. A journal entry, which is a dated
record: it cites `rule:<slug>` where a rule exists and `ADR-NNNN` where the ADR carries no governs block. Code,
inline code, link destinations, frontmatter, and quoted material.

**Rewrite.** Move the number out of the sentence into a footnote marker, and write the definition as
`[^marker]: rule:<slug>`, naming the rule the sentence relies on. If the text does not name the rule, file the
finding as `confirm` and ask the author for the slug; the sentence and its marker are correct without it.

## Precedence when checks collide

- Accuracy beats all seven rules. No finding.
- Rule 1 beats rule 6. If the established term is phrasal, keep it and use it everywhere.
- Rule 1 beats rule 3. If a nominalization is the project's name for a thing, it isn't a frozen verb.
- Rule 5 splits before rules 2 and 3 are applied. Split the sentence first, then re-test the parts; splitting often
  dissolves the other findings on its own.

## Output

Report only what you would change. Every finding carries a rewrite — **if you cannot write the replacement text,
you do not have a finding.** This is the discipline that keeps the review useful; "consider revising" is not a
finding and should never appear in output.

Use two severities:

- `fix` — the rewrite is correct from the text alone.
- `confirm` — the rewrite needs a fact only the author has, such as a benchmark number or which of two terms is
  the real one. State precisely what you need.

Format each finding like this:

```
[line 42] rule 4 · fix
  "a blazingly fast, seamless migration path"
  Neither adjective can be checked, and nobody advertises a slow, awkward migration.
  → "a migration path that runs without downtime"
```

Then close with a summary: counts per rule, and the single change that would most improve the text. Keep the
summary to a few lines.

If the text is clean, say so plainly and name the rules you checked. Do not manufacture findings to look thorough.

## Precision over recall

A review with four correct findings beats one with twenty findings of which half are wrong, because a wrong finding
teaches the author to distrust the whole report. When a call is genuinely close, either drop it or file it as
`confirm` with the question spelled out. Never file a finding you would abandon if the author pushed back once.

## Worked example

Input:

> Once the configuration file has been set up, the system will generally perform a validation of all incoming
> requests, and this powerful validation layer may help to prevent a number of potential issues.

Findings:

```
[line 1] rule 5 · fix
  One sentence carrying three ideas: configuring, validating, and the effect of validating.
  → Split at "and this ... layer".

[line 1] rule 6 · fix
  "set up" — a single-word verb carries the same meaning here.
  → "configured"

[line 1] rule 3 · fix
  "perform a validation of" — the action is frozen in a noun.
  → "validates"

[line 1] rule 2 · confirm
  "generally", "may help to", and "potential" all survive the deletion test with no change in meaning.
  Removing them asserts that validation always runs and always rejects malformed requests. Confirm that
  is true; if validation is skipped in some mode, name that mode instead.

[line 1] rule 4 · fix
  "powerful" fails the negation test — no author calls their own validation layer weak.
  → delete

[line 1] rule 1 · confirm
  "issues" is vaguer than "requests" and may name the same thing from the other side.
  Confirm whether the rejected items are malformed requests; if so, use that term.
```

Rewrite, assuming both `confirm` items resolve as expected:

> After you configure the file, the system validates every incoming request and rejects malformed ones.

Twenty-nine words become sixteen, and the sentence now states what actually happens.

## Recording findings when a release depends on them

When this skill runs as a release gate, the disposition of each finding is recorded, not recalled. Write one line
per finding to the release artifact: the rule, the severity, the location, and whether the author accepted or
rejected the rewrite. The record exists because the acceptance evidence for these rules is self-graded — the same
author who adopted them decides which findings to accept — so the disposition has to be auditable rather than
remembered.

## Apply the rules to your own report

The findings you write are prose. Hedged, adjective-laden review comments undercut the review. Write each
explanation as one short sentence that names the test the text failed.
