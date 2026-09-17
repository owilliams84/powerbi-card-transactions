# Runs the (view, DAX) pairs written by milestone_calendar.verify_queries against the model open
# in Power BI Desktop and prints   view|key|value|band   for milestone_calendar.compare.
#
#   python etl/calendar_check.py --queries q.json      (writes the pairs)
#   powershell -File etl/calendar_verify.ps1 -QueriesFile q.json > dump.txt
#   python etl/calendar_check.py --compare dump.txt
#
# Run as a background job - closing the ADOMD connection can hang the shell.

param([Parameter(Mandatory = $true)][string]$QueriesFile)

$ErrorActionPreference = "Stop"

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

$probe = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
$probe.Open()
$c = $probe.CreateCommand()
$c.CommandText = "SELECT [CATALOG_NAME] FROM `$SYSTEM.DBSCHEMA_CATALOGS"
$rd = $c.ExecuteReader(); $catalog = $null
if ($rd.Read()) { $catalog = $rd.GetString(0) }
$rd.Close(); $probe.Close()

$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection(
    "Data Source=localhost:$port;Initial Catalog=$catalog")
$conn.Open()
$inv = [Globalization.CultureInfo]::InvariantCulture
$watch = [Diagnostics.Stopwatch]::StartNew()

$queries = [IO.File]::ReadAllText($QueriesFile) | ConvertFrom-Json
foreach ($q in $queries) {
    $cmd = $conn.CreateCommand()
    $cmd.CommandTimeout = 600
    $cmd.CommandText = $q.dax
    $r = $cmd.ExecuteReader()
    while ($r.Read()) {
        $s = $r.GetValue(1); $b = $r.GetValue(2)
        $sTxt = if ($s -is [DBNull] -or $null -eq $s) { "0" } else { ([double]$s).ToString("0.00", $inv) }
        $bTxt = if ($b -is [DBNull] -or $null -eq $b) { "" } else { "$b" }
        Write-Output "$($q.view)|$($r.GetValue(0))|$sTxt|$bTxt"
    }
    $r.Close()
}
Write-Output ("# done in {0} ms" -f $watch.ElapsedMilliseconds)
$conn.Close()
