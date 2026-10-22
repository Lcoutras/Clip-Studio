' Starts Clip Studio without a console window. Double-click to open the app.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(dir & "\.venv\Scripts\pythonw.exe") Then
  MsgBox "Run setup.bat first.", vbExclamation, "Clip Studio"
  WScript.Quit 1
End If
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = dir
sh.Run """" & dir & "\.venv\Scripts\pythonw.exe"" -m rlclip studio", 0, False
