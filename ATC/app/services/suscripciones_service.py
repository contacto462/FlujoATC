"""Datos para la hoja "Registro de Suscripción" de Finanzas.

La fuente de datos es la tabla `suscripcion` (modelo `Suscripcion`,
importada una vez desde el CSV histórico — ver
`ATC/scripts/_importar_suscripciones_csv.py`). Ya no se lee el CSV en vivo
en cada carga de la página.

Lo único que se sigue cruzando en tiempo real contra bbdd_sucursales es
"Cantidad Cámaras", y solo cuando Servicio == "Televigilancia" (ver
obtener_camaras_monitoreadas_por_sucursal) — el resto de los datos ya
vienen resueltos en la tabla desde la importación.
"""
from __future__ import annotations

import csv
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ATC.app.integrations.piriod_client import listar_todas_suscripciones
from ATC.app.models.incidencias import SucursalBBDD, SucursalCamaraMonitoreo
from ATC.app.models.suscripciones import Suscripcion

# ATC/app/services/suscripciones_service.py -> ATC/app/services -> ATC/app -> ATC -> raiz del repo
CSV_PATH = Path(__file__).resolve().parents[3] / "Registro de Suscripciones ATC - Registro de Suscripciones.csv"

_MARCADORES_MOJIBAKE = ("Ã", "Â", "â", "ð", "�")


def reparar_texto_mojibake(valor: Any) -> str:
    """Copia standalone de IncidenciasService._reparar_texto_mojibake (sin
    depender de self/DB) — prueba re-decodificar como latin-1/cp1252 y se
    queda con la variante que tiene menos caracteres de mojibake."""
    txt = str(valor or "").strip()
    if not txt:
        return ""

    def _score(s: str) -> tuple[int, int]:
        raros = sum(s.count(ch) for ch in _MARCADORES_MOJIBAKE)
        return (raros, len(s))

    actual = txt
    mejor = txt
    visto: set[str] = {txt}
    for _ in range(4):
        candidatos = [actual]
        try:
            candidatos.append(actual.encode("latin-1").decode("utf-8"))
        except Exception:
            pass
        try:
            candidatos.append(actual.encode("cp1252").decode("utf-8"))
        except Exception:
            pass
        candidatos = [c.strip() for c in candidatos if str(c or "").strip()]
        if not candidatos:
            break
        mejor_paso = min(candidatos, key=_score)
        if _score(mejor_paso) < _score(mejor):
            mejor = mejor_paso
        if mejor_paso in visto or mejor_paso == actual:
            break
        visto.add(mejor_paso)
        actual = mejor_paso
    return mejor.strip()


def normalizar_rut(valor: Any) -> str:
    return re.sub(r"[^0-9K]", "", str(valor or "").upper())


_ABREVIATURAS_DIRECCION = {
    "avda": "av", "avenida": "av",
    "pje": "pasaje", "psje": "pasaje",
    "nro": "n", "numero": "n", "num": "n",
    "km": "kilometro",
}


def normalizar_direccion(valor: Any) -> str:
    """Repara mojibake, saca tildes/mayusculas, colapsa espacios y quita
    puntuacion/simbolos y abreviaturas comunes, para que 'Av. Providencia
    N°123' y 'Avenida Providencia 123' terminen siendo la misma clave."""
    txt = reparar_texto_mojibake(valor).lower()
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    txt = re.sub(r"[^a-z0-9\s]", " ", txt)
    palabras = [_ABREVIATURAS_DIRECCION.get(p, p) for p in txt.split()]
    return " ".join(p for p in palabras if p)


def parse_numero(valor: Any) -> float | None:
    """'20,00' / '4,5' / '225.000' (coma decimal, punto de miles, como
    exporta la planilla) -> float. None si no es numerico o esta vacio."""
    txt = str(valor or "").strip()
    if not txt:
        return None
    txt = txt.replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


