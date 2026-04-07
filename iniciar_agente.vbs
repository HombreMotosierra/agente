Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
pythonwPath = "C:\Users\OFF\AppData\Local\Programs\Python\Python311\pythonw.exe"
scriptPath = "C:\IA local\Codigo\texto.py"
q = Chr(34)
If FSO.FileExists(pythonwPath) Then
    WshShell.Run q & pythonwPath & q & " " & q & scriptPath & q, 0, False
Else
    WshShell.Run "pyw " & q & scriptPath & q, 0, False
End If
