# 404hunter

A polite CLI crawler that scans a website and reports every URL that returns a broken status (4xx, 5xx, soft-404, timeout, DNS error). For each broken link it records *which page* references it, so you know where to fix.

## Requirements

- Python 3.11 or newer

## Quick start

From the project root (`/path/to/404hunter`):

```bash
# 1. Install the package
pip install -e .

# 2. Run a scan
hunter https://example.com -o broken.csv -v
```

The first time it runs, it will:

1. fetch the seed URL,
2. discover and crawl every same-host link reachable from it (server-rendered HTML only),
3. HEAD-check external links to spot dead outbound references,
4. write `broken.csv` and a human-readable `broken.txt` next to it.

If you skip `-o`, the report is named `broken-YYYYMMDD-HHMM.csv` in the current directory.

## Examples

```bash
# Fast scan with verbose progress logging
hunter https://example.com -o report.csv -v

# Be gentler on a small server (1 request at a time, 1s gap)
hunter https://example.com -c 1 -d 1.0

# Only crawl /blog/, skip /admin/
hunter https://example.com --include '/blog/*' --exclude '/admin/*'

# Scan a staging site behind basic auth, ignore TLS issues
hunter https://staging.example.com --auth user:pass --insecure

# Pass a session cookie
hunter https://example.com --header "Cookie: session=abc123"

# Include subdomains (api.example.com, www.example.com, ...)
hunter https://example.com --scope domain

# Resume a crawl that crashed or was interrupted
hunter https://example.com --resume
```

## Running the test suite

```bash
pip install -e ".[dev]"
pytest
```

## Common options

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

See `hunter --help` for the full list.

## Output format

`broken.csv` columns:

```
status, url, final_url, source_page, link_text, redirect_chain, error
```

`status` is one of: a numeric HTTP code (`404`, `500`, ...), `soft-404`, `timeout`, `ssl-error`, `dns-error`, `connect-error`, `error`.

`broken.txt` is a quick-scan version with one line per broken URL:

```
      404  https://example.com/missing  (found on: https://example.com/blog/)
 soft-404  https://example.com/old      (found on: https://example.com/)  [heuristic-match]
```

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
