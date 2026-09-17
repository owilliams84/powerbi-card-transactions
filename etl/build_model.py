"""Generate the TMDL semantic model - tables, relationships, measures.

TMDL is indentation-sensitive (tabs) and forbids blank lines inside an object, and every object
needs a stable lineageTag. This owns the format and the tags (uuid5 of the object's path, so
re-running never churns them), and the table definitions below read as a schema.

    python etl/build_model.py            # partitions read the CSVs from GitHub over HTTPS
    python etl/build_model.py --local    # partitions read data/ on this machine (offline)

The fact is split one file per year and loaded as one partition per year, so a refresh pulls
ten 4 MB files rather than one 43 MB one, and a single year can be reloaded on its own.

Rewrites <model>/definition/ from scratch every run.
"""

from __future__ import annotations

import argparse
import shutil
import time
import uuid
from pathlib import Path

import milestone_calendar
from calendar_config import CALENDAR

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "Card Transactions.SemanticModel"
DEFN = MODEL / "definition"
DATA = ROOT / "data"

RAW = "https://raw.githubusercontent.com/owilliams84/powerbi-card-transactions/main/data/"
NS = uuid.UUID("9f4e2c71-6a8b-4d15-b3e0-7c9a5f2d8b46")
YEARS = list(range(2010, 2020))


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NS, "cardtx:" + ":".join(parts)))


def q(name: str) -> str:
    """Quote a TMDL identifier when it needs it."""
    return name if name.replace("_", "").isalnum() else f"'{name}'"


def doc(text: str | None, indent: int) -> list[str]:
    if not text:
        return []
    pad = "\t" * indent
    return [f"{pad}/// {para}".rstrip() for para in text.strip("\n").split("\n")]


def col(name, source, dtype, **o):
    return dict(name=name, source=source, dtype=dtype, **o)


INT_T, TXT_T, NUM_T, DATE_T = "Int64.Type", "type text", "type number", "type date"

