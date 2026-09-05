from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from grader import Criterion, GradeReport, QuestionMark  # noqa: E402
from mathpix import LineBox, PageOCR, pageocr_to_dict  # noqa: E402
from pdf_renderer import (  # noqa: E402
    RED,
    _draw_cross,
    _draw_handwritten_remark,
    _draw_report_strip,
    _draw_tick,
    _fmt_mark,
    _report_strip_height,
)


DOWNLOADS = Path(r"C:\Users\vs510\Downloads")
SOURCE = DOWNLOADS / "ea25a99b-9eea-4c1e-a422-a18eb5fc6c5d.pdf"
HERE = Path(__file__).resolve().parent
NORMALIZED = HERE / "taxation_answer_sheet_normalized.pdf"
OUTPUT = HERE / "evaluated_ca_taxation.pdf"
REPORT = HERE / "tool_style_report.json"
OCR = HERE / "tool_style_ocr.json"

# Native evaluated copies use an A4-like sheet size.  The source phone-scan PDF
# stores every image pixel as one PDF point (1500 x 2000), which would make the
# renderer's fixed-size handwriting and report text look less than half-sized.
SHEET_W = 600
SHEET_H = 800


def _c(description: str, page: int, x: float, y: float,
       awarded: float, maximum: float, evidence: str = "") -> dict:
    return {
        "description": description,
        "page": page,
        "x": x,
        "y": y,
        "awarded": awarded,
        "max": maximum,
        "evidence": evidence,
    }


