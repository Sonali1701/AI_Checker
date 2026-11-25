"""Account-wide task-list backoff and credential-free browser diagnostics."""
import random
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


def retry_seconds(value):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(str(value)).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, min(900, seconds))


class FetchRecovery:
    """Used on the account's serialized portal desk; queued copies share one deadline."""
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.failures = 0
        self.retry_at = 0
        self.reload_pending = False
        self.message = ""
        self.shot_at = -1e9

    def remaining(self):
        return max(0, self.retry_at - self.clock())

    def failed(self, delays, detail="", retry_after=0):
        self.failures += 1
        delay = max(delays[min(self.failures, len(delays)) - 1], retry_after)
        # Different accounts should not all probe the service again at the same instant.
        delay += random.uniform(0, min(10, delay * .2))
        self.retry_at = self.clock() + delay
        self.reload_pending = True
        self.message = "The portal says 'Unable to fetch task': " + (detail or "its task list did not load; the server cause is unknown")

    def succeeded(self):
        self.failures = 0
        self.retry_at = 0
        self.reload_pending = False
        self.message = ""


class ReadFailures:
    """Observe only fresh read requests to the portal backend. Never read tokens/bodies."""
    def __init__(self, backend_url):
        self.host = urlsplit(backend_url).netloc
        self.requests = set()
        self.detail = ""
        self.retry_after = 0

    def started(self, request):
        if (request.method == "GET" and request.resource_type in ("fetch", "xhr")
                and self.host and urlsplit(request.url).netloc == self.host and len(self.requests) < 128):
            self.requests.add(request)

    def response(self, response):
        if response.request not in self.requests:
            return
        self.requests.discard(response.request)
        code = response.status
        if code < 400:
            return
        if code == 429:
            self.detail = "HTTP 429: the portal is limiting requests"
        elif code in (401, 403):
            self.detail = f"HTTP {code}: the portal denied this read; its login or account permissions need checking"
        elif code >= 500:
            self.detail = f"HTTP {code}: the portal server failed to load the list"
        else:
            self.detail = f"HTTP {code}: the portal rejected the list request"
        self.retry_after = retry_seconds(response.headers.get("retry-after", ""))

    def failed(self, request):
        if request not in self.requests:
            return
        self.requests.discard(request)
        # Aborts caused by navigation are not evidence of an outage.
        if "ERR_ABORTED" not in str(request.failure or ""):
            self.detail = "network failure while loading the portal list"

    @contextmanager
    def watch(self, page):
        listeners = []
        try:
            for event, handler in (("request", self.started), ("response", self.response), ("requestfailed", self.failed)):
                try:
                    page.on(event, handler)
                    listeners.append((event, handler))
                except AttributeError:  # injected test/browser adapters may not expose events
                    break
            yield self
        finally:
            for event, handler in listeners:
                try:
                    page.remove_listener(event, handler)
                except Exception:  # a dead page must not replace the original portal error
                    pass
            self.requests.clear()