TABLES = {
    "Date": dict(
        file="dim_date.csv", date_table=True,
        doc="One row per day from 1 January 2010 to 31 October 2019, contiguous, and marked as\n"
            "the date table so DATEADD and TOTALYTD have a calendar to walk.\n"
            "\n"
            "'In Like For Like Period' is the column that keeps 2019 honest. The file stops on\n"
            "31 October, so 2019 holds ten months. A year-on-year chart that ignores that shows\n"
            "a 17% collapse in 2019 which is entirely the calendar; the like-for-like measures\n"
            "filter on this column and compare January to October against January to October.",
        columns=[
            col("Date", "Date", "dateTime", key=True, format="d mmm yyyy"),
            col("Year", "Year", "int64", format="0"),
            col("Quarter", "Quarter", "string"),
            col("Quarter Year", "QuarterYear", "string", sortBy="Quarter Year Sort"),
            col("Quarter Year Sort", "QuarterYearSort", "int64", hidden=True, format="0"),
            col("Month No", "MonthNumber", "int64", hidden=True, format="0"),
            col("Month", "MonthName", "string", sortBy="Month No"),
            col("Month Short", "MonthShort", "string", sortBy="Month No"),
            col("Month Year", "MonthYear", "string", sortBy="Month Year Sort"),
            col("Month Year Sort", "MonthYearSort", "int64", hidden=True, format="0"),
            col("Month Start", "MonthStart", "dateTime", format="mmm yyyy",
                doc="First day of the month. Visible because the ten-year trend charts put it\n"
                    "on a continuous axis - 118 month-year strings would not fit."),
            col("Day of Week", "DayOfWeek", "int64", hidden=True, format="0"),
            col("Day", "DayName", "string", sortBy="Day of Week"),
            col("Is Weekend", "IsWeekend", "string"),
            col("In Like For Like Period", "InLikeForLikePeriod", "string",
                doc="Yes for January to October, the months every year in the file has."),
            col("Is Complete Year", "IsCompleteYear", "string",
                doc="No for 2019, which stops on 31 October."),
        ],
        types={"Date": DATE_T, "Year": INT_T, "Quarter": TXT_T, "QuarterYear": TXT_T,
               "QuarterYearSort": INT_T, "MonthNumber": INT_T, "MonthName": TXT_T,
               "MonthShort": TXT_T, "MonthYear": TXT_T, "MonthYearSort": INT_T,
               "MonthStart": DATE_T, "DayOfWeek": INT_T, "DayName": TXT_T,
               "IsWeekend": TXT_T, "InLikeForLikePeriod": TXT_T, "IsCompleteYear": TXT_T},
    ),
    "Client": dict(
        file="dim_client.csv",
        doc="One row per cardholder in the sampled panel: 160 of the file's 2,000 clients.\n"
            "\n"
            "Street address, latitude and longitude are dropped in the ETL rather than hidden\n"
            "here. A column that never leaves the extract cannot leak out of a report, and none\n"
            "of the three answers a question this report asks.\n"
            "\n"
            "Age, income and credit score are as at the file's build date, not as at the\n"
            "transaction - so they band a client for the whole ten years, and a 2010 figure\n"
            "sliced by 'Under 30' means 'people who are under 30 now', not 'were then'.",
        columns=[
            col("Client", "ClientID", "int64", key=True, format="0",
                doc="The client's id. There are no names in this file, which is the point."),
            col("Gender", "Gender", "string"),
            col("Age", "Age", "int64", format="0"),
            col("Age Band", "AgeBand", "string", sortBy="Age Band Sort"),
            col("Age Band Sort", "AgeBandSort", "int64", hidden=True, format="0"),
            col("Birth Year", "BirthYear", "int64", hidden=True, format="0"),
            col("Retirement Age", "RetirementAge", "int64", hidden=True, format="0"),
            col("Yearly Income", "YearlyIncome", "double", format="\\$#,0"),
            col("Income Band", "IncomeBand", "string", sortBy="Income Band Sort"),
            col("Income Band Sort", "IncomeBandSort", "int64", hidden=True, format="0"),
            col("Per Capita Income", "PerCapitaIncome", "double", hidden=True, format="\\$#,0"),
            col("Total Debt", "TotalDebt", "double", format="\\$#,0"),
            col("Debt to Income", "DebtToIncome", "double", format="0.00"),
            col("Credit Score", "CreditScore", "int64", format="0",
                doc="FICO, 480 to 850 in this file."),
            col("Credit Score Band", "CreditScoreBand", "string",
                sortBy="Credit Score Band Sort"),
            col("Credit Score Band Sort", "CreditScoreBandSort", "int64", hidden=True,
                format="0"),
            col("Cards Held", "CardsHeld", "int64", format="0"),
        ],
        types={"ClientID": INT_T, "Gender": TXT_T, "Age": INT_T, "AgeBand": TXT_T,
               "AgeBandSort": INT_T, "RetirementAge": INT_T, "BirthYear": INT_T,
               "YearlyIncome": NUM_T, "IncomeBand": TXT_T, "IncomeBandSort": INT_T,
               "PerCapitaIncome": NUM_T, "TotalDebt": NUM_T, "DebtToIncome": NUM_T,
               "CreditScore": INT_T, "CreditScoreBand": TXT_T,
               "CreditScoreBandSort": INT_T, "CardsHeld": INT_T},
    ),
    "Card": dict(
        file="dim_card.csv",
        doc="One row per card held by a client in the panel - 490 cards across 160 clients,\n"
            "three each on average and up to nine.\n"
            "\n"
            "Card number, CVV and expiry date are dropped in the ETL. They are the columns that\n"
            "would make this extract a PCI problem rather than a dataset, and a report that\n"
            "shows them is a report nobody can hand to anyone.\n"
            "\n"
            "'Card On Dark Web' is dropped too, for the opposite reason: it holds the single\n"
            "value 'No' for all 6,146 cards in the source, so it can only ever draw one bar.",
        columns=[
            col("Card", "CardKey", "int64", key=True, format="0",
                doc="The card's id, with nothing else on it."),
            col("Client Key", "ClientID", "int64", hidden=True, format="0"),
            col("Card Brand", "CardBrand", "string",
                doc="Mastercard, Visa, Amex or Discover."),
            col("Card Type", "CardType", "string",
                doc="Credit, Debit or Debit (Prepaid)."),
            col("Chip", "HasChip", "string",
                doc="Whether the card carries an EMV chip. Every chip transaction in the file\n"
                    "comes from a card marked here as having one, which is the one internal\n"
                    "consistency check the channel data does pass."),
            col("Credit Limit", "CreditLimit", "double", format="\\$#,0"),
            col("Credit Limit Band", "CreditLimitBand", "string",
                sortBy="Credit Limit Band Sort"),
            col("Credit Limit Band Sort", "CreditLimitBandSort", "int64", hidden=True,
                format="0"),
            col("Account Opened", "AccountOpened", "dateTime", format="mmm yyyy"),
            col("Account Opened Year", "AccountOpenedYear", "int64", format="0"),
            col("Cards Issued", "CardsIssued", "int64", hidden=True, format="0"),
            col("PIN Last Changed", "PinLastChanged", "int64", format="0",
                doc="The year the PIN was last changed - a card-security hygiene measure, and\n"
                    "the only one in the file."),
        ],
        types={"CardKey": INT_T, "ClientID": INT_T, "CardBrand": TXT_T, "CardType": TXT_T,
               "HasChip": TXT_T, "CreditLimit": NUM_T, "CreditLimitBand": TXT_T,
               "CreditLimitBandSort": INT_T, "AccountOpened": DATE_T,
               "AccountOpenedYear": INT_T, "CardsIssued": INT_T, "PinLastChanged": INT_T},
    ),
    "Merchant Category": dict(
        file="dim_merchant_category.csv",
        doc="The 109 merchant category codes used in this file, with the ISO description and a\n"
            "grouping into the eleven headings a card issuer would report spend under. The\n"
            "grouping is mine; the codes and descriptions are the source's.",
        columns=[
            col("MCC", "MCC", "int64", key=True, format="0"),
            col("Merchant Category", "MerchantCategory", "string"),
            col("Category Group", "CategoryGroup", "string"),
        ],
        types={"MCC": INT_T, "MerchantCategory": TXT_T, "CategoryGroup": TXT_T},
    ),
    "Channel": dict(
        file="dim_channel.csv",
        doc="How the card was presented: swiped, dipped into a chip reader, or keyed online.\n"
            "'Card Type' here is the acquirer's sense of the phrase - card present or card not\n"
            "present - which is the split that matters for fraud, not the credit/debit one.",
        columns=[
            col("Channel Key", "ChannelKey", "int64", key=True, hidden=True, format="0"),
            col("Channel", "Channel", "string", sortBy="Channel Sort"),
            col("Channel Sort", "ChannelSort", "int64", hidden=True, format="0"),
            col("Presentment", "ChannelType", "string",
                doc="Card present (swipe, chip) or card not present (online)."),
        ],
        types={"ChannelKey": INT_T, "Channel": TXT_T, "ChannelSort": INT_T,
               "ChannelType": TXT_T},
    ),
    "Outcome": dict(
        file="dim_outcome.csv",
        doc="Whether the transaction was authorised, and if not, why not. The source carries\n"
            "this as a free-text 'errors' column which is null on an approval and can hold two\n"
            "reasons at once ('Bad PIN,Insufficient Balance'); the 745 double-reason rows in\n"
            "the source become 'Multiple errors' rather than being split and double counted.\n"
            "\n"
            "'Is Approved' is what the Spend measure filters on, and it matters: the Amount\n"
            "column is the amount that was *attempted*, and it is populated on declined rows\n"
            "too. Summing it whole overstates spend by $929,084 over the ten years.",
        columns=[
            col("Outcome Key", "OutcomeKey", "int64", key=True, hidden=True, format="0"),
            col("Outcome", "Outcome", "string", sortBy="Outcome Sort"),
            col("Outcome Sort", "OutcomeSort", "int64", hidden=True, format="0"),
            col("Outcome Class", "OutcomeClass", "string",
                doc="Who or what caused it: funds, cardholder, card data, technical."),
            col("Is Approved", "IsApproved", "string"),
        ],
        types={"OutcomeKey": INT_T, "Outcome": TXT_T, "OutcomeSort": INT_T,
               "OutcomeClass": TXT_T, "IsApproved": TXT_T},
    ),
    "Geography": dict(
        file="dim_geography.csv",
        doc="Where the merchant was. The source keeps this in one column that mixes two-letter\n"
            "US state codes with full country names - 199 distinct values, 147 of them country\n"
            "names - so 'Georgia' the country (34 rows) sits next to 'GA' the state (368,206).\n"
            "A map fed the raw column plots one onto the other.\n"
            "\n"
            "A blank is not one thing either. Almost every blank is an online transaction with\n"
            "no merchant location to record, but 497 card-present travel-agency rows are blank\n"
            "too; those are 'Not recorded' rather than being relabelled Online, which would\n"
            "have quietly moved card-present spend into the online channel.",
        columns=[
            col("Geo Key", "GeoKey", "int64", key=True, hidden=True, format="0"),
            col("Location", "Location", "string"),
            col("Location Type", "LocationType", "string",
                doc="US state, International, Online or Not recorded."),
            col("State Code", "StateCode", "string", hidden=True),
            col("Country", "Country", "string"),
        ],
        types={"GeoKey": INT_T, "Location": TXT_T, "LocationType": TXT_T,
               "StateCode": TXT_T, "Country": TXT_T},
    ),
    "Fraud Status": dict(
        file="dim_fraud_status.csv",
        doc="Confirmed fraud, confirmed not fraud, or not labelled - three states, not two.\n"
            "\n"
            "The source's fraud labels cover 67% of transactions and no more; the rest are\n"
            "simply unlabelled. Treating an unlabelled row as clean is the single easiest way\n"
            "to get this dataset wrong, and it understates the fraud rate by a third. Every\n"
            "fraud measure in this model divides by labelled rows only, and the report shows\n"
            "the naive figure beside it so the gap is visible rather than assumed away.",
        columns=[
            col("Fraud Key", "FraudKey", "int64", key=True, hidden=True, format="0"),
            col("Fraud Status", "FraudStatus", "string", sortBy="Fraud Sort"),
            col("Fraud Sort", "FraudSort", "int64", hidden=True, format="0"),
            col("Is Labelled", "IsLabelled", "string"),
            col("Is Fraud", "IsFraud", "string"),
        ],
        types={"FraudKey": INT_T, "FraudStatus": TXT_T, "FraudSort": INT_T,
               "IsLabelled": TXT_T, "IsFraud": TXT_T},
    ),
    "Transactions": dict(
        files=[f"fact_transaction_{y}.csv" for y in YEARS],
        partition_names=[str(y) for y in YEARS],
        doc="One row per transaction attempt - 1,017,087 of them across ten years, for the 160\n"
            "sampled clients. Loaded as ten partitions, one per year.\n"
            "\n"
            "Amount is signed and is the amount attempted. Negative rows are refunds and\n"
            "reversals - 4.9% of the file, and heavily concentrated in fuel and grocery, which\n"
            "is what a pre-authorisation reversal looks like. 269 rows are exactly zero and\n"
            "every one of them is a money transfer (MCC 4829).",
        columns=[
            col("Date", "Date", "dateTime", hidden=True, format="yyyy-mm-dd"),
            col("Hour", "Hour", "int64", format="0",
                doc="Hour of the day, 0 to 23, from the transaction timestamp."),
            col("Client Key", "ClientKey", "int64", hidden=True, format="0"),
            col("Card Key", "CardKey", "int64", hidden=True, format="0"),
            col("MCC", "MCC", "int64", hidden=True, format="0"),
            col("Channel Key", "ChannelKey", "int64", hidden=True, format="0"),
            col("Outcome Key", "OutcomeKey", "int64", hidden=True, format="0"),
            col("Geo Key", "GeoKey", "int64", hidden=True, format="0"),
            col("Fraud Key", "FraudKey", "int64", hidden=True, format="0"),
            col("Amount", "Amount", "double", hidden=True, format="#,0.00"),
        ],
        types={"Date": DATE_T, "Hour": INT_T, "ClientKey": INT_T, "CardKey": INT_T,
               "MCC": INT_T, "ChannelKey": INT_T, "OutcomeKey": INT_T, "GeoKey": INT_T,
               "FraudKey": INT_T, "Amount": NUM_T},
    ),
}

