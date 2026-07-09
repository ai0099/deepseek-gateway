$ErrorActionPreference = "SilentlyContinue"

Write-Host "=== Anthropic / Claude / ClaudeCode ==="
$anthropicPaths = @(
    "HKCU:\Software\Anthropic",
    "HKLM:\Software\Anthropic",
    "HKCU:\Software\Claude",
    "HKLM:\Software\Claude",
    "HKCU:\Software\ClaudeCode",
    "HKLM:\Software\ClaudeCode"
)
foreach ($p in $anthropicPaths) {
    if (Test-Path $p) {
        Write-Host "`n$p"
        Get-ChildItem -Path $p -Recurse 2>$null | ForEach-Object {
            $itemPath = $_.PSPath
            Write-Host "  [$itemPath]"
            $props = Get-ItemProperty -Path $itemPath 2>$null
            $props.PSObject.Properties | Where-Object { $_.Name -notmatch '^(PSPath|PSParentPath|PSChildName|PSDrive|PSProvider|PSIsContainer)$' } | ForEach-Object {
                Write-Host "    $($_.Name) = $($_.Value)"
            }
        }
    }
}

Write-Host "`n=== OpenAI / Codex ==="
$openaiPaths = @(
    "HKCU:\Software\OpenAI",
    "HKLM:\Software\OpenAI",
    "HKCU:\Software\Codex",
    "HKLM:\Software\Codex"
)
foreach ($p in $openaiPaths) {
    if (Test-Path $p) {
        Write-Host "`n$p"
        Get-ChildItem -Path $p -Recurse 2>$null | ForEach-Object {
            $itemPath = $_.PSPath
            Write-Host "  [$itemPath]"
            $props = Get-ItemProperty -Path $itemPath 2>$null
            $props.PSObject.Properties | Where-Object { $_.Name -notmatch '^(PSPath|PSParentPath|PSChildName|PSDrive|PSProvider|PSIsContainer)$' } | ForEach-Object {
                Write-Host "    $($_.Name) = $($_.Value)"
            }
        }
    }
}

Write-Host "`n=== Environment Variables (model/context/effort-related) ==="
Get-ChildItem Env: 2>$null | Where-Object { $_.Name -match 'model|context|effort|token|claude|codex|anthropic|openai|reasoning' } | ForEach-Object {
    Write-Host "  $($_.Name) = $($_.Value)"
}

Write-Host "`n=== DONE ==="
