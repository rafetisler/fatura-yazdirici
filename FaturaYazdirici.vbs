Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
 CurrentDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = CurrentDir
PythonExe = CurrentDir & "\.venv\Scripts\pythonw.exe"
MainScript = """" & CurrentDir & "\main.py"""
WshShell.Run """" & PythonExe & """ " & MainScript, 1, False

