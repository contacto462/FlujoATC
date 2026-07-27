@echo off
setlocal

set "ROOT=%~dp0"
set "LOGDIR=%ROOT%logs"
set "PYTHON=C:\Users\Administrador\AppData\Local\Programs\Python\Python311\python.exe"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

if not exist "%PYTHON%" (
    set "PYTHON=python"
)

echo Iniciando servidor ATC con watchdog...
echo Carpeta: %ROOT%

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server_watchdog.py*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Cerrando proceso en puerto 8000: %%p
    taskkill /PID %%p /F >nul 2>&1
)

timeout /t 2 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYTHON%' -ArgumentList 'server_watchdog.py' -WorkingDirectory '%ROOT%' -RedirectStandardOutput '%LOGDIR%\watchdog.out.log' -RedirectStandardError '%LOGDIR%\watchdog.err.log' -WindowStyle Hidden"

timeout /t 3 /nobreak >nul

netstat -ano | findstr ":8000"

echo.
echo Listo. Si ves LISTENING en 0.0.0.0:8000, el servidor quedo arriba.
echo Logs:
echo %LOGDIR%\watchdog.log
echo %LOGDIR%\watchdog.out.log
echo %LOGDIR%\watchdog.err.log
echo %LOGDIR%\uvicorn.out.log
echo %LOGDIR%\uvicorn.err.log
pause
