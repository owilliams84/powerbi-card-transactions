"""The Calendar page's report-specific half. Everything else is etl/milestone_calendar.py.

The calendar is shaded by spend - approved rows only, refunds netted off, the same [Spend] every
other page uses - with the approved transaction count as the second figure in each cell.
"""

from milestone_calendar import Config

CALENDAR = Config(
    value="[Spend]",
    value_noun="spend",
    money=True,
    count="[Approved Transactions]",
    count_one="transaction",
    count_many="transactions",
    title="Spend calendar",
    ref="06 / CALENDAR",
    card_label="SPEND IN VIEW",
    peak_word="Biggest",
    big_word="Biggest",
    active_day="active day",
    none_note="no spend",
    # The data ends on 31 October 2019.
    default_month="October",
    default_year=2019,
    years=10,
)
