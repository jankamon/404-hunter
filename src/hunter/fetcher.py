"""HTTP fetching with per-host rate limiting, retries, and redirect capture."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .normalize import host_of


@dataclass
class FetchResult:
    url: str  # original URL requested (normalized)
    final_url: str  # after redirects
    status_code: int | None
    content_type: str
    body: bytes  # empty for HEAD or non-HTML or error
    redirect_chain: list[str] = field(default_factory=list)
    error: str = ""  # populated on transport errors

    @property
    def ok_html(self) -> bool:
        return (
            self.status_code is not None
            and 200 <= self.status_code < 300
            and "html" in self.content_type.lower()
        )

    @property
    def is_broken(self) -> bool:
        if self.error:
            return True
        if self.status_code is None:
            return True
        return self.status_code >= 400


class HostRateLimiter:
    """Per-host token bucket: enforce a minimum delay between requests to the same host."""

    def __init__(self, min_delay: float):
        self.min_delay = max(0.0, min_delay)
        self._next_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global = asyncio.Lock()

    async def wait(self, host: str) -> None:
        if self.min_delay <= 0:
            return
        async with self._global:
            lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            ready = self._next_at.get(host, 0.0)
            if now < ready:
                await asyncio.sleep(ready - now)
            self._next_at[host] = max(time.monotonic(), ready) + self.min_delay


class Fetcher:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        rate_limiter: HostRateLimiter,
        max_body_bytes: int = 5_000_000,
        max_attempts: int = 3,
    ):
        self.client = client
        self.rate_limiter = rate_limiter
        self.max_body_bytes = max_body_bytes
        self.max_attempts = max_attempts

    async def get(self, url: str) -> FetchResult:
        return await self._request("GET", url, want_body=True)

    async def head(self, url: str) -> FetchResult:
        """HEAD with automatic GET fallback if the server rejects it."""
        result = await self._request("HEAD", url, want_body=False)
        if result.status_code in (405, 501):
            return await self._request("GET", url, want_body=False)
        return result

    async def _request(self, method: str, url: str, *, want_body: bool) -> FetchResult:
        await self.rate_limiter.wait(host_of(url))

        retry = AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential_jitter(initial=1, max=10),
            retry=retry_if_exception_type(_RetryableError),
            reraise=True,
        )

        try:
            async for attempt in retry:
                with attempt:
                    return await self._do(method, url, want_body=want_body)
        except RetryError as exc:
            inner = exc.last_attempt.exception() if exc.last_attempt else exc
            return _error_result(url, str(inner) or type(inner).__name__)
        except _RetryableError as exc:
            return _error_result(url, str(exc.original) or type(exc.original).__name__)
        except httpx.HTTPError as exc:
            return _error_result(url, f"{type(exc).__name__}: {exc}")
        # Should be unreachable, but keep mypy/type-checkers happy.
        return _error_result(url, "unknown")

    async def _do(self, method: str, url: str, *, want_body: bool) -> FetchResult:
        chain: list[str] = []
        try:
            req = self.client.build_request(method, url)
            resp = await self.client.send(req, follow_redirects=False)
            for _ in range(5):
                if resp.is_redirect:
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    next_url = str(resp.url.join(loc))
                    chain.append(next_url)
                    await resp.aclose()
                    req = self.client.build_request(method, next_url)
                    resp = await self.client.send(req, follow_redirects=False)
                else:
                    break

            content_type = resp.headers.get("content-type", "")
            body = b""
            if want_body and "html" in content_type.lower():
                # Stream up to max_body_bytes to avoid OOM on giant pages.
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= self.max_body_bytes:
                        break
                body = bytes(buf[: self.max_body_bytes])
            else:
                await resp.aread()

            status = resp.status_code
            await resp.aclose()

            if status in (429, 503):
                raise _RetryableError(httpx.HTTPStatusError(f"{status}", request=req, response=resp))

            return FetchResult(
                url=url,
                final_url=str(resp.url),
                status_code=status,
                content_type=content_type,
                body=body,
                redirect_chain=chain,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise _RetryableError(exc) from exc


class _RetryableError(Exception):
    def __init__(self, original: BaseException):
        super().__init__(str(original))
        self.original = original


def _error_result(url: str, msg: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=None,
        content_type="",
        body=b"",
        error=msg,
    )


def classify(result: FetchResult) -> tuple[str, str]:
    """Map a FetchResult to (status_label, error_detail) for the report."""
    if result.error:
        msg = result.error.lower()
        if "timeout" in msg:
            return "timeout", result.error
        if "ssl" in msg or "certificate" in msg:
            return "ssl-error", result.error
        if "name" in msg or "dns" in msg or "resolution" in msg:
            return "dns-error", result.error
        if "connect" in msg:
            return "connect-error", result.error
        return "error", result.error
    if result.status_code is None:
        return "error", "no-response"
    if result.status_code >= 400:
        return str(result.status_code), ""
    return "ok", ""


def make_client(
    *,
    timeout: float,
    user_agent: str,
    auth: tuple[str, str] | None,
    extra_headers: dict[str, str] | None,
    verify: bool,
    concurrency: int,
) -> httpx.AsyncClient:
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    if extra_headers:
        headers.update(extra_headers)
    limits = httpx.Limits(
        max_connections=concurrency * 2,
        max_keepalive_connections=concurrency,
    )
    return httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(connect=10.0, read=timeout, write=timeout, pool=timeout),
        limits=limits,
        auth=auth,
        verify=verify,
        follow_redirects=False,
    )
