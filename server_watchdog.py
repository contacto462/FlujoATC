from __future__ import annotations

import datetime as _dt
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOGDIR = ROOT / "logs"
LOCK_FILE = LOGDIR / "server_watchdog.lock"
HEALTH_URL = "http://127.0.0.1:8000/health"
CHECK_SECONDS = 5
HEALTH_SECONDS = 10
MAX_HEALTH_FAILS = 2
HEALTH_TIMEOUT = 5.0
STARTUP_GRACE_SECONDS = 45.0


def _log(message: str) -> None:
    LOGDIR.mkdir(exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with (LOGDIR / "watchdog.log").open("a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] {message}\n")


def _acquire_single_instance_lock():
    LOGDIR.mkdir(exist_ok=True)
    lock_fh = LOCK_FILE.open("a+b")
    try:
        if sys.platform.startswith("win"):
            import msvcrt

            lock_fh.seek(0)
            if not lock_fh.read(1):
                lock_fh.write(b"0")
                lock_fh.flush()
            lock_fh.seek(0)
            msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fh.close()
        return None
    return lock_fh


def _kill_port_8000_listeners() -> None:
    """Evita doble bind: en Windows SO_REUSEADDR permite que dos procesos
    escuchen el mismo puerto, y un uvicorn zombi deja el server errático."""
    if not sys.platform.startswith("win"):
        return
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue"
                " | Select-Object -ExpandProperty OwningProcess -Unique"
                " | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }",
            ],
            timeout=30,
            capture_output=True,
        )
    except Exception as exc:
        _log(f"Port 8000 cleanup failed: {exc}")


def _start_server() -> subprocess.Popen:
    LOGDIR.mkdir(exist_ok=True)
    _kill_port_8000_listeners()
    out = (LOGDIR / "uvicorn.out.log").open("ab", buffering=0)
    err = (LOGDIR / "uvicorn.err.log").open("ab", buffering=0)
    # run_server.py fija WindowsSelectorEventLoopPolicy antes de crear el loop
    # (el Proactor cierra el listener ante errores de accept — WinError 64).
    cmd = [
        sys.executable,
        str(ROOT / "run_server.py"),
    ]
    _log("Starting server: " + " ".join(cmd))
    return subprocess.Popen(cmd, cwd=str(ROOT), stdout=out, stderr=err)


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=HEALTH_TIMEOUT) as response:
            return 200 <= int(response.status) < 300
    except Exception as exc:
        _log(f"Health check failed: {exc}")
        return False


def _stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    _log(f"Stopping unresponsive server pid={proc.pid}")
    if sys.platform.startswith("win"):
        # taskkill /T mata el árbol completo (python -> uvicorn/hijos).
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        _log(f"Killing unresponsive server pid={proc.pid}")
        proc.kill()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _log(f"Could not confirm exit of pid={proc.pid}")


def main() -> None:
    lock_fh = _acquire_single_instance_lock()
    if lock_fh is None:
        _log("Another watchdog instance is already running; exiting")
        return

    _log("Watchdog started")
    proc = _start_server()
    health_fails = 0
    last_health = 0.0
    started_at = time.monotonic()

    while True:
        exit_code = proc.poll()
        if exit_code is not None:
            _log(f"Server exited with code {exit_code}; restarting in 5s")
            time.sleep(5)
            proc = _start_server()
            health_fails = 0
            last_health = 0.0
            started_at = time.monotonic()
            continue

        now = time.monotonic()
        if now - started_at < STARTUP_GRACE_SECONDS:
            time.sleep(CHECK_SECONDS)
            continue

        if now - last_health >= HEALTH_SECONDS:
            last_health = now
            if _health_ok():
                health_fails = 0
            else:
                health_fails += 1
                if health_fails >= MAX_HEALTH_FAILS:
                    _log("Server failed health checks; restarting")
                    _stop_server(proc)
                    time.sleep(5)
                    proc = _start_server()
                    health_fails = 0
                    last_health = 0.0
                    started_at = time.monotonic()

        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _log("Watchdog stopped by KeyboardInterrupt")
