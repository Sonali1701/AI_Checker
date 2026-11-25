# -*- coding: utf-8 -*-
"""Build 79, marking scheme ("rubric") group: Parishram ISC Test-10 (20 Sept 2026).

Faculty found correct work marked against points the tool made up:
  * Chemistry Q6: three stereocentres marked wrong "against 1". The verifier's copy of
    the Q6 solution ended at "Chiral center (1)", because the solution's numbered list
    "1. -H" was read as the start of question 1.
  * Chemistry Q20: "Complete the reaction" whose reactions are a picture. The paper
    and key went as text only, so the grader never saw them and invented the items.
  * Chemistry Q22/Q23 and English Q14(a): an extra "passage comprehension" or "fourth
    apparition" criterion that is not a printed part.
  * Chemistry Q24: the solution's part labels are shifted by one; nobody was told.
Fixtures are copied from the real text layer of the paper ("Questions with
Solutions" PDF, uploaded as both paper and key).
"""
from __future__ import annotations

import io
import os
import sys
import unittest

import fitz
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader  # noqa: E402
import pipeline  # noqa: E402
from grader import GradeReport, build_audit_prompt, build_system_prompt  # noqa: E402

# --- real text layer lines (Chemistry Test-10, pages 1, 5 and 7) -------------------
PAPER_Q6 = """Q6
SECTION - B
Short Answer Questions (6-15) (10 × 2 = 20)
How many stereogenic centers are possible for
given structures?
Q7
Why is the use of thionyl chloride (SOCl )
2
considered the best method for preparing
chloroethane from ethanol?
"""
KEY_TABLE = """Answer Key
Q6
Refer to Solution
Q7
Refer to Solution
"""
SOLUTION_Q6 = """Q6
Text Solution:
2
C1 (C = O): Achiral (sp  hybridized, only
bonded to three groups).
C2 (with two -CH  groups): Achiral (bonded to
3
two identical methyl groups).
C3 (with one -CH  group): Chiral center (1). It is
3
bonded to four different groups:
1. -H
2. -CH
3
3. -C(CH )  -C(= O) - (pathway toward C2)
3 2
4. -CH(Br) - (pathway toward C4)
C4 (with -Br group): Chiral center (2). It is
bonded to four different groups:
1. -H
2. -Br
3. -CH(CH ) - (pathway toward C3)
3
4. -C(CH )  - (pathway toward C5)
3 3
C5 (with three -CH  groups attached): Achiral
3
(bonded to two identical methyl groups on
the ring carbon, plus the fourth bond leads to
3
a methyl group attached to it, making it an sp
carbon bonded to three identical methyl
groups overall).
C6 (with one -CH  group): Chiral center (3). It
3
is bonded to four different groups:
1. -H
2. -CH
3
3. -C(CH )  - (pathway toward C5)
3 3
4. -C(= O) - (pathway toward C1)
Q7
Text Solution:
Reaction: C H OH + SOCl  → C H Cl + SO (g) +
"""
CHEMISTRY_DOC = PAPER_Q6 + KEY_TABLE + SOLUTION_Q6

