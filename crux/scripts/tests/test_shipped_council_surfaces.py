"""Content scan: no shipped surface offers a council alternative.

Council deliberation is the council runner obtaining verdicts from three
providers on the models the router registry assigns. Independent review is a
reviewer's examination. This test reads every shipped template, skill, agent
and box file and shows that none of them

* D1 carries a withdrawn council alternative (`council_alternative` owns the
  patterns),
* D2 pairs a council or seat word with equivalence vocabulary: "equivalent",
  "instead of" or "in place of" a council (or council deliberation, or
  convening the council), "in lieu of", "substitute for" or "stand in for" a
  council, "replace" a council, "the same as" a council, "count as" a council
  verdict or vote, and "satisfy" a council gate. The determiner may be the, a,
  any, every, this, that, its or their, or absent; a possessive "'s" takes a
  straight or curly apostrophe, and "council-gate" counts,
* D3 pairs a council or seat word with a model name, where the registry roles
  (`openai_top`, `anthropic_top`, `google_top`) are the names to use. A model
  name is a registry key or API string, a versioned display name ("Gemini 3",
  "GPT-6", "Claude Opus"), or a bare capitalised family name (`Opus`, `Sonnet`,
  `Haiku`, `GPT`, `Gemini`),
* D3b passes a model name to a council call in a fenced code example, where
  the call is an export of `crux.council` or the keyword is `models=` or
  `arbiter=`,
* D4 mentions `srde` in an adr-module prompt of a YAML template,
* D5 omits `run-council.py` from a council-gate prompt, or
  `write-review-report.py` from a review-gate prompt, or
* D6 splits the evaluation by dimension across seats: "five seats, agents,
  reviewers or subagents" with "one dimension", "one per dimension", "one for
  each dimension", "a dimension each" or "apiece", or a "single", "own",
  "separate" or "different" dimension in one window; "one seat (agent,
  subagent, reviewer) per dimension" or "five dimensions across five seats" on
  its own; or "five Agent calls" (also "five Agents in parallel", "5 Agent tool
  calls") beside a council word. The five dimensions do not imply five seats,
  and every seat assesses every dimension.

Structure. The surface assertions are one test method per detector
(`SurfaceTests`). They are RED until every shipped surface is corrected, so a
failure there is a finding about a surface, never about the detector. The
detectors are proven separately (`DetectorControlTests`): each turns red on a
seeded string, one end-to-end control copies a real template, injects one
violating line and shows the file-reading path reports it, and the corrected
wording passes. `CoverageTests` shows the scan reads a non-empty population
derived from disk, so a green surface test is not a scan of nothing.

Sentence windows and their limits. A detector that pairs two words works on a
window: the text splits at blank lines and at Markdown list, table and heading
starts, whitespace and backticks are normalized, and the window then splits at
a sentence end. Inside a fenced code block each line is its own window, because
a code line is a statement and a fence mixes unrelated lines. Two limits
follow. A false negative: a council word and a model name in different windows
(a heading and the line beneath it, two sentences, two list items) pair
nothing. A false positive: a sentence that mentions a council and a model in
different clauses, with no offer between them, matches. D3 reads registry
API calls in code fences by key (`get_model_config("claude-opus-5.5")`) as code
that selects a model, not as council prose, because each fence line is its own
window; a comment line inside the fence that names both still matches. D3b
reads fenced code only and works on the call, not the line: it reads each
`crux.council` export call (the names come from the `__all__` of
`crux/council/__init__.py`, read with `ast`) up to its balanced close
parenthesis, across lines, and each `models=` or `arbiter=` value up to its
next top-level comma or closing bracket. A registry lookup in a fence passes;
a council call that names a model token fails. Singular `model=` is out of
scope.
Agent frontmatter `model:` and `effort:` lines are removed before D3 because
the agent catalog governs them.

Negation in D2. The verb forms (replace, "the same as", "count as", "satisfy")
describe the corrected wording when a negator (never, no, not, cannot, neither,
nor, none, nothing, nobody, without, or "n't") stands among the four words
before the verb, in the same clause, so "a reviewer report never satisfies a
council gate" passes. A clause ends at `;`, `:`, `,`, a dash, a contrast word
(but, yet, although, however), a coordinator (and, or, so, then) or a
subordinator (if, when, whenever, unless, while). So "do not convene the council
and let the reports replace it" and "if no key is set a report satisfies the
council gate" are both read as offers. The offer phrasings "equivalent",
"instead of", "in place of", "in lieu of", "substitute for" and "stand in for"
are not excused by a negator, because the offer is in the words themselves. No
subject is exempt: a sentence saying the council record satisfies the council
gate matches too, and the surfaces say "only the council record does" instead.

D3 reads the bare family names by their capitalised form only (`(?-i:)`), because
`sonnet`, `haiku`, `opus` and `gemini` are also ordinary lowercase words. A bare
provider name (Claude, Anthropic, OpenAI, Google) is not read, because a council
surface legitimately names the three providers.

Remaining limits, stated rather than hidden. The scan is a vocabulary check, not
a proof that no surface offers an alternative. (1) A council word and the offer in
different windows pair nothing: a split across two sentences, two list items or a
heading and its body ("Convene the council. Use five reviewers instead.") passes.
(2) A constant or identifier built at runtime, or a string assembled from parts, is
not read; the scan reads text. (3) A paraphrase outside the vocabulary passes
("swap the council for a panel", "a reviewer panel is just as good"); each new
phrasing is a new alternative in the pattern, found by review. (4) The scan reads
shipped files only, `crux/` templates, skills, agents and box files, and the
dev-only opencode projection; it says nothing about a book, a record, an ADR or a
journal in a consuming project. (5) A negator within four words before the verb
excuses a verb form even when it negates something else ("a report that is not
late satisfies the council gate" reads as negated). Prose cannot enforce the council
gate: the execution boundary does, and this file only keeps the shipped wording
from contradicting it.

Reads `crux/` (which ships) unguarded. `opencode/agents/` is a dev-only
projection, read under `require_dev_surface`. Stdlib plus PyYAML.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from ._dev_surface import IS_STAGED_ARTIFACT, REPO_ROOT, require_dev_surface
except ImportError:  # run as a discovered top-level module
    from _dev_surface import IS_STAGED_ARTIFACT, REPO_ROOT, require_dev_surface

try:
    from .test_council_schemas import CORRECTED
except ImportError:
    from test_council_schemas import CORRECTED

import yaml

SCRIPTS = REPO_ROOT / "crux" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import council_alternative as ca  # after the sys.path insert
import council_gate as cg

CRUX = REPO_ROOT / "crux"
TEMPLATES = CRUX / "templates"
SKILLS = CRUX / "skills"
AGENTS = CRUX / "agents"
BOX = CRUX / "box"
CATALOG = CRUX / "catalog"
ROUTER_CONFIG = SCRIPTS / "crux" / "_config" / "llm_router_config.json"
OPENCODE_AGENTS = REPO_ROOT / "opencode" / "agents"
README = REPO_ROOT / "README.md"

TEMPLATE_SUFFIXES = {".yaml", ".yml", ".md", ".tmpl"}
CYCLE_TEMPLATES = (
    "cycle-module-adr.yaml",
    "cycle-module-verify.yaml",
    "cycle-module-review.yaml",
    "cycle-promptbook-template.yaml",
    "iterate-promptbook-template.yaml",
    "patch-promptbook-template.yaml",
)

# The sentence naming the two processes; every surface that names either may
# carry it once, so it must pass every detector.
NAMING_BLOCK = (
    "Council deliberation runs only through the council runner, and its council "
    "record is the only evidence a council gate accepts. Independent review is a "
    "reviewer's examination, and its reviewer report is the only evidence an "
    "independent-review gate accepts. Neither satisfies the other's gate."
)


# ----------------------------------------------------------------- surfaces

def shipped_surfaces() -> list[Path]:
    """Every file the plugin ships that the council scan reads."""
    files: list[Path] = []
    files += [p for p in TEMPLATES.rglob("*") if p.is_file() and p.suffix in TEMPLATE_SUFFIXES]
    files += [p for p in SKILLS.rglob("*.md") if p.is_file()]
    files += [p for p in AGENTS.glob("*.md") if p.is_file()]
    files += [p for p in BOX.glob("*.md") if p.is_file()]
    return sorted(files)


def rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:  # a copy under a temp tree
        return path.as_posix()


# ----------------------------------------------------------- model tokens

def registry_model_tokens(config: dict) -> set[str]:
    """Every model name the router registry knows, lowercased.

    The `models` keys, the `retired_models`, every `api_string` and the part of
    it after the provider prefix.
    """
    tokens: set[str] = set()
    for key, spec in (config.get("models") or {}).items():
        tokens.add(key.lower())
        api = (spec or {}).get("api_string")
        if api:
            tokens.add(api.lower())
            tokens.add(api.split("/", 1)[-1].lower())
    for key in config.get("retired_models") or []:
        tokens.add(str(key).lower())
    return tokens


def _load_registry_tokens() -> set[str]:
    return registry_model_tokens(json.loads(ROUTER_CONFIG.read_text(encoding="utf-8")))


def model_token_regex(tokens: set[str]) -> re.Pattern[str]:
    # Longest first so a key is not shadowed by its own prefix; the lookarounds
    # keep `gpt-6.1-sol` from matching inside a longer identifier.
    alts = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
    registry = rf"(?<![\w.-])(?:{alts})(?![\w-]|\.\d)"
    display = (
        r"\bGemini\s*\d|\bGPT[- ]\d|\bClaude\s+(?:Opus|Sonnet|Haiku|Fable)\b"
        r"|\b(?:Opus|Sonnet|Haiku)\s*\d"
        # The bare family name, capitalised as a display name. Case-sensitive on purpose:
        # `sonnet`, `haiku`, `opus` and `gemini` are ordinary lowercase words.
        r"|(?-i:\b(?:Opus|Sonnet|Haiku|GPT|Gemini)\b)"
    )
    return re.compile(rf"{registry}|{display}", re.IGNORECASE)


MODEL_TOKEN_RE = model_token_regex(_load_registry_tokens())

COUNCIL_WORD_RE = re.compile(r"\b(?:council|seats?)\b", re.IGNORECASE)

# An offer of something other than the council, or a claim that it equals the council.
# These phrasings carry the offer in the words themselves, so a negator in the same
# clause does not excuse them ("do not wait, run agents instead of a council").
_DET = r"(?:the|an?|any|every|this|that|its|their)"
_APOS_S = r"(?:['’]s)?"
EQUIVALENCE_RE = re.compile(
    r"\bequivalent(?:ly)?\b"
    r"|\b(?:instead\s+of|in\s+place\s+of)\s+(?:\w+ing\s+)?(?:" + _DET + r"\s+)?(?:[\w-]+\s+)?council\b"
    r"|\bin\s+lieu\s+of\b"
    r"|\bsubstitut\w*\s+for\s+(?:" + _DET + r"\s+)?council\b"
    r"|\bstand(?:s|ing)?\s+in\s+for\s+(?:" + _DET + r"\s+)?council\b",
    re.IGNORECASE,
)

# A verb that says something equals, replaces or passes as the council. The negated
# form is the corrected wording ("never satisfies", "is not the same as"), so a negator
# just before the verb, in the same clause, excuses it; see `_negated_before`.
# The slot after `satisfy` refuses a leading negator ("satisfies no council gate").
_COUNCIL_NOUN = r"(?!(?:no|none)\b)(?:[\w'’-]+\s+){0,3}?council"
VERB_EQUIVALENCE_RE = re.compile(
    r"\breplac(?:e|es|ed|ing)\s+(?:" + _DET + r"\s+)?council\b"
    r"|\bsame\s+as\s+(?:" + _DET + r"\s+)?council\b"
    r"|\bcount(?:s|ed|ing)?\s+as\s+(?:" + _DET + r"\s+)?council" + _APOS_S + r"\s+(?:verdict|vote|opinion)\b"
    r"|\bsatisf(?:y|ies|ied|ying)\s+" + _COUNCIL_NOUN + _APOS_S + r"[\s-]+gate\b",
    re.IGNORECASE,
)
# "Serve as", "act as" or "run as" the council: the agent is cast as the council. The
# slot after `council` refuses the council's own parts ("as the council runner").
# The detector asks for an agent, reviewer or subagent word in the same sentence.
AS_COUNCIL_RE = re.compile(
    r"\b(?:serv(?:e|es|ed|ing)|act(?:s|ed|ing)?|work(?:s|ed|ing)?|function(?:s|ed|ing)?"
    r"|run(?:s|ning)?|dispatch(?:es|ed|ing)?|use[sd]?|using)\b[^.;:]*?"
    r"\bas\s+" + _DET + r"\s+council\b(?![\s-]+(?:runner|record|gate|router|registry|seat|script|driver))",
    re.IGNORECASE,
)
_AGENT_WORD_RE = re.compile(r"\b(?:agents?|reviewers?|subagents?)\b", re.IGNORECASE)
# After a negator, "fail to" cancels it: "never fails to satisfy" is an affirmation.
_FAIL_RE = re.compile(r"^fail(?:s|ed|ing)?$", re.IGNORECASE)
_NEGATOR_RE = re.compile(
    r"^(?:never|no|not|cannot|neither|nor|none|nothing|nobody|without)$|n['’]t$",
    re.IGNORECASE,
)
# A clause ends at punctuation, a contrast word, a coordinator or a subordinator; a
# negator does not reach past one.
_CLAUSE_BREAK_RE = re.compile(
    r"[;:,]|\s[—–-]{1,2}\s"
    r"|\b(?:but|yet|although|however|and|or|so|then|if|when|whenever|unless|while)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[\w'’-]+")
#: A negator excuses a verb form only within this many words before the verb.
NEGATOR_REACH = 4


def _clause_before(sentence: str, start: int) -> str:
    """The text of the clause that holds position `start`, up to `start`."""
    clause_start = 0
    for brk in _CLAUSE_BREAK_RE.finditer(sentence, 0, start):
        clause_start = brk.end()
    return sentence[clause_start:start]


def _negated_before(sentence: str, start: int) -> bool:
    """A negator among the last `NEGATOR_REACH` words of the clause before `start`."""
    words = _WORD_RE.findall(_clause_before(sentence, start))[-NEGATOR_REACH:]
    negators = [i for i, w in enumerate(words) if _NEGATOR_RE.search(w)]
    if not negators:
        return False
    # "never fail(s) to satisfy": a `fail` after the last negator cancels it.
    return not any(_FAIL_RE.match(w) for w in words[negators[-1] + 1:])


def has_equivalence(sentence: str) -> bool:
    if EQUIVALENCE_RE.search(sentence):
        return True
    if any(not _negated_before(sentence, m.start())
           for m in VERB_EQUIVALENCE_RE.finditer(sentence)):
        return True
    if _AGENT_WORD_RE.search(sentence):
        return any(not _negated_before(sentence, m.start())
                   for m in AS_COUNCIL_RE.finditer(sentence))
    return False


# --------------------------------------------------------- sentence windows

_BLOCK_START_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\||#)")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def sentence_windows(text: str) -> list[tuple[int, str]]:
    """`(first line, sentence)` windows of `text`; see the module docstring."""
    windows: list[tuple[int, str]] = []
    block: list[str] = []
    block_start = 1
    in_fence = False

    def flush() -> None:
        if block:
            flat = ca.normalize("\n".join(block))
            for sentence in _SENTENCE_SPLIT_RE.split(flat):
                if sentence:
                    windows.append((block_start, sentence))
        block.clear()

    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(line):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            flat = ca.normalize(line)
            if flat:
                windows.append((number, flat))
            continue
        if not line.strip():
            flush()
            continue
        if _BLOCK_START_RE.match(line):
            flush()
        if not block:
            block_start = number
        block.append(line)
    flush()
    return windows


def strip_catalog_governed_lines(text: str) -> str:
    """Blank the frontmatter `model:` and `effort:` lines; the agent catalog governs them.

    Line numbers are preserved by blanking rather than deleting.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            break
        if re.match(r"\s*(?:model|effort)\s*:", lines[i]):
            lines[i] = ""
    return "\n".join(lines)


