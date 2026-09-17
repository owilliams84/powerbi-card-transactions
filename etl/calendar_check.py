"""Answer key for the Calendar page, from the CSVs, and the diff against the live model.

    python etl/calendar_check.py --queries calendar_queries.json
    powershell -File etl/calendar_verify.ps1 -QueriesFile calendar_queries.json > calendar_dump.txt
    python etl/calendar_check.py --compare calendar_dump.txt

Spend per day with no DAX involved: Amount summed over approved rows, refunds included. Every
cell of all four views is checked for its value and its 1-5 shade band.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import milestone_calendar
from calendar_config import CALENDAR

DATA = Path(__file__).resolve().parents[1] / "data"


def daily() -> dict[date, float]:
    with (DATA / "dim_outcome.csv").open(encoding="utf-8", newline="") as fh:
        approved = {r["OutcomeKey"] for r in csv.DictReader(fh) if r["IsApproved"] == "Yes"}
    # Decimal: a day is thousands of two-decimal amounts, and float drift would show at the cent.
    spend: dict[date, Decimal] = defaultdict(Decimal)
    for path in sorted(DATA.glob("fact_transaction_*.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["OutcomeKey"] in approved:
                    spend[date.fromisoformat(r["Date"])] += Decimal(r["Amount"])
    return {d: float(v) for d, v in spend.items()}


def bounds() -> tuple[date, date]:
    with (DATA / "dim_date.csv").open(encoding="utf-8", newline="") as fh:
        days = [date.fromisoformat(r["Date"]) for r in csv.DictReader(fh)]
    return min(days), max(days)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries")
    ap.add_argument("--compare")
    args = ap.parse_args()
    first, last = bounds()
    if args.queries:
        pairs = milestone_calendar.verify_queries(CALENDAR, list(range(first.year, last.year + 1)))
        Path(args.queries).write_text(json.dumps([{"view": v, "dax": q} for v, q in pairs]), encoding="utf-8")
        print(f"{len(pairs)} queries -> {args.queries}")
    if args.compare:
        key = milestone_calendar.expected(daily(), first, last)
        sys.exit(1 if milestone_calendar.compare(key, Path(args.compare)) else 0)


if __name__ == "__main__":
    main()
