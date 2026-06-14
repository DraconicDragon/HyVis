@echo off
setlocal enabledelayedexpansion

echo Starting HyVis update...

:: 1. Pull latest changes
if exist .git (
    echo Pulling latest code from Git...
    git pull
    if !errorlevel! neq 0 (
        echo ERROR: Git pull failed.
        goto :error
    )
) else (
    echo Warning: No .git directory found. Skipping git pull.
)

:: 2. Check and activate virtual environment
if exist .venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call .venv\Scripts\activate.bat
) else (
    echo ERROR: Virtual environment (.venv) not found. Please create it first.
    goto :error
)

:: 3. Reinstall package
echo Installing package updates...
pip install .
if !errorlevel! neq 0 (
    echo ERROR: Installation failed.
    goto :error
)

echo ----------------------------------------
echo HyVis updated successfully.
echo ----------------------------------------
pause
exit /b 0

:error
echo ----------------------------------------
echo Update failed. Please check the errors above.
echo ----------------------------------------
pause
exit /b 1