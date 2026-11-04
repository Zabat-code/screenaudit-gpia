@echo off
REM Launcher unico (nombre distinto a iniciar/INICIAR para evitar colision case-insensitive en Windows)
cd /d "%~dp0"
if not exist "hf_cache" mkdir hf_cache
if not exist "logs" mkdir logs
python app.py --panel
pause
