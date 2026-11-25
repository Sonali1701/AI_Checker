# -*- coding: utf-8 -*-
"""A learner's files as one PDF the checker can read and mark (School of Innovation).

Every file becomes typed pages under a heading that names it and says how it was
read, so the grader (and the reviewer looking at the marked copy) can tell the
learner's work from the checker's notes:

- Jupyter notebook: markdown, code and the outputs the learner SAVED, plots included.
  Nothing is run: a cell saved without output is marked on its code.
- Excel workbook: per sheet, the settings questions ask about (frozen panes, filters
  and their criteria, sorting, header formatting, column widths, conditional
  formatting, validation, tables), every formula pattern by column with how many
  cells use it, the first rows of values, pivot tables and charts.
- CSV: the first rows.
- Power BI (.pbix / .pbit): the data model (tables, calculated columns, measures and
  calculated tables with their DAX, Power Query steps, relationships) and the report
  pages with each visual's type, title and fields.
- Tableau (.twb / .twbx): data sources, calculated fields with their formulas,
  worksheets with their shelves, mark types and filters, dashboards.
- Word (.docx): paragraphs, tables and pictures in order.
- PDF and images: as they are. Code and text files: as code.

Imports nothing from pipeline, grader or server.
"""
from __future__ import annotations

import base64
import html
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_DOCUMENT_PAGES = 36        # converted pages kept (typed assignments run to 10-20)
MAX_PDF_PAGES = 24             # pages taken from one linked PDF
MAX_CODE_LINES = 300           # lines shown from one code file
MAX_CELL_LINES = 150           # lines shown from one notebook cell
MAX_OUTPUT_CHARS = 5000        # characters shown from one notebook output
SHEET_PREVIEW_ROWS = 20
SHEET_PREVIEW_COLS = 14

_CSS = """
body { font-family: sans-serif; font-size: 9.5pt; line-height: 1.3; }
h1 { font-size: 13pt; margin: 0 0 4pt 0; }
h2 { font-size: 11.5pt; margin: 10pt 0 3pt 0; }
h3 { font-size: 10.5pt; margin: 8pt 0 2pt 0; }
p { margin: 2pt 0; }
.note { font-size: 8.5pt; color: #555555; }
pre { font-family: monospace; font-size: 8pt; white-space: pre-wrap;
      background-color: #f4f4f4; margin: 2pt 0 4pt 0; padding: 2pt; }
pre.out { background-color: #ffffff; border-left: 1pt solid #999999; }
table { border-collapse: collapse; font-size: 8pt; margin: 2pt 0 6pt 0; }
td, th { border: 0.5pt solid #888888; padding: 1pt 3pt; }
th { background-color: #e8e8e8; }
"""

_KINDS = {
    ".ipynb": "Jupyter notebook", ".xlsx": "Excel workbook", ".xlsm": "Excel workbook",
    ".csv": "CSV data", ".pbix": "Power BI report", ".pbit": "Power BI template",
    ".twb": "Tableau workbook", ".twbx": "Tableau packaged workbook", ".docx": "Word document",
    ".pdf": "PDF document", ".png": "image", ".jpg": "image", ".jpeg": "image",
}
_CODE_EXTS = (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss", ".sql",
              ".java", ".c", ".cpp", ".cs", ".go", ".rb", ".php", ".r", ".sh", ".md", ".txt",
              ".json")


def _ext(name: str) -> str:
    name = str(name or "").lower()
    return name[name.rfind("."):] if "." in name else ""


def readable_kind(filename: str, data: bytes) -> str:
    """What the checker reads this file as, or "" when it is not a kind it converts."""
    ext = _ext(filename)
    if ext in _KINDS:
        return _KINDS[ext]
    if ext in _CODE_EXTS:
        return "code"
    return ""


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


# --- writing HTML to PDF ------------------------------------------------------------------

# Put between two parts of a body to start the second on a new page (one sheet per page).
NEW_PAGE = "<!--new page-->"


def _html_pdf(body: str, images: dict[str, bytes] | None = None) -> bytes:
    """The body laid out on A4 pages; each part between NEW_PAGE markers starts a page.

    MuPDF's story can place a block it will not split (a table that meets the end of the
    page) BELOW the page's bottom edge and then carry on from the next block, so whatever
    it put there was drawn off the page and lost. MEASURED 13 Nov 2026: a learner's
    17-sheet workbook lost sheets 8 and 17 that way (Q7 marked 0 for "no Priority Order
    formula" with the formula in all 400 rows), another lost sheets 2 and 3. A page whose
    content runs past its end is now made tall enough to show all of it.

    A single block taller than a page that MuPDF will not split (one huge table cell) is
    never placed at all: every page comes back empty. Two empty pages in a row put the rest
    on one tall page instead of dropping it."""
    import fitz
    archive = fitz.Archive()
    for name, data in (images or {}).items():
        archive.add((data, name))
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    page = fitz.paper_rect("a4")
    where = page + (36, 36, -36, -36)
    tallest = fitz.Rect(where.x0, where.y0, where.x1, where.y0 + _TALLEST_PAGE)
    pages = 0
    for part in [part for part in body.split(NEW_PAGE) if part.strip()] or [body]:
        story = fitz.Story(html=part, user_css=_CSS, archive=archive)
        more, empty_before = True, False
        while more and pages < MAX_DOCUMENT_PAGES:
            more, filled = story.place(where)
            filled = fitz.Rect(filled)
            empty = bool(more) and filled.y1 - where.y0 < 30
            if empty and empty_before:
                more, filled = story.place(tallest)     # placed again, now with room for it
                filled, empty = fitz.Rect(filled), False
            empty_before = empty
            size = page if filled.y1 <= where.y1 + 0.5 else fitz.Rect(page.x0, page.y0, page.x1, filled.y1 + 36)
            device = writer.begin_page(size)
            story.draw(device)
            writer.end_page()
            pages += 1
    writer.close()
    return buf.getvalue()


_TALLEST_PAGE = 14000      # points; a PDF page may be up to 14,400


def _header(index: int, count: int, name: str, kind: str, url: str, note: str) -> str:
    source = f"<p class='note'>From the learner's link: {_e(url)}</p>" if url else ""
    return (f"<h1>Linked work {index} of {count}: {_e(name)}</h1>"
            f"<p class='note'>{_e(kind)}. {_e(note)}</p>{source}")


# --- Jupyter notebooks --------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _joined(value) -> str:
    return "".join(value) if isinstance(value, list) else str(value or "")


def _clip_lines(text: str, limit: int) -> str:
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    return "\n".join(lines[:limit]) + f"\n... ({len(lines) - limit} more lines not shown)"


