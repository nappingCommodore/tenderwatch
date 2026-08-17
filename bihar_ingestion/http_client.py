"""HTTP client for the Bihar eProcurement portal.

Wraps :mod:`requests` with polite rate limiting, retry/backoff on transient
failures, JSON decoding, and an optional per-call logging hook so the caller can
persist an audit trail.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from urllib.parse import urlencode, urlparse

import requests

from .settings import Settings


@dataclass
class CallRecord:
    endpoint: str
    method: str
    url: str
    params: dict[str, Any] | None
    http_status: int | None
    response_bytes: int | None
    duration_ms: int | None
    ok: bool
    error: str | None


CallLogger = Callable[[CallRecord], None]


class PortalError(RuntimeError):
    """Raised when the portal returns a non-retryable error."""


class PortalClient:
    """Client for the portal's public REST endpoints."""

    def __init__(self, settings: Settings, call_logger: CallLogger | None = None) -> None:
        self.settings = settings
        self.base_url = settings.context_path
        self._logger = call_logger
        self._last_request_ts = 0.0
        self._auth_time = 0.0
        self.session = requests.Session()
        self.session.verify = settings.http.ca_bundle or settings.http.verify_tls
        # Header set mirrors the portal's AngularJS frontend. Notably the REST
        # endpoints require the custom `Auth-Token` header and Origin/Referer,
        # and must NOT carry a Content-Type on the empty-body POSTs (doing so
        # makes the server return HTTP 500).
        origin = self._origin(self.base_url)
        self.session.headers.update(
            {
                "User-Agent": settings.http.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Auth-Token": "X-Requested-With",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": origin,
                "Referer": f"{self.base_url}/openarea/tenderListingPage.action",
            }
        )

    @staticmethod
    def _origin(base_url: str) -> str:
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    # -- public API --------------------------------------------------------
    def bootstrap(self) -> bool:
        """Establish an authenticated session.

        The portal's REST endpoints require (1) a ``JSESSIONID`` cookie issued by
        the HTML entry page and (2) an ``Authorization`` bearer token obtained
        from ``/rest/login/provideTokenObject``. Without the token the endpoints
        return HTTP 500. ``requests.Session`` keeps the cookie automatically; we
        set the bearer token as a default header.
        """

        try:
            self.session.get(
                f"{self.base_url}/openarea/tenderListingPage.action",
                timeout=self.settings.http.timeout_seconds,
                headers={"Accept": "text/html,application/xhtml+xml,*/*"},
            )
        except (requests.ConnectionError, requests.Timeout):
            return False
        return self._refresh_token()

    def _refresh_token(self) -> bool:
        """Fetch a fresh bearer token and install it on the session."""

        try:
            resp = self.session.post(
                f"{self.base_url}/rest/login/provideTokenObject",
                timeout=self.settings.http.timeout_seconds,
            )
        except (requests.ConnectionError, requests.Timeout):
            return False
        if resp.status_code != 200:
            return False
        try:
            jwt = resp.json().get("jwt")
        except ValueError:
            return False
        if not jwt:
            return False
        self.session.headers["Authorization"] = jwt
        self._auth_time = time.monotonic()
        return True

    def get_json(self, endpoint: str, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request(endpoint, "GET", path, params)

    def post_json(
        self,
        endpoint: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        return self._request(endpoint, "POST", path, params, json_body=json_body)

    def paginate(
        self,
        endpoint: str,
        path: str,
        method: str = "POST",
        page_size: int = 100,
        max_pages: int = 0,
        start: int = 0,
        extra_params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Iterator[tuple[int, list[Any]]]:
        """Yield ``(startpoint, rows)`` pages until the portal returns fewer
        rows than requested (or ``max_pages`` is reached).

        The portal paginates via ``startpoint`` + ``maxRow``. We advance
        ``startpoint`` by ``page_size`` each call. Verified against the live
        portal: ``startpoint`` is honoured (pages march back in time) and the
        server returns an empty list once the archive is exhausted. We therefore
        stop on an *empty* page rather than a merely-short one, so a short page
        mid-stream can never truncate the crawl.
        """

        startpoint = start
        pages = 0
        while True:
            params = dict(extra_params or {})
            params["startpoint"] = startpoint
            params["maxRow"] = page_size
            rows = self._request(endpoint, method, path, params, json_body=json_body)
            if not isinstance(rows, list):
                rows = [] if rows is None else [rows]
            if not rows:
                break
            yield startpoint, rows
            pages += 1
            if max_pages and pages >= max_pages:
                break
            startpoint += page_size

    # -- internals ---------------------------------------------------------
    def _throttle(self) -> None:
        delay = self.settings.http.request_delay_seconds
        if delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def _maybe_refresh_token(self) -> None:
        """Proactively refresh the bearer token before it expires server-side."""
        if "Authorization" not in self.session.headers:
            return
        age = time.monotonic() - self._auth_time
        if age >= self.settings.http.token_refresh_seconds:
            self._refresh_token()

    def _request(
        self, endpoint: str, method: str, path: str, params: dict[str, Any] | None,
        json_body: Any = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        query = urlencode(params) if params else ""
        full_url = f"{url}?{query}" if query else url
        attempt = 0
        last_error: str | None = None
        reauthed = False

        while attempt <= self.settings.http.max_retries:
            self._throttle()
            self._maybe_refresh_token()
            started = time.monotonic()
            self._last_request_ts = started
            try:
                # The portal expects query-string params even on POST calls, and
                # a JSON body only on the listing/filter endpoints.
                resp = self.session.request(
                    method,
                    url,
                    params=params or None,
                    json=json_body,
                    timeout=self.settings.http.timeout_seconds,
                )
                duration_ms = int((time.monotonic() - started) * 1000)
                if resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}"
                    self._emit(endpoint, method, full_url, params, resp.status_code,
                               len(resp.content), duration_ms, False, last_error)
                    # A 500 is often a stale bearer token (the portal returns 500
                    # rather than 401 for an expired JWT). Re-auth once and retry
                    # immediately before falling back to backoff retries.
                    if not reauthed:
                        reauthed = True
                        if self._refresh_token():
                            continue
                    self._backoff(attempt)
                    attempt += 1
                    continue
                if resp.status_code >= 400:
                    self._emit(endpoint, method, full_url, params, resp.status_code,
                               len(resp.content), duration_ms, False, f"HTTP {resp.status_code}")
                    if resp.status_code in (401, 403) and not reauthed:
                        # Token likely expired; re-authenticate once and retry.
                        reauthed = True
                        if self._refresh_token():
                            continue
                    raise PortalError(f"{endpoint}: HTTP {resp.status_code} for {full_url}")

                data = self._decode(resp)
                self._emit(endpoint, method, full_url, params, resp.status_code,
                           len(resp.content), duration_ms, True, None)
                return data
            except requests.exceptions.SSLError as exc:
                # Certificate problems are not transient; fail fast with guidance.
                duration_ms = int((time.monotonic() - started) * 1000)
                msg = f"SSLError: {exc}"
                self._emit(endpoint, method, full_url, params, None, None,
                           duration_ms, False, msg)
                raise PortalError(
                    f"{endpoint}: TLS verification failed for {full_url}. "
                    "The portal's certificate chain could not be verified. Set "
                    "http.ca_bundle to a PEM file, or http.verify_tls: false in "
                    f"your config to bypass verification. ({exc})"
                ) from exc
            except (requests.ConnectionError, requests.Timeout) as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
                self._emit(endpoint, method, full_url, params, None, None,
                           duration_ms, False, last_error)
                self._backoff(attempt)
                attempt += 1

        raise PortalError(
            f"{endpoint}: exhausted {self.settings.http.max_retries} retries "
            f"for {full_url} (last error: {last_error})"
        )

    def _decode(self, resp: requests.Response) -> Any:
        text = resp.text.strip()
        if not text:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise PortalError(f"Invalid JSON response ({exc}) from {resp.url}") from exc

    def _backoff(self, attempt: int) -> None:
        base = self.settings.http.backoff_base_seconds
        cap = self.settings.http.backoff_max_seconds
        time.sleep(min(cap, base * (2 ** attempt)))

    def _emit(self, endpoint, method, url, params, status, size, duration, ok, error) -> None:
        if self._logger is None:
            return
        self._logger(
            CallRecord(endpoint, method, url, params, status, size, duration, ok, error)
        )
