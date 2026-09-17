"""milestone_pbir - the Milestone BI report helpers, in one file a project can vendor.

Canonical copy: ~/.claude/skills/milestone-report-design/assets/lib/milestone_pbir.py.
Projects keep a copy at etl/milestone_pbir.py so each repo rebuilds on its own; bump VERSION
and re-copy when the canonical file changes.

Two halves:

* Report (PBIR): the expression envelope, container chrome, and the visuals every page reuses -
  masthead, dynamic text, SVG image cards, button slicers, dropdowns, bookmark buttons, the
  filter panel with its two bookmarks, charts' axes and pivot-table formatting.
* Model (DAX + TMDL): SVG building blocks (card frame, ring, diverging bar, encoding), a
  two-direction rank measure, and writers for a measure table, a disconnected toggle table and
  the model.tmdl ref lines.

Everything here rendered in Power BI Desktop 2.157 and the Power BI Service on the Superstore
revenue page and the P&L Against Plan page. The traps each helper avoids are in its docstring;
the full list is in the skill's references/traps.md.
"""

from __future__ import annotations

import uuid
from pathlib import Path

VERSION = "1.1.0"  # 1.1.0: tone()/diverging_bar() take higher_is_better (Workforce page 05)

# --------------------------------------------------------------------------------------------
# Tokens
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

CANVAS_W, CANVAS_H = 1440, 900
MARK_FILE = "MilestoneMark.svg"

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
VISUAL_SCHEMA = f"{SCHEMA}/visualContainer/2.5.0/schema.json"
PAGE_SCHEMA = f"{SCHEMA}/page/2.0.0/schema.json"
BOOKMARK_SCHEMA = f"{SCHEMA}/bookmark/2.1.0/schema.json"
BOOKMARKS_METADATA_SCHEMA = f"{SCHEMA}/bookmarksMetadata/1.0.0/schema.json"


# ============================================================================================
# REPORT (PBIR)
# ============================================================================================


def lit(value) -> dict:
    """A literal in the expression envelope. The suffix is load-bearing: 'D' for a double, 'L'
    for an integer, quotes for text. A wrong one makes Desktop drop the property silently."""
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


def column(table: str, name: str, display: str | None = None) -> dict:
    f = {"field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}},
         "queryRef": f"{table}.{name}", "nativeQueryRef": name, "active": True}
    if display:
        f["displayName"] = display
    return f


class Fields:
    """Measure references bound to one measure table, so page code reads as layout."""

    def __init__(self, entity: str):
        self.entity = entity

    def m(self, name: str, display: str | None = None) -> dict:
        """A measure projection for a query role."""
        f = {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": self.entity}}, "Property": name}},
             "queryRef": f"{self.entity}.{name}", "nativeQueryRef": name}
        if display:
            f["displayName"] = display
        return f

    def expr(self, name: str) -> dict:
        """A measure bound into a property - a title, an image source, a colour."""
        return {"expr": {"Measure": {"Expression": {"SourceRef": {"Entity": self.entity}}, "Property": name}}}

    def ref(self, name: str) -> str:
        """The queryRef used by metadata selectors."""
        return f"{self.entity}.{name}"

    def range_filter(self, name: str, measure: str, low: int, high: int) -> dict:
        """Visual-level 'low <= measure <= high'. Keeps ranks 1..N of a rank measure whose
        direction a toggle flips; a TopN filter cannot change direction. It also narrows
        ALLSELECTED inside that visual to the rows kept - pool with ALL there."""
        ref = {"Measure": {"Expression": {"SourceRef": {"Source": "m"}}, "Property": measure}}
        return {
            "name": name,
            "field": {"Measure": {"Expression": {"SourceRef": {"Entity": self.entity}}, "Property": measure}},
            "type": "Advanced",
            "filter": {"Version": 2, "From": [{"Name": "m", "Entity": self.entity, "Type": 0}],
                       "Where": [{"Condition": {"And": {
                           "Left": {"Comparison": {"ComparisonKind": 2, "Left": ref, "Right": lit(low)["expr"]}},
                           "Right": {"Comparison": {"ComparisonKind": 4, "Left": ref, "Right": lit(high)["expr"]}},
                       }}}]},
        }

    def values_with_colour(self, colour_measure: str, coloured_measure: str) -> list:
        """pivotTable 'values': house cell style plus one column's font colour from a measure."""
        return [
            {"properties": {"fontSize": lit(9.0), "fontColorPrimary": colour(BODY),
                            "backColorPrimary": colour(CARD), "backColorSecondary": colour(CARD)}},
            {"properties": {"fontColor": {"solid": {"color": self.expr(colour_measure)}}},
             "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}],
                          "metadata": self.ref(coloured_measure)}},
        ]


