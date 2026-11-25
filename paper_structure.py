"""Conservative source registry for digital CA papers with Part A/B layouts.

This is not release authority. Both readers must independently read the source,
accept its allocation boundaries, and return every registered maximum and choice.
Unsupported or ambiguous layouts use the general extractor instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PaperUnit:
    qid: str
    maximum: float
    source: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaperStructure:
    units: tuple[PaperUnit, ...]
    choice_instructions: tuple[str, ...]

    def prompt(self) -> str:
        return (
            "=== SOURCE ALLOCATION REGISTRY ===\n"
            "Use these stable IDs for the printed allocations. MCQs Q1, Q2, etc and "
            "descriptive Q1.a etc are distinct items when numbering restarts. "
            "A shared allocation covers the whole named part. '.remaining' labels "
            "a separately marked, unnumbered continuation; it is not a printed roman numeral.\n"
            "Independently read the full paper. These excerpts are navigation aids; "
            "the original paper determines allocation scope. Report any unresolved "
            "allocation in allocation_issues with its question id and specific reason. "
            "Return allocation_issues=[] when all allocations can be read. "
            "Return each maximum by reading its source, not by forcing the official total. "
            "Return the complete choice graph, including nested alternatives. "
            "Do not write descriptions, model answers, or repeat rows.\n\n"
            + "\n\n".join(f"{unit.qid}\n{unit.source}" for unit in self.units)
            + "\n\nChoice rule slots (read their full scope from the paper):\n"
            + "\n".join(f"G{i}: {source}" for i, source in enumerate(self.choice_instructions, 1))
        )


_TAG = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s+Marks?\s*\)", re.I)
_PART = re.compile(r"(?m)^[ \t]*\(([A-H](?:[12])?)\)[ \t]*")
_ROMAN = re.compile(r"(?m)^[ \t]*\(([ivx]+)\)[ \t]*")
_ROMAN_ORDER = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")


def ca_paper_structure(text: str) -> PaperStructure | None:
    """Recognize only fully enumerated, tagged descriptive parts and uniform MCQs."""
    headers = list(re.finditer(r"(?im)^[ \t]*Part[ \t]*[-–—]?[ \t]*([AB])[ \t]*$", text))
    if len(headers) != 2 or [m.group(1).upper() for m in headers] != ["A", "B"]:
        return None
    intro, mcq, descriptive = text[:headers[0].start()], text[headers[0].end():headers[1].start()], text[headers[1].end():]
    count_match = re.search(r"Part\s*[-–—]?\s*A\s+comprises\s+(\d+)\s+Multiple\s+Choice", intro, re.I)
    desc_match = re.search(r"Part\s*[-–—]?\s*B\s+comprises\s+(\d+)\s+questions\s+having\s+(\d+)\s+parts", intro, re.I)
    if not count_match or not desc_match:
        return None
    mcq_count, desc_count, part_count = int(count_match[1]), int(desc_match[1]), int(desc_match[2])
    if not (1 <= mcq_count <= 60 and 1 <= desc_count <= 20 and 1 <= part_count <= 8):
        return None
    cases = list(re.finditer(r"(?im)^[ \t]*\(Case\s+Scenario\s+(\d+)\s+carries\s+(\d+)\s+Marks?\)[ \t]*$", mcq))
    ranges = list(re.finditer(r"Directions\s*\(\s*(\d+)\s*[-–—]\s*(\d+)\s*\)", mcq, re.I))
    general = list(re.finditer(r"General\s+MCQs?\s*\(\s*(\d+)\s+MCQs?\s*[×x*]\s*(\d+)\s+Marks?\s+each\s*=\s*(\d+)\s+Marks?\s*\)", mcq, re.I))
    if not cases or len(cases) != len(ranges) or len(general) != 1 or _TAG.search(mcq):
        return None
    allocations = {}
    for case, span in zip(cases, ranges):
        first, last = int(span[1]), int(span[2])
        if last < first or first != len(allocations) + 1:
            return None
        per_item = int(case[2]) / (last - first + 1)
        if per_item <= 0 or per_item % .5:
            return None
        for number in range(first, last + 1):
            allocations[number] = (per_item, case[0].strip() + "\n" + span[0])
    count, per_item, total = map(int, general[0].groups())
    if count * per_item != total or len(allocations) + count != mcq_count:
        return None
    for number in range(len(allocations) + 1, mcq_count + 1):
        allocations[number] = (float(per_item), general[0][0])
    # Case narrative lists also use 1./2./3.; actual MCQs have option lines.
    mcq_heads = list(re.finditer(r"(?m)^[ \t]*(\d+)\.[ \t]*$", mcq))
    question_sources = {}
    for index, head in enumerate(mcq_heads):
        end = mcq_heads[index + 1].start() if index + 1 < len(mcq_heads) else len(mcq)
        block = mcq[head.end():end]
        # A case heading between numbered MCQs starts the NEXT shared narrative.
        # Do not label that narrative as part of the previous MCQ's answer scope.
        next_case = re.search(r"(?im)^[ \t]*(?:Case\s+Scenario\b|General\s+MCQs?\b|Directions\s*\()", block)
        if next_case:
            block = block[:next_case.start()]
        options = set(re.findall(r"(?m)^[ \t]*\(([1-4A-D])\)[ \t]+", block))
        if len(options) < 3:
            continue
        number = int(head[1])
        if number in question_sources or number not in allocations:
            return None
        question_sources[number] = block.strip()
    if set(question_sources) != set(allocations):
        return None
    units = [PaperUnit(f"Q{i}", allocations[i][0], allocations[i][1]
                       + f"\nMCQ {i}: " + question_sources[i]) for i in allocations]
    heads = [head for head in re.finditer(r"(?m)^[ \t]*(\d+)\.[ \t]*$", descriptive)
             if not re.search(r"\b\d{4}[-–—]\s*$", descriptive[:head.start()])]
    if [int(head[1]) for head in heads] != list(range(1, desc_count + 1)):
        return None
    consumed_tags = 0
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(descriptive)
        block = descriptive[head.end():end]
        parts = list(_PART.finditer(block))
        if sorted({part[1][0] for part in parts}) != list("ABCDEFGH"[:part_count]):
            return None
        if len({part[1] for part in parts}) != len(parts) or _TAG.search(block[:parts[0].start()]):
            return None
        for j, part in enumerate(parts):
            finish = parts[j + 1].start() if j + 1 < len(parts) else len(block)
            source = block[part.end():finish].strip()
            tags, romans = list(_TAG.finditer(source)), list(_ROMAN.finditer(source))
            if not tags:
                return None
            base = f"Q{head[1]}.{part[1].lower()}"
            consumed_tags += len(tags)
            if len(tags) == 1:
                units.append(PaperUnit(base, float(tags[0][1]), source))
                continue
            used = set()
            for k, tag in enumerate(tags):
                preceding = [roman for roman in romans if roman.start() < tag.start()]
                if not preceding:
                    return None
                roman = preceding[-1]
                suffix = roman[1]
                start = roman.start()
                aliases = ()
                if suffix in used:
                    start = tags[k - 1].end()
                    # A further tagged requirement is not the enclosing part's total.
                    if not re.match(r"\s*(?:Further|Additionally|In addition)\b", source[start:tag.start()], re.I):
                        return None
                    suffix = "remaining"
                    # Some readers number this unnumbered continuation as the
                    # next roman clause. Only alias it when the source has a
                    # contiguous numbered prefix and NO such printed numeral.
                    n = len(used)
                    if (0 < n < len(_ROMAN_ORDER)
                            and used == set(_ROMAN_ORDER[:n])
                            and _ROMAN_ORDER[n] not in {r[1] for r in romans}):
                        aliases = (f"{base}.{_ROMAN_ORDER[n]}",)
                if suffix in used:
                    return None
                used.add(suffix)
                units.append(PaperUnit(f"{base}.{suffix}", float(tag[1]),
                                       source[start:tag.end()].strip(), aliases))
    if consumed_tags != len(_TAG.findall(descriptive)):
        return None
    if len({unit.qid for unit in units}) != len(units):
        return None
    instructions = tuple(match[0].strip() for match in re.finditer(
        r"(?im)^[ \t]*(?:(?:Answer|Attempt|Choose|Solve)\s+any\s+[^\n]+|OR)[ \t]*$", descriptive))
    return PaperStructure(tuple(units), instructions)