QUESTIONS = [
    {
        "qid": "Income Tax MCQ",
        "score": 7,
        "max": 15,
        "remark": "Recheck MCQ 1, 3, 4 and 5 against the key.",
        "criteria": [
            _c("MCQ 1", 1, 500, 319, 0, 2, "student: a; key: c"),
            _c("MCQ 2", 1, 500, 439, 2, 2),
            _c("MCQ 3", 1, 500, 559, 0, 2, "student: d; key: c"),
            _c("MCQ 4", 1, 500, 678, 0, 2, "student: a; key: b"),
            _c("MCQ 5", 1, 500, 798, 0, 2, "student: b; key: a"),
            _c("MCQ 6", 1, 500, 918, 2, 2),
            _c("MCQ 7", 1, 950, 1038, 2, 2),
            _c("MCQ 8", 1, 780, 1158, 1, 1),
        ],
    },
    {
        "qid": "Income Tax Q1",
        "score": 9.5,
        "max": 15,
        "remark": "Section 10AA is unavailable; total income should be Rs.33,49,600.",
        "criteria": [
            _c("Gross annual value", 2, 1140, 925, .5, .5),
            _c("Standard deduction order", 2, 1135, 1000, 0, .5, "deducted before municipal taxes"),
            _c("Municipal taxes", 2, 1135, 1090, .5, .5),
            _c("House property total", 2, 1170, 1260, 0, .5, "student: 1,29,600; key differs"),
            _c("Business income", 2, 1200, 1480, 1.5, 1.5),
            _c("Sale consideration", 2, 1270, 1740, .5, .5),
            _c("Acquisition cost", 2, 1270, 1830, .5, .5),
            _c("Long term capital gain", 2, 1260, 1920, .5, 1, "threshold applied too early"),
            _c("Savings interest", 3, 1280, 274, .5, .5),
            _c("Royalty income", 3, 1280, 322, .5, .5),
            _c("Income from other sources", 3, 1280, 336, 0, .5, "royalty expense omitted"),
            _c("Gross total income", 3, 1280, 410, 0, .5, "consequential incorrect total"),
            _c("Section 80QQB", 3, 1280, 576, 0, .5, "eligible amount should be 1,90,000"),
            _c("Section 10AA", 3, 1280, 835, 0, .5, "operations began in PY 2021-22"),
            _c("Section 80TTA", 3, 1280, 1287, .5, .5),
            _c("Son LIC premium", 3, 1280, 1500, .5, .5),
            _c("Father LIC treatment", 3, 1280, 1548, .5, .5),
            _c("Tuition fees", 3, 1280, 1602, .5, .5),
            _c("Total income", 3, 1320, 1711, 0, .5, "key: 33,49,600"),
            _c("Special rate income", 4, 1200, 278, 1, 1),
            _c("Normal income", 4, 1200, 350, 0, .5, "incorrect base"),
            _c("Regular tax method", 4, 1250, 780, 1, 1.5, "consequential method credit"),
            _c("Section 112A tax", 4, 1250, 1250, .5, .5),
            _c("Final tax liability", 4, 1250, 1560, 0, .5, "key AMT: 6,81,020"),
        ],
    },
    {
        "qid": "Income Tax Q3.a",
        "score": 2.75,
        "max": 6,
        "remark": "Car, education facility and family-pension deduction need correction.",
        "criteria": [
            _c("Basic salary", 5, 1035, 480, .5, .5),
            _c("Hotel accommodation", 5, 1035, 630, .5, .5),
            _c("Car perquisite", 5, 1035, 1080, 0, 1, "key: 70,000"),
            _c("Group insurance", 5, 1035, 1180, .5, .5),
            _c("Education facility", 5, 1035, 1370, 0, .5, "taxable: 2,00,000"),
            _c("Leave travel concession", 5, 1035, 1720, 0, .5, "exemption overstated"),
            _c("Standard deduction", 5, 1035, 1930, .5, .5),
            _c("Salary total", 6, 1165, 250, 0, .5, "consequential incorrect total"),
            _c("Trust receipt", 6, 1145, 430, .5, .5),
            _c("Family pension", 6, 1120, 490, .25, .5, "section 57 deduction omitted"),
            _c("Total income", 6, 1155, 610, 0, .5, "incorrect total"),
        ],
    },
    {
        "qid": "Income Tax Q3.b",
        "score": 1,
        "max": 4,
        "remark": "Trust gift is exempt; also apply section 50CA.",
        "criteria": [
            _c("Company and share transfer", 6, 1260, 930, 1, 3, "section 50CA consequence omitted"),
            _c("Trust gift", 6, 1260, 1390, 0, 1, "section 12AB trust gift is exempt"),
        ],
    },
    {
        "qid": "Income Tax Q4.a",
        "score": 4.25,
        "max": 6,
        "remark": "House-property loss is Rs.80,000; total income Rs.3,95,000.",
        "criteria": [
            _c("Salary", 7, 1130, 520, .5, .5),
            _c("Standard deduction", 7, 1130, 585, .5, .5),
            _c("House property set-off", 7, 1400, 870, 0, .5, "incorrect set-off against salary"),
            _c("Let-out property", 7, 1200, 1130, .25, .5, "deduction computation differs"),
            _c("Self-occupied interest", 7, 1160, 1580, .5, .5),
            _c("House property loss", 7, 1230, 1760, 0, .5, "key: 80,000"),
            _c("Speculation result", 8, 930, 700, .5, .5),
            _c("Section 44AD", 8, 930, 920, .5, .5),
            _c("Capital gain set-off", 8, 930, 1340, .5, .5),
            _c("PPF income", 8, 930, 1510, .5, .75, "amount copied incorrectly"),
            _c("Section 80C deposit", 8, 930, 1790, .5, .5),
            _c("Total income", 8, 1190, 1910, 0, .25, "key: 3,95,000"),
        ],
    },
    {
        "qid": "Income Tax Q4.b",
        "score": 3.5,
        "max": 4,
        "remark": "HUF filing reasoning should use the basic exemption limit.",
        "criteria": [
            _c("HUF return filing", 9, 1060, 735, 1.5, 2, "threshold reasoning incomplete"),
            _c("Electricity consumption", 9, 930, 1110, 2, 2),
        ],
    },
    {
        "qid": "GST MCQ",
        "score": 8,
        "max": 15,
        "remark": "Recheck MCQ 2, 3, 7 and 8 against the key.",
        "criteria": [
            _c("MCQ 1", 10, 700, 460, 2, 2),
            _c("MCQ 2", 10, 1100, 565, 0, 2, "student: a; key: d"),
            _c("MCQ 3", 10, 700, 674, 0, 2, "student: b; key: d"),
            _c("MCQ 4", 10, 1360, 800, 2, 2),
            _c("MCQ 5", 10, 600, 932, 2, 2),
            _c("MCQ 6", 10, 820, 1052, 2, 2),
            _c("MCQ 7", 10, 650, 1184, 0, 2, "student: c; key: d"),
            _c("MCQ 8", 10, 700, 1308, 0, 1, "student: b; key: c"),
        ],
    },
    {
        "qid": "GST Q1.a",
        "score": 3,
        "max": 10,
        "remark": "RCM and ITC set-off are incorrect; cash payment is required.",
        "criteria": [
            _c("Goods taxable portion", 11, 1210, 660, .5, .5),
            _c("Voucher treatment", 11, 1080, 930, .5, .5),
            _c("Local delivery", 11, 1110, 1150, 0, .75, "SSP is liable"),
            _c("Stock transfer", 11, 1110, 1280, 0, .75, "distinct GSTIN is taxable"),
            _c("Commercial pure labour", 11, 1210, 1520, .5, .5),
            _c("Output tax totals", 11, 1400, 1760, 0, .75, "incorrect CGST SGST IGST totals"),
            _c("Opening CGST credit", 12, 1150, 350, .5, .5),
            _c("CA fee", 12, 1200, 570, 0, .75, "no RCM and no ITC"),
            _c("VXT credit", 12, 1400, 880, .5, .5),
            _c("Office rent", 12, 1160, 1400, 0, .75, "RCM credit omitted"),
            _c("Eligible ITC total", 12, 1220, 1510, 0, .75, "incorrect total"),
            _c("IGST set-off", 13, 1400, 520, .5, 1.5, "set-off only partly correct"),
            _c("Reverse charge payment", 13, 1215, 870, 0, .75, "must be paid in cash"),
            _c("Minimum cash payment", 13, 1215, 1070, 0, .75, "CGST 6,750 and SGST 9,000"),
        ],
    },
    {
        "qid": "GST Q1.b",
        "score": 5,
        "max": 5,
        "remark": "",
        "criteria": [
            _c("GST point 1", 14, 1190, 590, .5, .5),
            _c("GST point 2", 14, 1190, 795, .5, .5),
            _c("GST point 3", 14, 1190, 1050, .5, .5),
            _c("GST point 4", 14, 1190, 1145, .5, .5),
            _c("GST point 5", 14, 1190, 1270, .5, .5),
            _c("GST point 6", 14, 1190, 1435, .5, .5),
            _c("GST point 7", 14, 1190, 1630, 1, 1),
            _c("GST point 8", 14, 1190, 1790, 1, 1),
        ],
    },
    {
        "qid": "GST Q2.a",
        "score": 2,
        "max": 5,
        "remark": "Eligible ITC is Rs.6,40,250 after blocked and late credits.",
        "criteria": [
            _c("Included credit", 18, 1380, 420, .5, 1, "treatment label is incorrect"),
            _c("Works contract", 18, 1180, 720, 0, 1, "blocked credit"),
            _c("Late invoice", 18, 1380, 940, .5, 1, "time limit not applied"),
            _c("Truck credit", 18, 1180, 1160, .5, .5),
            _c("Order-wise eligibility", 18, 1180, 1460, .5, 1, "third order incorrect"),
            _c("Eligible ITC total", 18, 1380, 1870, 0, .5, "key: 6,40,250"),
        ],
    },
    {
        "qid": "GST Q2.b",
        "score": 2.5,
        "max": 5,
        "remark": "Return and concessional ticket place-of-supply conclusions are incorrect.",
        "criteria": [
            _c("Onward journey", 17, 930, 850, 1.25, 1.25),
            _c("Return journey", 17, 930, 1200, 0, 1.25, "place of supply: Jaipur"),
            _c("Concessional future ticket", 17, 930, 1580, 0, 1.25, "Mumbai with address, otherwise Chennai"),
            _c("On-board movie", 17, 930, 1900, 1.25, 1.25),
        ],
    },
    {
        "qid": "GST Q3.a",
        "score": 4.5,
        "max": 5,
        "remark": "Defaulting supplier restrictions also cover outward supplies.",
        "criteria": [
            _c("E-way bill threshold", 15, 1180, 665, 1, 1),
            _c("Defaulting supplier rule", 15, 1190, 850, 1.5, 2, "outward restriction omitted"),
            _c("Roshan conclusion", 15, 1240, 1090, 1, 1),
            _c("Sohan conclusion", 15, 1240, 1390, 1, 1),
        ],
    },
    {
        "qid": "GST Q3.b",
        "score": 3.25,
        "max": 5,
        "remark": "Use higher threshold; permitted service value is Rs.12 lakh.",
        "criteria": [
            _c("Composition threshold", 16, 1210, 490, 1, 1),
            _c("Eligibility conclusion", 16, 1210, 815, 1, 1),
            _c("Comparison amounts", 16, 1210, 1080, 1.25, 1.5, "higher amount rule not applied"),
            _c("Permitted service value", 16, 1210, 1660, 0, 1.5, "should be 12 lakh"),
        ],
    },
]


