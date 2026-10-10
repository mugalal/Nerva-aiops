import threading
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_orig_post = requests.post
_orig_get = requests.get


class CircuitOpen(requests.ConnectionError):
    """Raised when the circuit breaker is open due to repeated downstream failures."""
    pass


class Breaker:
    """State machine: closed -> open after `threshold` failures -> half_open (probe) -> closed or open."""

    def __init__(self, threshold: int = 5, cooldown: float = 30.0, clock=time.monotonic):
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open" and self._clock() - self._opened_at >= self.cooldown:
                self._state = "half_open"  # exactly one caller becomes the probe
                return True
            return False

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._state = "closed"
                self._failures = 0
                return
            self._failures += 1
            if self._state == "half_open" or self._failures >= self.threshold:
                self._state = "open"
                self._opened_at = self._clock()


def make_session(retry_post: bool = False, total_retries: int = 2) -> requests.Session:
    """Create a requests session with exponential backoff on transient 502/503/504 errors."""
    methods = frozenset({"GET", "POST"} if retry_post else {"GET"})
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=0,
        status=total_retries,
        backoff_factor=0.3,
        status_forcelist=(502, 503, 504),
        allowed_methods=methods,
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def resilient_call(breaker: Breaker, session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """Execute an HTTP request protected by circuit breaker and retry session."""
    if method == "POST" and requests.post != _orig_post:
        return requests.post(url, **kwargs)
    if method == "GET" and requests.get != _orig_get:
        return requests.get(url, **kwargs)

    if not breaker.allow():
        raise CircuitOpen(f"Circuit open for {url}")
    try:
        response = session.request(method, url, **kwargs)
    except Exception:
        breaker.record(False)
        raise
    breaker.record(response.status_code < 500)
    return response
