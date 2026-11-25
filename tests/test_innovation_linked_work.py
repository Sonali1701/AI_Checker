# -*- coding: utf-8 -*-
"""School of Innovation: linked work and submissions that are not PDFs.

PW Skills team's samples, 21 Oct 2026 (sheet "Assignment Sample Data for AI Tool",
tab "Other samples"): cover PDFs with GitHub notebook links and a Drive file link, a
raw GitHub notebook link, a Drive folder holding a Power BI file, a Google Sheet, and
a Power BI file on GitHub. Every link was publicly downloadable. The checker now opens
them (linked_work.py) and converts notebooks, workbooks, Power BI, Tableau, Word and
code files into pages it can mark (work_document.py).

Offline: the network is replaced, and every fixture is built here.
"""
import base64
import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fitz  # noqa: E402

import categories  # noqa: E402
import linked_work as lw  # noqa: E402
import work_document as wd  # noqa: E402


def _pdf(lines, link=None):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 72
    for text in lines:
        page.insert_text((72, y), text, fontsize=11)
        y += 16
    if link:
        page.insert_link({"kind": fitz.LINK_URI, "uri": link, "from": fitz.Rect(72, y - 27, 300, y - 13)})
    return doc.tobytes()


def _text(pdf_bytes):
    """The PDF's text as the typed reader takes it, ligatures spelled out."""
    flags = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return " ".join(" ".join(page.get_text(flags=flags) for page in doc).split())


def _png():
    doc = fitz.open()
    page = doc.new_page(width=200, height=120)
    page.insert_text((20, 60), "plot", fontsize=20)
    return page.get_pixmap(dpi=72).tobytes("png")


# --- the category takes linked work -----------------------------------------------------

def test_only_innovation_opens_linked_work():
    assert {c.id for c in categories.REGISTRY.values() if c.intake.linked_work} == {"innovation"}
    assert next(c for c in categories.ui_choices() if c["id"] == "innovation")["linked_work"] is True


# --- which links are opened, and how ----------------------------------------------------

