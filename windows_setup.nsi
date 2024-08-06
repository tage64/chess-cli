; Includes

  !include "MUI2.nsh"
  !include "logiclib.nsh"
; Custom defines
  !define NAME "Chess-CLI"
  !define APPFILE "chess-cli.exe"
  !define VERSION "0.4.0"
  !define SLUG "${NAME} v${VERSION}"
; General
  Name "${NAME}"
  OutFile "${NAME}-setup.exe"
  InstallDir "$PROGRAMFILES\${NAME}"
  InstallDirRegKey HKCU "Software\${NAME}" ""
  RequestExecutionLevel admin
; UI
  !define MUI_WELCOMEPAGE_TITLE "${SLUG} Setup"
; Pages
  ; Installer pages
    !insertmacro MUI_PAGE_WELCOME
    ;!insertmacro MUI_PAGE_LICENSE "license.txt"
    !insertmacro MUI_PAGE_DIRECTORY
    !insertmacro MUI_PAGE_INSTFILES
    !insertmacro MUI_PAGE_FINISH
  ; Uninstaller pages
    !insertmacro MUI_UNPAGE_CONFIRM
    !insertmacro MUI_UNPAGE_INSTFILES
  ; Set UI language
    !insertmacro MUI_LANGUAGE "English"
; Section - Install App
  Section "-hidden app"
    SectionIn RO
    SetOutPath "$INSTDIR"
    File /r "chess-cli.dist\*.*" 
    File "path_ed\PathEd.exe" 
    WriteRegStr HKCU "Software\${NAME}" "" $INSTDIR
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    ;Create shortcuts
    CreateShortCut "$DESKTOP\${NAME}.lnk" "$INSTDIR\${APPFILE}"
    CreateShortcut "$SMPROGRAMS\${NAME}.lnk" "$INSTDIR\${APPFILE}"
    CreateShortcut "$SMPROGRAMS\${NAME} Uninstall.lnk" "$INSTDIR\Uninstall.exe"
    ;Add to PATH
    ExecWait '$INSTDIR\PathEd.exe add "$INSTDIR"'
  SectionEnd
; RMDirUP is used to recursively delete empty parent folders of a given folder.
; This function is used with uninstaller. The command RMDir /r "$INSTDIR" cant remove parent folder.
; Remove empty parent directories
  Function un.RMDirUP
    !define RMDirUP '!insertmacro RMDirUPCall'
    !macro RMDirUPCall _PATH
          push '${_PATH}'
          Call un.RMDirUP
    !macroend
    ; $0 - current folder
    ClearErrors
    Exch $0
    ;DetailPrint "ASDF - $0\.."
    RMDir "$0\.."
    IfErrors Skip
    ${RMDirUP} "$0\.."
    Skip:
    Pop $0
  FunctionEnd
; Section - Uninstaller
Section "Uninstall"
  ;Delete Shortcuts
  Delete "$DESKTOP\${NAME}.lnk"
  Delete "$SMPROGRAMS\${NAME}.lnk"
  Delete "$SMPROGRAMS\${NAME} Uninstall.lnk"
  ;Delete Uninstall
  Delete "$INSTDIR\Uninstall.exe"
  ;Remove from PATH
  ExecWait '$INSTDIR\PathEd.exe remove "$INSTDIR"'
  ;Remove PathEd
  Delete "$INSTDIR\PathEd.exe"
  ;Delete Folder
  RMDir /r "$INSTDIR"
  ${RMDirUP} "$INSTDIR"
  DeleteRegKey /ifempty HKCU "Software\${NAME}"
SectionEnd
