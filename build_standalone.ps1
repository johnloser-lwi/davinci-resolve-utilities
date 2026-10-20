# Build Fusion Clipboard into a standalone .exe.
#
# The panel has no Resolve or Fusion dependency - it only moves text on and off
# the Windows clipboard - so it bundles as an ordinary Python/tkinter app.
#
# This is a RELEASE step, not part of the edit loop. deploy.ps1 deliberately
# does not call it: a build takes ~30s and most of the time you only want the
# script refreshed in Resolve. While iterating, launch "Fusion Clipboard.cmd",
# which runs the .py directly and needs no rebuild.
#
#   .\build_standalone.ps1              build to .\dist
#   .\build_standalone.ps1 -OneDir      folder instead of a single file
#   .\build_standalone.ps1 -Clean       discard cached build state first

[CmdletBinding()]
param(
    [switch]$OneDir,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$repo    = $PSScriptRoot
$script  = Join-Path $repo "Fusion\01_fusion_clipboard.py"
$appName = "Fusion Clipboard"

if (-not (Test-Path $script)) {
    Write-Host "Cannot find $script" -ForegroundColor Red
    exit 1
}

# --- Find a Python that can actually build this ---------------------------
# "python" on PATH is often the Microsoft Store shim, which exits without doing
# anything. Candidates are tested by asking them to import tkinter and report
# their real executable, so the one we pick is known-good.
function Get-BuildPython {
    $probe = "import sys, tkinter; sys.stdout.write(sys.executable)"
    foreach ($candidate in @(
        @{ Exe = "py";     Args = @("-3") },
        @{ Exe = "python"; Args = @() }
    )) {
        $cmd = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $argList = @($candidate.Args) + @("-c", $probe)
            $found = & $cmd.Source @argList 2>$null
            if ($LASTEXITCODE -eq 0 -and $found) {
                return [string]$found
            }
        } catch {
            continue
        }
    }
    return $null
}

$python = Get-BuildPython
if (-not $python) {
    Write-Host "No Python with tkinter found on PATH." -ForegroundColor Red
    Write-Host "Install Python (with the tcl/tk option ticked), or just use"
    Write-Host "'Fusion Clipboard.cmd' - it needs no build."
    exit 1
}

$pyVersion = & $python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Host "Python : $python  ($pyVersion)"

# --- Make sure PyInstaller is there ---------------------------------------
$hasPyInstaller = $false
try {
    $pv = & $python -m PyInstaller --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        $hasPyInstaller = $true
        Write-Host "PyInstaller : $pv"
    }
} catch { }

if (-not $hasPyInstaller) {
    Write-Host "PyInstaller not installed - installing it now..." -ForegroundColor Yellow
    & $python -m pip install --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "pip could not install PyInstaller." -ForegroundColor Red
        Write-Host "Python $pyVersion may be newer than PyInstaller supports."
        Write-Host "'Fusion Clipboard.cmd' still works and needs no build."
        exit 1
    }
}

# --- Build -----------------------------------------------------------------
$distDir = Join-Path $repo "dist"
$workDir = Join-Path $repo "build"

$buildArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--windowed",                 # no console window behind the panel
    "--name", $appName,
    "--distpath", $distDir,
    "--workpath", $workDir,
    "--specpath", $repo
)
if ($OneDir) {
    $buildArgs += "--onedir"
} else {
    $buildArgs += "--onefile"     # one portable file you can pin or copy
}
if ($Clean) { $buildArgs += "--clean" }

# The Studio-safe conversion rules are data the script loads at runtime, so they
# have to be bundled or the .exe falls back to the embedded defaults only.
$rules = Join-Path $repo "Fusion\studio_safe_rules.json"
if (Test-Path $rules) {
    $buildArgs += @("--add-data", "$rules;.")
    Write-Host "Bundling : studio_safe_rules.json"
}

# An icon is optional; PyInstaller errors out if told to use one that is absent.
$icon = Join-Path $repo "Fusion\fusion_clipboard.ico"
if (Test-Path $icon) {
    $buildArgs += @("--icon", $icon)
    Write-Host "Icon   : $icon"
}

$buildArgs += $script

Write-Host ""
Write-Host "Building '$appName'..." -ForegroundColor Cyan
& $python @buildArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Build failed." -ForegroundColor Red
    Write-Host "If PyInstaller does not yet support Python $pyVersion, either build"
    Write-Host "with an older Python or keep using 'Fusion Clipboard.cmd'."
    exit 1
}

# --- Report ----------------------------------------------------------------
if ($OneDir) {
    $exe = Join-Path $distDir "$appName\$appName.exe"
} else {
    $exe = Join-Path $distDir "$appName.exe"
}

Write-Host ""
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "Done. $appName.exe  ($size MB)" -ForegroundColor Green
    Write-Host "  $exe"
    Write-Host ""
    Write-Host "Right-click it to pin to Start or the taskbar. It reads the same"
    Write-Host "library as the Resolve copy (%APPDATA%\FusionClipboard), so presets"
    Write-Host "are shared between them."
    Write-Host ""
    Write-Host "The .exe embeds a snapshot of the code - re-run this after editing"
    Write-Host "the script. 'Fusion Clipboard.cmd' always runs the current source."
} else {
    Write-Host "Build reported success but $exe is missing." -ForegroundColor Red
    Write-Host "Check the PyInstaller output above."
    exit 1
}
