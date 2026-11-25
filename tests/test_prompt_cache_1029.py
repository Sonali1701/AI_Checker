"""29 Oct 2026: every copy of a test reads the paper, key and scheme from the prompt cache.

The stored jobs showed later copies reading back only the system prompt: the booklet's
own notes (answer map, inventory) sat inside the paper text, so everything after them
differed per copy. The workflow sent one copy per job and cached nothing at all.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader
import server
from grader import Criterion, MarksItem, MarksScheme, QuestionMark

MODEL = "claude-haiku-4-5"
SCHEME = MarksScheme(total=2, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=1)])
PAPER = "Q1 Solve x + 1 = 3. (1 mark)\nQ2 Solve y - 1 = 2. (1 mark)"


@pytest.fixture(autouse=True)
def _fresh_cache_state():
    grader._CACHE_WARM.clear()
    grader._CACHE_WRITING.clear()
    yield
    grader._CACHE_WARM.clear()
    grader._CACHE_WRITING.clear()


def page(colour):
    data = BytesIO()
    Image.new("RGB", (60, 80), colour).save(data, format="PNG")
    return data.getvalue()


def reply():
    rows = {qid: QuestionMark(qid=qid, score=1, max_score=1, page=1, y_fraction=.2,
                              anchor_line_id="P1L1", mark_style="single",
                              criteria=[Criterion(description="Correct", awarded=1, max=1, met=True,
                                                  anchor_quote="x = 2", step_line_id="P1L1")])
            .model_dump(exclude={"qid"}) for qid in ("Q1", "Q2")}
    return {"stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": json.dumps({"questions": rows})}]}


def grade_copy(student_png, notes, *, cache_ttl="5m"):
    """The request one copy's grading call sends."""
    sent = []

    def generate(token, **kwargs):
        sent.append(kwargs["request"])
        return reply()

    with patch.object(grader.pw_access, "claude_generate", side_effect=generate):
        grader.grade_answer_sheet([], [student_png], answer_key_text="Q1 x = 2. Q2 y = 3.",
                                  model=MODEL, google_token="token", task_id="task",
                                  subject="Maths", student_class="10", marks_scheme=SCHEME,
                                  total_marks=2, qp_text=PAPER, cache_ttl=cache_ttl,
                                  reconcile_output=False, booklet_notes=notes)
    return sent[0]


def texts(request):
    return [block.get("text", "") for block in request["messages"][0]["content"]]


class TestTheSharedPrefixIsTheSameOnEveryCopy:
    def test_two_copies_of_a_test_send_the_same_cached_prefix(self):
        first = grade_copy(page("white"), "=== DETERMINISTIC OCR ANSWER MAP ===\n- Q1: page(s) 1")
        second = grade_copy(page("grey"), "=== DETERMINISTIC OCR ANSWER MAP ===\n- Q2: page(s) 1")
        assert grader._cached_prefix_digest(MODEL, first) == grader._cached_prefix_digest(MODEL, second)

    def test_the_booklet_notes_come_after_the_cache_boundary(self):
        request = grade_copy(page("white"), "=== DETERMINISTIC OCR ANSWER MAP ===\n- Q1: page(s) 1")
        content = request["messages"][0]["content"]
        boundary = max(i for i, block in enumerate(content) if block.get("cache_control"))
        notes = next(i for i, text in enumerate(texts(request)) if "ANSWER MAP" in text)
        sheet = next(i for i, text in enumerate(texts(request)) if "STUDENT'S ANSWER SHEET" in text)
        assert boundary < notes < sheet
        assert not any("ANSWER MAP" in text for text in texts(request)[:boundary + 1])

    def test_the_system_prompt_is_cached_on_the_same_ttl(self):
        request = grade_copy(page("white"), "")
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_a_copy_that_buys_no_cache_marks_no_boundary_and_keeps_its_notes(self):
        request = grade_copy(page("white"), "=== DETERMINISTIC OCR ANSWER MAP ===", cache_ttl="")
        assert grader._cached_prefix_digest(MODEL, request) is None
        assert any("ANSWER MAP" in text for text in texts(request))

    def test_page_windows_of_every_copy_share_the_prefix_too(self):
        prefix, cc = grader._window_prefix_blocks([], None, "Key", PAPER, "10", "Maths", None, 2,
                                                  SCHEME, "5m")
        assert prefix[-1]["cache_control"] == cc


