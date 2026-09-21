"""milestone_calendar - a heat-mapped calendar page for any Milestone report, from one config.

Canonical copy: ~/.claude/skills/milestone-report-design/assets/lib/milestone_calendar.py.
Vendor it into a project's etl/ next to milestone_pbir.py, which it is built on.

The page is one calendar at four grains - Day, Month, Quarter, Year - each its own matrix, swapped
by four bookmarks. Proven first by hand on Superstore page 06 (patterns.md section 6); this file
is that page with the report-specific parts lifted into `Config`.

It is self-contained on the model side: it needs a date table with a date column and one base
measure, and nothing else. Its axes are calculated columns it adds itself ('Cal ...'), its
measures live in their own table, and the year-earlier comparison is a plain filter swap, so it
works on a date table that is not marked.

    # build_model.py, after every table and model.tmdl are written
    milestone_calendar.install_model(DEFN, CALENDAR, tag)

    # build_report.py
    pg, visuals, bookmarks = milestone_calendar.build_page(CALENDAR)

Three things to know before editing:

* Shade is a rank, not a scale: five bands by position among the cells on screen, in integer
  arithmetic so DAX and the pandas answer key cannot disagree over a float on a boundary.
* A cell is ranked against ALLSELECTED(date table): the matrix's own axes come off, slicers stay.
* The Month and Year dropdowns mean nothing at some grains, and a slicer hidden by a bookmark
  still filters. Matrices and bar charts are cut off with visualInteractions; measure-only
  visuals also take the filters off in DAX (`_scoped`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import milestone_icons
import milestone_pbir as mb
from milestone_pbir import (BAD, BODY, CARD, GOLD, GOOD, INK, MUTED, NAVY, PAPER, RULE, SLATE,
                            colour, column, lit, obj)

VERSION = "1.0.0"

BANDS = ["#E6E9F1", "#BCC1D2", "#7C8598", "#3E4A66", "#111F38"]   # quiet -> busy; never green/red
DARK_FROM = 3                                                      # bands from here take white text
VIEWS = ["Day", "Month", "Quarter", "Year"]
CELL = {"Day": (112, 88), "Month": (266, 138), "Quarter": (196, 138), "Year": (196, 230)}
CARD_W, CARD_H = 476, 152
ENTITY = "Calendar Metrics"


@dataclass
class Config:
    value: str                       # base measure, e.g. "[Sales]"
    value_noun: str                  # "sales", "incidents", "late orders" - lower case, for sentences
    money: bool = True               # $-prefixed short format, or a plain count
    count: str | None = None         # second measure shown bottom right of a cell, e.g. "[Orders]"
    count_one: str = "order"
    count_many: str = "orders"
    count_join: str = " from "       # "$83k in sales from 223 orders"; ", with " suits "incidents, with 49 killed"
    date_table: str = "Date"
    date_col: str = "Date"
    title: str = "Sales calendar"
    ref: str = "06 / CALENDAR"
    slug: str = "Cal"
    page_name: str = "pgCalendar"
    page_display: str = "Calendar"
    card_label: str = "SALES IN VIEW"
    total_icon: str = "coin"         # milestone_icons name drawn beside the total; "bars" suits a count
    peak_icon: str = "calendar-check"  # and beside the busiest day, month, quarter or year
    peak_word: str = "Busiest"       # "Busiest day", "Biggest month" use their own words below
    big_word: str = "Biggest"
    active_day: str = "trading day"  # a day on which the value is above zero
    none_note: str = "no orders"     # legend text for the dash
    default_month: str = "December"
    default_year: int = 2024
    years: int = 4                   # years of data: sizes the Quarter rows and the Year columns
    higher_is_better: bool = True    # False paints a rise red: incidents, late orders
    masthead: bool = True            # False: a plain title, for a report without the brand band
    # Token overrides for a report that is not in the Milestone palette, e.g.
    # {"PAPER": "#F7F6F3", "NAVY": "#45617C", "BANDS": [...five hex, low to high...]}
    skin: dict = field(default_factory=dict)

    # ---- names ----------------------------------------------------------------------------------
    def col(self, name: str) -> str:
        return f"'{self.date_table}'[{name}]"

    @property
    def d(self) -> str:
        return self.col(self.date_col)


# ================================================================================================
# MODEL
# ================================================================================================


def cell_size(cfg: Config, view: str) -> tuple[int, int]:
    """The SVG cell for a view. Quarter has a row per year and Year a column per year, so both
    shrink to keep a long history on the panel without scrolling under the legend."""
    w, h = CELL[view]
    if view == "Quarter":
        h = max(44, min(h, 590 // max(cfg.years, 1) - 3))
    if view == "Year":
        w = max(60, min(w, 870 // max(cfg.years, 1) - 13))
    return w, h


_HIGHER_IS_BETTER = True


def _apply(cfg: Config) -> None:
    """Point this module and milestone_pbir at the config's palette. Both read their tokens as
    module globals at call time, so this is all a skin needs."""
    global _HIGHER_IS_BETTER
    _HIGHER_IS_BETTER = cfg.higher_is_better
    for name, value in cfg.skin.items():
        globals()[name] = value
        if hasattr(mb, name):
            setattr(mb, name, value)


def date_columns(cfg: Config) -> list[tuple]:
    """(name, dax, dtype, format, sortBy, doc). All prefixed 'Cal ' so they cannot collide."""
    d = cfg.d
    return [
        ("Cal Year", f"YEAR({d})", "int64", "0", None, None),
        ("Cal Month No", f"MONTH({d})", "int64", "0", None, None),
        ("Cal Month", f'FORMAT({d}, "mmmm")', "string", None, "Cal Month No", "Month name, January to December."),
        ("Cal Month Short", f'FORMAT({d}, "mmm")', "string", None, "Cal Month No", None),
        ("Cal Quarter", f'"Q" & ROUNDUP(MONTH({d}) / 3, 0)', "string", None, None, None),
        ("Cal Quarter Key", f"YEAR({d}) * 10 + ROUNDUP(MONTH({d}) / 3, 0)", "int64", "0", None, None),
        ("Cal Quarter Label", f'"Q" & ROUNDUP(MONTH({d}) / 3, 0) & " " & YEAR({d})', "string", None,
         "Cal Quarter Key", None),
        ("Cal Month in Quarter No", f"MOD(MONTH({d}) - 1, 3) + 1", "int64", "0", None, None),
        ("Cal Month in Quarter",
         f'SWITCH(MOD(MONTH({d}) - 1, 3) + 1, 1, "1st month", 2, "2nd month", "3rd month")',
         "string", None, "Cal Month in Quarter No", "The Month view's column headers."),
        ("Cal Weekday No", f"WEEKDAY({d}, 2)", "int64", "0", None, "Monday = 1."),
        ("Cal Day Short", f'FORMAT({d}, "ddd")', "string", None, "Cal Weekday No",
         "Mon to Sun, in that order - the Day view's column headers."),
        ("Cal Day Name", f'FORMAT({d}, "dddd")', "string", None, "Cal Weekday No", None),
        ("Cal Week of Month",
         f"INT((DAY({d}) + WEEKDAY(DATE(YEAR({d}), MONTH({d}), 1), 2) - 2) / 7) + 1",
         "int64", "0", None, "Which row of a Monday-first month grid the day sits on: 1 to 6."),
        # The Day matrix needs a row field to break the month into weeks, and nobody wants to read
        # it. A white font hid it under the Milestone theme and not under Gun Violence's, so the
        # label itself is invisible: n zero-width spaces, distinct per week, sorted by the number.
        ("Cal Week Row",
         f"REPT(UNICHAR(8203), INT((DAY({d}) + WEEKDAY(DATE(YEAR({d}), MONTH({d}), 1), 2) - 2) / 7) + 1)",
         "string", None, "Cal Week of Month", None),
    ]


def _short(cfg: Config, v: str) -> str:
    """$1.23M / $12.3k / $1.23k / $123 for money; 1.23M / 12.3k / 1,234 for counts."""
    p = '"$" & ' if cfg.money else ""
    small = f'{p}FORMAT({v}, "0")' if cfg.money else f'FORMAT({v}, "#,0")'
    mid = (f'{v} >= 1000, {p}FORMAT({v} / 1000, "0.00") & "k", ' if cfg.money else "")
    return (f'SWITCH(TRUE(), {v} >= 1000000, {p}FORMAT({v} / 1000000, "0.00") & "M", '
            f'{v} >= 10000, {p}FORMAT({v} / 1000, "0.0") & "k", {mid}{small})')


def _phrase(cfg: Config, v: str) -> str:
    """'$117.9k in sales' / '4,512 incidents' as a DAX text expression."""
    return f'{_short(cfg, v)} & " {"in " if cfg.money else ""}{cfg.value_noun}"'


def _scope_cols(cfg: Config, view: str) -> list[str]:
    month, year = cfg.col("Cal Month"), cfg.col("Cal Year")
    return {"Day": [], "Month": [month], "Quarter": [month, year], "Year": [month, year]}[view]


def _scoped(cfg: Config, expr: str, view: str) -> str:
    cols = _scope_cols(cfg, view)
    return f"CALCULATE({expr}, REMOVEFILTERS({', '.join(cols)}))" if cols else expr


def _grain(cfg: Config, view: str) -> str:
    return {"Day": cfg.d, "Month": cfg.col("Cal Month No"), "Quarter": cfg.col("Cal Quarter Key"),
            "Year": cfg.col("Cal Year")}[view]


def _top(cfg: Config, view: str) -> str:
    return {"Day": f'FORMAT(DAY(SELECTEDVALUE({cfg.d})), "00")',
            "Month": f"SELECTEDVALUE({cfg.col('Cal Month Short')})",
            "Quarter": f"SELECTEDVALUE({cfg.col('Cal Quarter Label')})",
            "Year": f'FORMAT(SELECTEDVALUE({cfg.col("Cal Year")}), "0")'}[view]


def _tspan(change: str) -> str:
    up, down = (GOOD, BAD) if _HIGHER_IS_BETTER else (BAD, GOOD)
    return (f'"<tspan font-weight=\'700\' fill=\'" & IF({change} >= 0, "{up}", "{down}") & "\'>" & '
            f'IF({change} >= 0, "&#9650; ", "&#9660; ") & FORMAT(ABS({change}), "0.0%") & "</tspan>"')


def _words(change: str) -> str:
    return f'IF({change} >= 0, "up ", "down ") & FORMAT(ABS({change}), "0.0%")'


def _card(label: str, value: str, note: str, rows: list[tuple[str, str]], icon: str | None = None) -> str:
    """KPI card frame, 476x152. Arguments are DAX text expressions; `note` may carry <tspan>.
    `icon` names a milestone_icons icon, drawn left of the value, which moves right to clear it."""
    value_x = 56 if icon else 16
    ys = [111, 128, 145] if len(rows) == 3 else [114, 134]
    body = [
        f'"<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'{CARD_W}\' height=\'{CARD_H}\' viewBox=\'0 0 {CARD_W} {CARD_H}\' font-family=\'Segoe UI, sans-serif\'>"',
        f'& "<rect x=\'0.5\' y=\'0.5\' width=\'{CARD_W - 1}\' height=\'{CARD_H - 1}\' rx=\'4\' fill=\'#FFFFFF\' stroke=\'{RULE}\'/><rect width=\'3\' height=\'{CARD_H}\' fill=\'{GOLD}\'/>"',
        f'& "<text x=\'16\' y=\'24\' font-size=\'11\' font-weight=\'700\' fill=\'{MUTED}\' letter-spacing=\'0.4\'>" & {label} & "</text>"',
        *([f'& "<g transform=\'translate(16 33) scale(0.625)\'>{milestone_icons.markup(icon)}</g>"'] if icon else []),
        f'& "<text x=\'{value_x}\' y=\'58\' font-size=\'28\' font-weight=\'700\' fill=\'{INK}\'>" & {value} & "</text>"',
        f'& "<text x=\'16\' y=\'77\' font-size=\'11.5\' fill=\'{MUTED}\'>" & {note} & "</text>"',
        f'& "<line x1=\'16\' y1=\'90\' x2=\'{CARD_W - 16}\' y2=\'90\' stroke=\'{RULE}\'/>"',
    ]
    for y, (left, right) in zip(ys, rows):
        body.append(f'& "<text x=\'16\' y=\'{y}\' font-size=\'11.5\' fill=\'{BODY}\'>" & {left} & "</text>'
                    f'<text x=\'{CARD_W - 16}\' y=\'{y}\' font-size=\'11.5\' font-weight=\'600\' text-anchor=\'end\' fill=\'{INK}\'>" & {right} & "</text>"')
    body.append('& "</svg>"')
    return "\n".join(body)


def _cell_measures(cfg: Config, view: str) -> list[dict]:
    w, h = cell_size(cfg, view)
    grain = _grain(cfg, view)
    pool = f'CALCULATETABLE(ADDCOLUMNS(VALUES({grain}), "@v", {cfg.value}), ALLSELECTED(\'{cfg.date_table}\'))'
    fills = ", ".join(f'{i + 1}, "{c}"' for i, c in enumerate(BANDS))
    big = 17 if (w >= 100 and h >= 70) else 14
    sub = ""
    if cfg.count and w >= 110:
        sub = (f'\n                & "<text x=\'{w - 8}\' y=\'{h - 8}\' font-size=\'10.5\' text-anchor=\'end\' fill=\'" & Fg & "\' opacity=\'0.8\'>"'
               f' & FORMAT(Cnt, "#,0") & IF(Cnt = 1, " {cfg.count_one}", " {cfg.count_many}") & "</text>"')
    return [
        mb.measure(f"Cal Band {view}", f"""
