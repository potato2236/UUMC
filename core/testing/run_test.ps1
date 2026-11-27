param(
    [Parameter(Mandatory = $false)]
    [string]$Profile = "",

    [Parameter(Mandatory = $false)]
    [string]$Script = "",

    [Parameter(Mandatory = $false)]
    [string]$Screenshot = "",

    [Parameter(Mandatory = $false)]
    [string]$WindowSize = "",

    [Parameter(Mandatory = $false)]
    [string]$WindowPos = "",

    [Parameter(Mandatory = $false)]
    [double]$Linger = 0,

    [switch]$Smoke,
    [switch]$Interactive,
    [switch]$WithAudio
)

$ErrorActionPreference = "Stop"
$runnerScript = Join-Path $PSScriptRoot "runner.py"

$pyArgs = @($runnerScript, "--launch", "--kill-existing", "--check-log")
if ($Profile) { $pyArgs += @("--profile", $Profile) }
if ($Script) { $pyArgs += @("--script", $Script) }
if ($Screenshot) { $pyArgs += @("--screenshot", $Screenshot) }
if ($WindowSize) { $pyArgs += @("--window-size", $WindowSize) }
if ($WindowPos) { $pyArgs += @("--window-pos", $WindowPos) }
if ($PSBoundParameters.ContainsKey("Linger")) { $pyArgs += @("--linger", ([string]$Linger).Replace(",", ".")) }
if ($Smoke) { $pyArgs += "--smoke" }
if ($Interactive) { $pyArgs += "--interactive" }
if ($WithAudio) { $pyArgs += "--with-audio" }

& python @pyArgs
exit $LASTEXITCODE
