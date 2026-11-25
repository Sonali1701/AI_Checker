"""Offline regression tests of the real portal adapter's selection guards.

The locator stubs deliberately leave a prior student's row visible after
search, modeling delayed or failed portal filtering without touching a portal.
"""
from dataclasses import dataclass, field
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import integrated_workflow as workflow
from portal_vendor import data_filler as portal
from portal_vendor import rejection_comments as portal_rejections
from portal_vendor import match_paper as portal_papers
from test_portal_vendor_evaluator_dropdown import Dropdown, option


def matches(pattern, text):
    if pattern is None:
        return True
    return bool(pattern.search(text)) if hasattr(pattern, "search") else str(pattern) in text


@dataclass
class Query:
    selector: str
    pattern: object = None


@dataclass
class Element:
    text: str = ""
    attributes: dict = field(default_factory=dict)

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.attributes.get(name)


class Elements:
    def __init__(self, elements):
        self.elements = elements

    def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]

    @property
    def first(self):
        return self.elements[0] if self.elements else Empty()

    @property
    def last(self):
        return self.elements[-1] if self.elements else Empty()

    def all_inner_texts(self):
        return [element.inner_text() for element in self.elements]


class Empty:
    def count(self):
        return 0


class Checkbox(Element):
    def __init__(self):
        super().__init__()
        self.checked = False

    def check(self):
        self.checked = True

    def uncheck(self):
        self.checked = False


class DeadlineBox(Element):
    def __init__(self):
        super().__init__()
        self.value = ""

    def wait_for(self, **kwargs):
        pass

    def is_visible(self):
        return True

    def fill(self, value):
        self.value = value


class Row(Element):
    def __init__(self, student="student123", test="Maths Test", series="Batch Tests", attributes=None, markup="",
                 evaluator="", status="Submitted"):
        # The live Students table: name, test, series, evaluator (column 3), ..., status last.
        self.cells = [Element(student), Element(test), Element(series), Element(evaluator), Element(status)]
        self.checkbox = Checkbox()
        self.children = []
        self.markup = markup
        super().__init__("\n".join(cell.text for cell in self.cells), attributes or {})

    def count(self):
        return 1

    def evaluate(self, expression):
        assert "outerHTML" in expression
        return self.markup or "<tr>" + self.text + "</tr>"

    def locator(self, selector, has_text=None):
        if selector == "td":
            return Elements([cell for cell in self.cells if matches(has_text, cell.text)])
        if selector == portal.SEL["row_checkbox"]:
            return Elements([self.checkbox])
        if selector.startswith("[") and selector.endswith("]"):
            attribute = selector[1:-1]
            return Elements([child for child in self.children if attribute in child.attributes])
        raise AssertionError(f"Unexpected row locator: {selector}")

    def has_query(self, query):
        if query.selector == portal.SEL["row_checkbox"]:
            return True
        if query.selector == "td":
            return any(matches(query.pattern, cell.text) for cell in self.cells)
        raise AssertionError(f"Unexpected nested selector: {query.selector}")


class Rows(Elements):
    def filter(self, *, has=None, has_text=None):
        return FilteredRows(self, has, has_text)

    def locator(self, selector):
        if selector == "td:nth-child(2)":
            return Elements([row.cells[1] for row in self.elements])
        raise AssertionError(f"Unexpected rows locator: {selector}")


class FilteredRows(Rows):
    """Lazy like a Playwright locator: re-evaluated on every count(), so rows the
    site renders later are seen by a waiting caller."""
    def __init__(self, parent, has, has_text):
        self.parent, self.has, self.has_text = parent, has, has_text

    @property
    def elements(self):
        return [row for row in self.parent.elements
                if (self.has is None or row.has_query(self.has)) and matches(self.has_text, row.inner_text())]


class SearchBox(Element):
    def __init__(self, value):
        super().__init__()
        self.value = value

    def wait_for(self, **kwargs):
        pass

    def bounding_box(self):
        return {"x": 10, "y": 10, "width": 200, "height": 30}

    def input_value(self):
        return self.value


class Page:
    def __init__(self, rows, search_value=None, later=None):
        self.rows = Rows(rows)
        self.deadline = DeadlineBox()
        self.later = list(later or [])   # rows the site renders after a poll (row_named waits)
        self.polls = 0
        self.search = Elements([SearchBox(search_value)]) if search_value is not None else Elements([])

    def wait_for_timeout(self, ms):
        self.polls += 1
        if self.later:
            self.rows.elements.extend(self.later)  # in place: existing locators see the new rows
            self.later = []

    def locator(self, selector, has_text=None):
        if selector == portal.SEL["data_rows"]:
            return self.rows
        if selector == portal.SEL["search"]:
            return self.search
        if selector == portal.SEL["deadline"]:
            return Elements([self.deadline])
        return Query(selector, has_text)

    def evaluate(self, expression):
        return 1000  # innerWidth for search_box()

    def is_closed(self):
        return False


def record(**updates):
    values = dict(program="Program", test_series="Batch Tests", test_name="Maths Test",
                  student_id="student123", evaluator="teacher@example.com",
                  deadline="2027-01-01T12:00", total_marks="", comment="")
    values.update(updates)
    return portal.StudentRecord(**values)


@pytest.fixture
def stale_search(monkeypatch):
    monkeypatch.setattr(portal, "search", lambda *args: None)


def test_evaluator_email_matches_complete_address_case_insensitively():
    # The v0.9.10 engine reads the evaluator control through one browser call;
    # the Dropdown double comes from the supplied regression tests.
    dropdown = Dropdown([option("Other (xteacher@example.com)", index=0),
                         option("Correct (Teacher@Example.com)", index=1),
                         option("Another (teacher@example.com.other)", index=2)])
    with dropdown.patches():
        assert portal.select_evaluator(dropdown.page, "teacher@example.com") == "Correct (Teacher@Example.com)"
    assert (dropdown.selected, dropdown.clock) == (1, 0)


