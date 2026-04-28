"""SQLite-backed state for resume support and finding storage."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class Finding:
    status: str  # "404", "500", "soft-404", "timeout", "ssl-error", "dns-error", "other"
    url: str
    final_url: str = ""
    source_page: str = ""
    anchor_text: str = ""
    raw_href: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    error: str = ""


SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    url TEXT PRIMARY KEY,
    status_code INTEGER,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    source_page TEXT,
    anchor_text TEXT,
    raw_href TEXT,
    redirect_chain TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS frontier (
    url TEXT PRIMARY KEY,
    source_page TEXT,
    anchor_text TEXT,
    raw_href TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_url ON findings(url);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self.conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def has_seen(self, url: str) -> bool:
        with self.cursor() as cur:
            cur.execute("SELECT 1 FROM seen WHERE url = ? LIMIT 1", (url,))
            return cur.fetchone() is not None

    def mark_seen(self, url: str, status_code: int | None) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO seen (url, status_code, fetched_at) VALUES (?, ?, datetime('now'))",
                (url, status_code),
            )

    def push_frontier(self, url: str, source_page: str, anchor_text: str, raw_href: str) -> bool:
        """Insert into frontier; return True if new."""
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO frontier (url, source_page, anchor_text, raw_href) VALUES (?, ?, ?, ?)",
                (url, source_page, anchor_text, raw_href),
            )
            return cur.rowcount > 0

    def pop_frontier_batch(self, n: int) -> list[tuple[str, str, str, str]]:
        """Pop up to n entries from the frontier."""
        with self.cursor() as cur:
            cur.execute("SELECT url, source_page, anchor_text, raw_href FROM frontier LIMIT ?", (n,))
            rows = cur.fetchall()
            if rows:
                cur.executemany("DELETE FROM frontier WHERE url = ?", [(r[0],) for r in rows])
            return rows

    def frontier_size(self) -> int:
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM frontier")
            return int(cur.fetchone()[0])

    def add_finding(self, f: Finding) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO findings (status, url, final_url, source_page, anchor_text, raw_href, redirect_chain, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f.status,
                    f.url,
                    f.final_url,
                    f.source_page,
                    f.anchor_text,
                    f.raw_href,
                    json.dumps(f.redirect_chain) if f.redirect_chain else "",
                    f.error,
                ),
            )

    def all_findings(self) -> list[Finding]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT status, url, final_url, source_page, anchor_text, raw_href, redirect_chain, error FROM findings ORDER BY id"
            )
            out: list[Finding] = []
            for row in cur.fetchall():
                chain = json.loads(row[6]) if row[6] else []
                out.append(
                    Finding(
                        status=row[0],
                        url=row[1],
                        final_url=row[2] or "",
                        source_page=row[3] or "",
                        anchor_text=row[4] or "",
                        raw_href=row[5] or "",
                        redirect_chain=chain,
                        error=row[7] or "",
                    )
                )
            return out

    def stats(self) -> dict[str, int]:
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM seen")
            seen = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM findings")
            findings = int(cur.fetchone()[0])
            return {"seen": seen, "findings": findings}
