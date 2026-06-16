Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
appPath = fso.BuildPath(baseDir, "app.py")
logPath = fso.BuildPath(baseDir, "xpano_gui_error.log")

pythonw = "D:\FastPrograms\Miniconda\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If

If fso.FileExists(logPath) Then
    fso.DeleteFile logPath, True
End If

shell.CurrentDirectory = baseDir
shell.Run """" & pythonw & """ """ & appPath & """", 0, False