def cargar_filas_csv() -> list[dict[str, Any]]:
    """Solo lo usa el script de importación (_importar_suscripciones_csv.py)
    — la página en vivo ya no lee el CSV, lee la tabla `suscripcion`."""
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def claves_direccion(direccion: Any, comuna: Any) -> set[str]:
    """La direccion de bbdd_sucursales muchas veces trae la comuna pegada al
    final (ej. '...Fundo Quepo, Talca') mientras que la columna Direccion
    del CSV nunca la incluye (esa comuna vive en su propia columna aparte).
    Devuelve el normalizado completo, y ademas una variante "sin el ultimo
    tramo" para poder matchear ese caso — probar una direccion mas corta
    que sigue siendo la misma direccion no es adivinar, solo reconocer que
    el origen del dato a veces agrega la comuna y a veces no:

    1) Si termina en ", {comuna}" (columna comuna de la sucursal) — la mas
       directa cuando ese dato esta bien cargado.
    2) Si el ULTIMO tramo separado por coma es corto (<=4 palabras) y no
       tiene numeros, tambien se prueba sin ese tramo. Se exige "sin
       numeros" a proposito: eso es lo que distingue un nombre de comuna
       ("Concón", "Ñuñoa") de un dato real de la direccion como "Local 2"
       o "Piso 3" que NO hay que descartar. Se usa esta variante porque en
       la practica la columna comuna de bbdd_sucursales viene mal cargada
       para varias filas (ej. dice "Santiago" o "Talcahuano" cuando la
       direccion en realidad termina en "Maipú" o "Talca") — confiar solo
       en la opcion 1 se quedaba corta."""
    base = normalizar_direccion(direccion)
    claves = {base} if base else set()

    comuna_norm = normalizar_direccion(comuna)
    if base and comuna_norm and base.endswith(f" {comuna_norm}"):
        sin_comuna = base[: -(len(comuna_norm) + 1)].strip()
        if sin_comuna:
            claves.add(sin_comuna)

    direccion_txt = str(direccion or "").strip()
    if "," in direccion_txt:
        antes, _, ultimo_tramo = direccion_txt.rpartition(",")
        ultimo_tramo_norm = normalizar_direccion(ultimo_tramo)
        if (
            ultimo_tramo_norm
            and not any(ch.isdigit() for ch in ultimo_tramo_norm)
            and len(ultimo_tramo_norm.split()) <= 4
        ):
            sin_ultimo_tramo = normalizar_direccion(antes)
            if sin_ultimo_tramo:
                claves.add(sin_ultimo_tramo)

    return claves


def resolver_sucursal_id(sucursales_por_rut: dict[str, list[dict[str, Any]]], rut: Any, direccion: Any) -> int | None:
    """sucursales_por_rut: rut_normalizado -> [{"sucursal_id", "claves"}].
    Usado solo por el script de importación para resolver, una vez, la
    sucursal de cada fila del CSV antes de guardarla."""
    rut_norm = normalizar_rut(rut)
    direccion_norm = normalizar_direccion(direccion)
    for candidata in sucursales_por_rut.get(rut_norm, []):
        if direccion_norm and direccion_norm in candidata["claves"]:
            return candidata["sucursal_id"]
    return None


def _numero_o_vacio(valor: float | None) -> Any:
    if valor is None:
        return ""
    return int(valor) if valor == int(valor) else valor


def obtener_camaras_monitoreadas_por_sucursal(db: Session) -> dict[int, int]:
    """sucursal_id -> cantidad de cámaras CON pantalla asignada en un puesto
    de monitoreo — misma fuente/criterio que Bitácora > Información Puestos
    (_informacion_puestos_data en bitacora.py: central IS NOT NULL +
    nombre_camara_monitoreo no vacío). Solo incluye sucursales con al menos
    una fila con central asignado: si una sucursal no tiene ninguna fila
    ahí, no aparece en el dict — no hay dato, no es un 0 confiable."""
    filas = (
        db.query(
            SucursalCamaraMonitoreo.sucursal_id,
            SucursalCamaraMonitoreo.nombre_camara_monitoreo,
        )
        .filter(
            SucursalCamaraMonitoreo.central.isnot(None),
            SucursalCamaraMonitoreo.sucursal_id.isnot(None),
        )
        .all()
    )
    conteo: dict[int, int] = {}
    for sucursal_id, cam_mon in filas:
        conteo.setdefault(sucursal_id, 0)
        if cam_mon and str(cam_mon).strip():
            conteo[sucursal_id] += 1
    return conteo