RELATIONSHIPS = [
    ("Date to Transactions", "Transactions.Date", "Date.Date"),
    ("Client to Transactions", "Transactions.'Client Key'", "Client.Client"),
    ("Card to Transactions", "Transactions.'Card Key'", "Card.Card"),
    ("Category to Transactions", "Transactions.MCC", "'Merchant Category'.MCC"),
    ("Channel to Transactions", "Transactions.'Channel Key'", "Channel.'Channel Key'"),
    ("Outcome to Transactions", "Transactions.'Outcome Key'", "Outcome.'Outcome Key'"),
    ("Geography to Transactions", "Transactions.'Geo Key'", "Geography.'Geo Key'"),
    ("Fraud to Transactions", "Transactions.'Fraud Key'", "'Fraud Status'.'Fraud Key'"),
]

USD, USD2, INT, PCT, PCT2, DEC1 = "\\$#,0", "\\$#,0.00", "#,0", "0.0%", "0.00%", "0.0"

APPROVED = "KEEPFILTERS('Outcome'[Is Approved] = \"Yes\")"

MEASURES = [
    # ---- volume and value
    ("Transactions", "COUNTROWS('Transactions')", INT,
     "Every attempt, approved or not. The denominator for a decline rate, never for spend."),
    ("Approved Transactions", f"CALCULATE([Transactions], {APPROVED})", INT, None),
    ("Declined Transactions",
     "CALCULATE([Transactions], KEEPFILTERS('Outcome'[Is Approved] = \"No\"))", INT, None),
    ("Decline Rate %", "DIVIDE([Declined Transactions], [Transactions])", PCT2,
     "Declines as a share of attempts. 1.50% over the ten years."),
    ("Spend", f"CALCULATE(SUM('Transactions'[Amount]), {APPROVED})", USD,
     "Settled value: approved rows only, refunds netted off. This is the money figure."),
    ("Attempted Value", "SUM('Transactions'[Amount])", USD,
     "Every row including the declined ones - what a naive SUM of Amount returns. It is\n"
     "$929,084 above Spend over the period, and the gap is not an error in the data, it is\n"
     "the money the bank declined to move."),
    ("Purchase Value", f"CALCULATE(SUM('Transactions'[Amount]), {APPROVED}, 'Transactions'[Amount] > 0)",
     USD, None),
    ("Refunds", f"CALCULATE([Transactions], {APPROVED}, 'Transactions'[Amount] < 0)", INT, None),
    ("Refund Value",
     f"CALCULATE(SUM('Transactions'[Amount]), {APPROVED}, 'Transactions'[Amount] < 0)", USD,
     None),
    ("Refund Rate %", "DIVIDE([Refunds], [Approved Transactions])", PCT,
     "4.9% of approved transactions are negative. Fuel and grocery account for most of them,\n"
     "which is the shape of a pre-authorisation being released rather than a returned jumper."),
    ("Average Transaction",
     "DIVIDE(\n"
     "    [Purchase Value],\n"
     f"    CALCULATE([Transactions], {APPROVED}, 'Transactions'[Amount] > 0)\n"
     ")", USD2,
     "Purchases only. Averaging the refunds in drags it below anything a cardholder would\n"
     "recognise as their typical spend."),

    # ---- cards and clients
    ("Active Cards", "DISTINCTCOUNT('Transactions'[Card Key])", INT,
     "Cards with at least one attempt in the period."),
    ("Active Clients", "DISTINCTCOUNT('Transactions'[Client Key])", INT, None),
    ("Transactions per Card", "DIVIDE([Approved Transactions], [Active Cards])", DEC1, None),
    ("Spend per Card", "DIVIDE([Spend], [Active Cards])", USD, None),
    ("Spend per Client", "DIVIDE([Spend], [Active Clients])", USD, None),
    ("Total Credit Limit",
     "CALCULATE(\n"
     "    SUM('Card'[Credit Limit]),\n"
     "    CROSSFILTER('Card'[Card], 'Transactions'[Card Key], BOTH)\n"
     ")", USD,
     "The combined limit of the cards in view. CROSSFILTER is doing real work here: filters\n"
     "run from the one side to the many, so a credit-score band selected on Client reaches the\n"
     "fact and stops. A plain SUM over Card returned the whole book's $10.3m on every row of\n"
     "the band table, identical five times over, and made the ratio below read 317% for one\n"
     "band. Turning the join on for this measure lets the filtered fact pick the cards back."),
    ("Years in Period", "DIVIDE(COUNTROWS('Date'), 365.25)", "0.0",
     "The length of the visible period in years, so a rate can be annualised without\n"
     "hard-coding ten."),
    ("Annual Spend to Limit",
     "DIVIDE(\n"
     "    DIVIDE([Spend], [Years in Period]),\n"
     "    [Total Credit Limit]\n"
     ")", PCT,
     "Spend per year against the cards' combined limit - how hard the book turns its limit\n"
     "over. Annualised on purpose: the raw ten-year ratio reads 711%, which is arithmetically\n"
     "fine and useless on a card. Not utilisation in the credit sense either, because the\n"
     "file carries no balances."),

    # ---- fraud
    ("Labelled Transactions",
     "CALCULATE([Transactions], KEEPFILTERS('Fraud Status'[Is Labelled] = \"Yes\"))", INT,
     "Rows the source actually adjudicated. Two thirds of the file."),
    ("Label Coverage %", "DIVIDE([Labelled Transactions], [Transactions])", PCT,
     "67.0%, and flat in every year - so the unlabelled rows are a sampling decision by\n"
     "whoever built the file, not a gap that grows or a period that was never reviewed."),
    ("Fraud Transactions",
     "CALCULATE([Transactions], KEEPFILTERS('Fraud Status'[Is Fraud] = \"Yes\"))", INT, None),
    ("Fraud Rate per 10k",
     "DIVIDE([Fraud Transactions], [Labelled Transactions]) * 10000", DEC1,
     "Confirmed fraud per ten thousand *adjudicated* transactions. The correct denominator:\n"
     "an unlabelled row is not a clean row."),
    ("Fraud Rate per 10k (all rows)",
     "DIVIDE([Fraud Transactions], [Transactions]) * 10000", DEC1,
     "The same number over every row, which is what you get by joining the labels and\n"
     "treating the misses as clean. It reads a third lower. Kept in the model so the report\n"
     "can show the two side by side instead of asserting that one of them is wrong."),
    ("Fraud Value",
     "CALCULATE(SUM('Transactions'[Amount]), KEEPFILTERS('Fraud Status'[Is Fraud] = \"Yes\"))",
     USD, None),
    ("Fraud Value Share",
     "DIVIDE(\n"
     "    [Fraud Value],\n"
     "    CALCULATE(\n"
     "        SUM('Transactions'[Amount]),\n"
     "        KEEPFILTERS('Fraud Status'[Is Labelled] = \"Yes\")\n"
     "    )\n"
     ")", PCT2,
     "Fraudulent value as a share of adjudicated value - the loss rate, on the same\n"
     "denominator as the count-based one."),
    ("Cards Hit by Fraud",
     "CALCULATE([Active Cards], KEEPFILTERS('Fraud Status'[Is Fraud] = \"Yes\"))", INT, None),
    ("Average Fraud Transaction",
     "DIVIDE(\n"
     "    [Fraud Value],\n"
     "    [Fraud Transactions]\n"
     ")", USD2, None),

    # ---- channel
    ("Channel Share %",
     "DIVIDE([Transactions], CALCULATE([Transactions], REMOVEFILTERS('Channel')))", PCT,
     "This channel's share of attempts in the same period - the measure behind the stacked\n"
     "channel chart."),
    ("Chip Share %",
     "DIVIDE(\n"
     "    CALCULATE([Transactions], KEEPFILTERS('Channel'[Channel] = \"Chip\")),\n"
     "    [Transactions]\n"
     ")", PCT, None),
    ("Online Share %",
     "DIVIDE(\n"
     "    CALCULATE([Transactions], KEEPFILTERS('Channel'[Channel] = \"Online\")),\n"
     "    [Transactions]\n"
     ")", PCT, None),
    ("Card Not Present Share %",
     "DIVIDE(\n"
     "    CALCULATE([Transactions], KEEPFILTERS('Channel'[Presentment] = \"Card not present\")),\n"
     "    [Transactions]\n"
     ")", PCT, None),

    # ---- authorisation quality
    ("Decline Share of Class",
     "DIVIDE([Declined Transactions], CALCULATE([Declined Transactions], REMOVEFILTERS('Outcome')))",
     PCT,
     "This reason's share of all declines in the period."),
    ("Technical Decline Rate %",
     "DIVIDE(\n"
     "    CALCULATE([Transactions], KEEPFILTERS('Outcome'[Outcome Class] = \"Declined - technical\")),\n"
     "    [Transactions]\n"
     ")", PCT2,
     "The share of attempts lost to the bank's own plumbing rather than to the cardholder -\n"
     "the only line in the decline mix that is entirely the issuer's to fix."),
    ("Declined Value",
     "CALCULATE(\n"
     "    SUM('Transactions'[Amount]),\n"
     "    KEEPFILTERS('Outcome'[Is Approved] = \"No\")\n"
     ")", USD, None),

    # ---- time
    ("Spend PY", "CALCULATE([Spend], DATEADD('Date'[Date], -1, YEAR))", USD,
     "Needs the marked date table; without it DATEADD returns blank everywhere."),
    ("Spend YoY %", "DIVIDE([Spend] - [Spend PY], [Spend PY])", PCT,
     "Straight year on year, and wrong for 2019 - the file stops in October, so this reads\n"
     "-17% for a year that is simply two months short. Use the like-for-like pair below."),
    ("Spend LFL",
     "CALCULATE([Spend], KEEPFILTERS('Date'[In Like For Like Period] = \"Yes\"))", USD,
     "January to October only, in every year - the window all ten years share."),
    ("Spend LFL PY", "CALCULATE([Spend LFL], DATEADD('Date'[Date], -1, YEAR))", USD, None),
    ("Spend LFL YoY %", "DIVIDE([Spend LFL] - [Spend LFL PY], [Spend LFL PY])", PCT,
     "The honest year-on-year: ten months against the same ten months."),
    ("Transactions LFL",
     "CALCULATE([Approved Transactions], KEEPFILTERS('Date'[In Like For Like Period] = \"Yes\"))",
     INT, None),
    ("Transactions LFL PY",
     "CALCULATE([Transactions LFL], DATEADD('Date'[Date], -1, YEAR))", INT, None),
    ("Transactions LFL YoY %",
     "DIVIDE([Transactions LFL] - [Transactions LFL PY], [Transactions LFL PY])", PCT, None),
    ("Spend 12M",
     "CALCULATE([Spend], DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -12, MONTH))", USD,
     None),

    # ---- ranking and share
    ("Spend Share",
     "DIVIDE(\n"
     "    [Spend],\n"
     "    CALCULATE(\n"
     "        [Spend],\n"
     "        REMOVEFILTERS('Merchant Category'), REMOVEFILTERS('Geography'),\n"
     "        REMOVEFILTERS('Card'), REMOVEFILTERS('Client'), REMOVEFILTERS('Channel')\n"
     "    )\n"
     ")", PCT,
     "This row's share of all spend in the same period, with every non-date filter cleared -\n"
     "so a category's share is of the whole book, not of whatever else is on the page."),
    ("Category Rank",
     "RANKX(ALLSELECTED('Merchant Category'[Merchant Category]), [Spend], , DESC, Dense)",
     "0", None),
    ("Location Rank", "RANKX(ALLSELECTED('Geography'[Location]), [Spend], , DESC, Dense)",
     "0", None),

    # ---- labels
    ("Report Period",
     "VAR First = MIN('Date'[Date])\n"
     "VAR Last = MAX('Date'[Date])\n"
     "VAR WholeYears = MONTH(First) = 1 && DAY(First) = 1 && MONTH(Last) = 12 && DAY(Last) = 31\n"
     "RETURN\n"
     "    SWITCH(\n"
     "        TRUE(),\n"
     "        WholeYears && YEAR(First) = YEAR(Last), FORMAT(Last, \"yyyy\"),\n"
     "        WholeYears, FORMAT(First, \"yyyy\") & \" to \" & FORMAT(Last, \"yyyy\"),\n"
     "        FORMAT(First, \"mmm yyyy\") & \" to \" & FORMAT(Last, \"mmm yyyy\")\n"
     "    )", None,
     "A label for the period on screen: '2016', or 'Jan 2010 to Oct 2019'."),
    ("Latest Transaction", "MAX('Transactions'[Date])", "d mmm yyyy", None),
]


