"""29 Oct 2026: the orientation probe goes eight numbered pages at a time.

The operator log of that day threw away 98 probe replies: 45 long copies whose reply
named a page too many or too few, 28 one-page probes answered "[0, 0]", and seven
uploads over the proxy's 100-image limit.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader


def probe(n, answer):
    """Run the probe over n pages; answer(images in the call, call number) -> reply text."""
    calls = []

    def call(token, *, request, **kwargs):
        content = request["messages"][0]["content"]
        images = sum(1 for block in content if block.get("type") == "image")
        calls.append((images, [b.get("text", "") for b in content if b.get("type") == "text"]))
        text = answer(images, len(calls))
        if isinstance(text, Exception):
            raise text
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 10, "output_tokens": 5}}

    with patch.object(grader, "_claude_call", side_effect=call), \
         patch.object(grader, "_downscale_for_model", side_effect=lambda png, **kw: png), \
         patch.object(grader, "_image_block",
                      side_effect=lambda data, *a: {"type": "image", "source": {"data": "x"}}):
        angles = grader.orientation_probe([b"page"] * n, google_token="t", task_id="k")
    return angles, calls


def test_a_long_copy_is_probed_eight_pages_at_a_time_in_order():
    angles, calls = probe(20, lambda images, number: str([90 * (number % 4)] * images))
    assert [images for images, _ in calls] == [8, 8, 4]
    assert angles == [90] * 8 + [180] * 8 + [270] * 4


def test_a_batch_that_miscounts_leaves_only_its_own_pages_unturned():
    def answer(images, number):
        return "[90, 90, 90]" if number == 2 else str([270] * images)
    angles, _ = probe(18, answer)
    assert angles == [270] * 8 + [0] * 8 + [270] * 2


def test_a_batch_that_fails_does_not_stop_the_others():
    angles, calls = probe(10, lambda images, number:
                          RuntimeError("proxy error") if number == 1 else str([90] * images))
    assert len(calls) == 2 and angles == [0] * 8 + [90] * 2


def test_no_call_carries_more_than_the_proxy_image_limit():
    _, calls = probe(372, lambda images, number: str([0] * images))
    assert max(images for images, _ in calls) == grader.ORIENTATION_BATCH < 100
    assert len(calls) == 47


def test_each_image_is_numbered_and_the_count_is_stated():
    _, calls = probe(3, lambda images, number: "[0, 90, 0]")
    texts = calls[0][1]
    assert "exactly 3 integers" in texts[0]
    assert texts[1:] == ["Image 1:", "Image 2:", "Image 3:"]


def test_one_page_answered_twice_the_same_way_is_read():
    assert probe(1, lambda images, number: "[0, 0]")[0] == [0]
    assert probe(1, lambda images, number: "[90, 90]")[0] == [90]
    assert grader.parse_orientation_reply("[90, 90]", 1) == [90]


def test_one_page_answered_two_different_ways_is_still_ignored():
    assert probe(1, lambda images, number: "[0, 90]")[0] == [0]
    assert grader.parse_orientation_reply("[0, 90]", 1) == [0, 90]


def test_a_single_page_probe_asks_for_one_integer_without_labels():
    _, calls = probe(1, lambda images, number: "[270]")
    assert calls[0][1] == [calls[0][1][0]] and "ONE image" in calls[0][1][0]
