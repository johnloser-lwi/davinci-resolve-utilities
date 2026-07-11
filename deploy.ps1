$source = $PSScriptRoot
$dest   = "C:\Users\john-\AppData\Roaming\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\John"

if (-not (Test-Path $dest)) {
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Write-Host "Created destination folder: $dest"
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
