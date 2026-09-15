# register_task.ps1
# A exécuter en tant qu'administrateur, une fois, sur chaque serveur de
# dépôt, après avoir copié le script, le .venv, et le config.yaml du dépôt
# concerné dans C:\ReportUploader.
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File register_task.ps1

$TaskName    = "ReportUploaderAgent"
$PythonExe   = "C:\ReportUploader\.venv\Scripts\python.exe"
$ScriptPath  = "C:\ReportUploader\report_uploader_agent.py"
$ConfigPath  = "C:\ReportUploader\config.yaml"

$Action  = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`" --config `"$ConfigPath`""
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 2) -RepetitionDuration ([TimeSpan]::MaxValue)

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Force

Write-Host "Tâche '$TaskName' enregistrée : exécution toutes les 2 minutes, en permanence."
Write-Host "Vérifier dans le Planificateur de tâches (taskschd.msc) sous 'Bibliothèque du Planificateur de tâches'."
