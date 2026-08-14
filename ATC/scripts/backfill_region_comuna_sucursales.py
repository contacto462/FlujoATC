"""
Completa region/comuna en bbdd_sucursales usando la API de Geocoding de
Google Maps (GOOGLE_MAPS_API_KEY en .env) — no se infiere de texto libre.

Prioridad por sucursal: si tiene latitud/longitud, reverse geocoding por
coordenadas (mas preciso); si no, forward geocoding por direccion_sucursal.
Solo completa los campos que esten vacios — no pisa region/comuna ya
cargados a mano. Seguro de re-ejecutar.

Ejecutar en el Windows Server (unico lugar con un Python 3.10+ utilizable
para este repo, ver CLAUDE.md):

    python ATC/scripts/backfill_region_comuna_sucursales.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.parse
import urllib.request
import json as _json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ATC.app.core.config import settings
from ATC.app.core.db import SessionLocal
from ATC.app.models.incidencias import SucursalBBDD

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def _limpiar_region(nombre: str) -> str:
    n = nombre.strip()
    for prefijo in ("Región del ", "Región de ", "Región "):
        if n.startswith(prefijo):
            return n[len(prefijo):].strip()
    return n


def _parsear_componentes(address_components: list[dict]) -> tuple[str, str]:
    region = ""
    comuna = ""
    locality = ""
    for comp in address_components:
        types = comp.get("types", [])
        if "administrative_area_level_1" in types:
            region = _limpiar_region(comp.get("long_name", ""))
        elif "administrative_area_level_3" in types:
            comuna = comp.get("long_name", "")
        elif "locality" in types and not locality:
            locality = comp.get("long_name", "")
    if not comuna:
        comuna = locality
    return region, comuna


def _llamar_geocoding(params: dict) -> dict:
    params = {**params, "key": settings.google_maps_api_key, "language": "es", "region": "cl"}
    url = f"{GEOCODE_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def _geocodificar(sucursal: SucursalBBDD) -> tuple[str, str, str]:
    """Devuelve (region, comuna, metodo) o ("", "", motivo_error)."""
    lat = (sucursal.latitud or "").strip()
    lng = (sucursal.longitud or "").strip()
    if lat and lng:
        data = _llamar_geocoding({"latlng": f"{lat},{lng}"})
        metodo = "coordenadas"
    else:
        direccion = (sucursal.direccion_sucursal or "").strip()
        if not direccion:
            return "", "", "sin_lat_lng_ni_direccion"
        data = _llamar_geocoding({"address": direccion, "components": "country:CL"})
        metodo = "direccion"

    status = data.get("status")
    if status != "OK":
        return "", "", f"geocode_status={status}"

    results = data.get("results") or []
    if not results:
        return "", "", "sin_resultados"

    region, comuna = _parsear_componentes(results[0].get("address_components", []))
    if not region and not comuna:
        return "", "", "sin_componentes_admin"
    return region, comuna, metodo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="No escribe en la BD, solo muestra lo que haria")
    parser.add_argument("--limit", type=int, default=0, help="Procesar como maximo N sucursales (0 = todas)")
    parser.add_argument("--sleep", type=float, default=0.05, help="Pausa entre llamadas a la API (segundos)")
    args = parser.parse_args()

    if not settings.google_maps_api_key:
        print("ERROR: GOOGLE_MAPS_API_KEY no esta configurada en .env")
        sys.exit(1)

    db = SessionLocal()
    try:
        query = db.query(SucursalBBDD).filter(
            (SucursalBBDD.region.is_(None)) | (SucursalBBDD.region == "")
            | (SucursalBBDD.comuna.is_(None)) | (SucursalBBDD.comuna == "")
        ).order_by(SucursalBBDD.id)
        sucursales = query.all()
        if args.limit:
            sucursales = sucursales[: args.limit]

        total = len(sucursales)
        print(f"Sucursales a procesar: {total}")

        ok = 0
        fallidas: list[tuple[int, str, str]] = []

        for i, suc in enumerate(sucursales, start=1):
            region, comuna, info = _geocodificar(suc)
            if not region and not comuna:
                fallidas.append((suc.id, suc.nombre_sucursal, info))
                print(f"[{i}/{total}] id={suc.id} '{suc.nombre_sucursal}' -> FALLO ({info})")
            else:
                cambios = []
                if not (suc.region or "").strip() and region:
                    cambios.append(f"region='{region}'")
                    if not args.dry_run:
                        suc.region = region
                if not (suc.comuna or "").strip() and comuna:
                    cambios.append(f"comuna='{comuna}'")
                    if not args.dry_run:
                        suc.comuna = comuna
                ok += 1
                print(f"[{i}/{total}] id={suc.id} '{suc.nombre_sucursal}' -> {', '.join(cambios) or '(sin cambios)'} [{info}]")

            if not args.dry_run and i % 25 == 0:
                db.commit()
            time.sleep(args.sleep)

        if not args.dry_run:
            db.commit()

        print()
        print(f"Listo. OK={ok} FALLIDAS={len(fallidas)} (de {total})")
        if fallidas:
            print("Fallidas:")
            for sid, nombre, motivo in fallidas:
                print(f"  id={sid} '{nombre}': {motivo}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
