@echo off
setlocal EnableDelayedExpansion

for %%i in ("%CD%") do (
	set NAME=Create-MCGui-1.21.1
)

set EXCLUDE=.git .gitignore AutoPacking.bat build .idea achieves shulker

if not exist "build" mkdir "build"
if exist "build\%NAME%.zip" del "build\%NAME%.zip"

set FILES=

:: 文件
for %%f in (*) do (
    set SKIP=
    for %%e in (%EXCLUDE%) do (
        if /I "%%f"=="%%e" set SKIP=1
    )
    if not defined SKIP (
        set FILES=!FILES! "%%f"
    )
)

:: 文件夹
for /D %%d in (*) do (
    set SKIP=
    for %%e in (%EXCLUDE%) do (
        if /I "%%d"=="%%e" set SKIP=1
    )
    if not defined SKIP (
        set FILES=!FILES! "%%d"
    )
)

tar -a -c -f "build\%NAME%.zip" !FILES!

echo.
echo Done: build\%NAME%.zip
pause