def test_duplicate_exact_evaluator_email_is_rejected():
    dropdown = Dropdown([option("First (teacher@example.com)", index=0),
                         option("Second (teacher@example.com)", index=1)])
    with dropdown.patches(), pytest.raises(RuntimeError, match="More than one evaluator option"):
        portal.select_evaluator(dropdown.page, "teacher@example.com")
    dropdown.control.select_option.assert_not_called()


@pytest.mark.parametrize("operation", ["assign", "upload"])
def test_stale_search_cannot_select_the_sole_wrong_student(stale_search, operation):
    row = Row(student="someone_else")
    page = Page([row])
    with pytest.raises(LookupError, match="student ID|identity"):
        if operation == "assign":
            portal.allot_student(page, record(), False)
        else:
            portal.find_evaluate_row(page, record())
    assert not row.checkbox.checked


@pytest.mark.parametrize("other_id", ["student123_other", "student123-other", "other_student123",
                                     "other-student123", "student1234", "xstudent123"])
def test_student_id_prefix_and_suffix_are_not_identity_matches(other_id):
    with pytest.raises(LookupError, match="student ID|identity"):
        portal.verify_student_row(Row(student=other_id), "student123")


@pytest.mark.parametrize("attribute", ["data-student-id", "data-studentid"])
def test_student_id_can_be_verified_from_dedicated_row_metadata(attribute):
    row = Row(student="Example Name", attributes={attribute: "student123"})
    portal.verify_student_row(row, "student123")


def test_student_id_can_be_verified_from_dedicated_child_metadata():
    row = Row(student="Example Name")
    row.children = [Element(attributes={"data-student-id": "student123"})]
    portal.verify_student_row(row, "student123")


@pytest.mark.parametrize("wrong", [{"test": "Maths Test 2"}, {"series": "Batch Tests 2"}])
def test_same_student_with_wrong_suffixed_test_or_category_is_rejected(stale_search, wrong):
    with pytest.raises(LookupError):
        portal.find_evaluate_row(Page([Row(**wrong)]), record())


def test_evaluation_accepts_whole_test_and_category_labels(stale_search):
    row = Row(test="  Maths-Test ", series="Batch  Tests")
    assert portal.find_evaluate_row(Page([row]), record()) is row


def test_navigation_selects_whole_label_instead_of_a_suffixed_name():
    wrong = Row(test="Maths Test 2")
    right = Row(test="  Maths-Test ")
    assert portal.row_named(Page([wrong, right]), "Maths Test") is right


def test_navigation_rejects_sole_partial_name(monkeypatch):
    monkeypatch.setattr(portal, "DEFAULT_TIMEOUT_MS", 0)
    with pytest.raises(LookupError, match="found 0"):
        portal.row_named(Page([Row(test="Maths Test 2")]), "Maths Test")


def test_navigation_rejects_duplicate_whole_labels():
    with pytest.raises(LookupError, match="found 2"):
        portal.row_named(Page([Row(), Row()]), "Maths Test")


def test_navigation_waits_for_the_list_to_render():
    """Live run 3: the exact row was searched before the site had rendered it."""
    right = Row(test="Maths Test")
    page = Page([Row(test="Loading")], later=[right])
    assert portal.row_named(page, "Maths Test") is right
    assert page.polls == 1


@pytest.mark.parametrize("text,expected", [("Maths-Test", True), (" maths  test ", True),
                                          ("Maths Test 2", False), ("Advanced Maths Test", False)])
def test_whole_label_normalizes_only_spacing_and_hyphens(text, expected):
    assert bool(portal.whole_label("Maths Test").search(text)) is expected


def test_decimal_marks_pass_both_workflow_and_actual_uploader_validation():
    report = {"questions": [{"qid": "Q1.a", "score": 1.23456}, {"qid": "Q1.b", "score": 0.33333}],
              "total_score": 1.56789}
    marks = workflow.portal_marks(report)
    rec = record(marks={number: str(mark) for number, mark in marks.items()},
                 total_marks=str(report["total_score"]))
    portal.validate_record(rec)
    assert marks[1] == pytest.approx(1.56789, abs=1e-9)


def test_workflow_rejects_total_that_actual_uploader_would_reject():
    report = {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1.00002}
    with pytest.raises(ValueError, match="do not match"):
        workflow.portal_marks(report)


def test_adapter_does_not_confirm_a_concurrently_changed_assignment(tmp_path):
    page = Page([Row()])
    engine = offline_engine(page, open_allotment_test=lambda *args: None, search=lambda *args: None,
                            allot_student=lambda *args, **kwargs: False)
    adapter = offline_adapter(tmp_path, engine)
    row = {"program_name": "Program", "test_series_name": "Batch Tests",
           "test_name": "Maths Test", "student_id": "student123"}
    options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
    with pytest.raises(RuntimeError, match="assignment changed"):
        adapter.perform("assign", row, options)


def offline_adapter(tmp_path, engine, rejections=None):
    adapter = workflow.Portal.__new__(workflow.Portal)
    adapter.engine, adapter.rejections, adapter.root = engine, rejections, tmp_path
    adapter.sessions, adapter.connect_delay, adapter.playwright = {}, 0, None
    adapter.windows, adapter.evaluator = dict(workflow.Portal.SESSIONS), ""
    adapter.browser = SimpleNamespace(find_pending_row=lambda *args: pytest.fail("no pending lookup expected"),
                                      has_view_upload=lambda page, row: False)
    return adapter


