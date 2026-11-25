"""Validate disjoint choices and choices wholly nested inside another option.

An outer option lists all of its leaves, including both sides of an inner OR.
The inner contribution replaces those leaves when computing the outer maximum.
Group labels and input order do not determine nesting or scoring.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


class ChoiceGraphError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass
class ChoiceForest:
    members: list[frozenset[str]]
    options: list[list[frozenset[str]]]
    parents: dict[int, int]
    children: dict[int, list[int]]
    roots: list[int]
    effective_total: float
    contributions: dict[int, float]


def build_choice_forest(rows, groups, normalize, *, check_contributions=True) -> ChoiceForest:
    """Validate nesting and derive maxima from leaves and selection counts.

    Only provider extraction uses check_contributions=False: its rules have no
    calculated totals yet. Persisted schemes and packages must still match the
    derived values, so changing a stored contribution never bypasses validation.
    """
    def fail(code, detail):
        raise ChoiceGraphError(code, detail)

    members, options = [], []
    for group in groups:
        if not 1 <= group.select_count < len(group.options):
            fail("invalid-choice-count", f"{group.group_id} has an invalid selection count.")
        if check_contributions:
            contribution = float(group.contribution_marks)
            if not math.isfinite(contribution) or contribution <= 0:
                fail("choice-contribution-mismatch", "A choice contribution must be positive and finite.")
        covered, group_options = set(), []
        for option in group.options:
            qids = [normalize(qid) for qid in option.question_ids]
            if not qids or any(not qid for qid in qids):
                fail("empty-choice-option", "Every choice option needs question ids.")
            if len(set(qids)) != len(qids):
                fail("duplicate-qid-in-choice-option", "A choice option repeats a question id.")
            if any(qid not in rows for qid in qids):
                fail("unknown-choice-question", "A choice option refers to an unknown question.")
            if covered.intersection(qids):
                fail("overlapping-choice-membership", "Alternatives in the same choice share questions.")
            covered.update(qids)
            group_options.append(frozenset(qids))
        members.append(frozenset(covered))
        options.append(group_options)

    containers = {i: [] for i in range(len(groups))}
    for i in range(len(groups)):
        for j in range(i):
            if not members[i].intersection(members[j]):
                continue
            child, parent = (i, j) if members[i] < members[j] else (j, i)
            if (not members[child] < members[parent]
                    or not any(members[child] <= option for option in options[parent])):
                fail("overlapping-choice-membership",
                     "Overlapping choice rules must be wholly nested inside one parent alternative.")
            containers[child].append(parent)
    parents = {child: min(values, key=lambda i: len(members[i]))
               for child, values in containers.items() if values}
    children = {i: [] for i in range(len(groups))}
    for child, parent in parents.items():
        children[parent].append(child)
    roots = [i for i in range(len(groups)) if i not in parents]

    # Children are strictly smaller; calculate them before their parents. Never
    # use a provider's or stored child's total as input to the parent's arithmetic.
    contributions = {}
    for i in sorted(range(len(groups)), key=lambda index: len(members[index])):
        totals = []
        for option in options[i]:
            fixed = set(option)
            nested_total = 0.0
            for child in children[i]:
                if members[child] <= option:
                    fixed.difference_update(members[child])
                    nested_total += contributions[child]
            totals.append(round(sum(rows[qid] for qid in fixed) + nested_total, 4))
        if any(abs(total - totals[0]) > 0.01 for total in totals[1:]):
            fail("choice-option-maximum-mismatch",
                 f"{groups[i].group_id} alternatives have unequal effective maxima: {totals}.")
        expected = round(totals[0] * groups[i].select_count, 4)
        contributions[i] = expected
        if check_contributions and abs(float(groups[i].contribution_marks) - expected) > 0.01:
            fail("choice-contribution-mismatch",
                 f"{groups[i].group_id} contributes {expected:g} marks after its choices, "
                 f"not {groups[i].contribution_marks:g}.")
    grouped = set().union(*members) if members else set()
    effective = sum(value for qid, value in rows.items() if qid not in grouped)
    effective += sum(contributions[i] for i in roots)
    return ChoiceForest(members, options, parents, children, roots, round(effective, 4), contributions)
