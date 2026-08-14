!macro XPANO_REMOVE_DIR DIR
  IfFileExists "${DIR}\*.*" 0 +2
    RMDir /r "${DIR}"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  SetRegView 64
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  SetRegView 32
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  IfFileExists "$INSTDIR\tools\webview2\MicrosoftEdgeWebView2RuntimeInstallerX64.exe" 0 webview_missing
  ExecWait '"$INSTDIR\tools\webview2\MicrosoftEdgeWebView2RuntimeInstallerX64.exe" /silent /install' $1
  IntCmp $1 0 webview_done webview_recheck webview_recheck
webview_recheck:
  SetRegView 64
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  SetRegView 32
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  MessageBox MB_ICONSTOP|MB_OK "Microsoft Edge WebView2 Runtime installation failed (exit code $1)."
  Abort
webview_missing:
  MessageBox MB_ICONSTOP|MB_OK "Microsoft Edge WebView2 Runtime is missing and the offline installer was not packaged."
  Abort
webview_done:
  SetRegView 64
  Delete "$INSTDIR\tools\webview2\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  SetShellVarContext current

  ; Projects live next to source media and are never touched here.
  !insertmacro XPANO_REMOVE_DIR "$APPDATA\com.xpano.app"
  IfSilent preserve_densify_runtime
  MessageBox MB_YESNO|MB_ICONQUESTION "是否同时删除已下载的致密化运行时与缓存？选择“否”可在重新安装后复用。" IDNO preserve_densify_runtime
  !insertmacro XPANO_REMOVE_DIR "$LOCALAPPDATA\com.xpano.app"
  Goto app_data_done
preserve_densify_runtime:
  !insertmacro XPANO_REMOVE_DIR "$LOCALAPPDATA\com.xpano.app\EBWebView"
  !insertmacro XPANO_REMOVE_DIR "$LOCALAPPDATA\com.xpano.app\logs"
app_data_done:
  !insertmacro XPANO_REMOVE_DIR "$APPDATA\xPano"
  !insertmacro XPANO_REMOVE_DIR "$LOCALAPPDATA\xPano"
  !insertmacro XPANO_REMOVE_DIR "$APPDATA\xpano"
  !insertmacro XPANO_REMOVE_DIR "$LOCALAPPDATA\xpano"
  !insertmacro XPANO_REMOVE_DIR "$APPDATA\xpano-ui"
  !insertmacro XPANO_REMOVE_DIR "$LOCALAPPDATA\xpano-ui"
  !insertmacro XPANO_REMOVE_DIR "$INSTDIR"
!macroend
