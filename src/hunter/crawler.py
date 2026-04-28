"""The async crawl loop."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .fetcher import Fetcher, classify
from .normalize import (
    host_of,
    is_same_site,
    looks_non_html,
    matches_any,
    normalize,
    path_of,
)
from .parser import extract_links
from .soft404 import Soft404Detector
from .state import Finding, Store

log = logging.getLogger("hunter")

# External links that come back with these codes are commonly WAF/anti-bot rejections
# (Cloudflare, Akamai) rather than real 404s — surface them under a separate label
# so users can eyeball them instead of chasing false positives.
_AMBIGUOUS_EXTERNAL_STATUSES = frozenset({401, 403, 429})


@dataclass
class CrawlConfig:
    seed_url: str
    seed_host: str
    scope: str = "host"
    concurrency: int = 5
    max_pages: int = 10_000  # 0 = unlimited
    check_external: bool = True
    soft_404: bool = True
    include: list[str] | None = None
    exclude: list[str] | None = None
    verbose: bool = False


class Crawler:
    def __init__(
        self,
        *,
        config: CrawlConfig,
        fetcher: Fetcher,
        store: Store,
        soft404_detector: Soft404Detector,
    ):
        self.cfg = config
        self.fetcher = fetcher
        self.store = store
        self.soft404 = soft404_detector
        # In-memory dedup of URLs we've already enqueued or finished, to avoid
        # SQL round-trips on every link discovery.
        self._enqueued: set[str] = set()
        self._pages_fetched = 0
        self._stop = asyncio.Event()

    async def run(self, *, resume: bool) -> None:
        if not resume:
            self.store.push_frontier(self.cfg.seed_url, source_page="", link_text="")

        if self.cfg.soft_404:
            try:
                await self.soft404.calibrate(self.fetcher, self.cfg.seed_url)
            except Exception as exc:
                log.warning("soft-404 calibration failed: %s", exc)

        queue: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()

        async def feeder() -> None:
            while not self._stop.is_set():
                if queue.qsize() >= self.cfg.concurrency * 4:
                    await asyncio.sleep(0.05)
                    continue
                batch = self.store.pop_frontier_batch(self.cfg.concurrency * 4)
                if not batch:
                    if queue.empty() and all(w.done() for w in workers):
                        break
                    await asyncio.sleep(0.1)
                    continue
                for item in batch:
                    await queue.put(item)

        async def worker(idx: int) -> None:
            while not self._stop.is_set():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if self.store.frontier_size() == 0 and queue.empty():
                        return
                    continue
                try:
                    await self._process(*item)
                except Exception as exc:
                    log.exception("worker %d crashed on %s: %s", idx, item[0], exc)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker(i)) for i in range(self.cfg.concurrency)]
        feeder_task = asyncio.create_task(feeder())

        await feeder_task
        for w in workers:
            w.cancel()
        for w in workers:
            try:
                await w
            except (asyncio.CancelledError, Exception):
                pass

    async def _process(self, url: str, source_page: str, link_text: str) -> None:
        if self.cfg.max_pages and self._pages_fetched >= self.cfg.max_pages:
            self._stop.set()
            return

        if self.store.has_seen(url):
            return

        same_site = is_same_site(url, self.cfg.seed_host, self.cfg.scope)

        # External: only HEAD-check (if enabled), never crawl deeper.
        if not same_site:
            if not self.cfg.check_external:
                return
            result = await self.fetcher.head(url)
            self._pages_fetched += 1
            self.store.mark_seen(url, result.status_code)
            self._maybe_record(url, source_page, link_text, result, parse=False, same_site=False)
            if self.cfg.verbose:
                _log_progress(result, url, source_page)
            return

        # Same-site path filters
        path = path_of(url)
        if self.cfg.exclude and matches_any(path, self.cfg.exclude):
            return
        if self.cfg.include and not matches_any(path, self.cfg.include):
            # Still HEAD-check — skipping by include filter shouldn't hide a 404.
            result = await self.fetcher.head(url)
            self._pages_fetched += 1
            self.store.mark_seen(url, result.status_code)
            self._maybe_record(url, source_page, link_text, result, parse=False, same_site=True)
            if self.cfg.verbose:
                _log_progress(result, url, source_page)
            return

        # Same-site, in-scope: HEAD for non-HTML, GET for HTML candidates.
        if looks_non_html(url):
            result = await self.fetcher.head(url)
            self._pages_fetched += 1
            self.store.mark_seen(url, result.status_code)
            self._maybe_record(url, source_page, link_text, result, parse=False, same_site=True)
            if self.cfg.verbose:
                _log_progress(result, url, source_page)
            return

        result = await self.fetcher.get(url)
        self._pages_fetched += 1
        self.store.mark_seen(url, result.status_code)
        self._maybe_record(url, source_page, link_text, result, parse=True, same_site=True)
        if self.cfg.verbose:
            _log_progress(result, url, source_page)

        if result.ok_html and result.body:
            try:
                html = result.body.decode("utf-8", errors="replace")
            except Exception:
                html = ""
            for link in extract_links(html, result.final_url or url):
                normalized = normalize(link.url) or ""
                if not normalized:
                    continue
                if normalized in self._enqueued:
                    continue
                self._enqueued.add(normalized)
                self.store.push_frontier(normalized, url, link.text)

    def _maybe_record(
        self,
        url: str,
        source_page: str,
        link_text: str,
        result,
        *,
        parse: bool,
        same_site: bool,
    ) -> None:
        is_soft = False
        soft_reason = ""
        if self.cfg.soft_404 and parse and result.ok_html and result.body:
            is_soft, soft_reason = self.soft404.is_soft_404(url, result.body, result.status_code or 0)

        if not result.is_broken and not is_soft:
            return

        if is_soft:
            status_label = "soft-404"
            error = soft_reason
        else:
            status_label, error = classify(result)
            if not same_site and result.status_code in _AMBIGUOUS_EXTERNAL_STATUSES:
                error = f"http-{result.status_code}-likely-anti-bot"
                status_label = "blocked"

        self.store.add_finding(
            Finding(
                status=status_label,
                url=url,
                final_url=result.final_url or "",
                source_page=source_page,
                link_text=link_text,
                redirect_chain=result.redirect_chain,
                error=error,
            )
        )


def _log_progress(result, url: str, source_page: str) -> None:
    label, _ = classify(result)
    log.info("[%s] %s (from %s)", label, url, source_page or "<seed>")


def derive_seed_host(seed_url: str) -> str:
    return host_of(seed_url)