def m_partition(name: str, file: str, spec: dict, local: bool) -> list[str]:
    if local:
        path = str((DATA / file).resolve()).replace("\\", "\\\\")
        src = f'File.Contents("{path}")'
    else:
        src = f'Web.Contents("{RAW}{file}")'
    n = len(spec["types"])
    types = ", ".join(f'{{"{c}", {t}}}' for c, t in spec["types"].items())
    return [
        f"\tpartition {q(name)} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        f'\t\t\t\t    Source = Csv.Document({src}, [Delimiter=",", Columns={n}, '
        f'Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
        '\t\t\t\t    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),',
        f'\t\t\t\t    #"Applied Types" = Table.TransformColumnTypes(#"Promoted Headers", {{{types}}})',
        "\t\t\t\tin",
        '\t\t\t\t    #"Applied Types"',
    ]


def write_table(name: str, spec: dict, local: bool) -> None:
    lines: list[str] = []
    lines += doc(spec.get("doc"), 0)
    lines.append(f"table {q(name)}")
    lines.append(f"\tlineageTag: {tag('table', name)}")
    if spec.get("date_table"):
        lines.append("\tdataCategory: Time")
    for c in spec["columns"]:
        lines.append("")
        lines += doc(c.get("doc"), 1)
        lines.append(f"\tcolumn {q(c['name'])}")
        lines.append(f"\t\tdataType: {c['dtype']}")
        if c.get("hidden"):
            lines.append("\t\tisHidden")
        if c.get("key"):
            lines.append("\t\tisKey")
        if c.get("format"):
            lines.append(f"\t\tformatString: {c['format']}")
        lines.append(f"\t\tlineageTag: {tag('column', name, c['name'])}")
        lines.append("\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {c['source']}")
        if c.get("sortBy"):
            lines.append(f"\t\tsortByColumn: {q(c['sortBy'])}")
    if "files" in spec:
        for pname, f in zip(spec["partition_names"], spec["files"]):
            lines.append("")
            lines += m_partition(f"{name} {pname}", f, spec, local)
    else:
        lines.append("")
        lines += m_partition(name, spec["file"], spec, local)
    lines.append("")
    lines.append("\tannotation PBI_ResultType = Table")
    write(DEFN / "tables" / f"{name}.tmdl", lines)