def _markdown(text: str) -> str:
    """A small markdown subset: headings, lists, fenced code, bold and inline code."""
    out, para, in_code, code = [], [], False, []

    def inline(value: str) -> str:
        value = _e(value)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)

    def flush():
        if para:
            out.append("<p>" + "<br/>".join(inline(line) for line in para) + "</p>")
            para.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre>" + _e("\n".join(code)) + "</pre>")
                code.clear()
            else:
                flush()
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        heading = re.match(r"\s*(#{1,6})\s+(.*)", line)
        if heading:
            flush()
            level = min(3, len(heading.group(1)) + 1)
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif not line.strip():
            flush()
        else:
            para.append(line)
    if code:
        out.append("<pre>" + _e("\n".join(code)) + "</pre>")
    flush()
    return "".join(out)


def _plotly_summary(figure) -> str:
    """An interactive Plotly chart as text: its traces and titles (no picture is saved)."""
    if isinstance(figure, str):
        try:
            figure = json.loads(figure)
        except json.JSONDecodeError:
            return "Interactive Plotly chart (not rendered)."
    traces = []
    for trace in (figure or {}).get("data", [])[:8]:
        kind = trace.get("type", "scatter")
        mode = trace.get("mode")
        name = trace.get("name")
        traces.append(f"{kind}" + (f" ({mode})" if mode else "") + (f" '{name}'" if name else ""))
    layout = (figure or {}).get("layout", {}) or {}

    def title(value):
        if isinstance(value, dict):
            return str(value.get("text") or "")
        return str(value or "")
    axes = [f"{axis}: {title((layout.get(axis) or {}).get('title'))}"
            for axis in ("xaxis", "yaxis") if title((layout.get(axis) or {}).get("title"))]
    return ("Interactive Plotly chart (not rendered): "
            + (", ".join(traces) or "no traces")
            + (f"; title '{title(layout.get('title'))}'" if title(layout.get("title")) else "; no title")
            + (f"; {'; '.join(axes)}" if axes else ""))


_NEWPLOT_RE = re.compile(r"Plotly\.newPlot\(\s*(['\"])[^'\"]*\1\s*,\s*")


def _html_output(markup: str) -> str:
    """The text of a notebook's HTML output. A Plotly chart saved as HTML (its figure
    inside Plotly.newPlot) becomes the chart's summary, and scripts and styles are
    dropped. MEASURED 23 Oct 2026: a learner's px.bar output reached the grader as a
    page and a half of raw JavaScript."""
    found = _NEWPLOT_RE.search(markup)
    if found:
        decoder = json.JSONDecoder()
        try:
            traces, end = decoder.raw_decode(markup, found.end())
            comma = re.match(r"\s*,\s*", markup[end:])
            layout = decoder.raw_decode(markup, end + comma.end())[0] if comma else {}
            return _plotly_summary({"data": traces, "layout": layout})
        except (ValueError, AttributeError):
            return "Interactive Plotly chart (not rendered)."
    cleaned = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", markup)
    text = re.sub(r"<[^>]+>", " ", cleaned)
    if "<script" in markup.lower() and not text.strip():
        return "Interactive chart or widget (not rendered)."
    return text


def notebook_html(data: bytes) -> tuple[str, dict[str, bytes], str]:
    """(body, images, note) for a notebook."""
    notebook = json.loads(data.decode("utf-8-sig"))
    cells = notebook.get("cells")
    if cells is None:                                   # nbformat 3
        cells = [cell for sheet in notebook.get("worksheets", []) for cell in sheet.get("cells", [])]
    language = (((notebook.get("metadata") or {}).get("kernelspec") or {}).get("language")
                or ((notebook.get("metadata") or {}).get("language_info") or {}).get("name") or "")
    body, images = [], {}
    code_cells = with_output = errors = 0
    for cell in cells:
        kind = cell.get("cell_type")
        source = _joined(cell.get("source", cell.get("input", "")))
        if kind == "markdown":
            body.append(_markdown(source))
            continue
        if kind != "code":
            if source.strip():
                body.append("<pre>" + _e(_clip_lines(source, MAX_CELL_LINES)) + "</pre>")
            continue
        code_cells += 1
        count = cell.get("execution_count", cell.get("prompt_number"))
        label = f"In [{count}]:" if count is not None else "In [ ]:"
        body.append(f"<p class='note'>{label}</p><pre>{_e(_clip_lines(source, MAX_CELL_LINES))}</pre>")
        outputs = cell.get("outputs") or []
        if outputs:
            with_output += 1
        for output in outputs:
            otype = output.get("output_type")
            if otype == "error":
                errors += 1
                trace = _ANSI.sub("", "\n".join(output.get("traceback") or []))
                text = f"{output.get('ename', 'Error')}: {output.get('evalue', '')}\n{trace}"
                body.append("<p class='note'>Error output:</p><pre class='out'>"
                            + _e(_clip_lines(text, 25)[:MAX_OUTPUT_CHARS]) + "</pre>")
                continue
            if otype == "stream":
                text = _joined(output.get("text"))
                body.append("<pre class='out'>" + _e(_clip_lines(text, 60)[:MAX_OUTPUT_CHARS]) + "</pre>")
                continue
            bundle = output.get("data") or {}
            plotly = bundle.get("application/vnd.plotly.v1+json")
            if plotly is not None:
                body.append("<pre class='out'>" + _e(_plotly_summary(plotly)) + "</pre>")
                continue
            for mime, ext in (("image/png", "png"), ("image/jpeg", "jpg")):
                if mime in bundle:
                    name = f"nb{len(images) + 1}.{ext}"
                    try:
                        images[name] = base64.b64decode(_joined(bundle[mime]))
                        body.append(f"<p><img src='{name}' width='420'/></p>")
                    except Exception:  # noqa: BLE001
                        pass
                    break
            else:
                text = _joined(bundle.get("text/plain"))
                if not text and "text/html" in bundle:
                    text = _html_output(_joined(bundle["text/html"]))
                if len(text) > 1500 and text.lstrip()[:1] in "{[" and text.count('":') > 40:
                    # A chart's or widget's raw data written out as text (a Plotly
                    # figure's template ran to two pages on a learner's notebook).
                    text = text[:300] + f"\n... (a chart or widget's raw data, {len(text)} characters, not shown)"
                if text.strip():
                    body.append("<pre class='out'>" + _e(_clip_lines(text, 60)[:MAX_OUTPUT_CHARS])
                                + "</pre>")
    note = (f"Read from the notebook file as the learner saved it ({language or 'unknown'} "
            f"kernel, {code_cells} code cells, {with_output} with saved output"
            + (f", {errors} with an error" if errors else "") + "). The checker does not run it.")
    if code_cells and not with_output:
        note += " No cell has saved output: the notebook was not run, or its outputs were cleared."
    return "".join(body), images, note