# Q23 (lettered parts, roman options) and Q24 (roman parts) from pages 4 and 12-13.
PAPER_Q23_Q24 = """Q23
Read the passage given below and answer the
following questions:
(a)   Which of the following is most reactive
towards S 1 reaction?
(i)
C6H5
(ii)
CH2
(iii)
CH   =
(iv)
CH   =   CHCl
(b)   Isopropyl chloride undergoes hydrolysis by
which type of nucleophilic substitution reaction;
(c)   Why tertiary alkyl halides are practically inert
to substitution by S 2 mechanism?
(d)   What is the correct order of reactivity
towards S 2 mechanism for the following alkyl
Q24
Read the passage given below and answer the
following questions:
Passage-II
(i) The best method for the conversion of an
alcohol into an alkyl chloride is by treating the
alcohol with
(a) PCl₅
(b) Conc .HCl in the presence of anhydrous ZnCl₂
(c) SOCl₂ in presence of pyridine
(d) None of these
(ii) The catalyst used in the preparation of an
alkyl chloride by the action of conc . HCl on
alcohol is
(a) anhydrous AlCl₃
(b) FeCl₃
(c) anhydrous ZnCl₂
(d) Cu
(iii) An alkyl halide reacts with metallic sodium in
dry ether. The reaction is known as:
(a) Frankland’s reaction
(b) Sandmeyer’s reaction
(c) Wurtz reaction
(d) Kolbe’s reaction
(iv) Fluorobenzene (C₆H₅F) can be synthesized in
the laboratory
(a) By direct fluorination of benzene with F₂ gas
(b) By reacting bromobenzene with NaF solution
(c) By heating phenol with HF and KF
(d) From aniline by diazotization followed by
heating the diazonium salt with HBF₄
"""
SOLUTION_Q23_Q24 = """Q23
Text Solution:
(a)   ClCH CH = CH  is allylic halide which is more
reactive.
(b)   Isopropyl chloride undergoes hydrolysis by
both S 1 and S 2 reaction.
(c)   In tertiary alkyl halide, steric hinderence is a
dominant factor.
(d)   The correct order for reactivity is: RCH X >
R CHX > R CX.
Q24
Text Solution:
(i) (c)
A mixture of concentrated HCl and anhydrous
ZnCl₂ is known as Lucas Reagent. Anhydrous
ZnCl₂ acts as a Lewis acid catalyst that helps
break the C–OH bond in the alcohol.
(ii) (c)
The Wurtz reaction involves the coupling of two
alkyl halide molecules in the presence of sodium
metal and dry ether to form a higher symmetric
alkane:
(iii) (d)
This specific method is called the Balz-
Schiemann reaction.
"""