VAR Me = {cfg.value}
VAR Live = FILTER({pool}, [@v] > 0)
VAR N = COUNTROWS(Live)
VAR Below = COUNTROWS(FILTER(Live, [@v] < Me))
RETURN
    IF(
        HASONEVALUE({grain}),
        IF(Me > 0, IF(N <= 1, 5, MIN(5, INT(DIVIDE((Below + 0) * 5, N - 1)) + 1)), 0)
    )""", "0",
                   "1 (lowest fifth of the cells on screen) to 5 (highest); 0 for an empty cell; blank\n"
                   "where the grid has no such cell. Rank-based so one outlier cannot flatten the rest."),
        mb.measure(f"Cal Colour {view}", f"""
VAR Band = [Cal Band {view}]
RETURN
    IF(NOT ISBLANK(Band), SWITCH(Band, {fills}, "{PAPER}"))""", None,
                   "Cell background for the band. Bound to the matrix's background colour."),
        mb.measure(f"Cal Cell {view}", f"""
VAR Band = [Cal Band {view}]
VAR V = {cfg.value}
VAR Cnt = {cfg.count or "BLANK()"}
VAR Fg = SWITCH(TRUE(), Band = 0, "{MUTED}", Band >= {DARK_FROM}, "#FFFFFF", "{INK}")
VAR Bg = [Cal Colour {view}]
VAR Svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}' font-family='Segoe UI, sans-serif'>"
        & "<rect width='{w}' height='{h}' fill='" & Bg & "'/>"
        & "<text x='8' y='17' font-size='11.5' font-weight='600' fill='" & Fg & "' opacity='0.85'>" & {_top(cfg, view)} & "</text>"
        & IF(
            Band = 0,
            "<text x='{w // 2}' y='{h // 2 + 5}' font-size='13' text-anchor='middle' fill='{MUTED}'>&#8211;</text>",
            "<text x='{w // 2}' y='{h // 2 + 6}' font-size='{big}' font-weight='700' text-anchor='middle' fill='" & Fg & "'>" & {_short(cfg, 'V')} & "</text>"{sub}
        )
        & "</svg>"
