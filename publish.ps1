[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [string]$Runtime = "win-x64",
    [switch]$CleanOutput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectPath = Join-Path $rootDir "src/AI-CLI-Watcher.csproj"
$outputDir = Join-Path $rootDir "app"
$assemblyName = [System.IO.Path]::GetFileNameWithoutExtension($projectPath)
$exeFileName = "$assemblyName.exe"
$settingsFileName = "settings.json"
$settingsPath = Join-Path $outputDir $settingsFileName
$settingsBackupPath = $null
$obsoleteRootArtifacts = @(
    "$assemblyName.deps.json"
    "$assemblyName.dll"
    "$assemblyName.pdb"
    "$assemblyName.runtimeconfig.json"
    "D3DCompiler_47_cor3.dll"
    "PenImc_cor3.dll"
    "PresentationNative_cor3.dll"
    "vcruntime140_cor3.dll"
    "wpfgfx_cor3.dll"
)

if (-not (Test-Path $projectPath)) {
    throw "Project file not found: $projectPath"
}

if ($CleanOutput -and (Test-Path $settingsPath)) {
    $settingsBackupPath = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
    Copy-Item -Path $settingsPath -Destination $settingsBackupPath -Force
}

if ($CleanOutput -and (Test-Path $outputDir)) {
    Get-ChildItem -Path $outputDir -Force | Remove-Item -Recurse -Force
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$publishArgs = @(
    "publish"
    $projectPath
    "-c"
    $Configuration
    "-r"
    $Runtime
    "--self-contained"
    "true"
    "-p:PublishSingleFile=true"
    "-p:IncludeNativeLibrariesForSelfExtract=true"
    "-p:DebugType=None"
    "-p:DebugSymbols=false"
    "-o"
    $outputDir
)

Write-Host "Publishing AI-CLI-Watcher..." -ForegroundColor Cyan
Write-Host "  Configuration : $Configuration"
Write-Host "  Runtime       : $Runtime"
Write-Host "  Package mode  : self-contained single-file"
Write-Host "  Output        : $outputDir"

try {
    & dotnet @publishArgs

    if ($LASTEXITCODE -ne 0) {
        throw "dotnet publish failed with exit code $LASTEXITCODE"
    }

    foreach ($artifact in $obsoleteRootArtifacts) {
        $artifactPath = Join-Path $outputDir $artifact
        if (Test-Path $artifactPath) {
            Remove-Item -Path $artifactPath -Force
        }
    }
}
finally {
    if ($settingsBackupPath -and (Test-Path $settingsBackupPath)) {
        Copy-Item -Path $settingsBackupPath -Destination $settingsPath -Force
        Remove-Item -Path $settingsBackupPath -Force
    }
}

Write-Host ""
Write-Host "Publish completed." -ForegroundColor Green
Write-Host "Expected root output: $exeFileName and optional $settingsFileName."
Write-Host "If you are switching from the old layout, run with -CleanOutput once to remove stale files."
