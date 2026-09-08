param([string]$OutputDir = "build\docs-qa")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$out = [IO.Path]::GetFullPath((Join-Path $repo $OutputDir))
New-Item -ItemType Directory -Force -Path $out | Out-Null
# Sous Windows, Word installé fournit le rendu natif lorsque LibreOffice
# n'est pas disponible. Cette instance n'ouvre que les documents de recette.
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    foreach ($file in Get-ChildItem -LiteralPath (Join-Path $repo "aquameasure-pyside\docs\word") -Filter *.docx) {
        $document = $word.Documents.Open($file.FullName, $false, $true)
        try {
            $pdf = Join-Path $out ($file.BaseName + ".pdf")
            $document.ExportAsFixedFormat($pdf, 17)
            Write-Output $pdf
        }
        finally { $document.Close(0) }
    }
}
finally { $word.Quit() }
