@echo off
chcp 65001 >nul
setlocal

set "PY=.venv\Scripts\python.exe"
set "DIST=build\onefile"

if not exist "%DIST%" mkdir "%DIST%"

"%PY%" -m nuitka ^
  --mode=onefile ^
  --msvc=latest ^
  --enable-plugin=pyside6 ^
  --include-qt-plugins=platforms,imageformats,styles,iconengines ^
  --windows-console-mode=disable ^
  --output-dir="%DIST%" ^
  --output-filename=a1packer.exe ^
  --remove-output ^
  run_gui.py
if errorlevel 1 exit /b %ERRORLEVEL%

echo.
echo GUI build done.
echo Next step is building CLI.

"%PY%" -m nuitka ^
  --mode=onefile ^
  --msvc=latest ^
  --output-dir="%DIST%" ^
  --output-filename=a1packer-cli.exe ^
  --remove-output ^
  run_cli.py
if errorlevel 1 exit /b %ERRORLEVEL%

robocopy "swap_gcode" "%DIST%\swap_gcode" /E
if %ERRORLEVEL% GEQ 8 exit /b %ERRORLEVEL%

copy /Y "gcode_patches.ini" "%DIST%\gcode_patches.ini" >nul
if errorlevel 1 exit /b %ERRORLEVEL%

echo.
echo Build done: %DIST%
exit /b 0