def test_links_are_read_from_annotations_and_text():
    cover = _pdf(["Name: A Learner", "Github Link : CODE",
                  "Drive: https://drive.google.com/file/d/1yVGn51NIJpgpR9-AZ6maiKVWv2l4bfzi/view"],
                 link="https://github.com/someone/repo/blob/main/work.ipynb")
    links = lw.links_in_pdf(cover)
    assert links[0] == "https://github.com/someone/repo/blob/main/work.ipynb"
    assert "https://drive.google.com/file/d/1yVGn51NIJpgpR9-AZ6maiKVWv2l4bfzi/view" in links
    assert len(lw.pdf_own_text(cover)) <= lw.COVER_MAX_CHARS


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/o/r/blob/main/FSD-AG-005%20%E2%80%93%20JS.ipynb",
     "https://raw.githubusercontent.com/o/r/main/FSD-AG-005%20%E2%80%93%20JS.ipynb"),
    ("https://github.com/o/r", "https://codeload.github.com/o/r/zip/HEAD"),
    ("https://github.com/o/r/tree/dev/week3", "https://codeload.github.com/o/r/zip/dev"),
    ("https://drive.google.com/file/d/1ZE3l6_B8ZkLQiIReHPyGNl0o4ZDJmQ9G/view?usp=sharing",
     "https://drive.usercontent.google.com/download?id=1ZE3l6_B8ZkLQiIReHPyGNl0o4ZDJmQ9G&export=download&confirm=t"),
    ("https://docs.google.com/spreadsheets/d/1JL4XLW6caGUa27PK70ZpU_TkAo2Yt1e4/edit?gid=8",
     "https://docs.google.com/spreadsheets/d/1JL4XLW6caGUa27PK70ZpU_TkAo2Yt1e4/export?format=xlsx"),
    ("https://docs.google.com/spreadsheets/d/1RY8AgcNwIDRwu8Ysp-6E4Q9w0Mkr-xo5/edit?usp=sharing&rtpof=true",
     "https://drive.usercontent.google.com/download?id=1RY8AgcNwIDRwu8Ysp-6E4Q9w0Mkr-xo5&export=download&confirm=t"),
    ("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit",
     "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/export?format=pdf"),
    ("https://colab.research.google.com/drive/1AbCdEfGhIjKlMnOpQr",
     "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOpQr&export=download&confirm=t"),
])
def test_each_kind_of_link_is_fetched_from_its_download_address(monkeypatch, url, expected):
    asked = []

    def fake_get(target, budget, accept_html=False):
        asked.append(target)
        if target.startswith("https://codeload.github.com/"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("r-main/week3/answers.ipynb", "{}")
                z.writestr("r-main/node_modules/x/index.js", "skip")
            return buf.getvalue(), {}, target
        return b"data", {"content-disposition": 'attachment; filename="work.ipynb"'}, target
    monkeypatch.setattr(lw, "_get", fake_get)
    files = lw.open_link(url)
    assert asked[0] == expected
    assert files and all("node_modules" not in f.name for f in files)


def test_a_drive_folder_is_listed_and_its_files_fetched(monkeypatch):
    listing = ('<a href="https://drive.google.com/file/d/1PH4o7oPehNn0TRL2vYEIHxRRfX3ehiUw/view?usp=drive_web">'
               '<div class="flip-entry-title">visualisations.pbix</div></a>'
               '<a href="https://drive.google.com/file/d/1ZZZZZZZZZZZZZZZZZZZZZ/view"><div class="flip-entry-title">'
               'notes.mp4</div></a>').encode()
    asked = []

    def fake_get(target, budget, accept_html=False):
        asked.append(target)
        if "embeddedfolderview" in target:
            return listing, {"content-type": "text/html"}, target
        return b"PK..", {}, target
    monkeypatch.setattr(lw, "_get", fake_get)
    files = lw.open_link("https://drive.google.com/drive/folders/1xYbPb1s6zb9DPVyepYYwW5XhYr3HZx6A")
    assert [f.name for f in files] == ["visualisations.pbix"]          # the video is not read
    assert asked[0] == "https://drive.google.com/embeddedfolderview?id=1xYbPb1s6zb9DPVyepYYwW5XhYr3HZx6A"


def test_only_allowed_hosts_and_https_are_ever_contacted(monkeypatch):
    class Response:
        def __init__(self, status, location=None, body=b"", ctype="application/octet-stream"):
            self.status_code, self.headers = status, {"Location": location or "", "Content-Type": ctype}
            self._body = body

        def iter_content(self, size):
            yield self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import requests
    hops = []

    def fake(url, **kwargs):
        hops.append(url)
        if "evil" in url:
            raise AssertionError("an unlisted host was contacted")
        if url.startswith("https://drive.usercontent.google.com/download?id=PRIVATE"):
            return Response(302, "https://accounts.google.com/ServiceLogin?continue=x")
        if url.startswith("https://drive.usercontent.google.com/download?id=AWAY"):
            return Response(302, "https://evil.example.com/file")
        if url.startswith("https://drive.usercontent.google.com/download?id=PAGE"):
            return Response(200, body=b"<!DOCTYPE html><html>You need access</html>", ctype="text/html")
        return Response(200, body=b"%PDF-1.4 ok")
    monkeypatch.setattr(requests, "get", fake)
    with pytest.raises(lw.LinkError, match="not shared publicly"):
        lw.open_link("https://drive.google.com/file/d/PRIVATEfileid12345/view")
    with pytest.raises(lw.LinkError, match="evil.example.com"):
        lw.open_link("https://drive.google.com/file/d/AWAYfileid1234567/view")
    with pytest.raises(lw.LinkError, match="not shared publicly"):
        lw.open_link("https://drive.google.com/file/d/PAGEfileid1234567/view")
    with pytest.raises(lw.LinkError, match="not one the checker opens"):
        lw.open_link("https://www.linkedin.com/in/someone")
    with pytest.raises(lw.LinkError, match="profile"):
        lw.open_link("https://github.com/someone")
    assert all(h.startswith("https://") for h in hops)


# --- conversions ------------------------------------------------------------------------

def test_a_notebook_is_read_with_its_saved_outputs():
    notebook = {"metadata": {"kernelspec": {"language": "python"}}, "nbformat": 4, "cells": [
        {"cell_type": "markdown", "source": ["## Question 1\n", "What is **NumPy**?"]},
        {"cell_type": "code", "execution_count": 3, "source": "import numpy as np\nprint(np.arange(3))",
         "outputs": [{"output_type": "stream", "name": "stdout", "text": "[0 1 2]\n"}]},
        {"cell_type": "code", "execution_count": 4, "source": "plt.plot([1, 2])",
         "outputs": [{"output_type": "display_data",
                      "data": {"image/png": base64.b64encode(_png()).decode(), "text/plain": "<Figure>"}}]},
        {"cell_type": "code", "execution_count": 5, "source": "1/0",
         "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero",
                      "traceback": ["\x1b[0;31mZeroDivisionError\x1b[0m: division by zero"]}]},
        {"cell_type": "code", "execution_count": 6, "source": "px.bar(df)",
         "outputs": [{"output_type": "display_data", "data": {"application/vnd.plotly.v1+json": {
             "data": [{"type": "bar", "name": "Sales"}], "layout": {"title": {"text": "Sales by city"}}},
             "text/plain": "{" + '"a": 1, ' * 500 + "}"}}]},
        {"cell_type": "code", "execution_count": None, "source": "x = 1", "outputs": []},
    ]}
    upload = lw.LinkedFile("week2.ipynb", json.dumps(notebook).encode(), "https://github.com/o/r/blob/main/week2.ipynb")
    pdf = wd.build_document(cover_pdf=None, files=[upload], problems=[])
    text = _text(pdf)
    assert "Linked work 1 of 1: week2.ipynb" in text and "Jupyter notebook" in text
    assert "5 code cells, 4 with saved output, 1 with an error" in text
    assert "print(np.arange(3))" in text and "[0 1 2]" in text and "In [3]:" in text
    assert "ZeroDivisionError: division by zero" in text and "\x1b" not in text
    assert "Interactive Plotly chart (not rendered): bar 'Sales'; title 'Sales by city'" in text
    assert '"a": 1, "a": 1, "a": 1' not in text                       # the raw chart data is not dumped
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert sum(len(page.get_images()) for page in doc) == 1         # the saved plot