_PIRIOD_CACHE_LOCK = threading.Lock()
_PIRIOD_CACHE: dict[str, Any] = {"por_codigo": {}, "actualizado_en": None}


def _mapear_estado_piriod(status: Any) -> str:
    status_norm = str(status or "").strip().lower()
    if status_norm in ("cancelled", "finalized"):
        return "Cancelada"
    if status_norm == "paused":
        return "Pausada"
    return "Vigente"


def _fecha_termino_piriod(sub: dict[str, Any]) -> str:
    """cancelled (fecha real de cancelacion) tiene prioridad sobre end_date
    (fecha calculada por ciclos de facturacion, puede ser una proyeccion a
    futuro que todavia no paso)."""
    fecha = sub.get("cancelled") or sub.get("end_date")
    if not fecha:
        return ""
    fecha_str = str(fecha)[:10]
    try:
        anio, mes, dia = fecha_str.split("-")
        return f"{dia}/{mes}/{anio}"
    except ValueError:
        return ""


def _valor_y_moneda_piriod(sub: dict[str, Any]) -> tuple[float | None, str | None]:
    """Suma el monto de todas las lineas de la suscripcion que comparten la
    moneda de la primera linea (no tiene sentido sumar UF con CLP)."""
    lineas = sub.get("lines") or []
    if not lineas:
        return None, None
    primera_moneda = ((lineas[0].get("plan") or {}).get("currency") or {}).get("id")
    total = 0.0
    encontro_alguna = False
    for linea in lineas:
        plan = linea.get("plan") or {}
        moneda_linea = (plan.get("currency") or {}).get("id")
        if moneda_linea != primera_moneda:
            continue
        monto = plan.get("amount")
        if monto is None:
            continue
        cantidad = linea.get("quantity") or 1
        total += float(monto) * float(cantidad)
        encontro_alguna = True
    if not encontro_alguna:
        return None, None
    # round() evita arrastrar imprecision de punto flotante de la suma
    # (ej. 62.699999999999996 en vez de 62.7) hasta la respuesta del API.
    return round(total, 3), primera_moneda


def refrescar_cache_piriod() -> None:
    """Trae todas las suscripciones de Piriod (paginando) y actualiza el
    cache en memoria, indexado por id — que coincide exactamente con
    Suscripcion.codigo. Si la llamada falla, se propaga la excepcion y el
    cache anterior queda intacto (una caida de Piriod no debe borrar los
    ultimos datos buenos que se mostraban)."""
    suscripciones = listar_todas_suscripciones()
    por_codigo = {sub["id"]: sub for sub in suscripciones if sub.get("id")}
    with _PIRIOD_CACHE_LOCK:
        _PIRIOD_CACHE["por_codigo"] = por_codigo
        _PIRIOD_CACHE["actualizado_en"] = datetime.now(timezone.utc)


def obtener_cache_piriod() -> dict[str, Any]:
    with _PIRIOD_CACHE_LOCK:
        return {
            "por_codigo": _PIRIOD_CACHE["por_codigo"],
            "actualizado_en": _PIRIOD_CACHE["actualizado_en"],
        }


def _lineas_piriod_texto(sub: dict[str, Any]) -> str:
    partes = []
    for linea in sub.get("lines") or []:
        plan = linea.get("plan") or {}
        moneda = (plan.get("currency") or {}).get("id") or ""
        nombre = plan.get("name") or ""
        monto = plan.get("amount")
        cantidad = linea.get("quantity") or 1
        partes.append(f"{nombre} ({monto} {moneda} x{cantidad})")
    return " | ".join(partes)


