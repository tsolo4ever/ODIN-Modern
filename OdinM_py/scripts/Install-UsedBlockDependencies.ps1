[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Distribution,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$requiredTools = @(
    'partclone.fat',
    'partclone.ntfs',
    'partclone.extfs',
    'partclone.restore',
    'partclone.chkimg',
    'blkid',
    'lsblk',
    'blockdev',
    'mkswap',
    'sync'
)
$packages = @('partclone', 'util-linux', 'coreutils')

function Invoke-TargetWsl {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [switch]$AsRoot,
        [int[]]$AllowedExitCodes = @(0)
    )

    $wslArguments = @()
    if ($Distribution) {
        $wslArguments += @('-d', $Distribution)
    }
    if ($AsRoot) {
        $wslArguments += @('-u', 'root')
    }
    $wslArguments += '--'
    $wslArguments += $Arguments

    $output = & wsl.exe @wslArguments
    if ($LASTEXITCODE -notin $AllowedExitCodes) {
        throw "WSL command failed with exit code $LASTEXITCODE."
    }
    return $output
}

function Test-TargetCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    $wslArguments = @()
    if ($Distribution) {
        $wslArguments += @('-d', $Distribution)
    }
    $wslArguments += @('--', 'sh', '-lc', "command -v $Name >/dev/null 2>&1")
    & wsl.exe @wslArguments | Out-Null
    return $LASTEXITCODE -eq 0
}

function Get-MissingTools {
    return @($requiredTools | Where-Object { -not (Test-TargetCommand -Name $_) })
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL is not installed or wsl.exe is not available.'
}

$osRelease = @{}
foreach ($line in @(Invoke-TargetWsl -Arguments @('cat', '/etc/os-release'))) {
    if ($line -match '^([A-Z_]+)=(.*)$') {
        $osRelease[$Matches[1]] = $Matches[2].Trim('"')
    }
}
if (-not $osRelease.ContainsKey('ID') -or -not $osRelease.ContainsKey('PRETTY_NAME')) {
    throw 'Could not identify the target WSL distribution.'
}
$idLike = if ($osRelease.ContainsKey('ID_LIKE')) { $osRelease['ID_LIKE'] } else { '' }
$distributionFamily = ($osRelease['ID'] + ' ' + $idLike).ToLowerInvariant()
if ($distributionFamily -notmatch '(^|\s)(ubuntu|debian)(\s|$)') {
    throw "Only Debian/Ubuntu WSL is supported by this installer. Found: $($osRelease['PRETTY_NAME'])"
}

$targetLabel = if ($Distribution) {
    "$Distribution ($($osRelease['PRETTY_NAME']))"
} else {
    "the default WSL distribution ($($osRelease['PRETTY_NAME']))"
}
$missing = @(Get-MissingTools)

Write-Host "Target: $targetLabel"
if ($missing.Count -eq 0) {
    Write-Host 'All OdinM general used-block dependencies are installed.' -ForegroundColor Green
    Invoke-TargetWsl -Arguments @('partclone.restore', '--version') -AllowedExitCodes @(0, 1)
    return
}

Write-Host ('Missing commands: ' + ($missing -join ', ')) -ForegroundColor Yellow
Write-Host ('APT packages: ' + ($packages -join ', '))

if ($CheckOnly) {
    Write-Host 'Dependency check failed. No packages were installed.' -ForegroundColor Red
    exit 1
}

$operation = 'run apt-get update and install ' + ($packages -join ', ')
if ($WhatIfPreference) {
    [void]$PSCmdlet.ShouldProcess($targetLabel, $operation)
    return
}

$answer = Read-Host "Install these packages in $targetLabel now? [y/N]"
if ($answer -notmatch '^(?i:y|yes)$') {
    Write-Host 'Cancelled. No packages were installed.'
    return
}

if ($PSCmdlet.ShouldProcess($targetLabel, $operation)) {
    Invoke-TargetWsl -AsRoot -Arguments @('apt-get', 'update')
    $installArguments = @('apt-get', 'install', '--yes', '--no-install-recommends') + $packages
    Invoke-TargetWsl -AsRoot -Arguments $installArguments
}

$remaining = @(Get-MissingTools)
if ($remaining.Count -ne 0) {
    throw 'Installation finished, but commands are still missing: ' + ($remaining -join ', ')
}

Write-Host 'All OdinM general used-block dependencies are installed.' -ForegroundColor Green
Invoke-TargetWsl -Arguments @('partclone.restore', '--version') -AllowedExitCodes @(0, 1)
