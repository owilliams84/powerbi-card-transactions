"""Turn the CaixaBank card dataset into a star schema, and say what was wrong with it.

Source: https://www.kaggle.com/datasets/computingvictor/transactions-fraud-datasets
(Apache 2.0, created by CaixaBank Tech for the 2024 AI Hackathon). Five files, 1.42 GB:
13,305,915 transactions, 6,146 cards, 2,000 clients, 109 merchant category codes and a
partial set of fraud labels.

    python etl/build_star_schema.py --source <folder with the unzipped CSVs>

Writes data/ (dimensions + one fact file per year) and data/quality_report.json.

Two decisions worth reading before the numbers:

1.  The fact is a **client sample**, not the whole file. 13.3M transactions is 1.26 GB and
    cannot go in a public repo, and every aggregate grain that keeps merchant category and
    the card link runs to 5M+ rows. So the panel is cut to CLIENT_SAMPLE clients drawn with
    a fixed seed, and every one of their transactions is kept, at full transaction grain.
    That keeps one clean star schema and leaves every question answerable; it costs the
    ability to quote whole-population totals, so the report never does.

2.  **Amount is the attempted amount, not the settled one.** 1.6% of rows carry an error and
    were never authorised. Spend measures approved rows only; counting all of them overstates
    spend by about 1.5%.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

CLIENT_SAMPLE = 160
SEED = 20260909
LF = "\n"

# The 109 MCC codes in this file, rolled into the thirteen headings a card issuer would
# report spend under. Written out code by code rather than as ranges, because this file's
# dictionary does not follow ISO 18245: the standard reserves 3000-3999 for named airlines,
# car-rental firms and hotel chains, and here those codes carry descriptions like "Steelworks"
# and "Welding Repair". A range rule would have filed a steel mill under travel.
#
# "Electronics, digital & valuables" is a deliberate grouping rather than a tidy-up: computers,
# consumer electronics, digital goods, jewellery and antiques are the things that resell, and
# putting them together is what makes the fraud page legible. Splitting them leaves five bars
# too small to read and hides the pattern.
MCC_GROUPS: dict[str, set[int]] = {
    "Groceries & food retail": {5411, 5499},
    "Fuel & motoring": {5541, 5533, 7538, 7531, 7549, 7542},
    "Restaurants & bars": {5812, 5813, 5814},
    "Travel & transport": {4111, 4112, 4121, 4131, 4214, 4411, 4511, 4722, 4784,
                           3722, 3771, 3775, 3730},
    "Utilities & telecom": {4814, 4899, 4900},
    "Money transfer & insurance": {4829, 6300},
    "Retail & department stores": {5300, 5310, 5311, 5211, 5251, 5261,
                                   5621, 5651, 5655, 5661},
    "Speciality retail": {5912, 5921, 5941, 5942, 5947, 5970, 5977, 5192, 5733, 3132},
    "Electronics, digital & valuables": {5045, 5732, 5722, 3684, 3780, 5815, 5816,
                                         5094, 5932},
    "Home, garden & industrial": {1711, 3144, 3174, 3256, 3260, 3504, 3640, 5193, 5712,
                                  5719, 3000, 3001, 3005, 3006, 3007, 3008, 3009, 3058,
                                  3066, 3075, 3359, 3387, 3389, 3390, 3393, 3395, 3405,
                                  3509, 3596},
    "Leisure, lodging & services": {7011, 7210, 7230, 7349, 7393, 7801, 7802, 7832,
                                    7922, 7995, 7996},
    "Health & professional": {8011, 8021, 8041, 8043, 8049, 8062, 8099, 8111, 8931, 7276},
    "Postal & government": {9402},
}

# Every error string in the file, mapped to who or what caused the decline. The multi-error
# rows ("Bad PIN,Insufficient Balance") are folded into Multiple: 745 rows in 13.3M.
ERROR_CLASS = {
    "Insufficient Balance": ("Insufficient balance", "Declined - funds"),
    "Bad PIN": ("Bad PIN", "Declined - cardholder"),
    "Technical Glitch": ("Technical glitch", "Declined - technical"),
    "Bad Card Number": ("Bad card number", "Declined - card data"),
    "Bad Expiration": ("Bad expiry date", "Declined - card data"),
    "Bad CVV": ("Bad CVV", "Declined - card data"),
    "Bad Zipcode": ("Bad postcode", "Declined - card data"),
}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def money(s: pd.Series) -> pd.Series:
    """'$-77.00' -> -77.00. The four money columns all arrive as text with a $ sign."""
    return s.astype("string").str.replace("$", "", regex=False).astype("float64")


MCC_TO_GROUP = {code: name for name, codes in MCC_GROUPS.items() for code in codes}


def mcc_group(code: int) -> str:
    """Every code in the file must be mapped. An "Other" bucket is where findings go to die -
    the first cut of this grouping had one, and it turned out to be the highest-fraud category
    in the book."""
    return MCC_TO_GROUP[int(code)]


def band(value, edges, labels):
    """Right-open bands; returns (label, sort) so Power BI can order them."""
    for i, edge in enumerate(edges):
        if value < edge:
            return labels[i], i
    return labels[-1], len(edges)


def write_csv(df: pd.DataFrame, name: str) -> None:
    path = DATA / name
    df.to_csv(path, index=False, lineterminator=LF)
    print("  %-28s %8d rows  %6.1f MB" % (name, len(df), path.stat().st_size / 1e6))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="folder holding the unzipped Kaggle CSVs")
    args = ap.parse_args()
    src = Path(args.source)
    DATA.mkdir(exist_ok=True)

    q: dict[str, object] = {}

    # ---------------------------------------------------------------- clients
    users = pd.read_csv(src / "users_data.csv")
    for c in ("per_capita_income", "yearly_income", "total_debt"):
        users[c] = money(users[c])

    # 781 of the 2,000 clients in users_data.csv - 39% - never appear in transactions_data.csv
    # at all, and 2,075 of the 6,146 cards never transact. That is the file, not dormancy: it
    # holds a customer master far wider than the transaction extract taken from it. A "spend per
    # customer" computed over the client table rather than over the fact would be 39% low.
    #
    # So the panel is drawn from the clients that do transact. Sampling the master instead gives
    # a dimension where two in five rows can never join to anything, which makes every per-client
    # figure on the report ambiguous for no gain.
    seen_clients: set[int] = set()
    seen_cards: set[int] = set()
    for chunk in pd.read_csv(src / "transactions_data.csv",
                             usecols=["client_id", "card_id"], dtype="int32",
                             chunksize=3_000_000):
        seen_clients |= set(chunk["client_id"].unique().tolist())
        seen_cards |= set(chunk["card_id"].unique().tolist())

    all_ids = set(users["id"].tolist())
    eligible = np.sort(np.fromiter(all_ids & seen_clients, dtype="int64"))
    q["population_clients"] = int(len(users))
    q["population_clients_with_transactions"] = int(len(eligible))
    q["population_clients_never_transacting"] = int(len(all_ids - seen_clients))
    q["population_cards_never_transacting"] = int(
        len(set(pd.read_csv(src / "cards_data.csv", usecols=["id"])["id"]) - seen_cards))

    rng = np.random.default_rng(SEED)
    sample_ids = np.sort(rng.choice(eligible, CLIENT_SAMPLE, replace=False))
    q["sample_clients"] = int(len(sample_ids))

    clients = users[users["id"].isin(sample_ids)].copy()
    # Street address is the only directly identifying column in the file. It is dropped here
    # rather than hidden in the model: a column that never leaves the ETL cannot leak.
    q["dropped_columns_client"] = ["address", "latitude", "longitude"]

    score_bands = ([580, 670, 740, 800],
                   ["Poor (<580)", "Fair (580-669)", "Good (670-739)",
                    "Very good (740-799)", "Exceptional (800+)"])
    income_bands = ([30000, 50000, 75000, 110000],
                    ["Under $30k", "$30k-$50k", "$50k-$75k", "$75k-$110k", "$110k+"])
    age_bands = ([30, 45, 60, 75], ["Under 30", "30-44", "45-59", "60-74", "75+"])

    rows = []
    for r in clients.itertuples():
        sb, sbs = band(r.credit_score, *score_bands)
        ib, ibs = band(r.yearly_income, *income_bands)
        ab, abs_ = band(r.current_age, *age_bands)
        rows.append(dict(
            ClientID=r.id, Gender=r.gender, Age=r.current_age, AgeBand=ab, AgeBandSort=abs_,
            RetirementAge=r.retirement_age, BirthYear=r.birth_year,
            YearlyIncome=r.yearly_income, IncomeBand=ib, IncomeBandSort=ibs,
            PerCapitaIncome=r.per_capita_income, TotalDebt=r.total_debt,
            DebtToIncome=round(r.total_debt / r.yearly_income, 4) if r.yearly_income else None,
            CreditScore=r.credit_score, CreditScoreBand=sb, CreditScoreBandSort=sbs,
            CardsHeld=r.num_credit_cards,
        ))
    dim_client = pd.DataFrame(rows).sort_values("ClientID")

    # ---------------------------------------------------------------- cards
    cards = pd.read_csv(src / "cards_data.csv")
    cards["credit_limit"] = money(cards["credit_limit"])
    q["population_cards"] = int(len(cards))
    q["dark_web_distinct_values"] = sorted(cards["card_on_dark_web"].unique().tolist())

    cards = cards[cards["client_id"].isin(sample_ids)].copy()
    # card_number, cvv and expires are dropped for the same reason as the address, and because
    # a report that shows them is a report that cannot be handed to anyone.
    q["dropped_columns_card"] = ["card_number", "cvv", "expires", "card_on_dark_web"]

    open_dt = pd.to_datetime(cards["acct_open_date"], format="%m/%Y")
    limit_bands = ([1000, 10000, 25000, 50000],
                   ["Under $1k", "$1k-$10k", "$10k-$25k", "$25k-$50k", "$50k+"])
    rows = []
    for r, od in zip(cards.itertuples(), open_dt):
        lb, lbs = band(r.credit_limit, *limit_bands)
        rows.append(dict(
            CardKey=r.id, ClientID=r.client_id, CardBrand=r.card_brand, CardType=r.card_type,
            HasChip="Chip" if r.has_chip == "YES" else "No chip",
            CreditLimit=r.credit_limit, CreditLimitBand=lb, CreditLimitBandSort=lbs,
            AccountOpened=od.date().isoformat(), AccountOpenedYear=od.year,
            CardsIssued=r.num_cards_issued, PinLastChanged=r.year_pin_last_changed,
        ))
    dim_card = pd.DataFrame(rows).sort_values("CardKey")

    # ---------------------------------------------------------------- mcc
    mcc = json.loads((src / "mcc_codes.json").read_text(encoding="utf-8"))
    unmapped = sorted(int(k) for k in mcc if int(k) not in MCC_TO_GROUP)
    if unmapped:
        raise SystemExit(f"MCC codes with no group: {unmapped} - add them to MCC_GROUPS")
    dim_mcc = pd.DataFrame(
        [dict(MCC=int(k), MerchantCategory=v, CategoryGroup=mcc_group(int(k)))
         for k, v in sorted(mcc.items(), key=lambda kv: int(kv[0]))])
    q["mcc_codes"] = int(len(dim_mcc))
    q["mcc_groups"] = int(dim_mcc["CategoryGroup"].nunique())

    # ---------------------------------------------------------------- fraud labels
    labels = json.loads((src / "train_fraud_labels.json").read_text(encoding="utf-8"))["target"]
    fraud_yes = np.fromiter((int(k) for k, v in labels.items() if v == "Yes"),
                            dtype="int64")
    labelled = np.fromiter((int(k) for k in labels), dtype="int64")
    q["population_transactions_labelled"] = int(len(labelled))
    q["population_transactions_fraud"] = int(len(fraud_yes))
    labelled_set = pd.Index(labelled)
    fraud_set = pd.Index(fraud_yes)

    # ---------------------------------------------------------------- transactions
    dtypes = {"id": "int64", "client_id": "int32", "card_id": "int32", "amount": "string",
              "use_chip": "string", "merchant_id": "int32", "merchant_city": "string",
              "merchant_state": "string", "zip": "float64", "mcc": "int32",
              "errors": "string"}
    card_keys = set(dim_card["CardKey"])

    kept, pop_rows = [], 0
    for chunk in pd.read_csv(src / "transactions_data.csv", dtype=dtypes,
                             chunksize=2_000_000):
        pop_rows += len(chunk)
        c = chunk[chunk["card_id"].isin(card_keys)].copy()
        if len(c):
            kept.append(c)
    tx = pd.concat(kept, ignore_index=True)
    del kept
    q["population_transactions"] = int(pop_rows)
    q["sample_transactions"] = int(len(tx))

    # Every sampled client must reach the fact, or the panel is not what the report says it is.
    silent = set(sample_ids.tolist()) - set(tx["client_id"].unique().tolist())
    if silent:
        raise SystemExit(f"{len(silent)} sampled clients have no transactions: {sorted(silent)[:5]}")
    # Cards are a different matter: a client can hold a card they never use, and that is real
    # dormancy rather than a gap in the extract, so the card dimension keeps all of them.
    q["sample_cards_on_file"] = int(len(dim_card))
    q["sample_cards_active"] = int(tx["card_id"].nunique())

    ts = pd.to_datetime(tx["date"])
    tx["DateKey"] = ts.dt.date.astype("string")
    tx["Hour"] = ts.dt.hour.astype("int16")
    tx["Amount"] = money(tx["amount"]).round(2)

    q["date_min"] = str(ts.min())
    q["date_max"] = str(ts.max())
    q["negative_amounts"] = int((tx["Amount"] < 0).sum())
    q["zero_amounts"] = int((tx["Amount"] == 0).sum())

    # Channel. "Swipe Transaction" -> "Swipe", and the order is the story: swipe gives way
    # to chip after the October 2015 US liability shift, with online climbing throughout.
    channel_order = {"Swipe": 1, "Chip": 2, "Online": 3}
    tx["Channel"] = tx["use_chip"].str.replace(" Transaction", "", regex=False)
    dim_channel = pd.DataFrame([
        dict(ChannelKey=k, Channel=n, ChannelSort=k,
             ChannelType="Card present" if n in ("Swipe", "Chip") else "Card not present")
        for n, k in channel_order.items()]).sort_values("ChannelKey")
    chan_key = {n: k for n, k in channel_order.items()}
    tx["ChannelKey"] = tx["Channel"].map(chan_key).astype("int8")

    # Authorisation outcome.
    err = tx["errors"].fillna("")
    q["error_values"] = {k: int(v) for k, v in err.value_counts().items() if k}
    outcomes = [dict(OutcomeKey=0, Outcome="Approved", OutcomeClass="Approved",
                     IsApproved="Yes", OutcomeSort=0)]
    okey = {"": 0}
    for i, (raw, (label, cls)) in enumerate(ERROR_CLASS.items(), start=1):
        okey[raw] = i
        outcomes.append(dict(OutcomeKey=i, Outcome=label, OutcomeClass=cls,
                             IsApproved="No", OutcomeSort=i))
    multi = len(ERROR_CLASS) + 1
    outcomes.append(dict(OutcomeKey=multi, Outcome="Multiple errors",
                         OutcomeClass="Declined - multiple", IsApproved="No",
                         OutcomeSort=multi))
    dim_outcome = pd.DataFrame(outcomes)
    tx["OutcomeKey"] = err.map(okey).fillna(multi).astype("int8")
    q["multi_error_rows"] = int((tx["OutcomeKey"] == multi).sum())

    # Geography. merchant_state holds two-letter US codes AND full country names in one
    # column - and "Georgia" the country sits alongside "GA" the state, so a map fed the raw
    # column plots a Tbilisi coffee to Atlanta.
    #
    # A null is not one thing either. Almost every null is an online transaction, where there
    # is no merchant location to record - but not all of them: a few hundred card-present
    # travel-agency rows are blank too. Those get their own member rather than being quietly
    # relabelled Online, which would have moved card-present spend into the online channel.
    st = tx["merchant_state"]
    is_us = st.notna() & (st.str.len() == 2)
    geo_label = pd.Series(index=tx.index, dtype="object")
    geo_label[is_us] = st[is_us]
    geo_label[st.notna() & ~is_us] = st[st.notna() & ~is_us]
    null_online = st.isna() & (tx["Channel"] == "Online")
    geo_label[null_online] = "Online"
    geo_label[st.isna() & ~null_online] = "Not recorded"
    q["state_null_rows"] = int(st.isna().sum())
    q["state_null_and_online"] = int(null_online.sum())
    q["state_null_not_online"] = int((st.isna() & ~null_online).sum())
    orphan = tx[st.isna() & ~null_online]
    q["state_null_not_online_detail"] = dict(
        channels=orphan["Channel"].value_counts().to_dict(),
        mccs={str(k): int(v) for k, v in orphan["mcc"].value_counts().head(5).items()})

    geos = sorted(set(geo_label))
    geo_rows = []
    for i, g in enumerate(geos, start=1):
        if g == "Online":
            geo_rows.append(dict(GeoKey=i, Location="Online", LocationType="Online",
                                 StateCode="", Country="United States"))
        elif g == "Not recorded":
            geo_rows.append(dict(GeoKey=i, Location="Not recorded",
                                 LocationType="Not recorded", StateCode="", Country=""))
        elif g in US_STATES:
            geo_rows.append(dict(GeoKey=i, Location=US_STATES[g], LocationType="US state",
                                 StateCode=g, Country="United States"))
        else:
            geo_rows.append(dict(GeoKey=i, Location=g, LocationType="International",
                                 StateCode="", Country=g))
    dim_geography = pd.DataFrame(geo_rows)
    gkey = {g: i for i, g in enumerate(geos, start=1)}
    tx["GeoKey"] = geo_label.map(gkey).astype("int16")

    # Fraud status. Only 67% of rows carry a label, so "not fraud" and "not labelled" are
    # different answers and the model keeps them apart.
    dim_fraud = pd.DataFrame([
        dict(FraudKey=0, FraudStatus="Not labelled", IsLabelled="No", IsFraud="No",
             FraudSort=2),
        dict(FraudKey=1, FraudStatus="Confirmed not fraud", IsLabelled="Yes", IsFraud="No",
             FraudSort=1),
        dict(FraudKey=2, FraudStatus="Confirmed fraud", IsLabelled="Yes", IsFraud="Yes",
             FraudSort=0),
    ])
    ids = pd.Index(tx["id"])
    tx["FraudKey"] = np.where(ids.isin(fraud_set), 2,
                              np.where(ids.isin(labelled_set), 1, 0)).astype("int8")
    q["sample_labelled"] = int((tx["FraudKey"] > 0).sum())
    q["sample_fraud"] = int((tx["FraudKey"] == 2).sum())

    # ---------------------------------------------------------------- date
    days = pd.date_range(ts.min().normalize(), ts.max().normalize(), freq="D")
    last = ts.max()
    dim_date = pd.DataFrame(dict(Date=days))
    d = dim_date["Date"]
    dim_date["Year"] = d.dt.year
    dim_date["Quarter"] = "Q" + d.dt.quarter.astype(str)
    dim_date["QuarterYear"] = "Q" + d.dt.quarter.astype(str) + " " + d.dt.year.astype(str)
    dim_date["QuarterYearSort"] = d.dt.year * 10 + d.dt.quarter
    dim_date["MonthNumber"] = d.dt.month
    dim_date["MonthName"] = d.dt.strftime("%B")
    dim_date["MonthShort"] = d.dt.strftime("%b")
    dim_date["MonthYear"] = d.dt.strftime("%b %Y")
    dim_date["MonthYearSort"] = d.dt.year * 100 + d.dt.month
    dim_date["MonthStart"] = d.dt.to_period("M").dt.start_time.dt.date
    dim_date["DayOfWeek"] = d.dt.dayofweek + 1
    dim_date["DayName"] = d.dt.strftime("%A")
    dim_date["IsWeekend"] = np.where(d.dt.dayofweek >= 5, "Weekend", "Weekday")
    # The file stops on 31 October 2019, so 2019 is ten months. Any year-on-year chart that
    # ignores that shows a collapse in 2019 that is purely the calendar. This flag is what
    # the like-for-like measures filter on.
    dim_date["InLikeForLikePeriod"] = np.where(d.dt.month <= last.month, "Yes", "No")
    dim_date["IsCompleteYear"] = np.where(d.dt.year < last.year, "Yes", "No")
    dim_date["Date"] = d.dt.date
    q["partial_final_year"] = dict(year=int(last.year), months=int(last.month))

    # ---------------------------------------------------------------- write
    print("data/")
    write_csv(dim_date, "dim_date.csv")
    write_csv(dim_client, "dim_client.csv")
    write_csv(dim_card, "dim_card.csv")
    write_csv(dim_mcc, "dim_merchant_category.csv")
    write_csv(dim_channel, "dim_channel.csv")
    write_csv(dim_outcome, "dim_outcome.csv")
    write_csv(dim_geography, "dim_geography.csv")
    write_csv(dim_fraud, "dim_fraud_status.csv")

    # Client sits on the fact as well as on the card. Cards do belong to clients, so a
    # snowflake would model it - but then "active clients" cannot be counted from the fact
    # and every client filter travels two hops. One extra integer column buys a flat star.
    fact = tx[["DateKey", "Hour", "client_id", "card_id", "mcc", "ChannelKey", "OutcomeKey",
               "GeoKey", "FraudKey", "Amount"]].rename(
        columns={"DateKey": "Date", "client_id": "ClientKey", "card_id": "CardKey",
                 "mcc": "MCC"})
    fact = fact.sort_values(["Date", "Hour", "CardKey"]).reset_index(drop=True)

    years = sorted(pd.to_datetime(fact["Date"]).dt.year.unique())
    yr = pd.to_datetime(fact["Date"]).dt.year
    for y in years:
        write_csv(fact[yr == y], f"fact_transaction_{y}.csv")
    q["fact_years"] = [int(y) for y in years]
    q["fact_rows"] = int(len(fact))

    # A few figures the report and the write-up both quote, computed here so the two cannot
    # drift apart from each other or from the data.
    approved = fact["OutcomeKey"] == 0
    q["sample_approved"] = int(approved.sum())
    q["sample_declined"] = int((~approved).sum())
    q["decline_rate"] = round(float((~approved).mean()), 6)
    q["spend_approved"] = round(float(fact.loc[approved, "Amount"].sum()), 2)
    q["spend_all_rows"] = round(float(fact["Amount"].sum()), 2)
    q["spend_overstatement_if_declines_counted"] = round(
        float(fact["Amount"].sum() - fact.loc[approved, "Amount"].sum()), 2)
    ch = fact.merge(dim_channel[["ChannelKey", "Channel"]], on="ChannelKey")
    ch["Year"] = pd.to_datetime(ch["Date"]).dt.year
    counts = ch.groupby(["Year", "Channel"]).size().unstack(fill_value=0)
    q["channel_mix_by_year"] = {
        str(y): {c: round(float(v), 4) for c, v in row.items()}
        for y, row in counts.div(counts.sum(axis=1), axis=0).round(4).iterrows()}

    # Chip does not phase in, it is switched on. Recorded here because it decides how the
    # channel page is allowed to be captioned.
    ch["Month"] = ch["Date"].astype("string").str.slice(0, 7)
    mth = ch.groupby(["Month", "Channel"]).size().unstack(fill_value=0)
    mth = mth.div(mth.sum(axis=1), axis=0)
    chip = mth.get("Chip")
    q["chip_switch"] = dict(
        first_chip_month=str(chip[chip > 0].index.min()),
        last_month_without_chip=str(chip[chip == 0].index.max()),
        share_month_before=round(float(chip[chip == 0].iloc[-1]), 4),
        share_first_month=round(float(chip[chip > 0].iloc[0]), 4),
        chip_rows_on_cards_without_a_chip=int(
            fact.merge(dim_card[["CardKey", "HasChip"]], on="CardKey")
                .query("ChannelKey == 2 and HasChip == 'No chip'").shape[0]))

    # The state column, measured on the whole 13.3M-row source rather than the sample, because
    # this is a property of the file and the write-up quotes it as one.
    src_state = pd.read_csv(src / "transactions_data.csv", usecols=["merchant_state"],
                            dtype={"merchant_state": "string"})["merchant_state"]
    vc = src_state.value_counts()
    q["source_state_column"] = dict(
        distinct=int(src_state.nunique()),
        country_names=int(sum(1 for i in vc.index if len(str(i)) != 2)),
        null_rows=int(src_state.isna().sum()),
        georgia_the_country=int(vc.get("Georgia", 0)),
        ga_the_us_state=int(vc.get("GA", 0)))

    (DATA / "quality_report.json").write_text(
        json.dumps(q, indent=2) + "\n", encoding="utf-8", newline=LF)
    print("\ndata/quality_report.json written")
    for k in ("population_transactions", "sample_transactions", "sample_clients",
              "sample_labelled", "sample_fraud", "decline_rate", "spend_approved"):
        print("  %-34s %s" % (k, q[k]))


if __name__ == "__main__":
    main()
