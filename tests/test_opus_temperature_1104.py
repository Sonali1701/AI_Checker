"""opus-4-8 refuses a `temperature`; every Claude call drops it for that model.

CS ECPIL copy R122 graded on Sonnet (4 Nov 2026): the choice-order regrade escalated
to opus-4-8 carrying the grading request's temperature 0, the proxy answered 400
"`temperature` is deprecated for this model", and the whole copy failed.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader  # noqa: E402


class OpusTemperatureTest(unittest.TestCase):
    def _sent(self, model):
        seen = {}

        def fake(token, *, model, request, **kwargs):
            seen["request"] = request
            return {"content": []}

        with mock.patch.object(grader.pw_access, "claude_generate", side_effect=fake):
            grader._claude_call("token", task_id="t", model=model,
                                request={"max_tokens": 10, "temperature": 0, "messages": []},
                                filename="f", count=1)
        return seen["request"]

    def test_opus_request_has_no_temperature(self):
        self.assertNotIn("temperature", self._sent("claude-opus-4-8"))

    def test_other_models_keep_temperature(self):
        self.assertEqual(self._sent("claude-sonnet-4-6")["temperature"], 0)
        self.assertEqual(self._sent("claude-haiku-4-5")["temperature"], 0)


if __name__ == "__main__":
    unittest.main()
