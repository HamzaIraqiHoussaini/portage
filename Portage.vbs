' Windows double-click launcher with no console window.
' Always runs a quick setup check (stamp / import) before starting.
Option Explicit
Dim sh, fso, root, venvPyw, venvPy, setup
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root

' Hidden setup pass (creates venv / installs if needed)
setup = "cmd /c """ & root & "\Portage.bat"" --setup-only"
sh.Run setup, 0, True

venvPyw = root & "\.venv\Scripts\pythonw.exe"
venvPy = root & "\.venv\Scripts\python.exe"
If fso.FileExists(venvPyw) Then
  sh.Run """" & venvPyw & """ -m app.desktop", 0, False
ElseIf fso.FileExists(venvPy) Then
  sh.Run """" & venvPy & """ -m app.desktop", 0, False
Else
  MsgBox "Portage could not find Python in .venv. Run Portage.bat once.", 16, "Portage"
End If