def write_metrics() -> None:
    lines: list[str] = []
    lines += doc("Measure-only table. Nothing here stores data; the hidden column exists because\n"
                 "a table needs one. Every number on the report comes from here.", 0)
    lines.append("table Metrics")
    lines.append(f"\tlineageTag: {tag('table', 'Metrics')}")
    for name, dax, fmt, d in MEASURES:
        lines.append("")
        lines += doc(d, 1)
        body = dax.split("\n")
        if len(body) == 1:
            lines.append(f"\tmeasure {q(name)} = {body[0]}")
        else:
            lines.append(f"\tmeasure {q(name)} =")
            for b in body:
                lines.append(("\t\t\t" + b) if b.strip() else "\t\t\t")
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        lines.append(f"\t\tlineageTag: {tag('measure', name)}")
    lines.append("")
    lines.append("\tcolumn Column")
    lines.append("\t\tdataType: string")
    lines.append("\t\tisHidden")
    lines.append(f"\t\tlineageTag: {tag('column', 'Metrics', 'Column')}")
    lines.append("\t\tsummarizeBy: none")
    lines.append("\t\tsourceColumn: Column")
    lines.append("")
    lines.append("\tpartition Metrics = m")
    lines.append("\t\tmode: import")
    lines.append("\t\tsource =")
    lines.append("\t\t\t\tlet")
    lines.append('\t\t\t\t    Source = #table(type table [Column = text], {})')
    lines.append("\t\t\t\tin")
    lines.append("\t\t\t\t    Source")
    lines.append("")
    lines.append("\tannotation PBI_ResultType = Table")
    write(DEFN / "tables" / "Metrics.tmdl", lines)


