# Measures whichever model is currently open in Power BI Desktop: VertiPaq storage per column,
# and the wall-clock cost of a set of DAX queries, cold and warm.
#
#   powershell -File perf/etl/measure_model.ps1 -Label star
#   powershell -File perf/etl/measure_model.ps1 -Label naive
#
# Writes perf/measurements/<label>_columns.csv, <label>_timings.csv and <label>_results.csv.
#
# Method, so the numbers can be argued with:
#
#   * Storage comes from the same DMVs DAX Studio reads - DISCOVER_STORAGE_TABLE_COLUMNS for the
#     dictionary, DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS for the compressed data, and
#     DISCOVER_STORAGE_TABLE_COLUMN_HIERARCHIES for the attribute hierarchies the engine builds
#     to make a column groupable. All three are part of what the model costs in memory.
#   * A cold run means the engine's caches were cleared immediately before it (ClearCache over
#     XMLA). A warm run is the query repeated straight after, so the second one reads the cache.
#     Both are worth having: a report page hit for the first time in the morning is cold, and
#     the same page ten seconds later is warm.
#   * Each query runs -Iterations times in each state and the MEDIAN is kept, not the mean - one
#     background process on a laptop skews a mean and does not move a median.
#   * The query results are written out too, so perf/etl/compare.py can prove both models return
#     the same answers. A performance comparison between two models that disagree is worthless.

param(
    [Parameter(Mandatory = $true)][ValidateSet("star", "naive")][string]$Label,
    [int]$Iterations = 5
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$QUERIES = Join-Path $ROOT "perf\queries\$Label"
$OUT = Join-Path $ROOT "perf\measurements"
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

# ---------------------------------------------------------------- connect
$msmdsrv = Get-CimInstance Win32_Process -Filter "Name='msmdsrv.exe'"
if (-not $msmdsrv) { throw "msmdsrv.exe is not running - open the PBIP in Desktop first." }
$port = $null
foreach ($proc in $msmdsrv) {
    $conn = Get-NetTCPConnection -State Listen -OwningProcess $proc.ProcessId -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -eq "127.0.0.1" } | Select-Object -First 1
    if ($conn) { $port = $conn.LocalPort; break }
}
if (-not $port) { throw "could not find the local XMLA port" }

$pkg = (Get-AppxPackage -Name "*PowerBIDesktop*").InstallLocation
$adomd = @("Microsoft.PowerBI.AdomdClient.dll", "Microsoft.AnalysisServices.AdomdClient.dll") |
         ForEach-Object { Join-Path $pkg "bin\$_" } |
         Where-Object { Test-Path $_ } | Select-Object -First 1
[void][Reflection.Assembly]::LoadFrom($adomd)

$catalog = $null
foreach ($attempt in 1..30) {
    try {
        $probe = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
        $probe.Open()
        $c = $probe.CreateCommand()
        $c.CommandText = "SELECT [CATALOG_NAME] FROM `$SYSTEM.DBSCHEMA_CATALOGS"
        $r = $c.ExecuteReader()
        if ($r.Read()) { $catalog = $r.GetString(0) }
        $r.Close(); $probe.Close()
    } catch { }
    if ($catalog) { break }
    Start-Sleep -Seconds 5
}
if (-not $catalog) { throw "no catalog mounted after 150s" }
Write-Output "$Label : localhost:$port, catalog $catalog"

$cn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection(
    "Data Source=localhost:$port;Initial Catalog=$catalog")
$cn.Open()

function Invoke-Rows([string]$text) {
    $cmd = $cn.CreateCommand()
    $cmd.CommandText = $text
    $cmd.CommandTimeout = 900
    $reader = $cmd.ExecuteReader()
    $cols = @()
    for ($i = 0; $i -lt $reader.FieldCount; $i++) { $cols += $reader.GetName($i) }
    $rows = @()
    while ($reader.Read()) {
        $row = [ordered]@{}
        for ($i = 0; $i -lt $reader.FieldCount; $i++) { $row[$cols[$i]] = $reader.GetValue($i) }
        $rows += [pscustomobject]$row
    }
    $reader.Close()
    return $rows
}

