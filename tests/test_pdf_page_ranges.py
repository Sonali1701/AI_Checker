"""Page selection is an upload operation, not a change to grading."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
import pytest

from pdf_page_ranges import PdfPageRangeError, parse_page_range, select_pdf_pages


def paper():
    with fitz.open() as doc:
        for index in range(1, 6):
            doc.new_page().insert_text((30, 30), f"Source page {index}")
        return doc.tobytes()


def page_text(data):
    with fitz.open(stream=data, filetype="pdf") as doc:
        return [page.get_text().strip() for page in doc]


def test_empty_range_preserves_original_bytes_and_non_pdf_input():
    data = paper()
    assert select_pdf_pages(data, "paper.pdf", "") is data
    image = b"\x89PNG\r\n\x1a\nimage"
    assert select_pdf_pages(image, "paper.png", "  ") is image


def test_pdf_pages_are_one_based_inclusive_and_keep_the_requested_order():
    assert parse_page_range(" 1 - 3 , 5 ", 5) == [0, 1, 2, 4]
    selected = select_pdf_pages(paper(), "paper.pdf", "5,1-3")
    assert page_text(selected) == ["Source page 5", "Source page 1", "Source page 2", "Source page 3"]


def test_repeated_extraction_is_byte_identical_for_existing_paper_proofs():
    original = paper()
    first = select_pdf_pages(original, "paper.pdf", "1-3,5")
    assert first == select_pdf_pages(original, "paper.pdf", "1-3,5")
    assert first == select_pdf_pages(original, "paper.pdf", " 1 - 3 , 5 ")
    assert page_text(original) == [f"Source page {index}" for index in range(1, 6)]


@pytest.mark.parametrize("value", ["0", "-1", "2-1", "1-6", "6", "1,1", "1-3,3-4",
                                          "1,", ",1", "1-", "one", "1.5", "1;2", "1--2"])
def test_malformed_out_of_bounds_or_repeated_pages_are_rejected(value):
    with pytest.raises(PdfPageRangeError):
        select_pdf_pages(paper(), "paper.pdf", value, "Question paper")


def test_a_range_rejects_images_and_unreadable_pdfs():
    with pytest.raises(PdfPageRangeError, match="PDF files only"):
        select_pdf_pages(b"image", "image.png", "1")
    with pytest.raises(PdfPageRangeError, match="readable PDF"):
        select_pdf_pages(b"%PDF-incomplete", "paper.pdf", "1")


def test_encrypted_pdf_requires_a_readable_source_before_selection():
    with fitz.open(stream=paper(), filetype="pdf") as doc:
        encrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                               owner_pw="owner", user_pw="reader")
    with pytest.raises(PdfPageRangeError, match="readable PDF"):
        select_pdf_pages(encrypted, "paper.pdf", "1")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
