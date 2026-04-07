Set WshShell = CreateObject("WScript.Shell")
q = Chr(34)
WshShell.Run "cmd /c " & q & "C:\IA local\Codigo\iniciar_orquestador.bat" & q, 0, False
