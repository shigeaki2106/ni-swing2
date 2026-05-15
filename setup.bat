@echo off
echo ================================================
echo   Swing Trade Analyzer - Setup
echo ================================================
echo.
python --version
echo.
echo Installing required libraries...
echo.
python -m pip install yfinance pandas numpy matplotlib --upgrade
echo.
echo ================================================
echo   Done! Now double-click run_analysis.bat
echo ================================================
echo.
pause
