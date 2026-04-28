"""Write findings to CSV (and a human-readable TXT companion)."""
from __future__ import annotations

import csv
from pathlib import Path

from .state import Finding


CSV_COLUMNS = [
    "status",
    "url",
    "final_url",
    "source_page",
    "link_text",
    "redirect_chain",
    "error",
]


def write_csv(findings: list[Finding], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for f in findings:
            writer.writerow(
                [
                    f.status,
                    f.url,
                    f.final_url,
                    f.source_page,
                    f.link_text,
                    " -> ".join(f.redirect_chain),
                    f.error,
                ]
            )


def write_txt(findings: list[Finding], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        if not findings:
            fh.write("No broken links found.\n")
            return
        for f in findings:
            source = f.source_page or "<seed>"
            line = f"{f.status:>9}  {f.url}  (found on: {source})"
            if f.error:
                line += f"  [{f.error}]"
            fh.write(line + "\n")


def companion_txt_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(".txt")
