"""
Single Massive API client used by every example script.

Fixes from the 2026-06-26 audit:

- H1, H2, D3, M8 (this file is part of the structural unlock)
- L3: no retry on transient network error. earnings-drilldown's first
  AAPL run died on socket.timeout. Now wrapped with bounded retry.
- D3: prose said `api.massive.com`, examples hardcoded `api.polygon.io`,
  next_url used `&apiKey=` while primary calls used `Authorization`.
  Both hosts answer 200 but the citation drift was real. This client
  uses one host and one auth scheme everywhere.
- M8: fetched_at was an import-time singleton in some scripts. Now
  returned per call.

Design choices:

- Host: `api.polygon.io`. The audit confirmed both hosts answer 200.
  polygon.io is what `next_url` values point at, so using it keeps
  pagination self-consistent without rewriting URLs.
- Auth: `Authorization: Bearer ${MASSIVE_API_KEY}` only. No
  query-string `apiKey=`. When following `next_url`, drop any embedded
  apiKey param to avoid leaking it in logs.
- Retry: 3 attempts on socket.timeout, 5xx, and 429. Exponential
  backoff with jitter. Raises `FetchError` after the last attempt.
- 429 (rate limit): respects `Retry-After` if present. Raises
  `RateLimited` if exhausted.
- Pagination: `paginate()` yields each page's `results` plus a
  `fetched_at` UTC ISO timestamp per page.
- fetched_at: every `get()` and every paginated page returns the wall-
  clock UTC timestamp of the response landing. Used for per-claim
  provenance in skill outputs.

Importing the client:

    from lib.quant_garage import MassiveClient
    client = MassiveClient()  # reads MASSIVE_API_KEY from env

    data, fetched_at = client.get("/v3/reference/tickers/AAPL")
    for page, fetched_at in client.paginate("/v3/reference/splits", {"ticker": "AAPL"}):
        ...
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator, Optional

from .as_of import utcnow_iso

DEFAULT_HOST = "https://api.polygon.io"
DEFAULT_USER_AGENT = "quant-garage/0.1 (+https://github.com/rgourley/quant-garage)"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE = 0.5  # seconds


class FetchError(Exception):
    """Raised when a request fails after exhausting all retries.

    Carries the final status code (if HTTP), the URL, and the underlying
    exception so callers can surface a useful message in skill output.
    """

    def __init__(self, message: str, url: str, status_code: Optional[int] = None, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.cause = cause


class RateLimited(FetchError):
    """429 Too Many Requests after exhausted retries."""


class MassiveClient:
    """Thin Massive (Polygon.io) HTTP client with retry + pagination.

    Reads `MASSIVE_API_KEY` from the environment. Raises at init if the
    key is missing so scripts fail fast rather than producing empty
    output that looks like a "no results" answer.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        host: str = DEFAULT_HOST,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ):
        # Lazy-validate: capture the key here but DO NOT raise on missing.
        # Scripts that instantiate at import time (e.g., for argparse --help)
        # should still be runnable. The actual RuntimeError fires the moment
        # an HTTP call is attempted, via _headers(). Fixes N7.
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY")
        self.host = host.rstrip("/")
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError(
                "MASSIVE_API_KEY not set. Export it before running, or pass api_key= explicitly."
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

    def _build_url(self, path_or_url: str, params: Optional[dict[str, Any]] = None) -> str:
        """Resolve to an absolute URL. Strips embedded apiKey from next_url values.

        If `path_or_url` is already absolute (starts with http), return it
        with any `apiKey` query param stripped so the bearer header is the
        single source of auth.
        """
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
            # Strip any apiKey param if it slipped in from a next_url
            parsed = urllib.parse.urlparse(url)
            query_pairs = [
                (k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                if k.lower() != "apikey"
            ]
            new_query = urllib.parse.urlencode(query_pairs)
            url = urllib.parse.urlunparse(parsed._replace(query=new_query))
        else:
            path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
            url = f"{self.host}{path}"

        if params:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(params, doseq=True)}"
        return url

    def get(self, path_or_url: str, params: Optional[dict[str, Any]] = None) -> tuple[dict, str]:
        """GET a single endpoint and return (parsed_body, fetched_at_iso_utc).

        Retries on socket.timeout, 5xx, and 429 with exponential backoff.
        Raises FetchError (or RateLimited subclass) after exhausting attempts.
        """
        url = self._build_url(path_or_url, params)
        last_exc: Optional[BaseException] = None
        last_status: Optional[int] = None

        for attempt in range(1, self.max_attempts + 1):
            req = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    fetched_at = utcnow_iso()
                    body = resp.read()
                    return json.loads(body), fetched_at
            except urllib.error.HTTPError as e:
                last_status = e.code
                if e.code in (429,) or 500 <= e.code < 600:
                    last_exc = e
                    self._sleep_backoff(attempt, retry_after=_parse_retry_after(e.headers))
                    continue
                # 4xx other than 429 are not retried; raise immediately with body for diagnostics
                body_excerpt = self._read_excerpt(e)
                raise FetchError(
                    f"HTTP {e.code} on {url}: {body_excerpt}",
                    url=url,
                    status_code=e.code,
                    cause=e,
                ) from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_exc = e
                self._sleep_backoff(attempt)
                continue

        # Exhausted attempts
        if last_status == 429:
            raise RateLimited(
                f"Rate limited after {self.max_attempts} attempts on {url}",
                url=url,
                status_code=429,
                cause=last_exc,
            )
        raise FetchError(
            f"Failed after {self.max_attempts} attempts on {url}: {last_exc!r}",
            url=url,
            status_code=last_status,
            cause=last_exc,
        )

    def paginate(self, path: str, params: Optional[dict[str, Any]] = None) -> Iterator[tuple[list, str]]:
        """Yield (results_list, fetched_at_iso_utc) for each page until next_url is empty.

        First call uses `path` + `params`. Subsequent calls follow the
        absolute URL in `next_url` from each response. apiKey is stripped
        from next_url; the Authorization header is the single auth source.
        """
        url_or_path = path
        current_params = params
        while True:
            body, fetched_at = self.get(url_or_path, current_params)
            results = body.get("results") or []
            yield results, fetched_at

            next_url = body.get("next_url")
            if not next_url:
                return
            # On the next iteration, follow next_url as an absolute URL and
            # do not re-apply the original params (they're already in the cursor).
            url_or_path = next_url
            current_params = None

    @staticmethod
    def _read_excerpt(http_error: urllib.error.HTTPError, limit: int = 300) -> str:
        try:
            body = http_error.read()
        except Exception:
            return "<no body>"
        text = body.decode("utf-8", errors="replace")
        if len(text) > limit:
            return text[: limit - 1] + "..."
        return text

    def _sleep_backoff(self, attempt: int, retry_after: Optional[float] = None) -> None:
        """Sleep with exponential backoff + jitter, or honor Retry-After."""
        if retry_after is not None:
            time.sleep(min(retry_after, 30))
            return
        # 0.5, 1.0, 2.0 ... with +/- 25% jitter
        base = DEFAULT_BACKOFF_BASE * (2 ** (attempt - 1))
        jitter = base * 0.25 * (2 * random.random() - 1)
        time.sleep(max(0.0, base + jitter))


def _parse_retry_after(headers) -> Optional[float]:
    """Parse the Retry-After header (seconds form only; HTTP-date ignored)."""
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