def sort_by(field: dict, direction: str = "Descending") -> dict:
    return {"sort": [{"field": field["field"], "direction": direction}], "isDefaultSort": True}


def in_filter(alias: str, table: str, col: str, values: list) -> dict:
    return {
        "Version": 2,
        "From": [{"Name": alias, "Entity": table, "Type": 0}],
        "Where": [{"Condition": {"In": {
            "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": alias}}, "Property": col}}],
            "Values": [[lit(v)["expr"]] for v in values],
        }}}],
    }


def categorical_filter(name: str, table: str, col: str, values: list) -> dict:
    return {"name": name,
            "field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}},
            "type": "Categorical", "filter": in_filter("t", table, col, values)}


# ---- chrome -------------------------------------------------------------------------------


def no_chrome() -> dict:
    """Declares every container object off. The theme's white card also paints textboxes and
    shapes, so anything floating on paper or the brand band must say so explicitly."""
    return {
        "padding": obj(top=lit(0.0), bottom=lit(0.0), left=lit(0.0), right=lit(0.0)),
        "dropShadow": obj(show=lit(False)),
        "background": obj(show=lit(False)),
        "border": obj(show=lit(False)),
        "title": obj(show=lit(False)),
    }


def dynamic_chrome(title: dict, subtitle: dict | None = None) -> dict:
    """Panel chrome with title and subtitle bound to measures (pass Fields.expr(...)). A title
    bound to a measure that does not exist silently disappears."""
    out = {
        "padding": obj(top=lit(8.0), bottom=lit(8.0), left=lit(10.0), right=lit(10.0)),
        "dropShadow": obj(show=lit(False)),
        "background": obj(show=lit(True), color=colour(CARD), transparency=lit(0.0)),
        "border": obj(show=lit(True), color=colour(RULE), radius=lit(4)),
        "title": obj(show=lit(True), text=title, fontSize=lit(10.5), bold=lit(True),
                     fontColor=colour(INK), heading=lit("Heading3")),
    }
    if subtitle:
        out["subTitle"] = obj(show=lit(True), text=subtitle, fontSize=lit(8.5), fontColor=colour(MUTED))
    return out


def visual(name, vtype, x, y, w, h, z, query=None, objects=None, container=None, filters=None) -> dict:
    """filterConfig is a sibling of 'visual' at the root of visual.json, not a child of it."""
    node = {"$schema": VISUAL_SCHEMA, "name": name,
            "position": {"x": x, "y": y, "z": z, "width": w, "height": h, "tabOrder": z},
            "visual": {"visualType": vtype}}
    if query is not None:
        node["visual"]["query"] = query
    if objects:
        node["visual"]["objects"] = objects
    node["visual"]["visualContainerObjects"] = container or no_chrome()
    if filters:
        node["filterConfig"] = {"filters": filters}
    return node


def page(name: str, display: str) -> dict:
    return {
        "$schema": PAGE_SCHEMA,
        "name": name, "displayName": display, "displayOption": "FitToPage",
        "height": CANVAS_H, "width": CANVAS_W,
        "objects": {"background": obj(color=colour(PAPER), transparency=lit(0.0)),
                    "displayArea": obj(verticalAlignment=lit("Top"))},
    }


# ---- visuals ------------------------------------------------------------------------------


def textbox(name, x, y, w, h, z, paragraphs, background=None) -> dict:
    """paragraphs: each a list of runs {text, size, color, bold, family, spacing, align}, or one
    run dict. 'align' on a paragraph's first run aligns the paragraph."""
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
        container["background"] = obj(show=lit(True), color=colour(background), transparency=lit(0.0))
        container["padding"] = obj(top=lit(4.0), bottom=lit(4.0), left=lit(10.0), right=lit(10.0))
    node = visual(name, "textbox", x, y, w, h, z, container=container)
    node["visual"]["objects"] = {"general": [{"properties": {"paragraphs": out}}]}
    return node


