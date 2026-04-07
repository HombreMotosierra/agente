Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
q = Chr(34)
basePath = FSO.GetParentFolderName(WScript.ScriptFullName)
launcherPath = FSO.BuildPath(basePath, "iniciar_orquestador.bat")
WshShell.Run "cmd /c " & q & launcherPath & q, 0, False