RETURN
    IF(NOT ISBLANK(Band), {mb.svg_uri()})""", None,
                   f"One calendar cell at the {view} grain, drawn at {w}x{h}.", category="ImageUrl"),
    ]


def measures(cfg: Config) -> list[dict]:
    _apply(cfg)
    out: list[dict] = []
    for view in VIEWS:
        out += _cell_measures(cfg, view)

    V, T = cfg.value, cfg.date_table
    year, month_no, month = cfg.col("Cal Year"), cfg.col("Cal Month No"), cfg.col("Cal Month")
    out += [
        mb.measure("Cal Active Days", f"COUNTROWS(FILTER(VALUES({cfg.d}), {V} > 0))", "#,0",
                   f"Days on which {cfg.value_noun} is above zero."),
        mb.measure("Cal Avg per Active Day", f"DIVIDE({V}, [Cal Active Days])", r"\$#,0" if cfg.money else "#,0.0",
                   f"{cfg.value_noun.capitalize()} divided by the days that had any."),
        mb.measure("Cal Weekday Colour",
                   f'IF(SELECTEDVALUE({cfg.col("Cal Weekday No")}) >= 6, "{SLATE}", "{NAVY}")', None,
                   "Weekends in slate on the weekday bars."),
        mb.measure("Cal Month Label",
                   f'SELECTEDVALUE({month}, "All months") & " " & SELECTEDVALUE({year}, "all years")'),
        # A plain filter swap rather than DATEADD, so it works on a date table that is not marked.
        # Both stop a year before the last date in view: where the data ends part-way through a
        # month or a year, the comparison is with the same span of days, not the whole period.
        mb.measure("Cal Value Month PY", f"""
VAR Y = SELECTEDVALUE({year})
VAR Mn = SELECTEDVALUE({month_no})
VAR Cap = EDATE(MAX({cfg.d}), -12)
RETURN
    IF(NOT ISBLANK(Y) && NOT ISBLANK(Mn), CALCULATE({V}, REMOVEFILTERS('{T}'), {year} = Y - 1, {month_no} = Mn, {cfg.d} <= Cap))""",
                   None, "The same days of the same month a year earlier."),
        mb.measure("Cal Value Year PY", f"""
VAR Y = SELECTEDVALUE({year})
VAR Cap = EDATE(MAX({cfg.d}), -12)
RETURN
    IF(NOT ISBLANK(Y), CALCULATE({V}, REMOVEFILTERS('{T}'), {year} = Y - 1, {cfg.d} <= Cap))""", None,
                   "The year before, up to the same day of the year - so a part year is compared with\n"
                   "the same part of the year before."),
        mb.measure("Cal Year Is Whole", f"""
VAR Y = SELECTEDVALUE({year})
RETURN
    CALCULATE(COUNTROWS('{T}'), REMOVEFILTERS('{T}'), {year} = Y) >= 365""", None,
                   "False where the date table covers only part of the year on screen."),
    ]

    rows2 = [('"' + cfg.count_many.capitalize() + '"', f'FORMAT({cfg.count}, "#,0")')] if cfg.count else []

    def total_card(name: str, view: str, prev: str, prev_label: str) -> dict:
        rows = rows2 + [(f'"Average per {cfg.active_day}"', _short(cfg, "[Cal Avg per Active Day]"))]
        if len(rows) == 1:
            rows.append(('"Days in view"', f'FORMAT(COUNTROWS(VALUES({cfg.d})), "#,0")'))
        return mb.measure(name, _scoped(cfg, f"""
VAR Cur = {V}
VAR Prev = {prev}
VAR Change = DIVIDE(Cur - Prev, Prev)
VAR NoteText =
    IF(
        Prev > 0,
        {_tspan('Change')} & " on " & {prev_label} & " (" & {_short(cfg, 'Prev')} & ")",
        "Nothing a year earlier to compare with"
    )