def mark(name, x, y, w, h, z, resource: str = MARK_FILE) -> dict:
    """A registered image: listed in report.json resourcePackages as type Image, bound through a
    ResourcePackageItem expression rather than a path."""
    node = visual(name, "image", x, y, w, h, z)
    node["visual"]["objects"] = {
        "general": obj(imageUrl={"expr": {"ResourcePackageItem": {
            "PackageName": "RegisteredResources", "PackageType": 1, "ItemName": resource}}}),
        "imageScaling": obj(imageScalingType=lit("Fit")),
    }
    return node


def masthead(slug: str, title: str, ref: str, title_width: int = 700) -> list[dict]:
    """Brand band, mark, wordmark, page ref and page title. Visual names end in `slug`."""
    return [
        textbox(f"vBand{slug}", 0, 0, CANVAS_W, 60, 50, [[{"text": "", "size": 6, "color": INK}]], background=INK),
        mark(f"vMark{slug}", 24, 12, 44, 38, 60),
        textbox(f"vWordmark{slug}", 76, 15, 260, 32, 70, [[{"text": "Milestone ", "size": 15, "color": CARD, "bold": True},
                                                          {"text": "BI", "size": 15, "color": GOLD, "bold": True}]]),
        textbox(f"vRef{slug}", 1016, 22, 400, 22, 80, [[{"text": ref, "size": 8, "color": GOLD, "bold": True,
                                                         "family": "Consolas", "spacing": "2px", "align": "right"}]]),
        textbox(f"vTitle{slug}", 24, 74, title_width, 40, 90, [[{"text": title, "size": 22,
                                                                 "color": INK, "bold": True}]]),
    ]


def dynamic_text(name, x, y, w, h, z, text: dict, size=10.0, color=BODY, align="left") -> dict:
    """Text from a measure (pass Fields.expr(...)). A textbox cannot bind a field, so this is a
    shape whose container title is the measure. The fill must be transparent bare AND on the
    default selector - with only show=false the theme painted a navy bar under the text."""
    container = no_chrome()
    container["title"] = obj(show=lit(True), text=text, fontSize=lit(size), fontColor=colour(color),
                             bold=lit(False), titleWrap=lit(True), alignment=lit(align))
    clear = {"show": lit(True), "fillColor": colour(PAPER), "transparency": lit(100.0)}
    off = {"show": lit(False)}
    node = visual(name, "shape", x, y, w, h, z, container=container)
    node["visual"]["objects"] = {
        "shape": [{"properties": {"tileShape": lit("rectangle")}, "selector": {"id": "default"}}],
        "fill": [{"properties": clear}, {"properties": clear, "selector": {"id": "default"}}],
        "outline": [{"properties": off}, {"properties": off, "selector": {"id": "default"}}],
    }
    return node


def svg_image(name, x, y, w, h, z, source: dict) -> dict:
    """An image visual showing an SVG measure (dataCategory ImageUrl; pass Fields.expr(...)).
    Draw the SVG at the visual's exact size - fit is Normal and the container adds nothing."""
    node = visual(name, "image", x, y, w, h, z)
    node["visual"]["objects"] = {"image": [{"properties": {
        "sourceType": lit("imageData"), "sourceField": source, "fit": lit("Normal")}}]}
    return node