def offline_playwright(page, connect_failures=0, calls=None):
    """A Playwright double: connect_over_cdp fails `connect_failures` times first."""
    context = SimpleNamespace(pages=[page], set_default_timeout=lambda _: None,
                              add_init_script=lambda script: (calls if calls is not None else []).append(("banner", script)))
    browser = SimpleNamespace(contexts=[context], is_connected=lambda: True)
    state = {"failures": connect_failures}

    def connect(url):
        (calls if calls is not None else []).append(("connect", url))
        if state["failures"]:
            state["failures"] -= 1
            raise ConnectionRefusedError("connect ECONNREFUSED " + url)
        return browser
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect),
                                 stop=lambda: (calls if calls is not None else []).append("playwright.stop"))
    return playwright


def offline_engine(page, calls=None, connect_failures=0, **extra):
    calls = calls if calls is not None else []
    playwright = offline_playwright(page, connect_failures, calls)
    chrome = SimpleNamespace(terminate=lambda: calls.append("chrome.terminate"), poll=lambda: None)
    values = dict(
        StudentRecord=portal.StudentRecord, SEL=portal.SEL, DEFAULT_TIMEOUT_MS=100, AbortRun=portal.AbortRun,
        whole_label=portal.whole_label, verify_student_row=portal.verify_student_row, validate_record=lambda record: None,
        # the Students page's status filter and the assignment helpers (assign_copy); real matchers, inert actions
        choose_status=lambda page, status: None, submit_bundle=lambda page, deadline: True, LOGGED_IN_URL_PART="/tev/",
        log=SimpleNamespace(warning=lambda *args: None, info=lambda *args: None),
        select_evaluator=lambda page, email: email, _status_pattern=portal._status_pattern,
        _email_option_matches=portal._email_option_matches, _evaluator_text_matches=portal._evaluator_text_matches,
        start_chrome=lambda: calls.append("start_chrome") or chrome,
        sync_playwright=lambda: calls.append("sync_playwright") or SimpleNamespace(start=lambda: playwright),
        wait_for_manual_login=lambda _: calls.append("login"), site_logged_in=lambda _: True,
        reset_page=lambda *args: calls.append("reset_page"),
        close_chrome=lambda *args: calls.append("close_chrome"),
        save_screenshot=lambda _page, student: calls.append(("screenshot", student)) or f"shots/{student}.png")
    values.update(extra)
    return SimpleNamespace(**values)


