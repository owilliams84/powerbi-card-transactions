"""Recompute every headline measure in pandas and diff it against what the model returns.

The model is the thing being checked, so the check cannot use it. This reads data/ straight
from the CSVs, computes the same eight figures at four grains, and compares them to the CSV
that etl/checks/verify.dax produced against the live model.

    powershell -File etl/query_model.ps1 -DaxFile etl/checks/verify.dax -Csv > etl/checks/dax_actual.csv
    python etl/verify_measures.py

Non-zero exit means a measure and its pandas equivalent disagree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ACTUAL = ROOT / "etl" / "checks" / "dax_actual.csv"

TOL_ABS = 0.011      # spend is rounded to cents on both sides
TOL_RATE = 1e-4      # rates are rounded to 4-6 dp in the DAX


def load() -> pd.DataFrame:
    fact = pd.concat([pd.read_csv(p) for p in sorted(DATA.glob("fact_transaction_*.csv"))],
                     ignore_index=True)
    fact["Year"] = pd.to_datetime(fact["Date"]).dt.year
    outcome = pd.read_csv(DATA / "dim_outcome.csv")
    fraud = pd.read_csv(DATA / "dim_fraud_status.csv")
    chan = pd.read_csv(DATA / "dim_channel.csv")
    mcc = pd.read_csv(DATA / "dim_merchant_category.csv")
    geo = pd.read_csv(DATA / "dim_geography.csv")
    f = (fact
         .merge(outcome[["OutcomeKey", "IsApproved"]], on="OutcomeKey")
         .merge(fraud[["FraudKey", "IsLabelled", "IsFraud"]], on="FraudKey")
         .merge(chan[["ChannelKey", "Channel"]], on="ChannelKey")
         .merge(mcc[["MCC", "CategoryGroup"]], on="MCC")
         .merge(geo[["GeoKey", "LocationType"]], on="GeoKey"))
    return f


def figures(g: pd.DataFrame) -> dict:
    approved_mask = g["IsApproved"] == "Yes"
    labelled = int((g["IsLabelled"] == "Yes").sum())
    fraud = int((g["IsFraud"] == "Yes").sum())
    n = len(g)
    return dict(
        transactions=n,
        approved=int(approved_mask.sum()),
        spend=round(float(g.loc[approved_mask, "Amount"].sum()), 2),
        decline_rate=round(1 - approved_mask.sum() / n, 6) if n else 0.0,
        labelled=labelled,
        fraud=fraud,
        fraud_10k=round(fraud / labelled * 10000, 4) if labelled else 0.0,
        fraud_10k_naive=round(fraud / n * 10000, 4) if n else 0.0,
        cards=int(g["CardKey"].nunique()),
        clients=int(g["ClientKey"].nunique()),
    )


def main() -> None:
    if not ACTUAL.exists():
        print(f"missing {ACTUAL} - run query_model.ps1 against the live model first")
        sys.exit(2)

    actual = pd.read_csv(ACTUAL)
    actual.columns = [c.strip("[]") for c in actual.columns]
    actual["key"] = actual["key"].astype(str)

    f = load()
    expected_rows = []
    for grain, col in (("year", "Year"), ("channel", "Channel"),
                       ("category", "CategoryGroup"), ("loctype", "LocationType")):
        for key, g in f.groupby(col):
            expected_rows.append(dict(grain=grain, key=str(key), **figures(g)))
    expected = pd.DataFrame(expected_rows)

    merged = expected.merge(actual, on=["grain", "key"], how="outer",
                            suffixes=("_pandas", "_dax"), indicator=True)
    problems = []
    only = merged[merged["_merge"] != "both"]
    for r in only.itertuples():
        problems.append(f"{r.grain}/{r.key}: present in {r._merge} only")

    both = merged[merged["_merge"] == "both"]
    fields = ["transactions", "approved", "spend", "decline_rate", "labelled",
              "fraud", "fraud_10k", "fraud_10k_naive", "cards", "clients"]
    for field in fields:
        a = both[f"{field}_pandas"].astype(float)
        # A DAX measure over an empty set returns BLANK, which lands here as NaN - so a group
        # with no declines and no fraud reads blank where pandas reads 0. Those mean the same
        # thing, and they must be filled before the comparison: NaN > tol is False, so leaving
        # them would let a genuine mismatch through as a pass rather than a failure.
        b = both[f"{field}_dax"].astype(float).fillna(0.0)
        tol = TOL_RATE if field in ("decline_rate",) else TOL_ABS
        if field in ("fraud_10k", "fraud_10k_naive"):
            tol = 0.011
        bad = (a - b).abs() > tol
        if bad.isna().any():
            problems.append(f"{field}: comparison produced NaN - a value is unparseable")
        for r, av, bv in zip(both[bad].itertuples(), a[bad], b[bad]):
            problems.append(f"{r.grain}/{r.key} {field}: pandas={av} dax={bv}")

    print(f"{len(both)} rows compared across {len(fields)} measures "
          f"({len(both) * len(fields)} checks)")
    if problems:
        print("\nMISMATCHES")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("every figure agrees")

    # The three numbers the write-up quotes, printed so they can be copied rather than retyped.
    total = figures(f)
    print("\nheadline, recomputed from the CSVs:")
    print("  transactions      %s" % f"{total['transactions']:,}")
    print("  approved          %s" % f"{total['approved']:,}")
    print("  spend             %s" % f"${total['spend']:,.2f}")
    print("  decline rate      %.2f%%" % (100 * total["decline_rate"]))
    print("  label coverage    %.1f%%" % (100 * total["labelled"] / total["transactions"]))
    print("  fraud per 10k     %.1f  (naive %.1f)"
          % (total["fraud_10k"], total["fraud_10k_naive"]))
    attempted = round(float(f["Amount"].sum()), 2)
    print("  attempted value   %s  (overstates spend by $%s)"
          % (f"${attempted:,.2f}", f"{attempted - total['spend']:,.2f}"))


if __name__ == "__main__":
    main()