def button_slicer(name, x, y, w, h, z, table, col, default, columns, header=None, filters=None) -> dict:
    """A tile slicer with one choice always selected. Tiles under ~40px high clip their text
    (104px per tile for 'Cumulative' at 9pt). Give every toggle its own disconnected table: two
    slicers on one column cross-filter each other down to one tile each."""
    container = no_chrome()
    if header:
        container["title"] = obj(show=lit(True), text=lit(header), fontSize=lit(8.5), bold=lit(True),
                                 fontColor=colour(MUTED))
    return visual(
        name, "advancedSlicerVisual", x, y, w, h, z,
        query={"queryState": {"Values": {"projections": [column(table, col)]}}},
        objects={
            "general": [{"properties": {"filter": {"filter": in_filter("b", table, col, [default])}}}],
            "selection": [{"properties": {"strictSingleSelect": lit(True), "selectAllCheckboxEnabled": lit(False)}}],
            "layout": [{"properties": {"rowCount": lit(1), "columnCount": lit(columns), "cellPadding": lit(0)}}],
            "shapeCustomRectangle": [{"properties": {"tileShape": lit("rectangleRoundedByPixel"),
                                                     "rectangleRoundedCurve": lit(3)}, "selector": {"id": "default"}}],
            "fillCustom": [
                {"properties": {"show": lit(True), "fillColor": colour(CARD)}, "selector": {"id": "default"}},
                {"properties": {"show": lit(True), "fillColor": colour(INK)}, "selector": {"id": "selected"}},
                {"properties": {"show": lit(True), "fillColor": colour(PAPER)}, "selector": {"id": "hover"}},
            ],
            "outline": [
                {"properties": {"show": lit(True), "lineColor": colour(RULE), "weight": lit(1.0)}, "selector": {"id": "default"}},
                {"properties": {"show": lit(True), "lineColor": colour(INK), "weight": lit(1.0)}, "selector": {"id": "selected"}},
            ],
            "value": [
                {"properties": {"fontColor": colour(BODY), "fontSize": lit(9.0), "bold": lit(True),
                                "horizontalAlignment": lit("center")}, "selector": {"id": "default"}},
                {"properties": {"fontColor": colour(CARD)}, "selector": {"id": "selected"}},
            ],
            "label": [{"properties": {"show": lit(False)}, "selector": {"id": "default"}}],
            "icon": [{"properties": {"show": lit(False)}, "selector": {"id": "default"}}],
            "selectionIcon": [{"properties": {"show": lit(False)}, "selector": {"id": "default"}}],
        },
        container=container, filters=filters,
    )


def dropdown(name, x, y, w, h, z, table, col, header) -> dict:
    """Classic dropdown slicer. Needs h >= 76 or the validator errors."""
    container = no_chrome()
    container["background"] = obj(show=lit(True), color=colour(CARD), transparency=lit(0.0))
    container["padding"] = obj(top=lit(8.0), bottom=lit(8.0), left=lit(10.0), right=lit(10.0))
    return visual(
        name, "slicer", x, y, w, h, z,
        query={"queryState": {"Values": {"projections": [column(table, col)]}}},
        objects={
            "general": [{"properties": {"orientation": lit(0)}}],
            "data": [{"properties": {"mode": lit("Dropdown")}}],
            "header": [{"properties": {"show": lit(True), "text": lit(header), "textSize": lit(8.5),
                                       "fontColor": colour(MUTED), "bold": lit(True)}}],
            "items": [{"properties": {"fontColor": colour(BODY), "textSize": lit(9.5), "background": colour(CARD)}}],
        },
        container=container,
    )


def action_button(name, x, y, w, h, z, text, bookmark, fill=CARD, text_colour=INK, outline=INK,
                  transparency=0.0) -> dict:
    """A button that applies a bookmark. Its styling objects need a bare entry AND a 'default'
    id entry, or Desktop ignores them. Ctrl+click in Desktop's edit mode, plain click elsewhere."""
    def dual(props):
        return [{"properties": props}, {"properties": props, "selector": {"id": "default"}}]
    objects = {
        "shape": [{"properties": {"tileShape": lit("rectangleRoundedByPixel"), "rectangleRoundedCurve": lit(4)}}],
        "fill": dual({"show": lit(True), "fillColor": colour(fill), "transparency": lit(transparency)}),
        "outline": dual({"show": lit(outline is not None),
                         **({"lineColor": colour(outline), "weight": lit(1.0)} if outline else {})}),
        "icon": dual({"show": lit(False)}),
        "text": dual({"show": lit(text is not None),
                      **({"text": lit(text), "fontColor": colour(text_colour), "fontSize": lit(10.0),
                          "bold": lit(True)} if text else {})}),
    }
    container = no_chrome()
    container["visualLink"] = obj(show=lit(True), type=lit("Bookmark"), bookmark=lit(bookmark))
    node = visual(name, "actionButton", x, y, w, h, z, container=container)
    node["visual"]["objects"] = objects
    node["howCreated"] = "InsertVisualButton"
    return node


