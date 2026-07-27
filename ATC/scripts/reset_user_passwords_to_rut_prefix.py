from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

from sqlalchemy import text

from ATC.app.core.db import SessionLocal
from ATC.app.core.security import hash_password, verify_password


@dataclass
class PasswordResetRow:
    user_id: int
    name: str
    rut: str
    password_plain: str


def _password_from_rut(rut: str) -> str:
    digits = re.sub(r"\D+", "", str(rut or ""))
    return digits[:5]


def collect_rows() -> tuple[list[PasswordResetRow], list[tuple[int, str, str]]]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, name, [user] AS rut
                FROM dbo.users
                ORDER BY id
                """
            )
        ).mappings().all()
    finally:
        db.close()

    ready: list[PasswordResetRow] = []
    skipped: list[tuple[int, str, str]] = []
    for row in rows:
        user_id = int(row["id"])
        name = str(row.get("name") or "").strip()
        rut = str(row.get("rut") or "").strip()
        password_plain = _password_from_rut(rut)
        if len(password_plain) < 5:
            skipped.append((user_id, name, rut))
            continue
        ready.append(
            PasswordResetRow(
                user_id=user_id,
                name=name,
                rut=rut,
                password_plain=password_plain,
            )
        )
    return ready, skipped


def apply_reset(rows: list[PasswordResetRow]) -> None:
    db = SessionLocal()
    try:
        for row in rows:
            hashed = hash_password(row.password_plain)
            if not verify_password(row.password_plain, hashed):
                raise RuntimeError(f"No se pudo verificar hash para user_id={row.user_id}")
            db.execute(
                text(
                    """
                    UPDATE dbo.users
                    SET [password] = :password
                    WHERE id = :id
                    """
                ),
                {"password": hashed, "id": row.user_id},
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resetea passwords de dbo.users al bcrypt de los primeros 5 digitos del RUT."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica los cambios. Sin este flag solo muestra conteos.",
    )
    args = parser.parse_args()

    rows, skipped = collect_rows()
    print(f"Usuarios listos para actualizar: {len(rows)}")
    print(f"Usuarios omitidos por RUT invalido/corto: {len(skipped)}")
    if skipped:
        print("Omitidos:")
        for user_id, name, rut in skipped:
            print(f"  id={user_id} name={name!r} rut={rut!r}")

    if not args.apply:
        print("Dry-run: no se hicieron cambios. Ejecuta con --apply para actualizar.")
        return

    apply_reset(rows)
    print("OK: passwords actualizadas con bcrypt usando los primeros 5 digitos del RUT.")


if __name__ == "__main__":
    main()