VAR Svg =
{mb.indent(_card(f'"{cfg.card_label}"', _short(cfg, 'Cur'), 'NoteText', rows, icon=cfg.total_icon), 1)}
RETURN
    {mb.svg_uri()}""", view), None, None, category="ImageUrl")

    out.append(total_card("Card Cal Total Day", "Day", "[Cal Value Month PY]",
                          f'SELECTEDVALUE({cfg.col("Cal Month Short")}) & " " & (SELECTEDVALUE({year}) - 1)'))
    out.append(total_card("Card Cal Total Month", "Month", "[Cal Value Year PY]",
                          f'IF([Cal Year Is Whole], "", "the same period of ") & (SELECTEDVALUE({year}) - 1)'))
    rows_all = rows2 + [(f'"Average per {cfg.active_day}"', _short(cfg, "[Cal Avg per Active Day]"))]
    if len(rows_all) == 1:
        rows_all.append(('"Days in view"', f'FORMAT(COUNTROWS(VALUES({cfg.d})), "#,0")'))
    out.append(mb.measure("Card Cal Total All", _scoped(cfg, f"""
VAR Cur = {V}
VAR YearCount = COUNTROWS(FILTER(VALUES({year}), {V} > 0))
VAR Svg =
{mb.indent(_card(f'"{cfg.card_label}"', _short(cfg, 'Cur'), '"All " & YearCount & " years - nothing earlier to compare with"', rows_all, icon=cfg.total_icon), 1)}
RETURN
    {mb.svg_uri()}""", "Quarter"), None, None, category="ImageUrl"))

    # ---- peak cards -----------------------------------------------------------------------------
    out.append(mb.measure("Card Cal Peak Day", f"""
VAR Pool = ADDCOLUMNS(VALUES({cfg.d}), "@v", {V})
VAR Best = MAXX(Pool, [@v])
VAR BestDate = MINX(FILTER(Pool, [@v] = Best), {cfg.d})
VAR DaysAll = COUNTROWS(Pool)
VAR Quiet = DaysAll - [Cal Active Days]
VAR Svg =
{mb.indent(_card(f'"{cfg.peak_word.upper()} DAY"', _short(cfg, 'Best'), 'FORMAT(BestDate, "ddd d mmmm yyyy")',
                 [(f'"Average per {cfg.active_day}"', _short(cfg, '[Cal Avg per Active Day]')),
                  (f'"Days with {cfg.none_note}"', 'Quiet & " of " & DaysAll'),
                  (f'"{cfg.peak_word} day&apos;s share of the month"', f'FORMAT(DIVIDE(Best, {V}), "0.0%")')], icon=cfg.peak_icon), 1)}
RETURN
    {mb.svg_uri()}""", None, None, category="ImageUrl"))

    m_short = cfg.col("Cal Month Short")
    out.append(mb.measure("Card Cal Peak Month", _scoped(cfg, f"""
VAR Pool = FILTER(ADDCOLUMNS(SUMMARIZE('{T}', {month_no}, {month}, {m_short}), "@v", {V}), [@v] > 0)
VAR Best = MAXX(Pool, [@v])
VAR Worst = MINX(Pool, [@v])
VAR BestName = MINX(FILTER(Pool, [@v] = Best), {month})
VAR WorstName = MINX(FILTER(Pool, [@v] = Worst), {m_short})
VAR Svg =
{mb.indent(_card(f'"{cfg.big_word.upper()} MONTH"', _short(cfg, 'Best'), f'BestName & " " & SELECTEDVALUE({year})',
                 [('"Average per month"', _short(cfg, f'DIVIDE({V}, COUNTROWS(Pool))')),
                  ('"Smallest month"', 'WorstName & " &#183; " & ' + _short(cfg, 'Worst')),
                  (f'"{cfg.big_word} month&apos;s share of the year" & IF([Cal Year Is Whole], "", " to date")', f'FORMAT(DIVIDE(Best, {V}), "0.0%")')], icon=cfg.peak_icon), 1)}
RETURN
    {mb.svg_uri()}""", "Month"), None, None, category="ImageUrl"))

    qk, ql, qn = cfg.col("Cal Quarter Key"), cfg.col("Cal Quarter Label"), cfg.col("Cal Quarter")
    whole = f"VAR Yr = {year} RETURN CALCULATE(COUNTROWS('{T}'), REMOVEFILTERS('{T}'), {year} = Yr) >= 365"
    # Which quarter carries most is a whole-years question: a history that stops in May has one
    # more Q1 and Q2 than Q3 and Q4, and "Q2 carries 26%" was that, not seasonality.
    whole_vars = f"""
VAR WholeYears = FILTER(VALUES({year}), {whole})
VAR PartYears = COUNTROWS(FILTER(VALUES({year}), {V} > 0)) - COUNTROWS(FILTER(WholeYears, {V} > 0))
VAR WholeValue = CALCULATE({V}, WholeYears)"""
    out.append(mb.measure("Card Cal Peak Quarter", _scoped(cfg, f"""
VAR Pool = FILTER(ADDCOLUMNS(SUMMARIZE('{T}', {qk}, {ql}), "@v", {V}), [@v] > 0)
VAR Best = MAXX(Pool, [@v])
VAR BestName = MINX(FILTER(Pool, [@v] = Best), {ql})
VAR AllValue = {V}{whole_vars}
VAR ShareNote = IF(PartYears > 0, " share, whole years", " share of the total")
VAR Svg =
{mb.indent(_card(f'"{cfg.big_word.upper()} QUARTER"', _short(cfg, 'Best'), 'BestName',
                 [('"Average per quarter"', _short(cfg, 'DIVIDE(AllValue, COUNTROWS(Pool))')),
                  ('"Q4" & ShareNote', f'FORMAT(DIVIDE(CALCULATE({V}, {qn} = "Q4", WholeYears), WholeValue), "0.0%")'),
                  ('"Q1" & ShareNote', f'FORMAT(DIVIDE(CALCULATE({V}, {qn} = "Q1", WholeYears), WholeValue), "0.0%")')], icon=cfg.peak_icon), 1)}
RETURN
    {mb.svg_uri()}""", "Quarter"), None, None, category="ImageUrl"))

    year_pool = f'FILTER(ADDCOLUMNS(VALUES({year}), "@v", {V}), [@v] > 0)'
    # Year-on-year claims use whole years only: "2018 down 89.5% on 2015" was one month against
    # twelve. A part year still gets its cell; it just cannot be the subject of a comparison.
    year_vars = f"""
