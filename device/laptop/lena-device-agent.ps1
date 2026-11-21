# Lena device agent (Windows, PowerShell) - nothing to install.
#
# Windows already has PowerShell, so this needs no Python, no pip, no downloads.
#
# RUN IT
#   1. Open PowerShell
#   2. cd to the folder containing this file
#   3. $env:LENA_API_KEY = "your_personal_base44_key"
#   4. powershell -ExecutionPolicy Bypass -File .\lena-device-agent.ps1
#
# ACTIONS: ping, status, open_url, open_app, screenshot, lock_screen,
#          volume_up, volume_down, mute, say
# No shell. No arbitrary commands. Ctrl+C stops it.

param(
    [string]$ApiKey = $env:LENA_API_KEY,
    [string]$AppId = "6a0ebc236c3234fc81a8acf3",
    [string]$Base = "https://app.base44.com",
    [string]$Device = "laptop",
    [int]$PollSeconds = 4
)

$Entity = "DeviceCommand"
$Allowed = @("ping","status","open_url","open_app","screenshot","lock_screen","volume_up","volume_down","mute","say","clipboard_get","clipboard_set","file_put","file_get")

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    Write-Host "No API key found." -ForegroundColor Yellow
    Write-Host '  $env:LENA_API_KEY = "your_personal_base44_key"'
    exit 1
}

$Headers = @{ "Authorization" = "Bearer $ApiKey"; "Content-Type" = "application/json" }
$Desktop = [Environment]::GetFolderPath("Desktop")

function Update-Record($Id, $Status, $Result) {
    $body = @{ status = $Status; result = "$Result" } | ConvertTo-Json -Compress
    try {
        Invoke-RestMethod -Method Put -Uri "$Base/api/apps/$AppId/entities/$Entity/$Id" `
            -Headers $Headers -Body $body -TimeoutSec 30 | Out-Null
    } catch {
        Write-Host "  [warn] could not save result: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }
}

function Get-Pending {
    $q = @{ status = "pending"; device = $Device } | ConvertTo-Json -Compress
    $uri = "$Base/api/apps/$AppId/entities/$Entity/v2/list?q=" + [uri]::EscapeDataString($q) + "&limit=5"
    try {
        $res = Invoke-RestMethod -Method Get -Uri $uri -Headers $Headers -TimeoutSec 30
        return @($res.items)
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 401) {
            Write-Host "Base44 rejected the key (401). Check it is a personal API key." -ForegroundColor Red
            exit 1
        }
        Write-Host "  [net] $($_.Exception.Message)" -ForegroundColor DarkYellow
        return @()
    }
}

function Invoke-Action($Command, $Args) {
    switch ($Command) {
        "ping"        { return "laptop agent alive at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" }
        "status"      { return "host=$env:COMPUTERNAME; os=$([System.Environment]::OSVersion.VersionString); time=$(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
        "open_url"    {
            if (-not $Args) { throw "open_url needs a URL" }
            $u = $Args.Trim()
            if ($u -notmatch '^https?://') { $u = "https://$u" }
            Start-Process $u; return "opened $u"
        }
        "open_app"    {
            if (-not $Args) { throw "open_app needs an application name" }
            Start-Process $Args.Trim(); return "launched $Args"
        }
        "screenshot"  {
            Add-Type -AssemblyName System.Windows.Forms, System.Drawing
            $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
            $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
            $g = [System.Drawing.Graphics]::FromImage($bmp)
            $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
            $path = Join-Path $Desktop ("lena_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".png")
            $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
            $g.Dispose(); $bmp.Dispose()
            return "saved $path"
        }
        "lock_screen" { rundll32.exe user32.dll,LockWorkStation; return "screen locked" }
        "volume_up"   { (New-Object -ComObject WScript.Shell).SendKeys([char]175); return "volume up" }
        "volume_down" { (New-Object -ComObject WScript.Shell).SendKeys([char]174); return "volume down" }
        "mute"        { (New-Object -ComObject WScript.Shell).SendKeys([char]173); return "mute toggled" }
        "say"         {
            $text = if ($Args) { $Args } else { "Done." }
            Add-Type -AssemblyName System.Speech
            $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $s.Speak($text); return "spoken"
        }
        "clipboard_get" { $t = Get-Clipboard -Raw; if (-not $t) { return "clipboard is empty" }; return "CLIP:" + $t.Substring(0, [Math]::Min(4000, $t.Length)) }
        "clipboard_set" { Set-Clipboard -Value $Args; return "clipboard set" }
        "file_put"      {
            $path = $Args.Trim().Trim('"')
            if (-not (Test-Path $path)) { throw "no such file: $path" }
            $size = (Get-Item $path).Length
            if ($size -gt 400000) { throw "file is $([int]($size/1024))KB; the queue carries up to 400KB" }
            $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
            return "FILE:" + (Split-Path $path -Leaf) + ":" + $b64
        }
        "file_get"      {
            if ($Args -notmatch '\|') { throw "file_get needs '<record_id>|<filename>'" }
            $parts = $Args.Split('|', 2)
            $rec = Invoke-RestMethod -Method Get -Uri "$Base/api/apps/$AppId/entities/$Entity/$($parts[0].Trim())" -Headers $Headers -TimeoutSec 40
            if (-not $rec.result -or -not $rec.result.StartsWith("FILE:")) { throw "that record does not hold a file" }
            $seg = $rec.result.Split(':', 3)
            $out = Join-Path $Desktop ($parts[1].Trim())
            [IO.File]::WriteAllBytes($out, [Convert]::FromBase64String($seg[2]))
            return "saved $out"
        }
        default       { throw "refused: '$Command' is not an allowed action" }
    }
}

Write-Host "Lena Windows agent is running. Nothing was installed." -ForegroundColor Green
Write-Host "  device tag: $Device"
Write-Host "  polling:    every $PollSeconds s"
Write-Host "  actions:    $($Allowed -join ', ')"
Write-Host "  Leave this window open. Ctrl+C stops it.`n"

while ($true) {
    foreach ($rec in Get-Pending) {
        $cmd = "$($rec.command)".Trim()
        $arg = "$($rec.args)"
        if ($Allowed -notcontains $cmd) {
            Update-Record $rec.id "error" "refused: '$cmd' not allowed on laptop"
            Write-Host "[refused] $cmd" -ForegroundColor DarkYellow
            continue
        }
        Write-Host "[run] $cmd $arg"
        Update-Record $rec.id "running" ""
        try {
            $result = Invoke-Action $cmd $arg
            Update-Record $rec.id "done" $result
            Write-Host "  ok: $result" -ForegroundColor Green
        } catch {
            Update-Record $rec.id "error" $_.Exception.Message
            Write-Host "  failed: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    Start-Sleep -Seconds $PollSeconds
}
