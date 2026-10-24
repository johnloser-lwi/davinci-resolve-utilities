# Install the After Effects scripts (.jsx) into AE's own Scripts folder.
#
# That folder lives under Program Files, so writing to it needs admin rights,
# which deploy.ps1 doesn't have. This asks for them once (a Windows UAC prompt)
# and copies every .jsx in the repo into the newest After Effects found.
#
# deploy.ps1 tells you when this is needed - it compares the installed copy
# with the repo and only shouts when they actually differ.

$repo = $PSScriptRoot

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "Asking Windows for permission to write to Program Files..."
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    )
    exit
}

$aeDirs = Get-ChildItem -Path "C:\Program Files\Adobe" -Directory -Filter "Adobe After Effects *" `
    -ErrorAction SilentlyContinue | Sort-Object Name
if ($aeDirs.Count -eq 0) {
    Write-Host "No After Effects installation found under C:\Program Files\Adobe." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$aeScripts = Join-Path $aeDirs[-1].FullName "Support Files\Scripts"
if (-not (Test-Path $aeScripts)) {
    New-Item -ItemType Directory -Path $aeScripts -Force | Out-Null
}

$installed = 0
foreach ($file in (Get-ChildItem -Path $repo -Recurse -Filter "*.jsx")) {
    $target = Join-Path $aeScripts $file.Name
    Copy-Item -Path $file.FullName -Destination $target -Force
    Write-Host "Installed: $($file.Name)" -ForegroundColor Green
    $installed++
}

Write-Host ""
Write-Host "$installed script(s) installed to:"
Write-Host "  $aeScripts"
Write-Host ""
Write-Host "After Effects picks them up next time you open File > Scripts."
Read-Host "Press Enter to close"
