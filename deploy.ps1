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

# --- Deploy .jsx scripts to After Effects (newest version found) ---
$jsxScripts = Get-ChildItem -Path $source -Recurse -Filter "*.jsx"
if ($jsxScripts.Count -gt 0) {
    $aeDirs = Get-ChildItem -Path "C:\Program Files\Adobe" -Directory -Filter "Adobe After Effects *" -ErrorAction SilentlyContinue | Sort-Object Name
    if ($aeDirs.Count -eq 0) {
        Write-Host "`nNo After Effects installation found - skipped .jsx deployment."
    } else {
        $aeScripts = Join-Path $aeDirs[-1].FullName "Support Files\Scripts"
        $jsxCopied = 0
        foreach ($file in $jsxScripts) {
            try {
                if (-not (Test-Path $aeScripts)) {
                    New-Item -ItemType Directory -Path $aeScripts -Force -ErrorAction Stop | Out-Null
                }
                Copy-Item -Path $file.FullName -Destination (Join-Path $aeScripts $file.Name) -Force -ErrorAction Stop
                Write-Host "Deployed to AE: $($file.Name)"
                $jsxCopied++
            } catch {
                Write-Host "WARNING: Could not copy '$($file.Name)' to '$aeScripts' (needs admin rights)."
                Write-Host "  Either run this deploy script elevated once, or run the .jsx from AE via File > Scripts > Run Script File."
            }
        }
        if ($jsxCopied -gt 0) {
            Write-Host "$jsxCopied .jsx script(s) deployed to:"
            Write-Host "  $aeScripts"
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
