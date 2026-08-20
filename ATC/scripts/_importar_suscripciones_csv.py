"""Importa el CSV de Registro de Suscripciones a la tabla `suscripcion`
(modelo Suscripcion) — reemplaza el modo anterior de leer el CSV en vivo en
cada carga de la página. Re-ejecutable: hace upsert por "codigo" (no
duplica si se corre de nuevo tras actualizar el CSV).

Resuelve sucursal_id con el mismo cruce conservador RUT + dirección exacta
que ya se usaba en vivo (ver claves_direccion en suscripciones_service.py)
— si no hay coincidencia exacta, sucursal_id queda NULL (se puede asignar
a mano después desde la página).

Uso: python -m ATC.scripts._importar_suscripciones_csv
"""
from __future__ import annotations

import sys

sys.path = [p for p in sys.path if "PROYECTO-ATC-SERVIDOR" not in p]

from ATC.app.core.db import SessionLocal  # noqa: E402
from ATC.app.models.incidencias import SucursalBBDD  # noqa: E402
from ATC.app.models.suscripciones import Suscripcion  # noqa: E402
from ATC.app.services.suscripciones_service import (  # noqa: E402
    cargar_filas_csv,
    claves_direccion,
    normalizar_rut,
    parse_numero,
    resolver_sucursal_id,
)


def _num(valor: str) -> float | None:
    return parse_numero(valor)


def _txt(valor: str) -> str | None:
    txt = str(valor or "").strip()
    return txt or None


def main() -> None:
    filas_csv = cargar_filas_csv()
    if not filas_csv:
        print("No se encontró el CSV o está vacío — nada que importar.")
        return

    db = SessionLocal()
    try:
        sucursales = db.query(SucursalBBDD.id, SucursalBBDD.rut, SucursalBBDD.direccion_sucursal, SucursalBBDD.comuna).all()
        sucursales_por_rut: dict[str, list[dict]] = {}
        for sucursal_id, rut, direccion, comuna in sucursales:
            rut_norm = normalizar_rut(rut)
            if not rut_norm:
                continue
            sucursales_por_rut.setdefault(rut_norm, []).append(
                {"sucursal_id": sucursal_id, "claves": claves_direccion(direccion, comuna)}
            )

        existentes = {s.codigo: s for s in db.query(Suscripcion).all()}

        creadas = 0
        actualizadas = 0
        con_sucursal = 0
        for fila in filas_csv:
            codigo = _txt(fila.get("Codigo"))
            if not codigo:
                continue

            rut = fila.get("RUT")
            direccion = fila.get("Dirección")
            sucursal_id = resolver_sucursal_id(sucursales_por_rut, rut, direccion)
            if sucursal_id:
                con_sucursal += 1

            valores = dict(
                rut=_txt(rut),
                nombre_cliente=_txt(fila.get("Nombre Cliente")),
                link_piriod=_txt(fila.get("Link Piriod")),
                servicio=_txt(fila.get("Servicio")),
                inicio_servicio=_txt(fila.get("Inicio Servicio")),
                cantidad_camaras=_num(fila.get("Cantidad Camaras")),
                moneda=_txt(fila.get("Moneda")),
                valor_neto_mensual=_num(fila.get("Valor Neto Mensual")),
                descuento=_num(fila.get("Descuento")),
                internet=_num(fila.get("Internet")),
                valor_neto_televigilancia=_num(fila.get("Valor Neto Televigilancia")),
                valor_neto_total=_num(fila.get("Valor Neto Total")),
                valor_por_camara=_num(fila.get("Valor por cámara")),
                direccion=_txt(direccion),
                comuna=_txt(fila.get("Comuna")),
                region=_txt(fila.get("Región")),
                direccion_completa=_txt(fila.get("Dirección completa")),
                nombre_sucursal=_txt(fila.get("Nombre Sucursal")),
                estado=_txt(fila.get("Estado")),
                fecha_termino=_txt(fila.get("Fecha Termino Suscripción")),
            )

            existente = existentes.get(codigo)
            if existente:
                # No se pisa una asignacion manual ya hecha a mano.
                if not existente.sucursal_asignada_manual:
                    existente.sucursal_id = sucursal_id
                for campo, valor in valores.items():
                    setattr(existente, campo, valor)
                actualizadas += 1
            else:
                db.add(Suscripcion(codigo=codigo, sucursal_id=sucursal_id, **valores))
                creadas += 1

        db.commit()
        print(f"Filas CSV procesadas: {len(filas_csv)}")
        print(f"Creadas: {creadas} | Actualizadas: {actualizadas}")
        print(f"Con sucursal_id resuelto automaticamente: {con_sucursal}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
