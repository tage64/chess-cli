; Includes

  !include "MUI2.nsh"
  !include nsDialogs.nsh
  !include "logiclib.nsh"
  !include "FileAssociation.nsh"
; Custom defines
  !define NAME "Chess-CLI"
  !define APPFILE "chess-cli.exe"
  !define VERSION "0.5.0"
  !define SLUG "${NAME} v${VERSION}"
; General
  Name "${NAME}"
  OutFile "${NAME}-setup.exe"
  InstallDir "$PROGRAMFILES\${NAME}"
  InstallDirRegKey HKCU "Software\${NAME}" ""
  RequestExecutionLevel admin
; Variables
  ; nsDialogs HWND variables
    var Dialog
    var Checkbox
  var SetFileAssociationsCheckbox
; UI
  !define MUI_WELCOMEPAGE_TITLE "${SLUG} Setup"
; Pages
  ; Installer pages
    !insertmacro MUI_PAGE_WELCOME
    ;!insertmacro MUI_PAGE_LICENSE "license.txt"
    Page custom FileAssociations FileAssociationsLeave
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
    ;Set file associations
    ${If} $SetFileAssociationsCheckbox == ${BST_CHECKED}
      ${registerExtension} "$INSTDIR\${APPFILE}" ".pgn" "PGN_File"
      ${registerExtension} "$INSTDIR\${APPFILE}" ".fen" "FEN_File"
    ${EndIf}
    ;Add to PATH
    ExecWait '$INSTDIR\PathEd.exe add "$INSTDIR"'
  SectionEnd
  Function FileAssociations
    !insertmacro MUI_HEADER_TEXT "File Associations" "Set file associations for common chess formats"
    nsDialogs::Create 1018
    Pop $Dialog
    ${If} $Dialog == error
      Abort
    ${EndIf}
    ${NSD_CreateCheckbox} 0 30u 100% 10u "Register PGN and FEN files to be opened by Chess-CLI."
    Pop $Checkbox
    ${NSD_Check} $Checkbox
    nsDialogs::Show
  FunctionEnd
  Function FileAssociationsLeave
    ${NSD_GetState} $Checkbox $SetFileAssociationsCheckbox
  FunctionEnd
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
  ;Remove file associations
  ${unregisterExtension} ".pgn" "PGN_File"
  ${unregisterExtension} ".fen" "FEN_File"
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