function Clear-EngineCache {
    $cmd = $cn.CreateCommand()
    $cmd.CommandText = "<ClearCache xmlns=`"http://schemas.microsoft.com/analysisservices/2003/engine`">" +
                       "<Object><DatabaseID>$catalog</DatabaseID></Object></ClearCache>"
    $cmd.ExecuteNonQuery() | Out-Null
}

# ---------------------------------------------------------------- storage
Write-Output "storage DMVs..."
# SELECT * on purpose: these DMVs reject an arbitrary column list, and their schema differs
# between engine builds. Filtering happens in compare.py where it can be read.
#
# DISCOVER_STORAGE_TABLE_COLUMN_HIERARCHIES is not exposed by the engine Desktop 2.157 hosts,
# but it is not needed: the attribute hierarchies come back through the segments DMV as
# pseudo-tables named H$<table>$<column>, and compare.py folds each one back onto the column it
# belongs to. They are a real cost - the engine builds one per column so the column can be
# grouped or filtered - and on a 1.68m-row text column they are not small.
$dict = Invoke-Rows "SELECT * FROM `$SYSTEM.DISCOVER_STORAGE_TABLE_COLUMNS"
$segs = Invoke-Rows "SELECT * FROM `$SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS"
$tabs = Invoke-Rows "SELECT * FROM `$SYSTEM.DISCOVER_STORAGE_TABLES"

$dict | Export-Csv (Join-Path $OUT "${Label}_dictionary.csv") -NoTypeInformation -Encoding UTF8
$segs | Export-Csv (Join-Path $OUT "${Label}_segments.csv") -NoTypeInformation -Encoding UTF8
$tabs | Export-Csv (Join-Path $OUT "${Label}_tables.csv") -NoTypeInformation -Encoding UTF8
Write-Output "  $($dict.Count) columns, $($segs.Count) segments, $($tabs.Count) tables"

# ---------------------------------------------------------------- timings
$files = Get-ChildItem -Path $QUERIES -Filter *.dax | Sort-Object Name
$timings = @()
$results = @()
foreach ($f in $files) {
    $dax = [IO.File]::ReadAllText($f.FullName)
    $name = $f.BaseName

    $cold = @()
    $warm = @()
    for ($i = 1; $i -le $Iterations; $i++) {
        Clear-EngineCache
        Start-Sleep -Milliseconds 200
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $rows = Invoke-Rows $dax
        $sw.Stop(); $cold += $sw.Elapsed.TotalMilliseconds

        $sw = [Diagnostics.Stopwatch]::StartNew()
        $null = Invoke-Rows $dax
        $sw.Stop(); $warm += $sw.Elapsed.TotalMilliseconds

        if ($i -eq 1) {
            foreach ($row in $rows) {
                foreach ($p in $row.PSObject.Properties) {
                    $results += [pscustomobject]@{
                        query = $name; column = $p.Name; value = $p.Value
                    }
                }
            }
        }
    }
    $median = { param($a) ($a | Sort-Object)[[int]([math]::Floor($a.Count / 2))] }
    $cm = & $median $cold
    $wm = & $median $warm
    $timings += [pscustomobject]@{
        query = $name; cold_ms = [math]::Round($cm, 1); warm_ms = [math]::Round($wm, 1)
        # @() first: in Windows PowerShell a single returned object has no .Count, and a
        # one-row query then writes an empty cell.
        iterations = $Iterations; rows = @($rows).Count
    }
    Write-Output ("  {0,-22} cold {1,8:N1} ms   warm {2,8:N1} ms" -f $name, $cm, $wm)
}

$timings | Export-Csv (Join-Path $OUT "${Label}_timings.csv") -NoTypeInformation -Encoding UTF8
$results | Export-Csv (Join-Path $OUT "${Label}_results.csv") -NoTypeInformation -Encoding UTF8
$cn.Close()
Write-Output "written to perf/measurements/${Label}_*.csv"
