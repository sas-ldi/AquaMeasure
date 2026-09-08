param(
    [string]$PortableDir = "",
    [string]$OutputName = "AquaMeasure-Setup-20260908"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$issPath = Join-Path $repoRoot "packaging\AquaMeasure-installer.iss"
$releaseDir = Join-Path $repoRoot "release"

if ([string]::IsNullOrWhiteSpace($PortableDir)) {
    $PortableDir = Join-Path $releaseDir "AquaMeasure-Windows-x64-20260908"
}
$PortableDir = (Resolve-Path -LiteralPath $PortableDir).Path

$exePath = Join-Path $PortableDir "AquaMeasure.exe"
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "AquaMeasure.exe est introuvable dans le paquet portable : $PortableDir"
}

$compilerCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$compiler = $compilerCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -First 1

if (-not $compiler) {
    throw "Inno Setup 6 est requis. Installez-le puis relancez ce script."
}

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null

$compilePortableDir = $PortableDir
$compileReleaseDir = $releaseDir
$compileIssPath = $issPath
$temporaryDrive = $null

try {
    # Inno Setup 6.2 utilise encore certaines API limitées à MAX_PATH. Le
    # chemin de ce dépôt est long ; une lettre SUBST temporaire évite les
    # échecs sur les sous-dossiers Qt sans copier les quelque 700 Mio du paquet.
    if ($PortableDir.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        $driveLetter = @("Z", "Y", "X", "W", "V", "U", "T") |
            Where-Object { -not (Test-Path -LiteralPath ($_ + ":\")) } |
            Select-Object -First 1
        if (-not $driveLetter) {
            throw "Aucune lettre de lecteur temporaire n'est disponible pour la compilation."
        }

        $temporaryDrive = $driveLetter + ":"
        & subst.exe $temporaryDrive $repoRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Impossible de créer le lecteur temporaire $temporaryDrive."
        }

        $driveRoot = $temporaryDrive + "\"
        $portableRelative = $PortableDir.Substring($repoRoot.Length).TrimStart("\")
        $compilePortableDir = Join-Path $driveRoot $portableRelative
        $compileReleaseDir = Join-Path $driveRoot "release"
        $compileIssPath = Join-Path $driveRoot "packaging\AquaMeasure-installer.iss"
    }

    & $compiler /Qp "/DMyAppSource=$compilePortableDir" "/O$compileReleaseDir" "/F$OutputName" $compileIssPath
    if ($LASTEXITCODE -ne 0) {
        throw "La compilation de l'installateur a échoué (code $LASTEXITCODE)."
    }
}
finally {
    if ($temporaryDrive) {
        & subst.exe $temporaryDrive /D | Out-Null
    }
}

$setupPath = Join-Path $releaseDir ($OutputName + ".exe")
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    throw "Le compilateur n'a pas produit le fichier attendu : $setupPath"
}

$setup = Get-Item -LiteralPath $setupPath
$hash = Get-FileHash -LiteralPath $setupPath -Algorithm SHA256

Write-Host ""
Write-Host "Installateur prêt : $($setup.FullName)"
Write-Host ("Taille : {0:N2} Mio" -f ($setup.Length / 1MB))
Write-Host "SHA256 : $($hash.Hash)"
