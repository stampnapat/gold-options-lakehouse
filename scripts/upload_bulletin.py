"""Upload hand-downloaded Section 64 PDFs to the landing volume.

The target name comes from the bulletin itself, never from the local file name:

    section64/<trade_date>_<FINAL|PRELIMINARY>.pdf

so a mislabeled download cannot land under the wrong date. Files already in the
volume are skipped, which keeps re-runs safe (Auto Loader would ignore a
re-uploaded path anyway). Uses the Databricks CLI, so `databricks auth login`
must have been run.

    python scripts/upload_bulletin.py ~/Downloads/Section64_*.pdf
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from cme_bulletin.section64 import read_header


def target_name(pdf: Path) -> str:
    try:
        header = read_header(pdf)
    except Exception as exc:  # pdfplumber raises several types for non-PDF input
        raise ValueError(f"{pdf}: cannot read as PDF ({exc})") from exc
    if header.trade_date is None or header.status is None:
        raise ValueError(f"{pdf}: not a Section 64 bulletin (no trade date or status on page 1)")
    return f"{header.trade_date.isoformat()}_{header.status}.pdf"


def existing(volume_dir: str) -> set[str]:
    out = subprocess.run(
        ["databricks", "fs", "ls", volume_dir], capture_output=True, text=True, check=False
    )
    return set(out.stdout.split()) if out.returncode == 0 else set()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("--catalog", default="gold_options_dev")
    args = ap.parse_args()

    volume_dir = f"dbfs:/Volumes/{args.catalog}/bronze/landing/section64"
    auth = subprocess.run(["databricks", "current-user", "me"], capture_output=True, text=True)
    if auth.returncode != 0:
        print(
            "Databricks login has expired. Run:\n"
            "  databricks auth login --host https://dbc-abd11c35-2b32.cloud.databricks.com"
            " --profile DEFAULT\nthen run this script again.",
            file=sys.stderr,
        )
        return 2
    subprocess.run(["databricks", "fs", "mkdir", volume_dir], check=True)
    present = existing(volume_dir)

    failed = False
    for pdf in args.pdfs:
        try:
            name = target_name(pdf)
        except ValueError as exc:
            print(f"SKIP  {exc}", file=sys.stderr)
            failed = True
            continue
        if name in present:
            print(f"HAVE  {pdf.name} -> {name}")
            continue
        subprocess.run(["databricks", "fs", "cp", str(pdf), f"{volume_dir}/{name}"], check=True)
        present.add(name)
        print(f"SENT  {pdf.name} -> {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
