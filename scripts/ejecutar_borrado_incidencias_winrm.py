from __future__ import annotations

import os
import sys

import winrm


HOST = os.getenv("ATC_WINRM_HOST", "10.20.30.8")
USER = os.getenv("ATC_WINRM_USER", "Administrador")
PASSWORD = os.getenv("ATC_WINRM_PASSWORD", "")
PROJECT_DIR = os.getenv(
    "ATC_REMOTE_PROJECT_DIR",
    r"C:\Users\Administrador\Downloads\proyectos\PROYECTO-ATC-SERVIDOR",
)


def main() -> int:
    if not PASSWORD:
        print("Falta ATC_WINRM_PASSWORD.", file=sys.stderr)
        return 2

    session = winrm.Session(
        f"http://{HOST}:5985/wsman",
        auth=(USER, PASSWORD),
        transport="ntlm",
    )
    command = (
        f'cd /d "{PROJECT_DIR}" && '
        r'scripts\borrar_registro_incidencias.bat'
    )
    result = session.run_cmd("cmd.exe", ["/c", command])

    stdout = result.std_out.decode("utf-8", errors="replace").strip()
    stderr = result.std_err.decode("utf-8", errors="replace").strip()
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)
    return int(result.status_code)


if __name__ == "__main__":
    raise SystemExit(main())
