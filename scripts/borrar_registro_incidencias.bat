@echo off
setlocal
cd /d "%~dp0.."

if exist ".venv-backend\Scripts\python.exe" (
  ".venv-backend\Scripts\python.exe" "scripts\borrar_registro_incidencias.py"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\borrar_registro_incidencias.py"
) else (
  python "scripts\borrar_registro_incidencias.py"
)

pause