def test_adapter_rejection_uses_evaluator_session_guard_and_submit_marker(tmp_path):
    page = Page([Row()])
    calls = []
    engine = offline_engine(page, calls,
                            open_evaluate=lambda _page, program: calls.append(("open_evaluate", program)),
                            evaluate_student=lambda *args, **kwargs: pytest.fail("A rejection must not run the marks upload"))

    def submit_rejection(_page, record, program, series, test, before_submit, verify_row):
        calls.append(("submit", record, program, series, test, verify_row))
        before_submit()
    rejections = SimpleNamespace(RejectionComment=portal_rejections.RejectionComment, submit_rejection=submit_rejection)
    adapter = offline_adapter(tmp_path, engine, rejections)
    source = tmp_path / "copy.pdf"
    source.write_bytes(b"%PDF")
    markers = []
    row = {"program_name": "Program", "test_series_name": "Batch Tests", "test_name": "Maths Test",
           "student_id": "student123", "student_name": "Example"}
    adapter.perform("reject", row, {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"},
                    source, reason="  Not an answer sheet for this paper.  ", before_submit=lambda: markers.append("attempt"))
    assert (engine.CDP_PORT, engine.USER_DATA_DIR) == (9238, tmp_path / "evaluator_profile")
    steps = [c for c in calls if not (isinstance(c, tuple) and c[0] == "banner")]
    assert steps[:4] == ["sync_playwright", "start_chrome", ("connect", "http://127.0.0.1:9238"), "login"]
    assert steps[4] == ("open_evaluate", "Program")
    _, record, program, series, test, verify_row = steps[5]
    assert (program, series, test) == ("Program", "Batch Tests", "Maths Test")
    assert (record.student_id, record.student_name, record.comment, record.paper_path) == (
        "student123", "Example", "Not an answer sheet for this paper.", source)
    # The identity guard is bound to the page and the sheet's name.
    verify_row(Row(student="student123"), "student123")  # ID in the row text
    with pytest.raises(LookupError, match="student ID|identity"):
        verify_row(Row(student="someone_else"), record.student_id)
    assert markers == ["attempt"]
    assert calls[-1] == "reset_page"


def test_adapter_refuses_a_rejection_without_a_comment(tmp_path):
    engine = offline_engine(Page([]), start_chrome=lambda: pytest.fail("Chrome must not start without a comment"))
    adapter = offline_adapter(tmp_path, engine, SimpleNamespace(RejectionComment=portal_rejections.RejectionComment))
    row = {"program_name": "Program", "test_series_name": "Batch Tests", "test_name": "Maths Test", "student_id": "student123"}
    with pytest.raises(ValueError, match="rejection comment"):
        adapter.perform("reject", row, {}, tmp_path / "copy.pdf", reason="   ")


def test_adapter_reads_exam_documents_through_the_evaluator_session(tmp_path):
    page = Page([Row()])
    calls = []
    engine = offline_engine(page, calls,
                            open_evaluate=lambda *args: pytest.fail("The details request needs no page navigation"))
    details = {"data": {"name": "  Maths-Test ", "questionFileId": {"baseUrl": "https://static.example/", "key": "papers/QP Maths.pdf"},
                        "solutions": [{"fileId": {"baseUrl": "https://static.example/", "key": "sol/one.pdf"}},
                                      {"fileId": {"key": "sol/two.pdf"}}, {"fileId": None}]}}

    def api_get(_page, url):
        calls.append(("api", url))
        return details
    papers = SimpleNamespace(api_get=api_get, TEST_URL=portal_papers.TEST_URL, site_files=portal_papers.site_files)
    adapter = offline_adapter(tmp_path, engine)
    adapter.papers = papers
    adapter.download = lambda url: calls.append(("download", url)) or url.encode()
    progress = []
    fetched = adapter.exam_documents({"test_id": "test123", "test_name": "Maths Test"}, progress=progress.append)
    assert (engine.CDP_PORT, engine.USER_DATA_DIR) == (9238, tmp_path / "evaluator_profile")
    steps = [c for c in calls if not (isinstance(c, tuple) and c[0] == "banner")]
    assert steps[:4] == ["sync_playwright", "start_chrome", ("connect", "http://127.0.0.1:9238"), "login"]
    assert steps[4] == ("api", portal_papers.TEST_URL.format(test="test123"))
    assert fetched == {"question_paper": [("QP Maths.pdf", b"https://static.example/papers/QP Maths.pdf")],
                       "answer_key": [("one.pdf", b"https://static.example/sol/one.pdf"), ("two.pdf", b"https://static.pw.live/sol/two.pdf")],
                       "links": {"question_paper": "https://static.example/papers/QP Maths.pdf",
                                 "answer_key": ["https://static.example/sol/one.pdf", "https://static.pw.live/sol/two.pdf"]}}
    assert progress[0] == "Reading the test's files from the portal" and "Downloading" in progress[1]


def test_adapter_refuses_portal_documents_of_a_differently_named_test(tmp_path):
    page = Page([Row()])
    engine = offline_engine(page)
    details = {"data": {"name": "Maths Test 2", "questionFileId": {"baseUrl": "https://static.example/", "key": "qp.pdf"}, "solutions": []}}
    adapter = offline_adapter(tmp_path, engine)
    adapter.papers = SimpleNamespace(api_get=lambda *_: details, TEST_URL=portal_papers.TEST_URL, site_files=portal_papers.site_files)
    adapter.download = lambda url: pytest.fail("Nothing may be downloaded for another test")
    with pytest.raises(ValueError, match="Maths Test 2"):
        adapter.exam_documents({"test_id": "test123", "test_name": "Maths Test"})


def test_prepare_exam_documents_merges_in_order_and_rejects_bad_files():
    import fitz
    def page_pdf(label):
        with fitz.open() as document:
            document.new_page().insert_text((30, 30), label)
            return document.tobytes()
    documents = workflow.prepare_exam_documents({"question_paper": [("qp.pdf", page_pdf("QP"))],
                                                 "answer_key": [("a.pdf", page_pdf("A")), ("b.pdf", page_pdf("B"))]})
    with fitz.open(stream=documents["answer_key"]["data"], filetype="pdf") as merged:
        assert [merged[i].get_text().strip() for i in range(merged.page_count)] == ["A", "B"]
    assert documents["answer_key"]["sources"] == ["a.pdf", "b.pdf"] and documents["question_paper"]["pages"] == 1
    with pytest.raises(ValueError, match="not a readable PDF"):
        workflow.prepare_exam_documents({"question_paper": [("qp.pdf", b"%PDF-broken")], "answer_key": []})
    with pytest.raises(ValueError, match="no question paper or Model Answer"):
        workflow.prepare_exam_documents({"question_paper": [], "answer_key": [("empty.pdf", b"")]})


@pytest.mark.parametrize("url", ["http://static.example/qp.pdf", "https://user:pw@static.example/qp.pdf"])
def test_exam_file_download_refuses_unsupported_addresses(url):
    with pytest.raises(ValueError, match="unsupported"):
        workflow.download_exam_file(url)


# ---------------------------------------------------------------- live-run findings (build 99)

def test_identity_accepted_from_the_search_result_and_the_sheet_name():
    """The live Allotment table shows names, not IDs (first live run, 12 Oct)."""
    row = Row(student="Prakash Kumar")
    page = Page([row], search_value="65d1a2cecd08ee0019e79f78")
    evidence = portal.verify_student_row(row, "65d1a2cecd08ee0019e79f78", page=page, student_name="Prakash Kumar")
    assert "name as in the sheet" in evidence
    assert "no name" in portal.verify_student_row(row, "65d1a2cecd08ee0019e79f78", page=page, student_name="")
    assert "name as in the sheet" in portal.verify_student_row(row, "65D1A2CECD08EE0019E79F78", page=page, student_name="prakash")


def test_identity_refused_when_the_name_or_the_search_disagrees():
    row = Row(student="Prakash Kumar")
    with pytest.raises(LookupError, match="does not show the sheet's name"):
        portal.verify_student_row(row, "65d1a2cecd08ee0019e79f78", page=Page([row], search_value="65d1a2cecd08ee0019e79f78"), student_name="Vedanta Rao")
    with pytest.raises(LookupError, match="does not show the sheet's name"):
        portal.verify_student_row(row, "65d1a2cecd08ee0019e79f78", page=Page([row], search_value="65d1a2cecd08ee0019e79f78"), student_name="Prakashan")
    with pytest.raises(LookupError, match="does not expose"):
        portal.verify_student_row(row, "65d1a2cecd08ee0019e79f78", page=Page([row], search_value="other-student-id"), student_name="Prakash Kumar")
    with pytest.raises(LookupError, match="does not expose"):
        portal.verify_student_row(row, "65d1a2cecd08ee0019e79f78", page=Page([row]), student_name="Prakash Kumar")


def test_identity_accepted_from_the_row_markup():
    row = Row(student="Prakash Kumar", markup='<tr><td><a href="/students/65d1a2cecd08ee0019e79f78">Prakash Kumar</a></td></tr>')
    assert "markup" in portal.verify_student_row(row, "65d1a2cecd08ee0019e79f78")


def test_assignment_and_upload_use_the_live_identity_evidence(stale_search):
    row = Row(student="Prakash Kumar")
    page = Page([row], search_value="65d1a2cecd08ee0019e79f78")
    rec = record(student_id="65d1a2cecd08ee0019e79f78", student_name="Prakash Kumar")
    assert portal.find_evaluate_row(page, rec) is row
    with pytest.raises(LookupError, match="sheet's name"):
        portal.find_evaluate_row(page, record(student_id="65d1a2cecd08ee0019e79f78", student_name="Someone Else"))
    assert not row.checkbox.checked


def test_one_chrome_window_per_account_is_kept_for_the_run(tmp_path):
    page = Page([Row()])
    calls = []
    engine = offline_engine(page, calls, open_allotment_test=lambda *args: None, search=lambda *args: None,
                            allot_student=lambda *args, **kwargs: True,
                            open_evaluate=lambda *args: None, evaluate_student=lambda *args, **kwargs: None)
    adapter = offline_adapter(tmp_path, engine)
    row = {"program_name": "Program", "test_series_name": "Batch Tests", "test_name": "Maths Test", "student_id": "student123"}
    options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
    adapter.perform("assign", row, options)
    adapter.perform("assign", row, options)   # a second copy; the page double lists one student
    assert calls.count("start_chrome") == 1 and calls.count("login") == 1 and calls.count("reset_page") == 2
    checked = tmp_path / "checked.pdf"
    checked.write_bytes(b"%PDF")
    adapter.perform("upload", row, options, checked, {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1})
    assert calls.count("start_chrome") == 2
    assert [c for c in calls if isinstance(c, tuple) and c[0] == "connect"] == [
        ("connect", "http://127.0.0.1:9237"), ("connect", "http://127.0.0.1:9238")]
    # Both windows hang off ONE Playwright instance: a second sync_playwright() in the
    # same thread is refused by Playwright ("Sync API inside the asyncio loop", live run 3).
    assert calls.count("sync_playwright") == 1
    adapter.close()
    assert calls.count("close_chrome") == 2 and calls.count("playwright.stop") == 1 and adapter.sessions == {}
    assert adapter.playwright is None


def test_debug_port_connection_is_retried_before_giving_up(tmp_path):
    page = Page([Row()])
    calls = []
    engine = offline_engine(page, calls, connect_failures=2, open_allotment_test=lambda *args: None,
                            search=lambda *args: None, allot_student=lambda *args, **kwargs: True)
    adapter = offline_adapter(tmp_path, engine)
    row = {"program_name": "Program", "test_series_name": "Batch Tests", "test_name": "Maths Test", "student_id": "student123"}
    adapter.perform("assign", row, {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"})
    assert calls.count("start_chrome") == 3 and calls.count("chrome.terminate") == 2 and calls.count("login") == 1
    engine = offline_engine(page, calls, connect_failures=5)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        offline_adapter(tmp_path, engine).session("admin")


@pytest.mark.parametrize("kind", ["admin", "evaluator"])
@pytest.mark.parametrize("failure", [portal.AbortRun, RuntimeError])
def test_login_failure_closes_the_newly_opened_account_window(tmp_path, kind, failure):
    calls = []
    engine = offline_engine(Page([]), calls)
    adapter = offline_adapter(tmp_path, engine)

    def failed_login(page):
        # The window must be tracked before login can be interrupted, so Stop
        # and a timed-out login cannot leave its port/profile open indefinitely.
        assert adapter.sessions[kind]["page"] is page
        raise failure("login interrupted")

    engine.wait_for_manual_login = failed_login
    with pytest.raises(failure, match="login interrupted"):
        adapter.session(kind)
    assert calls.count("start_chrome") == 1
    assert calls.count("close_chrome") == 1
    assert calls.count("playwright.stop") == 1
    assert adapter.sessions == {} and adapter.playwright is None
    adapter.close()
    assert calls.count("close_chrome") == 1


def test_portal_failures_are_photographed_and_submit_marker_is_forwarded(tmp_path):
    page = Page([Row()])
    calls = []
    def failing_open(*args):
        raise LookupError("'Complete Programe' was not found in the list on the site")
    engine = offline_engine(page, calls, open_allotment_test=failing_open)
    adapter = offline_adapter(tmp_path, engine)
    row = {"program_name": "Complete Programe", "test_series_name": "Batch Tests", "test_name": "Maths Test", "student_id": "student123"}
    options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
    with pytest.raises(RuntimeError, match=r"Complete Programe.*\(screenshot: student123\.png\)"):
        adapter.perform("assign", row, options)
    assert ("screenshot", "student123") in calls and calls[-1] == "reset_page"
    forwarded = {}
    engine = offline_engine(page, calls, open_allotment_test=lambda *args: None, search=lambda *args: None,
                            allot_student=lambda _p, _r, _c, before_submit: forwarded.update(assign=before_submit) or True,
                            open_evaluate=lambda *args: None,
                            evaluate_student=lambda _p, _ctx, _r, _c, before_submit: forwarded.update(upload=before_submit))
    adapter = offline_adapter(tmp_path, engine)
    fired = []
    marker = lambda: fired.append("marker")
    adapter.perform("assign", row, options, before_submit=marker)
    checked = tmp_path / "checked.pdf"
    checked.write_bytes(b"%PDF")
    adapter.perform("upload", row, options, checked, {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1}, before_submit=marker)
    forwarded["assign"]()   # wrapped too: a click marks that a portal 'Unable to fetch task' is no longer a plain retry
    assert fired == ["marker"]
    forwarded["upload"]()   # the upload marker is wrapped: it also records that the file reached the portal
    assert fired == ["marker", "marker"] and row["portal_upload"] is True


def test_stop_requests_are_not_photographed_or_rewrapped(tmp_path):
    page = Page([Row()])
    calls = []
    def stopping(*args):
        raise portal.AbortRun("Stopped by you")
    engine = offline_engine(page, calls, open_allotment_test=stopping)
    adapter = offline_adapter(tmp_path, engine)
    with pytest.raises(portal.AbortRun):
        adapter.perform("assign", {"program_name": "P", "test_series_name": "S", "test_name": "T", "student_id": "student123"},
                        {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"})
    assert not any(isinstance(c, tuple) and c[0] == "screenshot" for c in calls)


def test_a_second_sync_playwright_in_the_same_thread_is_refused_by_playwright():
    """The mechanism behind the live error: one Playwright instance must serve both windows."""
    from playwright.sync_api import Error, sync_playwright
    first = sync_playwright().start()
    try:
        with pytest.raises(Error, match="Sync API inside the asyncio loop"):
            sync_playwright().start()
    finally:
        first.stop()
    second = sync_playwright().start()  # after stop() a new instance is fine again
    second.stop()


# ---------------------------------------------------------- assignment reads the portal's state

def assignment_fixture(tmp_path, by_status, calls=None, option="Teacher One (teacher@example.com)"):
    """A Students page whose status filter shows by_status[status] rows; the engine's primitives as doubles."""
    calls = calls if calls is not None else []
    page = Page([])

    def choose_status(_page, status):
        calls.append(("filter", status))
        if status not in by_status:
            raise LookupError(f"The status filter with '{status}' is not on this page")
        page.rows = Rows(by_status[status])

    def allot_student(_page, rec, confirm, before_submit=lambda: None):
        calls.append(("allot", rec.student_id)); before_submit(); return True

    engine = offline_engine(page, calls, open_allotment_test=lambda *args: calls.append("open"),
                            search=lambda _page, value: calls.append(("search", value)),
                            choose_status=choose_status, allot_student=allot_student,
                            select_evaluator=lambda _page, email: calls.append(("select", email)) or option,
                            submit_bundle=lambda _page, deadline: calls.append(("submit", deadline.value)) or True,
                            _status_pattern=portal._status_pattern, _email_option_matches=portal._email_option_matches,
                            _evaluator_text_matches=portal._evaluator_text_matches)
    adapter = offline_adapter(tmp_path, engine)
    adapter.status_filters = lambda _page: list(by_status)
    return adapter, page, calls


ASSIGN_ROW = {"program_name": "Program", "test_series_name": "Batch Tests", "test_name": "Maths Test",
              "student_id": "student123", "student_name": "Example Student"}
ASSIGN_OPTIONS = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}


def test_a_submitted_copy_is_assigned(tmp_path):
    adapter, page, calls = assignment_fixture(tmp_path, {"Submitted": [Row()], "In Evaluation": []})
    markers = []
    outcome = adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS, before_submit=lambda: markers.append("marker"))
    assert outcome["result"] == "assigned" and markers == ["marker"]
    assert ("filter", "Submitted") in calls and ("allot", "student123") in calls
    assert not any(isinstance(c, tuple) and c[0] == "submit" for c in calls)


def test_a_copy_already_with_this_evaluator_is_left_alone(tmp_path):
    rows = {"Submitted": [], "In Evaluation": [Row(evaluator="teacher@example.com", status="In Evaluation")]}
    adapter, page, calls = assignment_fixture(tmp_path, rows)
    outcome = adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS, before_submit=lambda: pytest.fail("nothing is submitted"))
    assert outcome == {"result": "already", "status": "In Evaluation", "evaluator": "teacher@example.com"}
    assert not any(isinstance(c, tuple) and c[0] in ("allot", "select", "submit") for c in calls)
    # Shown by name: the dropdown option resolves the name, nothing is submitted either.
    rows = {"Submitted": [], "In Evaluation": [Row(evaluator="Teacher One", status="In Evaluation")]}
    adapter, page, calls = assignment_fixture(tmp_path, rows)
    outcome = adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS, before_submit=lambda: pytest.fail("nothing is submitted"))
    assert outcome["result"] == "already" and ("select", "teacher@example.com") in calls
    assert not page.rows.elements[0].checkbox.checked
    assert not any(isinstance(c, tuple) and c[0] == "submit" for c in calls)


def test_a_copy_with_another_evaluator_is_reassigned_with_the_intent_marker_before_submit(tmp_path):
    rows = {"Submitted": [], "In Evaluation": [Row(evaluator="Someone Else (other@example.com)", status="In Evaluation")]}
    adapter, page, calls = assignment_fixture(tmp_path, rows)
    outcome = adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS, before_submit=lambda: calls.append("marker"))
    assert outcome == {"result": "reassigned", "status": "In Evaluation", "evaluator": "Someone Else (other@example.com)"}
    assert page.rows.elements[0].checkbox.checked and page.deadline.value == "2027-01-01T12:00"
    assert calls.index("marker") < calls.index(("submit", "2027-01-01T12:00"))
    assert not any(isinstance(c, tuple) and c[0] == "allot" for c in calls)


