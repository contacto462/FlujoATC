"""Lanzador del servidor ATC.

IMPORTANTE (Windows): uvicorn 0.49 hardcodea ProactorEventLoop en Windows
(`uvicorn/loops/asyncio.py`), y con el Proactor un error transitorio en
accept() —p.ej. un cliente que corta el TCP a mitad del handshake— hace que
asyncio CIERRE el socket de escucha: el servidor queda vivo pero sordo
("Accept failed on a socket" / WinError 64 en los logs) y el watchdog entra
en loop de reinicios. El SelectorEventLoop no tiene ese comportamiento.

Por eso este script NO usa uvicorn.run(): crea un SelectorEventLoop propio y
corre server.serve() encima. Cambiar la policy (como hacía unified_main.py)
no basta — uvicorn no la consulta.
"""
from __future__ import annotations

import asyncio
import sys

import uvicorn


def main() -> None:
    config = uvicorn.Config(
        "unified_main:app",
        host="0.0.0.0",
        port=8000,
        http="h11",
        timeout_keep_alive=5,
    )
    server = uvicorn.Server(config)

    if sys.platform.startswith("win"):
        loop = asyncio.SelectorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
