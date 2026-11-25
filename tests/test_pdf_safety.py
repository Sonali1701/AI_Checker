# -*- coding: utf-8 -*-
"""Learners' PDFs that stopped the check (Autopilot of 9 Nov 2026), rebuilt byte by byte."""
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdf_safety  # noqa: E402
import pipeline  # noqa: E402
from grader import pdf_or_image_to_pngs  # noqa: E402

fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)


def written(pages=2):
    document = fitz.open()
    for number in range(pages):
        document.new_page().insert_text((72, 72), f"Answer {number + 1} written here")
    return document


def picture(document, page):
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60), False)
    pixmap.clear_with(180)
    return page.insert_image(fitz.Rect(100, 100, 300, 300), stream=pixmap.tobytes("png"))


def text_of(data):
    with fitz.open(stream=data, filetype="pdf") as document:
        return " ".join(page.get_text() for page in document)


def refuses_to_draw(data):
    with pytest.raises(Exception, match="cycle|not a stream"):
        pdf_or_image_to_pngs(data, "copy.pdf")


def parent_is_itself():
    document = written(3)
    for number in range(3):
        document.xref_set_key(document.page_xref(number), "Parent", f"{document.page_xref(0)} 0 R")
    return document.tobytes()


def test_blank_pages_are_left_white_not_refused():
    document = written(1)
    document.new_page()
    target = fitz.open()
    page = target.new_page()
    with pytest.raises(ValueError, match="nothing to show"):
        page.show_pdf_page(page.rect, document, 1)
    assert pdf_safety.show_source_page(page, page.rect, document, 1) is False
    assert pdf_safety.show_source_page(page, page.rect, document, 0) is True
    assert "Answer 1" in page.get_text()


def test_a_copy_with_a_blank_page_counts_as_copyable():
    document = written(2)
    document.new_page()
    assert pdf_safety.every_page_copies(document.tobytes())


def test_pages_whose_parent_is_themselves_are_mended_with_their_text():
    data = parent_is_itself()
    refuses_to_draw(data)
    mended, pngs = pipeline._render_copy(data, "copy.pdf")
    assert len(pngs) == 3 and mended != data
    for number in (1, 2, 3):
        assert f"Answer {number}" in text_of(mended)
    assert pdf_safety.every_page_copies(mended)


def test_an_image_whose_mask_lost_its_data_keeps_the_image():
    document = written(1)
    image = picture(document, document[0])
    fake = document.get_new_xref()
    document.update_object(fake, "<</Type/XObject/Subtype/Image/Width 1/Height 1/ColorSpace/DeviceGray/BitsPerComponent 8/Length 0>>")
    document.xref_set_key(image, "SMask", f"{fake} 0 R")
    data = document.tobytes()
    refuses_to_draw(data)
    mended, pngs = pipeline._render_copy(data, "copy.pdf")
    assert len(pngs) == 1 and "Answer 1" in text_of(mended)
    with fitz.open(stream=mended, filetype="pdf") as opened:
        assert len(opened[0].get_images()) == 1


def test_a_healthy_copy_is_not_rewritten():
    data = written(2).tobytes()
    assert pipeline._render_copy(data, "copy.pdf")[0] is data


def test_a_refusal_found_only_when_copying_is_mended_before_the_paid_check(monkeypatch):
    data = written(1).tobytes()
    answers = iter([False])
    monkeypatch.setattr(pdf_safety, "every_page_copies", lambda _data: next(answers))
    mended, pngs = pipeline._render_copy(data, "copy.pdf")
    assert len(pngs) == 1 and "Answer 1" in text_of(mended)


def test_damage_that_cannot_be_mended_still_fails(monkeypatch):
    data = parent_is_itself()
    monkeypatch.setattr(pdf_safety, "repair", lambda _data: _data)
    with pytest.raises(Exception, match="cycle"):
        pipeline._render_copy(data, "copy.pdf")


def test_a_copy_that_draws_goes_on_as_it_came_when_the_mend_breaks(monkeypatch):
    data = written(1).tobytes()
    monkeypatch.setattr(pdf_safety, "every_page_copies", lambda _data: False)

    def broken(_data):
        raise RuntimeError("repair blew up")
    monkeypatch.setattr(pdf_safety, "repair", broken)
    returned, pngs = pipeline._render_copy(data, "copy.pdf")
    assert returned is data and len(pngs) == 1


def test_a_repair_that_itself_breaks_reports_the_original_refusal(monkeypatch):
    data = parent_is_itself()

    def broken(_data):
        raise RuntimeError("repair blew up")
    monkeypatch.setattr(pdf_safety, "repair", broken)
    with pytest.raises(Exception, match="cycle"):
        pipeline._render_copy(data, "copy.pdf")


def test_a_password_locked_file_is_returned_as_it_came():
    document = written(1)
    data = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    assert pdf_safety.repair(data) == data


def test_a_huge_page_is_drawn_smaller_and_an_ordinary_page_is_not():
    document = fitz.open()
    document.new_page(width=6048, height=8064)
    document.new_page(width=595, height=842)
    huge, ordinary = document[0], document[1]
    assert pdf_safety.capped_dpi(ordinary, 150) == 150
    dpi = pdf_safety.capped_dpi(huge, 150)
    assert 36 <= dpi < 150
    assert huge.rect.width * huge.rect.height * (dpi / 72) ** 2 <= pdf_safety.MAX_PAGE_PIXELS


def test_a_huge_page_stays_under_the_image_librarys_limit():
    document = fitz.open()
    document.new_page(width=6048, height=8064).insert_text((72, 72), "Answer")
    png = pdf_or_image_to_pngs(document.tobytes(), "copy.pdf")[0]
    from PIL import Image
    import io
    width, height = Image.open(io.BytesIO(png)).size   # raises above PIL's limit
    assert width * height <= pdf_safety.MAX_PAGE_PIXELS * 1.01


@pytest.mark.parametrize("teacher_layout", [True, False])
def test_a_checked_copy_is_built_for_a_paper_with_blank_pages(teacher_layout, monkeypatch):
    # The failing step itself: after the paid check, building the copy the student gets.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    import pdf_renderer
    from mathpix import PageOCR
    from test_evidence_quality import fixture
    monkeypatch.setattr(pdf_renderer, "TEACHER_LAYOUT", teacher_layout)
    source, report, ocr, _package = fixture()
    with fitz.open(stream=source, filetype="pdf") as document:
        document.new_page(width=595, height=842)
        source = document.tobytes()
    built = pdf_renderer.build_evaluated_pdf(source, "copy.pdf", [], report, ocr + [PageOCR(2, 595, 842, [], blank=True)])
    with fitz.open(stream=built, filetype="pdf") as document:
        assert document.page_count == 2
        assert "answer = 4" in document[0].get_text()


def test_images_are_passed_through():
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pixmap.clear_with(200)
    data = pixmap.tobytes("png")
    returned, pngs = pipeline._render_copy(data, "photo.png")
    assert returned is data and len(pngs) == 1
