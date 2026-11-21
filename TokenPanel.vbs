' TokenPanel 静默启动（无控制台窗口）
Option Explicit
Dim sh, fso, base
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
On Error Resume Next
sh.Run "pythonw -m tokenpanel desktop", 0, False
If Err.Number <> 0 Then
  Err.Clear
  sh.Run "python -m tokenpanel desktop", 1, False
End If
