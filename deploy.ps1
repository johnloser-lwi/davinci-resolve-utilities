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
