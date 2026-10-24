$source = $PSScriptRoot
$dest   = "C:\Users\john-\AppData\Roaming\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\John"

# --- Back up the keyboard mapping before touching any scripts -------------
# Resolve stores script shortcuts by FILE PATH (verified in keyboard.preset.xml:
# fuHotkey_FlowView_RunScript{filename = 'Scripts:/Utility/...'}). Adding a
# script is safe, but renaming or removing one orphans its shortcut. Keep a
# rolling backup so a mapping can always be restored.
$kbPreset = "$env:APPDATA\Blackmagic Design\DaVinci Resolve\Preferences\keyboard.preset.xml"
if (Test-Path $kbPreset) {
    $backupDir = Join-Path $source ".keyboard-backups"
    if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Path $backupDir -Force | Out-Null }
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Copy-Item $kbPreset (Join-Path $backupDir "keyboard.preset.$stamp.xml") -Force
    # keep the 10 most recent
    Get-ChildItem $backupDir -Filter "keyboard.preset.*.xml" |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Host "Backed up keyboard mapping -> .keyboard-backups\keyboard.preset.$stamp.xml"
}

if (-not (Test-Path $dest)) {
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Write-Host "Created destination folder: $dest"
} else {
    # Mirror the repo: remove previously deployed .py files so renamed/removed
    # scripts don't linger as orphans (duplicate menu entries) in Resolve.
    $removed = Get-ChildItem -Path $dest -Recurse -Filter "*.py" -ErrorAction SilentlyContinue
    foreach ($old in $removed) { Remove-Item $old.FullName -Force }
    if ($removed.Count -gt 0) {
        Write-Host "Cleared $($removed.Count) previously deployed .py file(s)."
    }
}

$scripts = Get-ChildItem -Path $source -Recurse -Filter "*.py"

if ($scripts.Count -eq 0) {
    Write-Host "No .py scripts found in $source"
    exit
}

