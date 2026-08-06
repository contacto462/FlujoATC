"""
Crea (o resetea la contraseña de) el login del Portal Cliente para una empresa.

Uso:
    python -m ATC.scripts.crear_usuario_cliente --rut 76123456-7 --username acme --password "Clave123!"
    python -m ATC.scripts.crear_usuario_cliente --rut 76123456-7 --username acme --password "NuevaClave!" --reset

El usuario creado inicia sesión en /portal-cliente con `username`/`password` y
ve las sucursales cuyo `bbdd_sucursales.rut` coincide con `--rut`.
"""
from __future__ import annotations

import argparse
import re

from ATC.app.core.db import SessionLocal
from ATC.app.core.security import hash_password
from ATC.app.models.incidencias import ClienteBBDD, SucursalBBDD
from ATC.app.models.user import User


def _normalizar_rut(rut: str) -> str:
    return re.sub(r"[^0-9K]", "", str(rut or "").upper())


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea o resetea un login de Portal Cliente.")
    parser.add_argument("--rut", required=True, help="RUT de la empresa (bbdd_clientes.rut).")
    parser.add_argument("--username", required=True, help="Usuario de acceso al portal.")
    parser.add_argument("--password", required=True, help="Contraseña de acceso al portal.")
    parser.add_argument("--name", default=None, help="Nombre a mostrar (por defecto, el nombre del cliente).")
    parser.add_argument("--reset", action="store_true", help="Si el usuario ya existe, actualiza su contraseña/rut en vez de fallar.")
    args = parser.parse_args()

    rut_norm = _normalizar_rut(args.rut)
    if not rut_norm:
        raise SystemExit("RUT inválido.")

    db = SessionLocal()
    try:
        clientes = db.query(ClienteBBDD).filter(ClienteBBDD.rut.isnot(None)).all()
        cliente = next((c for c in clientes if _normalizar_rut(c.rut) == rut_norm), None)
        if not cliente:
            raise SystemExit(f"No se encontró ningún cliente (bbdd_clientes) con RUT {args.rut}.")

        n_sucursales = (
            db.query(SucursalBBDD).filter(SucursalBBDD.rut == cliente.rut).count()
        )
        if n_sucursales == 0:
            print(f"Aviso: {cliente.cliente} (RUT {cliente.rut}) no tiene sucursales cargadas todavía.")

        existente = db.query(User).filter(User.username == args.username.strip()).first()
        if existente and not args.reset:
            raise SystemExit(
                f"Ya existe un usuario '{args.username}' (id={existente.id}). "
                "Usa --reset si quieres actualizar su contraseña/RUT."
            )

        if existente:
            existente.hashed_password = hash_password(args.password)
            existente.cliente_rut = cliente.rut
            existente.is_active = True
            if args.name:
                existente.name = args.name
            db.commit()
            print(f"Actualizado: usuario '{existente.username}' -> cliente '{cliente.cliente}' (RUT {cliente.rut}), {n_sucursales} sucursal(es).")
            return

        nuevo = User(
            name=args.name or f"Portal {cliente.cliente}",
            username=args.username.strip(),
            hashed_password=hash_password(args.password),
            role="agent",
            is_active=True,
            department=None,
            cliente_rut=cliente.rut,
        )
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)
        print(f"Creado: usuario '{nuevo.username}' (id={nuevo.id}) -> cliente '{cliente.cliente}' (RUT {cliente.rut}), {n_sucursales} sucursal(es).")
        print("El cliente ya puede entrar en /portal-cliente con ese usuario y contraseña.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
