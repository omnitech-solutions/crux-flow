"""Classify caller-located changelog references; history grants no authority."""
from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter, defaultdict, deque
from datetime import date

from md_fences import closes_fence, fence_marker, split_lines

# Shared unchanged promotion grammar. The promoter normalizes the optional v
# before writing; historical qualification accepts only that emitted spelling.
SEMVER_RE = re.compile(r"^v?(\d+)\.\d+\.\d+(-[A-Za-z0-9.-]+)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_H2 = re.compile(r"^ {0,3}##(?:[ \t]+|$)")
_BRACKET = re.compile(r"^ {0,3}##[ \t]+\[([^\]]+)\]")
_DEFINITION = re.compile(r"^ {0,3}\[\^([^\]]+)\]:")


def _version(raw):
    return not raw.startswith('v') and SEMVER_RE.fullmatch(raw) is not None


def _release_scopes(lines):
    scopes, visible, counts = [], [], Counter()
    scope = None
    opener = None
    opened = None
    for index, line in enumerate(lines):
        marker = fence_marker(line)
        if opener is not None:
            visible.append(False)
            if closes_fence(marker, opener):
                opener = None
            scopes.append(scope)
            continue
        if marker is not None:
            opener, opened = marker[:2], index
            visible.append(False)
            scopes.append(scope)
            continue
        visible.append(True)
        if _H2.match(line):
            scope = None
            bracket = _BRACKET.match(line)
            if bracket and _version(bracket[1]):
                version = bracket[1]
                counts[version] += 1
                prefix = f'## [{version}] — '
                stamp = line[len(prefix):] if line.startswith(prefix) else ''
                if DATE_RE.fullmatch(stamp):
                    try:
                        date.fromisoformat(stamp)
                    except ValueError:
                        pass
                    else:
                        scope = (version, stamp)
        scopes.append(scope)
    scopes = [scope if scope and counts[scope[0]] == 1 else None for scope in scopes]
    if opener is not None:
        scopes[opened:] = [None] * (len(scopes) - opened)
    return scopes, visible


def _normalized_label(raw):
    return ' '.join(raw.split()).casefold()


def _labels(text, start=0):
    """Yield nonempty labels without rescanning an unmatched opener's suffix."""
    index, label_start = start, None
    while index < len(text):
        if label_start is None:
            if text.startswith('[^', index):
                label_start = index + 2
                index += 2
                continue
        elif text[index] == ']':
            if index > label_start:
                yield text[label_start:index]
            label_start = None
        index += 1


def _footnotes(lines, visible, scopes):
    definitions, uses = defaultdict(list), defaultdict(list)
    owners, uncertain = {}, set()
    active, separated, ambiguous = None, False, False
    for index, line in enumerate(lines):
        if not visible[index]:
            active = None
            continue
        definition = _DEFINITION.match(line)
        if definition:
            active = _normalized_label(definition[1])
            definitions[active].append(index)
            owners[index] = active
            separated, ambiguous = False, False
            body_start = definition.end()
        else:
            body_start = 0
            if not line.strip():
                separated = True
                ambiguous = False
                continue
            if active and len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(' ')) >= 4:
                owners[index] = active
                separated = False
            elif active or ambiguous:
                ambiguous = not separated and not re.match(r'^ {0,3}#', line)
                if ambiguous:
                    uncertain.add(index)
                active = None
        for use in _labels(line, body_start):
            uses[_normalized_label(use)].append(index)
    bad = {label for label, rows in definitions.items() if len(rows) != 1}
    dependencies = defaultdict(set)
    for label, positions in uses.items():
        for index in positions:
            if scopes[index] is None or index in uncertain:
                bad.add(label)
            if index in owners:
                dependencies[owners[index]].add(label)
    pending = deque(bad)
    while pending:
        for label in dependencies[pending.popleft()] - bad:
            bad.add(label)
            pending.append(label)
    return owners, uncertain, bad


def classify_changelog_references(text: str, occurrences: list[dict]) -> dict:
    """Partition exact caller token spans; never discover tokens or load proof.

    Offsets are Python string indexes, end exclusive. The caller owns canonical
    token extraction, bounded file reads and the whole-file raw hash inventory.
    """
    rows = []
    for occurrence in occurrences:
        if (not isinstance(occurrence, dict) or set(occurrence) != {'token', 'start', 'end'}
                or not isinstance(occurrence['token'], str)
                or type(occurrence['start']) is not int or type(occurrence['end']) is not int
                or not 0 <= occurrence['start'] < occurrence['end'] <= len(text)
                or text[occurrence['start']:occurrence['end']] != occurrence['token']):
            raise ValueError('changelog-reference-occurrence-refused')
        rows.append(dict(occurrence))
    rows.sort(key=lambda row: (row['start'], row['end'], row['token']))
    lines = split_lines(text)
    starts, cursor = [], 0
    for line in text.split('\n')[:len(lines)]:
        starts.append(cursor)
        cursor += len(line) + 1
    scopes, visible = _release_scopes(lines)
    owners, uncertain, bad = _footnotes(lines, visible, scopes)
    result = {'governing': [], 'historical': []}
    for row in rows:
        index = bisect_right(starts, row['start']) - 1
        end_index = bisect_right(starts, row['end'] - 1) - 1
        scope = scopes[index] if 0 <= index < len(scopes) else None
        if (scope is None or index != end_index or index in uncertain
                or owners.get(index) in bad):
            result['governing'].append(row)
        else:
            result['historical'].append(dict(row, reference_kind='dated-release',
                version=scope[0], date=scope[1], line=index + 1, authority='none'))
    return result
