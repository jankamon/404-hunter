"""Typer CLI entrypoint."""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from .crawler import Crawler, CrawlConfig, derive_seed_host
from .fetcher import Fetcher, HostRateLimiter, make_client
from .normalize import normalize
from .report import companion_txt_path, write_csv, write_txt
from .soft404 import Soft404Detector
from .state import Store

app = typer.Typer(add_completion=False, help="Polite broken-link crawler.")


def _parse_auth(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    if ":" not in value:
        raise typer.BadParameter("--auth must be in USER:PASS form")
    user, _, password = value.partition(":")
    return user, password


def _parse_headers(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in values:
        if ":" not in v:
            raise typer.BadParameter(f"--header must be 'Name: value', got {v!r}")
        name, _, val = v.partition(":")
        out[name.strip()] = val.strip()
    return out


@app.command()
def scan(
    url: str = typer.Argument(..., help="Starting URL to crawl, e.g. https://example.com"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="CSV report path."),
    concurrency: int = typer.Option(5, "-c", "--concurrency", min=1, max=64),
    delay: float = typer.Option(0.5, "-d", "--delay", min=0.0, help="Per-host minimum gap (s)."),
    timeout: float = typer.Option(15.0, "-t", "--timeout", min=1.0, help="Read timeout (s)."),
    max_pages: int = typer.Option(10_000, "--max-pages", min=0, help="Hard cap; 0 = unlimited."),
    max_body_mb: int = typer.Option(
        5, "--max-body-mb", min=1, help="Max HTML body downloaded per page, in MB."
    ),
    check_external: bool = typer.Option(True, "--check-external/--no-check-external"),
    soft_404: bool = typer.Option(True, "--soft-404/--no-soft-404"),
    scope: str = typer.Option("host", "--scope", help="host | domain"),
    include: list[str] = typer.Option([], "--include", help="Glob path filter; repeatable."),
    exclude: list[str] = typer.Option([], "--exclude", help="Glob path filter; repeatable."),
    auth: Optional[str] = typer.Option(None, "--auth", help="USER:PASS for HTTP basic auth."),
    header: list[str] = typer.Option([], "--header", help="Extra header; repeatable."),
    user_agent: str = typer.Option(
        "404-hunter/0.1 (+https://github.com/; broken-link audit)",
        "--user-agent",
    ),
    insecure: bool = typer.Option(False, "--insecure", help="Skip TLS verification."),
    resume: bool = typer.Option(False, "--resume", help="Continue prior run from .hunter-state.db."),
    state_path: Path = typer.Option(Path(".hunter-state.db"), "--state-path"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Crawl URL and report broken links."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        stream=sys.stderr,
    )

    if scope not in {"host", "domain"}:
        raise typer.BadParameter("--scope must be 'host' or 'domain'")
    if max_pages == 0:
        typer.echo("warning: --max-pages 0 disables the safety cap. CTRL-C to abort.", err=True)

    seed = normalize(url)
    if not seed:
        raise typer.BadParameter(f"Could not parse seed URL: {url!r}")

    if output is None:
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        output = Path(f"broken-{ts}.csv")

    if not resume and state_path.exists():
        state_path.unlink()

    asyncio.run(
        _run(
            seed=seed,
            output=output,
            concurrency=concurrency,
            delay=delay,
            timeout=timeout,
            max_pages=max_pages,
            max_body_bytes=max_body_mb * 1_000_000,
            check_external=check_external,
            soft_404=soft_404,
            scope=scope,
            include=include,
            exclude=exclude,
            auth=_parse_auth(auth),
            extra_headers=_parse_headers(header),
            user_agent=user_agent,
            insecure=insecure,
            resume=resume,
            state_path=state_path,
            verbose=verbose,
        )
    )


async def _run(
    *,
    seed: str,
    output: Path,
    concurrency: int,
    delay: float,
    timeout: float,
    max_pages: int,
    max_body_bytes: int,
    check_external: bool,
    soft_404: bool,
    scope: str,
    include: list[str],
    exclude: list[str],
    auth: tuple[str, str] | None,
    extra_headers: dict[str, str],
    user_agent: str,
    insecure: bool,
    resume: bool,
    state_path: Path,
    verbose: bool,
) -> None:
    store = Store(state_path)
    rate_limiter = HostRateLimiter(min_delay=delay)
    client = make_client(
        timeout=timeout,
        user_agent=user_agent,
        auth=auth,
        extra_headers=extra_headers,
        verify=not insecure,
        concurrency=concurrency,
    )
    try:
        fetcher = Fetcher(client=client, rate_limiter=rate_limiter, max_body_bytes=max_body_bytes)
        crawler = Crawler(
            config=CrawlConfig(
                seed_url=seed,
                seed_host=derive_seed_host(seed),
                scope=scope,
                concurrency=concurrency,
                max_pages=max_pages,
                check_external=check_external,
                soft_404=soft_404,
                include=list(include) if include else None,
                exclude=list(exclude) if exclude else None,
                verbose=verbose,
            ),
            fetcher=fetcher,
            store=store,
            soft404_detector=Soft404Detector(),
        )

        typer.echo(f"crawling {seed} (scope={scope}, concurrency={concurrency}, delay={delay}s)", err=True)
        await crawler.run(resume=resume)

        findings = store.all_findings()
        write_csv(findings, output)
        write_txt(findings, companion_txt_path(output))

        stats = store.stats()
        typer.echo(
            f"done. checked {stats['seen']} URLs, {len(findings)} broken. "
            f"report: {output}  txt: {companion_txt_path(output)}",
            err=True,
        )
    finally:
        await client.aclose()
        store.close()


if __name__ == "__main__":
    app()