def test_an_unacknowledged_reassignment_fails_after_the_marker(tmp_path):
    rows = {"Submitted": [], "In Evaluation": [Row(evaluator="other@example.com", status="In Evaluation")]}
    adapter, page, calls = assignment_fixture(tmp_path, rows)
    adapter.engine.submit_bundle = lambda _page, deadline: False
    markers = []
    with pytest.raises(RuntimeError, match="did not acknowledge"):
        adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS, before_submit=lambda: markers.append("marker"))
    assert markers == ["marker"]   # the workflow holds the copy as "assignment uncertain" for the operator


def test_a_finished_copy_is_reported_not_touched(tmp_path):
    rows = {"Submitted": [], "In Evaluation": [], "In Review": [], "Finished": [Row(evaluator="x@example.com", status="Finished")]}
    adapter, page, calls = assignment_fixture(tmp_path, rows)
    outcome = adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS, before_submit=lambda: pytest.fail("nothing is submitted"))
    assert outcome == {"result": "finished", "status": "Finished", "evaluator": "x@example.com"}
    assert [c[1] for c in calls if isinstance(c, tuple) and c[0] == "filter"] == ["Submitted", "In Evaluation", "In Review", "Finished"]


def test_a_copy_listed_nowhere_or_twice_stops(tmp_path):
    adapter, page, calls = assignment_fixture(tmp_path, {"Submitted": [], "In Evaluation": []})
    with pytest.raises(RuntimeError, match="not listed under any status"):
        adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS)
    adapter, page, calls = assignment_fixture(tmp_path, {"Submitted": [Row(), Row()], "In Evaluation": []})
    with pytest.raises(RuntimeError, match="returned 2 rows"):
        adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS)