def _normalise_pages() -> bytes:
    """Make the four sideways phone scans upright before native rendering."""
    src = fitz.open(SOURCE)
    out = fitz.open()
    clockwise = {5}
    counterclockwise = {8, 9, 17}
    for index, page in enumerate(src, start=1):
        if index in clockwise:
            target = out.new_page(width=SHEET_W, height=SHEET_H)
            target.show_pdf_page(target.rect, src, index - 1, rotate=270)
        elif index in counterclockwise:
            target = out.new_page(width=SHEET_W, height=SHEET_H)
            target.show_pdf_page(target.rect, src, index - 1, rotate=90)
        else:
            target = out.new_page(width=SHEET_W, height=SHEET_H)
            target.show_pdf_page(target.rect, src, index - 1)
    src.close()
    data = out.tobytes(garbage=4, deflate=True)
    out.close()
    NORMALIZED.write_bytes(data)
    return data


def _build_report_and_ocr() -> tuple[GradeReport, list[PageOCR]]:
    pages = {p: PageOCR(page=p, image_width=1500, image_height=2000, lines=[])
             for p in range(1, 19)}
    questions: list[QuestionMark] = []
    line_counter = {p: 0 for p in pages}

    for qspec in QUESTIONS:
        criteria: list[Criterion] = []
        first_lid = ""
        first_page = int(qspec["criteria"][0]["page"])
        first_y = float(qspec["criteria"][0]["y"])
        per_step = float(qspec["max"]) >= 3
        for cspec in qspec["criteria"]:
            p = int(cspec["page"])
            line_counter[p] += 1
            lid = f"P{p}L{line_counter[p]}"
            if not first_lid:
                first_lid = lid
            x = float(cspec["x"])
            y = float(cspec["y"])
            # The native renderer draws immediately after x1. These narrow synthetic
            # OCR boxes represent the exact handwritten row that was independently
            # inspected; their right edge is chosen so the red verdict lands at x/y.
            offset = 12 if per_step else 20
            x1 = max(55, min(1430, x - offset))
            x0 = max(35, x1 - 170)
            line_text = f"{qspec['qid']} {cspec['description']}"
            pages[p].lines.append(LineBox(
                line_id=lid,
                text=line_text,
                bbox=(x0, max(15, y - 12), x1, min(1985, y + 12)),
                page=p,
            ))
            criteria.append(Criterion(
                description=str(cspec["description"]),
                awarded=float(cspec["awarded"]),
                max=float(cspec["max"]),
                met=float(cspec["awarded"]) >= float(cspec["max"]) - 1e-9,
                evidence=str(cspec.get("evidence", "")),
                anchor_quote=line_text,
                step_line_id=lid,
            ))
        questions.append(QuestionMark(
            qid=str(qspec["qid"]),
            score=float(qspec["score"]),
            max_score=float(qspec["max"]),
            page=first_page,
            anchor_line_id=first_lid,
            y_fraction=max(0.01, min(.99, first_y / 2000.0)),
            remark=str(qspec["remark"]),
            criteria=criteria,
            mark_style="per_step" if per_step else "single",
        ))

    for p in pages.values():
        p.lines.sort(key=lambda line: (line.bbox[1], line.bbox[0]))

    report = GradeReport(
        total_score=56.25,
        max_total=100,
        overall_remarks=(
            "You handled several deductions and GST concepts correctly. "
            "Most marks were lost in total-income computation, ITC eligibility, "
            "and place-of-supply conclusions. Revise section 10AA, RCM set-off, "
            "and composition limits."
        ),
        section_totals=[
            {"qid": "Income Tax MCQ", "score": 7, "max_score": 15},
            {"qid": "Income Tax Q1", "score": 9.5, "max_score": 15},
            {"qid": "Income Tax Q3", "score": 3.75, "max_score": 10},
            {"qid": "Income Tax Q4", "score": 7.75, "max_score": 10},
            {"qid": "GST MCQ", "score": 8, "max_score": 15},
            {"qid": "GST Q1", "score": 8, "max_score": 15},
            {"qid": "GST Q2", "score": 4.5, "max_score": 10},
            {"qid": "GST Q3", "score": 7.75, "max_score": 10},
        ],
        questions=questions,
        annotations=[],
    )
    return report, [pages[p] for p in sorted(pages)]