# --- Excel workbooks ----------------------------------------------------------------------

_REF_RE = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)")


def _pattern(formula: str, row: int) -> str:
    """The formula with its relative row numbers made relative to its own row, so the
    same formula filled down a column reads as one pattern."""
    def repl(match):
        col_abs, col, row_abs, number = match.groups()
        if row_abs:
            return match.group(0)
        offset = int(number) - row
        return f"{col_abs}{col}[{'+' if offset >= 0 else ''}{offset}]"
    return _REF_RE.sub(repl, formula)


def _display(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _colour(fill) -> str:
    try:
        rgb = fill.fgColor.rgb if fill is not None and fill.fill_type else None
        return rgb if isinstance(rgb, str) and rgb not in ("00000000",) else ""
    except Exception:  # noqa: BLE001
        return ""


def _sheet_facts(ws, values_ws) -> list[str]:
    from openpyxl.utils import get_column_letter
    facts = [f"Used range {ws.dimensions} ({ws.max_row} rows, {ws.max_column} columns)."]
    if getattr(ws, "sheet_state", "visible") != "visible":
        facts.append(f"The sheet is {ws.sheet_state}.")
    if ws.freeze_panes:
        facts.append(f"Frozen panes at {ws.freeze_panes} (rows above and columns left of it stay in view).")
    auto = ws.auto_filter
    if auto is not None and auto.ref:
        facts.append(f"Filter buttons on {auto.ref}.")
        for column in auto.filterColumn or []:
            try:
                from openpyxl.utils.cell import range_boundaries
                letter = get_column_letter(range_boundaries(auto.ref)[0] + int(column.colId))
            except Exception:  # noqa: BLE001
                letter = f"column {column.colId}"
            criteria = []
            if column.filters is not None:
                criteria += [str(getattr(f, 'val', f)) for f in (column.filters.filter or [])]
            if column.customFilters is not None:
                criteria += [f"{c.operator or 'equal'} {c.val}" for c in column.customFilters.customFilter or []]
            if column.top10 is not None:
                criteria.append(f"top {column.top10.val}")
            facts.append(f"Filter on {letter}: {', '.join(criteria) or 'set'}.")
        if auto.sortState is not None:
            for condition in auto.sortState.sortCondition or []:
                facts.append(f"Sorted by {condition.ref} {'descending' if condition.descending else 'ascending'}.")
    sort_state = getattr(ws, "sort_state", None)
    if sort_state is not None and getattr(sort_state, "sortCondition", None):
        for condition in sort_state.sortCondition:
            facts.append(f"Sorted by {condition.ref} {'descending' if condition.descending else 'ascending'}.")
    header = next((r for r in range(1, min(ws.max_row, 10) + 1)
                   if sum(1 for c in ws[r] if c.value not in (None, "")) >= 2), None)
    if header:
        cells = [c for c in ws[header] if c.value not in (None, "")]
        bold = sum(1 for c in cells if c.font is not None and c.font.b)
        filled = sorted({_colour(c.fill) for c in cells} - {""})
        centred = sum(1 for c in cells if c.alignment is not None and c.alignment.horizontal == "center")
        facts.append(f"Header row {header}: {len(cells)} cells, {bold} bold, {centred} centred"
                     + (f", fill colour {', '.join(filled[:3])}" if filled else ", no fill colour") + ".")
    widths = {k: d.width for k, d in ws.column_dimensions.items() if d.width and d.customWidth}
    if widths:
        shown = ", ".join(f"{k}={v:.1f}" for k, v in list(widths.items())[:12])
        facts.append(f"Column widths set: {shown}.")
    else:
        facts.append("No column widths set (default widths).")
    rules = []
    for formatting in ws.conditional_formatting:
        for rule in formatting.rules:
            text = f"{formatting.sqref}: {rule.type}"
            if rule.operator:
                text += f" {rule.operator}"
            if rule.formula:
                text += " " + ", ".join(str(f) for f in rule.formula)
            rules.append(text)
    if rules:
        facts.append("Conditional formatting: " + "; ".join(rules[:8]) + ".")
    validations = getattr(ws, "data_validations", None)
    if validations is not None and validations.dataValidation:
        facts.append("Data validation: " + "; ".join(
            f"{v.sqref} {v.type} {v.formula1 or ''}".strip() for v in validations.dataValidation[:6]) + ".")
    if ws.tables:
        # openpyxl's TableList.items() gives (name, range text) pairs, not Table objects:
        # reading .ref off the text raised AttributeError and lost a learner's whole
        # workbook (PW Skills Data Analytics, 13 Nov 2026: 4,000 formulas read as
        # "the file could not be read"). dict.items() gives the tables themselves.
        facts.append("Excel tables: " + ", ".join(
            f"{name} ({getattr(table, 'ref', table)})" for name, table in dict.items(ws.tables)) + ".")
    if ws.merged_cells.ranges:
        facts.append("Merged cells: " + ", ".join(str(r) for r in list(ws.merged_cells.ranges)[:8]) + ".")
    return facts


def _formula_columns(ws) -> list[str]:
    from openpyxl.utils import get_column_letter
    by_column: dict[int, dict[str, list[int]]] = {}
    first: dict[tuple[int, str], str] = {}
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            text = value if isinstance(value, str) else getattr(value, "text", None)
            if isinstance(text, str) and text.startswith("="):
                pattern = _pattern(text, cell.row)
                by_column.setdefault(cell.column, {}).setdefault(pattern, []).append(cell.row)
                first.setdefault((cell.column, pattern), f"{cell.coordinate}: {text}")
    lines = []
    for column in sorted(by_column):
        patterns = by_column[column]
        total = sum(len(rows) for rows in patterns.values())
        letter = get_column_letter(column)
        header = ws.cell(row=1, column=column).value
        title = f"Column {letter}" + (f" ({_display(header)})" if header not in (None, "") else "")
        shown = sorted(patterns.items(), key=lambda item: -len(item[1]))[:6]
        parts = [f"{first[(column, p)]} [{len(rows)} cells, rows {min(rows)}-{max(rows)}]"
                 for p, rows in shown]
        more = f"; {len(patterns) - 6} more distinct formulas" if len(patterns) > 6 else ""
        lines.append(f"{title}: {total} formulas. " + "; ".join(parts) + more)
    return lines


def _preview(ws, values_ws) -> str:
    rows = min(ws.max_row, SHEET_PREVIEW_ROWS)
    cols = min(ws.max_column, SHEET_PREVIEW_COLS)
    if not rows or not cols:
        return "<p class='note'>The sheet is empty.</p>"
    from openpyxl.utils import get_column_letter
    out = ["<table><tr><th></th>" + "".join(f"<th>{get_column_letter(c)}</th>" for c in range(1, cols + 1)) + "</tr>"]
    for r in range(1, rows + 1):
        cells = []
        for c in range(1, cols + 1):
            raw = ws.cell(row=r, column=c).value
            shown = values_ws.cell(row=r, column=c).value if values_ws is not None else None
            if isinstance(raw, str) and raw.startswith("=") and shown is None:
                shown = raw
            elif not (isinstance(raw, str) and raw.startswith("=")):
                shown = raw if shown is None else shown
            cells.append(f"<td>{_e(_display(shown))}</td>")
        out.append(f"<tr><th>{r}</th>" + "".join(cells) + "</tr>")
    out.append("</table>")
    if ws.max_row > rows or ws.max_column > cols:
        out.append(f"<p class='note'>First {rows} rows and {cols} columns of {ws.max_row} x "
                   f"{ws.max_column} shown.</p>")
    return "".join(out)


def _pivots(ws) -> list[str]:
    out = []
    for pivot in getattr(ws, "_pivots", []) or []:
        try:
            fields = [f.name for f in pivot.cache.cacheFields]

            def names(items):
                return [("Values" if int(i.x) < 0 else fields[int(i.x)]) for i in items or []]
            data_fields = [f"{d.name or fields[int(d.fld)]} ({d.subtotal or 'sum'})"
                           for d in pivot.dataFields or []]
            source = pivot.cache.cacheSource.worksheetSource
            src = f"{source.sheet}!{source.ref}" if source is not None else "unknown source"
            filters = [fields[int(p.fld)] for p in pivot.pageFields or []]
            out.append(f"Pivot table {pivot.name} at {pivot.location.ref} from {src}: rows "
                       f"{names(pivot.rowFields) or '-'}, columns {names(pivot.colFields) or '-'}, "
                       f"values {data_fields or '-'}" + (f", filters {filters}" if filters else "") + ".")
        except Exception as exc:  # noqa: BLE001
            out.append(f"Pivot table {getattr(pivot, 'name', '')} could not be read ({type(exc).__name__}).")
    return out


_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _charts(data: bytes) -> list[str]:
    out = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return out
    for name in sorted(n for n in archive.namelist() if re.match(r"xl/charts/chart\d+\.xml$", n)):
        try:
            root = ET.fromstring(archive.read(name))
        except ET.ParseError:
            continue
        plot = root.find(f".//{_C}plotArea")
        kinds = [child.tag.replace(_C, "") for child in (plot if plot is not None else [])
                 if child.tag.endswith("Chart")]
        title = " ".join(t.text or "" for t in root.findall(f".//{_C}title//{_A}t")).strip()
        series = []
        for ser in root.findall(f".//{_C}ser"):
            label = " ".join(t.text or "" for t in ser.findall(f"{_C}tx//{_C}v")) or \
                    " ".join(t.text or "" for t in ser.findall(f"{_C}tx//{_C}f"))
            cat = ser.find(f"{_C}cat//{_C}f")
            val = ser.find(f"{_C}val//{_C}f")
            series.append(f"{label.strip() or 'series'}: categories {cat.text if cat is not None else '-'}, "
                          f"values {val.text if val is not None else '-'}")
        out.append(f"Chart {name.rsplit('/', 1)[-1]}: {', '.join(kinds) or 'chart'}"
                   + (f", title '{title}'" if title else ", no title") + ". " + "; ".join(series[:6]))
    return out


_SHEET_QUESTION = re.compile(
    r"(?<![a-z])q(?:uestion)?\s*[._-]?\s*\(?\s*(\d{1,2}(?:\s*(?:,|&|and)\s*(?:q\s*\.?\s*)?\d{1,2})*)(?!\d)",
    re.IGNORECASE)


def sheet_questions(name: str) -> list[int]:
    """The question numbers a sheet's name gives, so the answer map places the sheet under
    them: "Q3_Electronics" and "Q5 & Q6" as before, and where learners put the number
    later in the name, "SalesData-Q (7)", "SheetData- Q(14-B)", "Product Lookup Report(Q.4)"
    and "Sales_Data(Q.1,2,3)" (PW Skills Data Analytics workbooks, 13 Nov 2026)."""
    found: list[int] = []
    for match in _SHEET_QUESTION.finditer(str(name or "")):
        for number in re.findall(r"\d{1,2}", match.group(1)):
            if int(number) not in found:
                found.append(int(number))
    return found


def workbook_html(data: bytes) -> tuple[str, dict, str]:
    import openpyxl
    formulas = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
    try:
        values = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:  # noqa: BLE001
        values = None
    body = []
    names = formulas.sheetnames
    for index, name in enumerate(names, 1):
        ws = formulas[name]
        values_ws = values[name] if values is not None and name in values.sheetnames else None
        if index > 1:
            body.append(NEW_PAGE)       # each sheet on its own page
        asked = sheet_questions(name)
        if asked:
            body.append(f"<h2>{', '.join(f'Q{n}' for n in asked)}: sheet {_e(name)} "
                        f"(sheet {index} of {len(names)})</h2>")
        else:
            body.append(f"<h2>Sheet {index} of {len(names)}: {_e(name)}</h2>")
        try:
            facts = _sheet_facts(ws, values_ws)
        except Exception as exc:  # noqa: BLE001 - a summary line is never worth the sheet itself
            facts = [f"Sheet details could not be read ({type(exc).__name__}); its formulas and values follow."]
        body.append("".join(f"<p>{_e(fact)}</p>" for fact in facts))
        columns = _formula_columns(ws)
        if columns:
            body.append("<h3>Formulas</h3>" + "".join(f"<p>{_e(line)}</p>" for line in columns))
        pivots = _pivots(ws)
        if pivots:
            body.append("<h3>Pivot tables</h3>" + "".join(f"<p>{_e(line)}</p>" for line in pivots))
        body.append("<h3>Values</h3>" + _preview(ws, values_ws))
    charts = _charts(data)
    if charts:
        body.append("<h2>Charts</h2>" + "".join(f"<p>{_e(line)}</p>" for line in charts))
    defined = []
    try:
        defined = [f"{n}: {d.attr_text}" for n, d in formulas.defined_names.items()]
    except Exception:  # noqa: BLE001
        pass
    if defined:
        body.append("<h2>Named ranges</h2>" + "".join(f"<p>{_e(d)}</p>" for d in defined[:20]))
    note = (f"Read from the workbook file: {len(names)} sheets, with formulas, formatting and "
            "settings as saved. Values are the ones the file saved.")
    return "".join(body), {}, note


def csv_html(data: bytes) -> tuple[str, dict, str]:
    import csv
    text = data.decode("utf-8-sig", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    shown = rows[:40]
    out = ["<table>"]
    for i, row in enumerate(shown):
        tag = "th" if i == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{_e(_display(v))}</{tag}>" for v in row[:SHEET_PREVIEW_COLS]) + "</tr>")
    out.append("</table>")
    return "".join(out), {}, f"CSV data, {len(rows)} rows; the first {len(shown)} are shown."


# --- Power BI -----------------------------------------------------------------------------

_AUTO_TABLE = re.compile(r"^(LocalDateTable_|DateTableTemplate_)|\$")


def _decode_json(raw: bytes):
    for encoding in ("utf-16le", "utf-8-sig", "utf-16"):
        try:
            text = raw.decode(encoding)
            if text.lstrip("﻿").lstrip().startswith(("{", "[")):
                return json.loads(text.lstrip("﻿"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def _xpress9_model(raw: bytes) -> bytes:
    """The DataModel stream decompressed (Power BI's XPress9 backup)."""
    from xpress9 import Xpress9
    signature = raw[:102].decode("utf-16le", "ignore")
    out = bytearray()
    if "multithreaded" in signature:
        pos = 102
        main_cpt, prefix_cpt, prefix_threads, main_threads, _ = (
            int.from_bytes(raw[pos + 8 * i: pos + 8 * i + 8], "little") for i in range(5))
        pos += 40
        for threads, per in ((prefix_threads, prefix_cpt), (main_threads, main_cpt)):
            for _ in range(threads):
                lib = Xpress9()
                for _ in range(per):
                    size = int.from_bytes(raw[pos:pos + 4], "little")
                    packed = int.from_bytes(raw[pos + 4:pos + 8], "little")
                    pos += 8
                    out += lib.decompress(raw[pos:pos + packed], size)
                    pos += packed
                del lib
    elif "XPress9" in signature:
        pos, lib = 102, Xpress9()
        while pos < len(raw):
            size = int.from_bytes(raw[pos:pos + 4], "little")
            packed = int.from_bytes(raw[pos + 4:pos + 8], "little")
            pos += 8
            out += lib.decompress(raw[pos:pos + packed], size)
            pos += packed
        del lib
    elif "STREAM_STORAGE_SIGNATURE" in raw[:200].decode("utf-16le", "ignore"):
        out = bytearray(raw)
    else:
        raise ValueError("unknown DataModel format")
    return bytes(out)


def _model_sqlite(model: bytes) -> bytes:
    """metadata.sqlitedb out of the decompressed Analysis Services backup."""
    header = ET.fromstring(model[72:4096].decode("utf-16").rstrip("\x00"))
    if header.findtext("ApplyCompression") == "true":
        raise ValueError("the model's files are compressed a second time")
    error_code = header.findtext("ErrorCode") == "true"
    offset, size = int(header.findtext("m_cbOffsetHeader")), int(header.findtext("DataSize"))
    directory = ET.fromstring(model[offset:offset + size])
    files = {e.findtext("Path"): (int(e.findtext("m_cbOffsetHeader")), int(e.findtext("Size")))
             for e in directory.findall("BackupFile")}
    log_offset, log_size = files[list(files)[-1]]
    log = model[log_offset:log_offset + log_size]
    log = ET.fromstring((log[:-4] if error_code else log).decode("utf-16"))
    for group in log.findall("FileGroups/FileGroup"):
        for item in group.findall("FileList/BackupFile"):
            if str(item.findtext("Path") or "").endswith("metadata.sqlitedb"):
                where = files.get(item.findtext("StoragePath"))
                if where:
                    start, length = where
                    return model[start:start + length - (4 if error_code else 0)]
    raise ValueError("metadata.sqlitedb not found")


def _model_from_sqlite(blob: bytes) -> dict:
    import os
    import sqlite3
    import tempfile
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlitedb")
    try:
        handle.write(blob)
        handle.close()
        db = sqlite3.connect(handle.name)
        try:
            tables = {tid: name for tid, name in db.execute("SELECT ID, Name FROM [Table]")}
            columns = {cid: (tables.get(tid, "?"), name or inferred)
                       for cid, tid, name, inferred in db.execute(
                           "SELECT ID, TableID, ExplicitName, InferredName FROM [Column]")}
            model = {"tables": {}}
            for tid, name in tables.items():
                model["tables"][name] = {"columns": [], "calculated": [], "measures": [], "m": "", "dax_table": ""}
            for tid, name, inferred, ctype, expr in db.execute(
                    "SELECT TableID, ExplicitName, InferredName, Type, Expression FROM [Column]"):
                table = model["tables"].get(tables.get(tid, ""))
                if table is None or ctype == 3:            # 3: the internal RowNumber column
                    continue
                if ctype == 2:
                    table["calculated"].append((name or inferred, expr))
                else:
                    table["columns"].append(name or inferred)
            for tid, name, expr, fmt in db.execute(
                    "SELECT TableID, Name, Expression, FormatString FROM [Measure]"):
                table = model["tables"].get(tables.get(tid, ""))
                if table is not None:
                    table["measures"].append((name, expr, fmt))
            for tid, ptype, query in db.execute("SELECT TableID, Type, QueryDefinition FROM [Partition]"):
                table = model["tables"].get(tables.get(tid, ""))
                if table is None or not query:
                    continue
                if ptype == 2:
                    table["dax_table"] = query
                elif ptype == 4:
                    table["m"] = query
            relations = []
            for row in db.execute("SELECT FromColumnID, ToColumnID, IsActive, CrossFilteringBehavior "
                                  "FROM [Relationship]"):
                source, target = columns.get(row[0], ("?", "?")), columns.get(row[1], ("?", "?"))
                relations.append(f"{source[0]}[{source[1]}] -> {target[0]}[{target[1]}]"
                                 + ("" if row[2] else " (inactive)")
                                 + (" (both directions)" if row[3] == 2 else ""))
            model["relationships"] = relations
            return model
        finally:
            db.close()
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _model_from_schema(schema: dict) -> dict:
    model = {"tables": {}, "relationships": []}
    for table in (schema.get("model") or {}).get("tables", []):
        entry = {"columns": [], "calculated": [], "measures": [], "m": "", "dax_table": ""}
        for column in table.get("columns", []):
            if column.get("type") == "rowNumber":
                continue
            if column.get("type") == "calculated" or "expression" in column:
                entry["calculated"].append((column.get("name"), _joined(column.get("expression"))))
            else:
                entry["columns"].append(column.get("name"))
        for measure in table.get("measures", []):
            entry["measures"].append((measure.get("name"), _joined(measure.get("expression")),
                                      measure.get("formatString")))
        for partition in table.get("partitions", []):
            source = partition.get("source") or {}
            if source.get("type") == "calculated":
                entry["dax_table"] = _joined(source.get("expression"))
            elif source.get("type") == "m":
                entry["m"] = _joined(source.get("expression"))
        model["tables"][table.get("name")] = entry
    for rel in (schema.get("model") or {}).get("relationships", []):
        model["relationships"].append(
            f"{rel.get('fromTable')}[{rel.get('fromColumn')}] -> {rel.get('toTable')}[{rel.get('toColumn')}]"
            + ("" if rel.get("isActive", True) else " (inactive)"))
    return model


def _field_ref(projection: dict) -> str:
    ref = projection.get("queryRef") or projection.get("nativeQueryRef")
    if ref:
        return str(ref)
    field = projection.get("field") or {}
    for kind in ("Column", "Measure", "Aggregation", "HierarchyLevel"):
        if kind in field:
            inner = field[kind]
            if kind == "Aggregation":
                inner = (inner.get("Expression") or {}).get("Column") or {}
            entity = ((inner.get("Expression") or {}).get("SourceRef") or {}).get("Entity", "")
            return f"{entity}.{inner.get('Property', '')}".strip(".")
    return "field"


def _report_pages(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    pages = []
    if "Report/Layout" in names:
        layout = _decode_json(archive.read("Report/Layout")) or {}
        for section in layout.get("sections", []):
            visuals = []
            for container in section.get("visualContainers", []):
                try:
                    config = json.loads(container.get("config") or "{}")
                except json.JSONDecodeError:
                    continue
                single = config.get("singleVisual") or {}
                kind = single.get("visualType", "visual")
                fields = [p.get("queryRef", "") for role in (single.get("projections") or {}).values() for p in role]
                title = ""
                try:
                    title = single["vcObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"].strip("'")
                except (KeyError, IndexError, TypeError):
                    pass
                visuals.append(f"{kind}" + (f" '{title}'" if title else "") + f": {', '.join(fields) or 'no fields'}")
            pages.append((section.get("displayName", "page"), visuals))
    else:
        page_dirs = sorted({n.split("/")[3] for n in names
                            if n.startswith("Report/definition/pages/") and n.count("/") >= 4})
        for page_id in page_dirs:
            page = _decode_json(archive.read(f"Report/definition/pages/{page_id}/page.json")) or {}
            visuals = []
            for name in sorted(n for n in names if n.startswith(f"Report/definition/pages/{page_id}/visuals/")
                               and n.endswith("/visual.json")):
                visual = (_decode_json(archive.read(name)) or {}).get("visual") or {}
                state = ((visual.get("query") or {}).get("queryState") or {})
                fields = [_field_ref(p) for role in state.values() for p in (role.get("projections") or [])]
                visuals.append(f"{visual.get('visualType', 'visual')}: {', '.join(fields) or 'no fields'}")
            pages.append((page.get("displayName", page_id), visuals))
    return [f"Page '{title}': " + ("; ".join(visuals) if visuals else "no visuals") for title, visuals in pages]


def powerbi_html(data: bytes) -> tuple[str, dict, str]:
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = archive.namelist()
    model, problem = None, ""
    if "DataModelSchema" in names:
        model = _model_from_schema(_decode_json(archive.read("DataModelSchema")) or {})
    elif "DataModel" in names:
        try:
            model = _model_from_sqlite(_model_sqlite(_xpress9_model(archive.read("DataModel"))))
        except ImportError:
            problem = "The data model could not be read on this computer (the XPress9 reader is missing)."
        except Exception as exc:  # noqa: BLE001
            problem = f"The data model could not be read ({type(exc).__name__}: {exc})."
    else:
        problem = "The file has no data model of its own (a live connection to a dataset)."
    body = ["<h2>Data model</h2>"]
    if model is None:
        body.append(f"<p>{_e(problem)}</p>")
    else:
        user_tables = {name: t for name, t in model["tables"].items() if not _AUTO_TABLE.search(name or "")}
        for name, table in user_tables.items():
            body.append(f"<h3>Table {_e(name)}</h3>")
            if table["columns"]:
                body.append(f"<p>Columns: {_e(', '.join(str(c) for c in table['columns']))}</p>")
            for cname, expr in table["calculated"]:
                body.append(f"<p>Calculated column <b>{_e(cname)}</b> =</p>"
                            + (f"<pre>{_e(expr)}</pre>" if expr and str(expr).strip()
                               else "<p class='note'>(no formula saved in the file)</p>"))
            for mname, expr, fmt in table["measures"]:
                body.append(f"<p>Measure <b>{_e(mname)}</b>" + (f" (format {_e(fmt)})" if fmt else "") + " =</p>"
                            + (f"<pre>{_e(expr)}</pre>" if expr and str(expr).strip()
                               else "<p class='note'>(no formula saved in the file)</p>"))
            if table["dax_table"]:
                body.append(f"<p>Calculated table =</p><pre>{_e(table['dax_table'])}</pre>")
            if table["m"]:
                body.append(f"<p>Power Query (M):</p><pre>{_e(_clip_lines(table['m'], 40))}</pre>")
        if model.get("relationships"):
            body.append("<h3>Relationships</h3>" + "".join(f"<p>{_e(r)}</p>" for r in model["relationships"]))
    pages = _report_pages(archive)
    body.append("<h2>Report pages</h2>" + ("".join(f"<p>{_e(p)}</p>" for p in pages) or "<p>No report pages.</p>"))
    note = ("Read from the Power BI file: the data model's tables, DAX formulas, Power Query "
            "steps and relationships, and each report page's visuals with their fields. The "
            "rendered charts are not shown.")
    return "".join(body), {}, note


# --- Tableau ------------------------------------------------------------------------------

_TABLEAU_FIELD_RE = re.compile(r"\[[^\]]*\]\.\[([a-z]*):([^\]:]+):[a-z]*\]|\[([^\]\[]+)\](?!\.)")
# Tableau's derivation prefixes on a shelf field ("sum:Treatment_Cost:qk").
_TABLEAU_AGG = {"sum": "SUM", "avg": "AVG", "cnt": "COUNT", "ctd": "COUNTD", "min": "MIN",
                "max": "MAX", "med": "MEDIAN", "attr": "ATTR", "yr": "YEAR", "qr": "QUARTER",
                "mn": "MONTH", "dy": "DAY", "wk": "WEEK", "tyr": "YEAR (continuous)",
                "tqr": "QUARTER (continuous)", "tmn": "MONTH (continuous)",
                "twk": "WEEK (continuous)", "tdy": "DAY (continuous)", "md": "MONTH-DAY",
                "my": "MONTH-YEAR", "mdy": "DATE"}


def _tableau_field(text: str | None) -> str:
    """A shelf or filter field as an analyst names it: "SUM(Treatment_Cost)", not
    "[federated.1ncv...].[sum:Treatment_Cost:qk]"."""
    def one(match: "re.Match[str]") -> str:
        prefix, field, plain = match.group(1), match.group(2), match.group(3)
        if field is None:
            return plain or ""
        agg = _TABLEAU_AGG.get(prefix or "none")
        if agg and agg.endswith(" (continuous)"):
            return f"{agg.split(' ')[0]}({field}) continuous"
        return f"{agg}({field})" if agg else field
    return " ".join(_TABLEAU_FIELD_RE.sub(one, str(text or "")).split())


def _tableau_filter(node) -> str:
    column = _tableau_field(node.get("column"))
    low, high = node.findtext("min"), node.findtext("max")
    if low is not None or high is not None:
        def plain(value):
            return str(value or "").strip("#") or "any"
        return f"{column} from {plain(low)} to {plain(high)}"
    members = [g.get("member") for g in node.iter("groupfilter") if g.get("member")]
    if members:
        excluded = any(g.get("function") == "except" for g in node.iter("groupfilter"))
        shown = ", ".join(m.strip('"') for m in members[:12])
        return f"{column} {'excluding' if excluded else 'keeping'} {shown}"
    return f"{column} (filter with no values saved)"


def _tableau_title(node) -> str:
    runs = [r.text or "" for r in node.findall(".//layout-options/title//run")]
    return " ".join("".join(runs).split())


def tableau_html(data: bytes, name: str) -> tuple[str, dict, str]:
    """A Tableau workbook as what a reviewer checks: each worksheet's shelves, mark type,
    Marks card fields, colours and labels, filter ranges and values, and sorts, then
    the dashboards with their controls and the stories with their points. MEASURED 23
    Oct 2026: a learner's date filter (2024-03-01 to 2024-09-30), mark colour and one
    story point were all in the .twb, but only the filter's field name reached the
    grader, so three remarks said they were missing."""
    raw = data
    if _ext(name) == ".twbx" or data[:2] == b"PK":
        archive = zipfile.ZipFile(io.BytesIO(data))
        twb = next((n for n in archive.namelist() if n.lower().endswith(".twb")), None)
        if twb is None:
            return "<p>The packaged workbook has no .twb inside.</p>", {}, "Tableau packaged workbook."
        raw = archive.read(twb)
    root = ET.fromstring(raw)
    body = ["<h2>Data sources</h2>"]
    for ds in root.findall("./datasources/datasource"):
        caption = ds.get("caption") or ds.get("name")
        if caption == "Parameters":
            continue
        connection = ds.find(".//connection")
        source = ""
        if connection is not None:
            source = connection.get("filename") or connection.get("dbname") or connection.get("class") or ""
        body.append(f"<p><b>{_e(caption)}</b>" + (f" ({_e(source)})" if source else "") + "</p>")
        for column in ds.findall("./column"):
            calc = column.find("calculation")
            if calc is not None and calc.get("formula"):
                body.append(f"<p>Calculated field <b>{_e(column.get('caption') or column.get('name'))}</b> =</p>"
                            f"<pre>{_e(calc.get('formula'))}</pre>")
    body.append("<h2>Worksheets</h2>")
    for ws in root.findall("./worksheets/worksheet"):
        rows = _tableau_field(ws.findtext(".//table/rows"))
        cols = _tableau_field(ws.findtext(".//table/cols"))
        marks = [m.get("class") for m in ws.findall(".//pane/mark") if m.get("class")]
        encodings = [f"{e.tag.replace('-', ' ')}: {_tableau_field(e.get('column'))}"
                     for e in ws.findall(".//pane/encodings/*") if e.get("column")]
        styles = []
        for rule in ws.findall(".//style-rule"):
            for fmt in rule.findall("format"):
                attr, value = fmt.get("attr") or "", fmt.get("value") or ""
                if attr == "mark-color":
                    styles.append(f"mark colour set to {value}")
                elif attr == "mark-labels-show":
                    styles.append("mark labels shown" if value == "true" else "mark labels hidden")
        filters = [_tableau_filter(f) for f in ws.findall(".//filter") if f.get("column")]
        sorts = [f"{_tableau_field(s.get('dimension-to-sort'))} sorted by "
                 f"{_tableau_field(s.get('measure-to-sort-by')) or 'its values'} "
                 f"{'descending' if s.get('direction') == 'DESC' else 'ascending'}"
                 for s in ws.findall(".//shelf-sort-v2")]
        title = _tableau_title(ws)
        body.append(
            f"<h3>{_e(ws.get('name'))}</h3>" + (f"<p>Title: {_e(title)}</p>" if title else "")
            + f"<p>Rows: {_e(rows or 'none')}</p><p>Columns: {_e(cols or 'none')}</p>"
            f"<p>Mark type: {_e(', '.join(marks) or 'Automatic')}</p>"
            + (f"<p>On the Marks card: {_e('; '.join(encodings[:12]))}</p>" if encodings
               else "<p>On the Marks card: nothing</p>")
            + (f"<p>Formatting: {_e('; '.join(styles[:6]))}</p>" if styles else "")
            + (f"<p>Filters: {_e('; '.join(filters[:10]))}</p>" if filters else "<p>Filters: none</p>")
            + (f"<p>Sort: {_e('; '.join(sorts[:4]))}</p>" if sorts else ""))
    boards = root.findall("./dashboards/dashboard")
    dashboards = [d for d in boards if d.get("type") != "storyboard"]
    stories = [d for d in boards if d.get("type") == "storyboard"]
    if dashboards:
        body.append("<h2>Dashboards</h2>")
        for dash in dashboards:
            zones = dash.findall(".//zone")
            sheets = sorted({z.get("name") for z in zones if z.get("name") and not z.get("param")})
            controls = sorted({f"{z.get('type-v2') or z.get('type')} for {_tableau_field(z.get('param'))}"
                               for z in zones
                               if (z.get("type-v2") or z.get("type")) in ("filter", "paramctrl", "color")
                               and z.get("param")})
            body.append(f"<p><b>{_e(dash.get('name'))}</b>: sheets {_e(', '.join(sheets) or 'none')}</p>"
                        "<p>Controls on the dashboard: "
                        f"{_e('; '.join(controls) or 'none (no filter, legend or parameter control)')}</p>")
    if stories:
        body.append("<h2>Stories</h2>")
        for story in stories:
            points, seen = [], set()
            for point in story.iter("story-point"):
                key = point.get("id") or point.get("caption")
                if key in seen:
                    continue
                seen.add(key)
                points.append(f"“{point.get('caption') or 'untitled'}” showing "
                              f"{point.get('captured-sheet') or 'no sheet'}")
            body.append(f"<p><b>{_e(story.get('name'))}</b>: {len(points)} story point(s)</p>"
                        + "".join(f"<p>Story point {n}: {_e(text)}</p>"
                                  for n, text in enumerate(points, 1)))
    actions = root.findall("./actions/action")
    if actions:
        body.append("<h2>Actions</h2>" + "".join(
            f"<p>{_e(a.get('caption') or a.get('name'))}</p>" for a in actions[:12]))
    note = ("Read from the Tableau workbook: data sources, calculated fields with their "
            "formulas, each worksheet's shelves, mark type, Marks card fields, formatting, "
            "filters with their ranges or values, and sorts, each dashboard's sheets and "
            "controls, story points and actions. The rendered views are not shown.")
    return "".join(body), {}, note


# --- Word ---------------------------------------------------------------------------------

def docx_html(data: bytes) -> tuple[str, dict, str]:
    import docx
    document = docx.Document(io.BytesIO(data))
    body, images = [], {}
    blip = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
    embed = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for block in document.element.body.iterchildren():
        if block.tag == f"{w}p":
            text = "".join(t.text or "" for t in block.iter(f"{w}t"))
            style = block.find(f"{w}pPr/{w}pStyle")
            heading = style is not None and str(style.get(f"{w}val", "")).lower().startswith("heading")
            if text.strip():
                body.append(f"<h3>{_e(text)}</h3>" if heading else f"<p>{_e(text)}</p>")
            for pic in block.iter(blip):
                rid = pic.get(embed)
                part = document.part.related_parts.get(rid) if rid else None
                if part is not None and getattr(part, "blob", None):
                    name = f"doc{len(images) + 1}.{(part.partname.ext or 'png')}"
                    images[name] = part.blob
                    body.append(f"<p><img src='{name}' width='440'/></p>")
        elif block.tag == f"{w}tbl":
            rows = []
            for tr in block.iter(f"{w}tr"):
                cells = ["".join(t.text or "" for t in tc.iter(f"{w}t")) for tc in tr.iter(f"{w}tc")]
                rows.append("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in cells) + "</tr>")
            body.append("<table>" + "".join(rows) + "</table>")
    return "".join(body), images, "Read from the Word document: text, tables and pictures in order."


# --- the document ---------------------------------------------------------------------------

def _convert(item) -> tuple[bytes | None, str]:
    """(pages, kind) for one file; pages None when the file is a PDF or image inserted as is."""
    name, data = item.name, item.data
    ext = _ext(name)
    kind = readable_kind(name, data) or "file"
    if ext == ".ipynb":
        body, images, note = notebook_html(data)
    elif ext in (".xlsx", ".xlsm"):
        body, images, note = workbook_html(data)
    elif ext == ".csv":
        body, images, note = csv_html(data)
    elif ext in (".pbix", ".pbit"):
        body, images, note = powerbi_html(data)
    elif ext in (".twb", ".twbx"):
        body, images, note = tableau_html(data, name)
    elif ext == ".docx":
        body, images, note = docx_html(data)
    elif ext in _CODE_EXTS:
        text = data.decode("utf-8", "replace")
        body, images, note = (f"<pre>{_e(_clip_lines(text, MAX_CODE_LINES))}</pre>", {},
                              f"Code file, {len(text.splitlines())} lines.")
    else:
        return None, kind
    return _html_pdf(_header(item.index, item.count, name, kind, item.url, note) + body, images), kind


def build_document(*, cover_pdf: bytes | None, files: list, problems: list) -> bytes:
    """The learner's cover pages, then every file converted, then the links that could
    not be opened. At most MAX_DOCUMENT_PAGES pages of converted work."""
    import fitz
    out = fitz.open()
    if cover_pdf:
        with fitz.open(stream=cover_pdf, filetype="pdf") as cover:
            out.insert_pdf(cover)
    converted_pages = 0
    count = len(files)
    for index, item in enumerate(files, 1):
        item.index, item.count = index, count
        if converted_pages >= MAX_DOCUMENT_PAGES:
            break
        ext = _ext(item.name)
        try:
            if ext == ".pdf" or (item.data[:5] == b"%PDF-" and ext not in _KINDS):
                header = _html_pdf(_header(index, count, item.name, "PDF document", item.url,
                                           "The learner's PDF, as it is."))
                with fitz.open(stream=header, filetype="pdf") as head:
                    out.insert_pdf(head)
                with fitz.open(stream=item.data, filetype="pdf") as linked:
                    last = min(linked.page_count, MAX_PDF_PAGES, MAX_DOCUMENT_PAGES - converted_pages) - 1
                    if last >= 0:
                        out.insert_pdf(linked, to_page=last)
                        converted_pages += last + 2
                continue
            if ext in (".png", ".jpg", ".jpeg"):
                body = (_header(index, count, item.name, "image", item.url, "The learner's picture.")
                        + "<p><img src='picture' width='500'/></p>")
                pages = _html_pdf(body, {"picture": item.data})
            else:
                pages, _ = _convert(item)
            if pages is None:
                pages = _html_pdf(_header(index, count, item.name, "file", item.url,
                                          "The checker does not read this kind of file."))
        except Exception as exc:  # noqa: BLE001 - one unreadable file is noted, not fatal
            pages = _html_pdf(_header(index, count, item.name, readable_kind(item.name, item.data) or "file",
                                      item.url, f"The file could not be read ({type(exc).__name__})."))
        with fitz.open(stream=pages, filetype="pdf") as converted:
            keep = min(converted.page_count, MAX_DOCUMENT_PAGES - converted_pages)
            if keep > 0:
                out.insert_pdf(converted, to_page=keep - 1)
                converted_pages += keep
    if problems:
        rows = "".join(f"<p>{_e(url)}: {_e(reason)}</p>" for url, reason in problems)
        notice = _html_pdf("<h1>Links that could not be opened</h1>"
                           "<p class='note'>Listed by the checker. These links were not marked.</p>" + rows)
        with fitz.open(stream=notice, filetype="pdf") as page:
            out.insert_pdf(page)
    if out.page_count == 0:
        out.new_page()
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data