def test_a_page_without_the_status_filter_is_searched_as_it_is(tmp_path):
    adapter, page, calls = assignment_fixture(tmp_path, {})
    page.rows = Rows([Row(evaluator="teacher@example.com", status="In Evaluation")])
    adapter.status_filters = lambda _page: ["Submitted", "In Evaluation"]
    outcome = adapter.perform("assign", ASSIGN_ROW, ASSIGN_OPTIONS, before_submit=lambda: pytest.fail("nothing is submitted"))
    assert outcome["result"] == "already"


# ---------------------------------------------------------- one window per evaluator account

def test_an_evaluator_portal_gets_its_own_port_and_profile(tmp_path):
    adapter = workflow.Portal(tmp_path, evaluator=" Second.Teacher@example.com ", evaluator_port=9239)
    assert adapter.evaluator == "Second.Teacher@example.com"
    assert adapter.windows["evaluator"] == (9239, workflow.account_profile("evaluator", "Second.Teacher@example.com"))
    assert adapter.windows["admin"] == workflow.Portal.SESSIONS["admin"]
    assert workflow.Portal(tmp_path).windows == workflow.Portal.SESSIONS
    assert workflow.account_slug("A.B+c@x.com") == "a_b_c_x_com" and workflow.account_slug("") == "evaluator"