def _png(width, height, pattern):
    image = Image.new("RGB", (width, height), "white")
    pixels = image.load()
    for x in range(width):
        for y in range(height):
            pixels[x, y] = pattern(x, y)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _three_page_paper():
    """Like the publisher PDF: dense text on every page, a footer logo and a watermark
    repeated on every page, a structure picture on page 2, a QR code on page 3."""
    logo = _png(60, 60, lambda x, y: (200, 30, 30) if (x + y) % 7 else (255, 255, 255))
    structure = _png(240, 140, lambda x, y: (0, 0, 0) if abs(y - 70 - (x % 40) // 2) < 2
                     else ((128, 128, 128) if x == y else (255, 255, 255)))
    qr = _png(150, 150, lambda x, y: (0, 0, 0) if ((x // 10) * 7 + (y // 10) * 3) % 2 else (255, 255, 255))
    with fitz.open() as doc:
        logo_xref = 0
        for number in range(1, 4):
            page = doc.new_page()
            page.insert_textbox(fitz.Rect(40, 40, 560, 600),
                                f"Q{number} Explain the reaction mechanism in detail. " * 22, fontsize=9)
            if logo_xref:
                page.insert_image(fitz.Rect(250, 760, 290, 800), xref=logo_xref)
            else:
                logo_xref = page.insert_image(fitz.Rect(250, 760, 290, 800), stream=logo)
            if number == 2:
                page.insert_image(fitz.Rect(300, 610, 540, 750), stream=structure)
            if number == 3:
                page.insert_image(fitz.Rect(60, 640, 156, 736), stream=qr)
        return doc.tobytes()


class FocusedSolutionTests(unittest.TestCase):
    def test_numbered_list_in_a_solution_does_not_end_the_question(self):
        focused = grader._focus_reference_text(CHEMISTRY_DOC, {"Q6"}, 20_000)
        self.assertIn("Chiral center (2)", focused)
        self.assertIn("Chiral center (3)", focused)
        self.assertIn("4. -C(= O) - (pathway toward C1)", focused)
        self.assertNotIn("Reaction: C H OH", focused)

    def test_bare_numbered_key_keeps_the_points_of_its_answer(self):
        key = "4.\nAnswer four.\n5.\nThe answer has points:\n1. first point\n2. second point\n6.\nAnswer six.\n"
        focused = grader._focus_reference_text(key, {"Q5"}, 20_000)
        self.assertIn("2. second point", focused)
        self.assertNotIn("Answer six", focused)
        # A key with a "Q1 (A)" answer table and bare-numbered worked solutions.
        mixed = ("Q1 (A)\nQ2 (B)\nQ3 (C)\n1.\nsol one\n1. point\n2.\nsol two\n"
                 "1. step a\n2. step b\n3.\nsol three\n")
        focused = grader._focus_reference_text(mixed, {"Q2"}, 20_000)
        self.assertIn("2. step b", focused)
        self.assertNotIn("sol three", focused)

    def test_one_document_paper_and_key_reaches_the_verifier_once_with_late_solutions(self):
        filler = "explanation of the step with its reason " * 14
        paper = "".join(f"Q{n}\nQuestion {n} text. {filler}\n" for n in range(1, 21))
        solutions = "".join(f"Q{n}\nText Solution:\nSOLUTION-{n}-END {filler}\n" for n in range(1, 21))
        document = paper + "Answer Key\n" + solutions
        report = GradeReport(questions=[], total_score=0, max_total=0, overall_remarks="")
        prompt = build_audit_prompt(report, answer_key_text=document, ocr_transcript="",
                                    question_paper_text=document,
                                    selected_qids={str(n) for n in range(1, 21)})
        self.assertIn("SOLUTION-20-END", prompt)
        self.assertEqual(prompt.count("Question 7 text."), 1)
        separate = build_audit_prompt(report, answer_key_text=solutions, ocr_transcript="",
                                      question_paper_text=paper, selected_qids={"3"})
        self.assertIn("=== QUESTION PAPER (text) ===", separate)
        self.assertIn("=== ANSWER KEY / SOLUTION (text) ===", separate)


class FigurePageTests(unittest.TestCase):
    def test_a_printed_figure_keeps_its_page_image(self):
        data = _three_page_paper()
        text = grader.pdf_text_layer(data, "paper.pdf")
        self.assertEqual(grader.figure_pages_in_text(text), {2})
        self.assertIn("--- PAGE 2 ---\n" + grader.FIGURE_PAGE_NOTE, text)
        pages = [b"page1", b"page2", b"page3"]
        self.assertEqual(pipeline._prefer_text_over_images(pages, text), ([b"page2"], True))
        self.assertEqual(pipeline._prepare_answer_key(pages, None, text), ([b"page2"], text.strip(), True))
        # A paper with no picture (the English paper) still travels as text alone.
        prose = "--- PAGE 1 ---\n" + "Explain the theme of the poem. " * 40
        self.assertEqual(grader.figure_pages_in_text(prose), set())
        self.assertEqual(pipeline._prefer_text_over_images([b"page1"], prose), ([], True))


class InventedCriteriaTests(unittest.TestCase):
    def test_grading_prompts_forbid_criteria_that_are_not_printed_parts(self):
        for subject in ("Chemistry", "English"):
            prompt = build_system_prompt(subject, "Class 12")
            self.assertIn("A criterion is a printed part", prompt)
            self.assertIn("passage comprehension", prompt)
            self.assertIn("1, 1, 1, 2", prompt)
            self.assertIn("a fourth item when it asks for three", prompt)
        recovery = build_system_prompt("Chemistry", "Class 12", recovery=True)
        self.assertIn("never a comprehension, overall or balancing mark", recovery)


class KeyPartLabelTests(unittest.TestCase):
    def test_solution_labelling_fewer_parts_than_printed_is_named(self):
        from key_answers import solution_part_gaps
        document = PAPER_Q23_Q24 + "Answer Key\nQ23\nRefer to Solution\n" + SOLUTION_Q23_Q24
        notes = solution_part_gaps(document, document)
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].startswith("Q24: the paper prints parts (i) to (iv)"), notes)
        self.assertIn("(i), (ii), (iii)", notes[0])
        self.assertNotIn(";", notes[0])

    def test_complete_solution_or_choice_question_is_not_named(self):
        from key_answers import solution_part_gaps
        paper = "Q9\nComplete the following reactions:\n(i) \n(ii) \nQ10\nOR question\n(i) a\n(ii) b\nOR\n(i) c\n"
        key = "Q9\nText Solution:\n(i) CH3CH2OH\n(ii) (CH3)2CHBr + Na\nQ10\n(i) x\n"
        self.assertEqual(solution_part_gaps(paper, key), [])


if __name__ == "__main__":
    unittest.main()
