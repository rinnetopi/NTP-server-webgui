<#
.SYNOPSIS
  Copy this project to the Raspberry Pi with scp and install it there.

.DESCRIPTION
  1. Packs the project (without .git, .vscode, PDFs, logs) into a tar file.
  2. scp's it to the Pi.
  3. ssh's in, unpacks to ~/ntp-server-webgui and runs deploy/remote_install.sh,
     which runs the bootstrap installer and prints post-install checks.
  All output is also written to deploy.log next to this script.

  You will be asked for the Pi password up to three times (scp, ssh, sudo).

.EXAMPLE
  .\deploy.ps1
  .\deploy.ps1 -PiHost 192.168.1.50 -Wan eth0 -Lan eth1 -LanIp 10.12.0.1
#>
param(
    [string]$PiHost = "192.168.1.239",
    [string]$User = "ntp-admin",
    [string]$Wan = "eth0",
    [string]$Lan = "eth1",
    [string]$LanIp = "10.12.0.1",
    [int]$LanPrefix = 16
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$log = Join-Path $root "deploy.log"
$tarFile = Join-Path $env:TEMP "ntp-webgui.tar"
$target = "$User@$PiHost"

# Open the log once and keep it open with sharing enabled: Dropbox/OneDrive lock
# files briefly while syncing, which breaks per-line Add-Content calls.
function Open-Log([string]$path) {
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $stream = New-Object System.IO.FileStream($path, [System.IO.FileMode]::Create,
                [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
            $writer = New-Object System.IO.StreamWriter($stream, (New-Object System.Text.UTF8Encoding($false)))
            $writer.AutoFlush = $true
            return $writer
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Could not open $path (locked by another program?)"
}

$logWriter = Open-Log $log
trap { if ($logWriter) { $logWriter.WriteLine("ERROR: $_"); $logWriter.Close() }; break }

function Write-Log([string]$line) {
    Write-Host $line
    $logWriter.WriteLine($line)
}

Write-Log "deploy.ps1 started $(Get-Date -Format s) -> $target (WAN=$Wan LAN=$Lan LAN_IP=$LanIp/$LanPrefix)"

Write-Log "[1/3] Packing project..."
Push-Location $root
try {
    & tar.exe -cf $tarFile --exclude=.git --exclude=.vscode --exclude=__pycache__ --exclude=*.pdf `
        --exclude=deploy.log --exclude=deploy.ps1 .
    if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

Write-Log "[2/3] Copying to ${target}:/tmp/ntp-webgui.tar (enter the Pi password if asked)..."
& scp.exe -o ConnectTimeout=10 $tarFile "${target}:/tmp/ntp-webgui.tar"
if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: scp failed ($LASTEXITCODE)"; $logWriter.Close(); exit 1 }
Remove-Item $tarFile -ErrorAction SilentlyContinue

Write-Log "[3/3] Installing on the Pi (enter the Pi password for ssh, then again for sudo)..."
$remote = "rm -rf ~/ntp-server-webgui && mkdir -p ~/ntp-server-webgui && " +
          "tar -xf /tmp/ntp-webgui.tar -C ~/ntp-server-webgui && rm -f /tmp/ntp-webgui.tar && " +
          "bash ~/ntp-server-webgui/deploy/remote_install.sh $Wan $Lan $LanIp $LanPrefix"

# ssh writes "Connection ... closed." to stderr; don't treat that as a script error.
$ErrorActionPreference = "Continue"
& ssh.exe -tt -o ConnectTimeout=10 $target $remote 2>&1 | ForEach-Object {
    $line = "$_" -replace "$([char]27)\[[0-9;?]*[A-Za-z]", ""
    Write-Log $line
}
$code = $LASTEXITCODE
Write-Log "deploy.ps1 finished with exit code $code at $(Get-Date -Format s)"
$logWriter.Close()
if ($code -eq 0) {
    Write-Host ""
    Write-Host "Open the UI from the LAN side: http://$LanIp  (or http://${LanIp}:5000)"
}
exit $code
