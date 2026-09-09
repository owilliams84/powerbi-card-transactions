"""Emit the JSON the milestonebi.com case study reads.

The case study redraws the Power BI report for the web, so its numbers have to be the same
numbers. They are computed here from data/ - the same CSVs the model loads - rather than typed
into the page, and verify_measures.py has already proved that those CSVs and the model agree.

    python etl/build_web_data.py [--out <path>]

Writes web/card-transactions.json (about 40 KB).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "web" / "card-transactions.json"


def r(v, dp=2):
    return None if pd.isna(v) else round(float(v), dp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    fact = pd.concat([pd.read_csv(p) for p in sorted(DATA.glob("fact_transaction_*.csv"))],
                     ignore_index=True)
    outcome = pd.read_csv(DATA / "dim_outcome.csv")
    fraud_d = pd.read_csv(DATA / "dim_fraud_status.csv")
    chan = pd.read_csv(DATA / "dim_channel.csv")
    mcc = pd.read_csv(DATA / "dim_merchant_category.csv")
    geo = pd.read_csv(DATA / "dim_geography.csv")
    client = pd.read_csv(DATA / "dim_client.csv")
    card = pd.read_csv(DATA / "dim_card.csv")
    quality = json.loads((DATA / "quality_report.json").read_text(encoding="utf-8"))

    f = (fact
         .merge(outcome[["OutcomeKey", "Outcome", "OutcomeClass", "IsApproved"]], on="OutcomeKey")
         .merge(fraud_d[["FraudKey", "IsLabelled", "IsFraud"]], on="FraudKey")
         .merge(chan[["ChannelKey", "Channel", "ChannelType"]], on="ChannelKey")
         .merge(mcc[["MCC", "CategoryGroup"]], on="MCC")
         .merge(geo[["GeoKey", "Location", "LocationType"]], on="GeoKey"))
    dt = pd.to_datetime(f["Date"])
    f["Year"] = dt.dt.year
    f["Month"] = dt.dt.to_period("M").astype(str)
    ap_mask = f["IsApproved"] == "Yes"

    def agg(g: pd.DataFrame) -> dict:
        a = g["IsApproved"] == "Yes"
        lab = int((g["IsLabelled"] == "Yes").sum())
        fr = int((g["IsFraud"] == "Yes").sum())
        return dict(
            n=int(len(g)),
            approved=int(a.sum()),
            spend=r(g.loc[a, "Amount"].sum()),
            # Six places, not five: at five, 0.0159547 lands on 0.01595 and the page then
            # renders 1.59% where the report renders 1.60%.
            decline=r(1 - a.sum() / len(g), 6) if len(g) else 0,
            labelled=lab,
            fraud=fr,
            per10k=r(fr / lab * 10000, 1) if lab else 0,
            per10k_naive=r(fr / len(g) * 10000, 1) if len(g) else 0,
        )

    out: dict = {}

    total = agg(f)
    out["totals"] = dict(
        **total,
        clients=int(f["ClientKey"].nunique()),
        cards_active=int(f["CardKey"].nunique()),
        cards_on_file=int(len(card)),
        attempted_value=r(f["Amount"].sum()),
        avg_purchase=r(f.loc[ap_mask & (f["Amount"] > 0), "Amount"].mean()),
        refund_share=r((f.loc[ap_mask, "Amount"] < 0).mean(), 6),
        first=str(dt.min().date()),
        last=str(dt.max().date()),
    )
    out["totals"]["overstatement"] = r(out["totals"]["attempted_value"] - total["spend"])

    # Channel share by month - the step change, and the reason this page exists.
    cm = f.groupby(["Month", "Channel"]).size().unstack(fill_value=0)
    cm = cm.div(cm.sum(axis=1), axis=0)
    out["channel_by_month"] = dict(
        months=list(cm.index),
        series={c: [r(v, 4) for v in cm[c]] for c in ["Swipe", "Chip", "Online"]},
    )

    out["by_year"] = [dict(year=int(y), **agg(g)) for y, g in f.groupby("Year")]
    out["by_channel"] = [dict(channel=c, **agg(g)) for c, g in f.groupby("Channel")]
    out["by_category"] = sorted(
        [dict(category=c, **agg(g)) for c, g in f.groupby("CategoryGroup")],
        key=lambda d: -d["spend"])
    out["by_loctype"] = [dict(location=c, **agg(g)) for c, g in f.groupby("LocationType")]
    out["by_outcome"] = sorted(
        [dict(outcome=o, cls=g["OutcomeClass"].iloc[0], n=int(len(g)))
         for o, g in f[~ap_mask].groupby("Outcome")],
        key=lambda d: -d["n"])

    # Spend by month, for the trend line.
    sm = f[ap_mask].groupby("Month")["Amount"].sum()
    out["spend_by_month"] = dict(months=list(sm.index), values=[r(v) for v in sm])

    # Approved transactions by hour.
    hr = f[ap_mask].groupby("Hour").size()
    out["by_hour"] = dict(hours=[int(h) for h in hr.index], values=[int(v) for v in hr])

    # Credit score bands, for the customer panel.
    # The CSV column is ClientID; "Client" is only the display name the model gives it.
    cl = f.merge(client[["ClientID", "CreditScoreBand", "CreditScoreBandSort"]]
                 .rename(columns={"ClientID": "ClientKey"}), on="ClientKey")
    bands = []
    for (b, s), g in cl.groupby(["CreditScoreBand", "CreditScoreBandSort"]):
        bands.append(dict(band=b, sort=int(s), clients=int(g["ClientKey"].nunique()), **agg(g)))
    out["by_credit_band"] = sorted(bands, key=lambda d: d["sort"])

    out["quality"] = {k: quality[k] for k in (
        "population_transactions", "population_clients",
        "population_clients_with_transactions", "population_clients_never_transacting",
        "population_cards_never_transacting", "sample_transactions", "sample_clients",
        "sample_cards_on_file", "sample_cards_active", "negative_amounts", "zero_amounts",
        "state_null_not_online", "source_state_column", "chip_switch", "mcc_codes",
        "mcc_groups", "partial_final_year", "dropped_columns_card", "dropped_columns_client",
    )}

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"{path} {path.stat().st_size / 1024:.1f} KB")
    print(f"  {total['n']:,} transactions, ${total['spend']:,.0f} spend, "
          f"{total['per10k']} fraud per 10k")


if __name__ == "__main__":
    main()
