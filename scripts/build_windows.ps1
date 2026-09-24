param(
    [string]$Python = ".venv\Scripts\python.exe",
    [string]$ReleaseName = "",
    # « legere » : suite Fishial et petits modèles seulement (postes à faible débit).
    [ValidateSet("complete", "legere")]
    [string]$Edition = "complete",
    [string]$CpuOverlay = "build\pyinstaller-cpu-overlay",
    [string]$DocumentationPdfDir = "",
    [switch]$SkipArchive
)

$ErrorActionPreference = "Stop"
if (-not $ReleaseName) {
    $ReleaseName = "AquaMeasure-Windows-x64-20260924"
    if ($Edition -eq "legere") { $ReleaseName += "-Legere" }
}
$repo = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repo $Python
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python introuvable : $pythonPath"
}

$cpuOverlayPath = Join-Path $repo $CpuOverlay
$cpuTorch = Join-Path $cpuOverlayPath "torch\__init__.py"
$cpuTorchvision = Join-Path $cpuOverlayPath "torchvision\__init__.py"
if (-not (Test-Path -LiteralPath $cpuTorch) -or -not (Test-Path -LiteralPath $cpuTorchvision)) {
    New-Item -ItemType Directory -Force -Path $cpuOverlayPath | Out-Null
    & $pythonPath -m pip install `
        --disable-pip-version-check `
        --no-deps `
        --upgrade `
        --target $cpuOverlayPath `
        --index-url "https://download.pytorch.org/whl/cpu" `
        "torch==2.11.0+cpu" `
        "torchvision==0.26.0+cpu"
    if ($LASTEXITCODE -ne 0) {
        throw "Téléchargement du runtime PyTorch CPU échoué (code $LASTEXITCODE)."
    }
}

$previousPythonPath = $env:PYTHONPATH
Push-Location $repo
try {
    $env:PYTHONPATH = if ($previousPythonPath) {
        $cpuOverlayPath + [IO.Path]::PathSeparator + (Join-Path $repo "src") + [IO.Path]::PathSeparator + $previousPythonPath
    }
    else {
        $cpuOverlayPath + [IO.Path]::PathSeparator + (Join-Path $repo "src")
    }
    $extras = Join-Path $repo "build\release-extras"
    if (Test-Path -LiteralPath $extras) {
        $env:PYTHONPATH += [IO.Path]::PathSeparator + $extras
    }

    & $pythonPath -c @"
import torch
import torchvision
import ultralytics
assert torch.version.cuda is None, f"Runtime CUDA détecté par erreur : {torch.__version__}"
assert not torch.cuda.is_available(), "CUDA ne doit pas être actif dans le paquet CPU"
print(f"Runtime tracking : torch {torch.__version__}, torchvision {torchvision.__version__}, ultralytics {ultralytics.__version__}")
"@
    if ($LASTEXITCODE -ne 0) {
        throw "Le runtime de tracking CPU est incomplet ou incorrect."
    }

    & $pythonPath (Join-Path $repo "scripts\prepare_release_models.py") --edition $Edition
    if ($LASTEXITCODE -ne 0) { throw "Préparation des modèles échouée." }
    & $pythonPath -c @"
import sys
from pathlib import Path
sys.path.insert(0, str(Path('src/interface').resolve()))
import main
main._prepare_qml_module(main._setup_paths())
"@
    if ($LASTEXITCODE -ne 0) { throw "Préparation du module QML échouée." }

    & $pythonPath -m PyInstaller --clean --noconfirm packaging/aquameasure-windows.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller a échoué (code $LASTEXITCODE)."
    }

    $built = Join-Path $repo "dist\AquaMeasure-Windows-portable"
    $releaseRoot = Join-Path $repo "release"
    $release = Join-Path $releaseRoot $ReleaseName
    $releaseRootFull = [IO.Path]::GetFullPath($releaseRoot).TrimEnd('\')
    $releaseFull = [IO.Path]::GetFullPath($release)
    if (-not $releaseFull.StartsWith("$releaseRootFull\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cible de livraison hors du dossier release : $releaseFull"
    }
    $builtFull = (Resolve-Path -LiteralPath $built).Path
    $distRootFull = [IO.Path]::GetFullPath((Join-Path $repo "dist")).TrimEnd('\')
    if (-not $builtFull.StartsWith("$distRootFull\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source de livraison hors du dossier dist : $builtFull"
    }
    New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
    if (Test-Path -LiteralPath $release) {
        Remove-Item -LiteralPath $release -Recurse -Force
    }
    Move-Item -LiteralPath $builtFull -Destination $releaseFull

    foreach ($relative in @(
        "camera_parameters",
        "data\exports",
        "data\media",
        "annotations\data"
    )) {
        New-Item -ItemType Directory -Force -Path (Join-Path $release $relative) | Out-Null
    }

    Copy-Item -LiteralPath (Join-Path $repo "packaging\LISEZ-MOI-WINDOWS.txt") -Destination $release
    Copy-Item -LiteralPath (Join-Path $repo "packaging\NOTES-DE-VERSION.txt") -Destination $release
    $docsDir = Join-Path $release "Documentation"
    $docArgs = @($docsDir)
    if ($DocumentationPdfDir) { $docArgs += @("--pdf-root", $DocumentationPdfDir) }
    & $pythonPath (Join-Path $repo "scripts\copy_documentation.py") @docArgs
    if ($LASTEXITCODE -ne 0) { throw "Copie des manuels échouée." }
    & $pythonPath (Join-Path $repo "scripts\audit_windows_release.py") $release
    if ($LASTEXITCODE -ne 0) { throw "Contrôle du contenu de livraison échoué." }
    $zip = Join-Path $releaseRoot "$ReleaseName.zip"
    if (Test-Path -LiteralPath $zip) {
        Remove-Item -LiteralPath $zip -Force
    }
    if (-not $SkipArchive) {
        & $pythonPath (Join-Path $repo "scripts\package_release.py") $release
        if ($LASTEXITCODE -ne 0) { throw "Archivage echoue" }
    }
    Write-Output "Dossier : $release"
    if (-not $SkipArchive) { Write-Output "Archive : $zip" }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
