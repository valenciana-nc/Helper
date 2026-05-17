$WshShell = New-Object -ComObject WScript.Shell
$ShortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Helper.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$env:USERPROFILE\Helper\.venv\Scripts\pythonw.exe"
$Shortcut.Arguments = "$env:USERPROFILE\Helper\main.py"
$Shortcut.WorkingDirectory = "$env:USERPROFILE\Helper"
$Shortcut.Description = "Launch Helper AI Assistant"
$Shortcut.Save()
Write-Host "Shortcut created at $ShortcutPath"
