' TokenPanel silent launcher.
' Messages are kept in English on purpose: .vbs files are read as ANSI by WSH,
' so non-ASCII text would garble on some systems.
Option Explicit

Dim sh, fso, base, hasPython, rc

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

hasPython = (sh.Run("cmd /c where pythonw >nul 2>&1", 0, True) = 0)
If Not hasPython Then
  hasPython = (sh.Run("cmd /c where python >nul 2>&1", 0, True) = 0)
End If

If Not hasPython Then
  MsgBox "Python was not found." & vbCrLf & vbCrLf & _
         "TokenPanel needs Python 3.10 or newer." & vbCrLf & _
         "The setup wizard will open now. Install Python, then run this again.", _
         vbExclamation, "TokenPanel"
  sh.Run """" & base & "\setup.bat""", 1, False
  WScript.Quit
End If

rc = sh.Run("cmd /c pythonw -m tokenpanel doctor --quiet", 0, True)
If rc <> 0 Then
  If MsgBox("Environment check failed (usually the zstandard package is missing)." & vbCrLf & vbCrLf & _
            "Run setup.bat to fix it now?", _
            vbQuestion + vbYesNo, "TokenPanel") = vbYes Then
    sh.Run """" & base & "\setup.bat""", 1, False
    WScript.Quit
  End If
End If

sh.Run "pythonw -m tokenpanel desktop", 0, False