def write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip("\n") + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="read data/ from disk, not GitHub")
    args = ap.parse_args()

    if DEFN.exists():
        for attempt in range(5):
            try:
                shutil.rmtree(DEFN)
                break
            except PermissionError:
                if attempt == 4:
                    shutil.rmtree(DEFN, ignore_errors=True)
                else:
                    time.sleep(0.5)

    for name, spec in TABLES.items():
        write_table(name, spec, args.local)
    write_metrics()

    write(DEFN / "database.tmdl", ["database", "\tcompatibilityLevel: 1606"])

    order = ", ".join(f'"{t}"' for t in TABLES)
    write(DEFN / "model.tmdl", [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tdiscourageImplicitMeasures",
        "\tsourceQueryCulture: en-US",
        "",
        f"annotation PBI_QueryOrder = [{order}]",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        'annotation PBI_ProTooling = ["DevMode"]',
        "",
    ] + [f"ref table {q(t)}" for t in list(TABLES) + ["Metrics"]])

    rel_lines: list[str] = []
    for i, (name, frm, to) in enumerate(RELATIONSHIPS):
        if i:
            rel_lines.append("")
        rel_lines += [f"relationship {q(name)}", f"\tfromColumn: {frm}", f"\ttoColumn: {to}"]
    write(DEFN / "relationships.tmdl", rel_lines)

    write_json(MODEL / "definition.pbism", """
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
  "version": "4.2",
  "settings": {
    "qnaEnabled": true
  }
}""")
    write_json(MODEL / ".platform", f"""
{{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
  "metadata": {{
    "type": "SemanticModel",
    "displayName": "Card Transactions"
  }},
  "config": {{
    "version": "2.0",
    "logicalId": "{tag('platform', 'model')}"
  }}
}}""")

    # The Calendar page: 'Cal ...' columns on the date table and its own measure table.
    milestone_calendar.install_model(DEFN, CALENDAR, lambda *p: tag("calendar", *p))
    n_calendar = len(milestone_calendar.measures(CALENDAR))
    print(f"  + Calendar Metrics: {n_calendar} measures, "
          f"{len(milestone_calendar.date_columns(CALENDAR))} calculated date columns")

    n_cols = sum(len(s["columns"]) for s in TABLES.values())
    n_parts = sum(len(s.get("files", [1])) for s in TABLES.values())
    print(f"{len(TABLES) + 1} tables, {n_cols} columns, {len(MEASURES)} measures, "
          f"{len(RELATIONSHIPS)} relationships, {n_parts} partitions -> {MODEL.name} "
          f"({'local files' if args.local else 'GitHub raw'})")


if __name__ == "__main__":
    main()
