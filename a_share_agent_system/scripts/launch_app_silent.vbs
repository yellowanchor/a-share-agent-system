Option Explicit
' Silent launcher for the A-share dashboard.
' Double-click the desktop shortcut -> no console window, browser opens by itself.

Dim fso, sh, here, ps1
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1  = here & "\launch_app.ps1"

If Not fso.FileExists(ps1) Then
    MsgBox "Missing launcher: " & ps1, 16, "A-share dashboard"
    WScript.Quit 1
End If

sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """", 0, False