def filter_panel(slug: str, bookmark_prefix: str, display: str, page_name: str,
                 slicers: list[tuple[str, str, str, str]]) -> tuple[list[dict], list[dict]]:
    """A hidden filter panel on the right of the canvas and its open/closed bookmarks.

    slicers: (visual name, table, column, header) for each dropdown, stacked 84px apart.
    Returns (visuals, bookmarks). The Filters button that opens it is placed by the page and
    links to f"{bookmark_prefix}Open". No ClearAllSlicers button: it also resets the
    forced-selection toggles, which then jump to their first tile.
    """
    n = len(slicers)
    closed = f"{bookmark_prefix}Closed"
    visuals = [
        action_button(f"vScrim{slug}", 0, 0, CANVAS_W, CANVAS_H, 2000, None, closed,
                      fill=INK, outline=None, transparency=65.0),
        textbox(f"vPanel{slug}", 1076, 118, 340, 104 + 84 * n, 2010, [[{"text": "", "size": 6}]], background=CARD),
        textbox(f"vPanelHead{slug}", 1092, 128, 300, 52, 2020, [
            [{"text": "Filters", "size": 14, "color": INK, "bold": True}],
            [{"text": "Apply to every number on this page, cards included.", "size": 8.5, "color": MUTED}],
        ]),
    ]
    visuals += [dropdown(vname, 1092, 184 + 84 * i, 308, 80, 2030 + 10 * i, table, col, header)
                for i, (vname, table, col, header) in enumerate(slicers)]
    visuals.append(action_button(f"vDone{slug}", 1092, 176 + 84 * n, 308, 34, 2060, "Done", closed,
                                 fill=INK, text_colour=CARD))
    visuals[1]["visual"]["visualContainerObjects"]["border"] = obj(show=lit(True), color=colour(RULE), radius=lit(6))
    for v in visuals:
        v["isHidden"] = True
    return visuals, toggle_bookmarks(page_name, bookmark_prefix, display, visuals)


def toggle_bookmarks(page_name: str, prefix: str, display: str, targets: list[dict]) -> list[dict]:
    """Open and closed bookmarks that show or hide `targets`, carrying no data state - so
    opening a panel never resets a slicer (suppressData), and nothing else on the page moves."""
    names = [t["name"] for t in targets]

    def one(suffix: str, hidden: bool) -> dict:
        containers = {}
        for t in targets:
            single = {"visualType": t["visual"]["visualType"], "objects": {}}
            if hidden:
                single["display"] = {"mode": "hidden"}
            containers[t["name"]] = {"singleVisual": single}
        return {
            "$schema": BOOKMARK_SCHEMA,
            "displayName": f"{display} {suffix.lower()}",
            "name": f"{prefix}{suffix}",
            "options": {"targetVisualNames": names, "applyOnlyToTargetVisuals": True,
                        "suppressData": True, "suppressActiveSection": True},
            "explorationState": {"version": "1.3", "activeSection": page_name,
                                 "sections": {page_name: {"visualContainers": containers}}},
        }

    return [one("Open", False), one("Closed", True)]


def bookmarks_metadata(bookmarks: list[dict]) -> dict:
    return {"$schema": BOOKMARKS_METADATA_SCHEMA, "items": [{"name": b["name"]} for b in bookmarks]}


def axis(gridlines=False, **extra) -> list:
    props = {"show": lit(True), "showAxisTitle": lit(False), "fontSize": lit(8.5),
             "labelColor": colour(MUTED), "gridlineShow": lit(gridlines)}
    if gridlines:
        props["gridlineColor"] = colour(RULE)
    props.update(extra)
    return [{"properties": props}]


def pivot_objects(image_width: float | None = 120.0) -> dict:
    """House pivotTable formatting (add 'values' with Fields.values_with_colour). A pivotTable,
    not tableEx: a generated tableEx drops its measures. image_width sizes SVG cells - 120 left
    room for 'Amortization of Intangible Assets' in a 688px panel where 160 scrolled."""
    grid = {"gridVertical": lit(False), "gridHorizontal": lit(True), "gridHorizontalColor": colour(RULE),
            "rowPadding": lit(2)}
    if image_width:
        grid.update(imageHeight=lit(16.0), imageWidth=lit(image_width))
    return {
        "grid": [{"properties": grid}],
        "columnHeaders": [{"properties": {"fontSize": lit(9.0), "bold": lit(True), "fontColor": colour(INK),
                                          "backColor": colour(CARD), "alignment": lit("Right")}}],
        "rowHeaders": [{"properties": {"fontSize": lit(9.0), "fontColor": colour(BODY), "backColor": colour(CARD)}}],
        "subTotals": [{"properties": {"rowSubtotals": lit(False), "columnSubtotals": lit(False)}}],
    }


# ============================================================================================
# MODEL (DAX + TMDL)
# ============================================================================================


