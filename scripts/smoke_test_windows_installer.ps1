param(
    [string]$SetupPath = "",
    [string]$ReportPath = "",
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($SetupPath)) {
    $SetupPath = Join-Path $projectRoot "release\AquaMeasure-Setup-20260908.exe"
}
$SetupPath = (Resolve-Path -LiteralPath $SetupPath).Path

$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A1FB5837-A710-4DA4-99B8-073B2B11B05F}_is1"
if (Test-Path -LiteralPath $uninstallKey) {
    throw "Une installation AquaMeasure existe déjà dans ce profil ; test interrompu pour ne pas la modifier."
}

$smokeRoot = Join-Path $env:TEMP ("AMInstallerSmoke-" + [guid]::NewGuid().ToString("N"))
$installDir = Join-Path $smokeRoot "App"
$storageConfig = Join-Path $smokeRoot "storage.json"
$database = Join-Path $smokeRoot "aquameasure.db"
$trackingReport = Join-Path $smokeRoot "tracking.json"
$cleanReport = Join-Path $smokeRoot "clean-install.json"
$setupLog = Join-Path $smokeRoot "setup.log"
New-Item -ItemType Directory -Path $smokeRoot | Out-Null

$realConfig = Join-Path $env:APPDATA "AquaMeasure\storage.json"
$realConfigExisted = Test-Path -LiteralPath $realConfig
$realConfigHash = if ($realConfigExisted) {
    (Get-FileHash -LiteralPath $realConfig -Algorithm SHA256).Hash
} else {
    $null
}
$documentsData = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "AquaMeasure - Donnees"
$documentsDataExisted = Test-Path -LiteralPath $documentsData

$appProcess = $null
$failure = $null
$evidence = $null
$previousTestEnv = @{}
foreach ($envName in @("AQUAMEASURE_STORAGE_CONFIG", "AQUAMEASURE_MODELS_DIR", "FISH_VISION_DB", "FISH_VISION_SETTINGS", "HF_HOME", "HF_HUB_OFFLINE")) {
    $previousTestEnv[$envName] = [Environment]::GetEnvironmentVariable($envName, "Process")
}