VAR AllYears = {year_pool}
VAR Pool = FILTER(AllYears, {whole})
VAR PartYears = COUNTROWS(AllYears) - COUNTROWS(Pool)
VAR YearLast = MAXX(Pool, {year})
VAR YearFirst = MINX(Pool, {year})
VAR ValueLast = MAXX(FILTER(Pool, {year} = YearLast), [@v])
VAR ValueBefore = MAXX(FILTER(Pool, {year} = YearLast - 1), [@v])
VAR ValueFirst = MAXX(FILTER(Pool, {year} = YearFirst), [@v])
VAR ValueSecond = MAXX(FILTER(Pool, {year} = YearFirst + 1), [@v])"""
    out.append(mb.measure("Card Cal Peak Year", _scoped(cfg, f"""{year_vars}
VAR Best = MAXX(Pool, [@v])
VAR BestYear = MINX(FILTER(Pool, [@v] = Best), {year})
VAR ChangeLast = DIVIDE(ValueLast - ValueBefore, ValueBefore)
VAR ChangeSecond = DIVIDE(ValueSecond - ValueFirst, ValueFirst)
VAR Svg =
{mb.indent(_card(f'"{cfg.big_word.upper()} YEAR"', _short(cfg, 'Best'), 'FORMAT(BestYear, "0")',
                 [('IF(PartYears > 0, "Average per whole year", "Average per year")', _short(cfg, 'AVERAGEX(Pool, [@v])')),
                  ('YearLast & " on " & (YearLast - 1)', _tspan('ChangeLast')),
                  ('(YearFirst + 1) & " on " & YearFirst', _tspan('ChangeSecond'))], icon=cfg.peak_icon), 1)}
RETURN
    {mb.svg_uri()}""", "Year"), None, None, category="ImageUrl"))

    # ---- titles and standfirsts -------------------------------------------------------------------
    count_bit = (f' & "{cfg.count_join}" & FORMAT({cfg.count}, "#,0") & " {cfg.count_many}"' if cfg.count else "")
    ordinal = ('VAR Sfx = SWITCH(TRUE(), MOD(BestDay, 100) IN {11, 12, 13}, "th", MOD(BestDay, 10) = 1, "st", '
               'MOD(BestDay, 10) = 2, "nd", MOD(BestDay, 10) = 3, "rd", "th")')
    out += [
        mb.measure("Title Cal Day", f"""
VAR Cur = {V}
RETURN
    [Cal Month Label] & ": " & {_phrase(cfg, 'Cur')} & " across " & [Cal Active Days] & " {cfg.active_day}s" """),
        mb.measure("Title Cal Month", _scoped(cfg, f"""
VAR Pool = ADDCOLUMNS(SUMMARIZE('{T}', {month_no}, {month}), "@v", {V})
VAR Best = MAXX(Pool, [@v])
VAR Cur = {V}
RETURN
    SELECTEDVALUE({year}) & ": " & {_phrase(cfg, 'Cur')} & ", and " & MINX(FILTER(Pool, [@v] = Best), {month}) & " was the {cfg.big_word.lower()} month" """, "Month")),
        mb.measure("Title Cal Quarter", _scoped(cfg, f"""{whole_vars}
VAR Pool = ADDCOLUMNS(VALUES({qn}), "@v", CALCULATE({V}, WholeYears))
VAR Best = MAXX(Pool, [@v])
RETURN
    MINX(FILTER(Pool, [@v] = Best), {qn}) & " carries " & FORMAT(DIVIDE(Best, WholeValue), "0%") & " of all {cfg.value_noun}, more than any other quarter"
        & IF(PartYears > 0, " (whole years only)", "") """, "Quarter")),
        mb.measure("Title Cal Year", _scoped(cfg, f"""{year_vars}
VAR Change = DIVIDE(ValueLast - ValueFirst, ValueFirst)
RETURN
    YearLast & " closed at " & {_phrase(cfg, 'ValueLast')} & ", " & {_words('Change')} & " on " & YearFirst
        & IF(PartYears > 0, " (whole years only)", "") """, "Year")),
        mb.measure("Title Cal Weekday", f"""
VAR Pool = FILTER(ADDCOLUMNS(SUMMARIZE('{T}', {cfg.col('Cal Weekday No')}, {cfg.col('Cal Day Name')}), "@v", [Cal Avg per Active Day]), [@v] > 0)
VAR Best = MAXX(Pool, [@v])
RETURN
    IF(ISEMPTY(Pool), "Nothing in this period", MINX(FILTER(Pool, [@v] = Best), {cfg.col('Cal Day Name')}) & "s run highest per {cfg.active_day}")"""),

        mb.measure("Standfirst Cal Day", f"""
VAR Cur = {V}
VAR Prev = [Cal Value Month PY]
VAR Change = DIVIDE(Cur - Prev, Prev)
VAR Pool = ADDCOLUMNS(VALUES({cfg.d}), "@v", {V})
VAR Best = MAXX(Pool, [@v])
VAR BestDay = DAY(MINX(FILTER(Pool, [@v] = Best), {cfg.d}))
{ordinal}
VAR DaysAll = COUNTROWS(Pool)
RETURN
    [Cal Month Label] & " recorded " & {_phrase(cfg, 'Cur')}{count_bit}
        & IF(Prev > 0, ", " & {_words('Change')} & " on the same month a year earlier", "")
        & ". The {cfg.peak_word.lower()} day was the " & BestDay & Sfx & " at " & {_short(cfg, 'Best')} & "; "
        & IF(DaysAll = [Cal Active Days], "none of the " & DaysAll, (DaysAll - [Cal Active Days]) & " of " & DaysAll)
        & " days had {cfg.none_note}." """),
        mb.measure("Standfirst Cal Month", _scoped(cfg, f"""
VAR Cur = {V}
VAR Prev = [Cal Value Year PY]
VAR Change = DIVIDE(Cur - Prev, Prev)
VAR Pool = ADDCOLUMNS(SUMMARIZE('{T}', {month_no}, {month}), "@v", {V})
VAR Best = MAXX(Pool, [@v])
RETURN
    SELECTEDVALUE({year}) & " recorded " & {_phrase(cfg, 'Cur')}{count_bit}
        & IF(Prev > 0, ", " & {_words('Change')} & " on " & IF([Cal Year Is Whole], "", "the same period of ") & (SELECTEDVALUE({year}) - 1), "")
        & ". " & MINX(FILTER(Pool, [@v] = Best), {month}) & " alone was " & FORMAT(DIVIDE(Best, Cur), "0.0%")
        & " of the year" & IF([Cal Year Is Whole], "", " to date") & ". Switch to Day to open a month." """, "Month")),
        mb.measure("Standfirst Cal Quarter", _scoped(cfg, f"""
VAR Cur = {V}
VAR Pool = {year_pool}
RETURN
    MINX(Pool, {year}) & " to " & MAXX(Pool, {year}) & " recorded " & {_phrase(cfg, 'Cur')}{count_bit}
        & ". Read across a row for the shape of a year, down a column for the same quarter year on year." """, "Quarter")),
        mb.measure("Standfirst Cal Year", _scoped(cfg, f"""