def svg_uri(body: str = "Svg") -> str:
    """The encoding every SVG measure ends with. '%' before '#': unencoded, "3.6%" is read as an
    escape and '#' ends the URI; in the other order '%23' becomes '%2523'."""
    return f'"data:image/svg+xml;utf8," & SUBSTITUTE(SUBSTITUTE({body}, "%", "%25"), "#", "%23")'


def money(expr: str, signed: bool = False, thousands: bool = False) -> str:
    """DAX text for '$1,234' / '$1,234k', with a '+' when signed. The minus is an entity so the
    SVG text stays ASCII inside the data URI."""
    body = f'FORMAT(ABS({expr}) / 1000, "#,0") & "k"' if thousands else f'FORMAT(ABS({expr}), "#,0")'
    if signed:
        return f'IF({expr} < 0, "&#8722;$", "+$") & {body}'
    return f'IF({expr} < 0, "&#8722;$", "$") & {body}'


def pct(expr: str, cap: float | None = None) -> str:
    """DAX text for '+3.6%', 'new' when blank; with cap=10, '>+999%' past ten times."""
    core = f'IF({expr} < 0, "&#8722;", "+") & FORMAT(ABS({expr}), "0.0%")'
    if cap:
        core = f'IF(ABS({expr}) >= {cap}, IF({expr} > 0, ">+999%", "<-999%"), {core})'
    return f'IF(ISBLANK({expr}), "new", {core})'


def pp(a: str, b: str) -> str:
    """DAX text for a move between two ratios in percentage points."""
    return f'IF({a} - {b} < 0, "&#8722;", "+") & FORMAT(ABS({a} - {b}) * 100, "0.0") & "pp"'


def tone(expr: str, higher_is_better: bool = True) -> str:
    """Green at or above zero, red below - only ever for a comparison, never for magnitude.
    higher_is_better=False flips it for measures where a rise is bad (leavers, turnover, cost):
    red above zero, green at or below."""
    if not higher_is_better:
        return f'IF({expr} > 0, "{BAD}", "{GOOD}")'
    return f'IF({expr} >= 0, "{GOOD}", "{BAD}")'


def escape_xml(expr: str) -> str:
    return f'SUBSTITUTE(SUBSTITUTE({expr}, "&", "&amp;"), "<", "&lt;")'


def ring(cx: int, cy: int, r: int, share: str, fill: str = NAVY, over: str | None = None) -> str:
    """SVG ring gauge: grey track, `share` (0-1) in `fill`, optional `over` share in gold."""
    def arc(s: str, col: str) -> str:
        return (
            f'IF({s} > 0, "<path d=\'M{cx},{cy - r} A{r},{r} 0 " & IF({s} > 0.5, "1", "0") & " 1 " & '
            f'FORMAT({cx} + {r} * COS(2 * PI() * MIN({s}, 0.9999) - PI() / 2), "0.00") & "," & '
            f'FORMAT({cy} + {r} * SIN(2 * PI() * MIN({s}, 0.9999) - PI() / 2), "0.00") & '
            f'"\' stroke=\'{col}\' stroke-width=\'7\' fill=\'none\'/>")'
        )
    out = f'"<circle cx=\'{cx}\' cy=\'{cy}\' r=\'{r}\' stroke=\'{RULE}\' stroke-width=\'7\' fill=\'none\'/>" & {arc(share, fill)}'
    if over:
        out += f" & {arc(over, GOLD)}"
    return out


def ring_label(cx: int, cy: int, text: str) -> str:
    return f'"<text x=\'{cx}\' y=\'{cy + 5}\' font-size=\'13\' font-weight=\'700\' text-anchor=\'middle\' fill=\'{INK}\'>" & {text} & "</text>"'