try {
    $setupArgs = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOICONS",
        "/DIR=$installDir",
        "/LOG=$setupLog",
        "/SkipStorageSetup=1"
    )
    $setupProcess = Start-Process -FilePath $SetupPath -ArgumentList $setupArgs -WindowStyle Hidden -Wait -PassThru
    if ($setupProcess.ExitCode -ne 0) {
        throw "Installateur : code $($setupProcess.ExitCode)."
    }

    $installedExe = Join-Path $installDir "AquaMeasure.exe"
    if (-not (Test-Path -LiteralPath $installedExe)) {
        throw "Exécutable installé introuvable."
    }
    $uninstaller = Get-ChildItem -LiteralPath $installDir -Filter "unins*.exe" -File | Select-Object -First 1
    if ($null -eq $uninstaller) {
        throw "Désinstallateur introuvable."
    }

    [IO.File]::WriteAllText($storageConfig, (@{data_root = $smokeRoot} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    $env:AQUAMEASURE_STORAGE_CONFIG = $storageConfig
    $env:AQUAMEASURE_MODELS_DIR = Join-Path $installDir "fish-vision\models"
    $env:FISH_VISION_DB = $database
    $env:FISH_VISION_SETTINGS = Join-Path $smokeRoot "settings.json"
    $env:HF_HOME = Join-Path $smokeRoot "hf"
    $env:HF_HUB_OFFLINE = "1"

    $cleanProcess = Start-Process -FilePath $installedExe `
        -ArgumentList @("--self-test-clean-install", $cleanReport) `
        -WorkingDirectory $installDir -WindowStyle Hidden -Wait -PassThru
    if ($cleanProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $cleanReport)) {
        throw "Auto-test premier démarrage échoué (code $($cleanProcess.ExitCode))."
    }
    $clean = Get-Content -LiteralPath $cleanReport -Raw | ConvertFrom-Json
    if (-not $clean.ok) { throw "Le catalogue ou la base vierge n'est pas conforme." }

    $trackProcess = Start-Process `
        -FilePath $installedExe `
        -ArgumentList @("--self-test-cpu-tracking", $trackingReport) `
        -WorkingDirectory $installDir `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($trackProcess.ExitCode -ne 0) {
        throw "Auto-test tracking : code $($trackProcess.ExitCode)."
    }
    if (-not (Test-Path -LiteralPath $trackingReport)) {
        throw "Rapport de tracking absent."
    }
    $tracking = Get-Content -LiteralPath $trackingReport -Raw | ConvertFrom-Json
    if (
        -not $tracking.ok -or
        $tracking.device -ne "cpu" -or
        $tracking.torch_cuda_available -ne $false -or
        $null -ne $tracking.torch_cuda_build
    ) {
        throw "Le rapport de tracking ne confirme pas un moteur CPU pur."
    }

    $appProcess = Start-Process `
        -FilePath $installedExe `
        -WorkingDirectory $installDir `
        -WindowStyle Hidden `
        -PassThru
    $inputIdle = $appProcess.WaitForInputIdle(30000)
    Start-Sleep -Seconds 5
    $appProcess.Refresh()
    if ($appProcess.HasExited -or -not $appProcess.Responding -or -not $inputIdle) {
        throw "L'interface installée ne répond pas."
    }
    if ($appProcess.MainWindowTitle -ne "AquaMeasure") {
        throw "Titre de fenêtre inattendu : $($appProcess.MainWindowTitle)"
    }
    if (-not (Test-Path -LiteralPath $database)) {
        throw "La base locale de test n'a pas été créée."
    }

    $installedFiles = Get-ChildItem -LiteralPath $installDir -Recurse -File
    $cudaDllCount = @(
        $installedFiles | Where-Object {
            $_.Extension -ieq ".dll" -and
            $_.Name -match "(?i)(cuda|cudnn|cublas|cusparse|nvrtc|nvjit|nvtx|cupti)"
        }
    ).Count
    if ($cudaDllCount -ne 0) {
        throw "$cudaDllCount DLL CUDA détectée(s) dans l'installation."
    }

    $evidence = [pscustomobject]@{
        SetupExitCode = $setupProcess.ExitCode
        InstalledFileCount = $installedFiles.Count
        TrackingOk = $tracking.ok
        Tracker = $tracking.tracker
        Torch = $tracking.torch_version
        CudaBuild = $tracking.torch_cuda_build
        CudaAvailable = $tracking.torch_cuda_available
        Device = $tracking.device
        TrackingElapsedSeconds = $tracking.elapsed_seconds
        InterfaceResponding = $appProcess.Responding
        InterfaceTitle = $appProcess.MainWindowTitle
        DatabaseCreated = Test-Path -LiteralPath $database
        CudaDllCount = $cudaDllCount
        CleanInstall = $clean
    }
} catch {
    $failure = $_.Exception.Message
} finally {
    foreach ($envName in $previousTestEnv.Keys) {
        [Environment]::SetEnvironmentVariable($envName, $previousTestEnv[$envName], "Process")
    }
    if ($null -ne $appProcess) {
        try {
            $appProcess.Refresh()
            if (-not $appProcess.HasExited) {
                Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
                $appProcess.WaitForExit(10000)
            }
        } catch {
        }
    }

    $uninstallerForCleanup = if (Test-Path -LiteralPath $installDir) {
        Get-ChildItem -LiteralPath $installDir -Filter "unins*.exe" -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
    } else {
        $null
    }
    if ($null -ne $uninstallerForCleanup) {
        try {
            Start-Process `
                -FilePath $uninstallerForCleanup.FullName `
                -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") `
                -WindowStyle Hidden `
                -Wait | Out-Null
        } catch {
            if ($null -eq $failure) {
                $failure = "Échec de la désinstallation de recette : $($_.Exception.Message)"
            }
        }
    }
}

$exeStillPresent = Test-Path -LiteralPath (Join-Path $installDir "AquaMeasure.exe")
$uninstallKeyStillPresent = Test-Path -LiteralPath $uninstallKey
$realConfigUnchanged = if ($realConfigExisted) {
    (Test-Path -LiteralPath $realConfig) -and
    ((Get-FileHash -LiteralPath $realConfig -Algorithm SHA256).Hash -eq $realConfigHash)
} else {
    -not (Test-Path -LiteralPath $realConfig)
}
$documentsStateUnchanged = (Test-Path -LiteralPath $documentsData) -eq $documentsDataExisted

if ($exeStillPresent -or $uninstallKeyStillPresent -or -not $realConfigUnchanged -or -not $documentsStateUnchanged) {
    if ($null -eq $failure) {
        $failure = "Échec des contrôles après désinstallation ou altération de données existantes."
    }
}

if ($null -ne $evidence) {
    $evidence | Format-List
}
$cleanupEvidence = [pscustomobject]@{
    UninstallRemovedExe = -not $exeStillPresent
    UninstallKeyRemoved = -not $uninstallKeyStillPresent
    ExistingConfigUnchanged = $realConfigUnchanged
    DocumentsDataStateUnchanged = $documentsStateUnchanged
    SmokeRoot = $smokeRoot
}
$cleanupEvidence | Format-List

if ($ReportPath) {
    [pscustomobject]@{ok = ($null -eq $failure); installation = $evidence; cleanup = $cleanupEvidence; error = $failure} |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding utf8
}

if (-not $KeepArtifacts) {
    $resolvedSmoke = [IO.Path]::GetFullPath($smokeRoot)
    $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd([IO.Path]::DirectorySeparatorChar) +
        [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedSmoke.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Nettoyage refusé hors du dossier temporaire : $resolvedSmoke"
    }
    if (Test-Path -LiteralPath $resolvedSmoke) {
        Remove-Item -LiteralPath $resolvedSmoke -Recurse -Force
    }
}

if ($null -ne $failure) {
    throw $failure
}

Write-Host "Recette de l'installateur réussie."
