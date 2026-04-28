# 404hunter

A polite CLI crawler that scans a website and reports every URL that returns a broken status (4xx, 5xx, soft-404, timeout, DNS error). For each broken link it records *which page* references it, so you know where to fix.

## Install

```bash
pip install -e .
```

## Usage

```bash
hunter scan https://example.com -o broken.csv
```

Common options:

| Flag | Default | What |
|---|---|---|
| `-o, --output` | `broken-<timestamp>.csv` | CSV report path. A `.txt` companion is written next to it. |
| `-c, --concurrency` | `5` | Parallel in-flight requests. |
| `-d, --delay` | `0.5` | Minimum seconds between requests to the same host. |
| `-t, --timeout` | `15` | Read timeout in seconds. |
| `--max-pages` | `10000` | Hard cap on pages crawled. `0` = unlimited (warns). |
| `--check-external / --no-check-external` | on | HEAD external links to detect dead outbound references. |
| `--soft-404 / --no-soft-404` | on | Detect pages that return 200 but show "not found". |
| `--scope` | `host` | What counts as same site: `host` or `domain`. |
| `--include` / `--exclude` | — | Glob path filters; repeatable. |
| `--auth USER:PASS` | — | HTTP basic auth. |
| `--header` | — | Extra header (`"Cookie: ..."`); repeatable. |
| `--user-agent` | `404hunter/0.1` | UA override. |
| `--insecure` | off | Skip TLS verification. |
| `--resume` | off | Continue from prior `.hunter-state.db`. |
| `-v, --verbose` | off | Log each URL as checked. |

## Safety

Defaults are tuned to avoid accidental DoS:

- 5 parallel requests max
- 500ms minimum gap per host
- Exponential backoff with jitter on 429/503
- 10000-page hard cap
- Identifiable User-Agent

## Out of scope

- JavaScript-rendered SPAs (no headless browser)
- Form submissions / login flows beyond static cookies
- Fixing broken links — this is read-only reporting