def suscripciones_piriod_crudo(db: Session) -> list[dict[str, Any]]:
    """Todas las suscripciones que hay en Piriod (no solo las que están en
    nuestra tabla local `suscripcion`), con sus datos tal cual los entrega
    la API — sin curar, sin cruzar contra bbdd_sucursales ni nada. "En
    Registro Local" es el único dato que no viene de Piriod: compara el id
    contra los códigos que sí tenemos importados, para ver de un vistazo
    qué suscripciones de Piriod nunca se importaron."""
    codigos_locales = {c for (c,) in db.query(Suscripcion.codigo).all()}
    piriod_por_codigo = obtener_cache_piriod()["por_codigo"]

    resultado: list[dict[str, Any]] = []
    for sub in piriod_por_codigo.values():
        customer = sub.get("customer") or {}
        estado_raw = sub.get("status") or ""
        resultado.append(
            {
                "id": sub.get("id") or "",
                "estado": estado_raw,
                "cliente": customer.get("name") or "",
                "rut": customer.get("tax_id") or "",
                "email_cliente": customer.get("email") or "",
                "direccion_cliente": customer.get("address") or "",
                "comuna_cliente": ((customer.get("state") or {}).get("name")) or "",
                "nota": sub.get("note") or "",
                "fecha_inicio": sub.get("date_start") or "",
                "proximo_cobro": sub.get("next_billing") or "",
                "cobro_anterior": sub.get("previous_billing") or "",
                "fecha_fin": sub.get("end_date") or "",
                "cancelado_en": sub.get("cancelled") or "",
                "motivo_cancelacion": sub.get("cancel_reason") or "",
                "pausado_en": sub.get("paused") or "",
                "motivo_pausa": sub.get("paused_reason") or "",
                "ciclos_facturacion": sub.get("billing_cycles"),
                "modo_prueba": bool(sub.get("test_mode")),
                "esquema_cobro": sub.get("collection_scheme") or "",
                "lineas": _lineas_piriod_texto(sub),
                "creado": sub.get("created") or "",
                "actualizado": sub.get("updated") or "",
                "en_registro_local": sub.get("id") in codigos_locales,
            }
        )
    resultado.sort(key=lambda r: (r["cliente"], r["id"]))
    return resultado


