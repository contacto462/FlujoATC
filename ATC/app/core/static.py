from __future__ import annotations

from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send


class MultiDirectoryStaticFiles:
    """Serve static files from the first directory that contains the requested path."""

    def __init__(self, directories: list[Path], *, html: bool = False) -> None:
        self._directories = [directory.resolve() for directory in directories if directory.exists()]
        if not self._directories:
            raise RuntimeError("At least one static directory must exist.")
        self._apps = [StaticFiles(directory=str(directory), html=html) for directory in self._directories]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path") or "")
        root_path = str(scope.get("root_path") or "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :]
        path = path.lstrip("/")
        for directory, app in zip(self._directories, self._apps):
            if self._contains_file(directory, path):
                await app(scope, receive, send)
                return
        await self._apps[-1](scope, receive, send)

    @staticmethod
    def _contains_file(directory: Path, request_path: str) -> bool:
        try:
            candidate = (directory / request_path).resolve()
            return candidate.is_file() and candidate.is_relative_to(directory)
        except Exception:
            return False
