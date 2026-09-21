"""Generate the PBIR report definition - five pages, the Milestone theme and every visual.

PBIR stores one JSON file per visual and wraps every property in the same
{"expr": {"Literal": {"Value": ...}}} envelope. Hand-editing that is how typos get in, so the
report is generated from this file: the helpers own the envelope and the page functions read as
layout.

    python etl/build_report.py

Rewrites <report>/definition/pages from scratch every run. That matters - a renamed visual left
behind on disk still renders, as an empty box.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import milestone_icons
import milestone_calendar
import milestone_pbir
from calendar_config import CALENDAR

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "Card Transactions.Report"
PAGES = REPORT / "definition" / "pages"
RESOURCES = REPORT / "StaticResources" / "RegisteredResources"
ASSETS = ROOT / "etl" / "assets"

CANVAS_W, CANVAS_H = 1440, 900

# --------------------------------------------------------------------------------------------
# Palette: milestonebi.com's own tokens.
# --------------------------------------------------------------------------------------------
PAPER = "#F4F6FA"
CARD = "#FFFFFF"
RULE = "#E3E7EF"
INK = "#0A0917"
BODY = "#4A5768"
MUTED = "#667284"
GOLD = "#C9A227"
GOLD_TEXT = "#8A6D14"
NAVY = "#111F38"
SLATE = "#7C8598"
LIGHT = "#BCC1D2"
GOOD = "#1E7A4C"
BAD = "#B3261E"

THEME_NAME = "MilestoneTheme.json"
MARK_NAME = "MilestoneMark.svg"

# --------------------------------------------------------------------------------------------
# Expression envelope helpers
# --------------------------------------------------------------------------------------------


def lit(value) -> dict:
    """Wrap a literal in the expression envelope PBIR expects.

    The suffix is load-bearing: 'D' for a double, 'L' for an integer, quotes for text. Getting
    it wrong makes Desktop drop the property silently rather than complain.
    """
    if isinstance(value, bool):
        v = "true" if value else "false"
    elif isinstance(value, int):
        v = f"{value}L"
    elif isinstance(value, float):
        v = f"{value}D"
    else:
        v = f"'{value}'"
    return {"expr": {"Literal": {"Value": v}}}


def colour(hex_code: str) -> dict:
    return {"solid": {"color": lit(hex_code)}}


def obj(**props) -> list:
    return [{"properties": props}]


def obj_for(metadata: str, **props) -> dict:
    return {"properties": props, "selector": {"metadata": metadata}}


def obj_for_value(table: str, col: str, value, **props) -> dict:
    """A property block scoped to one category value - a channel, a year, an outcome."""
    return {"properties": props, "selector": {"data": [{"scopeId": {"Comparison": {
        "ComparisonKind": 0,
        "Left": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}},
        "Right": lit(value)["expr"],
    }}}]}}


def measure(table: str, name: str, display: str | None = None) -> dict:
    field = {
        "field": {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}},
        "queryRef": f"{table}.{name}",
        "nativeQueryRef": name,
    }
    if display:
        field["displayName"] = display
    return field


def column(table: str, name: str, display: str | None = None, active: bool = True) -> dict:
    field = {
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}},
        "queryRef": f"{table}.{name}",
        "nativeQueryRef": name,
    }
    if active:
        field["active"] = True
    if display:
        field["displayName"] = display
    return field


def m(name: str, display: str | None = None) -> dict:
    return measure("Metrics", name, display)


def sort_by(field: dict, direction: str = "Descending") -> dict:
    return {"sort": [{"field": field["field"], "direction": direction}], "isDefaultSort": True}


def categorical_filter(name: str, table: str, col: str, values: list, alias: str = "t") -> dict:
    """A visual-level 'this column is one of these values' filter."""
    return {
        "name": name,
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}},
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": alias, "Entity": table, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": alias}},
                                            "Property": col}}],
                "Values": [[lit(v)["expr"]] for v in values],
            }}}],
        },
    }


# --------------------------------------------------------------------------------------------
# Container chrome
# --------------------------------------------------------------------------------------------


def chrome(title: str | None = None, subtitle: str | None = None, *,
           transparent: bool = False) -> dict:
    """Card background, hairline border and the small bold title every panel shares.

    subtitle is the SECOND positional parameter on purpose. It used to be third, behind
    transparent, and every chart in this file called chrome("Title", "Subtitle") - so the
    caption went into transparent, which is truthy, and the subtitle was dropped. The theme
    paints the card background anyway, so the panels still looked right and the analysis in
    the captions simply never rendered. transparent is keyword-only now so it cannot happen
    again.
    """
    show = not transparent
    out = {
        "padding": obj(top=lit(8.0), bottom=lit(8.0), left=lit(10.0), right=lit(10.0)),
        "dropShadow": obj(show=lit(False)),
        "background": obj(show=lit(show), color=colour(CARD), transparency=lit(0.0)),
        "border": obj(show=lit(show), color=colour(RULE), radius=lit(4)),
    }
    if title:
        out["title"] = obj(show=lit(True), text=lit(title), fontSize=lit(10.5), bold=lit(True),
                           fontColor=colour(INK), heading=lit("Heading3"))
        if subtitle:
            out["subTitle"] = obj(show=lit(True), text=lit(subtitle), fontSize=lit(8.5),
                                  fontColor=colour(MUTED))
    else:
        out["title"] = obj(show=lit(False))
    return out


def visual(name: str, vtype: str, x: int, y: int, w: int, h: int, z: int,
           query: dict | None = None, objects: dict | None = None,
           container: dict | None = None, filters: list | None = None) -> dict:
    node: dict = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": z, "width": w, "height": h, "tabOrder": z},
        "visual": {"visualType": vtype},
    }
    if query is not None:
        node["visual"]["query"] = query
    if objects:
        node["visual"]["objects"] = objects
    node["visual"]["visualContainerObjects"] = container or chrome()
    if filters:
        # filterConfig is a sibling of "visual" at the root, not a child of it.
        node["filterConfig"] = {"filters": filters}
    return node


# --------------------------------------------------------------------------------------------
# Reusable formatting blocks
# --------------------------------------------------------------------------------------------


def axis(show_title: bool = False, gridlines: bool = False, size: float = 8.5,
         title_size: float | None = None, **extra) -> list:
    return [{"properties": {
        "show": lit(True), "showAxisTitle": lit(show_title), "fontSize": lit(size),
        "labelColor": colour(MUTED), "gridlineShow": lit(gridlines),
        **({"gridlineColor": colour(RULE)} if gridlines else {}),
        **({"titleFontSize": lit(title_size), "titleColor": colour(MUTED)}
           if show_title and title_size else {}),
        **extra,
    }}]


def legend(show: bool = True, position: str = "Top") -> list:
    return [{"properties": {
        "show": lit(show), "position": lit(position), "showTitle": lit(False),
        "fontSize": lit(8.5), "labelColor": colour(MUTED),
    }}]


def no_labels() -> list:
    return [{"properties": {"show": lit(False)}}]


def data_labels(size: float = 8.5, units: str = "1", colour_hex: str = BODY) -> list:
    return [{"properties": {
        "show": lit(True), "fontSize": lit(size), "color": colour(colour_hex),
        "labelDisplayUnits": lit(units),
    }}]


def series_colour(mapping: dict[str, str]) -> list:
    return [obj_for(k, fill=colour(v)) for k, v in mapping.items()]


def value_colours(table: str, col: str, mapping: dict) -> list:
    return [obj_for_value(table, col, k, fill=colour(v)) for k, v in mapping.items()]


def no_chrome() -> dict:
    return {
        "padding": obj(top=lit(0.0), bottom=lit(0.0), left=lit(0.0), right=lit(0.0)),
        "dropShadow": obj(show=lit(False)),
        "background": obj(show=lit(False)),
        "border": obj(show=lit(False)),
        "title": obj(show=lit(False)),
    }


def textbox(name: str, x: int, y: int, w: int, h: int, z: int, paragraphs: list,
            background: str | None = None) -> dict:
    """paragraphs: each is a list of runs, or a single run dict for a one-run paragraph."""
    out = []
    for para in paragraphs:
        runs = para if isinstance(para, list) else [para]
        text_runs = []
        for run in runs:
            style = {"fontSize": f"{run.get('size', 11)}pt", "color": run.get("color", BODY)}
            if run.get("bold"):
                style["fontWeight"] = "bold"
            if run.get("family"):
                style["fontFamily"] = run["family"]
            if run.get("spacing"):
                style["letterSpacing"] = run["spacing"]
            text_runs.append({"value": run["text"], "textStyle": style})
        node = {"textRuns": text_runs}
        if runs[0].get("align"):
            node["horizontalTextAlignment"] = runs[0]["align"]
        out.append(node)
    container = no_chrome()
    if background:
        container["background"] = obj(show=lit(True), color=colour(background),
                                      transparency=lit(0.0))
        container["padding"] = obj(top=lit(4.0), bottom=lit(4.0), left=lit(10.0),
                                   right=lit(10.0))
    node = visual(name, "textbox", x, y, w, h, z, container=container)
    node["visual"]["objects"] = {"general": [{"properties": {"paragraphs": out}}]}
    return node


def note(name: str, x: int, y: int, w: int, h: int, z: int, heading: str,
         lines: list[str]) -> dict:
    """A card of prose - used where the honest caveat needs more room than a subtitle."""
    paras: list = [[{"text": heading, "size": 10.5, "color": INK, "bold": True}]]
    for line in lines:
        paras.append([{"text": "", "size": 4, "color": BODY}])
        paras.append([{"text": line, "size": 9, "color": BODY}])
    node = textbox(name, x, y, w, h, z, paras)
    node["visual"]["visualContainerObjects"] = chrome()
    return node


def image(name: str, x: int, y: int, w: int, h: int, z: int, resource: str) -> dict:
    node = visual(name, "image", x, y, w, h, z, container=no_chrome())
    node["visual"]["objects"] = {
        "general": [{"properties": {"imageUrl": {"expr": {"ResourcePackageItem": {
            "PackageName": "RegisteredResources", "PackageType": 1, "ItemName": resource}}}}}],
        "imageScaling": [{"properties": {"imageScalingType": lit("Fit")}}],
    }
    return node


# Icons the KPI strips ask for; main() registers exactly these as report resources.
USED_ICONS: set[str] = set()


def kpi_card(name: str, x: int, y: int, w: int, h: int, z: int, measures: list[dict],
             filters: list | None = None, value_size: float = 17.0, icons: list[str] | None = None) -> dict:
    node = visual(
        name, "cardVisual", x, y, w, h, z,
        query={"queryState": {"Data": {"projections": measures}}},
        objects={
            "general": [{"properties": {}}],
            "value": [{"properties": {
                "fontSize": lit(value_size), "bold": lit(True), "fontColor": colour(INK),
                "fontFamily": lit("Segoe UI"), "horizontalAlignment": lit("Left"),
                # Without this the auto units turn 1,001,858 into "1M".
                "labelDisplayUnits": lit("1"),
            }, "selector": {"id": "default"}}],
            "label": [{"properties": {
                "show": lit(True), "fontSize": lit(8.5), "fontColor": colour(MUTED),
                "bold": lit(False), "position": lit("belowValue"),
                "horizontalAlignment": lit("Left"),
            }, "selector": {"id": "default"}}],
            "accentBar": [{"properties": {
                "show": lit(True), "color": colour(GOLD), "width": lit(3),
            }, "selector": {"id": "default"}}],
        },
        filters=filters,
    )
    if icons:
        # One icon to the left of each value, from etl/milestone_icons.py.
        node["visual"]["objects"]["image"] = milestone_icons.card_images(measures, icons, w)
        USED_ICONS.update(icons)
    return node


def slicer(name: str, x: int, y: int, w: int, h: int, z: int, table: str, col: str,
           header: str, default: list | None = None, mode: str = "Dropdown") -> dict:
    general: dict = {"orientation": lit(0)}
    if default is not None:
        alias = table[0].lower()
        general["filter"] = {"filter": {
            "Version": 2,
            "From": [{"Name": alias, "Entity": table, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": alias}},
                                            "Property": col}}],
                "Values": [[lit(v)["expr"]] for v in default],
            }}}],
        }}
    return visual(
        name, "slicer", x, y, w, h, z,
        query={"queryState": {"Values": {"projections": [column(table, col)]}}},
        objects={
            "general": [{"properties": general}],
            "data": [{"properties": {"mode": lit(mode)}}],
            "header": [{"properties": {
                "show": lit(True), "text": lit(header), "textSize": lit(8.5),
                "fontColor": colour(MUTED), "bold": lit(True),
            }}],
            "items": [{"properties": {
                "fontColor": colour(BODY), "textSize": lit(9.5), "background": colour(CARD),
            }}],
        },
    )


def table_visual(name: str, x: int, y: int, w: int, h: int, z: int, fields: list[dict],
                 sort: dict, title: str, subtitle: str | None = None,
                 filters: list | None = None, totals: bool = False) -> dict:
    """A flat ranked table. Built as a matrix with one row field: on Desktop 2.157 a tableEx
    generated this way rendered the column fields and silently dropped every measure."""
    rows = [f for f in fields if "Column" in f["field"]]
    values = [f for f in fields if "Measure" in f["field"]]
    return visual(
        name, "pivotTable", x, y, w, h, z,
        query={"queryState": {"Rows": {"projections": rows},
                              "Values": {"projections": values}},
               "sortDefinition": sort},
        objects={
            "grid": [{"properties": {
                "gridVertical": lit(False), "gridHorizontal": lit(True),
                "gridHorizontalColor": colour(RULE), "rowPadding": lit(3),
            }}],
            "columnHeaders": [{"properties": {
                "fontSize": lit(9.0), "bold": lit(True), "fontColor": colour(INK),
                "backColor": colour(CARD), "alignment": lit("Right"),
            }}],
            "rowHeaders": [{"properties": {
                "fontSize": lit(9.0), "fontColor": colour(BODY), "backColor": colour(CARD),
            }}],
            "values": [{"properties": {
                "fontSize": lit(9.0), "fontColorPrimary": colour(BODY),
                "backColorPrimary": colour(CARD), "backColorSecondary": colour(CARD),
            }}],
            "subTotals": [{"properties": {"rowSubtotals": lit(totals),
                                          "columnSubtotals": lit(False)}}],
        },
        container=chrome(title, subtitle=subtitle),
        filters=filters,
    )


# --------------------------------------------------------------------------------------------
# Masthead
# --------------------------------------------------------------------------------------------


def masthead(slug: str, title: str, standfirst: str, ref: str) -> list[dict]:
    return [
        textbox(f"vBand{slug}", 0, 0, CANVAS_W, 60, 50, [
            [{"text": "", "size": 6, "color": INK}],
        ], background=INK),
        image(f"vMark{slug}", 24, 12, 44, 38, 60, MARK_NAME),
        textbox(f"vWordmark{slug}", 76, 15, 260, 32, 70, [
            [{"text": "Milestone ", "size": 15, "color": CARD, "bold": True},
             {"text": "BI", "size": 15, "color": GOLD, "bold": True}],
        ]),
        textbox(f"vRef{slug}", 1016, 22, 400, 22, 80, [
            [{"text": ref, "size": 8, "color": GOLD, "bold": True, "family": "Consolas",
              "spacing": "2px", "align": "right"}],
        ]),
        textbox(f"vTitle{slug}", 24, 74, 900, 40, 90, [
            [{"text": title, "size": 22, "color": INK, "bold": True}],
        ]),
        textbox(f"vStand{slug}", 24, 114, 1000, 46, 95, [
            [{"text": standfirst, "size": 10, "color": BODY}],
        ]),
    ]


def page(name: str, display: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
        "name": name,
        "displayName": display,
        "displayOption": "FitToPage",
        "height": CANVAS_H,
        "width": CANVAS_W,
        "objects": {
            "background": obj(color=colour(PAPER), transparency=lit(0.0)),
            "displayArea": obj(verticalAlignment=lit("Top")),
        },
    }


CHANNEL_COLOURS = {"Swipe": LIGHT, "Chip": NAVY, "Online": GOLD}
PRESENTMENT_COLOURS = {"Card present": NAVY, "Card not present": GOLD}
BRAND_COLOURS = {"Mastercard": NAVY, "Visa": GOLD, "Amex": SLATE, "Discover": LIGHT}
TYPE_COLOURS = {"Debit": NAVY, "Credit": GOLD, "Debit (Prepaid)": SLATE}
LOCTYPE_COLOURS = {"US state": NAVY, "Online": GOLD, "International": SLATE,
                   "Not recorded": LIGHT}
FRAUD_COLOURS = {"Confirmed fraud": BAD, "Confirmed not fraud": NAVY, "Not labelled": LIGHT}

SL_Y, SL_H = 74, 84
SL1_X, SL2_X, SL_W = 1076, 1246, 170


# --------------------------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------------------------


def page_overview() -> tuple[dict, list[dict]]:
    v: list[dict] = []
    v += masthead(
        "Ovr",
        "Card transactions, 2010 to 2019",
        "A 160-cardholder panel from a US card issuer's book: 1,682,640 attempts on 640 cards "
        "over ten years. Spend counts approved transactions only - Amount is populated on "
        "declines too. Every figure follows the year slicer.",
        "01 / OVERVIEW",
    )
    v.append(slicer("vYearOvr", SL1_X, SL_Y, SL_W, SL_H, 400, "Date", "Year", "YEAR"))
    v.append(kpi_card("vPeriodOvr", SL2_X, SL_Y, SL_W, SL_H, 410,
                      [m("Report Period", "Figures for")], value_size=11.0))

    v.append(kpi_card("vKpiOvr", 24, 176, 1392, 92, 500, [
        m("Approved Transactions", "Approved"),
        m("Spend", "Spend"),
        m("Average Transaction", "Average purchase"),
        m("Decline Rate %", "Decline rate"),
        m("Fraud Rate per 10k", "Fraud per 10k"),
    ], icons=["card-check", "coin", "receipt", "cancel", "shield-alert"]))

    v.append(visual(
        "vTrend", "lineChart", 24, 284, 900, 300, 600,
        query={
            "queryState": {
                "Category": {"projections": [column("Date", "Month Start", "Month")]},
                "Y": {"projections": [m("Spend")]},
            },
            "sortDefinition": sort_by(column("Date", "Month Start"), "Ascending"),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": no_labels(),
            "lineStyles": [{"properties": {
                "strokeWidth": lit(2), "lineStyle": lit("solid"), "showMarker": lit(False),
            }}],
            "dataPoint": series_colour({"Metrics.Spend": NAVY}),
        },
        container=chrome("Spend by month, 2010 to 2019",
                         "A fixed panel of 160 cardholders, so this is spending behaviour "
                         "over time, not a book that grew"),
    ))

    v.append(table_visual(
        "vYears", 940, 284, 476, 300, 610,
        [
            column("Date", "Year"),
            m("Approved Transactions", "Approved"),
            m("Spend", "Spend"),
            m("Spend LFL YoY %", "LFL vs PY"),
            m("Decline Rate %", "Declines"),
            m("Fraud Rate per 10k", "Fraud/10k"),
        ],
        sort_by(column("Date", "Year"), "Ascending"),
        "Year by year",
        "2019 stops on 31 October, so the comparison is like-for-like: Jan-Oct against Jan-Oct",
    ))

    v.append(visual(
        "vCategory", "barChart", 24, 600, 452, 276, 620,
        query={
            "queryState": {
                "Category": {"projections": [column("Merchant Category", "Category Group")]},
                "Y": {"projections": [m("Spend")]},
                "Tooltips": {"projections": [m("Spend Share", "Share of spend"),
                                             m("Approved Transactions", "Approved")]},
            },
            "sortDefinition": sort_by(m("Spend")),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": data_labels(units="1000"),
            "dataPoint": series_colour({"Metrics.Spend": NAVY}),
        },
        container=chrome("Spend by category group",
                         "Retail leads on value; groceries and fuel lead on volume and sit well down this list"),
    ))

    v.append(visual(
        "vChannelMix", "hundredPercentStackedColumnChart", 492, 600, 452, 276, 630,
        query={
            "queryState": {
                "Category": {"projections": [column("Date", "Year")]},
                "Series": {"projections": [column("Channel", "Channel", active=False)]},
                "Y": {"projections": [m("Transactions")]},
            },
            "sortDefinition": sort_by(column("Date", "Year"), "Ascending"),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(),
            "labels": no_labels(),
            "dataPoint": value_colours("Channel", "Channel", CHANNEL_COLOURS),
        },
        container=chrome("Channel mix by year",
                         "Nothing to two thirds in one month - see page 2"),
    ))

    v.append(visual(
        "vHour", "columnChart", 960, 600, 456, 276, 640,
        query={
            "queryState": {
                "Category": {"projections": [column("Transactions", "Hour")]},
                "Y": {"projections": [m("Approved Transactions")]},
            },
            "sortDefinition": sort_by(column("Transactions", "Hour"), "Ascending"),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": no_labels(),
            "dataPoint": series_colour({"Metrics.Approved Transactions": NAVY}),
        },
        container=chrome("Approved transactions by hour of day",
                         "58% falls between 9am and 6pm; 11am is the peak"),
    ))

    return page("pgOverview", "Overview"), v


def page_channels() -> tuple[dict, list[dict]]:
    v: list[dict] = []
    v += masthead(
        "Chn",
        "Chip, swipe and online",
        "How the card was presented, and what the bank did with the attempt. The channel "
        "series carries a discontinuity worth knowing about before anyone reads a trend into "
        "it - the note below the chart says what it is.",
        "02 / CHANNELS",
    )
    v.append(slicer("vYearChn", SL1_X, SL_Y, SL_W, SL_H, 400, "Date", "Year", "YEAR"))
    v.append(kpi_card("vPeriodChn", SL2_X, SL_Y, SL_W, SL_H, 410,
                      [m("Report Period", "Figures for")], value_size=11.0))

    v.append(kpi_card("vKpiChn", 24, 176, 1392, 92, 500, [
        m("Chip Share %", "Chip"),
        m("Online Share %", "Online"),
        m("Card Not Present Share %", "Card not present"),
        m("Decline Rate %", "Decline rate"),
        m("Technical Decline Rate %", "Technical declines"),
    ], icons=["chip", "globe", "laptop", "cancel", "sliders"]))

    v.append(visual(
        "vChannelMonth", "lineChart", 24, 284, 900, 300, 600,
        query={
            "queryState": {
                "Category": {"projections": [column("Date", "Month Start", "Month")]},
                "Series": {"projections": [column("Channel", "Channel", active=False)]},
                "Y": {"projections": [m("Channel Share %")]},
            },
            "sortDefinition": sort_by(column("Date", "Month Start"), "Ascending"),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(),
            "labels": no_labels(),
            "lineStyles": [{"properties": {
                "strokeWidth": lit(2), "lineStyle": lit("solid"), "showMarker": lit(False),
            }}],
            "dataPoint": value_colours("Channel", "Channel", CHANNEL_COLOURS),
        },
        container=chrome("Share of transactions by channel, month by month",
                         "Chip is 0.0% in December 2014 and 68% in January 2015, then flat for five years"),
    ))

    v.append(note(
        "vChipNote", 940, 284, 476, 300, 610,
        "That is not an EMV migration",
        ["Chip does not phase in here. It is 0.0% of transactions in December 2014, 68% in "
         "January 2015, and then flat within a point of that for the next five years.",
         "The real US liability shift was October 2015 and took years to work through the "
         "estate. A switch that clean is a property of how this file was generated, not of "
         "anything a bank did.",
         "The data is internally consistent - every chip transaction comes from a card the "
         "file marks as chip-enabled - so the flag is not random. It is just not a timeline.",
         "Which is why this page reports the channel mix and stops short of calling it "
         "adoption."],
    ))

    v.append(visual(
        "vReasons", "barChart", 24, 600, 700, 276, 620,
        query={
            "queryState": {
                "Category": {"projections": [column("Outcome", "Outcome")]},
                "Y": {"projections": [m("Declined Transactions")]},
                "Tooltips": {"projections": [m("Decline Share of Class", "Share of declines"),
                                             m("Declined Value", "Value declined")]},
            },
            "sortDefinition": sort_by(m("Declined Transactions")),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": data_labels(),
            "dataPoint": series_colour({"Metrics.Declined Transactions": NAVY}),
        },
        container=chrome("Why a transaction was declined",
                         "Insufficient balance leads; the technical glitches are the only "
                         "declines the issuer owns outright"),
        filters=[categorical_filter("fApproved", "Outcome", "Is Approved", ["No"], "o")],
    ))

    v.append(table_visual(
        "vChanTable", 740, 600, 676, 276, 630,
        [
            column("Channel", "Channel"),
            m("Transactions", "Attempts"),
            m("Spend", "Spend"),
            m("Decline Rate %", "Decline rate"),
            m("Fraud Rate per 10k", "Fraud/10k"),
        ],
        sort_by(m("Transactions")),
        "By channel",
        "Online is 11.4% of attempts, declines at 2.33% against 1.50% card present, and runs 27 times the fraud rate of a swipe",
    ))

    return page("pgChannels", "Channels"), v


def page_fraud() -> tuple[dict, list[dict]]:
    v: list[dict] = []
    v += masthead(
        "Fra",
        "Fraud, and the third of the file nobody labelled",
        "The source adjudicated 67% of transactions and left the rest unlabelled. Every rate "
        "here divides by the adjudicated rows only; the naive figure is shown beside it so the "
        "gap is visible rather than assumed away.",
        "03 / FRAUD",
    )
    v.append(slicer("vYearFra", SL1_X, SL_Y, SL_W, SL_H, 400, "Date", "Year", "YEAR"))
    v.append(kpi_card("vPeriodFra", SL2_X, SL_Y, SL_W, SL_H, 410,
                      [m("Report Period", "Figures for")], value_size=11.0))

    v.append(kpi_card("vKpiFra", 24, 176, 1392, 92, 500, [
        m("Labelled Transactions", "Adjudicated"),
        m("Label Coverage %", "Label coverage"),
        m("Fraud Transactions", "Confirmed fraud"),
        m("Fraud Rate per 10k", "Fraud per 10k"),
        m("Fraud Rate per 10k (all rows)", "If unlabelled counted clean"),
    ], icons=["clipboard-check", "percent", "warning", "shield-alert", "alert-circle"]))

    v.append(visual(
        "vFraudChannel", "barChart", 24, 284, 452, 300, 600,
        query={
            "queryState": {
                "Category": {"projections": [column("Channel", "Channel")]},
                "Y": {"projections": [m("Fraud Rate per 10k")]},
                "Tooltips": {"projections": [m("Fraud Transactions", "Cases"),
                                             m("Fraud Value", "Value")]},
            },
            "sortDefinition": sort_by(m("Fraud Rate per 10k")),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": data_labels(),
            "dataPoint": value_colours("Channel", "Channel", CHANNEL_COLOURS),
        },
        container=chrome("Fraud per 10,000 adjudicated, by channel",
                         "Online 91.3, chip 11.7, swipe 3.4"),
    ))

    v.append(visual(
        "vFraudYear", "columnChart", 492, 284, 452, 300, 610,
        query={
            "queryState": {
                "Category": {"projections": [column("Date", "Year")]},
                "Y": {"projections": [m("Fraud Rate per 10k")]},
                "Tooltips": {"projections": [m("Fraud Transactions", "Cases"),
                                             m("Labelled Transactions", "Adjudicated")]},
            },
            "sortDefinition": sort_by(column("Date", "Year"), "Ascending"),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": data_labels(),
            "dataPoint": series_colour({"Metrics.Fraud Rate per 10k": NAVY}),
        },
        container=chrome("Fraud rate by year",
                         "The swings are large because the case count is small - read the "
                         "counts in the tooltip before reading the shape"),
    ))

    v.append(visual(
        "vFraudCat", "barChart", 960, 284, 456, 300, 620,
        query={
            "queryState": {
                "Category": {"projections": [column("Merchant Category", "Category Group")]},
                "Y": {"projections": [m("Fraud Rate per 10k")]},
                "Tooltips": {"projections": [m("Fraud Transactions", "Cases")]},
            },
            "sortDefinition": sort_by(m("Fraud Rate per 10k")),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": data_labels(),
            "dataPoint": series_colour({"Metrics.Fraud Rate per 10k": NAVY}),
        },
        container=chrome("Fraud rate by category group",
                         "Electronics, digital goods and jewellery: 227 per 10,000, fourteen times the book. Fraud buys what resells"),
    ))

    v.append(table_visual(
        "vFraudTable", 24, 600, 880, 276, 630,
        [
            column("Channel", "Channel"),
            m("Transactions", "Attempts"),
            m("Labelled Transactions", "Adjudicated"),
            m("Fraud Transactions", "Fraud"),
            m("Fraud Rate per 10k", "Per 10k, correct"),
            m("Fraud Rate per 10k (all rows)", "Per 10k, naive"),
            m("Fraud Value Share", "Value at risk"),
        ],
        sort_by(m("Fraud Rate per 10k")),
        "The two denominators, side by side",
        "Same numerator both times. The naive column divides by every row, including the "
        "third nobody looked at",
    ))

    v.append(note(
        "vFraudNote", 920, 600, 496, 276, 640,
        "Why the two columns differ",
        ["Label coverage is 67.0%, and it is 67.0% in every single year, so the unlabelled rows "
         "are a sampling decision by whoever built the file - not a period that went "
         "unreviewed.",
         "Treating them as clean does not add information, it just inflates the denominator "
         "by half and pushes every rate down by a third.",
         "With 1,858 confirmed cases in the panel, the annual rates move around a lot. The "
         "channel split is the part that holds."],
    ))

    return page("pgFraud", "Fraud"), v


def page_customers() -> tuple[dict, list[dict]]:
    v: list[dict] = []
    v += masthead(
        "Cus",
        "Who is spending, and on what card",
        "160 cardholders and the 640 cards between them, 575 of which ever transact. Age, "
        "income and credit score are as at the file's build date, so a band describes the "
        "person now, not the person who made a 2010 transaction.",
        "04 / CUSTOMERS",
    )
    v.append(slicer("vYearCus", SL1_X, SL_Y, SL_W, SL_H, 400, "Date", "Year", "YEAR"))
    v.append(kpi_card("vPeriodCus", SL2_X, SL_Y, SL_W, SL_H, 410,
                      [m("Report Period", "Figures for")], value_size=11.0))

    v.append(kpi_card("vKpiCus", 24, 176, 1392, 92, 500, [
        m("Active Clients", "Active clients"),
        m("Active Cards", "Active cards"),
        m("Spend per Client", "Spend per client"),
        m("Total Credit Limit", "Combined limit"),
        m("Annual Spend to Limit", "Spend to limit, a year"),
    ], icons=["people", "card", "person-coin", "layers", "gauge"]))

    v.append(visual(
        "vScore", "barChart", 24, 284, 452, 300, 600,
        query={
            "queryState": {
                "Category": {"projections": [column("Client", "Credit Score Band")]},
                "Y": {"projections": [m("Spend")]},
                "Tooltips": {"projections": [m("Active Clients", "Clients"),
                                             m("Decline Rate %", "Decline rate")]},
            },
            "sortDefinition": sort_by(column("Client", "Credit Score Band"), "Ascending"),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": data_labels(units="1000"),
            "dataPoint": series_colour({"Metrics.Spend": NAVY}),
        },
        container=chrome("Spend by credit score band"),
    ))

    v.append(visual(
        "vIncome", "barChart", 492, 284, 452, 300, 610,
        query={
            "queryState": {
                "Category": {"projections": [column("Client", "Income Band")]},
                "Y": {"projections": [m("Spend")]},
                "Tooltips": {"projections": [m("Active Clients", "Clients"),
                                             m("Average Transaction", "Average purchase")]},
            },
            "sortDefinition": sort_by(column("Client", "Income Band"), "Ascending"),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": data_labels(units="1000"),
            "dataPoint": series_colour({"Metrics.Spend": GOLD}),
        },
        container=chrome("Spend by household income band"),
    ))

    v.append(visual(
        "vBrand", "hundredPercentStackedColumnChart", 960, 284, 456, 300, 620,
        query={
            "queryState": {
                "Category": {"projections": [column("Card", "Card Brand")]},
                "Series": {"projections": [column("Card", "Card Type", active=False)]},
                "Y": {"projections": [m("Spend")]},
            },
            "sortDefinition": sort_by(m("Spend")),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(),
            "labels": no_labels(),
            "dataPoint": value_colours("Card", "Card Type", TYPE_COLOURS),
        },
        container=chrome("Credit and debit split, by card brand"),
    ))

    v.append(table_visual(
        "vClientTable", 24, 600, 1392, 276, 630,
        [
            column("Client", "Credit Score Band"),
            m("Active Clients", "Clients"),
            m("Active Cards", "Cards"),
            m("Spend", "Spend"),
            m("Average Transaction", "Average purchase"),
            m("Total Credit Limit", "Combined limit"),
            m("Annual Spend to Limit", "Spend to limit, a year"),
            m("Decline Rate %", "Decline rate"),
            m("Fraud Rate per 10k", "Fraud/10k"),
        ],
        sort_by(column("Client", "Credit Score Band"), "Ascending"),
        "By credit score band",
        "Decline rate is the column that should move with the score. Whether it does is the first thing to check on any credit book",
    ))

    return page("pgCustomers", "Customers"), v


def page_geography() -> tuple[dict, list[dict]]:
    v: list[dict] = []
    v += masthead(
        "Geo",
        "Where the card was used",
        "The source keeps US states and foreign countries in one column - 199 values, 147 of "
        "them country names, and 'Georgia' the country sitting next to 'GA' the state. They "
        "are separated here before anything is drawn.",
        "05 / GEOGRAPHY",
    )
    v.append(slicer("vYearGeo", SL1_X, SL_Y, SL_W, SL_H, 400, "Date", "Year", "YEAR"))
    v.append(kpi_card("vPeriodGeo", SL2_X, SL_Y, SL_W, SL_H, 410,
                      [m("Report Period", "Figures for")], value_size=11.0))

    v.append(kpi_card("vKpiGeo", 24, 176, 1392, 92, 500, [
        m("Spend", "Spend"),
        m("Online Share %", "Online"),
        m("Approved Transactions", "Approved"),
        m("Average Transaction", "Average purchase"),
        m("Fraud Rate per 10k", "Fraud per 10k"),
    ], icons=["coin", "globe", "card-check", "receipt", "shield-alert"]))

    v.append(visual(
        "vLocType", "barChart", 24, 284, 452, 300, 600,
        query={
            "queryState": {
                "Category": {"projections": [column("Geography", "Location Type")]},
                "Y": {"projections": [m("Transactions")]},
                "Tooltips": {"projections": [m("Spend"), m("Fraud Rate per 10k", "Fraud/10k")]},
            },
            "sortDefinition": sort_by(m("Transactions")),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(False),
            "labels": data_labels(),
            "dataPoint": value_colours("Geography", "Location Type", LOCTYPE_COLOURS),
        },
        container=chrome("Transactions by location type",
                         "'Not recorded' is 845 card-present travel-agency rows, and they net to minus $44,657 - refunds, not sales"),
    ))

    v.append(table_visual(
        "vStates", 492, 284, 924, 300, 610,
        [
            column("Geography", "Location", "US state"),
            m("Approved Transactions", "Approved"),
            m("Spend", "Spend"),
            m("Spend Share", "Share"),
            m("Average Transaction", "Average purchase"),
            m("Fraud Rate per 10k", "Fraud/10k"),
        ],
        sort_by(m("Spend")),
        "US states by spend",
        "Fifty states and DC. Fraud is almost absent here - 172 cases in 1,478,159 attempts",
        filters=[categorical_filter("fUsState", "Geography", "Location Type",
                                    ["US state"], "g")],
    ))

    v.append(table_visual(
        "vIntl", 24, 600, 690, 276, 620,
        [
            column("Geography", "Country", "Country"),
            m("Approved Transactions", "Approved"),
            m("Spend", "Spend"),
            m("Average Transaction", "Average purchase"),
        ],
        sort_by(m("Spend")),
        "International spend",
        "11,815 attempts, 0.7% of the book - and 651 confirmed fraud per 10,000 adjudicated, against 1.7 inside the US",
        filters=[categorical_filter("fIntl", "Geography", "Location Type",
                                    ["International"], "g")],
    ))

    v.append(visual(
        "vPresentYear", "hundredPercentStackedColumnChart", 740, 600, 676, 276, 630,
        query={
            "queryState": {
                "Category": {"projections": [column("Date", "Year")]},
                "Series": {"projections": [column("Channel", "Presentment", active=False)]},
                "Y": {"projections": [m("Spend")]},
            },
            "sortDefinition": sort_by(column("Date", "Year"), "Ascending"),
        },
        objects={
            "categoryAxis": axis(), "valueAxis": axis(gridlines=True), "legend": legend(),
            "labels": no_labels(),
            "dataPoint": value_colours("Channel", "Presentment", PRESENTMENT_COLOURS),
        },
        container=chrome("Card present against card not present, by spend"),
    ))

    return page("pgGeography", "Geography"), v


# --------------------------------------------------------------------------------------------
# Theme and writers
# --------------------------------------------------------------------------------------------


def theme() -> dict:
    return {
        # Desktop caches themes by name, and the name must match the filename exactly.
        "name": THEME_NAME,
        "dataColors": [NAVY, GOLD, SLATE, LIGHT, GOOD, BAD, GOLD_TEXT, MUTED],
        "background": PAPER,
        "foreground": BODY,
        "tableAccent": INK,
        "good": GOOD,
        "neutral": MUTED,
        "bad": BAD,
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "fontSize": 14, "color": INK},
            "header": {"fontFace": "Segoe UI Semibold", "fontSize": 11, "color": INK},
            "label": {"fontFace": "Segoe UI", "fontSize": 9, "color": BODY},
            "callout": {"fontFace": "Segoe UI", "fontSize": 20, "color": INK},
        },
        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": CARD}}}],
                    "border": [{"show": True, "color": {"solid": {"color": RULE}}, "radius": 4}],
                    "padding": [{"top": 8, "bottom": 8, "left": 10, "right": 10}],
                    "dropShadow": [{"show": False}],
                }
            }
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # No BOM: a BOM breaks .platform and PBIR parsing. newline="\n" because write_text otherwise
    # uses the platform ending, and the repo is normalised to LF.
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def rmtree_retry(path: Path) -> None:
    """OneDrive intermittently holds a directory handle open; the files are gone by then."""
    for attempt in range(4):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 3:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.4)


CALENDAR_BOOKMARKS: list[dict] = []


def page_calendar() -> tuple[dict, list[dict]]:
    """The heat-mapped calendar, generated by etl/milestone_calendar.py from CALENDAR."""
    pg, visuals, bookmarks = milestone_calendar.build_page(CALENDAR)
    CALENDAR_BOOKMARKS[:] = bookmarks
    return pg, visuals


def main() -> None:
    if PAGES.exists():
        rmtree_retry(PAGES)

    builders = [page_overview, page_channels, page_fraud, page_customers, page_geography, page_calendar]
    order: list[str] = []
    total_visuals = 0

    for build in builders:
        pg, visuals = build()
        page_dir = PAGES / pg["name"]
        write_json(page_dir / "page.json", pg)
        names = set()
        for node in visuals:
            if node["name"] in names:
                print(f"ERROR: duplicate visual name {node['name']} on {pg['name']}",
                      file=sys.stderr)
                sys.exit(1)
            names.add(node["name"])
            write_json(page_dir / "visuals" / node["name"] / "visual.json", node)
        order.append(pg["name"])
        total_visuals += len(visuals)
        print(f"  {pg['name']:14s} {len(visuals):2d} visuals  ({pg['displayName']})")

    stale = [d for d in PAGES.rglob("visuals/*")
             if d.is_dir() and not (d / "visual.json").exists()]
    for d in stale:
        rmtree_retry(d)
        if d.exists():
            print(f"ERROR: could not remove stale visual directory {d}. "
                  f"Close Power BI Desktop and run again.", file=sys.stderr)
            sys.exit(1)
        print(f"  swept stale visual directory {d.name}")

    write_json(PAGES / "pages.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": order,
        "activePageName": order[0],
    })

    bookmarks_dir = REPORT / "definition" / "bookmarks"
    if bookmarks_dir.exists():
        rmtree_retry(bookmarks_dir)
    for bm in CALENDAR_BOOKMARKS:
        write_json(bookmarks_dir / f"{bm['name']}.bookmark.json", bm)
    write_json(bookmarks_dir / "bookmarks.json", milestone_pbir.bookmarks_metadata(CALENDAR_BOOKMARKS))

    write_json(REPORT / "definition" / "version.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    })

    write_json(REPORT / "definition" / "report.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {
                "name": "CY25SU12",
                "reportVersionAtImport": {"visual": "2.12.0", "report": "3.4.0",
                                          "page": "2.3.1"},
                "type": "SharedResources",
            },
            "customTheme": {
                "name": THEME_NAME,
                "reportVersionAtImport": {"visual": "2.12.0", "report": "3.4.0",
                                          "page": "2.3.1"},
                "type": "RegisteredResources",
            },
        },
        "objects": {
            "section": [{"properties": {"verticalAlignment": lit("Top")}}],
            "outspacePane": [{"properties": {"expanded": lit(False)}}],
        },
        "resourcePackages": [
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": THEME_NAME, "path": THEME_NAME, "type": "CustomTheme"},
                       {"name": MARK_NAME, "path": MARK_NAME, "type": "Image"}]
                      + [{"name": n, "path": n, "type": "Image"}
                         for n in milestone_icons.resources(USED_ICONS)]},
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY25SU12", "path": "BaseThemes/CY25SU12.json",
                        "type": "BaseTheme"}]},
        ],
        "settings": {"useStylableVisualContainerHeader": True, "useEnhancedTooltips": False},
    })

    RESOURCES.mkdir(parents=True, exist_ok=True)
    write_json(RESOURCES / THEME_NAME, theme())
    shutil.copyfile(ASSETS / "milestone-mark.svg", RESOURCES / MARK_NAME)
    for icon_file, svg in milestone_icons.resources(USED_ICONS).items():
        (RESOURCES / icon_file).write_text(svg, encoding="utf-8", newline="\n")

    write_json(REPORT / "definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": "../Card Transactions.SemanticModel"}},
    })

    write_json(REPORT / ".platform", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": "Card Transactions"},
        "config": {"version": "2.0", "logicalId": "b7d4e91a-3c26-4f58-8a0d-6e5b2c9f1487"},
    })

    write_json(ROOT / "Card Transactions.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": "Card Transactions.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })

    print(f"\n{len(order)} pages, {total_visuals} visuals written")


if __name__ == "__main__":
    main()