@pytest.mark.parametrize("kind", ["admin", "evaluator"])
def test_accounts_with_the_same_slug_keep_distinct_browser_profiles(tmp_path, kind):
    first = workflow.Portal(tmp_path, **{kind: "a.b@example.com"})
    second = workflow.Portal(tmp_path, **{kind: "a+b@example.com"})
    same_account = workflow.Portal(tmp_path, **{kind: " A.B@EXAMPLE.COM "})
    assert workflow.account_slug("a.b@example.com") == workflow.account_slug("a+b@example.com")
    assert first.windows[kind][1] != second.windows[kind][1]
    assert first.windows[kind][1] == same_account.windows[kind][1]


def test_every_login_page_says_which_account_to_use(tmp_path):
    page = Page([Row()])
    calls = []
    shown_now = []
    page.evaluate = shown_now.append
    engine = offline_engine(page, calls, open_evaluate=lambda *args: None, evaluate_student=lambda *args, **kwargs: None,
                            open_allotment_test=lambda *args: None, search=lambda *args: None,
                            allot_student=lambda *args, **kwargs: True, LOGGED_IN_URL_PART="/tev/",
                            log=SimpleNamespace(warning=lambda *args: None))
    adapter = offline_adapter(tmp_path, engine)
    adapter.admin = "admin@example.com"
    adapter.evaluator = "teacher@example.com"
    adapter.windows["evaluator"] = (9238, "evaluator_profile_teacher_example_com")
    row = {"program_name": "P", "test_series_name": "S", "test_name": "T", "student_id": "student123"}
    options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
    adapter.perform("assign", row, options)
    banners = [script for kind, script in (c for c in calls if isinstance(c, tuple) and c[0] == "banner")]
    assert len(banners) == 1 and "ADMIN account admin@example.com" in banners[0] and "/tev/" in banners[0]
    labels = lambda: [script for script in shown_now if "ai-checker-login-banner" in script]   # the account check also reads the page
    assert labels() == banners  # the existing login page is labeled immediately
    checked = tmp_path / "checked.pdf"
    checked.write_bytes(b"%PDF")
    adapter.perform("upload", row, options, checked, {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1})
    banners = [script for kind, script in (c for c in calls if isinstance(c, tuple) and c[0] == "banner")]
    assert len(banners) == 2 and "EVALUATOR account teacher@example.com" in banners[1]
    assert labels() == banners
    assert engine.USER_DATA_DIR == tmp_path / "evaluator_profile_teacher_example_com"


# ------------------------------------------------- lost confirmations are checked on the portal

