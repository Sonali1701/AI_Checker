# -*- coding: utf-8 -*-
"""Calls to the PW proxy reuse a pool of open connections (pw_access._session).

MEASURED 2026-10-06 (build 83, three folder runs at once, 75 copies at a time): 110
TLS handshakes cut short (SSLEOFError), 104 connection resets, failed DNS look-ups,
and not one 429 or 529. requests.post opened a new connection, with its own DNS
look-up and TLS handshake, for every call. These tests run against a local server.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pw_access  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, as the proxy offers
    clients: list = []

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).clients.append(self.client_address[1])
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "tracker=one; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Pool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/api/x"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.clients.clear()

    def test_one_connection_carries_many_calls(self):
        for _ in range(5):
            self.assertEqual(pw_access._http_post(self.url, json={"a": 1}, timeout=10).json(),
                             {"ok": True})
        self.assertEqual(len(_Handler.clients), 5)
        self.assertEqual(len(set(_Handler.clients)), 1, "the same connection was reused")

    def test_calls_from_many_threads_share_a_few_connections(self):
        def call(_):
            return pw_access._http_post(self.url, json={}, timeout=10).status_code
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as pool:
            self.assertEqual(set(pool.map(call, range(80))), {200})
        self.assertLessEqual(len(set(_Handler.clients)), 8, "80 calls, at most 8 connections")

    def test_no_cookie_is_kept_between_calls(self):
        pw_access._http_post(self.url, json={}, timeout=10)
        self.assertEqual(len(pw_access._session().cookies), 0)

    def test_one_session_per_process(self):
        self.assertIs(pw_access._session(), pw_access._session())

    def test_only_connections_that_never_opened_are_retried_here(self):
        adapter = pw_access._session().get_adapter("https://pw-apps-proxy.vercel.app")
        retry = adapter.max_retries
        self.assertEqual(retry.connect, pw_access._CONNECT_RETRIES)
        self.assertEqual((retry.read, retry.status, retry.other), (0, 0, 0),
                         "a call that may have reached the model is never replayed here")
        self.assertGreaterEqual(adapter._pool_maxsize, 64)

    def test_proxy_calls_go_through_the_pool(self):
        with patch.object(pw_access, "_http_post") as post, \
                patch.object(pw_access, "_auth_token", return_value="pass"):
            pw_access._post_once("/api/claude/generate", "token", {"x": 1}, 10)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(post.call_args.args[0].endswith("/api/claude/generate"))


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body or {}, json.dumps(body or {})

    def json(self):
        return self._body


class BlobOutage(unittest.TestCase):
    """MEASURED 2026-10-06: "claude upload error 503: Blob service is currently
    unavailable" failed a Business Laws copy. Nothing runs or is billed until the
    request that names the blob is sent, so a 5xx from blob storage is retried."""

    def _upload(self, puts):
        big = {"x": "a" * (pw_access._LARGE_REQUEST_BYTES + 10)}
        with patch.object(pw_access, "_post",
                          return_value=_Resp(200, {"upload_url": "https://blob/put"})), \
                patch.object(pw_access, "_http_put", side_effect=puts) as put, \
                patch.object(pw_access.time, "sleep"):
            return pw_access._upload_large_json("token", "app", big, "claude"), put

    def test_a_blob_outage_is_retried(self):
        url, put = self._upload([_Resp(503), _Resp(503), _Resp(200, {"url": "https://blob/x"})])
        self.assertEqual(url, "https://blob/x")
        self.assertEqual(put.call_count, 3)

    def test_a_lasting_outage_still_fails_with_the_reason(self):
        with self.assertRaises(pw_access.PWAccessError) as caught:
            self._upload([_Resp(503)] * pw_access._RETRY_ATTEMPTS)
        self.assertIn("503", str(caught.exception))

    def test_a_refusal_is_not_retried(self):
        with self.assertRaises(pw_access.PWAccessError):
            self._upload([_Resp(403), _Resp(200, {"url": "x"})])


if __name__ == "__main__":
    unittest.main()
