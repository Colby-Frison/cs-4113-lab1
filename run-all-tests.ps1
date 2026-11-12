# Run the tests on this branch.
# Usage (from anywhere):
#   .\run-all-tests.ps1
#   .\run-all-tests.ps1 -Extended
#
# The default pass runs the Objective A and matchmaking unittest suites, then
# the C++ file self-tests when CMake is on PATH.
#
# -Extended prints the longer studies (KV smoke, the KV N x M sweep, the
# medium wait-job sanity check, and matchmaking Small through Very Large).
# Those commands are not launched. This branch is not the merge, so that
# list stays an open extension: invoke a line from Show-ExtendedStudies
# when a longer run should actually execute.

param(
    [switch]$Extended
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Invoke-Checked {
    param(
        [string]$Title,
        [scriptblock]$Body
    )
    Write-Host "==> $Title"
    & $Body
    if ($LASTEXITCODE -ne 0) {
        throw "$Title failed (exit $LASTEXITCODE)"
    }
}

function Find-Built {
    param(
        [string]$BuildDir,
        [string]$Name
    )
    $hits = Get-ChildItem -Path $BuildDir -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -eq $Name -or $_.Name -eq "$Name.exe") -and
            ($_.FullName -notmatch '\\CMakeFiles\\')
        }
    return $hits | Select-Object -First 1
}

function ConvertTo-WindowsArg {
    param([string]$Argument)
    # CommandLineToArgvW quoting, so a CSV row keeps its quotes.
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($ch in $Argument.ToCharArray()) {
        if ($ch -eq '\') {
            $slashes++
            continue
        }
        if ($ch -eq '"') {
            [void]$builder.Append('\', ($slashes * 2 + 1))
            [void]$builder.Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) {
            [void]$builder.Append('\', $slashes)
            $slashes = 0
        }
        [void]$builder.Append($ch)
    }
    if ($slashes -gt 0) {
        [void]$builder.Append('\', ($slashes * 2))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-Exe {
    param(
        [string]$File,
        [string[]]$ArgumentList
    )
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $File
    $start.UseShellExecute = $false
    $start.WorkingDirectory = (Get-Location).Path
    $escaped = foreach ($argument in $ArgumentList) { ConvertTo-WindowsArg $argument }
    $start.Arguments = ($escaped -join ' ')
    $process = [System.Diagnostics.Process]::Start($start)
    $process.WaitForExit()
    $script:LASTEXITCODE = $process.ExitCode
}

function Invoke-CppSelfTests {
    if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
        Write-Host "==> C++ file self-tests skipped (cmake is not on PATH)"
        return
    }

    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("lab1-selftest-" + [guid]::NewGuid().ToString("n"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $row = 'j0,0.000,"[""0: W 0.000000"", ""1: W 0.000000"", ""2: W 0.000000"", ""3: W 0.000000""]"'
        $jobs = Join-Path $tmp "jobs.csv"
        $sink = Join-Path $tmp "sink.csv"
        Set-Content -Path $jobs -Value ("Job Id,arrival time,Tasks`r`n" + $row) -Encoding ascii

        Invoke-Checked "configure queue-cpp" {
            cmake -S implementation/obj-a/queue-cpp -B implementation/obj-a/queue-cpp/build
        }
        Invoke-Checked "build queue-cpp" {
            cmake --build implementation/obj-a/queue-cpp/build
        }
        $queueTest = Find-Built "implementation/obj-a/queue-cpp/build" "queue_file_selftest"
        if (-not $queueTest) {
            throw "queue_file_selftest was not produced"
        }
        Invoke-Checked "queue_file_selftest" {
            Invoke-Exe $queueTest.FullName @($jobs, $sink)
        }

        Invoke-Checked "configure matchmaking cpp" {
            cmake -S implementation/obj-b.matchmaking/cpp -B implementation/obj-b.matchmaking/cpp/build
        }
        Invoke-Checked "build matchmaking cpp" {
            cmake --build implementation/obj-b.matchmaking/cpp/build
        }
        $matchTest = Find-Built "implementation/obj-b.matchmaking/cpp/build" "match_file_selftest"
        if (-not $matchTest) {
            throw "match_file_selftest was not produced"
        }
        Invoke-Checked "match_file_selftest" {
            Invoke-Exe $matchTest.FullName @($row, (Join-Path $tmp "match-sink.csv"))
        }
    } finally {
        if (Test-Path $tmp) {
            Remove-Item -Recurse -Force $tmp
        }
    }
}

function Show-ExtendedStudies {
    # Open on purpose. Nothing in this list is started.
    # To close one case, replace the Write-Host of that line with a call.
    Write-Host "==> extended testing left open (not launched)"
    Write-Host "    This branch is not planned for merge. These are the longer runs we had in mind."
    $commands = @(
        'python implementation/obj-a/kv-store/smoke_check.py --jobs 24 --configs "1,1;2,1;1,2;2,2"'
        'python implementation/obj-a/kv-store/studies/run_kv_study.py'
        'python implementation/obj-a/sanity-check/medium/run_medium.py'
        'python implementation/obj-b.matchmaking/studies/run_study.py --size small'
        'python implementation/obj-b.matchmaking/studies/run_study.py --size medium'
        'python implementation/obj-b.matchmaking/studies/run_study.py --size large'
        'python implementation/obj-b.matchmaking/studies/run_study.py --size very-large'
        'bash implementation/obj-b.matchmaking/studies/remote/run_medium.sh'
        'bash implementation/obj-b.matchmaking/studies/remote/run_large.sh'
        'bash implementation/obj-b.matchmaking/studies/remote/run_very_large.sh'
    )
    foreach ($command in $commands) {
        Write-Host "    $command"
    }
}

Invoke-Checked "Objective A unit tests" {
    python -m unittest discover -s implementation/obj-a/tests -v
}
Invoke-Checked "matchmaking unit tests" {
    python -m unittest discover -s implementation/obj-b.matchmaking/tests -v
}
Invoke-CppSelfTests

if ($Extended) {
    Show-ExtendedStudies
}

Write-Host "==> all tests finished"
Write-Host "    Extended studies stay open. Pass -Extended to list them; they are not run."