# ---------------------------------------------------------------- detectors
# Each detector is a pure function over text and returns its hits as
# `(line, detail)`; no hits means clean.

def _normalize_with_lines(text: str) -> tuple[str, list[int]]:
    """`ca.normalize(text)` and, per normalized character, its 1-based raw line."""
    chars: list[str] = []
    lines: list[int] = []
    line = 1
    for ch in text:
        if ch == "`":
            pass
        elif ch.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                lines.append(line)
        else:
            chars.append(ch)
            lines.append(line)
        if ch == "\n":
            line += 1
    while chars and chars[-1] == " ":
        chars.pop()
        lines.pop()
    return "".join(chars), lines


def detect_d1(text: str) -> list[tuple[int, str]]:
    """`(first line of the match, pattern id)` per withdrawn offer the text carries."""
    flat, lines = _normalize_with_lines(text)
    assert flat == ca.normalize(text), "the line map must agree with the owner's normalizer"
    hits: list[tuple[int, str]] = []
    for pattern in ca.WITHDRAWN_ALTERNATIVE_PATTERNS:
        match = pattern.pattern.search(flat)
        if match:
            hits.append((lines[match.start()], pattern.id))
    return hits


def detect_d2(text: str) -> list[tuple[int, str]]:
    return [
        (line, sentence[:140])
        for line, sentence in sentence_windows(text)
        if COUNCIL_WORD_RE.search(sentence) and has_equivalence(sentence)
    ]