def card(label: str, value: str, note: str, row1: tuple, row2: tuple, graphic: str) -> str:
    """The 336x140 KPI card frame as DAX text. Arguments are DAX text expressions; each row is
    (left text, right text, right colour). `graphic` fills the top-right corner."""
    def row(y: int, r: tuple) -> str:
        return (f'"<text x=\'16\' y=\'{y}\' font-size=\'11.5\' fill=\'{BODY}\'>" & {r[0]} & "</text>'
                f'<text x=\'320\' y=\'{y}\' font-size=\'11.5\' font-weight=\'600\' text-anchor=\'end\' fill=\'" & {r[2]} & "\'>" & {r[1]} & "</text>"')
    return "\n".join([
        f'"<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'336\' height=\'140\' viewBox=\'0 0 336 140\' font-family=\'Segoe UI, sans-serif\'>"',
        f'& "<rect x=\'0.5\' y=\'0.5\' width=\'335\' height=\'139\' rx=\'4\' fill=\'#FFFFFF\' stroke=\'{RULE}\'/><rect width=\'3\' height=\'140\' fill=\'{GOLD}\'/>"',
        f'& "<text x=\'16\' y=\'24\' font-size=\'11\' font-weight=\'700\' fill=\'{MUTED}\' letter-spacing=\'0.4\'>" & {label} & "</text>"',
        f'& "<text x=\'16\' y=\'58\' font-size=\'28\' font-weight=\'700\' fill=\'{INK}\'>" & {value} & "</text>"',
        f'& "<text x=\'16\' y=\'76\' font-size=\'11.5\' fill=\'{MUTED}\'>" & {note} & "</text>"',
        f'& "<line x1=\'16\' y1=\'88\' x2=\'320\' y2=\'88\' stroke=\'{RULE}\'/>"',
        f"& {row(107, row1)}",
        f"& {row(127, row2)}",
        f"& {graphic}",
        '& "</svg>"',
    ])


def empty_card(label: str, message: str) -> str:
    """The card shown when there is nothing to compare with - never a blank image. `message` is
    DAX string content, so it may splice a measure in with '" & [Measure] & "'."""
    return (f'"<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'336\' height=\'140\' viewBox=\'0 0 336 140\' font-family=\'Segoe UI, sans-serif\'>'
            f'<rect x=\'0.5\' y=\'0.5\' width=\'335\' height=\'139\' rx=\'4\' fill=\'#FFFFFF\' stroke=\'{RULE}\'/>'
            f'<text x=\'16\' y=\'24\' font-size=\'11\' font-weight=\'700\' fill=\'{MUTED}\'>{label}</text>'
            f'<text x=\'16\' y=\'64\' font-size=\'13\' fill=\'{BODY}\'>{message}</text></svg>"')


def diverging_bar(change: str, scale: str, show_when: str, width: int = 120,
                  higher_is_better: bool = True) -> str:
    """DAX for a table-cell SVG bar: zero line in the middle, rises right and falls left, scaled
    to `scale`, coloured by tone() - pass higher_is_better=False when a rise is bad.
    Mark the measure dataCategory ImageUrl and set the pivot's image width to `width`.
    Build `scale` from a pool that suits the table: ALL(column) when the table has a measure
    filter, ALLSELECTED(column) when a slicer targets the column."""
    half = width // 2
    return f"""
VAR Change = {change}
VAR Scale = {scale}
VAR W = DIVIDE(ABS(Change), Scale) * {half - 4}
VAR X = IF(Change >= 0, {half}, {half} - W)
VAR Svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='16' viewBox='0 0 {width} 16'>"
        & "<line x1='{half}' y1='0' x2='{half}' y2='16' stroke='{MUTED}'/>"
        & "<rect x='" & FORMAT(X, "0.0") & "' y='3' width='" & FORMAT(W, "0.0") & "' height='10' fill='" & {tone("Change", higher_is_better)} & "'/>"
        & "</svg>"
RETURN
    IF({show_when}, {svg_uri()})"""


def rank_measure(direction: str, default: str, pool: str, value: str, valid: str) -> str:
    """DAX for a rank that counts from the top or the bottom as a toggle says.

    direction: the toggle column, e.g. 'Account Ranking'[Show]; default: "Top" or "Bottom";
    pool: the table expression ranked over; value: the measure ranked; valid: the condition for
    a row to get a rank at all (text, may span lines). Pair with Fields.range_filter.
    """
    return f"""
VAR Direction = SELECTEDVALUE({direction}, "{default}")
VAR Pool = {pool}
VAR Me = {value}
RETURN
    IF(
        {valid},
        IF(
            Direction = "Top",
            COUNTROWS(FILTER(Pool, {value} > Me)) + 1,
            COUNTROWS(FILTER(Pool, {value} < Me)) + 1
        )
    )"""


def indent(text: str, n: int = 1) -> str:
    return "\n".join(("    " * n + line) if line else line for line in text.split("\n"))


# ---- TMDL writers -------------------------------------------------------------------------


