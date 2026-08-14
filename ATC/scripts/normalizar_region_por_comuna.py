"""
Corrige region en bbdd_sucursales derivandola de la comuna (dato ya
confiable) en vez de las coordenadas — varias sucursales tienen
latitud/longitud mal cargadas (apuntan a otro lugar de Chile), lo que
hizo que backfill_region_comuna_sucursales.py les asignara una region
incorrecta via reverse geocoding. En Chile cada comuna pertenece a UNA
sola region de forma fija, asi que la region correcta se puede derivar
de la comuna sin ambiguedad.

Geocodifica cada comuna DISTINTA una sola vez ("{comuna}, Chile") para
armar un mapa comuna -> region, y lo aplica a todas las sucursales con
esa comuna (sobrescribe region si no coincide).

Ejecutar en el Windows Server:

    python ATC/scripts/normalizar_region_por_comuna.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ATC.app.core.db import SessionLocal
from ATC.app.models.incidencias import SucursalBBDD
from ATC.scripts.backfill_region_comuna_sucursales import (
    _llamar_geocoding,
    _parsear_componentes,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        comunas = [
            c for (c,) in db.query(SucursalBBDD.comuna).filter(
                SucursalBBDD.comuna.is_not(None), SucursalBBDD.comuna != ""
            ).distinct().all()
        ]
        print(f"Comunas distintas a resolver: {len(comunas)}")

        mapa_region: dict[str, str] = {}
        fallidas: list[str] = []
        for i, comuna in enumerate(comunas, start=1):
            data = _llamar_geocoding({"address": f"{comuna}, Chile", "components": "country:CL"})
            if data.get("status") != "OK" or not data.get("results"):
                fallidas.append(comuna)
                print(f"[{i}/{len(comunas)}] '{comuna}' -> FALLO ({data.get('status')})")
                time.sleep(args.sleep)
                continue
            region, comuna_google = _parsear_componentes(data["results"][0]["address_components"])
            if not region:
                fallidas.append(comuna)
                print(f"[{i}/{len(comunas)}] '{comuna}' -> sin region en respuesta")
                time.sleep(args.sleep)
                continue
            mapa_region[comuna] = region
            print(f"[{i}/{len(comunas)}] '{comuna}' -> region='{region}' (comuna google: '{comuna_google}')")
            time.sleep(args.sleep)

        print()
        print(f"Mapa comuna->region construido: {len(mapa_region)} OK, {len(fallidas)} fallidas")
        if fallidas:
            print("Comunas sin resolver (no se tocan):", fallidas)

        cambios = 0
        sucursales = db.query(SucursalBBDD).filter(
            SucursalBBDD.comuna.is_not(None), SucursalBBDD.comuna != ""
        ).all()
        for suc in sucursales:
            region_correcta = mapa_region.get(suc.comuna)
            if region_correcta and (suc.region or "").strip() != region_correcta:
                print(f"id={suc.id} '{suc.nombre_sucursal}': region '{suc.region}' -> '{region_correcta}' (comuna={suc.comuna})")
                cambios += 1
                if not args.dry_run:
                    suc.region = region_correcta

        if not args.dry_run:
            db.commit()

        print()
        print(f"Listo. {cambios} sucursales corregidas (de {len(sucursales)} con comuna).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