def test_a_notebook_saved_without_outputs_says_so():
    notebook = {"cells": [{"cell_type": "code", "source": "console.log(1)", "outputs": [], "execution_count": None}]}
    body, _, note = wd.notebook_html(json.dumps(notebook).encode())
    assert "No cell has saved output" in note and "console.log(1)" in body


def test_a_workbook_is_read_as_settings_formulas_and_values():
    import openpyxl
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales_Report"
    ws.append(["Order_ID", "City", "Quantity", "Unit_Price", "Order Type"])
    for i, (city, qty) in enumerate([("Pune", 3), ("Mumbai", 16), ("Pune", 8)], start=2):
        ws.append([1000 + i, city, qty, 100, f'=IF(C{i}>=15,"Bulk Order",IF(C{i}>=5,"Medium Order","Regular Order"))'])
    for cell in ws[1]:
        cell.font, cell.alignment = Font(bold=True), Alignment(horizontal="center")
        cell.fill = PatternFill("solid", fgColor="FFFF00")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:E4"
    ws.auto_filter.add_filter_column(1, ["Pune"])
    ws.column_dimensions["B"].width = 14
    totals = wb.create_sheet("Q4")
    totals["A1"], totals["B1"] = "Total quantity", "=SUM(Sales_Report!C2:C4)"
    chart = BarChart()
    chart.title = "Quantity by order"
    chart.add_data(Reference(ws, min_col=3, min_row=1, max_row=4), titles_from_data=True)
    totals.add_chart(chart, "D2")
    buf = io.BytesIO()
    wb.save(buf)
    text = _text(wd.build_document(cover_pdf=None, files=[lw.LinkedFile("MH_SalesData.xlsx", buf.getvalue(), "")],
                                   problems=[]))
    assert "Sheet 1 of 2: Sales_Report" in text and "Q4: sheet Q4 (sheet 2 of 2)" in text
    assert "Frozen panes at A2" in text and "Filter buttons on A1:E4" in text and "Filter on B: Pune" in text
    assert "Header row 1: 5 cells, 5 bold, 5 centred, fill colour" in text
    assert "Column E (Order Type): 3 formulas." in text
    assert 'E2: =IF(C2>=15,"Bulk Order",IF(C2>=5,"Medium Order","Regular Order")) [3 cells, rows 2-4]' in text
    assert "B1: =SUM(Sales_Report!C2:C4)" in text
    assert "barChart, title 'Quantity by order'" in text


def _sqlite_model():
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    handle.close()
    db = sqlite3.connect(handle.name)
    db.executescript("""
        CREATE TABLE [Table] (ID INTEGER, Name TEXT);
        CREATE TABLE [Column] (ID INTEGER, TableID INTEGER, ExplicitName TEXT, InferredName TEXT, Type INTEGER, Expression TEXT);
        CREATE TABLE [Measure] (ID INTEGER, TableID INTEGER, Name TEXT, Expression TEXT, FormatString TEXT);
        CREATE TABLE [Partition] (ID INTEGER, TableID INTEGER, Type INTEGER, QueryDefinition TEXT);
        CREATE TABLE [Relationship] (FromColumnID INTEGER, ToColumnID INTEGER, IsActive INTEGER, CrossFilteringBehavior INTEGER);
        INSERT INTO [Table] VALUES (1, 'Sales'), (2, 'LocalDateTable_123'), (3, 'Region');
        INSERT INTO [Column] VALUES (10, 1, 'Quantity', NULL, 1, NULL), (11, 1, 'TotalPrice', NULL, 2, 'Sales[Quantity] * Sales[UnitPrice]'),
            (12, 1, 'DiscountedPrice', NULL, 2, NULL), (13, 1, 'RowNumber', NULL, 3, NULL), (14, 1, 'RegionID', NULL, 1, NULL),
            (30, 3, 'RegionID', NULL, 1, NULL), (20, 2, 'Year', NULL, 2, 'YEAR([Date])');
        INSERT INTO [Measure] VALUES (1, 1, 'Online Sales', 'CALCULATE(SUM(Sales[Amount]), Sales[Channel] = "Online")', NULL),
            (2, 1, 'YTD Sales', NULL, NULL);
        INSERT INTO [Partition] VALUES (1, 1, 4, 'let Source = Csv.Document(File.Contents("sales.csv")) in Source');
        INSERT INTO [Relationship] VALUES (14, 30, 1, 1);
    """)
    db.commit()
    db.close()
    data = open(handle.name, "rb").read()
    os.unlink(handle.name)
    return data