def detect_d3(text: str) -> list[tuple[int, str]]:
    return [
        (line, sentence[:140])
        for line, sentence in sentence_windows(strip_catalog_governed_lines(text))
        if COUNCIL_WORD_RE.search(sentence) and MODEL_TOKEN_RE.search(sentence)
    ]


_FIVE_SEATS_RE = re.compile(
    r"\b(?:five|5)\s+(?:[\w-]+\s+){0,2}?(?:seats?|agents?|reviewers?|subagents?)\b", re.IGNORECASE
)
_ONE_DIMENSION_RE = re.compile(
    r"\bone\s+dimension\b|\bone\s+per\s+dimension\b|\bone\s+for\s+each\s+dimension\b"
    r"|\ba\s+dimension\s+(?:each|apiece)\b"
    r"|\b(?:single|own|separate|different)\s+dimension\b",
    re.IGNORECASE,
)
# A seat per dimension is the split itself, so it needs no count.
_SEAT_PER_DIMENSION_RE = re.compile(
    r"\bone\s+(?:seat|agent|subagent|reviewer)s?\s+(?:per|for\s+each)\s+dimension\b"
    r"|\b(?:five|5)\s+dimensions\s+across\s+(?:five|5)\s+(?:seats|agents|reviewers|subagents)\b",
    re.IGNORECASE,
)
_FIVE_AGENT_CALLS_RE = re.compile(
    r"\b(?:five|5)\s+(?:parallel\s+|native\s+)?(?:Agent|subagent)s?(?:\s+tool)?"
    r"(?:\s+(?:calls?|invocations?|dispatch(?:es)?)|\s+in\s+parallel)\b",
    re.IGNORECASE,
)