def test_a_lost_upload_confirmation_is_read_from_the_pending_and_evaluated_lists(tmp_path):
    page = Page([Row()])
    engine = offline_engine(page, open_evaluate=lambda *args: None, first_line=portal.first_line)
    adapter = offline_adapter(tmp_path, engine)
    row = {"program_name": "P", "test_series_name": "S", "test_name": "T", "student_id": "student123", "student_name": "Example"}
    options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
    def gone(*args):
        raise LookupError("Not Pending: matching test is in Evaluated. No upload or resubmission attempted.")
    adapter.browser = SimpleNamespace(find_pending_row=gone, has_view_upload=lambda page, row: False)
    assert adapter.perform("verify_upload", row, options) == {"result": "done", "detail": "Not Pending: matching test is in Evaluated"}
    adapter.browser = SimpleNamespace(find_pending_row=lambda *args: page.rows.elements[0], has_view_upload=lambda page, row: True)
    assert adapter.perform("verify_reject", row, options) == {"result": "not_done", "detail": "still in the Pending list"}
    def missing(*args):
        raise LookupError("Matching test was not found in the available evaluation statuses after a fresh-page retry.")
    adapter.browser = SimpleNamespace(find_pending_row=missing, has_view_upload=lambda page, row: False)
    assert adapter.perform("verify_upload", row, options)["result"] == "unknown"


def test_a_lost_assignment_confirmation_is_read_from_the_students_page(tmp_path):
    row = {"program_name": "P", "test_series_name": "S", "test_name": "T", "student_id": "student123", "student_name": "Example"}
    options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
    adapter, page, calls = assignment_fixture(tmp_path, {"Submitted": [Row()], "In Evaluation": []})
    assert adapter.perform("verify_assign", row, options) == {"result": "not_done", "detail": "still Submitted"}
    adapter, page, calls = assignment_fixture(tmp_path, {"Submitted": [], "In Evaluation": [Row(evaluator="teacher@example.com", status="In Evaluation")]})
    assert adapter.perform("verify_assign", row, options)["result"] == "done"
    adapter, page, calls = assignment_fixture(tmp_path, {"Submitted": [], "In Evaluation": [Row(evaluator="Someone Else (other@example.com)", status="In Evaluation")]})
    assert adapter.perform("verify_assign", row, options)["result"] == "not_done" and not page.rows.elements[0].checkbox.checked
    adapter, page, calls = assignment_fixture(tmp_path, {"Submitted": [], "In Evaluation": [], "Finished": [Row(status="Finished")]})
    assert adapter.perform("verify_assign", row, options) == {"result": "done", "detail": "status Finished"}
    adapter, page, calls = assignment_fixture(tmp_path, {"Submitted": [], "In Evaluation": []})
    assert adapter.perform("verify_assign", row, options)["result"] == "unknown"


def test_a_retried_upload_does_not_resend_a_copy_already_on_the_portal(tmp_path):
    page = Page([Row()])
    calls = []
    engine = offline_engine(page, calls, open_evaluate=lambda *args: None,
                            find_evaluate_row=lambda _page, rec: page.rows.elements[0],
                            upload_paper=lambda *args: calls.append("upload_paper"),
                            click_menu_item=lambda _page, _row, item: calls.append(("menu", item)),
                            fill_evaluation_modal=lambda _page, rec, confirm, before_submit: (before_submit(), calls.append("submitted")),
                            evaluate_student=lambda *args, **kwargs: pytest.fail("the resuming path is used after an upload"))
    adapter = offline_adapter(tmp_path, engine)
    adapter.browser = SimpleNamespace(find_pending_row=None, has_view_upload=lambda _page, _row: True)
    row = {"program_name": "P", "test_series_name": "S", "test_name": "T", "student_id": "student123", "portal_upload": True}
    options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
    checked = tmp_path / "checked.pdf"
    checked.write_bytes(b"%PDF")
    markers = []
    adapter.perform("upload", row, options, checked, {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1}, before_submit=lambda: markers.append("marker"))
    assert "upload_paper" not in calls and ("menu", "View Upload") in calls and "submitted" in calls and markers == ["marker"]
    # Without a previous upload by this run, the supplied evaluate_student path is used and marks the row.
    engine.evaluate_student = lambda _page, _ctx, rec, _confirm, before_submit: before_submit()
    fresh = dict(row, portal_upload=False)
    adapter.perform("upload", fresh, options, checked, {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1})
    assert fresh["portal_upload"] is True


def test_uploads_from_the_wrong_evaluator_account_are_refused_with_both_names(tmp_path):
    page = Page([Row()])
    engine = offline_engine(page, open_evaluate=lambda *args: None, evaluate_student=lambda *args, **kwargs: pytest.fail("no upload from the wrong account"))
    adapter = offline_adapter(tmp_path, engine)
    adapter.signed_in_email = lambda _page: "other.teacher@example.com"
    row = {"program_name": "P", "test_series_name": "S", "test_name": "T", "student_id": "student123"}
    options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
    checked = tmp_path / "checked.pdf"
    checked.write_bytes(b"%PDF")
    with pytest.raises(RuntimeError, match="signed in as other.teacher@example.com, but this run's evaluator is teacher@example.com"):
        adapter.perform("upload", row, options, checked, {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1})
    adapter.signed_in_email = lambda _page: "Teacher@Example.com"   # same account, different case
    engine.evaluate_student = lambda *args, **kwargs: None
    adapter.perform("upload", row, options, checked, {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1})


def test_the_signed_in_email_is_read_from_the_portals_user_details(tmp_path):
    adapter = offline_adapter(tmp_path, offline_engine(Page([])))
    class Stored:
        def __init__(self, value):
            self.value = value
        def evaluate(self, script):
            return self.value
    assert adapter.signed_in_email(Stored('{"data": {"name": "T", "email": "teacher@example.com", "roles": ["evaluator"]}}')) == "teacher@example.com"
    assert adapter.signed_in_email(Stored('{"user": {"userName": "x.y@pw.live"}}')) == "x.y@pw.live"
    assert adapter.signed_in_email(Stored("")) == "" and adapter.signed_in_email(Stored("not json")) == "" and adapter.signed_in_email(Stored(1000)) == ""
