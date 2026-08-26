"""HTTP transport with retry, behind a protocol so ingestion is testable offline.

The retry policy distinguishes *retryable* from *fatal*, which matters more than
it sounds: retrying a 404 wastes a minute per missing resource, and with 40-60
datasets that is most of an afternoon. A 404 from CKAN's datastore is not a
failure to be retried, it is a signal to take the fallback path.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "HttpError",
    "HttpResponse",
    "Transport",
    "RetryPolicy",
    "RetryingTransport",
    "RequestsTransport",
]

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, *, retryable: bool | None = None):
        super().__init__(message)
        self.status = status
        # A transport-level failure (DNS, reset, timeout) has no status and is
        # assumed transient; an HTTP status decides for itself.
        self.retryable = (
            retryable
            if retryable is not None
            else (status is None or status in RETRYABLE_STATUS)
        )


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        import json

        return json.loads(self.body.decode("utf-8"))


class Transport(Protocol):
    def get(self, url: str, params: dict[str, Any] | None = None) -> HttpResponse: ...


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter: float = 0.25

    def delay_for(self, attempt: int, rng: random.Random) -> float:
        """Exponential backoff with jitter, capped.

        Jitter matters when a run fans out over dozens of resources: without it,
        every worker that hit the same 429 retries at the same instant.
        """
        raw = min(self.base_delay_s * (2**attempt), self.max_delay_s)
        return raw * (1 + rng.uniform(-self.jitter, self.jitter))


class RetryingTransport:
    """Wraps a transport with bounded retries.

    ``sleep`` and ``rng`` are injected so tests can assert on backoff behaviour
    without actually waiting.
    """

    def __init__(
        self,
        inner: Transport,
        policy: RetryPolicy | None = None,
        *,
        sleep=time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.inner = inner
        self.policy = policy or RetryPolicy()
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.attempts_made = 0

    def get(self, url: str, params: dict[str, Any] | None = None) -> HttpResponse:
        last: HttpError | None = None

        for attempt in range(self.policy.max_attempts):
            self.attempts_made += 1
            try:
                return self.inner.get(url, params)
            except HttpError as err:
                last = err
                if not err.retryable:
                    raise
                if attempt == self.policy.max_attempts - 1:
                    break
                self._sleep(self.policy.delay_for(attempt, self._rng))

        assert last is not None
        raise HttpError(
            f"gave up after {self.policy.max_attempts} attempts: {last}",
            status=last.status,
            retryable=False,
        ) from last


class RequestsTransport:
    """Real transport. Imported lazily so the test suite needs no HTTP library."""

    def __init__(self, timeout_s: float = 30.0, user_agent: str | None = None) -> None:
        self.timeout_s = timeout_s
        self.user_agent = user_agent or (
            "boston-eval-harness/0.1 (research; contact via repository)"
        )

    def get(self, url: str, params: dict[str, Any] | None = None) -> HttpResponse:
        import requests

        try:
            response = requests.get(
                url,
                params=params,
                timeout=self.timeout_s,
                headers={"User-Agent": self.user_agent},
            )
        except Exception as err:  # connection reset, DNS, timeout
            raise HttpError(f"transport failure for {url}: {err}", status=None) from err

        if response.status_code >= 400:
            raise HttpError(
                f"HTTP {response.status_code} for {url}", status=response.status_code
            )

        return HttpResponse(
            status=response.status_code,
            body=response.content,
            headers=dict(response.headers),
        )