def request_for(tail, *, cached=True):
    control = {"cache_control": {"type": "ephemeral"}} if cached else {}
    return {"system": [{"type": "text", "text": "marking rules", **control}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "paper and key", **control},
                {"type": "text", "text": tail}]}]}


def send_together(requests, *, fail_first=False, seconds=.3):
    """Send the requests from parallel threads; (start, end) of each provider call."""
    spans, lock, calls = [], threading.Lock(), []

    def generate(token, **kwargs):
        start = time.monotonic()
        with lock:
            calls.append(start)
            first = len(calls) == 1
        time.sleep(seconds)
        with lock:
            spans.append((start, time.monotonic()))
        if fail_first and first:
            raise RuntimeError("proxy error")
        return {"content": []}

    def one(request):
        try:
            grader._claude_call("token", task_id="task", model=MODEL, request=request,
                                filename="copy.pdf", count=1)
        except RuntimeError:
            pass

    with patch.object(grader.pw_access, "claude_generate", side_effect=generate):
        threads = [threading.Thread(target=one, args=(request,)) for request in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
    return sorted(spans)


class TestCopiesSentTogetherPayOneWrite:
    def test_the_first_copy_writes_alone_and_the_rest_wait_for_it(self):
        spans = send_together([request_for(f"copy {n}") for n in range(4)])
        (first_start, first_end), *rest = spans
        assert len(rest) == 3
        assert all(start >= first_end - .01 for start, _ in rest)
        # ...and the rest go together once the entry is there.
        assert max(start for start, _ in rest) - min(start for start, _ in rest) < .2

    def test_a_writer_that_fails_lets_the_next_copy_write(self):
        spans = send_together([request_for(f"copy {n}") for n in range(3)], fail_first=True)
        assert len(spans) == 3
        (_, first_end), (second_start, second_end), (third_start, _) = spans
        assert second_start >= first_end - .01 and third_start >= second_end - .01

    def test_requests_without_a_cache_boundary_never_wait(self):
        spans = send_together([request_for(f"copy {n}", cached=False) for n in range(3)])
        assert spans[-1][0] - spans[0][0] < .2

    def test_a_warm_prefix_is_read_by_copies_sent_together(self):
        send_together([request_for("copy 0")], seconds=0)
        spans = send_together([request_for(f"copy {n}") for n in range(1, 4)])
        assert spans[-1][0] - spans[0][0] < .2

    def test_an_entry_past_its_ttl_is_written_again_by_one_copy(self):
        digest, ttl = grader._cached_prefix_digest(MODEL, request_for("copy 0"))
        grader._CACHE_WARM[digest] = time.monotonic() - ttl
        spans = send_together([request_for(f"copy {n}") for n in range(3)])
        (_, first_end), *rest = spans
        assert all(start >= first_end - .01 for start, _ in rest)

    def test_a_slow_writer_holds_the_others_only_so_long(self, monkeypatch):
        monkeypatch.setattr(grader, "CACHE_WRITER_WAIT_SECONDS", .2)
        spans = send_together([request_for(f"copy {n}") for n in range(2)], seconds=.6)
        assert spans[1][0] - spans[0][0] < .45

    def test_the_schema_and_the_ttl_are_part_of_the_entry(self):
        plain = request_for("copy")
        schema = {**plain, "output_config": {"format": {"type": "json_schema", "schema": {"type": "object"}}}}
        hour = request_for("copy")
        hour["messages"][0]["content"][0]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        digests = {grader._cached_prefix_digest(MODEL, r) for r in (plain, schema, hour)}
        assert len(digests) == 3
        assert grader._cached_prefix_digest(MODEL, hour)[1] == 3600
        assert grader._cached_prefix_digest("claude-sonnet-4-6", plain) != grader._cached_prefix_digest(MODEL, plain)


class TestTheWorkflowBuysTheCache:
    @pytest.mark.parametrize("copies, ttl", [(None, ""), (1, ""), (2, "5m"), (40, "5m"), ("x", "")])
    def test_five_minutes_while_other_copies_of_the_test_are_to_check(self, copies, ttl):
        options = {} if copies is None else {"copies_to_check": copies}
        assert server._workflow_cache_ttl(options) == ttl

    @pytest.mark.parametrize("chosen, sheets, ttl", [(None, 1, ""), (None, 2, "5m"), (None, 5, "1h"),
                                                     ("5m", 1, "5m"), ("", 5, "")])
    def test_a_job_uses_its_callers_choice_before_its_own_size(self, chosen, sheets, ttl):
        payload = {"sheets": [("copy.pdf", b"")] * sheets}
        if chosen is not None:
            payload["cache_ttl"] = chosen
        assert server._job_cache_ttl(payload) == ttl

    def test_each_copy_tells_the_grader_how_many_of_its_test_are_left(self, tmp_path):
        import test_integrated_workflow as harness
        seen = []

        def grade(folder, source, options, authorization):
            seen.append(options["copies_to_check"])
            return {"release": True, "pdf": harness.pdf(), "report": harness.report(), "error": ""}

        store = harness.store(tmp_path)
        store.grader = grade
        rows = [{**harness.row(), "submission_id": f"copy{n}", "student_id": f"student{n}"}
                for n in range(3)]
        chosen = {**harness.options(), "copies_in_flight": 1}
        run = store.create(rows, chosen, "owner", harness.pdf(), harness.pdf())
        store.work(run["id"], "token")
        assert seen == [3, 2, 1]
        assert server._workflow_cache_ttl({"copies_to_check": seen[-1]}) == ""


def test_a_strict_copy_sends_its_answer_map_apart_from_the_paper_text():
    """Through the real pipeline: the paper text the grader caches carries no booklet notes."""
    import base64
    import mathpix
    import pipeline
    from test_coverage_recovery import fixture

    _, images, pages, rows, _, cfg, scheme = fixture()
    source = fitz.open()
    for image in images:
        source.new_page(width=595, height=842).insert_image(fitz.Rect(0, 0, 595, 842), stream=image)
    sheet = source.tobytes()
    source.close()
    read = []

    def ocr_response(*args, request, **kwargs):
        image = Image.open(BytesIO(base64.b64decode(request["src"].split(",", 1)[1])))
        page_ocr = pages[len(read)]
        read.append(1)
        sx, sy = image.width / page_ocr.image_width, image.height / page_ocr.image_height
        return {"line_data": [{"type": "text", "text": line.text,
                               "cnt": [[line.bbox[0] * sx, line.bbox[1] * sy],
                                       [line.bbox[2] * sx, line.bbox[3] * sy]],
                               "confidence": .99} for line in page_ocr.lines]}

    sent = []

    def grade(**kwargs):
        sent.append(kwargs)
        raise RuntimeError("stop after the grading request")

    with patch.object(mathpix.pw_access, "mathpix_ocr", side_effect=ocr_response), \
         patch.object(pipeline, "ocr_all_pages",
                      side_effect=lambda imgs, **kw: mathpix.ocr_all_pages(imgs, max_workers=1, **kw)), \
         patch.object(pipeline, "claude_grade", side_effect=grade),          patch.object(grader, "_claude_call", side_effect=RuntimeError("no network in tests")),          patch.object(grader, "_gpt_call", side_effect=RuntimeError("no network in tests")):
        try:
            pipeline.grade_sheet(qp_pngs=[], sa_bytes=sheet, filename="copy.pdf", ak_pngs=[],
                                 ak_text="Award each correct result its question maximum.",
                                 marks_scheme=scheme, cfg=cfg, student_class="10", subject="Maths",
                                 total_marks=13, use_mathpix=True,
                                 qp_text="Maximum Marks: 13\n" + "\n".join(
                                     f"{q.qid}. Calculate. ({q.max_score:g} Marks)" for q in rows))
        except Exception:  # noqa: BLE001 - only the request matters here
            pass
    assert sent, "the grading call was never reached"
    assert "ANSWER MAP" not in sent[0]["qp_text"]
    assert sent[0]["booklet_notes"].startswith("=== DETERMINISTIC OCR ANSWER MAP ===")
    assert "Q1" in sent[0]["booklet_notes"]
