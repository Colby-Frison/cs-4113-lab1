# Three-AS stand-in for the Lab 0 LARGE packet-forwarding model.
# Uses the generator, both server functions, file edges, and all three gRPC services.
$ErrorActionPreference = "Stop"

$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here "..\..\..\..")).Path
$Run = Join-Path $Here "run"
New-Item -ItemType Directory -Force -Path $Run | Out-Null
Get-ChildItem -Path $Run -File -ErrorAction SilentlyContinue | Remove-Item -Force

$Gen = Join-Path $Repo "implementation\obj-a\workload-generator\workload_generator.py"
$Queue = Join-Path $Repo "implementation\obj-a\queue\queue.py"
$Stateless = Join-Path $Repo "implementation\obj-a\server-func-stateless\server_func.py"
$Stateful = Join-Path $Repo "implementation\obj-a\server-func-stateful\server_func.py"

$Jobs = Join-Path $Run "jobs.csv"
$Forwarded = Join-Path $Run "forwarded.csv"
$Sink = Join-Path $Run "sink.csv"
$Count = 6

function Quote-Arg([string]$Text) {
    if ($Text -match '[\s"]') {
        return '"' + ($Text -replace '"', '\"') + '"'
    }
    return $Text
}

function Join-Args([string[]]$Parts) {
    return (($Parts | ForEach-Object { Quote-Arg $_ }) -join " ")
}

$Listeners = @()

function Start-Listener([string]$Name, [string[]]$Parts) {
    $proc = Start-Process -FilePath "python" `
        -ArgumentList (Join-Args $Parts) `
        -WorkingDirectory $Repo `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput (Join-Path $Run "$Name.stdout") `
        -RedirectStandardError (Join-Path $Run "$Name.stderr")
    $script:Listeners += $proc
}

function Stop-Listeners {
    foreach ($proc in $script:Listeners) {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-DataLines([string]$Path, [int]$Needed, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $Path) {
            $lines = @(Get-Content -Path $Path | Where-Object { $_.Trim() -ne "" })
            if ($lines.Count -ge $Needed) {
                return
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "timed out waiting for $Needed data rows in $Path"
}

function Count-Text([string]$Path, [string]$Needle) {
    if (-not (Test-Path $Path)) {
        return 0
    }
    return @(Select-String -Path $Path -Pattern $Needle -SimpleMatch).Count
}

try {
    # Listeners before senders. Ports stay on localhost.
    Start-Listener "sf-stateless" @(
        $Stateless,
        "--qid", "q0",
        "--server-id", "0",
        "--ingrpc", "0.0.0.0:50151,1",
        "--outgrpc", "127.0.0.1:50152,1",
        "--logfile", (Join-Path $Run "sf-stateless.log")
    )
    Start-Listener "q1" @(
        $Queue,
        "--qid", "q1",
        "--qsize", "8",
        "--ingrpc", "0.0.0.0:50152,2",
        "--outgrpc", "127.0.0.1:50153,1,queue",
        "--logfile", (Join-Path $Run "q1.log")
    )
    Start-Listener "q2" @(
        $Queue,
        "--qid", "q2",
        "--qsize", "8",
        "--ingrpc", "0.0.0.0:50153,2",
        "--outfile", $Forwarded,
        "--logfile", (Join-Path $Run "q2.log")
    )

    Start-Sleep -Seconds 1

    & python $Gen `
        --arrival-dist fixed --arrival-fixed 0 `
        --wait-dist uniform --wait-low 0.01 --wait-high 0.05 `
        --tasks 1 --count $Count --seed 7 `
        --out $Jobs
    if ($LASTEXITCODE -ne 0) {
        throw "workload generator exited $LASTEXITCODE"
    }

    & python $Queue `
        --qid q0 --qsize 8 `
        -i $Jobs `
        --outgrpc 127.0.0.1:50151,1 `
        --logfile (Join-Path $Run "q0.log")
    if ($LASTEXITCODE -ne 0) {
        throw "q0 exited $LASTEXITCODE"
    }

    Wait-DataLines -Path $Forwarded -Needed $Count -TimeoutSec 30
}
finally {
    Stop-Listeners
}

& python $Stateful `
    --qid q2 --server-id 0 `
    -i $Forwarded `
    -o $Sink `
    --logfile (Join-Path $Run "sf-stateful.log")
if ($LASTEXITCODE -ne 0) {
    throw "stateful server function exited $LASTEXITCODE"
}

$q0 = Join-Path $Run "q0.log"
$q1 = Join-Path $Run "q1.log"
$q2 = Join-Path $Run "q2.log"
$sf0 = Join-Path $Run "sf-stateless.log"
$sf1 = Join-Path $Run "sf-stateful.log"

Write-Output "jobs: $Count"
Write-Output ("q0 ARRIVE-VIA: " + (Count-Text $q0 "ARRIVE-VIA"))
Write-Output ("q0 DEPART-VIA: " + (Count-Text $q0 "DEPART-VIA"))
Write-Output ("stateless ENTERS-SERVER: " + (Count-Text $sf0 "ENTERS-SERVER"))
Write-Output ("q1 ARRIVE-VIA: " + (Count-Text $q1 "ARRIVE-VIA"))
Write-Output ("q1 DEPART-VIA: " + (Count-Text $q1 "DEPART-VIA"))
Write-Output ("q2 ARRIVE-VIA: " + (Count-Text $q2 "ARRIVE-VIA"))
Write-Output ("q2 DEPART-VIA: " + (Count-Text $q2 "DEPART-VIA"))
Write-Output ("stateful ENTERS-SERVER: " + (Count-Text $sf1 "ENTERS-SERVER"))
Write-Output ("stateful state-visits: " + (Count-Text $sf1 "state-visits"))
Write-Output ("QUEUE-FULL: " + (
    (Count-Text $q0 "QUEUE-FULL") + (Count-Text $q1 "QUEUE-FULL") + (Count-Text $q2 "QUEUE-FULL")
))
Write-Output "sink: $Sink"

& python (Join-Path $Repo "implementation\obj-a\mockups\analyze_run.py") $Run
if ($LASTEXITCODE -ne 0) {
    throw "analysis exited $LASTEXITCODE"
}
