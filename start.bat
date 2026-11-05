@echo off
setlocal
where py >nul 2>nul
if errorlevel 1 goto python
py -3 "%~dp0tools\start_regular.py" %*
set "regular_exit=%errorlevel%"
goto finish
:python
where python >nul 2>nul
if errorlevel 1 goto missing
python "%~dp0tools\start_regular.py" %*
set "regular_exit=%errorlevel%"
goto finish
:missing
echo Python 3.10+ is required. Install Python and enable its launcher or PATH entry.
set "regular_exit=1"
:finish
if not "%regular_exit%"=="0" pause
exit /b %regular_exit%
