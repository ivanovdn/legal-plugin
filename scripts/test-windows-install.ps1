# Checks the PowerShell in docs/tester-setup.md Part B without a Windows machine.
#
#   brew install powershell && pwsh -NoProfile -File scripts/test-windows-install.ps1
#
# Windows-only cmdlets (Get-SmbShare, the HKCU: provider) are stubbed, so this
# exercises the LOGIC, not the platform: that the hosts line lands on its own row
# whether or not the file ends in a newline, that the GUID keeps the braces
# Microsoft requires, that a second run adds nothing, and — the claim the guide
# makes to a legal reader — that an unrelated add-in catalogue already on the
# machine is neither edited nor removed.
#
# NOT wired into scripts/check.sh: it needs pwsh, which the rest of this repo
# does not. Run it by hand after editing any PowerShell in that guide.
# Separately, a parse check of every ```powershell block in the guide is worth
# re-running after edits; see the Parser::ParseFile loop in that doc's history.

$env:COMPUTERNAME = 'TESTPC'; $env:USERDOMAIN = 'TESTPC'; $env:USERNAME = 'dmytro'
$script:fail = 0
function Check($name, $got, $want) {
  if ("$got" -eq "$want") { "  PASS  $name" }
  else { $script:fail++; "  FAIL  $name  got:[$got] want:[$want]" }
}

"--- B2: what Add-Content actually writes into hosts ---"
$tmp = [System.IO.Path]::GetTempPath()
# Case 1: a hosts file with NO trailing newline (the case the leading `n exists for)
$h1 = Join-Path $tmp "h1"; [System.IO.File]::WriteAllText($h1, "127.0.0.1`tlocalhost")
Add-Content -Path $h1 -Value "`n172.20.1.10`tlegal-triage.internal.trinetix.net"
$c1 = [System.IO.File]::ReadAllText($h1)
Check "no-trailing-newline: previous line intact" ($c1 -match "(?m)^127\.0\.0\.1`tlocalhost$") $true
Check "no-trailing-newline: our entry on its own line" ($c1 -match "(?m)^172\.20\.1\.10`tlegal-triage\.internal\.trinetix\.net$") $true
Check "no-trailing-newline: lines are not welded together" ($c1 -match "localhost172") $false

# Case 2: a normal hosts file WITH a trailing newline
$h2 = Join-Path $tmp "h2"; [System.IO.File]::WriteAllText($h2, "127.0.0.1`tlocalhost`n")
Add-Content -Path $h2 -Value "`n172.20.1.10`tlegal-triage.internal.trinetix.net"
$c2 = [System.IO.File]::ReadAllText($h2)
Check "trailing-newline: our entry on its own line" ($c2 -match "(?m)^172\.20\.1\.10`tlegal-triage\.internal\.trinetix\.net$") $true
Check "trailing-newline: costs only one blank line" (($c2 -split "`r?`n" | Where-Object { $_ -eq '' }).Count -le 2) $true
Check "backticks never survive literally" ($c1.Contains('`n') -or $c2.Contains('`n')) $false

"--- B5: interpolation ---"
Check "UNC path"    "\\$env:COMPUTERNAME\LegalTriage" '\\TESTPC\LegalTriage'
Check "share grant" "$env:USERDOMAIN\$env:USERNAME"   'TESTPC\dmytro'
$guid = [guid]::NewGuid().ToString('B')
Check "GUID keeps braces" ($guid -match '^\{[0-9a-fA-F-]{36}\}$') $true
Check "string-concat key path (no provider drive needed)" ("HKCU:\A\B\$guid") "HKCU:\A\B\$guid"

"--- B5: idempotency + blast radius, registry stubbed ---"
$script:reg = @{}
function Test-Path       { [CmdletBinding()] param([Parameter(Position=0)]$Path) $script:reg.ContainsKey($Path) }
function New-Item        { [CmdletBinding()] param([Parameter(Position=0)]$Path, [switch]$Force) $script:reg[$Path] = @{}; [PSCustomObject]@{ PSPath = $Path } }
function Get-ChildItem   { [CmdletBinding()] param([Parameter(Position=0)]$Path) @($script:reg.Keys | Where-Object { $_ -like "$Path\*" } | ForEach-Object { [PSCustomObject]@{ PSPath = $_ } }) }
function Get-ItemProperty{ [CmdletBinding()] param([Parameter(Position=0)]$Path) if ($script:reg.ContainsKey($Path)) { [PSCustomObject]$script:reg[$Path] } }
function New-ItemProperty{ [CmdletBinding()] param($Path, $Name, $Value, $PropertyType, [switch]$Force) $script:reg[$Path][$Name] = $Value }

function Invoke-TheBlock {
  $url  = "\\$env:COMPUTERNAME\LegalTriage"
  $root = "HKCU:\Software\Microsoft\Office\16.0\WEF\TrustedCatalogs"
  if (-not (Test-Path $root)) { New-Item -Path $root -Force | Out-Null }
  $existing = Get-ChildItem $root -ErrorAction SilentlyContinue |
    Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).Url -eq $url }
  if (-not $existing) {
    $guid = [guid]::NewGuid().ToString('B')
    $key  = "$root\$guid"
    New-Item -Path $key | Out-Null
    New-ItemProperty -Path $key -Name Id    -Value $guid -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name Url   -Value $url  -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name Flags -Value 1     -PropertyType DWord  -Force | Out-Null
  }
}

$root = "HKCU:\Software\Microsoft\Office\16.0\WEF\TrustedCatalogs"
$script:reg[$root] = @{}
$corp = "$root\{99999999-0000-0000-0000-000000000000}"
$script:reg[$corp] = @{ Id='{99999999-0000-0000-0000-000000000000}'; Url='\\CORP\OtherAddins'; Flags=1 }

Invoke-TheBlock; $after1 = @($script:reg.Keys | Where-Object { $_ -like "$root\*" }).Count
Invoke-TheBlock; Invoke-TheBlock
$after3 = @($script:reg.Keys | Where-Object { $_ -like "$root\*" }).Count

Check "run 1 adds ours beside the existing one" $after1 2
Check "runs 2 and 3 add nothing"                $after3 2
Check "pre-existing corporate catalog untouched" $script:reg[$corp].Url '\\CORP\OtherAddins'
$ours = @($script:reg.Keys | Where-Object { $_ -like "$root\*" -and $script:reg[$_].Url -eq '\\TESTPC\LegalTriage' })[0]
Check "ours has Flags=1"           $script:reg[$ours].Flags 1
Check "ours: Id equals key name"   $script:reg[$ours].Id ($ours -replace '^.*\\','')

"--- Removal filter selects only ours ---"
$doomed = @($script:reg.Keys | Where-Object { $_ -like "$root\*" -and $script:reg[$_].Url -like "*LegalTriage*" })
Check "matches exactly one key" $doomed.Count 1
Check "and not the corporate one" ($doomed[0] -eq $corp) $false

"--- B4: the LASTEXITCODE check reports correctly ---"
# `if` is a statement, not an argument expression — it needs $() to be one.
& /usr/bin/true
Check "success path" $(if ($LASTEXITCODE -eq 0) {'OK'} else {'FAILED'}) 'OK'
& /usr/bin/false
Check "a non-zero exit is NOT reported as success" $(if ($LASTEXITCODE -eq 0) {'OK'} else {'FAILED'}) 'FAILED'

""
if ($script:fail -eq 0) { "ALL PASS" } else { "$($script:fail) FAILED"; exit 1 }