VAR Cur = {V}
VAR YearCount = COUNTROWS({year_pool})
RETURN
    YearCount & " years, " & {_phrase(cfg, 'Cur')}{count_bit}
        & ". Switch to Quarter or Month to see where inside a year the change came from." """, "Year")),
    ]

    swatches = "".join(f"<rect x='{46 + i * 32}' y='5' width='26' height='10' rx='1' fill='{c}'/>" for i, c in enumerate(BANDS))
    out.append(mb.measure("Cal Legend", f"""
VAR Svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='640' height='20' viewBox='0 0 640 20' font-family='Segoe UI, sans-serif'>"
        & "<text x='0' y='14' font-size='11' fill='{MUTED}'>Lower</text>{swatches}"
        & "<text x='{46 + 5 * 32 + 2}' y='14' font-size='11' fill='{MUTED}'>Higher</text>"
        & "<text x='262' y='14' font-size='11' fill='{MUTED}'>Shade = fifths of the cells shown &#183; &#8211; {cfg.none_note}</text>"
        & "</svg>"
RETURN
    {mb.svg_uri()}""", None, "The five-band key under the calendar.", category="ImageUrl"))
    return out


def install_model(defn: Path, cfg: Config, tag) -> None:
    """Adds the 'Cal ...' calculated columns to the date table's TMDL, writes the measure table and
    registers it in model.tmdl. Run after the project's own writers; safe to run again."""
    date_file = defn / "tables" / f"{cfg.date_table}.tmdl"
    lines = date_file.read_text(encoding="utf-8").split("\n")
    if not any(line.startswith("\tcolumn 'Cal Year'") for line in lines):
        at = next(i for i, line in enumerate(lines) if line.startswith("\tpartition "))
        block: list[str] = []
        for name, dax, dtype, fmt, sort_col, doc_text in date_columns(cfg):
            block += mb.doc(doc_text, "\t")
            block += [f"\tcolumn {mb.q(name)} = {dax}", f"\t\tdataType: {dtype}"]
            if fmt:
                block.append(f"\t\tformatString: {fmt}")
            block += ["\t\tdisplayFolder: Calendar", f"\t\tlineageTag: {tag('column', cfg.date_table, name)}",
                      "\t\tsummarizeBy: none"]
            if sort_col:
                block.append(f"\t\tsortByColumn: {mb.q(sort_col)}")
            block.append("")
        lines[at:at] = block
        date_file.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    mb.write_lines(defn / "tables" / f"{ENTITY}.tmdl", mb.measure_table_tmdl(
        ENTITY, measures(cfg), tag,
        "Measures behind the Calendar page: SVG cells, shade bands, cards, titles. Generated by\n"
        "etl/milestone_calendar.py from the CALENDAR config."))
    text = (defn / "model.tmdl").read_text(encoding="utf-8")
    last_ref = [line for line in text.split("\n") if line.startswith("ref table ")][-1]
    mb.add_table_refs(defn / "model.tmdl", [ENTITY], last_ref)


# ================================================================================================
# REPORT
# ================================================================================================


def _title_chrome(F: mb.Fields, title_measure: str, subtitle: str) -> dict:
    out = mb.dynamic_chrome(F.expr(title_measure))
    out["subTitle"] = obj(show=lit(True), text=lit(subtitle), fontSize=lit(8.5), fontColor=colour(MUTED))
    return out


def _matrix(cfg: Config, F: mb.Fields, view: str, rows: dict | None, columns: dict, subtitle: str,
            row_header_width: float | None) -> dict:
    w, h = cell_size(cfg, view)
    state: dict = {"Columns": {"projections": [columns]}, "Values": {"projections": [F.m(f"Cal Cell {view}", " ")]}}
    if rows is not None:
        state["Rows"] = {"projections": [rows]}
    widths = [{"properties": {"value": lit(float(w + 10))}, "selector": {"metadata": F.ref(f"Cal Cell {view}")}}]
    if rows is not None and row_header_width is not None:
        widths.append({"properties": {"value": lit(row_header_width)}, "selector": {"metadata": rows["queryRef"]}})
    node = mb.visual(
        f"vCal{view}{cfg.slug}", "pivotTable", 24, 176, 900, 700, 600,
        # No sortDefinition: the axis columns sort by their own sort-by columns, and an explicit
        # sort draws a sort arrow in the corner of the calendar.
        query={"queryState": state},
        objects={
            "grid": [{"properties": {
                "outlineColor": colour(CARD), "outlineWeight": lit(1),
                "gridVertical": lit(True), "gridVerticalColor": colour(CARD), "gridVerticalWeight": lit(3),
                "gridHorizontal": lit(True), "gridHorizontalColor": colour(CARD), "gridHorizontalWeight": lit(3),
                "rowPadding": lit(0), "imageHeight": lit(float(h)), "imageWidth": lit(float(w)),
            }}],
            "columnHeaders": [{"properties": {
                "fontSize": lit(9.0), "bold": lit(True), "fontColor": colour(INK), "backColor": colour(CARD),
                "alignment": lit("Center"), "autoSizeColumnWidth": lit(False), "outlineColor": colour(CARD),
            }}],
            "rowHeaders": [{"properties": {
                "fontSize": lit(9.0), "bold": lit(True), "backColor": colour(CARD),
                "fontColor": colour(CARD if view == "Day" else INK),
                "showExpandCollapseButtons": lit(False), "outlineColor": colour(CARD),
            }}],
            "values": [
                {"properties": {"fontSize": lit(9.0), "fontColorPrimary": colour(BODY), "outlineColor": colour(CARD),
                                "backColorPrimary": colour(CARD), "backColorSecondary": colour(CARD)}},
                {"properties": {"backColor": {"solid": {"color": F.expr(f"Cal Colour {view}")}}},
                 "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}],
                              "metadata": F.ref(f"Cal Cell {view}")}},
            ],
            "columnWidth": widths,
            "subTotals": [{"properties": {"rowSubtotals": lit(False), "columnSubtotals": lit(False)}}],
        },
        container=_title_chrome(F, f"Title Cal {view}", subtitle),
    )
    # The cell is an image measure, so the default tooltip prints its value: the raw
    # "data:image/svg+xml..." string. Everything worth knowing is already drawn in the cell.
    node["visual"]["visualContainerObjects"]["visualTooltip"] = obj(show=lit(False))
    return node