def _draw_feedback(page: fitz.Page, qspec: dict) -> None:
    """Put totals and feedback only in the white strip, never over handwriting."""
    x = SHEET_W + 12
    first = qspec["criteria"][0]
    y = max(34.0, min(SHEET_H - 90.0, float(first["y"]) * SHEET_H / 2000.0))
    title = (f"{qspec['qid']}  {_fmt_mark(qspec['score'])}/"
             f"{_fmt_mark(qspec['max'])}")
    page.insert_text((x, y), title, fontsize=10.5, color=RED, fontname="hebo")
    remark = str(qspec.get("remark", "") or "").strip()
    if remark:
        _draw_handwritten_remark(
            page, x, y + 16, remark,
            max_width=150, size=9.5, draw_bg=False,
        )


def _draw_clean_copy(source_bytes: bytes, report: GradeReport) -> bytes:
    """Native report/feedback layout with glyph-only marks beside answer rows.

    The general renderer also writes per-step numerals and a question total into
    the answer area.  On this dense Taxation script those characters covered the
    student's figures.  This review renderer deliberately draws only the native
    red tick/cross glyph on the sheet; all words and scores stay in the strip.
    """
    source = fitz.open(stream=source_bytes, filetype="pdf")
    widened = fitz.open()
    strip_w = 175
    page_w = SHEET_W + strip_w

    for pno in range(1, len(source) + 1):
        page = widened.new_page(width=page_w, height=SHEET_H)
        page.show_pdf_page(fitz.Rect(0, 0, SHEET_W, SHEET_H), source, pno - 1)
        page.draw_line(
            fitz.Point(SHEET_W, 0), fitz.Point(SHEET_W, SHEET_H),
            color=RED, width=1.0,
        )

        for qspec in QUESTIONS:
            for i, cspec in enumerate(qspec["criteria"]):
                if int(cspec["page"]) != pno:
                    continue
                x = float(cspec["x"]) * SHEET_W / 1500.0
                y = float(cspec["y"]) * SHEET_H / 2000.0
                seed = f"tax|{qspec['qid']}|{i}|{pno}"
                awarded = float(cspec["awarded"])
                maximum = float(cspec["max"])
                if awarded >= maximum - 1e-9:
                    _draw_tick(page, x, y, size=12.0, seed=seed)
                else:
                    # A partial step is not fully correct, so it receives a small
                    # cross at the disputed line. Its partial credit is visible in
                    # the question total, safely outside the answer image.
                    _draw_cross(page, x, y, size=9.5, seed=seed)

            if int(qspec["criteria"][0]["page"]) == pno:
                _draw_feedback(page, qspec)

    source.close()

    out = fitz.open()
    strip_h = _report_strip_height(report, page_w)
    first = out.new_page(width=page_w, height=strip_h + SHEET_H)
    _draw_report_strip(first, 0, 0, page_w, report)
    first.draw_line(
        fitz.Point(0, strip_h), fitz.Point(page_w, strip_h),
        color=RED, width=1.0,
    )
    first.show_pdf_page(
        fitz.Rect(0, strip_h, page_w, strip_h + SHEET_H), widened, 0,
    )
    if len(widened) > 1:
        out.insert_pdf(widened, from_page=1, to_page=len(widened) - 1)
    widened.close()
    out.set_metadata({
        "title": "CA Intermediate Taxation - Evaluated Copy",
        "subject": "Evaluation against supplied official question paper and solution",
        "author": "AI Copy Checker",
    })
    data = out.tobytes(garbage=4, deflate=True)
    out.close()
    return data


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    HERE.mkdir(parents=True, exist_ok=True)
    source_bytes = _normalise_pages()
    report, ocr_pages = _build_report_and_ocr()
    evaluated = _draw_clean_copy(source_bytes, report)
    OUTPUT.write_bytes(evaluated)
    REPORT.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    OCR.write_text(json.dumps([pageocr_to_dict(p) for p in ocr_pages], indent=2),
                   encoding="utf-8")
    print(OUTPUT)
    print(f"score={report.total_score:g}/{report.max_total:g}")
    print(f"pages={len(fitz.open(stream=evaluated, filetype='pdf'))}")
    print(f"unanchored={report.unanchored_step_qids}")


if __name__ == "__main__":
    main()