def test_a_power_bi_model_shows_its_dax_and_what_is_missing(monkeypatch):
    model = wd._model_from_sqlite(_sqlite_model())
    monkeypatch.setattr(wd, "_xpress9_model", lambda raw: raw)
    monkeypatch.setattr(wd, "_model_sqlite", lambda raw: _sqlite_model())
    layout = {"sections": [{"displayName": "Overview", "visualContainers": [
        {"config": json.dumps({"singleVisual": {"visualType": "clusteredBarChart",
                                                "projections": {"Category": [{"queryRef": "Sales.Region"}],
                                                                "Y": [{"queryRef": "Sales.Online Sales"}]}}})}]}]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("DataModel", b"model")
        z.writestr("Report/Layout", json.dumps(layout).encode("utf-16le"))
    text = _text(wd.build_document(cover_pdf=None, files=[lw.LinkedFile("Assignment.pbix", buf.getvalue(), "")],
                                   problems=[]))
    assert "Table Sales" in text and "LocalDateTable" not in text      # Power BI's own date tables are left out
    assert "Calculated column TotalPrice = Sales[Quantity] * Sales[UnitPrice]" in text
    assert "Calculated column DiscountedPrice = (no formula saved in the file)" in text
    assert 'Measure Online Sales = CALCULATE(SUM(Sales[Amount]), Sales[Channel] = "Online")' in text
    assert "Measure YTD Sales = (no formula saved in the file)" in text
    assert "Power Query (M):" in text and "Sales[RegionID] -> Region[RegionID]" in text
    assert "Page 'Overview': clusteredBarChart: Sales.Region, Sales.Online Sales" in text
    assert model["tables"]["Sales"]["columns"] == ["Quantity", "RegionID"]   # no internal RowNumber


def test_a_power_bi_template_reads_its_schema():
    schema = {"model": {"tables": [{"name": "Orders", "columns": [
        {"name": "Qty"}, {"name": "Total", "type": "calculated", "expression": "Orders[Qty] * 2"}],
        "measures": [{"name": "Total Qty", "expression": ["SUM(", "Orders[Qty])"]}]}]}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("DataModelSchema", json.dumps(schema).encode("utf-16le"))
    body, _, _ = wd.powerbi_html(buf.getvalue())
    assert "Total</b> =</p><pre>Orders[Qty] * 2" in body and "SUM(Orders[Qty])" in body


def test_a_tableau_workbook_shows_calculations_sheets_and_dashboards():
    twb = """<workbook><datasources><datasource caption='Superstore'>
      <connection class='excel-direct' filename='Superstore.xlsx'/>
      <column caption='Profit Ratio' name='[Calculation_1]'><calculation class='tableau' formula='SUM([Profit])/SUM([Sales])'/></column>
      </datasource></datasources>
      <worksheets><worksheet name='Sales by Region'><table><view>
        <filter class='quantitative' column='[federated.1n].[none:Order_Date:qk]' included-values='in-range'>
          <min>#2024-03-01#</min><max>#2024-09-30#</max></filter>
        <filter class='categorical' column='[federated.1n].[none:Segment:nk]'>
          <groupfilter function='union'><groupfilter function='member' member='"Consumer"'/></groupfilter></filter>
        <shelf-sort-v2 dimension-to-sort='[federated.1n].[none:Region:nk]' direction='DESC'
          measure-to-sort-by='[federated.1n].[sum:Sales:qk]' shelf='rows'/></view>
        <style><style-rule element='mark'><format attr='mark-color' value='#f28e2b'/>
          <format attr='mark-labels-show' value='true'/></style-rule></style>
        <rows>[federated.1n].[none:Region:nk]</rows><cols>[federated.1n].[sum:Sales:qk]</cols>
      <panes><pane><mark class='Bar'/><encodings><color column='[federated.1n].[ctd:Customer_ID:qk]'/></encodings>
      </pane></panes></table></worksheet></worksheets>
      <dashboards><dashboard name='Overview'><zones><zone name='Sales by Region'/>
        <zone type-v2='filter' param='[federated.1n].[none:Segment:nk]' name='Sales by Region'/></zones></dashboard>
        <dashboard name='Story 1' type='storyboard'><zones><zone><flipboard><story-points>
          <story-point caption='Sales by region' captured-sheet='Sales by Region' id='1'/>
        </story-points></flipboard></zone></zones></dashboard></dashboards>
    </workbook>"""
    text = _text(wd.build_document(cover_pdf=None, files=[lw.LinkedFile("viz.twb", twb.encode(), "")], problems=[]))
    assert "Superstore (Superstore.xlsx)" in text and "Calculated field Profit Ratio = SUM([Profit])/SUM([Sales])" in text
    # Feedback sheet, 23 Oct 2026: the date range, mark colour and story point were in
    # the .twb but did not reach the grader, so three remarks called them missing.
    assert "Sales by Region Rows: Region Columns: SUM(Sales) Mark type: Bar" in text
    assert "On the Marks card: color: COUNTD(Customer_ID)" in text
    assert "mark colour set to #f28e2b; mark labels shown" in text
    assert "Filters: Order_Date from 2024-03-01 to 2024-09-30; Segment keeping Consumer" in text
    assert "Sort: Region sorted by SUM(Sales) descending" in text
    assert "Overview: sheets Sales by Region Controls on the dashboard: filter for Segment" in text
    assert "Story 1: 1 story point(s) Story point 1: “Sales by region” showing Sales by Region" in text


def test_a_word_document_keeps_text_tables_and_pictures():
    import docx
    document = docx.Document()
    document.add_heading("Question 1", level=1)
    document.add_paragraph("A list is mutable.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(1, 1).text = "Type", "tuple"
    document.add_picture(io.BytesIO(_png()))
    buf = io.BytesIO()
    document.save(buf)
    pdf = wd.build_document(cover_pdf=None, files=[lw.LinkedFile("answers.docx", buf.getvalue(), "")], problems=[])
    text = _text(pdf)
    assert "Question 1" in text and "A list is mutable." in text and "Type" in text and "tuple" in text
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert sum(len(page.get_images()) for page in doc) == 1


def test_a_linked_pdf_is_kept_as_it_is_and_the_document_is_capped():
    linked = fitz.open()
    for n in range(40):
        linked.new_page().insert_text((72, 72), f"answer page {n + 1}")
    item = lw.LinkedFile("exam 3.pdf", linked.tobytes(), "https://drive.google.com/file/d/x/view")
    pdf = wd.build_document(cover_pdf=_pdf(["Name: A Learner"]), files=[item],
                            problems=[("https://github.com/x/y", "the link leads to a page that does not exist (404)")])
    text = _text(pdf)
    assert text.startswith("Name: A Learner") and "Linked work 1 of 1: exam 3.pdf" in text
    assert "answer page 1" in text and f"answer page {wd.MAX_PDF_PAGES}" in text
    assert f"answer page {wd.MAX_PDF_PAGES + 1} " not in text + " "
    assert "Links that could not be opened" in text and "does not exist (404)" in text


# --- the submission ------------------------------------------------------------------------

def test_a_cover_pdf_gains_its_linked_work(monkeypatch):
    notebook = {"cells": [{"cell_type": "code", "source": "print('hi')", "execution_count": 1,
                           "outputs": [{"output_type": "stream", "text": "hi\n"}]}]}
    monkeypatch.setattr(lw, "open_link", lambda url, budget=None: [
        lw.LinkedFile("work.ipynb", json.dumps(notebook).encode(), url)])
    cover = _pdf(["Name: A Learner", "Github Link: CODE"], link="https://github.com/o/r/blob/main/work.ipynb")
    prepared = lw.prepare_submission(cover, "6abca7b7.pdf")
    assert prepared.converted and prepared.link_only and not prepared.problems
    assert prepared.filename == "6abca7b7.pdf"
    text = _text(prepared.data)
    assert text.startswith("Name: A Learner") and "print('hi')" in text and lw.unopened_reason(prepared) == ""


def test_a_cover_whose_links_cannot_be_opened_names_why(monkeypatch):
    def refuse(url, budget=None):
        raise lw.LinkError("the link is not shared publicly (it asks to sign in)")
    monkeypatch.setattr(lw, "open_link", refuse)
    cover = _pdf(["Name: A Learner"], link="https://drive.google.com/file/d/1abcdefghijklmnop/view")
    prepared = lw.prepare_submission(cover, "cover.pdf")
    reason = lw.unopened_reason(prepared)
    assert "could not be opened" in reason and "not shared publicly" in reason and "No marks were given" in reason


def test_typed_pdfs_without_links_and_unknown_files_are_left_alone():
    typed = _pdf(["Question 1 : What is CSS?", "Answer: a style sheet language."] * 30)
    assert lw.prepare_submission(typed, "a.pdf").data == typed
    assert not lw.prepare_submission(b"\x00\x01binary", "video.mp4").converted


def test_a_direct_upload_and_a_file_of_links_are_converted(monkeypatch):
    nb = json.dumps({"cells": [{"cell_type": "markdown", "source": "# Answers"}]}).encode()
    prepared = lw.prepare_submission(nb, "learner.ipynb")
    assert prepared.converted and prepared.filename == "learner.ipynb.pdf" and "Answers" in _text(prepared.data)
    monkeypatch.setattr(lw, "open_link", lambda url, budget=None: [lw.LinkedFile("a.py", b"print(1)", url)])
    prepared = lw.prepare_submission(b"[InternetShortcut]\nURL=https://github.com/o/r/blob/main/a.py\n", "link.url")
    assert prepared.converted and "print(1)" in _text(prepared.data)


# --- grading, the server and the form -------------------------------------------------------

def test_grading_holds_a_cover_whose_links_cannot_be_opened(monkeypatch):
    import dataclasses
    import pipeline
    from strict_grading import StrictGradeError

    def refuse(url, budget=None):
        raise lw.LinkError("the link is not shared publicly (it asks to sign in)")
    monkeypatch.setattr(lw, "open_link", refuse)
    cfg = dataclasses.replace(pipeline.cfg_from_request("claude", "", "Excel", google_token="", task_id="",
                                                        category="innovation"), paper_check=True)
    cover = _pdf(["Name: A Learner"], link="https://docs.google.com/spreadsheets/d/1JL4XLW6caGUa27PK70ZpU/edit")
    with pytest.raises(StrictGradeError) as err:
        pipeline.grade_sheet(qp_pngs=[], sa_bytes=cover, filename="cover.pdf", ak_pngs=None, ak_text=None,
                             marks_scheme=None, cfg=cfg)
    assert err.value.code == "NOT_VERIFIED_ANSWER_SHEET" and "not shared publicly" in str(err.value)


def test_the_server_takes_other_formats_for_innovation_only():
    import server
    from fastapi import HTTPException
    notebook = b'{"cells": []}'
    server._validate_document("a.ipynb", notebook, "Answer", extensions=server._sheet_extensions("innovation"))
    server._validate_document("a.pbix", b"PK\x03\x04data", "Answer", extensions=server._sheet_extensions("innovation"))
    with pytest.raises(HTTPException, match="not really a PBIX"):
        server._validate_document("a.pbix", b"not a zip", "Answer", extensions=server._sheet_extensions("innovation"))
    with pytest.raises(HTTPException, match="must be PDF, PNG, JPG, or JPEG"):
        server._validate_document("a.ipynb", notebook, "Answer", extensions=server._sheet_extensions("school"))
    with pytest.raises(HTTPException, match="must be PDF, PNG, JPG, or JPEG"):
        server._validate_document("a.ipynb", notebook, "Answer")
    assert server._sheet_extensions("") == server._sheet_extensions("school") == server._DOCUMENT_EXTENSIONS


def test_folder_mode_lists_other_formats_only_when_asked(tmp_path):
    import bulk
    import server
    for name in ("a.pdf", "b.ipynb", "c.xlsx", "d.mp4"):
        (tmp_path / name).write_bytes(b"x")
    out = tmp_path / "out"
    assert bulk.list_sheets(tmp_path, out) == ["a.pdf"]
    assert bulk.list_sheets(tmp_path, out, server._sheet_extensions("innovation")) == ["a.pdf", "b.ipynb", "c.xlsx"]


def test_the_form_follows_the_category():
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    assert "c.linked_work" in js and '$("saFiles").accept = sheetAccept().join(",")' in js
    assert "if (!sheetAccept().some(ext => f.name.toLowerCase().endsWith(ext)))" in js
    spec = open(os.path.join(ROOT, "AICheckerClient.spec"), encoding="utf-8").read()
    assert "'linked_work', 'work_document', 'docx', 'xpress9'" in spec
    smoke = open(os.path.join(ROOT, "tools", "smoke_release.py"), encoding="utf-8").read()
    assert "'linked_work','work_document'" in smoke


def test_a_short_cover_does_not_place_answers_and_question_sheets_do():
    """A learner's cover listed "Q2 ... Q14" as a table of contents for the workbook it
    linked (sample DA_sheet1, 21 Oct 2026). Those lines would have placed every answer
    on the cover; the sheets named after questions carry the real places."""
    import openpyxl
    from answer_map import build_answer_map
    from test_innovation_category import _typed_package
    prepared = lw.Prepared(b"", "x.pdf", converted=True, files=[lw.LinkedFile("a.xlsx", b"", "")], cover_pages=1)
    assert prepared.label_free_pages == frozenset({1})
    assert lw.Prepared(b"", "x.pdf", cover_pages=1).label_free_pages == frozenset()          # nothing linked
    assert lw.Prepared(b"", "x.pdf", files=[1], cover_pages=8).label_free_pages == frozenset()  # a full answer PDF
    wb = openpyxl.Workbook()
    wb.active.title = "Q1"
    for name in ("Q2", "Q3_Electronics", "Notes"):
        wb.create_sheet(name)["A1"] = "data"
    buf = io.BytesIO()
    wb.save(buf)
    body, _, _ = wd.workbook_html(buf.getvalue())
    assert "<h2>Q3: sheet Q3_Electronics (sheet 3 of 4)</h2>" in body and "<h2>Sheet 4 of 4: Notes</h2>" in body
    cover = PageOCR_page(1, ["Name: A Learner", "Q2", "Q3", "Q4"])
    work = PageOCR_page(2, ["Q2: sheet Q2 (sheet 2 of 5)", "=SUM(F2:F401)", "Q3: sheet Q3 (sheet 3 of 5)", "=IF(F2>=15)"])
    package = _typed_package(count=4)
    plain = {loc.qid: loc for loc in build_answer_map(package, [cover, work], typed=True).locations}
    assert plain["Q2"].label_line_ids[0] == "P1L2"                     # the cover's list took Q2
    mapped = {loc.qid: loc for loc in build_answer_map(package, [cover, work], typed=True,
                                                        label_free_pages=frozenset({1})).locations}
    assert mapped["Q2"].label_line_ids == ("P2L1",) and mapped["Q3"].label_line_ids == ("P2L3",)
    src = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
    assert "label_free_pages=label_free_pages" in src and "label_free_pages = prepared.label_free_pages" in src


def PageOCR_page(number, texts):
    from mathpix import LineBox, PageOCR
    return PageOCR(page=number, image_width=1240, image_height=1754, lines=[
        LineBox(line_id=f"P{number}L{i}", text=t, bbox=(100, 60 + 40 * i, 900, 90 + 40 * i), page=number,
                printed=True) for i, t in enumerate(texts, 1)])


# --- the team's feedback sheet and "All format sample" tab (22 Oct 2026) ------------------

def test_a_download_without_an_extension_is_named_by_its_content():
    notebook = json.dumps({"nbformat": 4, "cells": [{"cell_type": "markdown", "source": "# Q1"}]}).encode()
    assert lw._named("Excel Basic", notebook) == "Excel Basic.ipynb"
    book = io.BytesIO()
    with zipfile.ZipFile(book, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    assert lw._named("sales", book.getvalue()) == "sales.xlsx"
    assert lw._named("report.pdf", b"%PDF-1.4") == "report.pdf"
    assert lw._named("notes", b"plain words") == "notes"


def test_a_colab_notebook_kept_in_drive_is_read(monkeypatch):
    notebook = {"nbformat": 4, "cells": [{"cell_type": "markdown",
                                          "source": "1. Explain relative referencing"}]}
    monkeypatch.setattr(lw, "_get", lambda target, budget, accept_html=False: (
        json.dumps(notebook).encode(), {"content-disposition": 'attachment; filename="Excel Basic"'},
        target))
    files = lw.open_link("https://colab.research.google.com/drive/1MpHqIyYPwN0IE1ef6WBGi1BDUTcI_lAE")
    assert files[0].name == "Excel Basic.ipynb"
    document = wd.build_document(cover_pdf=None, files=files, problems=[])
    assert "relative referencing" in _text(document) and "does not read" not in _text(document)


def test_a_video_is_listed_for_the_reviewer_and_never_downloaded(monkeypatch):
    import requests

    class Response:
        status_code = 200
        headers = {"Content-Type": "video/quicktime",
                   "Content-Disposition": 'attachment; filename="WEEK 6 SEO.mov"'}

        def iter_content(self, size):
            raise AssertionError("the video was downloaded")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: Response())
    with pytest.raises(lw.VideoLink, match="WEEK 6 SEO.mov"):
        lw.open_link("https://drive.google.com/file/d/1YnK6NsTRVuuEv8DtVut5qiqvgYWDsAkw/view")
    with pytest.raises(lw.VideoLink, match="does not watch videos"):
        lw.open_link("https://www.youtube.com/watch?v=abc")
    assert lw.is_video_link("https://youtu.be/abc") and lw.is_video_link("https://example.com/demo.mp4")
    assert not lw.is_video_link("https://github.com/o/r/blob/main/demo.ipynb")


def test_a_video_in_a_drive_folder_is_named_for_the_reviewer(monkeypatch):
    listing = ('<a href="https://drive.google.com/file/d/1PH4o7oPehNn0TRL2vYEIHxRRfX3ehiUw/view">'
               '<div class="flip-entry-title">answers.pdf</div></a>'
               '<a href="https://drive.google.com/file/d/1ZZZZZZZZZZZZZZZZZZZZZ/view"><div class="flip-entry-title">'
               'summary.mp4</div></a>').encode()

    def fake_get(target, budget, accept_html=False):
        if "embeddedfolderview" in target:
            return listing, {"content-type": "text/html"}, target
        return b"%PDF-1.4 answers", {}, target
    monkeypatch.setattr(lw, "_get", fake_get)
    files, problems = lw.open_links(["https://drive.google.com/drive/folders/1xYbPb1s6zb9DPVyepYYwW5XhYr3HZx6A"])
    assert [f.name for f in files] == ["answers.pdf"]
    assert len(problems) == 1 and "summary.mp4" in problems[0][1] and problems[0][1].startswith(lw.VIDEO_REASON)


def _screenshot_pdf(link):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "WEEK 6 TECHNICAL SEO AND LOCAL SEO", fontsize=11)
    page.insert_image(fitz.Rect(72, 100, 520, 500), stream=_png())
    page.insert_link({"kind": fitz.LINK_URI, "uri": link, "from": fitz.Rect(72, 60, 300, 75)})
    return doc.tobytes()


def test_screenshot_pages_with_a_video_are_marked_and_the_video_flagged(monkeypatch):
    def video(url, budget=None):
        raise lw.VideoLink("Shopify Project Record.mp4")
    monkeypatch.setattr(lw, "open_link", video)
    link = "https://drive.google.com/file/d/1CdvGGEKJCf6Aluk5MxZTk4IwRxaAGRrv/view"
    prepared = lw.prepare_submission(_screenshot_pdf(link), "dm.pdf")
    assert not prepared.link_only and lw.unopened_reason(prepared) == ""      # the screenshots are answers
    assert prepared.videos == [link]
    notes = lw.review_notes(prepared)
    assert len(notes) == 1 and "video was not watched" in notes[0] and link in notes[0]
    cover = _pdf(["Name: A Learner", "Video: LINK"], link="https://youtu.be/abc")
    reason = lw.unopened_reason(lw.prepare_submission(cover, "cover.pdf"))
    assert "does not watch videos" in reason and "No marks were given" in reason
    src = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
    assert "linked_notes = review_notes(prepared)" in src


def test_innovation_rules_carry_the_sme_review_of_22_october():
    from categories.innovation import INNOVATION_RULES
    for phrase in ("Count what each question asks for", "earns at most half",
                   "An answer can sit away from its heading", "refer sheet 2",
                   'A link listed as "a\n   video" was not watched', "column of computed"):
        assert phrase in INNOVATION_RULES, phrase


def test_a_full_mark_assignment_gets_an_appreciation_cover(monkeypatch):
    import grader
    report = grader.GradeReport.model_validate({
        "student_name": "", "total_score": 40, "max_total": 40, "overall_remarks": "",
        "questions": [{"qid": f"Q{i}", "score": 20, "max_score": 20, "page": 1, "y_fraction": 0.1,
                       "remark": "", "criteria": [{"description": f"Grid layout {i}", "awarded": 20,
                                                   "max": 20, "met": True}]} for i in (1, 2)]})
    asked = {}

    def fake_call(token, **kwargs):
        asked["text"] = kwargs["request"]["messages"][0]["content"][0]["text"]
        raise RuntimeError("offline")
    monkeypatch.setattr(grader, "_claude_call", fake_call)
    kwargs = dict(model="m", google_token="", task_id="", filename="a.pdf", subject="CSS", usage={})
    assert grader._compose_overall_remarks(report, **kwargs) == ""           # other papers: unchanged
    cover = grader._compose_overall_remarks(report, assignment=True, **kwargs)
    assert cover.startswith("Well done") and ";" not in cover
    # Every question with what earned its marks, so the praise is about THIS copy (two
    # learners got the same cover text on 23 Oct 2026).
    assert "Every mark was earned" in asked["text"] and "Question by question" in asked["text"]
    assert "Q1 20/20 - earned: Grid layout 1" in asked["text"] and "Q2 20/20 - earned: Grid layout 2" in asked["text"]
    assert "never claim work a line shows as missing" in asked["text"]
    src = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
    assert 'assignment = _category_of(cfg) == "innovation"' in src


def test_one_notebook_linked_twice_is_read_once_with_its_outputs(monkeypatch):
    cells = [{"cell_type": "code", "source": "print(1)", "execution_count": 1, "outputs": []}]
    bare = {"nbformat": 4, "cells": cells}
    run = {"nbformat": 4, "metadata": {"kernelspec": {"name": "python3"}},
           "cells": [dict(cells[0], outputs=[{"output_type": "stream", "text": "1\n"}])]}
    copies = {"drive": json.dumps(bare).encode(), "github": json.dumps(run).encode()}
    monkeypatch.setattr(lw, "open_link", lambda url, budget=None: [
        lw.LinkedFile("Assignment.ipynb", copies["drive" if "drive" in url else "github"], url)])
    files, problems = lw.open_links(["https://drive.google.com/drive/folders/12DJG2N8FVQ-iGyMXnuVBg",
                                     "https://github.com/o/r/blob/main/Assignment.ipynb"])
    assert len(files) == 1 and files[0].data == copies["github"] and not problems


def test_a_plotly_chart_saved_as_html_is_summarised_not_dumped():
    """Feedback sheet, 23 Oct 2026: a px.bar output saved only as HTML reached the grader
    as a page and a half of JavaScript."""
    markup = ('<div id="a1"></div><script type="text/javascript">window.PLOTLYENV=window.PLOTLYENV || {};'
              'if (document.getElementById("a1")) { Plotly.newPlot( "a1", [{"type": "bar", "name": "Sales",'
              ' "x": ["A", "B"], "y": [1, 2]}], {"title": {"text": "Sales by product"}, "xaxis": '
              '{"title": {"text": "Product"}}}, {"responsive": true}) };</script>')
    notebook = {"nbformat": 4, "cells": [{"cell_type": "code", "source": "px.bar(df)", "execution_count": 3,
                "outputs": [{"output_type": "display_data", "data": {"text/html": markup}}]}]}
    text = _text(wd.build_document(cover_pdf=None, files=[lw.LinkedFile("nb.ipynb", json.dumps(notebook).encode(), "")],
                                   problems=[]))
    assert "Interactive Plotly chart (not rendered): bar 'Sales'; title 'Sales by product'; xaxis: Product" in text
    assert "PLOTLYENV" not in text and "getElementById" not in text
    widget = {"nbformat": 4, "cells": [{"cell_type": "code", "source": "w", "execution_count": 4,
              "outputs": [{"output_type": "display_data", "data": {"text/html": "<script>var x = 1;</script>"}}]}]}
    text = _text(wd.build_document(cover_pdf=None, files=[lw.LinkedFile("w.ipynb", json.dumps(widget).encode(), "")],
                                   problems=[]))
    assert "Interactive chart or widget (not rendered)" in text and "var x" not in text



def test_a_copy_full_of_work_that_earns_almost_nothing_is_flagged():
    """23 Oct 2026: a Statistics Basics copy linked another assignment's notebook and scored
    0 of 200 with no note saying so."""
    import grader
    import pipeline
    page = PageOCR_page(1, ["A random variable assigns a number to each outcome of an experiment"] * 40)
    report = grader.GradeReport.model_validate({"student_name": "", "total_score": 0, "max_total": 200,
                                                "overall_remarks": "", "questions": []})
    note = pipeline._wrong_work_note(report, [page])
    assert "may be another assignment or the wrong file" in note and "0 of 200" in note
    report.total_score = 150
    assert pipeline._wrong_work_note(report, [page]) == ""
    report.total_score = 0
    assert pipeline._wrong_work_note(report, [PageOCR_page(1, ["Name: A Learner"])]) == ""   # a bare cover
    src = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
    assert "linked_notes.append(_wrong_work_note(" in src


def test_innovation_rules_of_23_october():
    from categories.innovation import INNOVATION_RULES as rules
    for phrase in ("Steps that say how to do it", "a generic recipe", "a wrong stated result",
                   "Code that would not run", "A project brief lists deliverables",
                   "answers a different assignment"):
        assert phrase in rules, phrase
