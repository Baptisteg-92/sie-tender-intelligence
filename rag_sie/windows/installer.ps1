# Installation du moteur RAG sur le serveur SIE (Windows Server 2025, Lenovo ST250 V2, sans carte graphique).
# A lancer dans PowerShell en administrateur, depuis le dossier qui contient rag_sie\ :
#     powershell -ExecutionPolicy Bypass -File rag_sie\windows\installer.ps1 -Racine D:\SIE\rag -CompteService "SIE\svc-rag"
#
# Ce que fait le script (rien n'est supprimé, il peut être relancé) :
#   1. crée D:\SIE\rag\{code, donnees, modeles} et un environnement Python isolé (.venv) ;
#   2. installe les dépendances, PyTorch pour processeur et sentence-transformers ;
#   3. copie configuration.exemple en configuration.env si celui-ci n'existe pas encore ;
#   4. restreint configuration.env (clé d'API) aux administrateurs et au compte de service ;
#   5. crée deux tâches planifiées : la synchronisation de nuit et le service HTTP au démarrage.
#
# Prérequis installés à la main : Python 3.12 (python.org, « pour tous les utilisateurs »),
# Tesseract avec la langue fra (PDF scannés), PostgreSQL 16 + pgvector pour l'ensemble des archives.
# Script non encore exécuté sur le serveur SIE : à dérouler la première fois avec le prestataire informatique.

param(
    [string]$Racine = "D:\SIE\rag",
    [Parameter(Mandatory = $true)][string]$CompteService,
    [string]$HeureSynchro = "02:00",
    [int]$Port = 8600,
    [string]$Python = "py -3.12"
)

$ErrorActionPreference = "Stop"
$source = Split-Path -Parent (Split-Path -Parent $PSCommandPath)   # ...\rag_sie
$code = Join-Path $Racine "code"
$venv = Join-Path $Racine ".venv"
$config = Join-Path $Racine "configuration.env"

Write-Host "1/5 Dossiers dans $Racine"
foreach ($d in @($Racine, $code, (Join-Path $Racine "donnees"), (Join-Path $Racine "modeles"), (Join-Path $Racine "journaux"))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Copy-Item -Recurse -Force $source (Join-Path $code "rag_sie")

Write-Host "2/5 Environnement Python et dépendances (plusieurs minutes)"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Invoke-Expression "$Python -m venv `"$venv`""
}
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r (Join-Path $code "rag_sie\requirements.txt")
& $py -m pip install --index-url https://download.pytorch.org/whl/cpu torch
& $py -m pip install "sentence-transformers>=3.0" pywin32 docling

Write-Host "3/5 Configuration"
if (-not (Test-Path $config)) {
    Copy-Item (Join-Path $code "rag_sie\configuration.exemple") $config
    Write-Host "   -> compléter $config (chemin des archives, base, jeton, clé OpenAI) avant le premier lancement"
}

Write-Host "4/5 Droits sur configuration.env (administrateurs et $CompteService seulement)"
icacls $config /inheritance:r /grant:r "*S-1-5-32-544:(F)" "${CompteService}:(R)" | Out-Null

Write-Host "5/5 Tâches planifiées (le mot de passe du compte de service est demandé)"
$cred = Get-Credential -UserName $CompteService -Message "Mot de passe du compte de service"
$env = "set SIE_RAG_CONFIG=$config&& cd /d `"$code`"&&"
$synchro = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $env `"$py`" -m rag_sie synchroniser --duree-max 300 >> `"$Racine\journaux\synchro.log`" 2>&1"
$service = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $env `"$py`" -m rag_sie servir --hote 0.0.0.0 --port $Port >> `"$Racine\journaux\service.log`" 2>&1"
$reglages = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 6) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -Force -TaskName "SIE RAG synchronisation" -Action $synchro -Settings $reglages `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $HeureSynchro) -User $CompteService -Password $cred.GetNetworkCredential().Password | Out-Null
$reglagesService = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -Force -TaskName "SIE RAG service" -Action $service -Settings $reglagesService `
    -Trigger (New-ScheduledTaskTrigger -AtStartup) -User $CompteService -Password $cred.GetNetworkCredential().Password | Out-Null

Write-Host ""
Write-Host "Terminé. Étapes suivantes :"
Write-Host "  - compléter $config ;"
Write-Host "  - ouvrir le port $Port dans le pare-feu pour le réseau interne seulement :"
Write-Host "      New-NetFirewallRule -DisplayName 'SIE RAG' -Direction Inbound -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Action Allow"
Write-Host "  - premier essai : & `"$py`" -m rag_sie --config `"$config`" synchroniser --duree-max 30"
Write-Host "  - puis démarrer la tâche « SIE RAG service » et ouvrir http://localhost:$Port/health"
