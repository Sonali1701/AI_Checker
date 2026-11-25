# -*- coding: utf-8 -*-
"""Operator's request of 23 Nov 2026: an iPhone photo is an answer sheet like any other.

iOS uploads HEIC from the camera roll unless the student has changed the camera setting, and
the download refused every one of them as "not a PDF or a photo". They are now converted to a
one-page PDF, as JPEG and PNG photos already were. No network.
"""
from __future__ import annotations

import builtins
import io
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import unreadable_notice as notice

HEIC_TYPE = "image/heic"


def heic(width=1200, height=1600, colour=(238, 238, 248)):
    """A HEIC photo, written the way an iPhone's camera roll would hold one."""
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="HEIF", quality=60)
    return buffer.getvalue()


def pages_of(data):
    with fitz.open(stream=data, filetype="pdf") as document:
        return document.page_count, document[0].rect


# ------------------------------------------------------------------ reading one

def test_an_iphone_photo_becomes_a_one_page_pdf():
    data = heic()
    pdf = workflow.readable_copy(data, HEIC_TYPE)
    assert pdf[:4] == b"%PDF"
    count, rect = pages_of(pdf)
    assert count == 1 and rect.height > rect.width        # the photo's own shape is kept


def test_the_photo_is_recognised_without_a_content_type():
    """The portal does not always say what it is serving, so the file's own brand decides."""
    data = heic()
    assert data[4:8] == b"ftyp" and bytes(data[8:12]) == b"heic"
    assert workflow.is_heif_photo(data[:1024]) is True
    assert workflow.readable_copy(data)[:4] == b"%PDF"


@pytest.mark.parametrize("content_type", ["image/heic", "image/heif", "image/avif",
                                          "image/heic-sequence", "IMAGE/HEIC", "image/heic; charset=binary"])
def test_the_content_type_alone_is_enough(content_type):
    assert workflow.is_heif_photo(b"any old bytes here", content_type) is True


@pytest.mark.parametrize("brand", [b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx",
                                   b"mif1", b"msf1", b"avif", b"avis"])
def test_every_brand_of_the_family_is_a_photo(brand):
    assert workflow.is_heif_photo(b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 8) is True


def test_a_video_is_not_taken_for_a_photo():
    """ftypisom and ftypmp42 are video. A video is not an answer sheet and must still be refused,
    with the words that send the student a notice asking for a PDF."""
    for brand in (b"isom", b"mp42", b"qt  "):
        assert workflow.is_heif_photo(b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 8) is False
    video = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
    with pytest.raises(workflow.UnreadableCopy, match="not a PDF or a photo"):
        workflow.readable_copy(video, "video/mp4")
    assert notice.reason_code("the portal served video/mp4 of 80 bytes, not a PDF or a photo") == notice.WRONG_TYPE


def test_a_pdf_and_a_jpeg_still_go_through_untouched():
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "Answer sheet")
        pdf = document.tobytes()
    assert workflow.readable_copy(pdf, "application/pdf") == pdf
    photo = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 60))
    jpeg = photo.tobytes("jpg")
    assert workflow.readable_copy(jpeg, "image/jpeg")[:4] == b"%PDF"


# ------------------------------------------------------------------ when it cannot be read

def test_a_broken_photo_is_refused_in_words_that_ask_for_another_upload():
    broken = heic()[:120] + b"rubbish from here on"
    with pytest.raises(workflow.UnreadableCopy, match="HEIC photo could not be read"):
        workflow.readable_copy(broken, HEIC_TYPE)
    assert notice.reason_code("Downloaded file is not a readable PDF: the HEIC photo could not be read (x)") == notice.PHOTO


def test_a_build_without_the_heic_library_says_so_and_still_notices_the_student(monkeypatch):
    """The notice must not call an iPhone photo "not a PDF or a photo" if the library is missing
    from a build: the student would be told to do something they have already done."""
    photo = heic()                       # made before the library is hidden
    real_import = builtins.__import__

    def without_pillow_heif(name, *args, **kwargs):
        if name == "pillow_heif":
            raise ImportError("no pillow_heif in this build")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pillow_heif)
    with pytest.raises(workflow.UnreadableCopy, match="no HEIC support built in") as refused:
        workflow.readable_copy(photo, HEIC_TYPE)
    assert notice.reason_code(str(refused.value)) == notice.PHOTO
    page = notice.notice_pdf({"student_name": "Riya"}, {}, str(refused.value))
    with fitz.open(stream=page, filetype="pdf") as document:
        assert "photo uploaded for this answer sheet could not be opened" in " ".join(document[0].get_text().split())


# ------------------------------------------------------------------ what ships

def test_the_library_is_required_and_bundled():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    requirements = open(os.path.join(root, "requirements.txt"), encoding="utf-8").read()
    spec = open(os.path.join(root, "AICheckerClient.spec"), encoding="utf-8").read()
    assert "pillow-heif" in requirements
    assert "'pillow_heif'" in spec and "'unreadable_notice'" in spec
