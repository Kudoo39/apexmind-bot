@echo off
REM ============================================================================
REM ApexMind — daily PREPARE + notify (Windows Task Scheduler entry).
REM Thin wrapper: all logic lives in apex_daily.py (auto-resolve -> scan/briefing
REM -> Telegram ping if configured -> stamp data/LAST_PREPARE.txt). Prepare-only;
REM the probability reasoning is done by you in a Claude Code session.
REM ============================================================================
set "APEXDIR=c:\Users\npdkh\Downloads\projects\apexmind-bot"
set "PY=C:\Users\npdkh\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"
cd /d "%APEXDIR%"
echo ==== ApexMind daily run %DATE% %TIME% ==== >> "%APEXDIR%\logs\daily_cron.log"
"%PY%" apex_daily.py >> "%APEXDIR%\logs\daily_cron.log" 2>&1
echo ==== done  %DATE% %TIME% ==== >> "%APEXDIR%\logs\daily_cron.log"
