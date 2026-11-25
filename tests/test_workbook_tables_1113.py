"""A workbook holding an Excel table is read, not dropped as "could not be read".

13 Nov 2026, PW Skills Data Analytics: a learner's Google Sheet (4,000 formulas across
eight report sheets) reached the checker as one line, "The file could not be read
(AttributeError)", because openpyxl's TableList.items() gives (name, range) pairs and
the sheet summary read .ref off the range text. The checker then saw only the link page
and reported that the submission held a link, not answers.
"""
import io
import os
import sys

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.table import Table

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import work_document  # noqa: E402


def workbook_with_table():
    book = Workbook()
    sheet = book.active
    sheet.title = "Sales_Data(Q.1,2,3)"
    sheet.append(["Region", "Amount", "Band"])
    for n in range(2, 12):
        sheet.append(["North" if n % 2 else "South", n * 1000, f'=IF(B{n}>=5000,"High","Low")'])
    sheet.add_table(Table(displayName="SalesTable", ref="A1:C11"))
    report = book.create_sheet("Product Lookup Report(Q.4)")
    report["A1"] = "Total"
    report["B1"] = "=SUM('Sales_Data(Q.1,2,3)'!B2:B11)"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_a_sheet_with_an_excel_table_is_read():
    html, _images, _note = work_document.workbook_html(workbook_with_table())
    assert "SalesTable (A1:C11)" in html
    assert "IF(B" in html and "SUM(" in html, "the formulas reach the checker"


def test_a_summary_line_that_fails_does_not_lose_the_sheet(monkeypatch):
    def broken(ws, values_ws):
        raise AttributeError("a summary detail openpyxl gave in another shape")
    monkeypatch.setattr(work_document, "_sheet_facts", broken)
    html, _images, _note = work_document.workbook_html(workbook_with_table())
    assert "Sheet details could not be read (AttributeError)" in html
    assert "SUM(" in html, "the formulas and values are still there"


def test_question_numbers_are_read_wherever_the_name_puts_them():
    cases = {"Q3_Electronics": [3], "Q5 & Q6": [5, 6], "SalesData-Q (7)": [7], "SheetData- Q(14-B)": [14],
             "Product Lookup Report(Q.4)": [4], "Sales_Data(Q.1,2,3)": [1, 2, 3], "Question 12": [12],
             "MH_SalesData": [], "Product_master": [], "SQL 2": [], "Unique 4": [], "Quarterly": []}
    for name, expected in cases.items():
        assert work_document.sheet_questions(name) == expected, name


def seventeen_sheets():
    """Like the learner's workbook of 13 Nov 2026: a data sheet, then one sheet per question,
    each 400 rows of figures with a formula column."""
    book = Workbook()
    for index in range(17):
        sheet = book.active if index == 0 else book.create_sheet()
        sheet.title = "MH_SalesData" if index == 0 else f"SalesData-Q ({index})"
        sheet.append(["Order_ID", "Customer", "City", "Category", "Product", "Quantity", "Unit_Price", "Discount_%",
                      "Order_Date", "Delivery_Date", "Salesperson", f"Answer {index}"])
        for row in range(2, 402):
            sheet.append([1000 + row, "Ritesh", "Pune", "Electronics", "Headset", row % 20, 45652, 10,
                          "2026-01-01", "2026-01-08", "Amit", f'=IF(OR(C{row}="Mumbai",H{row}>=15),"Priority","Normal")'])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_no_sheet_of_a_long_workbook_is_laid_out_off_the_page():
    import fitz
    html, images, note = work_document.workbook_html(seventeen_sheets())
    data = work_document._html_pdf(html, images)
    with fitz.open(stream=data, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        for index in range(1, 17):
            assert f"Q{index}: sheet SalesData-Q ({index})" in text, f"sheet for Q{index} is on a page"
        assert "MH_SalesData" in text
        assert document.page_count >= 17, "each sheet starts its own page"


def test_content_placed_below_the_page_end_gets_a_taller_page():
    import fitz
    # One table row far taller than A4 cannot be split, so MuPDF places it past the page end.
    tall = "<table><tr><td>" + "<br/>".join(f"line {n}" for n in range(140)) + "</td></tr></table><p>after the table</p>"
    data = work_document._html_pdf(tall)
    with fitz.open(stream=data, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert "line 139" in text and "after the table" in text
        assert max(page.rect.height for page in document) >= fitz.paper_rect("a4").height


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