$copied = 0
foreach ($file in $scripts) {
    $relative  = $file.FullName.Substring($source.Length).TrimStart('\')
    $target    = Join-Path $dest $relative
    $targetDir = Split-Path $target -Parent
    if (-not (Test-Path $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }
    Copy-Item -Path $file.FullName -Destination $target -Force
    Write-Host "Deployed: $relative"
    $copied++
}

Write-Host "`nDone. $copied script(s) deployed to:"
Write-Host "  $dest"

# --- Data files that sit beside a script ---
# The Fusion Clipboard's Studio-safe conversion rules are data, not code, so
# they would otherwise never reach Resolve's scripts folder.
# Only JSON that sits in a script folder - never dot-folders like .claude or
# .git, and never build output.
$dataFiles = Get-ChildItem -Path $source -Recurse -Filter "*.json" |
    Where-Object { $_.FullName -notmatch '\\\.[^\\]+\\' -and
                   $_.FullName -notmatch '\\(build|dist)\\' }
foreach ($file in $dataFiles) {
    $relative  = $file.FullName.Substring($source.Length).TrimStart('\')
    $target    = Join-Path $dest $relative
    $targetDir = Split-Path $target -Parent
    if (-not (Test-Path $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }
    Copy-Item -Path $file.FullName -Destination $target -Force
    Write-Host "Deployed data: $relative"
}

# --- Deploy .jsx scripts to After Effects (newest version found) ---
$jsxScripts = Get-ChildItem -Path $source -Recurse -Filter "*.jsx"
if ($jsxScripts.Count -gt 0) {
    $aeDirs = Get-ChildItem -Path "C:\Program Files\Adobe" -Directory -Filter "Adobe After Effects *" -ErrorAction SilentlyContinue | Sort-Object Name
    if ($aeDirs.Count -eq 0) {
        Write-Host "`nNo After Effects installation found - skipped .jsx deployment."
    } else {
        $aeScripts = Join-Path $aeDirs[-1].FullName "Support Files\Scripts"
        $jsxCopied = 0
        $stale = @()
        foreach ($file in $jsxScripts) {
            $target = Join-Path $aeScripts $file.Name
            # Only act when the installed copy actually differs. The old
            # behaviour warned on EVERY deploy, so a genuinely outdated script
            # looked exactly like routine noise - which is how AE ended up
            # running a months-old FxLink Export with no sign anything was wrong.
            if ((Test-Path $target) -and
                ((Get-FileHash $target).Hash -eq (Get-FileHash $file.FullName).Hash)) {
                Write-Host "AE script up to date: $($file.Name)"
                continue
            }
            try {
                if (-not (Test-Path $aeScripts)) {
                    New-Item -ItemType Directory -Path $aeScripts -Force -ErrorAction Stop | Out-Null
                }
                Copy-Item -Path $file.FullName -Destination $target -Force -ErrorAction Stop
                Write-Host "Deployed to AE: $($file.Name)"
                $jsxCopied++
            } catch {
                if (Test-Path $target) { $stale += "$($file.Name)  (outdated)" }
                else { $stale += "$($file.Name)  (not installed yet)" }
            }
        }
        if ($jsxCopied -gt 0) {
            Write-Host "$jsxCopied .jsx script(s) deployed to:"
            Write-Host "  $aeScripts"
        }
        if ($stale.Count -gt 0) {
            Write-Host ""
            Write-Host "!! After Effects needs these scripts updated:" -ForegroundColor Red
            foreach ($name in $stale) { Write-Host "     $name" -ForegroundColor Red }
            Write-Host "   Program Files needs admin rights. Run this once to update it:" -ForegroundColor Red
            Write-Host "     .\install_ae_scripts.ps1      (Windows will ask for permission)" -ForegroundColor Red
        }
    }
}

# --- Deploy .js scripts to Cavalry ---
# Cavalry creates its Scripts folder on demand, and the user-content root is
# Roaming (Palettes, Third-Party live there) while Local holds preferences.
# Probe both so this is self-correcting; Help > Show Scripts Folder is the
# authority if it ever picks wrong.
$allJs = Get-ChildItem -Path $source -Recurse -Filter "*.js"
# Anything under a "core" folder is shared library code, not a menu entry. It
# goes to %APPDATA%\motion_link instead, because every .js in Cavalry's Scripts
# folder shows up under Window > Scripts whether it is runnable or not.
$coreJs = $allJs | Where-Object { $_.FullName -match '\\core\\' }
$jsScripts = $allJs | Where-Object { $_.FullName -notmatch '\\core\\' }

if ($coreJs.Count -gt 0) {
    $mlInbox = "$env:APPDATA\motion_link"
    if (-not (Test-Path $mlInbox)) { New-Item -ItemType Directory -Path $mlInbox -Force | Out-Null }
    foreach ($file in $coreJs) {
        Copy-Item -Path $file.FullName -Destination (Join-Path $mlInbox $file.Name) -Force
        Write-Host "Deployed shared core: $($file.Name) -> $mlInbox"
    }
}

if ($jsScripts.Count -gt 0) {
    $cavRoot = @("$env:APPDATA\Cavalry", "$env:LOCALAPPDATA\Cavalry") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $cavRoot) {
        Write-Host "`nNo Cavalry data folder found - skipped .js deployment."
    } else {
        # A subfolder, so both scripts group under one Window > Scripts submenu
        # instead of cluttering its root.
        $cavScripts = Join-Path $cavRoot "Scripts\John"
        if (-not (Test-Path $cavScripts)) {
            New-Item -ItemType Directory -Path $cavScripts -Force | Out-Null
            Write-Host "`nCreated Cavalry scripts folder: $cavScripts"
        } else {
            # Mirror the repo, same reasoning as the .py leg: a renamed script
            # would otherwise linger as a duplicate menu entry.
            $staleJs = Get-ChildItem -Path $cavScripts -Recurse -Filter "*.js" -ErrorAction SilentlyContinue
            foreach ($old in $staleJs) { Remove-Item $old.FullName -Force }
        }
        $jsCopied = 0
        foreach ($file in $jsScripts) {
            Copy-Item -Path $file.FullName -Destination (Join-Path $cavScripts $file.Name) -Force
            Write-Host "Deployed to Cavalry: $($file.Name)"
            $jsCopied++
        }
        Write-Host "$jsCopied .js script(s) deployed to:"
        Write-Host "  $cavScripts"
    }
}
