"""Turn the two sets of measurements into one comparison, and check the two models agree.

    python perf/etl/compare.py

Reads perf/measurements/{star,naive}_*.csv and writes perf/web/model-performance.json plus a
summary to stdout.

Three things happen here that are worth knowing about:

1.  **Attribute hierarchies are folded back onto their columns.** The engine builds a structure
    per column so the column can be grouped and filtered, and it reports those as pseudo-tables
    named `H$<table>$<column>`. They are part of what a column costs, so leaving them out would
    flatter the wide model - they are biggest exactly where the column is widest.

2.  **Result equivalence is checked before any timing is reported.** The two models use different
    table and column names, and the naive one keeps the source's own labels ("Swipe Transaction"
    rather than "Swipe"), so the comparison is on the multiset of numeric values each query
    returns. If a query disagrees between models it is reported and excluded: a performance
    comparison between two models that give different answers measures nothing.

3.  **Nothing is described as "x times faster" unless the gap clears the harness floor.** Every
    query here round-trips through ADOMD, which costs a few milliseconds on its own. A 3 ms
    difference between two 12 ms queries is noise, and saying otherwise would be the same kind
    of overclaiming this whole exercise exists to argue against.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MEAS = ROOT / "perf" / "measurements"
OUT = ROOT / "perf" / "web" / "model-performance.json"

# Below this, a difference in query time is the harness, not the model.
NOISE_FLOOR_MS = 8.0
H_PREFIX = re.compile(r"^H\$(?P<table>.+?)\$(?P<column>.+)$")


def clean(name: str) -> str:
    """'Transactions (10)' -> 'Transactions'; 'Transaction ID (13)' -> 'Transaction ID'."""
    return re.sub(r"\s*\(\d+\)\s*$", "", str(name)).strip()


def storage(label: str) -> tuple[pd.DataFrame, dict]:
    dic = pd.read_csv(MEAS / f"{label}_dictionary.csv")
    seg = pd.read_csv(MEAS / f"{label}_segments.csv")

    d = (dic.groupby(["TABLE_ID", "COLUMN_ID"])["DICTIONARY_SIZE"].sum()
            .rename("dictionary").reset_index())
    s = (seg.groupby(["TABLE_ID", "COLUMN_ID"])["USED_SIZE"].sum()
            .rename("data").reset_index())
    both = d.merge(s, on=["TABLE_ID", "COLUMN_ID"], how="outer").fillna(0.0)

    # Fold H$<table>$<column> pseudo-tables onto the column they serve.
    rows = []
    for r in both.itertuples():
        m = H_PREFIX.match(str(r.TABLE_ID))
        if m:
            table, column, kind = clean(m["table"]), clean(m["column"]), "hierarchy"
        else:
            table, column, kind = clean(r.TABLE_ID), clean(r.COLUMN_ID), "column"
        rows.append(dict(table=table, column=column, kind=kind,
                         dictionary=float(r.dictionary), data=float(r.data)))
    df = pd.DataFrame(rows)

    # RowNumber is the engine's own internal column; it is real but not anybody's modelling
    # decision, so it is kept in the totals and never shown as a finding.
    per_col = (df.groupby(["table", "column"])[["dictionary", "data"]].sum().reset_index())
    per_col["total"] = per_col["dictionary"] + per_col["data"]
    hier = (df[df.kind == "hierarchy"].groupby(["table", "column"])[["dictionary", "data"]]
              .sum().sum(axis=1).rename("hierarchy").reset_index())
    per_col = per_col.merge(hier, on=["table", "column"], how="left").fillna({"hierarchy": 0.0})

    summary = dict(
        total_bytes=float(per_col["total"].sum()),
        dictionary_bytes=float(per_col["dictionary"].sum()),
        data_bytes=float(per_col["data"].sum()),
        hierarchy_bytes=float(per_col["hierarchy"].sum()),
        tables=int(per_col["table"].nunique()),
        columns=int(len(per_col)),
    )
    return per_col.sort_values("total", ascending=False), summary


def results_match(star: pd.DataFrame, naive: pd.DataFrame) -> dict[str, bool]:
    """Compare the multiset of numeric values each query returned, not the labels."""
    out = {}
    for query in sorted(set(star["query"]) | set(naive["query"])):
        def nums(df):
            v = pd.to_numeric(df.loc[df["query"] == query, "value"], errors="coerce").dropna()
            return sorted(round(float(x), 2) for x in v)
        a, b = nums(star), nums(naive)
        if len(a) != len(b):
            out[query] = False
            continue
        out[query] = all(abs(x - y) <= 0.02 for x, y in zip(a, b))
    return out


def main() -> None:
    star_cols, star_sum = storage("star")
    naive_cols, naive_sum = storage("naive")

    st = pd.read_csv(MEAS / "star_timings.csv")
    nt = pd.read_csv(MEAS / "naive_timings.csv")
    timings = st.merge(nt, on="query", suffixes=("_star", "_naive"))

    match = results_match(pd.read_csv(MEAS / "star_results.csv"),
                          pd.read_csv(MEAS / "naive_results.csv"))
    timings["results_match"] = timings["query"].map(match)

    # A disagreement is not a broken harness, it is the most interesting thing the harness can
    # find: the same question, asked of two models built from the same rows, answered
    # differently. It is excluded from the timing comparison and reported on its own.
    mismatched = sorted(q for q, ok in match.items() if not ok)
    divergence = []
    if mismatched:
        sres = pd.read_csv(MEAS / "star_results.csv")
        nres = pd.read_csv(MEAS / "naive_results.csv")
        print("QUERIES WHOSE RESULTS DISAGREE (excluded from the timing story):")
        for q in mismatched:
            print("  " + q)
            def wide(df):
                d = df[df["query"] == q].copy()
                d["i"] = d.groupby("column").cumcount()
                return d.pivot(index="i", columns="column", values="value")
            a, b = wide(sres), wide(nres)
            divergence.append(dict(
                query=q,
                star=[{str(c): (None if pd.isna(v) else v) for c, v in row.items()}
                      for _, row in a.iterrows()],
                naive=[{str(c): (None if pd.isna(v) else v) for c, v in row.items()}
                       for _, row in b.iterrows()]))
        print()

    timings["cold_delta_ms"] = (timings["cold_ms_naive"] - timings["cold_ms_star"]).round(1)
    timings["warm_delta_ms"] = (timings["warm_ms_naive"] - timings["warm_ms_star"]).round(1)
    timings["cold_separable"] = timings["cold_delta_ms"].abs() >= NOISE_FLOOR_MS

    payload = {
        "star": {**star_sum, "top_columns": [
            dict(column=f"{r.table}[{r.column}]", total=round(r.total),
                 dictionary=round(r.dictionary), data=round(r.data),
                 hierarchy=round(r.hierarchy))
            for r in star_cols.head(12).itertuples()]},
        "naive": {**naive_sum, "top_columns": [
            dict(column=f"{r.table}[{r.column}]", total=round(r.total),
                 dictionary=round(r.dictionary), data=round(r.data),
                 hierarchy=round(r.hierarchy))
            for r in naive_cols.head(12).itertuples()]},
        "timings": [
            dict(query=r.query, cold_star=r.cold_ms_star, cold_naive=r.cold_ms_naive,
                 warm_star=r.warm_ms_star, warm_naive=r.warm_ms_naive,
                 rows=int(r.rows_star) if pd.notna(r.rows_star) else None,
                 match=bool(r.results_match),
                 separable=bool(r.cold_separable))
            for r in timings.itertuples()],
        "divergence": divergence,
        "method": {
            "iterations": int(st["iterations"].iloc[0]),
            "statistic": "median",
            "noise_floor_ms": NOISE_FLOOR_MS,
            "hierarchies_included": True,
            "queries_matching": int(sum(match.values())),
            "queries_total": len(match),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")

    mb = lambda b: b / 1_048_576
    print(f"{'':22s} {'star':>12s} {'naive':>12s}   ratio")
    for k, label in (("total_bytes", "total"), ("dictionary_bytes", "dictionary"),
                     ("data_bytes", "data"), ("hierarchy_bytes", "hierarchies")):
        a, b = star_sum[k], naive_sum[k]
        print(f"  {label:20s} {mb(a):9.1f} MB {mb(b):9.1f} MB   {b / a:5.1f}x")
    print(f"  {'tables':20s} {star_sum['tables']:9d}    {naive_sum['tables']:9d}")
    print(f"  {'columns':20s} {star_sum['columns']:9d}    {naive_sum['columns']:9d}")

    print("\nnaive model, biggest columns:")
    for r in naive_cols.head(5).itertuples():
        share = r.total / naive_sum["total_bytes"]
        print(f"  {r.table}[{r.column}]".ljust(46) +
              f"{mb(r.total):7.1f} MB  {share:5.1%}")

    print("\ntimings (median of %d, ms):" % payload["method"]["iterations"])
    print(f"  {'query':24s} {'cold star':>10s} {'cold naive':>11s} "
          f"{'warm star':>10s} {'warm naive':>11s}  same answer")
    for r in timings.itertuples():
        print(f"  {r.query:24s} {r.cold_ms_star:10.1f} {r.cold_ms_naive:11.1f} "
              f"{r.warm_ms_star:10.1f} {r.warm_ms_naive:11.1f}  "
              f"{'yes' if r.results_match else 'NO'}")
    print(f"\n{payload['method']['queries_matching']}/{payload['method']['queries_total']} "
          f"queries return identical values from both models")
    print(f"written {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