def obtener_suscripciones(
    db: Session, camaras_por_sucursal: dict[int, int] | None = None
) -> list[dict[str, Any]]:
    """Todas las filas de la tabla `suscripcion`. "Nombre Sucursal" se
    resuelve en vivo desde bbdd_sucursales cuando hay sucursal_id (así
    refleja el nombre actual aunque cambie después de la importación), con
    fallback al valor guardado en la fila si por algo la sucursal no
    existe más. "Cantidad Cámaras" se reemplaza por el dato de
    bbdd_sucursales SOLO cuando Servicio == "Televigilancia" y hay
    sucursal_id con datos en camaras_por_sucursal — si no, se deja el
    valor original importado del CSV y se marca "camaras_sin_bbdd": true.

    Estado, Fecha Término, Valor Neto Mensual, Moneda y Link Piriod se
    reemplazan por el dato en vivo del cache de Piriod (ver
    refrescar_cache_piriod) cuando el código de la fila (que es el id real
    de la suscripción en Piriod) aparece ahí — el resto de columnas
    "curadas" (Servicio, Cámaras, Descuento, Internet, Comuna, Región, etc.)
    no tienen equivalente limpio en la API de Piriod y se dejan como están
    en la tabla importada del CSV (pedido explícito, ago 2026)."""
    camaras_por_sucursal = camaras_por_sucursal or {}
    piriod_por_codigo = obtener_cache_piriod()["por_codigo"]
    filas = (
        db.query(Suscripcion, SucursalBBDD.nombre_sucursal)
        .outerjoin(SucursalBBDD, Suscripcion.sucursal_id == SucursalBBDD.id)
        .order_by(Suscripcion.nombre_cliente, Suscripcion.id)
        .all()
    )

    resultado: list[dict[str, Any]] = []
    for sus, nombre_sucursal_actual in filas:
        nombre_sucursal = nombre_sucursal_actual or sus.nombre_sucursal or ""

        sub_piriod = piriod_por_codigo.get(sus.codigo)
        if sub_piriod:
            estado = _mapear_estado_piriod(sub_piriod.get("status"))
            fecha_termino = _fecha_termino_piriod(sub_piriod)
            valor_neto_mensual_piriod, moneda_piriod = _valor_y_moneda_piriod(sub_piriod)
            valor_neto_mensual = (
                valor_neto_mensual_piriod
                if valor_neto_mensual_piriod is not None
                else _numero_o_vacio(sus.valor_neto_mensual)
            )
            moneda = moneda_piriod or (sus.moneda or "")
            link_piriod = f"https://app.piriod.com/subscriptions/{sus.codigo}"
        else:
            estado = sus.estado or ""
            fecha_termino = sus.fecha_termino or ""
            valor_neto_mensual = _numero_o_vacio(sus.valor_neto_mensual)
            moneda = sus.moneda or ""
            link_piriod = sus.link_piriod or ""

        cancelada = str(estado).strip().casefold() == "cancelada"

        camaras_sin_bbdd = False
        if sus.servicio == "Televigilancia":
            if sus.sucursal_id is not None and sus.sucursal_id in camaras_por_sucursal:
                cantidad_camaras = camaras_por_sucursal[sus.sucursal_id]
            else:
                cantidad_camaras = _numero_o_vacio(sus.cantidad_camaras)
                # Una suscripcion Cancelada no necesita cruce de camaras
                # confirmado — no tiene sentido marcarla amarilla pidiendo
                # revision de algo que ya no esta vigente (pedido
                # explicito, ago 2026).
                camaras_sin_bbdd = not cancelada
        else:
            cantidad_camaras = _numero_o_vacio(sus.cantidad_camaras)

        resultado.append(
            {
                "codigo": sus.codigo,
                "rut": sus.rut or "",
                "nombre_cliente": sus.nombre_cliente or "",
                "link_piriod": link_piriod,
                "servicio": sus.servicio or "",
                "inicio_servicio": sus.inicio_servicio or "",
                "cantidad_camaras": cantidad_camaras,
                "camaras_sin_bbdd": camaras_sin_bbdd,
                "moneda": moneda,
                "valor_neto_mensual": valor_neto_mensual,
                "descuento": _numero_o_vacio(sus.descuento),
                "internet": _numero_o_vacio(sus.internet),
                "valor_neto_televigilancia": _numero_o_vacio(sus.valor_neto_televigilancia),
                "valor_neto_total": _numero_o_vacio(sus.valor_neto_total),
                "valor_por_camara": _numero_o_vacio(sus.valor_por_camara),
                "direccion": sus.direccion or "",
                "comuna": sus.comuna or "",
                "region": sus.region or "",
                "direccion_completa": sus.direccion_completa or "",
                "nombre_sucursal": nombre_sucursal,
                "sucursal_id": sus.sucursal_id,
                "asignado_manual": bool(sus.sucursal_asignada_manual),
                "estado": estado,
                "fecha_termino": fecha_termino,
                "piriod_en_vivo": bool(sub_piriod),
            }
        )
    return resultado


def asignar_sucursal_suscripcion(
    db: Session, *, codigo: str, sucursal_id: int, nombre_sucursal: str
) -> None:
    """Asigna a mano la sucursal de ATC que corresponde a una fila de
    `suscripcion` (identificada por su Código), para cuando el cruce
    automático de la importación no la encontró o la encontró equivocada."""
    codigo = str(codigo or "").strip()
    if not codigo:
        raise ValueError("Falta el código de la suscripción.")
    sus = db.execute(select(Suscripcion).where(Suscripcion.codigo == codigo)).scalar_one_or_none()
    if not sus:
        raise ValueError(f"No existe una suscripción con código {codigo!r}.")
    sus.sucursal_id = sucursal_id
    sus.nombre_sucursal = nombre_sucursal
    sus.sucursal_asignada_manual = True
    db.commit()