def detect_d6(text: str) -> list[tuple[int, str]]:
    """Five seats, agents or reviewers split one dimension each; or five Agent calls beside a council word.

    The five evaluation dimensions do not imply five seats. Every seat assesses every dimension."""
    return [
        (line, sentence[:140])
        for line, sentence in sentence_windows(text)
        if (_FIVE_SEATS_RE.search(sentence) and _ONE_DIMENSION_RE.search(sentence))
        or _SEAT_PER_DIMENSION_RE.search(sentence)
        or (_FIVE_AGENT_CALLS_RE.search(sentence) and COUNCIL_WORD_RE.search(sentence))
    ]


def council_exports() -> tuple[str, ...]:
    """The `__all__` of `crux/council/__init__.py`, read with `ast`; never imported."""
    tree = ast.parse((SCRIPTS / "crux" / "council" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            return tuple(
                e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
            )
    return ()


COUNCIL_EXPORTS = council_exports()
_COUNCIL_CALL_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in sorted(COUNCIL_EXPORTS, key=len, reverse=True)) + r")\s*\("
    if COUNCIL_EXPORTS
    else r"(?!x)x"
)
_SEAT_KEYWORD_RE = re.compile(r"\b(?:models|arbiter)\s*=(?!=)")
_OPENERS = "([{"
_CLOSERS = ")]}"


def fenced_blocks(text: str) -> list[tuple[int, str]]:
    """`(first line of the block body, body)` for each fenced block; an unclosed fence runs to the end."""
    blocks: list[tuple[int, str]] = []
    body: list[str] = []
    start = 0
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(line):
            if in_fence:
                blocks.append((start, "\n".join(body)))
                body = []
            else:
                start = number + 1
            in_fence = not in_fence
        elif in_fence:
            body.append(line)
    if in_fence:
        blocks.append((start, "\n".join(body)))
    return blocks


def _span_end(code: str, begin: int, *, stop_at_comma: bool) -> int:
    """Index where the span starting at `begin` ends; strings are skipped.

    A call span ends at the close that balances its opening parenthesis, which the
    caller has consumed. A value span ends at a depth-0 comma, a depth-0 newline once
    the value has started, or the close bracket that encloses it."""
    depth = 0
    quote = ""
    started = False
    i = begin
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            started = True
        elif ch in _OPENERS:
            depth += 1
            started = True
        elif ch in _CLOSERS:
            if depth == 0:
                return i
            depth -= 1
        elif stop_at_comma and depth == 0 and (ch == "," or (ch == "\n" and started)):
            return i
        elif not ch.isspace():
            started = True
        i += 1
    return len(code)


def detect_d3b(text: str) -> list[tuple[int, str]]:
    """Council calls in fenced code that name a model token; see the module docstring."""
    hits: list[tuple[int, str]] = []
    for first, code in fenced_blocks(text):
        for match in _COUNCIL_CALL_RE.finditer(code):
            span = code[match.end():_span_end(code, match.end(), stop_at_comma=False)]
            if MODEL_TOKEN_RE.search(span):
                line = first + code.count("\n", 0, match.start())
                hits.append((line, f"{match.group(1)}( names a model"))
        for match in _SEAT_KEYWORD_RE.finditer(code):
            span = code[match.end():_span_end(code, match.end(), stop_at_comma=True)]
            if MODEL_TOKEN_RE.search(span):
                line = first + code.count("\n", 0, match.start())
                hits.append((line, f"{match.group(0).rstrip('= ')}= names a model"))
    return sorted(hits)


_PROMPT_TEXT_FIELDS = ("prompt", "purpose", "expected_output", "side_effects")


def template_prompts(doc) -> list[dict]:
    """The prompt elements of a module list or a book's `prompts` list."""
    if isinstance(doc, dict):
        doc = doc.get("prompts")
    return [p for p in (doc or []) if isinstance(p, dict)]


def _fields_text(prompt: dict, fields=_PROMPT_TEXT_FIELDS) -> str:
    return "\n".join(str(prompt.get(f) or "") for f in fields)


def detect_d4(doc) -> list[tuple[int, str]]:
    """adr-module prompts that mention srde; the detail is the prompt title."""
    return [
        (0, str(p.get("title")))
        for p in template_prompts(doc)
        if str(p.get("module_tag") or "").startswith("adr-")
        and re.search(r"\bsrde\b", _fields_text(p), re.IGNORECASE)
    ]


def gate_prompts(doc) -> list[tuple[str, dict]]:
    """`(kind, prompt)` for each council-gate and review-gate prompt of a template.

    The classification is `council_gate.classify`, the function the execution
    boundary uses, so this scan and the gate agree on which prompt is a gate. A
    template carries no prompt numbers, so each prompt is numbered by position.
    A document with `cycle_kind` is classified under it (patch by phase); a
    module list, which carries none, is classified by each prompt's own tag.
    """
    prompts = template_prompts(doc)
    book = {"prompts": [{**p, "n": i} for i, p in enumerate(prompts, start=1)]}
    if isinstance(doc, dict) and doc.get("cycle_kind") is not None:
        book["cycle_kind"] = doc["cycle_kind"]
        start = cg.StartFields.of(book)
    else:
        start = cg.StartFields(False)
    kinds = {"council": "council", "independent-review": "review"}
    found: list[tuple[str, dict]] = []
    for i, p in enumerate(prompts, start=1):
        gate = cg.classify(book, i, start)
        if gate.cls in kinds:
            found.append((kinds[gate.cls], p))
    return found


GATE_COMMAND = {"council": "run-council.py", "review": "write-review-report.py"}


def detect_d5(doc) -> list[tuple[int, str]]:
    """Gate prompts that omit the command that produces their gate's evidence."""
    return [
        (0, f"{kind} gate prompt '{p.get('title')}' omits {GATE_COMMAND[kind]}")
        for kind, p in gate_prompts(doc)
        if GATE_COMMAND[kind] not in _fields_text(p)
    ]


