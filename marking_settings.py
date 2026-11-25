# -*- coding: utf-8 -*-
"""Two batch settings the operator chooses: the language the answers are written in,
and how leniently steps are marked (Vidyapeeth team requirements, 10 Oct 2026).

Both reach the grader with the paper and the second examiner with the category's
rules, so the two examiners mark under the same setting. Neither changes a mark by
itself: they are instructions, and code still verifies every awarded step.

Languages are released only once their handwriting reading has been checked on real
copies. Hindi and English are; the others send every copy to a faculty reviewer until
sample copies in that language have been graded and compared (their suggestion:
"run validation tests per supported language before full automated scoring").
"""
from __future__ import annotations

LANGUAGES = {
    "": "Auto (English or Hindi)",
    "english": "English",
    "hindi": "Hindi",
    "marathi": "Marathi",
    "gujarati": "Gujarati",
    "bengali": "Bengali",
    "tamil": "Tamil",
    "telugu": "Telugu",
    "urdu": "Urdu",
}
SCRIPTS = {
    "english": "Latin script", "hindi": "Devanagari script", "marathi": "Devanagari script",
    "gujarati": "Gujarati script", "bengali": "Bengali script", "tamil": "Tamil script",
    "telugu": "Telugu script", "urdu": "Urdu (Perso-Arabic) script, written right to left",
}
# Languages whose copies have been graded live and compared with faculty marks.
VALIDATED_LANGUAGES = frozenset({"", "english", "hindi"})

LENIENCY = {"standard": "Standard", "strict": "Strict", "lenient": "Lenient"}
_LENIENCY_RULES = {
    "strict": (
        "STRICT step marking, chosen for this batch: a step earns its marks only when it "
        "is complete and correct as written. A right idea with a wrong figure, a missing "
        "unit or an unfinished statement earns nothing for that step, and unclear writing "
        "gets no benefit of the doubt. Do not award part of a step."),
    "lenient": (
        "LENIENT step marking, chosen for this batch: a step whose idea or method is right "
        "earns its marks even when it is brief or carries a small slip in arithmetic, "
        "units or wording. A step that is partly right earns half of its marks. Unclear "
        "handwriting gets the benefit of the doubt when the working that follows fits the "
        "right reading. A wrong final answer still loses the final answer step."),
}


class SettingError(ValueError):
    pass


def language(value: str | None) -> str:
    key = " ".join(str(value or "").strip().lower().split())
    if key in ("auto", "default"):
        key = ""
    if key not in LANGUAGES:
        raise SettingError(f"Unknown answer language {value!r}. Choose one of: "
                           + ", ".join(v for v in LANGUAGES.values()) + ".")
    return key


def leniency(value: str | None) -> str:
    key = str(value or "").strip().lower() or "standard"
    if key not in LENIENCY:
        raise SettingError(f"Unknown step marking {value!r}. Choose Standard, Strict or Lenient.")
    return key


def rules(answer_language: str = "", step_leniency: str = "standard") -> str:
    """The batch settings as marking rules, or "" for the defaults."""
    parts = []
    if answer_language and answer_language != "english":
        name = LANGUAGES[answer_language]
        parts.append(
            f"ANSWER LANGUAGE, chosen for this batch: the answers are written in {name} "
            f"({SCRIPTS[answer_language]}). Read every answer in {name}. Mark the content, "
            f"not the language: an answer in {name} earns what the same answer in English "
            "would. Quote the student's own words in their own script. Write remarks in "
            "simple English.")
    rule = _LENIENCY_RULES.get(step_leniency or "standard")
    if rule:
        parts.append(rule)
    return "\n".join(parts)


def language_hold(answer_language: str = "") -> str | None:
    """Why copies in this language go to a faculty reviewer, or None."""
    if answer_language in VALIDATED_LANGUAGES:
        return None
    name = LANGUAGES.get(answer_language, answer_language)
    return (f"{name} answers go to faculty review until the reading of {name} handwriting "
            "has been checked on sample copies.")


def ui_choices() -> dict:
    return {"languages": [{"id": k, "name": v, "validated": k in VALIDATED_LANGUAGES}
                          for k, v in LANGUAGES.items()],
            "leniency": [{"id": k, "name": v} for k, v in LENIENCY.items()]}
