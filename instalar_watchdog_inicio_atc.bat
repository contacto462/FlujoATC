@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=C:\Users\Administrador\AppData\Local\Programs\Python\Python311\python.exe"
set "TASK_NAME=ATC Server Watchdog"

if not exist "%PYTHON%" (
    set "PYTHON=python"
)

echo Instalando tarea de inicio: %TASK_NAME%
echo Carpeta: %ROOT%

schtasks /Create /TN "%TASK_NAME%" /SC ONSTART /TR "\"%PYTHON%\" \"%ROOT%server_watchdog.py\"" /RL HIGHEST /F

if errorlevel 1 (
    echo.
    echo No se pudo instalar la tarea. Ejecuta este archivo como Administrador.
    pause
    exit /b 1
)

echo.
echo Tarea instalada. El watchdog se iniciara automaticamente al reiniciar Windows.
echo Para iniciarlo ahora, ejecuta reiniciar_servidor_atc.bat.
pause
