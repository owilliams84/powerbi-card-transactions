"""Build the one wide table the naive model loads.

The star schema in ../data is the same 160-client panel, modelled properly. This writes the
same transactions the way most Power BI files actually look: every dimension merged onto the
fact in Power Query, nothing thrown away, keys left as text because that is what the source
gave, and the timestamp kept to the minute because "we might need the time".

    python perf/etl/build_flat_source.py --source <folder with the unzipped Kaggle CSVs>

Writes perf/data/flat_transactions.csv - about 300 MB, so it is gitignored and regenerated
rather than committed. The star schema's CSVs in ../data are committed; this one exists only
to be measured against them.

Nothing here is a strawman. Every choice below is one I have seen in a production PBIX:
merging the dimensions in, keeping the id column "for drill-through", keeping the full
timestamp, and letting the numeric keys arrive as text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STAR = ROOT / "data"
OUT = ROOT / "perf" / "data" / "flat_transactions.csv"
LF = "\n"


def money(s: pd.Series) -> pd.Series:
    return s.astype("string").str.replace("$", "", regex=False).astype("float64")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="folder holding the unzipped Kaggle CSVs")
    args = ap.parse_args()
    src = Path(args.source)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # The same panel the star schema uses, so the two models hold identical rows.
    clients = set(pd.read_csv(STAR / "dim_client.csv")["ClientID"].tolist())
    cards = set(pd.read_csv(STAR / "dim_card.csv")["CardKey"].tolist())
    print(f"panel: {len(clients)} clients, {len(cards)} cards")

    users = pd.read_csv(src / "users_data.csv")
    users = users[users["id"].isin(clients)].copy()
    for c in ("per_capita_income", "yearly_income", "total_debt"):
        users[c] = money(users[c])
    users = users.rename(columns={
        "id": "ClientID", "current_age": "ClientAge", "gender": "ClientGender",
        "yearly_income": "ClientYearlyIncome", "total_debt": "ClientTotalDebt",
        "credit_score": "ClientCreditScore", "num_credit_cards": "ClientCardsHeld",
    })[["ClientID", "ClientAge", "ClientGender", "ClientYearlyIncome",
        "ClientTotalDebt", "ClientCreditScore", "ClientCardsHeld"]]

    cd = pd.read_csv(src / "cards_data.csv")
    cd = cd[cd["id"].isin(cards)].copy()
    cd["credit_limit"] = money(cd["credit_limit"])
    cd = cd.rename(columns={
        "id": "CardID", "card_brand": "CardBrand", "card_type": "CardType",
        "has_chip": "CardHasChip", "credit_limit": "CardCreditLimit",
        "acct_open_date": "CardAccountOpened", "num_cards_issued": "CardsIssued",
    })[["CardID", "CardBrand", "CardType", "CardHasChip", "CardCreditLimit",
        "CardAccountOpened", "CardsIssued"]]

    mcc = json.loads((src / "mcc_codes.json").read_text(encoding="utf-8"))
    mcc_df = pd.DataFrame([{"MCC": int(k), "MerchantCategory": v} for k, v in mcc.items()])

    labels = json.loads((src / "train_fraud_labels.json").read_text(encoding="utf-8"))["target"]
    fraud = pd.Series({int(k): v for k, v in labels.items()}, dtype="string")

    dtypes = {"id": "int64", "client_id": "int32", "card_id": "int32", "amount": "string",
              "use_chip": "string", "merchant_id": "int32", "merchant_city": "string",
              "merchant_state": "string", "zip": "float64", "mcc": "int32",
              "errors": "string"}

    kept = []
    for chunk in pd.read_csv(src / "transactions_data.csv", dtype=dtypes, chunksize=2_000_000):
        c = chunk[chunk["card_id"].isin(cards)]
        if len(c):
            kept.append(c.copy())
    tx = pd.concat(kept, ignore_index=True)
    del kept
    print(f"transactions: {len(tx):,}")

    tx["Amount"] = money(tx["amount"]).round(2)
    tx = tx.rename(columns={
        "id": "TransactionID", "client_id": "ClientID", "card_id": "CardID",
        "date": "Timestamp", "use_chip": "Channel", "merchant_id": "MerchantID",
        "merchant_city": "MerchantCity", "merchant_state": "MerchantState",
        "zip": "MerchantZip", "mcc": "MCC", "errors": "Errors",
    })
    tx["FraudLabel"] = tx["TransactionID"].map(fraud).fillna("")

    flat = (tx[["TransactionID", "Timestamp", "ClientID", "CardID", "Amount", "Channel",
                "MerchantID", "MerchantCity", "MerchantState", "MerchantZip", "MCC",
                "Errors", "FraudLabel"]]
            .merge(mcc_df, on="MCC", how="left")
            .merge(cd, on="CardID", how="left")
            .merge(users, on="ClientID", how="left"))

    # Keys as text. The source hands them over as numbers, and a merge in Power Query keeps
    # whatever type the first step produced - text far more often than not.
    for c in ("TransactionID", "ClientID", "CardID", "MerchantID", "MCC"):
        flat[c] = flat[c].astype("string")
    flat["MerchantZip"] = flat["MerchantZip"].astype("string").str.replace(".0", "",
                                                                          regex=False)
    flat["Errors"] = flat["Errors"].fillna("")
    flat["MerchantState"] = flat["MerchantState"].fillna("")
    flat["MerchantCity"] = flat["MerchantCity"].fillna("")
    flat["MerchantZip"] = flat["MerchantZip"].fillna("")

    flat = flat.sort_values("Timestamp").reset_index(drop=True)
    flat.to_csv(OUT, index=False, lineterminator=LF)

    size = OUT.stat().st_size
    print(f"{OUT.name}: {len(flat):,} rows x {len(flat.columns)} columns, "
          f"{size / 1e6:.0f} MB")
    print("\ncolumn cardinality (what the dictionary has to hold):")
    for c in flat.columns:
        print(f"  {c:22s} {flat[c].nunique():>9,}")


if __name__ == "__main__":
    main()
