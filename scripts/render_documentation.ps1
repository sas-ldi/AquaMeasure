param([string]$OutputDir = "docs\pdf")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$out = [IO.Path]::GetFullPath((Join-Path $repo $OutputDir))
$guides = Get-Content -LiteralPath (Join-Path $repo "docs\documents.json") -Raw -Encoding UTF8 | ConvertFrom-Json
# Instance Word réservée au rendu ; aucun document utilisateur n'est ouvert.
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
$word.Options.UpdateLinksAtOpen = $false
try {
    foreach ($guide in $guides) {
        $source = Join-Path $repo ("docs\word\" + $guide.category + "\" + $guide.stem + ".docx")
        $destination = Join-Path $out $guide.category
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
        $document = $word.Documents.Open($source, $false, $true)
        try {
            $pdf = Join-Path $destination ($guide.stem + ".pdf")
            $document.ExportAsFixedFormat($pdf, 17)
            Write-Output $pdf
        }
        finally { $document.Close(0) }
    }
}
finally { $word.Quit() }
