# The same data, modelled twice

A measured comparison between the star schema in this repository and the same 1,682,640
transactions loaded the way most Power BI files are actually built: one wide table, everything
merged in, nothing thrown away.

Both models hold identical rows. Nine of the ten benchmark queries return identical values from
both. The tenth does not, and that turned out to be the point.

---

## What it costs

|  | Star schema | One flat table | |
|---|---:|---:|---|
| **Total in memory** | **30.8 MB** | **190.4 MB** | 6.2× |
| Dictionaries | 19.9 MB | 139.1 MB | 7.0× |
| Compressed data | 10.8 MB | 51.3 MB | 4.7× |
| Attribute hierarchies | 0.2 MB | 24.0 MB | 99× |
| Tables | 18 | 1 | |
| Columns | 96 | 32 | |

The star schema has three times the columns and eighteen times the tables, and takes a sixth of
the memory.

### Where the 190 MB goes

| Column | Size | Share of model |
|---|---:|---:|
| `Transaction ID` | 91.2 MB | 47.9% |
| `Timestamp` | 58.1 MB | 30.5% |
| everything else (30 columns) | 41.1 MB | 21.6% |

**Two columns are 78% of the model, and neither has ever been on a visual.** One is the source's
transaction id, kept because someone might want to drill to a row. The other is the timestamp
kept to the minute, because we might need the time later.

They are expensive for the same reason: VertiPaq compresses a column by building a dictionary of
its distinct values, so cost tracks cardinality, not row count. `Transaction ID` has 1,682,640
distinct values in 1,682,640 rows — the worst case, one entry per row, nothing to compress.
`Timestamp` has 1,365,222. Every other column in the file has fewer than 20,000.

The star schema does not contain either. The transaction id was dropped because a row count is
a measure, not a column; the timestamp became a date key plus an integer hour, which is what the
report actually asks for. That single pair of decisions is 149 MB.

---

## What it costs in time: much less than you would think

| Query | Cold star | Cold flat | Warm star | Warm flat |
|---|---:|---:|---:|---:|
| Total spend | 11.5 ms | 11.4 ms | 2.9 ms | 5.1 ms |
| Spend by year | 11.5 ms | 18.6 ms | 3.5 ms | 5.5 ms |
| Spend by channel | 12.2 ms | 13.3 ms | 3.3 ms | 5.7 ms |
| Spend by merchant category | 16.5 ms | 20.1 ms | 7.7 ms | 11.1 ms |
| Fraud rate by channel | 12.5 ms | 14.6 ms | 3.7 ms | 8.6 ms |
| Distinct cards and clients | 12.0 ms | 36.6 ms | 3.4 ms | 5.0 ms |
| Spend by card × year (6,400 rows) | 880.4 ms | 871.8 ms | 794.6 ms | 834.8 ms |
| Active cards by month | 30.2 ms | 51.7 ms | 9.7 ms | 12.3 ms |
| Top 200 cards | 22.9 ms | 33.0 ms | 13.6 ms | 15.7 ms |

The flat model is slower on most queries — up to three times on a cold distinct count — but the
absolute numbers are tens of milliseconds, and on the heaviest query in the set the two are
indistinguishable. **At 1.7 million rows, bad modelling does not make a report feel slow.**

That is worth saying plainly, because the usual version of this article claims a tenfold speed-up
and does not show its timings. The cost here is not latency. It is that the model is six times
larger than it needs to be, which is what decides whether it fits in a capacity, how long it takes
to refresh, and how far it can grow before any of that becomes a crisis. A model that is 190 MB at
1.7m rows is 11 GB at 100m. The star schema is 1.8 GB.

---

## What it costs in being right

Nine of the ten queries return identical values from both models. The tenth is year-on-year
spend, and it does not:

| Year | Star: prior year | Star: YoY | Flat: prior year | Flat: YoY |
|---|---:|---:|---:|---:|
| 2018 | $7,606,151 | +0.3% | $7,606,151 | +0.3% |
| **2019** | **$6,347,500** | **−1.0%** | **$7,627,793** | **−17.6%** |

Every year to 2018 agrees to thirteen decimal places. 2019 does not, because 2019 is a partial
year — the data stops on 31 October.

The star schema has a marked date table, so `DATEADD` shifts the visible ten months back to the
same ten months of 2018 and compares like with like. The flat model has no date table, so
year-on-year is done the only way left — arithmetic on a year column — and compares ten months
of 2019 against twelve months of 2018.

The flat model reports that spending fell 17.6%. It fell 1.0%. Nothing is broken, no error
appears, and the number is the kind that ends up in a board pack.

**That is the real cost of the shortcut.** The memory is recoverable in an afternoon. A number
that is wrong by a factor of seventeen, in a report nobody has reason to doubt, is not.

---

## Method

Both models were measured on the same machine, in the same Power BI Desktop session type,
against the same rows.

- **Storage** comes from the engine's own DMVs — `DISCOVER_STORAGE_TABLE_COLUMNS` for
  dictionaries and `DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS` for compressed data. Attribute
  hierarchies arrive as pseudo-tables named `H$<table>$<column>` and are folded back onto the
  column they belong to, because they are part of what a column costs and they are largest
  exactly where the column is widest.
- **Cold** means the engine's caches were cleared immediately before the query (`ClearCache`
  over XMLA). **Warm** is the same query run straight after. Both matter: a page opened for the
  first time this morning is cold, the same page ten seconds later is warm.
- Each query ran **five times in each state** and the **median** is reported. A mean would move
  with one background process; a median does not.
- Every query pair is checked for **identical results** before its timing is used. A performance
  comparison between two models that disagree measures nothing.
- Differences below **8 ms** are treated as the harness, not the model — every query round-trips
  through ADOMD, which costs a few milliseconds on its own.

**Two things this does not measure.** Refresh time is not compared, because the flat model reads
a 315 MB CSV and the star reads eleven small ones, which measures the file layout rather than
the model. And auto date/time was left enabled in the flat model, but Desktop does not generate
the hidden date tables for a TMDL-authored model, so that cost — real in a Desktop-authored PBIX
— is absent from these figures. Both omissions favour the flat model.

## Running it

```
python perf/etl/build_flat_source.py --source <folder with the unzipped Kaggle CSVs>
python perf/etl/build_naive_model.py

# open perf/Card Transactions Naive.pbip in Desktop, then:
powershell -File etl/refresh_model.ps1
powershell -File perf/etl/measure_model.ps1 -Label naive

# open Card Transactions.pbip in Desktop, then:
powershell -File etl/refresh_model.ps1
powershell -File perf/etl/measure_model.ps1 -Label star

python perf/etl/compare.py
```

`perf/data/flat_transactions.csv` is 315 MB and is not committed; the script regenerates it.
The star schema's CSVs in `../data` are committed, and both models read the same 160-client
panel.

## Is the flat model a strawman?

Every decision in it is one I have seen in a production PBIX, and each is defensible on its own:
merge the dimensions in because that is what the merge button does; keep the id for
drill-through; keep the timestamp because we might need the time; leave the keys as the text the
merge produced; put Year and the approved flag in calculated columns because that is the obvious
place; write `SUMX(FILTER(...))` because it reads like the sentence you were asked for.

None of them is stupid. Together they cost six times the memory and one materially wrong number.
That is the argument for modelling on paper before modelling in the Power Query window.