def _yaml_doc(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ------------------------------------------------ the file-reading paths

def _label(detector) -> str:
    return detector.__name__.removeprefix("detect_").upper()


def scan_text_detectors(paths: list[Path], detector) -> list[str]:
    """One `path:line detector detail` entry per hit of a text detector."""
    hits: list[str] = []
    for path in paths:
        for line, detail in detector(path.read_text(encoding="utf-8")):
            hits.append(f"{rel(path)}:{line} {_label(detector)} {detail}")
    return hits


def scan_yaml_detectors(paths: list[Path], detector) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if path.suffix not in (".yaml", ".yml"):
            continue
        for _line, detail in detector(_yaml_doc(path)):
            hits.append(f"{rel(path)} {_label(detector)} {detail}")
    return hits


def _report(hits: list[str]) -> str:
    files = sorted({h.split(" ", 1)[0].split(":", 1)[0] for h in hits})
    return f"{len(hits)} hit(s) in {len(files)} file(s):\n  " + "\n  ".join(hits)


# ------------------------------------------------------------- the tests

class SurfaceTests(unittest.TestCase):
    """RED until every shipped surface is corrected; one method per detector."""

    @classmethod
    def setUpClass(cls):
        cls.surfaces = shipped_surfaces()

    def test_d1_no_withdrawn_council_alternative(self):
        hits = scan_text_detectors(self.surfaces, detect_d1)
        self.assertEqual(hits, [], _report(hits))

    def test_d2_no_council_equivalence(self):
        hits = scan_text_detectors(self.surfaces, detect_d2)
        self.assertEqual(hits, [], _report(hits))

    def test_d3_no_model_named_next_to_the_council(self):
        hits = scan_text_detectors(self.surfaces, detect_d3)
        self.assertEqual(hits, [], _report(hits))

    def test_d3b_no_model_named_in_a_council_call_in_a_code_fence(self):
        hits = scan_text_detectors(self.surfaces, detect_d3b)
        self.assertEqual(hits, [], _report(hits))

    def test_d6_no_five_seats_split_by_dimension(self):
        hits = scan_text_detectors(self.surfaces, detect_d6)
        self.assertEqual(hits, [], _report(hits))

    def test_d4_no_srde_in_an_adr_module_prompt(self):
        hits = scan_yaml_detectors(self.surfaces, detect_d4)
        self.assertEqual(hits, [], _report(hits))

    def test_d5_every_gate_prompt_names_its_command(self):
        hits = scan_yaml_detectors(self.surfaces, detect_d5)
        self.assertEqual(hits, [], _report(hits))

    def test_the_readme_passes_the_text_detectors(self):
        if IS_STAGED_ARTIFACT:
            # The staged README.md is the public rendition (public/README.md), regenerated at
            # release; the scan gates the dev-repo source, never generated release text.
            self.skipTest("README.md in the staged artifact is the public rendition")
        require_dev_surface(self, README, "README.md")
        hits = []
        for detector in (detect_d1, detect_d2, detect_d3, detect_d3b, detect_d6):
            hits += scan_text_detectors([README], detector)
        self.assertEqual(hits, [], _report(hits))

    def test_the_dev_only_opencode_projection_passes_the_text_detectors(self):
        require_dev_surface(self, OPENCODE_AGENTS, "opencode/agents")
        paths = sorted(OPENCODE_AGENTS.glob("*.md"))
        self.assertTrue(paths, "opencode/agents holds no projection to scan")
        hits = []
        for detector in (detect_d1, detect_d2, detect_d3, detect_d6):
            hits += scan_text_detectors(paths, detector)
        self.assertEqual(hits, [], _report(hits))


class CoverageTests(unittest.TestCase):
    """The scan reads a population derived from disk, not a literal."""

    def test_every_skill_is_scanned(self):
        scanned = {p.parent.name for p in shipped_surfaces() if p.name == "SKILL.md" and p.parent.parent == SKILLS}
        catalog = json.loads((CATALOG / "skills.json").read_text(encoding="utf-8"))
        names = {e["name"] for e in catalog}
        self.assertGreaterEqual(len(names), 1)
        self.assertEqual(scanned, names)

    def test_every_agent_is_scanned(self):
        scanned = {p.stem for p in shipped_surfaces() if p.parent == AGENTS}
        catalog = json.loads((CATALOG / "agents.json").read_text(encoding="utf-8"))
        names = {e["name"] for e in catalog}
        self.assertGreaterEqual(len(names), 1)
        self.assertEqual(scanned, names)

    def test_the_council_exports_d3b_reads_are_read_from_the_package(self):
        self.assertTrue(COUNCIL_EXPORTS)
        for name in ("council_vote", "get_opinion", "AsyncCouncilConfig"):
            self.assertIn(name, COUNCIL_EXPORTS)

    def test_every_box_file_and_template_is_scanned(self):
        scanned = set(shipped_surfaces())
        self.assertTrue(list(BOX.glob("*.md")))
        self.assertTrue(set(BOX.glob("*.md")) <= scanned)
        for name in CYCLE_TEMPLATES:
            with self.subTest(template=name):
                self.assertIn(TEMPLATES / name, scanned)

    def test_d5_finds_the_gate_prompts_of_each_cycle_template(self):
        for name in CYCLE_TEMPLATES:
            with self.subTest(template=name):
                kinds = {k for k, _p in gate_prompts(_yaml_doc(TEMPLATES / name))}
                self.assertTrue(kinds, f"{name} yields no gate prompt, so D5 would scan nothing")

    def test_d4_finds_adr_module_prompts(self):
        adr = template_prompts(_yaml_doc(TEMPLATES / "cycle-module-adr.yaml"))
        self.assertTrue([p for p in adr if str(p.get("module_tag")).startswith("adr-")])

    def test_the_registry_token_set_carries_the_google_top_model(self):
        config = json.loads(ROUTER_CONFIG.read_text(encoding="utf-8"))
        tokens = registry_model_tokens(config)
        self.assertTrue(tokens)
        self.assertIn(config["model_roles"]["google_top"].lower(), tokens)
        self.assertTrue(MODEL_TOKEN_RE.search(config["model_roles"]["google_top"]))


class DetectorControlTests(unittest.TestCase):
    """Positive controls: a seeded violation turns each detector red."""

    def test_d1_matches_a_withdrawn_offer(self):
        text = "Convene the council, or dispatch 5 parallel review agents instead."
        self.assertEqual([p for _, p in detect_d1(text)], ["native-agents-alternative"])

    def test_d1_reports_the_first_line_of_the_match_across_a_wrapped_offer(self):
        text = "Intro.\n\nConvene the council, or dispatch 5 parallel\nreview agents instead.\n"
        hits = detect_d1(text)
        self.assertEqual(hits[0][0], 3)

    def test_d2_matches_equivalence_vocabulary_beside_a_council_word(self):
        for text in (
            "Five reviewer agents are equivalent to the council.",
            "Dispatch reviewers in place of the council.",
            "A fallback may substitute for the council here.",
            "The reviewer stands in for the council.",
            "A seat is equivalently filled by a native agent.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d2(text))

    def test_d2_ignores_equivalence_without_a_council_word(self):
        self.assertEqual(detect_d2("The two values are equivalent."), [])

    def test_d2_matches_each_replacement_phrasing(self):
        for text in (
            "Use five reviewer agents instead of a council.",
            "Dispatch a reviewer instead of the council.",
            "Five reviewers replace the council.",
            "The reviewer replaces a council here.",
            "A native agent replaced the council.",
            "Run native agents in lieu of the council.",
            "In lieu of convening, dispatch five agents; the council is optional.",
            "The reviewers work the same as a council.",
            "This is the same as the council would produce.",
            "The five reports count as the council verdict.",
            "One reviewer report counts as a council vote.",
            "Five reviewer reports satisfy the council gate.",
            "A native agent satisfies a promptbook council gate.",
            "A native agent and the reviewer satisfies every council gate.",
            "Dispatch reviewers in place of a council.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d2(text))

    def test_d2_ignores_the_negated_verb_forms_and_the_naming_wording(self):
        for text in (
            "A reviewer report never satisfies the council gate.",
            "A reviewer report, filed by an agent, never satisfies a council gate.",
            "No reviewer report satisfies the council gate.",
            "A reviewer report cannot satisfy the council gate.",
            "A reviewer report does not satisfy the council gate.",
            "A reviewer report doesn't satisfy the council gate.",
            "A reviewer report doesn’t satisfy the council gate.",
            "Neither report satisfies the council gate.",
            "No reviewer replaces the council.",
            "A reviewer never replaces the council.",
            "A reviewer report is not the same as a council record.",
            "No native agent counts as a council vote.",
            "A reviewer report never counts as the council verdict.",
            "A reviewer report does not and cannot satisfy the council gate.",
            "Nothing a reviewer writes satisfies the council gate.",
            # verbatim from shipped surfaces
            "Neither satisfies the other's gate.",
            "A council record never satisfies this gate.",
            "A reviewer report never satisfies a council gate; only the council runner's council record does.",
            "It satisfies no promptbook council gate.",
            "The async driver under Step 4 satisfies no gate.",
            "A reviewer report never satisfies that gate.",
            "No reviewer subagent casts a seat's vote.",
            "A reviewer fallback never satisfies the gate.",
            NAMING_BLOCK,
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_d2(text), [])

    def test_d2_a_negator_does_not_excuse_an_offer_in_another_clause(self):
        for text in (
            "A reviewer never blocks; the report satisfies the council gate.",
            "The reviewer never runs the council, but the report satisfies the council gate.",
            "Do not convene the council: the reviewers replace the council.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d2(text))

    def test_d2_a_preposition_offer_is_not_excused_by_a_negator(self):
        self.assertTrue(detect_d2("Do not convene a council and use agents instead of a council."))
        self.assertTrue(detect_d2("Do not wait and run native agents in lieu of the council."))

    def test_d2_a_conditional_naming_the_runner_excuses_nothing(self):
        for text in (
            "When the runner is down a fallback model satisfies the council gate.",
            "If the runner fails a single call-llm answer satisfies the council gate.",
            "If run-council errors the dev-lead's judgment satisfies the council gate.",
            "The reviewer report and the council record satisfy the council gate.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d2(text))

    def test_d2_a_negator_far_from_the_verb_or_in_a_joined_clause_excuses_nothing(self):
        for text in (
            "Do not convene the council and let five reviewer reports replace the council.",
            "Do not wait for the runner and let the reviewer report satisfy the council gate.",
            "When the network is not reachable five native agents replace the council.",
            "If no API key is set a reviewer report satisfies the council gate.",
            "Without a key a reviewer report satisfies the council gate.",
            "No one doubts the report satisfies the council gate.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d2(text))

    def test_d2_matches_serve_as_and_act_as_the_council_with_agents_nearby(self):
        for text in (
            "Dispatch five review agents as the council.",
            "Five reviewer subagents can serve as the council.",
            "A native agent acts as a council here.",
            "The reviewers served as the council.",
            "Run the reviewers as a council.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d2(text), text)

    def test_d2_a_double_negative_with_fail_is_affirmative(self):
        for text in (
            "Reports never fail to satisfy the council gate.",
            "A reviewer report does not fail to satisfy the council gate.",
            "Reviewer reports cannot fail to replace the council.",
            "Five reviewers never fail to count as the council verdict.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d2(text), text)

    def test_d2_serve_as_the_council_corrected_wording_stays_clean(self):
        for text in (
            "No reviewer subagent serves as the council.",
            "A native agent never acts as the council.",
            "A reviewer report cannot serve as a council.",
            "The council runner writes the record as the council runner does.",
            "Agents report to the lead as the council runner finishes.",
            "A reviewer writes the report as the reviewer of record.",
            "The reviewers never fail the council gate.",
            "A report that fails to satisfy the review gate is refused.",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_d2(text), [], text)

    def test_d2_matches_other_determiners_and_spellings(self):
        for text in (
            "A reviewer report satisfies this council gate.",
            "Five reviewer reports satisfy its council gate.",
            "Five native agents replace this council.",
            "A native agent satisfies the book's council gate.",
            "Five reports satisfy the council-gate.",
            "Five reports satisfy the council’s gate.",
            "The five reports count as the council’s verdict.",
            "Use reviewers instead of council deliberation.",
            "Dispatch five agents instead of convening the council.",
            "Reviewers replace council deliberation.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d2(text))

    def test_d2_the_cross_sentence_split_is_a_documented_limit(self):
        # The council word and the offer sit in different windows, so nothing pairs.
        self.assertEqual(detect_d2("Convene the council. Use five reviewers instead."), [])
        self.assertEqual(detect_d2("The council is convened.\n\nA native agent is equivalent to it."), [])

    def test_d2_a_near_negator_that_does_not_negate_the_offer_is_a_documented_limit(self):
        self.assertEqual(detect_d2("A report that is not late satisfies the council gate."), [])

    def test_d6_matches_five_seats_split_one_dimension_each(self):
        for text in (
            "Give each of the five seats one dimension.",
            "Assign five reviewers, one dimension each.",
            "Five subagents take one per dimension.",
            "Dispatch 5 native agents so each reviewer owns one dimension.",
            "Each of the five agents gets its own dimension.",
            "Five reviewer agents cover a single dimension apiece.",
            "Run five parallel subagents, one agent per dimension.",
            "Use five seats with one dimension per seat.",
            "Use five seats, one for each dimension.",
            "Give the five reviewers a dimension each.",
            "Dispatch one subagent per dimension for the council.",
            "Split the five dimensions across five seats.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d6(text), text)

    def test_d6_matches_five_agent_calls_beside_a_council_word(self):
        for text in (
            "The council is dispatching five Agent calls.",
            "Run five `Agent` calls for the council.",
            "Convene the council with five parallel Agent calls.",
            "Five Agent calls fill the five seats.",
            "Make five subagent calls as the council.",
            "The council fans out five Agents in parallel.",
            "The council dispatches 5 Agent tool calls.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d6(text), text)

    def test_d6_ignores_the_wording_that_keeps_dimensions_apart_from_seats(self):
        for text in (
            "Every seat assesses every dimension.",
            "The five dimensions do not imply five seats.",
            "Each seat assesses all five dimensions.",
            "Each reviewer examines one dimension.",
            "Dispatch five Agent calls to explore the repository.",
            "The council runs three seats.",
            "The five evaluation dimensions do not imply five seats; each seat reports one score per dimension.",
            NAMING_BLOCK,
            CORRECTED,
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_d6(text), [])

    def test_d6_the_cross_sentence_split_is_a_documented_limit(self):
        self.assertEqual(detect_d6("The council has five seats. Each takes one dimension."), [])
        self.assertEqual(detect_d6("Convene the council.\n\nRun five Agent calls."), [])

    def test_d3_matches_a_model_name_beside_a_council_word(self):
        for text in (
            "The council runs gemini-3.1-pro-preview as one seat.",
            "The council runs gpt-6-astra.",
            "The council seats claude-opus-5.5-xhigh.",
            "The council uses Gemini 3 and GPT-6.",
            "The council includes Claude Opus.",
            "Each seat uses Sonnet 5.",
            "The council includes openai/gpt-6-astra.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d3(text))

    def test_d3_matches_each_bare_display_name_beside_a_council_word(self):
        for text in (
            "The council seats Opus, one per provider.",
            "Each council seat runs Sonnet.",
            "The council has a Haiku seat.",
            "The council seats GPT and Gemini Pro.",
            "The council includes Gemini.",
            "Opus holds the first seat.",
            "A seat for GPT.",
            "The council (Opus, GPT, Gemini) votes.",
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d3(text))

    def test_d3_ignores_a_bare_display_name_without_a_council_word(self):
        for text in (
            "The reviewer runs on Opus.",
            "Gemini and GPT are model families.",
            "A Haiku and a Sonnet are poems.",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_d3(text), [])

    def test_d3_reads_the_bare_display_name_by_its_capitalised_form_only(self):
        # Lowercase `sonnet`, `haiku`, `opus` and `gemini` are ordinary words, so only the
        # capitalised display form of a model family is read.
        for text in (
            "The council writes a sonnet.",
            "A seat reads the opus and a haiku.",
            "The council reads the gemini constellation.",
            "The council seats a gptq quantized build.",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_d3(text), [])

    def test_d3_ignores_registry_roles_and_a_model_without_a_council_word(self):
        self.assertEqual(detect_d3("The council seats run openai_top, anthropic_top and google_top."), [])
        self.assertEqual(detect_d3("The reviewer runs gemini-3.1-pro-preview."), [])
        self.assertEqual(detect_d3("A reviewer agent names the three providers."), [])

    def test_d3_ignores_a_registry_call_by_key_in_a_code_fence(self):
        text = (
            "Call the registry:\n\n```python\n"
            'cfg = get_model_config("claude-opus-5.5")\n'
            'council = get_default_models("council_default")\n'
            "```\n"
        )
        self.assertEqual(detect_d3(text), [])

    def test_d3_matches_a_comment_naming_both_inside_a_code_fence(self):
        text = "```python\n# the council runs claude-opus-5.5-xhigh\n```\n"
        self.assertTrue(detect_d3(text))

    def test_d3_matches_a_model_name_in_council_prose_across_a_wrapped_line(self):
        self.assertTrue(detect_d3("The council runs\nGemini 3.1 Pro on one seat.\n"))

    def test_d3b_matches_a_council_call_naming_a_model_in_a_code_fence(self):
        for text in (
            (
                "```python\ndecision = council_vote(\n    question=q,\n    context=c,\n"
                '    models=["gpt-6-astra"],\n)\n```\n'
            ),
            '```python\nget_opinion("claude-opus-5.5-xhigh", "q")\n```\n',
            '```python\nAsyncCouncilConfig(models=["gemini-3.1-pro-preview"])\n```\n',
            '```python\nx = council_vote(q, arbiter="claude-opus-5.5-xhigh")\n```\n',
            # the keyword rule alone: no council export call encloses it
            '```python\nconfig = dict(models=["gpt-6-astra"])\n```\n',
            '```python\narbiter = "claude-opus-5.5-xhigh"\n```\n',
        ):
            with self.subTest(text=text):
                self.assertTrue(detect_d3b(text))

    def test_d3b_reports_the_line_of_the_call_and_of_each_keyword(self):
        text = "```python\ncouncil_vote(\n    q,\n    models=[\"gpt-6-astra\"],\n)\n```\n"
        self.assertEqual([line for line, _ in detect_d3b(text)], [2, 4])

    def test_d3b_ignores_registry_calls_and_a_singular_model_keyword(self):
        for text in (
            '```python\ncfg = get_model_config("claude-opus-5.5")\n```\n',
            '```python\nm = get_default_models("council_default")\n```\n',
            '```python\ncall_model(model="claude-opus-5.5")\n```\n',
            '```python\nget_opinion(get_default_model("council_default"), "q")\n```\n',
            (
                '```python\nmodels = get_default_models("council_default")\n'
                'other = "claude-opus-5.5"\n```\n'
            ),
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_d3b(text), [])

    def test_d3b_reads_fenced_code_only(self):
        self.assertEqual(detect_d3b('Call get_opinion("claude-opus-5.5-xhigh", "q") here.'), [])

    def test_d3_strips_agent_frontmatter_model_and_effort(self):
        text = "---\nname: x\nmodel: claude-opus-5.5\neffort: high\n---\nThe council is convened.\n"
        self.assertEqual(detect_d3(text), [])
        text = "---\nname: x\ndescription: council claude-opus-5.5\n---\n"
        self.assertTrue(detect_d3(text))

    def test_d4_matches_srde_in_an_adr_prompt_only(self):
        doc = [{"title": "T", "module_tag": "adr-1", "prompt": "run the `srde` skill"}]
        self.assertEqual(detect_d4(doc), [(0, "T")])
        doc[0]["module_tag"] = "dev-1"
        self.assertEqual(detect_d4(doc), [])

    def test_d5_matches_a_gate_prompt_without_its_command(self):
        council = [
            {"title": "a", "module_tag": "adr-1", "prompt": "x"},
            {"title": "b", "module_tag": "adr-1", "prompt": "convene the council"},
        ]
        self.assertEqual(len(detect_d5(council)), 1)
        council[1]["prompt"] = "run run-council.py"
        self.assertEqual(detect_d5(council), [])
        review = [{"title": "r", "module_tag": "review-1", "prompt": "review"}]
        self.assertEqual(len(detect_d5(review)), 1)
        review[0]["prompt"] = "write-review-report.py"
        self.assertEqual(detect_d5(review), [])
        patch = [{"title": "v", "phase": "verify", "prompt": "x"}, {"title": "w", "phase": "review", "prompt": "y"}]
        self.assertEqual(len(detect_d5(patch)), 2)

    def test_d5_agrees_with_council_gate_on_a_patch_document_and_a_split_module(self):
        patch = {
            "cycle_kind": "patch",
            "prompts": [
                {"title": "v", "phase": "verify", "module_tag": "adr-1", "prompt": "x"},
                {"title": "w", "phase": "review", "prompt": "y"},
            ],
        }
        self.assertEqual([k for k, _p in gate_prompts(patch)], ["council", "review"])
        # the ordinal counts the contiguous run of a tag; a later run of the same tag restarts it
        doc = [
            {"title": "a", "module_tag": "adr-1", "prompt": "x"},
            {"title": "b", "module_tag": "adr-2", "prompt": "x"},
            {"title": "c", "module_tag": "adr-1", "prompt": "x"},
            {"title": "d", "module_tag": "adr-1", "prompt": "x"},
        ]
        self.assertEqual([p["title"] for _k, p in gate_prompts(doc)], ["d"])

    def test_d5_does_not_treat_the_other_ordinals_as_gates(self):
        doc = [{"title": str(i), "module_tag": "adr-1", "prompt": "x"} for i in range(4)]
        self.assertEqual([p["title"] for _k, p in gate_prompts(doc)], ["1"])

    def test_the_file_reading_paths_report_a_violation_injected_into_a_real_template(self):
        real = TEMPLATES / "cycle-module-adr.yaml"
        injected = {
            detect_d2: "# The council is equivalent to five reviewer agents.\n",
            detect_d3: "# The council runs gemini-3.1-pro-preview.\n",
            detect_d3b: '```python\nget_opinion("claude-opus-5.5-xhigh", "q")\n```\n',
            detect_d6: "# The council runs five Agent calls, one dimension each.\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "cycle-module-adr.yaml"
            for detector, line in injected.items():
                with self.subTest(detector=detector.__name__):
                    shutil.copyfile(real, copy)
                    clean = scan_text_detectors([copy], detector)
                    copy.write_text(copy.read_text(encoding="utf-8") + "\n" + line, encoding="utf-8")
                    hits = scan_text_detectors([copy], detector)
                    self.assertEqual(len(hits), len(clean) + 1, hits)
                    self.assertIn("cycle-module-adr.yaml:", hits[-1])
                    self.assertIn(_label(detector), hits[-1])
            # D4 and D5 read the parsed file. Make the copy clean first (the real
            # template may still be red), then break it and see both report it.
            doc = yaml.safe_load(real.read_text(encoding="utf-8"))
            for prompt in doc:
                for field in _PROMPT_TEXT_FIELDS:
                    prompt[field] = re.sub(r"srde", "the resolver", str(prompt.get(field) or ""), flags=re.IGNORECASE)
            doc[1]["prompt"] += "\nrun-council.py\n"
            copy.write_text(yaml.safe_dump(doc), encoding="utf-8")
            self.assertEqual(scan_yaml_detectors([copy], detect_d4), [])
            self.assertEqual(scan_yaml_detectors([copy], detect_d5), [])
            doc[0]["prompt"] += "\nThen run the srde skill.\n"
            doc[1]["prompt"] = doc[1]["prompt"].replace("run-council.py", "the runner")
            copy.write_text(yaml.safe_dump(doc), encoding="utf-8")
            d4 = scan_yaml_detectors([copy], detect_d4)
            d5 = scan_yaml_detectors([copy], detect_d5)
            self.assertEqual(len(d4), 1, d4)
            self.assertEqual(len(d5), 1, d5)
            self.assertIn("cycle-module-adr.yaml", d5[0])
            self.assertIn("D5", d5[0])

    def test_the_readme_scan_reads_the_file_and_flags_the_old_council_row(self):
        if IS_STAGED_ARTIFACT:
            # The staged README.md is the public rendition (public/README.md), regenerated at
            # release; the scan gates the dev-repo source, never generated release text.
            self.skipTest("README.md in the staged artifact is the public rendition")
        require_dev_surface(self, README, "README.md")
        old_row = (
            '| `council` | "ask the council" / choosing between approaches | '
            "Multi-model deliberation (Claude + Gemini + GPT). |\n"
        )
        self.assertEqual(scan_text_detectors([README], detect_d3), [])
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "README.md"
            copy.write_text(README.read_text(encoding="utf-8") + "\n" + old_row, encoding="utf-8")
            hits = scan_text_detectors([copy], detect_d3)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("README.md:", hits[0])
        self.assertIn("D3", hits[0])

    def test_the_naming_block_the_corrected_text_and_the_notice_pass_every_detector(self):
        for name, text in (
            ("naming block", NAMING_BLOCK),
            ("CORRECTED", CORRECTED),
            ("CORRECTION_NOTICE", ca.CORRECTION_NOTICE),
        ):
            for detector in (detect_d1, detect_d2, detect_d3, detect_d6):
                with self.subTest(text=name, detector=detector.__name__):
                    self.assertEqual(detector(text), [])


if __name__ == "__main__":
    unittest.main()