def tagger(namespace: str, prefix: str):
    """Stable lineage tags: uuid5 of the object's path, so a re-run never churns them."""
    ns = uuid.UUID(namespace)
    return lambda *parts: str(uuid.uuid5(ns, prefix + ":" + ":".join(parts)))


def q(name: str) -> str:
    """Quote a TMDL identifier when it needs it."""
    return name if name.replace("_", "").isalnum() else f"'{name}'"


def doc(text: str | None, pad: str) -> list[str]:
    return [f"{pad}/// {p}".rstrip() for p in text.split("\n")] if text else []


def measure(name, dax, fmt=None, doc_text=None, category=None, hidden=False) -> dict:
    """A measure spec for measure_table_tmdl. category 'ImageUrl' for SVG measures."""
    return dict(name=name, dax=dax.strip("\n"), fmt=fmt, doc=doc_text, category=category, hidden=hidden)


def measure_table_tmdl(entity: str, specs: list[dict], tag, table_doc: str) -> list[str]:
    """A measure-only table with a hidden placeholder column and a calculated partition. No
    blank lines inside an expression block: TMDL rejects them."""
    lines = doc(table_doc, "") + [f"table {q(entity)}", f"\tlineageTag: {tag('table', entity)}"]
    for s in specs:
        lines.append("")
        lines += doc(s["doc"], "\t")
        body = s["dax"].split("\n")
        if len(body) == 1:
            lines.append(f"\tmeasure {q(s['name'])} = {body[0]}")
        else:
            lines.append(f"\tmeasure {q(s['name'])} =")
            lines += [("\t\t\t" + b) if b.strip() else "\t\t\t" for b in body]
        if s["fmt"]:
            lines.append(f"\t\tformatString: {s['fmt']}")
        if s["hidden"]:
            lines.append("\t\tisHidden")
        if s["category"]:
            lines.append(f"\t\tdataCategory: {s['category']}")
        lines.append(f"\t\tlineageTag: {tag('measure', s['name'])}")
    lines += [
        "",
        "\tcolumn Placeholder",
        "\t\tdataType: string",
        "\t\tisHidden",
        f"\t\tlineageTag: {tag('column', entity, 'Placeholder')}",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: [Placeholder]",
        "",
        f"\tpartition {q(entity)} = calculated",
        "\t\tmode: import",
        "\t\tsource = ROW(\"Placeholder\", \"\")",
    ]
    return lines


def disconnected_table_tmdl(name: str, table_doc: str, columns: list, rows: list, tag) -> list[str]:
    """A small typed table with no relationships, held inline in M, for one button slicer.
    columns: (name, 'string' | 'int64', sortByColumn or None); int columns are hidden."""
    lines = doc(table_doc, "") + [f"table {q(name)}", f"\tlineageTag: {tag('table', name)}"]
    for col, dtype, sort_by_col in columns:
        lines += ["", f"\tcolumn {q(col)}", f"\t\tdataType: {dtype}"]
        if dtype == "int64":
            lines += ["\t\tisHidden", "\t\tformatString: 0"]
        lines += [f"\t\tlineageTag: {tag('column', name, col)}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {col}"]
        if sort_by_col:
            lines.append(f"\t\tsortByColumn: {q(sort_by_col)}")
    fields = ", ".join((c if c.isidentifier() else f'#"{c}"') + (" = Int64.Type" if t == "int64" else " = text")
                       for c, t, _ in columns)
    data = ", ".join("{" + ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in r) + "}" for r in rows)
    lines += [
        "",
        f"\tpartition {q(name)} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        f"\t\t\t\t    Source = #table(type table [{fields}], {{{data}}})",
        "\t\t\t\tin",
        "\t\t\t\t    Source",
        "",
        "\tannotation PBI_ResultType = Table",
    ]
    return lines


def write_lines(path: Path, lines: list[str]) -> None:
    """LF endings, UTF-8 without a BOM - a BOM breaks .platform and PBIR parsing."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def add_table_refs(model_tmdl: Path, names: list[str], after: str) -> None:
    """Insert `ref table` lines for any of `names` missing from model.tmdl, after the line `after`."""
    text = model_tmdl.read_text(encoding="utf-8")
    missing = [n for n in names if f"ref table {q(n)}" not in text]
    if not missing:
        return
    text = text.replace(after, after + "\n" + "\n".join(f"ref table {q(n)}" for n in missing), 1)
    model_tmdl.write_text(text, encoding="utf-8", newline="\n")