def _weekday_bars(cfg: Config, F: mb.Fields, view: str) -> dict:
    return mb.visual(
        f"vWeekday{view}{cfg.slug}", "barChart", 940, 512, 476, 364, 640,
        query={"queryState": {
            "Category": {"projections": [column(cfg.date_table, "Cal Day Short", "Day")]},
            "Y": {"projections": [F.m("Cal Avg per Active Day", f"Average per {cfg.active_day}")]},
        }},
        objects={
            "categoryAxis": mb.axis(),
            "valueAxis": [{"properties": {"show": lit(False), "showAxisTitle": lit(False)}}],
            "legend": [{"properties": {"show": lit(False)}}],
            "labels": [{"properties": {"show": lit(True), "fontSize": lit(8.5), "color": colour(BODY),
                                       "labelDisplayUnits": lit(1)}}],
            "dataPoint": [{"properties": {"fill": {"solid": {"color": F.expr("Cal Weekday Colour")}}},
                           "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}],
        },
        container=_title_chrome(F, "Title Cal Weekday",
                                f"Average {cfg.value_noun} per {cfg.active_day}. Weekends in slate."),
    )


def _hover_guard(name: str, x: int, y: int, w: int, h: int, z: int) -> dict:
    """A fully transparent rectangle laid over the matrices. A matrix cell holding an image
    measure shows the cell's raw value on hover - the whole "data:image/svg+xml..." string - and
    that is the grid's own cell tooltip, not the visual tooltip: General > Tooltips off does not
    remove it (tested in Desktop). A shape on top takes the hover instead. Nothing under it needs
    a click: the view buttons and the dropdowns sit outside it."""
    clear = {"show": lit(True), "fillColor": colour(PAPER), "transparency": lit(100.0)}
    off = {"show": lit(False)}
    container = mb.no_chrome()
    container["visualHeader"] = obj(show=lit(False))     # no "..." over the calendar in the Service
    node = mb.visual(name, "shape", x, y, w, h, z, container=container)
    node["visual"]["objects"] = {
        "shape": [{"properties": {"tileShape": lit("rectangle")}, "selector": {"id": "default"}}],
        "fill": [{"properties": clear}, {"properties": clear, "selector": {"id": "default"}}],
        "outline": [{"properties": off}, {"properties": off, "selector": {"id": "default"}}],
    }
    return node


def _dropdown(cfg: Config, name: str, x: int, col: str, header: str, default) -> dict:
    node = mb.dropdown(name, x, 72, 170, 84, 400, cfg.date_table, col, header)
    node["visual"]["objects"]["general"][0]["properties"]["filter"] = {
        "filter": mb.in_filter("d", cfg.date_table, col, [default])}
    # Force one selection: with two months selected every Day cell would hold two dates.
    node["visual"]["objects"]["selection"] = [{"properties": {"strictSingleSelect": lit(True)}}]
    node["visual"]["visualContainerObjects"]["border"] = obj(show=lit(True), color=colour(RULE), radius=lit(4))
    return node


def build_page(cfg: Config) -> tuple[dict, list[dict], list[dict]]:
    """Returns (page.json, visuals, bookmarks). The page opens on Day; CAL_START_VIEW=Month (etc.)
    builds a copy that opens on another grain, which is how the views behind the buttons get
    screenshotted."""
    _apply(cfg)
    F, s, T = mb.Fields(ENTITY), cfg.slug, cfg.date_table
    if cfg.masthead:
        common = mb.masthead(s, cfg.title, cfg.ref)
    else:
        common = [mb.textbox(f"vTitle{s}", 24, 60, 680, 44, 90,
                             [[{"text": cfg.title, "size": 21, "color": INK, "bold": True}]])]
    common.append(mb.textbox(f"vViewLabel{s}", 708, 72, 120, 22, 395,
                             [[{"text": "VIEW", "size": 8.5, "color": MUTED, "bold": True}]]))
    common.append(mb.svg_image(f"vLegend{s}", 38, 848, 640, 20, 900, F.expr("Cal Legend")))
    common.append(_hover_guard(f"vHoverGuard{s}", 24, 176, 900, 700, 950))

    month_slicer = _dropdown(cfg, f"vMonth{s}", 1070, "Cal Month", "MONTH", cfg.default_month)
    year_slicer = _dropdown(cfg, f"vYear{s}", 1246, "Cal Year", "YEAR", cfg.default_year)

    n = cfg.value_noun
    matrices = {
        "Day": _matrix(cfg, F, "Day", column(T, "Cal Week Row", " "), column(T, "Cal Day Short", "Day"),
                       f"Each cell is one day. Shade ranks the day against the other days of this month.", 14.0),
        "Month": _matrix(cfg, F, "Month", column(T, "Cal Quarter", " "), column(T, "Cal Month in Quarter", "Month"),
                         "Each cell is one month, a quarter to a row. Shade ranks the month against the others in the year.", 44.0),
        "Quarter": _matrix(cfg, F, "Quarter", column(T, "Cal Year", " "), column(T, "Cal Quarter", "Quarter"),
                           "Each cell is one quarter, a year to a row. Shade ranks the quarter against all of them.", 50.0),
        "Year": _matrix(cfg, F, "Year", None, column(T, "Cal Year", "Year"),
                        "Each cell is one year. Shade ranks the year against the others.", None),
    }

    # Unselected tiles are shared between views: tile X shows in every view except X.
    off = {view: mb.action_button(f"vBtn{view}Off{s}", 708 + i * 88, 96, 88, 34, 430 + i, view, f"bm{s}{view}",
                                  fill=CARD, text_colour=BODY, outline=RULE)
           for i, view in enumerate(VIEWS)}
    by_view: dict[str, list[dict]] = {}
    for i, view in enumerate(VIEWS):
        total = f"Card Cal Total {view}" if view in ("Day", "Month") else "Card Cal Total All"
        by_view[view] = [
            matrices[view],
            mb.dynamic_text(f"vStand{view}{s}", 18, 112, 680, 56, 95 + i, F.expr(f"Standfirst Cal {view}")),
            mb.svg_image(f"vTotal{view}{s}", 940, 176, CARD_W, CARD_H, 500 + i, F.expr(total)),
            mb.svg_image(f"vPeak{view}{s}", 940, 344, CARD_W, CARD_H, 520 + i, F.expr(f"Card Cal Peak {view}")),
            _weekday_bars(cfg, F, view),
            mb.action_button(f"vBtn{view}On{s}", 708 + i * 88, 96, 88, 34, 440 + i, view, f"bm{s}{view}",
                             fill=INK, text_colour=CARD),
        ] + [off[o] for o in VIEWS if o != view]
    by_view["Day"] += [month_slicer, year_slicer]
    by_view["Month"] += [year_slicer]

    ordered: list[dict] = []
    for view in VIEWS:
        for node in by_view[view]:
            if all(node is not seen for seen in ordered):
                ordered.append(node)
    start = os.environ.get("CAL_START_VIEW", "Day")
    for node in ordered:
        if all(node is not own for own in by_view[start]):
            node["isHidden"] = True

    bookmarks = []
    for view, own in by_view.items():
        own_names = {v["name"] for v in own}
        containers = {}
        for v in ordered:
            single = {"visualType": v["visual"]["visualType"], "objects": {}}
            if v["name"] not in own_names:
                single["display"] = {"mode": "hidden"}
            containers[v["name"]] = {"singleVisual": single}
        bookmarks.append({
            "$schema": mb.BOOKMARK_SCHEMA, "displayName": f"{cfg.page_display} {view.lower()} view",
            "name": f"bm{s}{view}",
            "options": {"targetVisualNames": [v["name"] for v in ordered], "applyOnlyToTargetVisuals": True,
                        "suppressData": True, "suppressActiveSection": True},
            "explorationState": {"version": "1.3", "activeSection": cfg.page_name,
                                 "sections": {cfg.page_name: {"visualContainers": containers}}},
        })

    blocked_by = {"Day": [], "Month": [month_slicer], "Quarter": [month_slicer, year_slicer],
                  "Year": [month_slicer, year_slicer]}
    interactions = [{"source": src["name"], "target": tgt["name"], "type": "NoFilter"}
                    for view in VIEWS for src in blocked_by[view] for tgt in by_view[view]
                    if tgt["visual"]["visualType"] in ("pivotTable", "barChart", "image", "shape")]
    pg = mb.page(cfg.page_name, cfg.page_display)
    pg["visualInteractions"] = interactions
    return pg, common + ordered, bookmarks


# ================================================================================================
# VERIFY: the answer key from a daily series, the DAX that dumps the same cells, and the diff
# ================================================================================================


def _bands(cells: dict[str, float]) -> dict[str, int]:
    live = sorted(v for v in cells.values() if v > 0)
    out = {}
    for key, v in cells.items():
        if not v > 0:
            out[key] = 0
        else:
            below = sum(1 for x in live if x < v)
            out[key] = 5 if len(live) == 1 else min(5, below * 5 // (len(live) - 1) + 1)
    return out


def expected(daily: dict[date, float], first: date, last: date) -> dict[tuple[str, str], tuple[float, int]]:
    """Every cell of all four views -> (value, band). `daily` holds the base measure per day (it
    must be additive over days); first/last are the date table's bounds."""
    out: dict[tuple[str, str], tuple[float, int]] = {}
    for view, key_of, pool_of in (
            ("Day", lambda d: d.isoformat(), lambda d: d.strftime("%Y-%m")),
            ("Month", lambda d: d.strftime("%Y-%m"), lambda d: str(d.year)),
            ("Quarter", lambda d: f"{d.year}-Q{(d.month - 1) // 3 + 1}", lambda d: "all"),
            ("Year", lambda d: str(d.year), lambda d: "all")):
        groups: dict[str, dict[str, float]] = {}
        d = first
        while d <= last:
            cells = groups.setdefault(pool_of(d), {})
            cells[key_of(d)] = cells.get(key_of(d), 0.0) + daily.get(d, 0.0)
            d += timedelta(days=1)
        for cells in groups.values():
            b = _bands(cells)
            for k, v in cells.items():
                out[(view, k)] = (round(v, 2), b[k])
    return out


def verify_queries(cfg: Config, years: list[int]) -> list[tuple[str, str]]:
    """(view, DAX) pairs for calendar_verify.ps1. Each pins slicers the way the page does: the Day
    view under a month and a year (every month of `years`), the Month view under a year."""
    T, V = cfg.date_table, cfg.value
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]
    out = []
    for y in years:
        for mname in months:
            out.append(("Day", f"""EVALUATE CALCULATETABLE(SELECTCOLUMNS(SUMMARIZE('{T}', {cfg.d}, {cfg.col('Cal Week of Month')}, {cfg.col('Cal Day Short')}),
"k", FORMAT({cfg.d}, "yyyy-mm-dd"), "s", {V}, "b", [Cal Band Day]), TREATAS({{"{mname}"}}, {cfg.col('Cal Month')}), TREATAS({{{y}}}, {cfg.col('Cal Year')}))"""))
        out.append(("Month", f"""EVALUATE CALCULATETABLE(SELECTCOLUMNS(SUMMARIZE('{T}', {cfg.col('Cal Month No')}, {cfg.col('Cal Quarter')}, {cfg.col('Cal Month in Quarter')}),
"k", "{y}-" & FORMAT({cfg.col('Cal Month No')}, "00"), "s", {V}, "b", [Cal Band Month]), TREATAS({{{y}}}, {cfg.col('Cal Year')}))"""))
    out.append(("Quarter", f"""EVALUATE SELECTCOLUMNS(SUMMARIZE('{T}', {cfg.col('Cal Quarter Key')}, {cfg.col('Cal Year')}, {cfg.col('Cal Quarter')}),
"k", {cfg.col('Cal Year')} & "-" & {cfg.col('Cal Quarter')}, "s", {V}, "b", [Cal Band Quarter])"""))
    out.append(("Year", f"""EVALUATE SELECTCOLUMNS(VALUES({cfg.col('Cal Year')}), "k", FORMAT({cfg.col('Cal Year')}, "0"), "s", {V}, "b", [Cal Band Year])"""))
    return out


def compare(key: dict[tuple[str, str], tuple[float, int]], dump: Path, tolerance: float = 0.011) -> int:
    """Diffs calendar_verify.ps1 output (view|key|value|band) against `expected`. Returns the
    number of mismatches and prints them."""
    seen, bad = set(), []
    for line in dump.read_text(encoding="utf-8-sig").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 4 or parts[0] not in VIEWS:
            continue
        view, k = parts[0], parts[1]
        v = float(parts[2] or 0)
        b = int(float(parts[3])) if parts[3] != "" else None
        seen.add((view, k))
        if (view, k) not in key:
            bad.append(f"{view} {k}: in the model, not in the key")
        elif abs(key[(view, k)][0] - v) > tolerance or key[(view, k)][1] != b:
            bad.append(f"{view} {k}: model {v:.2f}/band {b}  key {key[(view, k)][0]:.2f}/band {key[(view, k)][1]}")
    bad += [f"{v} {k}: in the key, not in the model" for (v, k) in key if (v, k) not in seen]
    for line in bad[:40]:
        print("  MISMATCH", line)
    print(f"{len(seen)} cells, {len(seen) * 2} checks, {len(bad)} mismatches")
    return len(bad)
