"""Generate the naive model - one flat table, the way most PBIX files are actually built.

    python perf/etl/build_naive_model.py

Writes perf/Card Transactions Naive.{pbip,SemanticModel,Report}. The report is one page with
two visuals; it exists only so the PBIP opens in Desktop, because that is how the engine gets
started and measured.

The point of this file is that none of it is a strawman. Every decision below is one a
competent person makes when they are modelling in the Power Query window rather than on paper:

  * everything merged into one table, because that is what the merge button does
  * the id column kept, because someone might want to drill to a transaction
  * the timestamp kept to the minute, because "we might need the time later"
  * keys left as text, because that is the type the merge produced
  * Year, Month and the approved flag added as calculated columns, because that is the
    obvious place to put them
  * auto date/time left on, because it is on by default
  * measures written as SUMX(FILTER(...)), because that is what reads like the sentence
    "sum the amount where it was approved"

Each one is defensible on its own. Together they cost what perf/etl/compare.py measures.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERF = ROOT / "perf"
NAME = "Card Transactions Naive"
MODEL = PERF / f"{NAME}.SemanticModel"
REPORT = PERF / f"{NAME}.Report"
DEFN = MODEL / "definition"
FLAT = PERF / "data" / "flat_transactions.csv"

NS = uuid.UUID("2c5e8a13-9b74-4f06-a1d8-3e6b7c40f295")


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NS, "naive:" + ":".join(parts)))


def q(name: str) -> str:
    return name if name.replace("_", "").isalnum() else f"'{name}'"


# (model name, source column, TMDL data type, M type)
COLUMNS = [
    ("Transaction ID", "TransactionID", "string", "type text"),
    ("Timestamp", "Timestamp", "dateTime", "type datetime"),
    ("Client ID", "ClientID", "string", "type text"),
    ("Card ID", "CardID", "string", "type text"),
    ("Amount", "Amount", "double", "type number"),
    ("Channel", "Channel", "string", "type text"),
    ("Merchant ID", "MerchantID", "string", "type text"),
    ("Merchant City", "MerchantCity", "string", "type text"),
    ("Merchant State", "MerchantState", "string", "type text"),
    ("Merchant Zip", "MerchantZip", "string", "type text"),
    ("MCC", "MCC", "string", "type text"),
    ("Errors", "Errors", "string", "type text"),
    ("Fraud Label", "FraudLabel", "string", "type text"),
    ("Merchant Category", "MerchantCategory", "string", "type text"),
    ("Card Brand", "CardBrand", "string", "type text"),
    ("Card Type", "CardType", "string", "type text"),
    ("Card Has Chip", "CardHasChip", "string", "type text"),
    ("Card Credit Limit", "CardCreditLimit", "double", "type number"),
    ("Card Account Opened", "CardAccountOpened", "string", "type text"),
    ("Cards Issued", "CardsIssued", "int64", "Int64.Type"),
    ("Client Age", "ClientAge", "int64", "Int64.Type"),
    ("Client Gender", "ClientGender", "string", "type text"),
    ("Client Yearly Income", "ClientYearlyIncome", "double", "type number"),
    ("Client Total Debt", "ClientTotalDebt", "double", "type number"),
    ("Client Credit Score", "ClientCreditScore", "int64", "Int64.Type"),
    ("Client Cards Held", "ClientCardsHeld", "int64", "Int64.Type"),
]

# Calculated columns: the obvious place to put a derived value when the model is one table.
CALC_COLUMNS = [
    ("Year", "YEAR('Transactions'[Timestamp])", "int64", "0"),
    ("Month Year", "FORMAT('Transactions'[Timestamp], \"mmm yyyy\")", "string", None),
    ("Is Approved",
     "IF(LEN('Transactions'[Errors]) = 0, \"Yes\", \"No\")", "string", None),
    ("Spend Amount",
     "IF('Transactions'[Is Approved] = \"Yes\", 'Transactions'[Amount], BLANK())",
     "double", "#,0.00"),
    ("Is Fraud",
     "IF('Transactions'[Fraud Label] = \"Yes\", \"Yes\", \"No\")", "string", None),
]

USD, INT, PCT, DEC1 = "\\$#,0", "#,0", "0.00%", "0.0"

# Measures written the way the flat table invites: iterate and filter, because that is how the
# sentence reads. Every one returns the same answer as its star-schema counterpart.
MEASURES = [
    ("Transactions", "COUNTROWS('Transactions')", INT),
    ("Approved Transactions",
     "COUNTROWS(FILTER('Transactions', 'Transactions'[Is Approved] = \"Yes\"))", INT),
    ("Declined Transactions",
     "COUNTROWS(FILTER('Transactions', 'Transactions'[Is Approved] = \"No\"))", INT),
    ("Decline Rate %", "DIVIDE([Declined Transactions], [Transactions])", PCT),
    ("Spend",
     "SUMX(\n"
     "    FILTER('Transactions', 'Transactions'[Is Approved] = \"Yes\"),\n"
     "    'Transactions'[Amount]\n"
     ")", USD),
    ("Active Cards", "DISTINCTCOUNT('Transactions'[Card ID])", INT),
    ("Active Clients", "DISTINCTCOUNT('Transactions'[Client ID])", INT),
    ("Labelled Transactions",
     "COUNTROWS(FILTER('Transactions', LEN('Transactions'[Fraud Label]) > 0))", INT),
    ("Fraud Transactions",
     "COUNTROWS(FILTER('Transactions', 'Transactions'[Fraud Label] = \"Yes\"))", INT),
    ("Fraud Rate per 10k",
     "DIVIDE([Fraud Transactions], [Labelled Transactions]) * 10000", DEC1),
    ("Average Transaction",
     "AVERAGEX(\n"
     "    FILTER(\n"
     "        'Transactions',\n"
     "        'Transactions'[Is Approved] = \"Yes\" && 'Transactions'[Amount] > 0\n"
     "    ),\n"
     "    'Transactions'[Amount]\n"
     ")", "\\$#,0.00"),
    # No marked date table, so year-on-year is done by arithmetic on the year column.
    ("Spend PY",
     "VAR ThisYear = SELECTEDVALUE('Transactions'[Year])\n"
     "RETURN\n"
     "    CALCULATE(\n"
     "        [Spend],\n"
     "        FILTER(ALL('Transactions'), 'Transactions'[Year] = ThisYear - 1)\n"
     "    )", USD),
    ("Spend YoY %", "DIVIDE([Spend] - [Spend PY], [Spend PY])", PCT),
]


def m_partition() -> list[str]:
    path = str(FLAT.resolve()).replace("\\", "\\\\")
    n = len(COLUMNS)
    types = ", ".join(f'{{"{src}", {mt}}}' for _, src, _, mt in COLUMNS)
    return [
        "\tpartition Transactions = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        f'\t\t\t\t    Source = Csv.Document(File.Contents("{path}"), [Delimiter=",", '
        f'Columns={n}, Encoding=65001, QuoteStyle=QuoteStyle.Csv]),',
        '\t\t\t\t    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),',
        f'\t\t\t\t    #"Applied Types" = Table.TransformColumnTypes(#"Promoted Headers", {{{types}}})',
        "\t\t\t\tin",
        '\t\t\t\t    #"Applied Types"',
    ]


def write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def rmtree_retry(path: Path) -> None:
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 4:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.5)


def build_model() -> None:
    if DEFN.exists():
        rmtree_retry(DEFN)

    lines: list[str] = [
        "/// One table. Every dimension merged in, nothing dropped, keys as text and the",
        "/// timestamp to the minute. This is the control in a performance comparison, not a",
        "/// recommendation.",
        "table Transactions",
        f"\tlineageTag: {tag('table', 'Transactions')}",
    ]
    for name, src, dtype, _ in COLUMNS:
        lines += [
            "",
            f"\tcolumn {q(name)}",
            f"\t\tdataType: {dtype}",
            f"\t\tlineageTag: {tag('column', name)}",
            "\t\tsummarizeBy: none",
            f"\t\tsourceColumn: {src}",
        ]
    for name, expr, dtype, fmt in CALC_COLUMNS:
        lines += ["", f"\tcolumn {q(name)} = {expr}" if "\n" not in expr
                  else f"\tcolumn {q(name)} ="]
        if "\n" in expr:
            lines += ["\t\t\t" + b for b in expr.split("\n")]
        lines.append(f"\t\tdataType: {dtype}")
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        lines.append(f"\t\tlineageTag: {tag('calc', name)}")
        lines.append("\t\tsummarizeBy: none")
    for name, dax, fmt in MEASURES:
        body = dax.split("\n")
        lines.append("")
        if len(body) == 1:
            lines.append(f"\tmeasure {q(name)} = {body[0]}")
        else:
            lines.append(f"\tmeasure {q(name)} =")
            lines += [("\t\t\t" + b) if b.strip() else "\t\t\t" for b in body]
        lines.append(f"\t\tformatString: {fmt}")
        lines.append(f"\t\tlineageTag: {tag('measure', name)}")
    lines.append("")
    lines += m_partition()
    lines += ["", "\tannotation PBI_ResultType = Table"]
    write(DEFN / "tables" / "Transactions.tmdl", lines)

    write(DEFN / "database.tmdl", ["database", "\tcompatibilityLevel: 1606"])
    write(DEFN / "model.tmdl", [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "",
        'annotation PBI_QueryOrder = ["Transactions"]',
        "",
        # Left on, as it is by default. Desktop then builds a hidden date table for every date
        # column in the model, and those tables are part of what gets measured.
        "annotation __PBI_TimeIntelligenceEnabled = 1",
        "",
        'annotation PBI_ProTooling = ["DevMode"]',
        "",
        "ref table Transactions",
    ])
    write(DEFN / "relationships.tmdl", [])

    write_json(MODEL / "definition.pbism", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2",
        "settings": {},
    })
    write_json(MODEL / ".platform", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("platform", "model")},
    })


def lit(v):
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, int):
        s = f"{v}L"
    elif isinstance(v, float):
        s = f"{v}D"
    else:
        s = f"'{v}'"
    return {"expr": {"Literal": {"Value": s}}}


def build_report() -> None:
    """One page, two visuals - just enough for the PBIP to open."""
    pages = REPORT / "definition" / "pages"
    if pages.exists():
        rmtree_retry(pages)

    def field(kind, prop, display=None):
        f = {"field": {kind: {"Expression": {"SourceRef": {"Entity": "Transactions"}},
                              "Property": prop}},
             "queryRef": f"Transactions.{prop}", "nativeQueryRef": prop}
        if display:
            f["displayName"] = display
        return f

    visuals = [
        ("vCard", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json",
            "name": "vCard",
            "position": {"x": 24, "y": 24, "z": 0, "width": 600, "height": 140, "tabOrder": 0},
            "visual": {
                "visualType": "card",
                "query": {"queryState": {"Values": {"projections": [
                    field("Measure", "Spend", "Spend")]}}},
            },
        }),
        ("vYear", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json",
            "name": "vYear",
            "position": {"x": 24, "y": 180, "z": 1, "width": 900, "height": 400, "tabOrder": 1},
            "visual": {
                "visualType": "columnChart",
                "query": {"queryState": {
                    "Category": {"projections": [field("Column", "Year")]},
                    "Y": {"projections": [field("Measure", "Spend")]},
                }},
            },
        }),
    ]
    for name, node in visuals:
        write_json(pages / "pgMain" / "visuals" / name / "visual.json", node)
    write_json(pages / "pgMain" / "page.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
        "name": "pgMain", "displayName": "Main", "displayOption": "FitToPage",
        "height": 720, "width": 1280,
    })
    write_json(pages / "pages.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": ["pgMain"], "activePageName": "pgMain",
    })
    write_json(REPORT / "definition" / "version.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    })
    write_json(REPORT / "definition" / "report.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
        "themeCollection": {"baseTheme": {
            "name": "CY25SU12",
            "reportVersionAtImport": {"visual": "2.12.0", "report": "3.4.0", "page": "2.3.1"},
            "type": "SharedResources"}},
        "resourcePackages": [{"name": "SharedResources", "type": "SharedResources", "items": [
            {"name": "CY25SU12", "path": "BaseThemes/CY25SU12.json", "type": "BaseTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True},
    })
    write_json(REPORT / "definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}},
    })
    write_json(REPORT / ".platform", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("platform", "report")},
    })
    write_json(PERF / f"{NAME}.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })


def main() -> None:
    if not FLAT.exists():
        raise SystemExit(f"{FLAT} is missing - run perf/etl/build_flat_source.py first")
    build_model()
    build_report()
    print(f"1 table, {len(COLUMNS)} source columns, {len(CALC_COLUMNS)} calculated columns, "
          f"{len(MEASURES)} measures, 0 relationships -> {MODEL.name}")


if __name__ == "__main__":
    main()
