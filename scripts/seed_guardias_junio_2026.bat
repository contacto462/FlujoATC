@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=C:\Users\Administrador\AppData\Local\Programs\Python\Python311\python.exe"

if not exist "%PYTHON%" (
    set "PYTHON=python"
)

cd /d "%ROOT%"

echo Cargando junio 2026 con registros coincidentes de guardias y supervisores...
"%PYTHON%" scripts\seed_guardias_junio_2026.py --year 2026 --month 6 --replace

echo.
pause
