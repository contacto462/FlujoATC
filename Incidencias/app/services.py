from __future__ import annotations

import csv
import json
import logging
import mimetypes
import os
import re
import smtplib
import threading
import urllib.error
import urllib.request
import uuid
import unicodedata
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from html import escape as html_escape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, build_engine
from app.drive_report_service import (
    DriveReportError,
    create_drive_report_for_odt,
    list_support_images_for_odt,
    upload_support_images_for_odt,
)
from app.models import (
    AdministracionODT,
    CatalogoCliente,
    ClienteBBDD,
    ContactoEmergencia,
    IncidenciaImagenTabla,
    MantencionImagenSucursal,
    LoginSession,
    OdtVenta,
    Registro,
    RegistroCorreoCliente,
    Rendicion,
    SyncOutbox,
    Tarea,
)
from app.schemas import (
    ContactoDestinoRequest,
    EnviarInformacionContactoRequest,
    FormularioRegistro,
    IncidenciaNueva,
    RendicionRequest,
    TareaManualRequest,
)


USUARIOS_LOGIN = {
    "Ronald Montilla": "RM2025",
    "Julissa Mella": "JM2025",
    "Antonio Bahamondes": "AB2025",
    "Sthefan Leal": "SL2025",
    "Felipe Mora": "FM2025",
    "Fernando Lubiano": "Fernando1180",
}
CLAVE_TECNICOS_TEMPORAL = "123456"
_GEOCODE_CACHE: dict[str, tuple[str, str]] = {}
_COORD_FALLBACK_CL: list[tuple[tuple[str, ...], tuple[str, str]]] = [
    (("valparaiso", "valparaiso"), ("-33.0472", "-71.6127")),
    (("vina del mar", "vinaa del mar", "vina"), ("-33.0245", "-71.5518")),
    (("quilpue",), ("-33.0475", "-71.4425")),
    (("san bernardo",), ("-33.5922", "-70.6996")),
    (("san miguel",), ("-33.4979", "-70.6510")),
    (("maipu",), ("-33.5108", "-70.7653")),
    (("region metropolitana", "santiago"), ("-33.4489", "-70.6693")),
]
USUARIOS_TABLA_SERVICIO = [
    "Jason Kevin Pérez Ortiz",
    "Carlos Zamora Munita",
    "Fernando Andrés Lubiano Moraga",
]
USUARIOS_INCIDENCIAS = [
    "Mery Delgado",
    "Cristian Olivares",
    "Héctor Rosales",
    "Angélica Guerra",
    "Nicolas Santibañez",
    "Daisy Vergara",
    "Tahira Riquelme",
    "Marian Macho",
    "Manuel Mondaca",
]
MANTENCIONES_PROGRAMADAS_QUILPUE: dict[int, list[str]] = {
    1: [
        "Imq Consistorial Nuevo",
        "Imq Juzgado",
        "Imq Teatro Municipal - Dirección Cultura",
        "IMQ Centro Cultural",
        "Imq Carozzi 2 Dideco/secpla/obras",
        "Imq Derecho - Carozzi 3",
        "Imq Oficina Niñez",
    ],
    2: [
        "Imq Estadio V. Olimpica",
        "Imq Piscina V. Olimpica",
        "Imq Pisc. Bto. Sur",
        "Imq Unco",
        "Imq Oficina persona mayores",
    ],
    3: [
        "Imq Vep",
        "Imq Operaciones",
        "Imq Pisc. Bto. Norte",
        "Imq Tránsito y Transporte público - deleg el Belloto",
        "IMQ Feria",
        "Imq Centro Prácticas",
    ],
    4: [
        "Impuesto Territorial ex Seguridad Pública",
        "Imq Desarrollo economico - ex biblioteca",
        "Imq Deportes- Gimnasio Municipal",
        "IMQ Zoologico",
        "Imq CIAM",
    ],
}
MANTENCIONES_TRIMESTRALES_QUINTERO: list[str] = [
    "MQUIN Terminal de Buses",
    "MQUIN Dirección de Seguridad Pública",
    "MQUIN Parque Municipal",
    "MQUIN Seguridad Pública Loncura",
    "MQUIN Medio Ambiente",
    "MQUIN Oficina Aseo y Ornato Loncura",
    "MQUIN Estadio Municipal y Cancha aledañas",
    "MQUIN Cementerio Municipal",
    "MQUIN EDIFICIO DAEM",
    "MQUIN Escombrera Municipal",
    "MQUIN DIDECO",
    "MQUIN Aparcadero Municipal",
    "MQUIN Juzgado de Policía Local",
    "MQUIN Edificio Consistorial Base",
    "MQUIN Edificio de Administración DESAM",
    "MQUIN Farmacia Municipal",
    "MQUIN Posta de Salud Loncura",
    "MQUIN Cesfam Quintero",
]
MANTENCIONES_TRIMESTRALES_CONCON: list[str] = [
    "MC Secplac",
    "MC Playa Amarilla",
    "MC OPD",
    "MC Museo",
    "MC DIDECO",
    "MC Turismo y Omil",
    "MC Edificio Municipal",
    "MC Carpa (Avanzada Cultural)",
    "MC Cesfam",
    "MC Biblioteca Municipal",
    "MC Juzgado Policía Local",
    "MC Tránsito",
    "MC CJAM - Centro de Juventud, Adulto Mayor y Discapacidad",
    "MC Deporte, Estadio Atlético",
    "MC Parque La Isla",
    "MC Jardín Infantil Conconcito",
    "MC Centro Cultural",
    "MC DOM",
    "MC Corrales Municipales",
]
MANTENCIONES_MENSUALES_LLAY_LLAY: list[str] = [
    "Cesfam Llay Llay",
]
MESES_MANTENCION_TRIMESTRAL = {3, 6, 9, 12}
MANTENCIONES_TRIMESTRALES_POR_COMUNA: dict[str, list[str]] = {
    "quintero": MANTENCIONES_TRIMESTRALES_QUINTERO,
    "concon": MANTENCIONES_TRIMESTRALES_CONCON,
}
MANTENCIONES_IMAGENES_POR_SUCURSAL: dict[str, list[str]] = {
    "imq consistorial nuevo": [
        "app/static/mantenciones/quilpue/imq_consistorial_nuevo_1.jpg",
        "app/static/mantenciones/quilpue/imq_consistorial_nuevo_2.jpg",
    ],
}
SUCURSALES_EXTRA_MANTENCION: list[str] = [
    *MANTENCIONES_TRIMESTRALES_QUINTERO,
    *MANTENCIONES_TRIMESTRALES_CONCON,
    "Quintero",
    "Concon",
]
KNOWN_REGISTRO_BLOCKING_QUERY = "ALTER TABLE registro DROP COLUMN IF EXISTS foto_2"
LOGGER = logging.getLogger(__name__)
TICKET_STATUS_ABIERTO = "open"
TICKET_STATUS_PENDIENTE = "pending"
TICKET_STATUS_PENDIENTE_SERVICIO = "pending_service"
TICKET_STATUS_PENDIENTE_CLIENTE = "pending_client"
TICKET_STATUS_RESUELTO = "resolved"
TICKET_STATUS_RESUELTO_SERVICIO = "resolved_service"
TICKET_STATUS_RESUELTO_CLIENTE = "resolved_client"
TICKET_STATUS_CERRADO = "closed"
TICKET_STATUS_ALIASES = {
    "open": TICKET_STATUS_ABIERTO,
    "abierto": TICKET_STATUS_ABIERTO,
    "pendiente": TICKET_STATUS_PENDIENTE,
    "pending": TICKET_STATUS_PENDIENTE,
    "pendiente_servicio": TICKET_STATUS_PENDIENTE_SERVICIO,
    "pending_service": TICKET_STATUS_PENDIENTE_SERVICIO,
    "pendiente_cliente": TICKET_STATUS_PENDIENTE_CLIENTE,
    "pending_client": TICKET_STATUS_PENDIENTE_CLIENTE,
    "resuelto": TICKET_STATUS_RESUELTO,
    "resolved": TICKET_STATUS_RESUELTO,
    "resuelto_servicio": TICKET_STATUS_RESUELTO_SERVICIO,
    "resuleto_servicio": TICKET_STATUS_RESUELTO_SERVICIO,
    "resolved_service": TICKET_STATUS_RESUELTO_SERVICIO,
    "resuelto_cliente": TICKET_STATUS_RESUELTO_CLIENTE,
    "resolved_client": TICKET_STATUS_RESUELTO_CLIENTE,
    "cerrado": TICKET_STATUS_CERRADO,
    "closed": TICKET_STATUS_CERRADO,
}
TICKET_STATUSES_PERMITIDOS = {
    *TICKET_STATUS_ALIASES.keys(),
    *TICKET_STATUS_ALIASES.values(),
}


def _normalizar_estado_ticket_soporte(estado: str | None) -> str:
    raw = " ".join(str(estado or "").strip().split())
    key = unicodedata.normalize("NFD", raw.casefold())
    key = "".join(ch for ch in key if unicodedata.category(ch) != "Mn")
    key = key.replace(" ", "_").replace("-", "_")
    return TICKET_STATUS_ALIASES.get(key, key)


def _to_ddmmyyyy(valor: datetime | None) -> str:
    if not valor:
        return ""
    return valor.strftime("%d/%m/%Y")


def _to_ddmmyyyy_hhmm(valor: datetime | None) -> str:
    if not valor:
        return ""
    return valor.strftime("%d/%m/%Y %H:%M")


def _parse_prefijo_numero(odt: str | None) -> int | None:
    if not odt:
        return None
    match = re.search(r"(\d+)$", odt.strip())
    if not match:
        return None
    return int(match.group(1))


def _build_db_write_error(exc: Exception, tabla: str = "Registro") -> ValueError:
    message = str(exc or "").lower()
    if "lock timeout" in message or "canceling statement due to lock timeout" in message:
        return ValueError(
            f"La tabla {tabla} esta bloqueada en PostgreSQL por otra sesion. "
            "Cierra la transaccion abierta y vuelve a intentar."
        )
    if "deadlock detected" in message:
        return ValueError(
            f"PostgreSQL detecto un deadlock al escribir en {tabla}. "
            "Vuelve a intentar en unos segundos."
        )
    return ValueError(f"No se pudo guardar el registro en {tabla}: {exc}")


def _is_lock_timeout_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    has_lock_hint = "lock timeout" in message or "locknotavailable" in message or "tiempo de espera" in message
    return has_lock_hint and ("lock" in message or "locks" in message)


class IncidenciasService:
    def __init__(self, db: Session):
        self.db = db
        self._direcciones_csv_cache: dict[str, str] | None = None

    def _terminate_known_registro_lockers(self) -> int:
        rows = self.db.execute(
            text(
                """
                SELECT pid
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND state = 'idle in transaction'
                  AND query = :query
                  AND pid <> pg_backend_pid()
                """
            ),
            {"query": KNOWN_REGISTRO_BLOCKING_QUERY},
        ).scalars().all()

        killed = 0
        for pid in rows:
            ok = self.db.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": int(pid)}).scalar()
            if ok:
                killed += 1
        if killed:
            self.db.commit()
        return killed

    def _run_registro_query(self, loader, operation: str):
        try:
            return loader()
        except OperationalError as exc:
            self.db.rollback()
            if not _is_lock_timeout_error(exc):
                raise

            killed = self._terminate_known_registro_lockers()
            if killed:
                try:
                    return loader()
                except OperationalError as retry_exc:
                    self.db.rollback()
                    if _is_lock_timeout_error(retry_exc):
                        raise ValueError(
                            f"No se pudo {operation} porque la tabla Registro sigue bloqueada en PostgreSQL."
                        ) from retry_exc
                    raise

            raise ValueError(
                f"No se pudo {operation} porque la tabla Registro esta bloqueada en PostgreSQL."
            ) from exc

    def _ruta_csv_registro_incidencias(self) -> Path:
        return Path(__file__).resolve().parents[2] / "ATC" / "Registro Incidencias - Registro.csv"

    def _load_env_runtime(self) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            env_path = Path(__file__).resolve().parents[1] / ".env"
            if not env_path.exists():
                return out
            for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                key = k.strip()
                val = v.strip().strip('"').strip("'")
                if key:
                    out[key] = val
        except Exception:
            return out
        return out

    @staticmethod
    def _to_bool_env(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        txt = str(value).strip().lower()
        if txt in {"1", "true", "yes", "on"}:
            return True
        if txt in {"0", "false", "no", "off"}:
            return False
        return default

    def _smtp_runtime_config(self) -> dict[str, Any]:
        env_file = self._load_env_runtime()
        env_get = lambda k, d="": (os.getenv(k) or env_file.get(k) or d)

        enabled = bool(settings.smtp_enabled)
        enabled = enabled or self._to_bool_env(env_get("SMTP_ENABLED", "false"), False)

        host = str(settings.smtp_host or env_get("SMTP_HOST", "")).strip()
        port_raw = settings.smtp_port or env_get("SMTP_PORT", "587")
        try:
            port = int(port_raw)
        except Exception:
            port = 587
        username = str(settings.smtp_username or env_get("SMTP_USERNAME", "")).strip()
        password = str(settings.smtp_password or env_get("SMTP_PASSWORD", ""))
        from_email = str(settings.smtp_from_email or env_get("SMTP_FROM_EMAIL", username)).strip()
        from_name = str(settings.smtp_from_name or env_get("SMTP_FROM_NAME", "ATC Incidencias")).strip()
        use_tls = self._to_bool_env(settings.smtp_use_tls, True)
        use_tls = self._to_bool_env(env_get("SMTP_USE_TLS", str(use_tls).lower()), use_tls)
        use_ssl = self._to_bool_env(settings.smtp_use_ssl, False)
        use_ssl = self._to_bool_env(env_get("SMTP_USE_SSL", str(use_ssl).lower()), use_ssl)
        timeout_raw = settings.smtp_timeout_sec or env_get("SMTP_TIMEOUT_SEC", "20")
        try:
            timeout = int(timeout_raw)
        except Exception:
            timeout = 20

        return {
            "enabled": enabled,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "from_email": from_email,
            "from_name": from_name,
            "use_tls": use_tls,
            "use_ssl": use_ssl,
            "timeout": timeout,
        }

    def _logo_atc_bytes(self) -> bytes | None:
        try:
            logo_path = Path(__file__).resolve().parents[2] / "ATC" / "static" / "img" / "logo-atc2.jpeg"
            if not logo_path.exists():
                return None
            return logo_path.read_bytes()
        except Exception:
            return None

    @staticmethod
    def _parse_fecha_visita(fecha_raw: str | None) -> datetime | None:
        valor = str(fecha_raw or "").strip()
        if not valor:
            return None
        try:
            # Soporta date/datetime ISO: YYYY-MM-DD o YYYY-MM-DDTHH:MM
            return datetime.fromisoformat(valor)
        except Exception:
            pass
        try:
            # Soporta formato tabla: dd/mm/yyyy HH:MM
            return datetime.strptime(valor, "%d/%m/%Y %H:%M")
        except Exception:
            pass
        try:
            # Soporta formato simple: dd/mm/yyyy
            return datetime.strptime(valor, "%d/%m/%Y")
        except Exception:
            return None

    def _build_correo_visita_html(
        self,
        *,
        odt: str,
        sucursal: str,
        problema: str,
        estado: str,
        tecnico: str,
        acompanante: str,
        fecha_visita: datetime,
        observacion: str,
    ) -> tuple[str, str, str]:
        fecha_txt = fecha_visita.strftime("%d/%m/%Y")
        tecnico_txt = tecnico or "Por confirmar"
        acompanante_txt = acompanante or "Sin tecnico adicional asignado"
        subject = f"Aviso de Visita Tecnica - {sucursal}"

        html_body = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f3f5f8;font-family:Segoe UI,Arial,sans-serif;color:#0f172a;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f5f8;padding:30px 14px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:720px;background:#ffffff;border:1px solid #dde4ec;border-radius:14px;overflow:hidden;box-shadow:0 8px 24px rgba(2,18,36,0.08);">
            <tr>
              <td style="background:#0f3048;padding:12px 0;"></td>
            </tr>
            <tr>
              <td style="padding:24px 28px 12px;text-align:center;">
                <img src="cid:logoatc" alt="ATC" style="height:54px;width:auto;display:block;margin:0 auto 10px;" />
                <div style="font-size:34px;line-height:1.18;font-weight:800;color:#0f3048;letter-spacing:0.2px;">Visita de Servicio Tecnico</div>
                <div style="font-size:15px;color:#3b4b5c;margin-top:8px;">Alguien Te Cuida</div>
              </td>
            </tr>
            <tr>
              <td style="padding:8px 28px 26px;">
                <p style="margin:0 0 14px;font-size:21px;line-height:1.45;color:#1b2a3a;">
                  Estimados/as,
                </p>
                <p style="margin:0 0 16px;font-size:22px;line-height:1.5;color:#1b2a3a;">
                  Informamos que el equipo de <b>Servicio Tecnico de Alguien Te Cuida</b> el dia
                  <b>{html_escape(fecha_txt)}</b>, realizara una visita tecnica a la dependencia
                  "<b>{html_escape(sucursal)}</b>".
                </p>

                <div style="margin:0 0 14px;background:#f7f9fc;border:1px solid #d8e0ea;border-radius:10px;padding:14px 16px;">
                  <div style="font-size:20px;font-weight:800;color:#10263a;margin-bottom:8px;">Tecnicos asignados:</div>
                  <div style="font-size:21px;line-height:1.55;color:#1f3347;">{html_escape(tecnico_txt)}</div>
                  <div style="font-size:21px;line-height:1.55;color:#1f3347;">{html_escape(acompanante_txt)}</div>
                </div>
                <div style="font-size:16px;line-height:1.6;color:#4a5b6c;margin-bottom:16px;">
                  (Sujeto a modificaciones, de ser asi se le notificara por este mismo medio)
                </div>

                <div style="margin-top:14px;background:#f4f8ff;border:1px solid #cfe0ff;border-left:4px solid #2a5fa0;border-radius:10px;padding:12px 14px;color:#1f3b5f;font-size:16px;line-height:1.7;">
                  Esta Visita Tecnica se realizara entre las <b>09:00 AM</b> y las <b>18:00 PM</b>,
                  la que tiene como objetivo asegurar la continuidad y correcta operacion de los servicios contratados.
                </div>

                <p style="margin:18px 0 0;font-size:17px;line-height:1.65;color:#2a3a4a;">
                  Agradecemos mantener acceso disponible a las zonas de trabajo e informar si existiese alguna eventual reprogramacion.
                </p>

                <p style="margin:20px 0 0;font-size:17px;line-height:1.65;color:#2a3a4a;">
                  Saludos cordiales,<br />
                  <span style="font-weight:700;color:#1f5fa3;">Equipo Tecnico</span><br />
                  Alguien Te Cuida
                </p>
              </td>
            </tr>
            <tr>
              <td style="border-top:1px solid #e2e8f0;padding:14px 24px;text-align:center;font-size:12px;color:#6b7c8f;background:#fafbfd;">
                Mensaje generado automaticamente por ATC - Servicio Tecnico.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""

        text_body = "\n".join(
            [
                "Visita de Servicio Tecnico ATC",
                "",
                "Estimados/as,",
                "",
                f"Informamos que el equipo de Servicio Tecnico el dia {fecha_txt},",
                f"realizara una visita tecnica a la dependencia \"{sucursal}\".",
                "",
                "Tecnicos asignados:",
                tecnico_txt,
                acompanante_txt,
                "(Sujeto a modificaciones, de ser asi se le notificara por este mismo medio)",
                "",
                "Esta Visita Tecnica se realizara entre las 09:00 AM y las 18:00 PM,",
                "la que tiene como objetivo asegurar la continuidad y correcta operacion de los servicios contratados.",
                "",
                "Agradecemos mantener acceso disponible a las zonas de trabajo para el óptimo servicio e informar si existiese alguna eventual reprogramacion.",
                "",
                "Saludos cordiales,",
                "Equipo Tecnico",
                "ATC - Alguien Te Cuida",
            ]
        )
        return subject, text_body + "\n", html_body

    def _direcciones_desde_csv(self) -> dict[str, str]:
        if self._direcciones_csv_cache is not None:
            return self._direcciones_csv_cache

        ruta = self._ruta_csv_registro_incidencias()
        out: dict[str, str] = {}
        if not ruta.exists():
            self._direcciones_csv_cache = out
            return out

        try:
            with ruta.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames:
                    self._direcciones_csv_cache = out
                    return out

                headers = {self._normalizar_texto(h): h for h in reader.fieldnames if h}
                col_sucursal = next((headers.get(key) for key in ["sucursal", "cliente", "nombre sucursal", "nombre cliente"] if headers.get(key)), None)
                col_direccion = next((headers.get(key) for key in ["direccion", "direccion sucursal", "direccion trabajos", "direccion cliente"] if headers.get(key)), None)
                if not col_sucursal or not col_direccion:
                    self._direcciones_csv_cache = out
                    return out

                for row in reader:
                    sucursal = str(row.get(col_sucursal) or "").strip()
                    direccion = str(row.get(col_direccion) or "").strip()
                    if not sucursal or not direccion:
                        continue
                    key = self._normalizar_texto(sucursal)
                    if key and key not in out:
                        out[key] = direccion
        except Exception:
            out = {}

        self._direcciones_csv_cache = out
        return out

    def _obtener_tecnicos_helpdesk(self, solo_activos: bool = True) -> list[str]:
        db_url = (settings.support_db_url or "").strip()
        if not db_url:
            return []
        schema = (settings.support_db_schema or "public").strip() or "public"

        try:
            eng = build_engine(db_url, pool_pre_ping=True)
            with eng.connect() as conn:
                cols = conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = :schema_name
                          AND table_name = 'incidencias_tecnicos'
                        """
                    ),
                    {"schema_name": schema},
                ).all()
                colset = {str(c[0]).strip() for c in cols if c and c[0]}
                if not colset:
                    return []

                col_nombre = next((c for c in ["nombre", "tecnico", "nombre_tecnico"] if c in colset), None)
                if not col_nombre:
                    return []
                col_activo = "activo" if "activo" in colset else None

                where = ""
                if solo_activos and col_activo:
                    where = f'WHERE "{col_activo}" = TRUE'

                rows = conn.execute(
                    text(
                        f'''
                        SELECT btrim(CAST("{col_nombre}" AS text)) AS nombre
                        FROM "{schema}"."incidencias_tecnicos"
                        {where}
                        ORDER BY 1
                        '''
                    )
                ).all()
                out = [str(r[0]).strip() for r in rows if r and r[0] and str(r[0]).strip()]
                # Deduplicado preservando orden
                vistos: set[str] = set()
                unicos: list[str] = []
                for n in out:
                    k = self._normalizar_nombre_login(n)
                    if not k or k in vistos:
                        continue
                    vistos.add(k)
                    unicos.append(n)
                return unicos
        except Exception:
            return []

    def _schemas_con_tabla(self, table_name: str) -> list[str]:
        rows = self.db.execute(
            text(
                """
                SELECT DISTINCT table_schema
                FROM information_schema.columns
                WHERE table_name = :table_name
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema
                """
            ),
            {"table_name": table_name},
        ).all()
        return [str(r[0]).strip() for r in rows if r and r[0]]

    def _columnas_tabla(self, schema_name: str, table_name: str) -> set[str]:
        rows = self.db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema_name
                  AND table_name = :table_name
                """
            ),
            {"schema_name": schema_name, "table_name": table_name},
        ).all()
        return {str(r[0]).strip() for r in rows if r and r[0]}

    def _pick_col(self, cols: set[str], opciones: list[str]) -> str | None:
        return next((c for c in opciones if c in cols), None)

    def _reparar_texto_mojibake(self, valor: Any) -> str:
        txt = str(valor or "").strip()
        if not txt:
            return ""

        def _score(s: str) -> tuple[int, int]:
            marcadores = ("Ã", "Â", "â", "ð", "�")
            raros = sum(s.count(ch) for ch in marcadores)
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
            mejor_paso = min(candidatos, key=_score)
            if _score(mejor_paso) < _score(mejor):
                mejor = mejor_paso

            if mejor_paso in visto or mejor_paso == actual:
                break

            visto.add(mejor_paso)
            actual = mejor_paso

        return mejor.strip()

    def _normalizar_nombre_login(self, valor: Any) -> str:
        txt = self._reparar_texto_mojibake(valor).lower()
        txt = unicodedata.normalize("NFD", txt)
        txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def _extraer_nombres_desde_texto(self, valor: Any) -> list[str]:
        txt = self._reparar_texto_mojibake(valor)
        if not txt or txt in {"-", "Todos", "todos"}:
            return []
        txt = re.sub(r"(?i)acompa(?:n|ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â±)ante\s*:\s*", ";", txt)
        txt = re.sub(r"(?i)tecnic(?:o|a|os|as)\s*:\s*", ";", txt)
        partes = re.split(r"[\n,;/|]+", txt)
        salida: list[str] = []
        for p in partes:
            nombre = re.sub(r"\s+", " ", p).strip(" -\t\r")
            if not nombre:
                continue
            if nombre in {"-", "Todos", "todos"}:
                continue
            salida.append(nombre)
        return salida

    def _usuarios_login_tecnicos(self) -> list[str]:
        nombres: dict[str, str] = {}

        def _add(valor: Any) -> None:
            for nombre in self._extraer_nombres_desde_texto(valor):
                key = self._normalizar_nombre_login(nombre)
                if key and key not in nombres:
                    nombres[key] = nombre

        # Fuente principal pedida por operaciÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n: helpdesk.incidencias_tecnicos
        for nombre in self._obtener_tecnicos_helpdesk(solo_activos=True):
            _add(nombre)

        try:
            for v in self.db.scalars(select(Registro.tecnicos)).all():
                _add(v)
            for v in self.db.scalars(select(Registro.acompanante)).all():
                _add(v)
        except Exception:
            pass
        try:
            for v in self.db.scalars(select(AdministracionODT.tecnico)).all():
                _add(v)
            for v in self.db.scalars(select(AdministracionODT.acompanante)).all():
                _add(v)
        except Exception:
            pass
        try:
            for v in self.db.scalars(select(OdtVenta.tecnico)).all():
                _add(v)
            for v in self.db.scalars(select(OdtVenta.acompanante)).all():
                _add(v)
        except Exception:
            pass

        # Compatibilidad: usuarios histÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³ricos de soporte.
        for nombre in USUARIOS_LOGIN.keys():
            _add(nombre)

        return sorted(nombres.values(), key=lambda x: self._normalizar_nombre_login(x))

    def _usuarios_login_tabla_servicio(self) -> list[str]:
        return list(USUARIOS_TABLA_SERVICIO)

    def _usuarios_login_incidencias(self) -> list[str]:
        return list(USUARIOS_INCIDENCIAS)

    def _es_usuario_tabla_servicio(self, usuario: str) -> bool:
        usuario_norm = self._normalizar_nombre_login(usuario)
        permitidos = {
            self._normalizar_nombre_login(n): n for n in self._usuarios_login_tabla_servicio()
        }
        return usuario_norm in permitidos

    def obtener_usuarios_login_tecnicos(self, destino: str = "tecnicos") -> list[str]:
        destino_norm = (destino or "").strip().lower()
        if destino_norm in {
            "panelselector",
            "panel_selector",
            "panelselectorcoordinacion",
            "panel_selector_coordinacion",
            "cierreaperturaclientes",
            "controlprotocolos",
            "tablaprotocolos",
            "envioprotocolossemanales",
            "coordinacion",
            "panelselectorventa",
            "registrocliente",
            "tablacliente",
        }:
            return self._usuarios_login_incidencias()
        if destino_norm in {"panelselectorservicio", "panel_selector_servicio", "stventas"}:
            return self._usuarios_login_tabla_servicio()
        if destino_norm in {"tabla", "serviciotecnico"}:
            return self._usuarios_login_tabla_servicio()
        if destino_norm in {"incidencias"}:
            return self._usuarios_login_incidencias()
        return self._usuarios_login_tecnicos()

    # =========================
    # LOGIN
    # =========================
    def _expira_fin_dia_utc(self) -> datetime:
        tz_name = (settings.timezone or "America/Santiago").strip() or "America/Santiago"
        tz = ZoneInfo(tz_name)
        now_local = datetime.now(tz)
        midnight_local = datetime.combine(now_local.date() + timedelta(days=1), time.min, tzinfo=tz)
        return midnight_local.astimezone(timezone.utc).replace(tzinfo=None)

    def check_login(
        self,
        nombre_tecnico: str,
        clave: str,
        token: str,
        app_url: str,
        destino: str = "pendientes",
    ) -> dict[str, Any]:
        nombre_limpio = str(nombre_tecnico or "").strip()
        if not nombre_limpio:
            return {"success": False, "message": "Usuario invalido"}

        destino_norm = (destino or "").strip()
        if destino_norm == "tabla":
            destino_norm = "servicioTecnico"
        if destino_norm == "STVentas":
            destino_norm = "stVentas"
        destino_ok = (
            destino_norm
            if destino_norm
            in {
                "panelSelector",
                "panelSelectorServicio",
                "panelSelectorCoordinacion",
                "panelSelectorVenta",
                "registroCliente",
                "tablaCliente",
                "incidencias",
                "cierreAperturaClientes",
                "controlProtocolos",
                "tablaProtocolos",
                "envioProtocolosSemanales",
                "pendientes",
                "tecnicos",
                "coordinacion",
                "servicioTecnico",
                "stVentas",
            }
            else "tecnicos"
        )

        if destino_ok in {"servicioTecnico", "panelSelectorServicio", "stVentas"}:
            usuarios_base = self._usuarios_login_tabla_servicio()
        elif destino_ok in {
            "incidencias",
            "panelSelector",
            "panelSelectorCoordinacion",
            "panelSelectorVenta",
            "registroCliente",
            "tablaCliente",
            "cierreAperturaClientes",
            "controlProtocolos",
            "tablaProtocolos",
            "envioProtocolosSemanales",
            "coordinacion",
        }:
            usuarios_base = self._usuarios_login_incidencias()
        else:
            usuarios_base = self._usuarios_login_tecnicos()
        usuarios_norm = {self._normalizar_nombre_login(u): u for u in usuarios_base}
        nombre_norm = self._normalizar_nombre_login(nombre_limpio)

        # Modo temporal solicitado: clave unica para tecnicos.
        if str(clave or "").strip() == CLAVE_TECNICOS_TEMPORAL:
            if destino_ok in {
                "servicioTecnico",
                "panelSelectorServicio",
                "stVentas",
                "incidencias",
                "panelSelector",
                "panelSelectorCoordinacion",
                "panelSelectorVenta",
                "registroCliente",
                "tablaCliente",
                "cierreAperturaClientes",
                "controlProtocolos",
                "tablaProtocolos",
                "envioProtocolosSemanales",
                "coordinacion",
            }:
                # En tabla de servicio, acceso estricto solo a usuarios permitidos.
                if nombre_norm not in usuarios_norm:
                    scope_name = (
                        "Tabla Servicio Tecnico"
                        if destino_ok in {"servicioTecnico", "panelSelectorServicio", "stVentas"}
                        else ("Venta" if destino_ok in {"panelSelectorVenta", "registroCliente", "tablaCliente"} else "Operaciones")
                    )
                    return {"success": False, "message": f"Usuario no autorizado para {scope_name}"}
                nombre_sesion = usuarios_norm[nombre_norm]
            else:
                # Modo temporal para tÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©cnicos: permitir nombre libre.
                nombre_sesion = usuarios_norm.get(nombre_norm, nombre_limpio)
        else:
            # Fallback legado: claves histÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³ricas de soporte.
            if nombre_limpio not in USUARIOS_LOGIN:
                return {"success": False, "message": "Usuario invalido"}
            if USUARIOS_LOGIN[nombre_limpio] != clave:
                return {"success": False, "message": "Clave incorrecta"}
            nombre_sesion = nombre_limpio

        self.db.merge(
            LoginSession(
                token=token,
                usuario=nombre_sesion,
                expires_at=self._expira_fin_dia_utc(),
            )
        )
        self.db.commit()
        if destino_ok in {"servicioTecnico", "panelSelectorServicio", "stVentas"}:
            return {
                "success": True,
                "redirect": f"{app_url}?form=panelSelectorServicio&token={token}&next={destino_ok}",
            }
        if destino_ok == "coordinacion":
            return {"success": True, "redirect": f"{app_url}?form=coordinacion&token={token}"}
        if destino_ok in {"panelSelectorCoordinacion", "tablaProtocolos", "envioProtocolosSemanales"}:
            return {
                "success": True,
                "redirect": f"{app_url}?form=panelSelectorCoordinacion&token={token}&next={destino_ok}",
            }
        if destino_ok in {"panelSelectorVenta", "registroCliente", "tablaCliente"}:
            return {
                "success": True,
                "redirect": f"{app_url}/venta/panel-selector?token={token}&next={destino_ok}",
            }
        if destino_ok in {"incidencias", "panelSelector", "cierreAperturaClientes", "controlProtocolos"}:
            return {
                "success": True,
                "redirect": f"{app_url}?form=panelSelector&token={token}&next={destino_ok}",
            }
        return {"success": True, "redirect": f"{app_url}?form={destino_ok}&token={token}"}

    def usuario_logueado_por_token(self, token: str) -> bool:
        if not token:
            return False
        now = datetime.utcnow()
        stmt = select(LoginSession).where(LoginSession.token == token, LoginSession.expires_at > now)
        return self.db.scalar(stmt) is not None

    def get_usuario_actual(self, token: str) -> str:
        if not token:
            return "Desconocido"
        now = datetime.utcnow()
        stmt = select(LoginSession).where(LoginSession.token == token, LoginSession.expires_at > now)
        sesion = self.db.scalar(stmt)
        return sesion.usuario if sesion else "Desconocido"

    def usuario_autorizado_para_tabla(self, token: str) -> bool:
        usuario = self.get_usuario_actual(token)
        if not usuario or usuario == "Desconocido":
            return False
        return self._es_usuario_tabla_servicio(usuario)

    def logout(self, token: str) -> bool:
        sesion = self.db.get(LoginSession, token)
        if sesion:
            self.db.delete(sesion)
            self.db.commit()
        return True

    # =========================
    # REGISTRO / INCIDENCIAS
    # =========================
    def _proximo_odt(self, prefijo: str = "I") -> str:
        odts = self._run_registro_query(
            lambda: list(self.db.scalars(select(Registro.odt))),
            "obtener el correlativo de ODT",
        )
        mayor = 0
        for odt in odts:
            n = _parse_prefijo_numero(odt)
            if n is not None:
                mayor = max(mayor, n)
        return f"{prefijo}{mayor + 1}"

    def _ticket_soporte_existe(self, conn, schema: str, ticket_id: int | None) -> int | None:
        if not ticket_id:
            return None
        exists = conn.execute(
            text(f'SELECT id FROM "{schema}"."tickets" WHERE id = :ticket_id'),
            {"ticket_id": int(ticket_id)},
        ).scalar()
        return int(exists) if exists else None

    def _resolver_ticket_id_soporte_desde_registro(self, conn, schema: str, odt: str) -> int | None:
        odt_limpia = str(odt or "").strip()
        if not odt_limpia:
            return None

        for schema_registro in self._schemas_con_tabla("registro"):
            cols = self._columnas_tabla(schema_registro, "registro")
            col_odt = self._pick_col(cols, ["odt", "codigo_odt", "codigo", "nro_odt"])
            col_source_row = self._pick_col(cols, ["source_row", "source_id", "origen_id"])
            col_source_file = self._pick_col(cols, ["source_file", "source", "origin", "origen"])
            if not col_odt or not col_source_row:
                continue

            select_source = f'CAST("{col_source_file}" AS text) AS source_file' if col_source_file else "'' AS source_file"
            row = self.db.execute(
                text(
                    f'''
                    SELECT CAST("{col_source_row}" AS text) AS source_row,
                           {select_source}
                    FROM "{schema_registro}"."registro"
                    WHERE btrim(CAST("{col_odt}" AS text)) = :odt
                    LIMIT 1
                    '''
                ),
                {"odt": odt_limpia},
            ).mappings().first()
            if not row:
                continue

            source_file_norm = self._normalizar_texto(row.get("source_file") or "")
            if col_source_file and source_file_norm not in {"ticket", "tickets", "helpdesk", "soporte"}:
                continue
            try:
                ticket_id = int(str(row.get("source_row") or "").strip())
            except Exception:
                continue
            return self._ticket_soporte_existe(conn, schema, ticket_id)

        return None

    def _resolver_ticket_id_soporte_desde_mensajes(self, conn, schema: str, odt: str) -> int | None:
        odt_limpia = str(odt or "").strip()
        if not odt_limpia:
            return None

        needle = f"%{odt_limpia}%"
        rows = conn.execute(
            text(
                f'''
                SELECT m.ticket_id, CAST(m.content AS text) AS content
                FROM "{schema}"."messages" m
                JOIN "{schema}"."tickets" t ON t.id = m.ticket_id
                WHERE CAST(m.content AS text) ILIKE :needle
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT 50
                '''
            ),
            {"needle": needle},
        ).mappings().all()
        patron_odt = re.compile(rf"(?<![A-Za-z0-9]){re.escape(odt_limpia)}(?![A-Za-z0-9])", re.IGNORECASE)
        for row in rows:
            if patron_odt.search(str(row.get("content") or "")):
                try:
                    return int(row.get("ticket_id"))
                except Exception:
                    continue
        return None

    def _resolver_ticket_id_soporte(self, conn, schema: str, odt: str) -> int | None:
        # Importante: la ODT es correlativo de Incidencias, no ID de ticket.
        # Nunca debe mapearse T15 -> ticket #15 por el numero.
        ticket_id = self._resolver_ticket_id_soporte_desde_registro(conn, schema, odt)
        if ticket_id:
            return ticket_id
        return self._resolver_ticket_id_soporte_desde_mensajes(conn, schema, odt)

    def _insertar_nota_ticket_soporte(self, conn, schema: str, ticket_id: int, contenido: str) -> None:
        contenido_limpio = str(contenido or "").strip()
        if not contenido_limpio:
            return
        conn.execute(
            text(
                f'''
                INSERT INTO "{schema}"."messages"
                    (ticket_id, sender_type, channel, content, is_internal_note, created_at)
                VALUES
                    (:ticket_id, :sender_type, :channel, :content, :is_internal_note, :created_at)
                '''
            ),
            {
                "ticket_id": ticket_id,
                "sender_type": "system",
                "channel": "internal",
                "content": contenido_limpio,
                "is_internal_note": True,
                "created_at": datetime.now(timezone.utc),
            },
        )

    def _actualizar_estado_ticket_soporte(
        self,
        odt: str,
        estado: str,
        nota_interna: str | None = None,
    ) -> bool:
        estado_limpio = _normalizar_estado_ticket_soporte(estado)
        if estado_limpio not in TICKET_STATUSES_PERMITIDOS:
            raise ValueError("Estado de ticket no permitido.")

        db_url = str(settings.support_db_url or "").strip()
        if not db_url:
            return False

        schema = (settings.support_db_schema or "public").strip() or "public"
        engine = build_engine(db_url, pool_pre_ping=True)
        ahora = datetime.utcnow()
        with engine.begin() as conn:
            ticket_id = self._resolver_ticket_id_soporte(conn, schema, odt)
            if ticket_id is None:
                return False

            result = conn.execute(
                text(
                    f'''
                    UPDATE "{schema}"."tickets"
                    SET status = CAST(:status AS varchar),
                        updated_at = :updated_at,
                        resolved_at = CASE
                            WHEN CAST(:status AS text) IN (:resuelto, :resuelto_servicio, :resuelto_cliente) THEN COALESCE(resolved_at, :updated_at)
                            ELSE resolved_at
                        END,
                        closed_at = CASE
                            WHEN CAST(:status AS text) = :cerrado THEN COALESCE(closed_at, :updated_at)
                            ELSE closed_at
                        END
                    WHERE id = :ticket_id
                    '''
                ),
                {
                    "status": estado_limpio,
                    "updated_at": ahora,
                    "resuelto": TICKET_STATUS_RESUELTO,
                    "resuelto_servicio": TICKET_STATUS_RESUELTO_SERVICIO,
                    "resuelto_cliente": TICKET_STATUS_RESUELTO_CLIENTE,
                    "cerrado": TICKET_STATUS_CERRADO,
                    "ticket_id": ticket_id,
                },
            )
            if nota_interna:
                try:
                    self._insertar_nota_ticket_soporte(conn, schema, ticket_id, nota_interna)
                except Exception:
                    LOGGER.exception("No se pudo agregar nota interna al ticket soporte para ODT %s.", odt)
            return int(result.rowcount or 0) > 0

    def _sync_estado_ticket_soporte_silencioso(
        self,
        odt: str,
        estado: str,
        nota_interna: str | None = None,
    ) -> None:
        try:
            self._actualizar_estado_ticket_soporte(odt, estado, nota_interna=nota_interna)
        except Exception:
            LOGGER.exception("No se pudo actualizar estado de ticket soporte para ODT %s.", odt)

    def _build_nota_cierre_ticket_soporte(
        self,
        *,
        odt: str,
        estado_ticket: str,
        derivacion: str,
        observacion_final: str,
    ) -> str:
        estado_label = {
            TICKET_STATUS_RESUELTO_CLIENTE: "Resuelto Cliente",
            TICKET_STATUS_RESUELTO_SERVICIO: "Resuelto Servicio",
            TICKET_STATUS_RESUELTO: "Resuelto",
            TICKET_STATUS_CERRADO: "Cerrado",
        }.get(_normalizar_estado_ticket_soporte(estado_ticket), estado_ticket)
        detalle = str(observacion_final or "").strip() or "Sin observacion final registrada."
        return (
            "<strong>Actualizacion automatica desde Incidencias</strong><br>"
            f"ODT: {html_escape(str(odt or '').strip())}<br>"
            f"Estado: {html_escape(estado_label)}<br>"
            f"Derivacion/Cierre: {html_escape(str(derivacion or '').strip() or '-')}<br>"
            f"Resultado final: {html_escape(detalle).replace(chr(10), '<br>')}"
        )


    def _proximo_odt_incidencias(self, prefijo: str = "I") -> str:
        return self._proximo_odt(prefijo)

    def _direccion_cliente(self, cliente: str) -> str:
        cliente_txt = str(cliente or "").strip()
        if not cliente_txt:
            return ""

        try:
            for schema_name in self._schemas_con_tabla("catalogo_clientes"):
                cols = self._columnas_tabla(schema_name, "catalogo_clientes")
                col_cliente = self._pick_col(cols, ["nombre_sucursal", "nombre_cliente", "sucursal", "cliente"])
                col_direccion = self._pick_col(cols, ["direccion", "direccion_sucursal", "direccion_trabajos", "direccion_cliente"])
                if not col_cliente or not col_direccion:
                    continue

                sql = text(
                    f"""
                    SELECT COALESCE(CAST("{col_direccion}" AS text), '') AS direccion
                    FROM "{schema_name}"."catalogo_clientes"
                    WHERE lower(btrim(CAST("{col_cliente}" AS text))) = lower(:cliente)
                    LIMIT 1
                    """
                )
                row = self.db.execute(sql, {"cliente": cliente_txt}).mappings().first()
                if row:
                    direccion = str(row.get("direccion") or "").strip()
                    if direccion:
                        return direccion
        except Exception:
            self.db.rollback()

        try:
            stmt = select(ClienteBBDD.direccion).where(ClienteBBDD.cliente == cliente_txt)
            direccion = self.db.scalar(stmt) or ""
            if direccion:
                return str(direccion).strip()
        except Exception:
            self.db.rollback()

        try:
            return self._direcciones_desde_csv().get(self._normalizar_texto(cliente_txt), "")
        except Exception:
            return ""

    def _normalizar_direccion(self, valor: Any) -> str:
        txt = self._normalizar_texto(valor)
        txt = re.sub(r"[^a-z0-9\s]", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def _extraer_numero_direccion(self, valor: Any) -> str:
        txt = str(valor or "").strip()
        if not txt:
            return ""
        m = re.search(r"\b(\d{2,6})\b", txt)
        return m.group(1) if m else ""

    def _contacto_preferente_sucursal(self, cliente: str) -> dict[str, str]:
        salida = {"contacto": "", "telefono": "", "correo": ""}
        cliente_norm = self._normalizar_texto(cliente)
        if not cliente_norm:
            return salida

        try:
            contactos = self.obtener_contactos_por_sucursal()
            sucursal_key = next(
                (key for key in contactos.keys() if self._normalizar_texto(key) == cliente_norm),
                None,
            )
            if not sucursal_key:
                return salida

            candidatos = contactos.get(sucursal_key) or []
            if not candidatos:
                return salida

            primero = candidatos[0]
            salida["contacto"] = str(primero.get("nombre") or "").strip()
            salida["telefono"] = str(primero.get("telefono") or "").strip()
            salida["correo"] = str(primero.get("email") or "").strip()
        except Exception:
            return salida

        return salida

    def _coordenadas_por_direccion_bd(self, direccion: str) -> tuple[str, str]:
        direccion_txt = str(direccion or "").strip()
        direccion_norm = self._normalizar_direccion(direccion_txt)
        nro_ref = self._extraer_numero_direccion(direccion_txt)
        if not direccion_norm:
            return "", ""

        for table_name in ["bbdd_clientes", "catalogo_clientes"]:
            try:
                for schema_name in self._schemas_con_tabla(table_name):
                    cols = self._columnas_tabla(schema_name, table_name)
                    if not cols:
                        continue
                    col_dir = self._pick_col(cols, ["direccion", "direccion_sucursal", "direccion_trabajos", "direccion_cliente"])
                    col_lat = self._pick_col(cols, ["latitud", "lat", "latitude"])
                    col_lng = self._pick_col(cols, ["longitud", "lng", "lon", "longitude"])
                    if not col_dir or not col_lat or not col_lng:
                        continue

                    sql = text(
                        f"""
                        SELECT
                            COALESCE(CAST("{col_lat}" AS text), '') AS latitud,
                            COALESCE(CAST("{col_lng}" AS text), '') AS longitud
                        FROM "{schema_name}"."{table_name}"
                        WHERE lower(regexp_replace(btrim(CAST("{col_dir}" AS text)), '\\s+', ' ', 'g'))
                              = lower(regexp_replace(:direccion, '\\s+', ' ', 'g'))
                          AND btrim(CAST("{col_lat}" AS text)) <> ''
                          AND btrim(CAST("{col_lng}" AS text)) <> ''
                        LIMIT 1
                        """
                    )
                    row = self.db.execute(sql, {"direccion": direccion_txt}).mappings().first()
                    if row:
                        lat = str(row.get("latitud") or "").strip()
                        lng = str(row.get("longitud") or "").strip()
                        if lat and lng:
                            return lat, lng
            except Exception:
                continue

        # Fallback BD flexible: tolera variaciones de formato en direccion
        # (parentesis, comas, espacios, etc.).
        mejor_score = -1
        mejor_coords: tuple[str, str] = ("", "")
        tokens_ref = set(direccion_norm.split())

        for table_name in ["bbdd_clientes", "catalogo_clientes"]:
            try:
                for schema_name in self._schemas_con_tabla(table_name):
                    cols = self._columnas_tabla(schema_name, table_name)
                    if not cols:
                        continue
                    col_dir = self._pick_col(cols, ["direccion", "direccion_sucursal", "direccion_trabajos", "direccion_cliente"])
                    col_lat = self._pick_col(cols, ["latitud", "lat", "latitude"])
                    col_lng = self._pick_col(cols, ["longitud", "lng", "lon", "longitude"])
                    if not col_dir or not col_lat or not col_lng:
                        continue

                    sql_all = text(
                        f"""
                        SELECT
                            COALESCE(CAST("{col_dir}" AS text), '') AS direccion,
                            COALESCE(CAST("{col_lat}" AS text), '') AS latitud,
                            COALESCE(CAST("{col_lng}" AS text), '') AS longitud
                        FROM "{schema_name}"."{table_name}"
                        WHERE btrim(CAST("{col_dir}" AS text)) <> ''
                          AND btrim(CAST("{col_lat}" AS text)) <> ''
                          AND btrim(CAST("{col_lng}" AS text)) <> ''
                        """
                    )
                    for row in self.db.execute(sql_all).mappings().all():
                        dir_cand = str(row.get("direccion") or "").strip()
                        lat = str(row.get("latitud") or "").strip()
                        lng = str(row.get("longitud") or "").strip()
                        if not dir_cand or not lat or not lng:
                            continue

                        dir_norm_cand = self._normalizar_direccion(dir_cand)
                        if not dir_norm_cand:
                            continue
                        tokens_cand = set(dir_norm_cand.split())
                        inter = len(tokens_ref & tokens_cand)
                        if inter <= 0:
                            continue

                        score = inter
                        if direccion_norm in dir_norm_cand or dir_norm_cand in direccion_norm:
                            score += 4
                        nro_cand = self._extraer_numero_direccion(dir_cand)
                        if nro_ref and nro_cand and nro_ref == nro_cand:
                            score += 8
                        elif nro_ref and nro_cand and nro_ref != nro_cand:
                            score -= 6

                        if score > mejor_score:
                            mejor_score = score
                            mejor_coords = (lat, lng)
            except Exception:
                continue

        # Exigir coincidencia minima razonable.
        if mejor_score >= 3 and mejor_coords[0] and mejor_coords[1]:
            return mejor_coords

        return "", ""

    def _geocodificar_direccion(self, direccion: str) -> tuple[str, str]:
        def _parse_coords(lat_raw: Any, lng_raw: Any) -> tuple[str, str]:
            try:
                lat_txt = str(lat_raw or "").strip().replace(",", ".")
                lng_txt = str(lng_raw or "").strip().replace(",", ".")
                if not lat_txt or not lng_txt:
                    return "", ""
                lat_f = float(lat_txt)
                lng_f = float(lng_txt)
                if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
                    return "", ""
                return f"{lat_f:.6f}", f"{lng_f:.6f}"
            except Exception:
                return "", ""

        direccion_txt = str(direccion or "").strip()
        cache_key = self._normalizar_direccion(direccion_txt)
        if not cache_key:
            return "", ""

        cached = _GEOCODE_CACHE.get(cache_key)
        if cached and cached[0] and cached[1]:
            return cached

        queries = [direccion_txt]
        dir_norm = self._normalizar_texto(direccion_txt)
        if "chile" not in dir_norm:
            queries.append(f"{direccion_txt}, Chile")

        for q in queries:
            # 1) Nominatim (OpenStreetMap)
            try:
                url = (
                    "https://nominatim.openstreetmap.org/search"
                    f"?q={quote_plus(q)}&format=jsonv2&limit=1&countrycodes=cl"
                )
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "ATC-Incidencias/1.0",
                        "Accept": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=4.0) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
                if isinstance(payload, list) and payload:
                    first = payload[0] or {}
                    lat, lng = _parse_coords(first.get("lat"), first.get("lon"))
                    if lat and lng:
                        _GEOCODE_CACHE[cache_key] = (lat, lng)
                        return lat, lng
            except Exception:
                pass

            # 2) maps.co
            try:
                url = f"https://geocode.maps.co/search?q={quote_plus(q)}"
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "ATC-Incidencias/1.0",
                        "Accept": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=4.0) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
                if isinstance(payload, list) and payload:
                    first = payload[0] or {}
                    lat, lng = _parse_coords(first.get("lat"), first.get("lon"))
                    if lat and lng:
                        _GEOCODE_CACHE[cache_key] = (lat, lng)
                        return lat, lng
            except Exception:
                pass

            # 3) Photon (komoot)
            try:
                url = f"https://photon.komoot.io/api/?q={quote_plus(q)}&limit=1"
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "ATC-Incidencias/1.0",
                        "Accept": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=4.0) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
                features = payload.get("features") if isinstance(payload, dict) else []
                if isinstance(features, list) and features:
                    geom = (features[0] or {}).get("geometry") or {}
                    coords = geom.get("coordinates") if isinstance(geom, dict) else None
                    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                        lng_raw, lat_raw = coords[0], coords[1]
                        lat, lng = _parse_coords(lat_raw, lng_raw)
                        if lat and lng:
                            _GEOCODE_CACHE[cache_key] = (lat, lng)
                            return lat, lng
            except Exception:
                pass

        return "", ""

    def _coordenadas_aproximadas_por_direccion(self, direccion: str) -> tuple[str, str]:
        dir_norm = self._normalizar_texto(direccion or "")
        if not dir_norm:
            return "", ""

        # Si la direccion trae numero, no usar centro de comuna para evitar
        # coordenadas incorrectas (prefiere exactitud antes que aproximacion).
        if self._extraer_numero_direccion(direccion):
            return "", ""

        for keywords, coords in _COORD_FALLBACK_CL:
            if any(k in dir_norm for k in keywords):
                return coords

        # Fallback nacional: centro de Santiago.
        return "-33.4489", "-70.6693"

    def _persistir_coordenadas_sucursal(self, cliente: str, direccion: str, latitud: str, longitud: str) -> None:
        cli = str(cliente or "").strip()
        dir_txt = str(direccion or "").strip()
        lat = str(latitud or "").strip()
        lng = str(longitud or "").strip()
        if not lat or not lng:
            return

        hubo_cambios = False
        for table_name in ["bbdd_clientes", "catalogo_clientes"]:
            try:
                for schema_name in self._schemas_con_tabla(table_name):
                    cols = self._columnas_tabla(schema_name, table_name)
                    if not cols:
                        continue
                    col_lat = self._pick_col(cols, ["latitud", "lat", "latitude"])
                    col_lng = self._pick_col(cols, ["longitud", "lng", "lon", "longitude"])
                    if not col_lat or not col_lng:
                        continue
                    col_cliente = self._pick_col(cols, ["cliente", "nombre_sucursal", "sucursal", "nombre_cliente"])
                    col_dir = self._pick_col(cols, ["direccion", "direccion_sucursal", "direccion_trabajos", "direccion_cliente"])

                    where_parts: list[str] = []
                    params: dict[str, Any] = {"lat": lat, "lng": lng}
                    if col_cliente and cli:
                        where_parts.append(f'btrim(CAST("{col_cliente}" AS text)) = :cliente')
                        params["cliente"] = cli
                    if col_dir and dir_txt:
                        where_parts.append(f'btrim(CAST("{col_dir}" AS text)) = :direccion')
                        params["direccion"] = dir_txt
                    if not where_parts:
                        continue

                    sql = text(
                        f"""
                        UPDATE "{schema_name}"."{table_name}"
                        SET "{col_lat}" = :lat,
                            "{col_lng}" = :lng
                        WHERE ({' OR '.join(where_parts)})
                          AND (
                            btrim(CAST("{col_lat}" AS text)) = ''
                            OR btrim(CAST("{col_lng}" AS text)) = ''
                          )
                        """
                    )
                    result = self.db.execute(sql, params)
                    if (result.rowcount or 0) > 0:
                        hubo_cambios = True
            except Exception:
                continue

        if hubo_cambios:
            try:
                self.db.commit()
            except Exception:
                self.db.rollback()

    def _support_sync_enabled(self) -> bool:
        # AJUSTE SOPORTE REGISTRO SQL #
        mode = (settings.support_sync_mode or "off").strip().lower()
        return mode in {"db", "api"}

    def _build_support_payload(self, odt: str, data: IncidenciaNueva, fecha: datetime) -> dict[str, Any]:
        fecha_txt = fecha.strftime("%d/%m/%Y %H:%M")
        return {
            "odt": odt,
            "fecha": fecha_txt,
            "fecha_registro": fecha.isoformat(),
            "puesto": (data.puesto or "").strip(),
            "sucursal": (data.cliente or "").strip(),
            "cliente": (data.cliente or "").strip(),
            "problema": (data.tipo_incidencia or "").strip(),
            "tipo_incidencia": (data.tipo_incidencia or "").strip(),
            "derivacion": "Pendiente",
            "observacion": (data.descripcion or "").strip(),
            "descripcion": (data.descripcion or "").strip(),
            "estado": "Pendiente",
            "origen": "incidencias_app",
            "source_file": "incidencias_sync",
        }

    def _crear_outbox_sync(self, odt: str, payload: dict[str, Any]) -> SyncOutbox:
        row = SyncOutbox(
            event_type="incidencia_created",
            entity_key=odt,
            payload_json=json.dumps(payload, ensure_ascii=False),
            status="pending",
            attempts=0,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _sync_to_support_api(self, payload: dict[str, Any]) -> None:
        raw_url = (settings.support_sync_api_url or "").strip()
        if not raw_url:
            raise RuntimeError("SUPPORT_SYNC_API_URL no configurado.")

        def _rstrip_slash(v: str) -> str:
            return v[:-1] if v.endswith("/") else v

        def _build_payload_nueva(src: dict[str, Any]) -> dict[str, Any]:
            return {
                "puesto": (src.get("puesto") or "").strip(),
                "cliente": (src.get("cliente") or src.get("sucursal") or "").strip(),
                "tipoIncidencia": (src.get("tipo_incidencia") or src.get("problema") or "").strip(),
                "descripcion": (src.get("descripcion") or src.get("observacion") or "").strip(),
                "estado": (src.get("estado") or "Pendiente").strip(),
            }

        def _expand_api_candidates(url_value: str) -> list[tuple[str, Any]]:
            src = _rstrip_slash(url_value)
            parsed = urlsplit(src)
            origin = src
            if parsed.scheme and parsed.netloc:
                origin = f"{parsed.scheme}://{parsed.netloc}"

            payload_nueva = _build_payload_nueva(payload)
            candidates: list[tuple[str, Any]] = [
                (src, payload),
                (_rstrip_slash(origin) + "/api/incidencias/sync", payload),
                (_rstrip_slash(origin) + "/api/incidencias/nueva", payload_nueva),
                (_rstrip_slash(origin) + "/api/incidencias", payload_nueva),
                (_rstrip_slash(origin) + "/api/incidencias/multiples", [payload_nueva]),
            ]

            # Deduplicar por URL + body serializado.
            seen: set[tuple[str, str]] = set()
            deduped: list[tuple[str, Any]] = []
            for u, b in candidates:
                key = (_rstrip_slash(u), json.dumps(b, ensure_ascii=False, sort_keys=True))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append((u, b))
            return deduped

        token = (settings.support_sync_api_token or "").strip()
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        timeout = max(1, int(settings.support_sync_timeout_sec or 10))

        errores: list[str] = []
        for candidate_url, candidate_body in _expand_api_candidates(raw_url):
            req_body = json.dumps(candidate_body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url=candidate_url, data=req_body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - endpoint configurable del usuario
                    status_code = getattr(resp, "status", 200)
                    if status_code >= 400:
                        raise RuntimeError(f"Sync API status {status_code}")
                    return
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                errores.append(f"{candidate_url} -> HTTP {exc.code}: {detail}")
                # Reintentamos solo para errores de ruta/mÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©todo/payload no compatible.
                if exc.code in {404, 405, 422}:
                    continue
                raise RuntimeError(f"Sync API HTTPError {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                errores.append(f"{candidate_url} -> URLError: {exc}")
                continue

        raise RuntimeError("Sync API fallÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³ en todos los endpoints candidatos. " + " | ".join(errores))

    def _sync_to_support_db(self, payload: dict[str, Any]) -> None:
        db_url = (settings.support_db_url or "").strip()
        if not db_url:
            raise RuntimeError("SUPPORT_DB_URL no configurado.")

        schema = (settings.support_db_schema or "public").strip()
        # AJUSTE SOPORTE REGISTRO SQL #
        table = (settings.support_db_table or "registro").strip()
        engine = build_engine(db_url, pool_pre_ping=True)

        with engine.begin() as conn:
            cols_rows = conn.execute(
                text(
                    """
                    SELECT
                        column_name,
                        is_nullable,
                        column_default,
                        data_type,
                        udt_name,
                        is_identity,
                        is_generated
                    FROM information_schema.columns
                    WHERE table_schema = :schema_name
                      AND table_name = :table_name
                    """
                ),
                {"schema_name": schema, "table_name": table},
            ).all()

            columns_info: dict[str, dict[str, Any]] = {}
            for r in cols_rows:
                if not r or not r[0]:
                    continue
                col_name = str(r[0]).strip()
                columns_info[col_name] = {
                    "is_nullable": str(r[1] or "").strip().upper() == "YES",
                    "column_default": r[2],
                    "data_type": str(r[3] or "").strip().lower(),
                    "udt_name": str(r[4] or "").strip().lower(),
                    "is_identity": str(r[5] or "").strip().upper() == "YES",
                    "is_generated": str(r[6] or "").strip().upper(),
                }

            cols = set(columns_info.keys())
            if not cols:
                raise RuntimeError(f"No se encontrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³ {schema}.{table} en SQL de soporte.")

            campo_map = {
                "odt": ["odt", "codigo", "codigo_odt", "nro_odt"],
                "fecha": ["fecha", "fecha_registro", "created_at"],
                "puesto": ["puesto", "nro_puesto", "puesto_numero"],
                "sucursal": ["sucursal", "cliente", "nombre_sucursal", "nombre_cliente"],
                "problema": ["problema", "tipo_incidencia", "tipo"],
                "derivacion": ["derivacion", "servicio", "area"],
                # AJUSTE SOPORTE REGISTRO SQL #
                "observacion": ["observacion", "detalle_problema", "descripcion", "detalle"],
                "estado": ["estado", "status", "situacion"],
            }

            insert_cols: list[str] = []
            params: dict[str, Any] = {}
            for key, opciones in campo_map.items():
                col = next((c for c in opciones if c in cols), None)
                if not col:
                    continue
                insert_cols.append(col)
                params[col] = payload.get(key) or ""

            # Campos ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Âºtiles por nombre directo, si existen.
            for direct_col in ["source_file", "origen", "origin", "source"]:
                if direct_col in cols and direct_col not in insert_cols:
                    insert_cols.append(direct_col)
                    params[direct_col] = (
                        payload.get(direct_col)
                        or payload.get("origen")
                        or payload.get("source_file")
                        or "incidencias_sync"
                    )

            # AJUSTE SOPORTE REGISTRO SQL #
            for direct_col in ["tecnicos", "acompanante", "prioridad", "direccion", "observacion_final"]:
                if direct_col in cols and direct_col not in insert_cols:
                    insert_cols.append(direct_col)
                    params[direct_col] = payload.get(direct_col) or ""

            def _coerce_required_value(col_name: str, info: dict[str, Any]) -> Any:
                cname = col_name.lower()
                data_type = info.get("data_type", "")
                udt_name = info.get("udt_name", "")
                now = datetime.utcnow()
                odt_tail = _parse_prefijo_numero(str(payload.get("odt") or "").strip()) or int(now.timestamp())

                if cname in {"source_file", "source", "origin", "origen"}:
                    return payload.get("source_file") or payload.get("origen") or "incidencias_sync"
                if cname in {"source_row", "source_id", "origen_id"}:
                    raw = payload.get("source_row")
                    if raw is not None and str(raw).strip() != "":
                        try:
                            return int(str(raw).strip())
                        except Exception:
                            pass
                    return odt_tail
                if cname in {"estado", "status", "situacion"}:
                    return payload.get("estado") or "Pendiente"
                if cname in {"created_at", "updated_at", "fecha_creacion", "fecha_actualizacion"}:
                    return now
                if cname in {"uuid", "guid"} or "uuid" in udt_name:
                    return str(uuid.uuid4())
                if "bool" in data_type or udt_name == "bool":
                    return False
                if any(x in data_type for x in ["int", "numeric", "decimal", "real", "double"]) or udt_name in {
                    "int2",
                    "int4",
                    "int8",
                    "numeric",
                    "float4",
                    "float8",
                }:
                    return 0
                if "timestamp" in data_type or data_type in {"date", "time"} or udt_name in {
                    "timestamp",
                    "timestamptz",
                    "date",
                    "time",
                }:
                    return now
                if "json" in data_type or udt_name in {"json", "jsonb"}:
                    return "{}"
                return ""

            # Completar columnas NOT NULL sin default para evitar fallos por esquema destino.
            for col_name, info in columns_info.items():
                if col_name in insert_cols:
                    continue
                if info.get("is_nullable", True):
                    continue
                if info.get("column_default") is not None:
                    continue
                if info.get("is_identity", False):
                    continue
                if info.get("is_generated", "") in {"ALWAYS", "BY DEFAULT"}:
                    continue
                insert_cols.append(col_name)
                params[col_name] = _coerce_required_value(col_name, info)

            if not insert_cols:
                raise RuntimeError(f"No hay columnas compatibles para insertar en {schema}.{table}.")

            placeholders = [f":{c}" for c in insert_cols]
            sql_insert = text(
                f'INSERT INTO "{schema}"."{table}" ({", ".join([f"""\"{c}\"""" for c in insert_cols])}) '
                f'VALUES ({", ".join(placeholders)})'
            )
            conn.execute(sql_insert, params)

    def _sync_outbox_row(self, row: SyncOutbox) -> None:
        mode = (settings.support_sync_mode or "off").lower()
        payload = json.loads(row.payload_json or "{}")
        try:
            if mode == "api":
                try:
                    self._sync_to_support_api(payload)
                except Exception as api_exc:
                    # Fallback opcional: si API no existe/estÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡ caÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â­da, intenta inserciÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n directa al SQL de soporte.
                    if (settings.support_db_url or "").strip():
                        try:
                            self._sync_to_support_db(payload)
                        except Exception as db_exc:
                            raise RuntimeError(f"API: {api_exc} | DB fallback: {db_exc}") from db_exc
                    else:
                        raise
            elif mode == "db":
                self._sync_to_support_db(payload)
            else:
                raise RuntimeError(f"Modo de sync no soportado: {mode}")
            row.status = "sent"
            row.sent_at = datetime.utcnow()
            row.last_error = None
        except Exception as exc:
            row.status = "failed"
            row.last_error = str(exc)[:4000]
        finally:
            row.attempts = int(row.attempts or 0) + 1
            row.updated_at = datetime.utcnow()
            self.db.commit()

    def _registrar_sync_soporte_nueva(
        self,
        odt: str,
        data: IncidenciaNueva,
        fecha: datetime,
        descripcion_registro: str | None = None,
    ) -> None:
        # AJUSTE SOPORTE REGISTRO SQL #
        if not self._support_sync_enabled():
            return
        try:
            payload = self._build_support_payload(odt, data, fecha)
            if descripcion_registro is not None:
                payload["observacion"] = descripcion_registro
                payload["descripcion"] = descripcion_registro
            row = self._crear_outbox_sync(odt, payload)
            self._sync_outbox_row(row)
        except Exception:
            self.db.rollback()

    def sync_soporte_pendientes(self, limit: int = 50) -> dict[str, Any]:
        if not self._support_sync_enabled():
            return {"processed": 0, "sent": 0, "failed": 0, "disabled": True}

        q_limit = max(1, min(int(limit or 50), 500))
        rows = self.db.scalars(
            select(SyncOutbox)
            .where(SyncOutbox.event_type == "incidencia_created", SyncOutbox.status.in_(["pending", "failed"]))
            .order_by(SyncOutbox.id.asc())
            .limit(q_limit)
        ).all()
        sent = 0
        failed = 0
        for row in rows:
            prev_status = row.status
            self._sync_outbox_row(row)
            if row.status == "sent":
                sent += 1
            elif row.status == "failed":
                failed += 1
            elif prev_status == "failed":
                failed += 1
        return {"processed": len(rows), "sent": sent, "failed": failed}

    def obtener_estado_sync_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self._support_sync_enabled():
            return []

        q_limit = max(1, min(int(limit or 100), 500))
        rows = self.db.scalars(select(SyncOutbox).order_by(SyncOutbox.id.desc()).limit(q_limit)).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "eventType": r.event_type,
                    "entityKey": r.entity_key,
                    "status": r.status,
                    "attempts": r.attempts,
                    "lastError": r.last_error,
                    "createdAt": _to_ddmmyyyy_hhmm(r.created_at),
                    "updatedAt": _to_ddmmyyyy_hhmm(r.updated_at),
                    "sentAt": _to_ddmmyyyy_hhmm(r.sent_at),
                }
            )
        return out

    def enviar_formulario(self, datos: FormularioRegistro) -> str:
        odt = datos.odt or self._proximo_odt("I")
        registro = Registro(
            odt=odt,
            fecha_registro=datos.fecha or datetime.utcnow(),
            cliente=datos.cliente,
            problema=datos.problema,
            detalle_problema=datos.detalle_problema,
            derivacion=datos.derivacion,
            observacion=datos.observacion,
            tecnicos=datos.tecnicos,
            acompanante=datos.acompanante,
            estado=datos.estado,
            dias_ejecucion=datos.dias_ejecucion,
            foto_1=datos.foto,
            observacion_final=datos.observacion_final,
            fecha_cierre=datos.fecha_cierre,
            direccion=self._direccion_cliente(datos.cliente),
        )
        self.db.add(registro)
        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise _build_db_write_error(exc) from exc
        return "Registro guardado en SQL"

    def _firmar_observacion_registro(self, token: str | None, observacion: str, fecha: datetime) -> str:
        texto = (observacion or "").strip()
        if not texto:
            return ""
        token_limpio = (token or "").strip()
        usuario = self.get_usuario_actual(token_limpio) if token_limpio else "Usuario no identificado"
        if not usuario or usuario == "Desconocido":
            usuario = "Usuario no identificado"
        tz_name = (settings.timezone or "America/Santiago").strip() or "America/Santiago"
        fecha_local = fecha.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
        marca = fecha_local.strftime("%d/%m/%Y %H:%M")
        return f"[{usuario} - {marca}] {texto}"

    def guardar_incidencia_nueva(self, data: IncidenciaNueva) -> str:
        odt = self._proximo_odt("I")
        ahora = datetime.utcnow()
        cliente = (data.cliente or "").strip()
        descripcion = (data.descripcion or "").strip()
        observacion_registro = self._firmar_observacion_registro(data.token, descripcion, ahora)

        reg = Registro(
            odt=odt,
            fecha_registro=ahora,
            puesto=((data.puesto or "").strip() or None),
            cliente=cliente,
            problema=(data.tipo_incidencia or "").strip(),
            detalle_problema=(observacion_registro or None),
            derivacion="Pendiente",
            observacion=(observacion_registro or None),
            estado="Pendiente",
            fecha_derivacion_area=ahora,
            direccion=self._direccion_cliente(cliente),
            tecnicos="",
            acompanante="",
        )
        self.db.add(reg)
        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise _build_db_write_error(exc) from exc
        # AJUSTE SOPORTE REGISTRO SQL #
        self._registrar_sync_soporte_nueva(odt, data, ahora, observacion_registro)
        return odt

    def derivar_odt_a_tecnico(
        self,
        odt: str,
        tecnico: str = "",
        acompanante: str = "",
        derivacion: str = "Servicio T?cnico",
        estado: str = "Pendiente",
    ) -> bool:
        odt_limpia = (odt or "").strip()
        tecnico_limpio = (tecnico or "").strip()
        acompanante_limpio = (acompanante or "").strip()
        derivacion_final = (derivacion or "").strip()

        if not odt_limpia:
            raise ValueError("ODT invalida.")

        row = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row:
            return False

        ahora = datetime.utcnow()
        if not derivacion_final:
            derivacion_final = str(row.derivacion or "").strip() or "Servicio T?cnico"

        if tecnico_limpio:
            estado_final = "En Proceso"
            row.tecnicos = tecnico_limpio
            row.acompanante = acompanante_limpio or None
            row.fecha_derivacion_tecnico = ahora
        else:
            # Permite limpiar asignacion y volver a pendiente.
            estado_final = "Pendiente"
            row.tecnicos = ""
            row.acompanante = None
            row.fecha_derivacion_tecnico = None

        row.derivacion = derivacion_final
        row.estado = estado_final
        if not row.fecha_derivacion_area:
            row.fecha_derivacion_area = ahora
        self.db.commit()
        if tecnico_limpio:
            self._sync_estado_ticket_soporte_silencioso(odt_limpia, TICKET_STATUS_PENDIENTE_SERVICIO)
        else:
            self._sync_estado_ticket_soporte_silencioso(odt_limpia, TICKET_STATUS_PENDIENTE)
        return True

    def editar_incidencia_tabla(
        self,
        token: str,
        odt: str,
        derivacion: str | None = None,
        observacion: str | None = None,
        observacion_servicio: str | None = None,
        observacion_final: str | None = None,
        repetida_odt_ref: str | None = None,
    ) -> dict[str, Any]:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            raise ValueError("ODT invalida.")
        if not self.usuario_logueado_por_token(token):
            raise ValueError("Sesion expirada. Inicia sesion nuevamente.")

        usuario = self.get_usuario_actual(token)
        if not usuario or usuario == "Desconocido":
            raise ValueError("No se pudo identificar al usuario de la sesion.")

        derivacion_in = (derivacion or "").strip()
        observacion_in = (observacion or "").strip()
        observacion_servicio_in = (observacion_servicio or "").strip()
        observacion_final_in = (observacion_final or "").strip()
        repetida_ref_in = (repetida_odt_ref or "").strip()
        if not derivacion_in and not observacion_in and not observacion_servicio_in and not observacion_final_in:
            raise ValueError("Debes enviar derivacion u observacion para editar.")

        opciones_derivacion = [
            "T?cnico Externo",
            "Técnico Externo",
            "Cliente",
            "Soporte T?cnico",
            "Soporte Técnico",
            "Servicio Técnico",
            "Coordinacion",
            "Coordinación",
            "Finalizado por Soporte",
            "Finalizado Sin VT",
            "Repetida",
        ]
        if derivacion_in:
            mapa_deriv = {self._normalizar_texto(v): v for v in opciones_derivacion}
            key = self._normalizar_texto(derivacion_in)
            if key not in mapa_deriv:
                raise ValueError("Derivacion no permitida.")
            derivacion_in = mapa_deriv[key]

        tz_name = (settings.timezone or "America/Santiago").strip() or "America/Santiago"
        ahora_local = datetime.now(ZoneInfo(tz_name))
        ahora_utc = datetime.utcnow()
        marca = ahora_local.strftime("%d/%m/%Y %H:%M")

        row = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row:
            return {"ok": False}

        observacion_soporte_final = ""
        observacion_servicio_final = ""
        estado_ticket_soporte = ""
        nota_ticket_soporte = ""
        correo_derivacion_result: dict[str, Any] | None = None

        if derivacion_in:
            row.derivacion = derivacion_in
            if derivacion_in == "Finalizado por Soporte":
                if not observacion_final_in:
                    raise ValueError("Debes indicar que se hizo para finalizar por soporte.")
                row.estado = "Terminado"
                row.observacion_final = observacion_final_in
                row.fecha_cierre = ahora_utc
                if row.fecha_registro:
                    row.dias_ejecucion = (row.fecha_cierre.date() - row.fecha_registro.date()).days
                estado_ticket_soporte = TICKET_STATUS_RESUELTO_SERVICIO
                nota_ticket_soporte = self._build_nota_cierre_ticket_soporte(
                    odt=odt_limpia,
                    estado_ticket=estado_ticket_soporte,
                    derivacion=derivacion_in,
                    observacion_final=observacion_final_in,
                )
            elif derivacion_in == "Finalizado Sin VT":
                row.estado = "Terminado"
                row.fecha_cierre = ahora_utc
                if row.fecha_registro:
                    row.dias_ejecucion = (row.fecha_cierre.date() - row.fecha_registro.date()).days
                estado_ticket_soporte = TICKET_STATUS_RESUELTO_SERVICIO
                nota_ticket_soporte = self._build_nota_cierre_ticket_soporte(
                    odt=odt_limpia,
                    estado_ticket=estado_ticket_soporte,
                    derivacion=derivacion_in,
                    observacion_final=observacion_final_in,
                )
            elif derivacion_in == "Repetida":
                if not repetida_ref_in:
                    match_ref = re.search(r"\b([A-Za-z]\d+)\b", observacion_servicio_in or "")
                    repetida_ref_in = str(match_ref.group(1) if match_ref else "").strip()
                if not repetida_ref_in:
                    raise ValueError("Debes indicar la ODT con la que se repite.")
                if repetida_ref_in == odt_limpia:
                    raise ValueError("La ODT repetida no puede ser la misma ODT actual.")

                row_ref = self.db.scalar(select(Registro).where(Registro.odt == repetida_ref_in))
                if not row_ref:
                    raise ValueError(f"No se encontro la ODT de referencia {repetida_ref_in}.")

                estado_ref_norm = self._normalizar_texto(getattr(row_ref, "estado", "") or "")
                if ("pend" not in estado_ref_norm) and ("proceso" not in estado_ref_norm):
                    raise ValueError("La ODT de referencia debe estar Pendiente o En Proceso.")

                sucursal_actual = self._normalizar_texto(getattr(row, "cliente", "") or "")
                sucursal_ref = self._normalizar_texto(getattr(row_ref, "cliente", "") or "")
                if sucursal_actual != sucursal_ref:
                    raise ValueError("La ODT de referencia debe ser de la misma sucursal.")

                problema_actual = self._normalizar_texto(getattr(row, "problema", "") or "")
                problema_ref = self._normalizar_texto(getattr(row_ref, "problema", "") or "")
                if problema_actual != problema_ref:
                    raise ValueError("Solo puedes marcar como repetida con una ODT del mismo problema.")

                observacion_servicio_in = f"ODT con la que se repite {repetida_ref_in}"
                row.estado = "Repetida"
                row.fecha_cierre = ahora_utc
                if row.fecha_registro:
                    row.dias_ejecucion = (row.fecha_cierre.date() - row.fecha_registro.date()).days
                estado_ticket_soporte = TICKET_STATUS_RESUELTO_SERVICIO
                nota_ticket_soporte = self._build_nota_cierre_ticket_soporte(
                    odt=odt_limpia,
                    estado_ticket=estado_ticket_soporte,
                    derivacion=derivacion_in,
                    observacion_final=observacion_servicio_in,
                )
            else:
                row.estado = "Pendiente"
                row.fecha_cierre = None
                deriv_norm = self._normalizar_texto(derivacion_in)
                if deriv_norm in {"cliente", "coordinacion"}:
                    estado_ticket_soporte = TICKET_STATUS_PENDIENTE_CLIENTE
                elif "servicio tecnico" in deriv_norm or "soporte tecnico" in deriv_norm:
                    estado_ticket_soporte = TICKET_STATUS_PENDIENTE_SERVICIO
                else:
                    estado_ticket_soporte = TICKET_STATUS_PENDIENTE

        if observacion_final_in and derivacion_in != "Finalizado por Soporte":
            row.observacion_final = observacion_final_in

        if observacion_in:
            base = str(getattr(row, "observacion_soporte", "") or "").strip()
            if not base:
                # Compatibilidad con datos guardados antes de separar columnas.
                base = str(getattr(row, "observacion_servicio", "") or "").strip()
            nuevo = observacion_in.strip()
            if base:
                if nuevo.startswith(base):
                    nuevo = nuevo[len(base):].strip()
                ultima_linea = base.splitlines()[-1].strip() if base.splitlines() else ""
                if nuevo == ultima_linea:
                    nuevo = ""
            if nuevo:
                linea = f"[{usuario} - {marca}] {nuevo}"
                row.observacion_soporte = f"{base}\n{linea}".strip() if base else linea
                observacion_soporte_final = row.observacion_soporte

        if observacion_servicio_in:
            base_servicio = str(getattr(row, "observacion_servicio", "") or "").strip()
            nuevo_servicio = observacion_servicio_in.strip()
            if base_servicio:
                if nuevo_servicio.startswith(base_servicio):
                    nuevo_servicio = nuevo_servicio[len(base_servicio):].strip()
                ultima_linea_serv = base_servicio.splitlines()[-1].strip() if base_servicio.splitlines() else ""
                if nuevo_servicio == ultima_linea_serv:
                    nuevo_servicio = ""
            if nuevo_servicio:
                linea_servicio = f"[{usuario} - {marca}] {nuevo_servicio}"
                row.observacion_servicio = (
                    f"{base_servicio}\n{linea_servicio}".strip() if base_servicio else linea_servicio
                )
                observacion_servicio_final = row.observacion_servicio

        self.db.commit()
        if estado_ticket_soporte:
            self._sync_estado_ticket_soporte_silencioso(
                odt_limpia,
                estado_ticket_soporte,
                nota_interna=nota_ticket_soporte or None,
            )
        if derivacion_in:
            deriv_norm_correo = self._normalizar_texto(derivacion_in)
            if deriv_norm_correo in {"cliente", "coordinacion"} or "servicio tecnico" in deriv_norm_correo:
                correo_derivacion_result = self._enviar_correo_derivacion_automatico_silencioso(
                    row=row,
                    derivacion=derivacion_in,
                    usuario=usuario,
                )
        return {
            "ok": True,
            "odt": odt_limpia,
            "derivacion": derivacion_in or None,
            "observacion": observacion_soporte_final or None,
            "observacion_soporte": observacion_soporte_final or None,
            "observacion_servicio": (
                observacion_servicio_final
                or str(getattr(row, "observacion_servicio", "") or "").strip()
                or None
            ),
            "observacion_final": str(getattr(row, "observacion_final", "") or "").strip() or None,
            "correo_derivacion": correo_derivacion_result,
        }

    def enviar_multiples_incidencias(self, incidencias: list[IncidenciaNueva]) -> list[str]:
        odts_creadas: list[str] = []
        for inc in incidencias:
            if not inc.cliente or not inc.tipo_incidencia:
                continue
            odt = self.guardar_incidencia_nueva(inc)
            odts_creadas.append(odt)
        if not odts_creadas:
            raise ValueError("No se encontrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³ ninguna incidencia vÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡lida para registrar.")
        return odts_creadas

    def obtener_registros(self, tecnico: str | None = None) -> list[list[Any]]:
        tecnico_filtrado = (tecnico or "").strip().lower()
        stmt = select(Registro).order_by(Registro.id.desc())
        registros = self._run_registro_query(
            lambda: self.db.scalars(stmt).all(),
            "cargar los registros de tecnicos",
        )
        salida: list[list[Any]] = []
        for r in registros:
            tecnico_match = not tecnico_filtrado or (
                tecnico_filtrado in (r.tecnicos or "").lower()
                or tecnico_filtrado in (r.acompanante or "").lower()
            )
            if not tecnico_match:
                continue
            salida.append(
                [
                    r.odt,
                    _to_ddmmyyyy_hhmm(r.fecha_derivacion_area),
                    r.cliente,
                    r.problema,
                    r.observacion,
                    r.estado,
                    r.prioridad,
                ]
            )
        return salida

    def obtener_registros_desde_administracion(self, tecnico: str | None = None) -> list[list[Any]]:
        filtro = (tecnico or "").strip().lower()
        rows = self._run_registro_query(
            lambda: self.db.scalars(select(Registro).order_by(Registro.id.desc())).all(),
            "cargar la tabla principal de incidencias",
        )
        resultado: list[list[Any]] = []
        for row in rows:
            tecnico_principal = (row.tecnicos or "").lower()
            tecnico_acom = (row.acompanante or "").lower()
            if filtro and filtro not in tecnico_principal and filtro not in tecnico_acom:
                continue

            fecha_ref = row.fecha_derivacion_area or row.fecha_registro
            detalle = (row.observacion_final or row.observacion_pendiente or row.observacion or "")
            resultado.append(
                [
                    row.odt,
                    _to_ddmmyyyy_hhmm(fecha_ref),
                    row.cliente,
                    row.problema,
                    detalle,
                    row.estado,
                ]
            )
        return resultado

    def obtener_datos_cliente(self, nombre_cliente: str) -> dict[str, str]:
        stmt = select(ClienteBBDD).where(ClienteBBDD.cliente == nombre_cliente)
        row = self.db.scalar(stmt)
        if not row:
            return {}
        return {
            "derivacion": "",
            "servicio": "",
            "soporte": "",
            "problema": "",
        }

    def obtener_datos_sucursal(self, cliente: str) -> dict[str, str]:
        stmt = select(ClienteBBDD).where(ClienteBBDD.cliente == cliente)
        row = self.db.scalar(stmt)
        if not row:
            return {}
        return {
            "direccion": row.direccion or "",
            "contacto": row.contacto or "",
            "correo": row.correo or "",
        }

    def obtener_listas_bbdd(self) -> dict[str, list[str]]:
        rows = self.db.scalars(select(ClienteBBDD).order_by(ClienteBBDD.cliente.asc())).all()
        sucursales = sorted(
            {
                str(nombre).strip()
                for nombre in [*(r.cliente for r in rows if r.cliente), *SUCURSALES_EXTRA_MANTENCION]
                if str(nombre or "").strip()
            },
            key=self._normalizar_texto,
        )
        direccion = sorted({r.direccion for r in rows if r.direccion})
        contactos = sorted({r.contacto for r in rows if r.contacto})
        correos = sorted({r.correo for r in rows if r.correo})
        tecnicos_helpdesk = self._obtener_tecnicos_helpdesk(solo_activos=True)
        tecnicos_registro = self._run_registro_query(
            lambda: sorted(
                {
                    str(nombre).strip()
                    for row in self.db.scalars(select(Registro).order_by(Registro.id.desc())).all()
                    for nombre in [row.tecnicos, row.acompanante]
                    if str(nombre or "").strip() and str(nombre or "").strip() not in {"-", "None", "null"}
                }
            ),
            "cargar la lista de tecnicos",
        )
        tecnicos = sorted(
            {
                str(nombre).strip()
                for nombre in [*(tecnicos_helpdesk or []), *(tecnicos_registro or [])]
                if str(nombre or "").strip()
            }
        )
        derivaciones: list[str] = []
        soportes: list[str] = []
        problemas = [
            "Desconexion",
            "Problema de Parlante",
            "Problema de Alarma",
            "Hora y/o Fecha Cambiada",
            "Problema de Visual",
        ]
        return {
            "sucursales": sucursales,
            "direccion": direccion,
            "contactos": contactos,
            "correos": correos,
            "tecnicos": tecnicos,
            "derivaciones": derivaciones,
            "soportes": soportes,
            "problemas": problemas,
        }

    def obtener_listas_incidencias(self) -> dict[str, list[str]]:
        clientes = self.obtener_catalogo_clientes()
        problemas = [
            "DesconexiÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n",
            "Problema de Parlante",
            "Problema de Alarma",
            "Hora y/o Fecha Cambiada",
            "Problema de Visual",
        ]
        return {"clientes": clientes, "problemas": problemas}


    def obtener_incidencias_por_puesto(self, tecnico: str | None = None) -> list[list[Any]]:
        def _fmt_fecha(v: Any) -> str:
            if isinstance(v, datetime):
                return _to_ddmmyyyy_hhmm(v)
            if v is None:
                return ""
            s = str(v).strip()
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                return _to_ddmmyyyy_hhmm(dt)
            except Exception:
                return s

        rows = self._run_registro_query(
            lambda: self.db.scalars(select(Registro).order_by(Registro.id.asc())).all(),
            "cargar las incidencias por puesto",
        )
        out: list[list[Any]] = []
        for r in rows:
            sucursal = str(r.cliente or "").strip()
            direccion = str(r.direccion or "").strip() or self._direccion_cliente(sucursal)
            # Para tabla.html la observacion visible debe salir de "observacion"
            # y no de "detalle_problema".
            detalle = str(r.observacion or "").strip()
            obs_pend = str(getattr(r, "observacion_pendiente", "") or "").strip()
            obs_soporte = str(getattr(r, "observacion_soporte", "") or "").strip()
            obs_servicio = str(getattr(r, "observacion_servicio", "") or "").strip()
            estado = str(r.estado or "").strip() or "Pendiente"
            out.append([
                r.odt,
                _fmt_fecha(r.fecha_registro),
                r.puesto or "",
                sucursal,
                r.problema,
                r.derivacion,
                detalle,
                r.tecnicos or "",
                estado,
                r.id,
                r.acompanante or "",
                _fmt_fecha(r.fecha_cierre),
                str(r.prioridad or ""),
                direccion,
                obs_pend,
                obs_soporte,
                obs_servicio,
                str(getattr(r, "observacion_final", "") or "").strip(),
            ])
        return self._filtrar_incidencias_para_tecnico(out, tecnico)

    def obtener_incidencias_derivadas_cliente(self) -> list[list[Any]]:
        rows = self.obtener_incidencias_por_puesto()
        derivaciones_coord = {"cliente", "coordinacion"}
        return [
            row
            for row in rows
            if len(row) > 5 and self._normalizar_texto(row[5]) in derivaciones_coord
        ]

    def finalizar_odt_coordinacion(self, odt: str, observacion_final: str = "") -> dict[str, Any]:
        odt_limpia = str(odt or "").strip()
        if not odt_limpia:
            raise ValueError("ODT invalida.")
        row = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row:
            return {"ok": False}
        ahora = datetime.utcnow()
        obs = str(observacion_final or "").strip()
        row.estado = "Terminado"
        row.fecha_cierre = ahora
        if obs:
            row.observacion_final = obs
        if row.fecha_registro:
            row.dias_ejecucion = (row.fecha_cierre.date() - row.fecha_registro.date()).days
        self.db.commit()
        self._sync_estado_ticket_soporte_silencioso(
            odt_limpia,
            TICKET_STATUS_RESUELTO_CLIENTE,
            nota_interna=self._build_nota_cierre_ticket_soporte(
                odt=odt_limpia,
                estado_ticket=TICKET_STATUS_RESUELTO_CLIENTE,
                derivacion=str(row.derivacion or "Cliente"),
                observacion_final=row.observacion_final or "",
            ),
        )
        return {
            "ok": True,
            "odt": odt_limpia,
            "estado": row.estado,
            "fecha_cierre": _to_ddmmyyyy_hhmm(row.fecha_cierre),
            "observacion_final": row.observacion_final or "",
        }

    def actualizar_observacion_final_coordinacion(self, odt: str, observacion_final: str = "") -> dict[str, Any]:
        odt_limpia = str(odt or "").strip()
        if not odt_limpia:
            raise ValueError("ODT invalida.")
        row = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row:
            return {"ok": False}
        row.observacion_final = str(observacion_final or "").strip()
        self.db.commit()
        return {"ok": True, "odt": odt_limpia, "observacion_final": row.observacion_final or ""}

    def cerrar_incidencia(self, odt: str, fecha_cierre: datetime) -> bool:
        odt_limpia = (odt or "").strip()
        row = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row:
            return False
        row.derivacion = "Finalizado Por Encargados"
        row.estado = "Terminado"
        row.fecha_cierre = fecha_cierre
        if row.fecha_registro and row.fecha_cierre:
            row.dias_ejecucion = (row.fecha_cierre.date() - row.fecha_registro.date()).days
        self.db.commit()
        self._sync_estado_ticket_soporte_silencioso(
            odt_limpia,
            TICKET_STATUS_RESUELTO_SERVICIO,
            nota_interna=self._build_nota_cierre_ticket_soporte(
                odt=odt_limpia,
                estado_ticket=TICKET_STATUS_RESUELTO_SERVICIO,
                derivacion=row.derivacion or "Finalizado Por Encargados",
                observacion_final=row.observacion_final or "",
            ),
        )
        return True

    def registrar_finalizacion_rapida(self, odt: str, observacion: str) -> str:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            raise ValueError("ODT invalida")

        es_venta = odt_limpia.upper().startswith(("V", "S"))
    def registrar_finalizacion_rapida(self, odt: str, observacion: str) -> str:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            raise ValueError("ODT invalida")

        ahora = datetime.utcnow()
        row = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row:
            raise ValueError(f"No se encontro la ODT {odt_limpia}")

        row.estado = "Terminado"
        row.derivacion = "Servicio T?cnico"
        row.observacion_final = observacion
        row.porcentaje_avance = "100%"
        row.fecha_cierre = ahora
        row.prioridad = None
        if row.fecha_registro:
            row.dias_ejecucion = (row.fecha_cierre.date() - row.fecha_registro.date()).days
        self.db.commit()
        self._sync_estado_ticket_soporte_silencioso(
            odt_limpia,
            TICKET_STATUS_RESUELTO_SERVICIO,
            nota_interna=self._build_nota_cierre_ticket_soporte(
                odt=odt_limpia,
                estado_ticket=TICKET_STATUS_RESUELTO_SERVICIO,
                derivacion=row.derivacion or "Servicio Tecnico",
                observacion_final=row.observacion_final or observacion or "",
            ),
        )
        return "OK"
    def _normalizar_texto(self, valor: Any) -> str:
        txt = str(valor or "").strip().lower()
        txt = unicodedata.normalize("NFD", txt)
        return "".join(c for c in txt if unicodedata.category(c) != "Mn")
    def _filtrar_incidencias_para_tecnico(
        self,
        filas: list[list[Any]],
        tecnico: str | None = None,
    ) -> list[list[Any]]:
        tecnico_norm = self._normalizar_texto(tecnico or "")
        if not tecnico_norm:
            return filas

        filtradas: list[list[Any]] = []
        for fila in filas:
            derivacion = self._normalizar_texto(fila[5] if len(fila) > 5 else "")
            estado = self._normalizar_texto(fila[8] if len(fila) > 8 else "")
            es_terminada = (
                "termin" in estado
                or "final" in estado
                or "finalizado" in derivacion
                or "terminado" in derivacion
                or "repetida" in derivacion
            )
            # Mantener foco en Servicio Tecnico, pero no ocultar ODT ya finalizadas
            # que siguen perteneciendo al tecnico logueado.
            if "servicio tecnico" not in derivacion and not es_terminada:
                continue

            tecnico_txt = self._normalizar_texto(fila[7] if len(fila) > 7 else "")
            acomp_txt = self._normalizar_texto(fila[10] if len(fila) > 10 else "")
            obs_txt = self._normalizar_texto(fila[6] if len(fila) > 6 else "")
            asignados = f"{tecnico_txt} {acomp_txt} {obs_txt}".strip()
            if tecnico_norm in asignados:
                filtradas.append(fila)
        return filtradas

    def _buscar_cliente_por_odt(self, odt: str) -> str:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            return ""

        # Fuente de verdad: ODT activa en tablas locales de la app.
        # Esto evita tomar una sucursal incorrecta cuando en tablas externas
        # existen ODT antiguas/repetidas con el mismo código.
        row_reg = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if row_reg and row_reg.cliente:
            return row_reg.cliente

        row_venta = self.db.scalar(select(OdtVenta).where(OdtVenta.odt == odt_limpia))
        if row_venta and row_venta.cliente:
            return row_venta.cliente

        # Fallback: tabla externa de incidencias, solo si no existe en locales.
        try:
            for schema_name in self._schemas_con_tabla("incidencias"):
                cols = self._columnas_tabla(schema_name, "incidencias")
                if not cols:
                    continue
                col_odt = self._pick_col(cols, ["odt", "codigo_odt", "codigo", "nro_odt"])
                col_cliente = self._pick_col(cols, ["cliente", "nombre_sucursal", "sucursal", "nombre_cliente"])
                if not col_odt or not col_cliente:
                    continue
                sql = text(
                    f"""
                    SELECT "{col_cliente}"
                    FROM "{schema_name}"."incidencias"
                    WHERE btrim(CAST("{col_odt}" AS text)) = :odt
                    LIMIT 1
                    """
                )
                value = self.db.execute(sql, {"odt": odt_limpia}).scalar()
                if value and str(value).strip():
                    return str(value).strip()
        except Exception:
            pass

        return ""

    def obtener_datos_sucursal_con_coordenadas(self, odt: str) -> dict[str, str]:
        cliente = self._buscar_cliente_por_odt(odt)
        salida = {
            "cliente": cliente or "",
            "direccion": "",
            "contacto": "",
            "telefono": "",
            "correo": "",
            "latitud": "",
            "longitud": "",
            "layout": "",
            "observacion": "",
        }
        if not cliente:
            return salida

        row_reg = self.db.scalar(select(Registro).where(Registro.odt == (odt or "").strip()))
        if row_reg and row_reg.direccion:
            salida["direccion"] = str(row_reg.direccion or "").strip()

        row = self.db.scalar(select(ClienteBBDD).where(ClienteBBDD.cliente == cliente))
        if row:
            salida["direccion"] = salida["direccion"] or (row.direccion or "")
            salida["contacto"] = row.contacto or ""
            salida["correo"] = row.correo or ""

        for table_name in ["bbdd_clientes", "catalogo_clientes"]:
            try:
                for schema_name in self._schemas_con_tabla(table_name):
                    cols = self._columnas_tabla(schema_name, table_name)
                    if not cols:
                        continue
                    col_cliente = self._pick_col(cols, ["cliente", "nombre_sucursal", "sucursal", "nombre_cliente"])
                    if not col_cliente:
                        continue
                    col_lat = self._pick_col(cols, ["latitud", "lat", "latitude"])
                    col_lng = self._pick_col(cols, ["longitud", "lng", "lon", "longitude"])
                    col_layout = self._pick_col(cols, ["layout", "plano", "plano_url", "url_layout"])
                    col_obs = self._pick_col(cols, ["observacion", "observaciones", "nota"])
                    col_contacto = self._pick_col(cols, ["contacto", "nombre_contacto", "encargado"])
                    col_tel = self._pick_col(cols, ["telefono", "telefono_contacto", "celular", "fono"])
                    col_correo = self._pick_col(cols, ["correo", "email", "mail"])
                    col_dir = self._pick_col(cols, ["direccion", "direccion_sucursal"])

                    select_cols = []
                    if col_dir:
                        select_cols.append(f'COALESCE(CAST("{col_dir}" AS text), \'\') AS direccion')
                    if col_contacto:
                        select_cols.append(f'COALESCE(CAST("{col_contacto}" AS text), \'\') AS contacto')
                    if col_tel:
                        select_cols.append(f'COALESCE(CAST("{col_tel}" AS text), \'\') AS telefono')
                    if col_correo:
                        select_cols.append(f'COALESCE(CAST("{col_correo}" AS text), \'\') AS correo')
                    if col_lat:
                        select_cols.append(f'COALESCE(CAST("{col_lat}" AS text), \'\') AS latitud')
                    if col_lng:
                        select_cols.append(f'COALESCE(CAST("{col_lng}" AS text), \'\') AS longitud')
                    if col_layout:
                        select_cols.append(f'COALESCE(CAST("{col_layout}" AS text), \'\') AS layout')
                    if col_obs:
                        select_cols.append(f'COALESCE(CAST("{col_obs}" AS text), \'\') AS observacion')
                    if not select_cols:
                        continue

                    sql = text(
                        f"""
                        SELECT {", ".join(select_cols)}
                        FROM "{schema_name}"."{table_name}"
                        WHERE btrim(CAST("{col_cliente}" AS text)) = :cliente
                        LIMIT 1
                        """
                    )
                    row_sql = self.db.execute(sql, {"cliente": cliente}).mappings().first()
                    if not row_sql:
                        continue

                    for key in ["direccion", "contacto", "telefono", "correo", "latitud", "longitud", "layout", "observacion"]:
                        val = str(row_sql.get(key) or "").strip()
                        if val and not salida.get(key):
                            salida[key] = val
            except Exception:
                continue

        contacto_pref = self._contacto_preferente_sucursal(cliente)
        for key in ["contacto", "telefono", "correo"]:
            if not salida.get(key):
                salida[key] = str(contacto_pref.get(key) or "").strip()

        if not salida["direccion"] and cliente:
            salida["direccion"] = self._direccion_cliente(cliente)

        if salida["direccion"] and (not salida["latitud"] or not salida["longitud"]):
            lat_bd, lng_bd = self._coordenadas_por_direccion_bd(salida["direccion"])
            if lat_bd and lng_bd:
                salida["latitud"] = lat_bd
                salida["longitud"] = lng_bd

        if salida["direccion"] and (not salida["latitud"] or not salida["longitud"]):
            lat_geo, lng_geo = self._geocodificar_direccion(salida["direccion"])
            if lat_geo and lng_geo:
                salida["latitud"] = lat_geo
                salida["longitud"] = lng_geo
                self._persistir_coordenadas_sucursal(
                    cliente=cliente,
                    direccion=salida["direccion"],
                    latitud=lat_geo,
                    longitud=lng_geo,
                )

        if salida["direccion"] and (not salida["latitud"] or not salida["longitud"]):
            lat_apx, lng_apx = self._coordenadas_aproximadas_por_direccion(salida["direccion"])
            if lat_apx and lng_apx:
                salida["latitud"] = lat_apx
                salida["longitud"] = lng_apx

        return salida

    def obtener_ultimas_incidencias_sucursal(self, nombre_sucursal: str) -> list[dict[str, str]]:
        sucursal = (nombre_sucursal or "").strip()
        if not sucursal:
            return []
        objetivo = self._normalizar_texto(sucursal)
        incidencias: list[dict[str, str]] = []

        def _fmt_fecha_texto(valor: Any) -> str:
            if isinstance(valor, datetime):
                return _to_ddmmyyyy_hhmm(valor)
            if valor is None:
                return ""
            raw = str(valor).strip()
            if not raw:
                return ""
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return _to_ddmmyyyy_hhmm(dt)
            except Exception:
                return raw

        try:
            for schema_name in self._schemas_con_tabla("incidencias"):
                cols = self._columnas_tabla(schema_name, "incidencias")
                if not cols:
                    continue
                col_cliente = self._pick_col(cols, ["cliente", "nombre_sucursal", "sucursal", "nombre_cliente"])
                col_fecha = self._pick_col(cols, ["fecha", "fecha_registro", "created_at", "createdat"])
                col_prob = self._pick_col(cols, ["tipo_incidencia", "problema", "tipo", "servicio", "incidencia"])
                col_deriv = self._pick_col(cols, ["derivacion", "servicio", "area"])
                col_obs = self._pick_col(cols, ["observacion_final", "descripcion", "detalle", "observacion", "detalle_problema"])
                if not col_cliente or not col_obs:
                    continue

                select_cols = [
                    f'COALESCE(CAST("{col_cliente}" AS text), \'\') AS cliente',
                    f'"{col_fecha}" AS fecha' if col_fecha else "NULL AS fecha",
                    f'COALESCE(CAST("{col_prob}" AS text), \'\') AS problema' if col_prob else "'' AS problema",
                    f'COALESCE(CAST("{col_deriv}" AS text), \'\') AS derivacion' if col_deriv else "'' AS derivacion",
                    f'COALESCE(CAST("{col_obs}" AS text), \'\') AS texto',
                ]

                sql = text(
                    f"""
                    SELECT {", ".join(select_cols)}
                    FROM "{schema_name}"."incidencias"
                    WHERE "{col_cliente}" IS NOT NULL
                      AND btrim(CAST("{col_cliente}" AS text)) <> ''
                    """
                )
                for row in self.db.execute(sql).mappings().all():
                    cli = str(row.get("cliente") or "").strip()
                    if self._normalizar_texto(cli) != objetivo:
                        continue
                    deriv = self._normalizar_texto(row.get("derivacion"))
                    if deriv and "servicio tecnico" not in deriv and "servicio tÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©cnico" not in deriv:
                        continue
                    texto = str(row.get("texto") or "").strip()
                    if not texto:
                        continue
                    incidencias.append(
                        {
                            "fecha": _fmt_fecha_texto(row.get("fecha")),
                            "problema": str(row.get("problema") or "").strip(),
                            "texto": texto,
                        }
                    )
        except Exception:
            pass

        if not incidencias:
            rows = self.db.scalars(select(Registro).where(Registro.cliente == sucursal).order_by(Registro.fecha_registro.asc())).all()
            for r in rows:
                deriv = self._normalizar_texto(r.derivacion)
                if deriv and "servicio tecnico" not in deriv and "servicio tÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©cnico" not in deriv:
                    continue
                texto = (r.observacion_final or r.observacion or "").strip()
                if not texto:
                    continue
                incidencias.append(
                    {
                        "fecha": _to_ddmmyyyy_hhmm(r.fecha_registro),
                        "problema": r.problema or "",
                        "texto": texto,
                    }
                )

        def _sort_key(item: dict[str, str]) -> tuple[int, str]:
            fecha = item.get("fecha") or ""
            m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?$", fecha)
            if not m:
                return (0, fecha)
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh = int(m.group(4) or 0)
            mm = int(m.group(5) or 0)
            return (int(datetime(y, mo, d, hh, mm).timestamp()), fecha)

        incidencias.sort(key=_sort_key)
        return incidencias

    @staticmethod
    def _parse_image_list(value: object) -> list[str]:
        parsed_images: list[str] = []
        if isinstance(value, list):
            parsed_images = [str(v or "").strip() for v in value]
        elif isinstance(value, str):
            raw = value.strip()
            if raw:
                try:
                    decoded = json.loads(raw)
                    if isinstance(decoded, list):
                        parsed_images = [str(v or "").strip() for v in decoded]
                    else:
                        parsed_images = [raw]
                except Exception:
                    parsed_images = [raw]
        else:
            parsed_images = [str(value or "").strip()]

        unique_images: list[str] = []
        for image_url in parsed_images:
            clean = str(image_url or "").strip()
            if clean and clean not in unique_images:
                unique_images.append(clean)
        return unique_images

    def _upsert_unified_images(
        self,
        odt: str,
        sucursal: str,
        usuario: str,
        imagenes: list[str],
    ) -> None:
        row_imgs = self.db.scalar(select(IncidenciaImagenTabla).where(IncidenciaImagenTabla.odt == odt))
        payload = json.dumps(imagenes[:3], ensure_ascii=False)
        if row_imgs:
            row_imgs.sucursal = sucursal or row_imgs.sucursal or None
            row_imgs.imagenes = payload
            row_imgs.created_by = usuario
            row_imgs.updated_at = datetime.utcnow()
        else:
            self.db.add(
                IncidenciaImagenTabla(
                    odt=odt,
                    sucursal=sucursal or None,
                    imagenes=payload,
                    created_by=usuario,
                )
            )

    def _reset_unified_images_if_odt_reused(self, odt: str, sucursal: str) -> bool:
        odt_limpia = str(odt or "").strip()
        sucursal_limpia = str(sucursal or "").strip()
        if not odt_limpia:
            return False

        row_imgs = self.db.scalar(select(IncidenciaImagenTabla).where(IncidenciaImagenTabla.odt == odt_limpia))
        if not row_imgs:
            return False

        sucursal_actual = str(row_imgs.sucursal or "").strip()
        if self._normalizar_sucursal_key(sucursal_actual) == self._normalizar_sucursal_key(sucursal_limpia):
            return False

        row_imgs.sucursal = sucursal_limpia or None
        row_imgs.imagenes = "[]"
        row_imgs.created_by = "reset_odt_reutilizada"
        row_imgs.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def obtener_imagenes_tabla(self, odt: str) -> list[str]:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            return []
        row = self.db.scalar(select(IncidenciaImagenTabla).where(IncidenciaImagenTabla.odt == odt_limpia))
        unified_images = self._parse_image_list(row.imagenes if row else "[]")
        drive_images = list_support_images_for_odt(
            odt=odt_limpia,
            root_folder_id=str(settings.google_drive_support_folder_id or "").strip(),
        )

        merged: list[str] = []
        for img in [*drive_images, *unified_images]:
            url = str(img or "").strip()
            if not url or url in merged:
                continue
            merged.append(url)
            if len(merged) >= 3:
                break

        if merged and merged != unified_images[:3]:
            usuario_sync = "sync_unificado"
            sucursal_sync = str(getattr(row, "sucursal", "") or "").strip()
            if not sucursal_sync:
                row_odt = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
                sucursal_sync = str(getattr(row_odt, "cliente", "") or "").strip()
            self._upsert_unified_images(odt_limpia, sucursal_sync, usuario_sync, merged)
            self.db.commit()

        return merged[:3]

    def subir_imagenes_tabla(
        self,
        odt: str,
        image_payloads: list[dict[str, object]],
        token: str = "",
    ) -> dict[str, Any]:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            raise ValueError("ODT es obligatoria.")

        row_odt = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row_odt:
            raise ValueError(f"ODT {odt_limpia} no encontrada.")

        incoming_images: list[dict[str, object]] = []
        for payload in image_payloads or []:
            content = payload.get("bytes")
            mime_type = str(payload.get("mime_type") or "").strip().lower()
            if not isinstance(content, (bytes, bytearray)) or not content:
                continue
            if not mime_type.startswith("image/"):
                continue
            incoming_images.append(
                {
                    "filename": str(payload.get("filename") or "imagen.png").strip() or "imagen.png",
                    "mime_type": mime_type,
                    "bytes": bytes(content),
                }
            )
        if not incoming_images:
            raise ValueError("Debes adjuntar al menos una imagen valida.")

        existing_images = self.obtener_imagenes_tabla(odt_limpia)

        remaining_slots = max(0, 3 - len(existing_images))
        if remaining_slots <= 0:
            raise ValueError("Esta ODT ya tiene 3 imagenes cargadas.")
        if len(incoming_images) > remaining_slots:
            raise ValueError(f"Solo puedes subir {remaining_slots} imagen(es) adicional(es) para esta ODT.")

        try:
            drive_result = upload_support_images_for_odt(
                odt=odt_limpia,
                image_payloads=incoming_images,
                root_folder_id=str(settings.google_drive_support_folder_id or "").strip(),
                start_index=len(existing_images) + 1,
            )
        except DriveReportError as exc:
            raise ValueError(f"No se pudo subir a Drive: {exc}") from exc
        except Exception as exc:
            raise ValueError(f"Error inesperado al subir imagenes: {exc}") from exc

        new_urls = [str(url or "").strip() for url in (drive_result.get("imagenes") or []) if str(url or "").strip()]
        if not new_urls:
            raise ValueError("No se pudo obtener URL publica de las imagenes subidas.")

        merged_images = existing_images[:]
        for url in new_urls:
            if url not in merged_images:
                merged_images.append(url)
        merged_images = merged_images[:3]

        usuario = self.get_usuario_actual((token or "").strip())
        if not usuario or usuario == "Desconocido":
            usuario = "Usuario no identificado"

        sucursal_value = str(row_odt.cliente or "").strip()
        self._upsert_unified_images(odt_limpia, sucursal_value, usuario, merged_images)

        self.db.commit()
        return {
            "ok": True,
            "odt": odt_limpia,
            "imagenes": merged_images,
            "imagenes_guardadas": len(new_urls),
            "total_imagenes": len(merged_images),
            "drive_folder_id": str(drive_result.get("folder_id") or ""),
            "drive_folder_name": str(drive_result.get("folder_name") or ""),
        }

    def obtener_imagenes_finalizacion(self, odt: str) -> list[str]:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            return []
        urls: list[str] = []

        try:
            for schema_name in self._schemas_con_tabla("incidencias"):
                cols = self._columnas_tabla(schema_name, "incidencias")
                if not cols:
                    continue
                col_odt = self._pick_col(cols, ["odt", "codigo_odt", "codigo", "nro_odt"])
                if not col_odt:
                    continue
                col_img1 = self._pick_col(cols, ["foto_1", "foto1", "imagen_1", "imagen1", "url_foto_1"])
                col_img2 = self._pick_col(cols, ["foto_2", "foto2", "imagen_2", "imagen2", "url_foto_2"])
                col_img3 = self._pick_col(cols, ["foto_3", "foto3", "imagen_3", "imagen3", "url_foto_3"])
                col_single = self._pick_col(cols, ["foto", "foto_url", "imagen_url"])
                cols_select = []
                if col_img1:
                    cols_select.append(f'COALESCE(CAST("{col_img1}" AS text), \'\') AS img1')
                if col_img2:
                    cols_select.append(f'COALESCE(CAST("{col_img2}" AS text), \'\') AS img2')
                if col_img3:
                    cols_select.append(f'COALESCE(CAST("{col_img3}" AS text), \'\') AS img3')
                if col_single:
                    cols_select.append(f'COALESCE(CAST("{col_single}" AS text), \'\') AS img_single')
                if not cols_select:
                    continue
                sql = text(
                    f"""
                    SELECT {", ".join(cols_select)}
                    FROM "{schema_name}"."incidencias"
                    WHERE btrim(CAST("{col_odt}" AS text)) = :odt
                    LIMIT 1
                    """
                )
                row = self.db.execute(sql, {"odt": odt_limpia}).mappings().first()
                if not row:
                    continue
                for k in ["img1", "img2", "img3", "img_single"]:
                    v = str(row.get(k) or "").strip()
                    if v and v not in urls:
                        urls.append(v)
                if urls:
                    return urls
        except Exception:
            pass

        reg = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if reg:
            for v in [reg.foto_1, reg.foto_2, reg.foto_3]:
                s = str(v or "").strip()
                if s and s not in urls:
                    urls.append(s)
        if urls:
            return urls

        venta = self.db.scalar(select(OdtVenta).where(OdtVenta.odt == odt_limpia))
        if venta and venta.foto_url:
            return [venta.foto_url]
        return []

    def guardar_datos_en_proceso(self, odt: str, avance: int, observacion: str, token: str = "") -> str:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            raise ValueError("ODT invalida")

        row = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row:
            raise ValueError(f"No se encontro la ODT {odt_limpia}")

        avance_num = max(0, min(100, int(avance)))
        marca = datetime.utcnow().strftime("%d/%m/%Y %H:%M")
        usuario = (self.get_usuario_actual((token or "").strip()) if (token or "").strip() else "").strip()
        if not usuario or usuario == "Desconocido":
            usuario = str(getattr(row, "tecnicos", "") or "").strip() or str(getattr(row, "acompanante", "") or "").strip()
        if not usuario:
            usuario = "Usuario no identificado"
        nota = f"[{usuario} - {marca}] {observacion.strip()} (Avance: {avance_num}%)"

        row.estado = "Pendiente"
        row.porcentaje_avance = f"{avance_num}%"
        base = (getattr(row, "observacion_pendiente", "") or "").strip()
        row.observacion_pendiente = f"{base}\n{nota}".strip() if base else nota
        self.db.commit()
        return "OK"
    def _obtener_snapshot_cierre_odt(self, odt: str) -> dict[str, str]:
        odt_limpia = (odt or "").strip()
        out = {
            "odt": odt_limpia,
            "cliente": "",
            "sucursal": "",
            "problema": "",
            "direccion": "",
            "tecnico": "",
            "acompanante": "",
            "fecha_cierre": "",
            "observacion_final": "",
            "rut_cliente": "-",
        }

        row_reg = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if row_reg:
            out["odt"] = str(row_reg.odt or odt_limpia).strip()
            out["cliente"] = str(row_reg.cliente or "").strip()
            out["sucursal"] = out["cliente"]
            out["problema"] = str(row_reg.problema or "").strip()
            out["direccion"] = str(row_reg.direccion or "").strip()
            out["tecnico"] = str(row_reg.tecnicos or "").strip()
            out["acompanante"] = str(row_reg.acompanante or "").strip()
            out["observacion_final"] = str(row_reg.observacion_final or "").strip()
            if isinstance(row_reg.fecha_cierre, datetime):
                out["fecha_cierre"] = _to_ddmmyyyy_hhmm(row_reg.fecha_cierre)

        if not out["cliente"]:
            out["cliente"] = self._buscar_cliente_por_odt(odt_limpia)
            out["sucursal"] = out["cliente"]

        if not out["direccion"] and out["cliente"]:
            out["direccion"] = self._direccion_cliente(out["cliente"])

        try:
            if not out["cliente"]:
                row_venta = self.db.scalar(select(OdtVenta).where(OdtVenta.odt == odt_limpia))
                if row_venta:
                    out["cliente"] = str(row_venta.cliente or "").strip()
                    out["sucursal"] = out["cliente"]
                    out["problema"] = str(row_venta.servicio or "").strip()
                    out["direccion"] = str(row_venta.direccion or "").strip()
                    out["tecnico"] = str(row_venta.tecnico or "").strip()
                    out["acompanante"] = str(row_venta.acompanante or "").strip()
                    out["observacion_final"] = str(row_venta.observacion_final or "").strip()
                    if isinstance(row_venta.fecha_cierre, datetime):
                        out["fecha_cierre"] = _to_ddmmyyyy_hhmm(row_venta.fecha_cierre)
        except Exception:
            self.db.rollback()

        if not out["fecha_cierre"]:
            tz_name = (settings.timezone or "America/Santiago").strip() or "America/Santiago"
            out["fecha_cierre"] = datetime.now(ZoneInfo(tz_name)).strftime("%d/%m/%Y %H:%M")

        self._enriquecer_snapshot_desde_catalogo(out)
        return out

    def _enriquecer_snapshot_desde_catalogo(self, out: dict[str, str]) -> None:
        cliente = str(out.get("cliente") or "").strip()
        if not cliente:
            return

        try:
            row_bbdd = self.db.execute(
                text(
                    """
                    SELECT
                        COALESCE(CAST(rut AS text), '') AS rut,
                        COALESCE(CAST(direccion AS text), '') AS direccion
                    FROM bbdd_clientes
                    WHERE lower(btrim(CAST(cliente AS text))) = lower(:cliente)
                    LIMIT 1
                    """
                ),
                {"cliente": cliente},
            ).mappings().first()
            if row_bbdd:
                rut_txt = str(row_bbdd.get("rut") or "").strip()
                dir_txt = str(row_bbdd.get("direccion") or "").strip()
                if rut_txt:
                    out["rut_cliente"] = rut_txt
                if dir_txt:
                    out["direccion"] = dir_txt

            schema_preferido = (getattr(settings, "db_schema", None) or "public").strip() or "public"
            row_catalogo_preferido = self.db.execute(
                text(
                    f"""
                    SELECT
                        COALESCE(CAST(rut_cliente AS text), '') AS rut,
                        COALESCE(CAST(direccion_sucursal AS text), '') AS direccion
                    FROM "{schema_preferido}"."catalogo_clientes"
                    WHERE lower(btrim(CAST(nombre_sucursal AS text))) = lower(:cliente)
                       OR lower(btrim(CAST(nombre_cliente AS text))) = lower(:cliente)
                    LIMIT 1
                    """
                ),
                {"cliente": cliente},
            ).mappings().first()
            if row_catalogo_preferido:
                rut_txt = str(row_catalogo_preferido.get("rut") or "").strip()
                dir_txt = str(row_catalogo_preferido.get("direccion") or "").strip()
                if rut_txt:
                    out["rut_cliente"] = rut_txt
                if dir_txt:
                    out["direccion"] = dir_txt
                return

            for schema_name in self._schemas_con_tabla("catalogo_clientes"):
                cols_cat = self._columnas_tabla(schema_name, "catalogo_clientes")
                if not cols_cat:
                    continue

                col_cliente_cat = self._pick_col(cols_cat, ["nombre_sucursal", "nombre_cliente", "sucursal", "cliente"])
                col_rut_cat = self._pick_col(cols_cat, ["rut_cliente", "rut", "rut_empresa", "rut_sucursal"])
                col_dir_cat = self._pick_col(cols_cat, ["direccion", "direccion_sucursal", "direccion_trabajos", "direccion_cliente"])
                if not col_cliente_cat or (not col_rut_cat and not col_dir_cat):
                    continue

                select_cols: list[str] = []
                if col_rut_cat:
                    select_cols.append(f'COALESCE(CAST("{col_rut_cat}" AS text), '') AS rut')
                else:
                    select_cols.append("'' AS rut")
                if col_dir_cat:
                    select_cols.append(f'COALESCE(CAST("{col_dir_cat}" AS text), '') AS direccion')
                else:
                    select_cols.append("'' AS direccion")

                sql_cat = text(
                    f"""
                    SELECT {", ".join(select_cols)}
                    FROM "{schema_name}"."catalogo_clientes"
                    WHERE lower(btrim(CAST("{col_cliente_cat}" AS text))) = lower(:cliente)
                    LIMIT 1
                    """
                )
                row_cat = self.db.execute(sql_cat, {"cliente": cliente}).mappings().first()
                if not row_cat:
                    continue

                rut_txt = str(row_cat.get("rut") or "").strip()
                dir_txt = str(row_cat.get("direccion") or "").strip()
                if rut_txt:
                    out["rut_cliente"] = rut_txt
                if dir_txt:
                    out["direccion"] = dir_txt
                break
        except Exception:
            self.db.rollback()

    def _guardar_pdf_url_odt(self, odt: str, pdf_url: str) -> None:
        odt_limpia = (odt or "").strip()
        url = (pdf_url or "").strip()
        if not odt_limpia or not url:
            return

        try:
            row_reg = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
            if row_reg:
                row_reg.pdf_url = url
                self.db.commit()
        except Exception:
            self.db.rollback()

    def _generar_drive_para_cierre(
        self,
        odt: str,
        observacion: str,
        fotos: list[str],
    ) -> dict[str, Any]:
        snapshot = self._obtener_snapshot_cierre_odt(odt)
        observacion_final = str(observacion or snapshot.get("observacion_final") or "").strip()
        fuentes = [str(f or "").strip() for f in (fotos or self.obtener_imagenes_finalizacion(odt)) if str(f or "").strip()]
        tecnico = str(snapshot.get("tecnico") or "").strip()
        acompanante = str(snapshot.get("acompanante") or "").strip()
        tecnico_reporte = tecnico
        if acompanante:
            tecnico_reporte = f"{tecnico} / {acompanante}".strip(" /")

        try:
            result = create_drive_report_for_odt(
                odt=str(snapshot.get("odt") or odt).strip(),
                sucursal=str(snapshot.get("sucursal") or snapshot.get("cliente") or "Sucursal Sin Nombre").strip(),
                cliente=str(snapshot.get("cliente") or snapshot.get("sucursal") or "").strip(),
                rut_cliente=str(snapshot.get("rut_cliente") or "-").strip() or "-",
                problema=str(snapshot.get("problema") or "").strip(),
                direccion=str(snapshot.get("direccion") or "").strip(),
                tecnico=tecnico_reporte,
                fecha_cierre=str(snapshot.get("fecha_cierre") or "").strip(),
                observacion_cierre=observacion_final,
                image_sources=fuentes,
            )
        except DriveReportError as exc:
            raise ValueError(f"Error generando informe Drive: {exc}") from exc

        pdf_url = str(result.get("pdf_web_view_link") or "").strip()
        if pdf_url:
            self._guardar_pdf_url_odt(odt, pdf_url)
        return result

    @staticmethod
    def _ejecutar_drive_en_segundo_plano(odt: str, observacion: str, fotos: list[str]) -> None:
        db = SessionLocal()
        try:
            service = IncidenciasService(db)
            service._generar_drive_para_cierre(odt, observacion, fotos)
        except Exception:
            LOGGER.exception("Fallo la generacion automatica del informe Drive para ODT %s", odt)
        finally:
            db.close()

    def continuar_finalizacion_asincrona(
        self,
        odt: str,
        fotos_base64: list[str],
        observacion: str = "",
    ) -> dict[str, Any] | str:
        odt_limpia = (odt or "").strip()
        if not odt_limpia:
            raise ValueError("ODT invalida")

        fotos = [str(f or "").strip() for f in (fotos_base64 or []) if str(f or "").strip()][:3]

        row = self.db.scalar(select(Registro).where(Registro.odt == odt_limpia))
        if not row:
            raise ValueError(f"No se encontro la ODT {odt_limpia}")

        obs_cierre = str(observacion or "").strip()
        cierre_ya_sincronizado = (
            self._normalizar_texto(getattr(row, "estado", "") or "") == "terminado"
            and str(getattr(row, "observacion_final", "") or "").strip() == obs_cierre
        )

        if len(fotos) >= 1:
            row.foto_1 = fotos[0]
        if len(fotos) >= 2:
            row.foto_2 = fotos[1]
        if len(fotos) >= 3:
            row.foto_3 = fotos[2]
        if obs_cierre:
            row.observacion_final = obs_cierre
        self.db.commit()
        if not cierre_ya_sincronizado:
            self._sync_estado_ticket_soporte_silencioso(
                odt_limpia,
                TICKET_STATUS_RESUELTO_SERVICIO,
                nota_interna=self._build_nota_cierre_ticket_soporte(
                    odt=odt_limpia,
                    estado_ticket=TICKET_STATUS_RESUELTO_SERVICIO,
                    derivacion=row.derivacion or "Servicio Tecnico",
                    observacion_final=row.observacion_final or obs_cierre,
                ),
            )

        drive_enabled = bool(settings.google_drive_enabled)
        if drive_enabled:
            worker = threading.Thread(
                target=self._ejecutar_drive_en_segundo_plano,
                args=(odt_limpia, observacion, fotos),
                daemon=True,
                name=f"drive-report-{odt_limpia}",
            )
            worker.start()
            return {
                "result": "OK",
                "drive_enabled": True,
                "drive_queued": True,
                "drive_message": "Informe en generacion en segundo plano.",
            }

        return {"result": "OK", "drive_enabled": False}

    def guardar_mantencion_correctiva(self, data: dict[str, Any]) -> str:
        sucursal = str(data.get("sucursal") or "").strip()
        problema = str(data.get("problema") or "").strip()
        if not sucursal or not problema:
            raise ValueError("Sucursal y servicio son obligatorios.")

        odt = self._proximo_odt("M")
        ahora = datetime.utcnow()
        observacion = str(data.get("observacion") or "").strip()
        observacion_servicio = str(data.get("observacion_servicio") or "").strip()
        estado = str(data.get("estado") or "Pendiente").strip() or "Pendiente"

        reg = Registro(
            odt=odt,
            fecha_registro=ahora,
            puesto=None,
            cliente=sucursal,
            problema=problema,
            detalle_problema=(observacion or None),
            derivacion="Servicio Tecnico",
            observacion=(observacion or None),
            observacion_servicio=(observacion_servicio or None),
            tecnicos=str(data.get("tecnico") or "").strip() or None,
            acompanante=str(data.get("acompanante") or "").strip() or None,
            estado=estado,
            fecha_derivacion_area=ahora,
            fecha_derivacion_tecnico=ahora,
            direccion=self._direccion_cliente(sucursal),
            prioridad=data.get("prioridad") or None,
        )
        self.db.add(reg)
        self.db.commit()
        self._reset_unified_images_if_odt_reused(odt, sucursal)
        return "OK"

    @staticmethod
    def _semana_del_mes(fecha_ref: datetime) -> int:
        return ((fecha_ref.day - 1) // 7) + 1

    @staticmethod
    def _normalizar_sucursal_key(valor: str) -> str:
        txt = str(valor or "").strip().lower()
        if not txt:
            return ""
        txt = unicodedata.normalize("NFD", txt)
        txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def obtener_plantilla_imagenes_mantencion(self, sucursal: str) -> list[str]:
        sucursal_key = self._normalizar_sucursal_key(sucursal)
        if not sucursal_key:
            return []

        row = self.db.scalar(
            select(MantencionImagenSucursal).where(MantencionImagenSucursal.sucursal_key == sucursal_key)
        )
        if row:
            return self._parse_image_list(row.imagenes)[:3]

        # Compatibilidad/migracion desde implementacion antigua (plantilla guardada por pseudo-ODT).
        odt_tpl_legacy = f"__MANT_TPL__:{sucursal_key}"
        legacy = self.db.scalar(select(IncidenciaImagenTabla).where(IncidenciaImagenTabla.odt == odt_tpl_legacy))
        legacy_urls = self._parse_image_list(legacy.imagenes if legacy else "[]")[:3]
        if not legacy_urls:
            return []

        nuevo = MantencionImagenSucursal(
            sucursal_key=sucursal_key,
            sucursal=str(sucursal or "").strip() or sucursal_key,
            imagenes=json.dumps(legacy_urls, ensure_ascii=False),
            created_by="migracion_legacy",
        )
        self.db.add(nuevo)
        self.db.commit()
        return legacy_urls

    def guardar_plantilla_imagenes_mantencion(
        self,
        sucursal: str,
        imagenes: list[str],
    ) -> dict[str, Any]:
        sucursal_limpia = str(sucursal or "").strip()
        if not sucursal_limpia:
            raise ValueError("Sucursal es obligatoria.")

        urls = [
            str(url or "").strip()
            for url in (imagenes or [])
            if self._es_url_publica_imagen(str(url or "").strip())
        ][:3]
        if not urls:
            raise ValueError("Debes indicar al menos una URL publica de imagen.")

        sucursal_key = self._normalizar_sucursal_key(sucursal_limpia)
        if not sucursal_key:
            raise ValueError("No se pudo normalizar la sucursal.")

        row = self.db.scalar(
            select(MantencionImagenSucursal).where(MantencionImagenSucursal.sucursal_key == sucursal_key)
        )
        payload = json.dumps(urls, ensure_ascii=False)
        if row:
            row.sucursal = sucursal_limpia
            row.imagenes = payload
            row.created_by = "plantilla_mantencion"
            row.updated_at = datetime.utcnow()
        else:
            self.db.add(
                MantencionImagenSucursal(
                    sucursal_key=sucursal_key,
                    sucursal=sucursal_limpia,
                    imagenes=payload,
                    created_by="plantilla_mantencion",
                )
            )
        self.db.commit()
        return {
            "ok": True,
            "sucursal": sucursal_limpia,
            "sucursal_key": sucursal_key,
            "imagenes": urls,
            "total_imagenes": len(urls),
        }

    def guardar_plantilla_imagenes_mantencion_desde_odt(
        self,
        sucursal: str,
        odt_origen: str,
    ) -> dict[str, Any]:
        odt_limpia = str(odt_origen or "").strip()
        if not odt_limpia:
            raise ValueError("ODT de origen es obligatoria.")
        imagenes = self.obtener_imagenes_tabla(odt_limpia)
        if not imagenes:
            raise ValueError(f"La ODT {odt_limpia} no tiene imagenes cargadas.")
        result = self.guardar_plantilla_imagenes_mantencion(sucursal=sucursal, imagenes=imagenes)
        result["odt_origen"] = odt_limpia
        return result

    def _imagenes_programadas_para_sucursal(self, sucursal: str) -> list[str]:
        tpl_urls = self.obtener_plantilla_imagenes_mantencion(sucursal)
        if tpl_urls:
            return tpl_urls
        key = self._normalizar_sucursal_key(sucursal)
        if not key:
            return []
        seeds = MANTENCIONES_IMAGENES_POR_SUCURSAL.get(key, [])
        return [str(u or "").strip() for u in seeds if str(u or "").strip()][:3]

    @staticmethod
    def _es_url_publica_imagen(valor: str) -> bool:
        txt = str(valor or "").strip().lower()
        return txt.startswith("http://") or txt.startswith("https://")

    def _payloads_imagenes_programadas(self, fuentes: list[str]) -> list[dict[str, object]]:
        payloads: list[dict[str, object]] = []
        for fuente in fuentes:
            raw = str(fuente or "").strip()
            if not raw:
                continue
            if self._es_url_publica_imagen(raw):
                continue
            file_path = Path(raw)
            if not file_path.is_absolute():
                file_path = Path.cwd() / file_path
            if not file_path.exists() or not file_path.is_file():
                continue
            mime_type = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
            if not str(mime_type).lower().startswith("image/"):
                mime_type = "image/jpeg"
            payloads.append(
                {
                    "filename": file_path.name,
                    "mime_type": mime_type,
                    "bytes": file_path.read_bytes(),
                }
            )
        return payloads[:3]

    def _asignar_imagenes_programadas_a_odt(
        self,
        odt: str,
        sucursal: str,
        sobrescribir: bool = False,
    ) -> bool:
        odt_limpia = str(odt or "").strip()
        if not odt_limpia:
            return False

        fuentes = self._imagenes_programadas_para_sucursal(sucursal)
        direct_urls = [f for f in fuentes if self._es_url_publica_imagen(f)][:3]
        payloads = self._payloads_imagenes_programadas(fuentes)
        if not direct_urls and not payloads:
            LOGGER.warning(
                "No hay imagenes plantilla disponibles para sucursal programada '%s'.",
                sucursal,
            )
            return False

        row_imgs = self.db.scalar(select(IncidenciaImagenTabla).where(IncidenciaImagenTabla.odt == odt_limpia))
        existentes = self._parse_image_list(row_imgs.imagenes if row_imgs else "[]")[:3]
        if existentes and not sobrescribir and all(self._es_url_publica_imagen(url) for url in existentes):
            return False

        try:
            existentes_drive = list_support_images_for_odt(
                odt=odt_limpia,
                root_folder_id=str(settings.google_drive_support_folder_id or "").strip(),
            )
            existentes_drive = [
                str(url or "").strip()
                for url in (existentes_drive or [])
                if str(url or "").strip()
            ][:3]
        except Exception:
            existentes_drive = []

        if existentes_drive and not sobrescribir:
            self._upsert_unified_images(
                odt=odt_limpia,
                sucursal=str(sucursal or "").strip(),
                usuario="auto_mantencion_programada",
                imagenes=existentes_drive,
            )
            self.db.commit()
            return True

        if direct_urls and not payloads:
            self._upsert_unified_images(
                odt=odt_limpia,
                sucursal=str(sucursal or "").strip(),
                usuario="auto_mantencion_programada",
                imagenes=direct_urls,
            )
            self.db.commit()
            return True

        try:
            drive_result = upload_support_images_for_odt(
                odt=odt_limpia,
                image_payloads=payloads,
                root_folder_id=str(settings.google_drive_support_folder_id or "").strip(),
                start_index=1,
            )
        except Exception:
            LOGGER.exception(
                "No se pudieron subir imagenes programadas de mantencion (odt=%s, sucursal=%s).",
                odt_limpia,
                sucursal,
            )
            return False

        nuevas_urls = [
            str(url or "").strip()
            for url in (drive_result.get("imagenes") or [])
            if str(url or "").strip()
        ][:3]
        merged_urls = []
        for url in [*direct_urls, *nuevas_urls]:
            clean = str(url or "").strip()
            if clean and clean not in merged_urls:
                merged_urls.append(clean)
            if len(merged_urls) >= 3:
                break
        if not merged_urls:
            return False

        self._upsert_unified_images(
            odt=odt_limpia,
            sucursal=str(sucursal or "").strip(),
            usuario="auto_mantencion_programada",
            imagenes=merged_urls,
        )
        self.db.commit()
        return True

    def programar_mantenciones_quilpue(
        self,
        fecha_referencia: datetime | None = None,
        forzar: bool = False,
    ) -> dict[str, Any]:
        tz = ZoneInfo(settings.timezone or "America/Santiago")
        if fecha_referencia is None:
            ref = datetime.now(tz)
        elif fecha_referencia.tzinfo is None:
            ref = fecha_referencia.replace(tzinfo=tz)
        else:
            ref = fecha_referencia.astimezone(tz)

        if not forzar and ref.weekday() != 0:
            return {
                "status": "skip",
                "reason": "solo_lunes",
                "fecha_referencia": ref.isoformat(),
            }
        if not forzar and ref.hour < 6:
            return {
                "status": "skip",
                "reason": "hora_menor_a_06",
                "fecha_referencia": ref.isoformat(),
            }

        semana = self._semana_del_mes(ref)
        sucursales = MANTENCIONES_PROGRAMADAS_QUILPUE.get(semana, [])
        if not sucursales:
            return {
                "status": "skip",
                "reason": "semana_5_sin_mantenciones",
                "semana": semana,
                "fecha_referencia": ref.isoformat(),
                "creadas": 0,
                "omitidas": 0,
            }

        mes_key = ref.strftime("%Y-%m")
        marca = f"[AUTO-MANT-QUILPUE {mes_key} S{semana}]"
        observacion_servicio_auto = "Mantenci\u00f3n Preventiva Completa de la totalidad del servicio"
        creadas: list[str] = []
        omitidas: list[str] = []
        normalizadas: list[str] = []
        imagenes_asignadas: list[str] = []
        errores: list[dict[str, str]] = []

        for sucursal in sucursales:
            # Normaliza registros creados con la version anterior de la automatizacion.
            legacy_rows = self.db.scalars(
                select(Registro).where(
                    Registro.cliente == sucursal,
                    Registro.problema == "Mantencion Preventiva",
                    Registro.observacion.is_not(None),
                    Registro.observacion.ilike(f"%{marca}%"),
                )
            ).all()
            if legacy_rows:
                for row in legacy_rows:
                    row.derivacion = "Servicio Tecnico"
                    row.estado = "Pendiente"
                    row.observacion_servicio = observacion_servicio_auto
                    if row.observacion and marca in str(row.observacion):
                        row.observacion = None
                self.db.commit()
                for row in legacy_rows:
                    try:
                        if self._asignar_imagenes_programadas_a_odt(str(row.odt or "").strip(), sucursal):
                            if sucursal not in imagenes_asignadas:
                                imagenes_asignadas.append(sucursal)
                    except Exception:
                        self.db.rollback()
                normalizadas.append(sucursal)

            ya_existe = (
                self.db.scalar(
                    select(func.count())
                    .select_from(Registro)
                    .where(
                        Registro.cliente == sucursal,
                        Registro.problema == "Mantencion Preventiva",
                        or_(
                            Registro.observacion_servicio == observacion_servicio_auto,
                            Registro.observacion.ilike(f"%{marca}%"),
                        ),
                    )
                )
                or 0
            )
            if ya_existe:
                omitidas.append(sucursal)
                odt_existente = (
                    self.db.scalar(
                        select(Registro.odt)
                        .where(
                            Registro.cliente == sucursal,
                            Registro.problema == "Mantencion Preventiva",
                        )
                        .order_by(Registro.id.desc())
                    )
                    or ""
                )
                if odt_existente:
                    try:
                        if self._asignar_imagenes_programadas_a_odt(str(odt_existente), sucursal):
                            if sucursal not in imagenes_asignadas:
                                imagenes_asignadas.append(sucursal)
                    except Exception:
                        self.db.rollback()
                continue

            try:
                self.guardar_mantencion_correctiva(
                    {
                        "sucursal": sucursal,
                        "problema": "Mantencion Preventiva",
                        "observacion": "",
                        "observacion_servicio": observacion_servicio_auto,
                        "estado": "Pendiente",
                        "tecnico": "",
                        "acompanante": "",
                        "prioridad": "",
                    }
                )
                odt_creada = (
                    self.db.scalar(
                        select(Registro.odt)
                        .where(
                            Registro.cliente == sucursal,
                            Registro.problema == "Mantencion Preventiva",
                        )
                        .order_by(Registro.id.desc())
                    )
                    or ""
                )
                if odt_creada:
                    try:
                        if self._asignar_imagenes_programadas_a_odt(str(odt_creada), sucursal):
                            if sucursal not in imagenes_asignadas:
                                imagenes_asignadas.append(sucursal)
                    except Exception:
                        self.db.rollback()
                creadas.append(sucursal)
            except Exception as exc:
                self.db.rollback()
                errores.append({"sucursal": sucursal, "error": str(exc)})

        return {
            "status": "ok",
            "semana": semana,
            "fecha_referencia": ref.isoformat(),
            "marca": marca,
            "creadas": len(creadas),
            "omitidas": len(omitidas),
            "normalizadas": len(normalizadas),
            "imagenes_asignadas": len(imagenes_asignadas),
            "errores": errores,
            "sucursales_creadas": creadas,
            "sucursales_omitidas": omitidas,
            "sucursales_normalizadas": normalizadas,
            "sucursales_imagenes_asignadas": imagenes_asignadas,
        }

    def _programar_mantenciones_trimestrales(
        self,
        comuna_key: str,
        sucursales_base: list[str],
        fecha_referencia: datetime | None = None,
        forzar: bool = False,
        limite: int | None = None,
    ) -> dict[str, Any]:
        comuna_normalizada = self._normalizar_sucursal_key(comuna_key)
        marca_comuna = comuna_normalizada.upper().replace(" ", "-") or "TRIMESTRAL"
        tz = ZoneInfo(settings.timezone or "America/Santiago")
        if fecha_referencia is None:
            ref = datetime.now(tz)
        elif fecha_referencia.tzinfo is None:
            ref = fecha_referencia.replace(tzinfo=tz)
        else:
            ref = fecha_referencia.astimezone(tz)

        trimestre = ((ref.month - 1) // 3) + 1
        if not forzar and ref.month not in MESES_MANTENCION_TRIMESTRAL:
            return {
                "status": "skip",
                "reason": "mes_fuera_de_cierre_trimestral",
                "comuna": comuna_normalizada,
                "trimestre": trimestre,
                "fecha_referencia": ref.isoformat(),
            }
        if not forzar and ref.day != 1:
            return {
                "status": "skip",
                "reason": "solo_dia_01",
                "comuna": comuna_normalizada,
                "trimestre": trimestre,
                "fecha_referencia": ref.isoformat(),
            }
        if not forzar and ref.hour < 6:
            return {
                "status": "skip",
                "reason": "hora_menor_a_06",
                "comuna": comuna_normalizada,
                "trimestre": trimestre,
                "fecha_referencia": ref.isoformat(),
            }

        mes_key = ref.strftime("%Y-%m")
        primer_mes_trimestre = ((trimestre - 1) * 3) + 1
        meses_trimestre = [primer_mes_trimestre, primer_mes_trimestre + 1, primer_mes_trimestre + 2]
        sucursales = sucursales_base
        if limite is not None and limite > 0:
            sucursales = sucursales[:limite]
        marca = f"[AUTO-MANT-{marca_comuna} {ref.year} T{trimestre}]"
        observacion_servicio_auto = "Mantenci\u00f3n Preventiva Completa de la totalidad del servicio"
        creadas: list[str] = []
        omitidas: list[str] = []
        imagenes_asignadas: list[str] = []
        errores: list[dict[str, str]] = []

        for sucursal in sucursales:
            ya_existe = (
                self.db.scalar(
                    select(func.count())
                    .select_from(Registro)
                    .where(
                        Registro.cliente == sucursal,
                        Registro.problema == "Mantencion Preventiva",
                        Registro.observacion_servicio == observacion_servicio_auto,
                        func.extract("year", Registro.fecha_registro) == ref.year,
                        func.extract("month", Registro.fecha_registro).in_(meses_trimestre),
                    )
                )
                or 0
            )
            if ya_existe:
                omitidas.append(sucursal)
                odt_existente = (
                    self.db.scalar(
                        select(Registro.odt)
                        .where(
                            Registro.cliente == sucursal,
                            Registro.problema == "Mantencion Preventiva",
                        )
                        .order_by(Registro.id.desc())
                    )
                    or ""
                )
                if odt_existente:
                    try:
                        if self._asignar_imagenes_programadas_a_odt(str(odt_existente), sucursal):
                            if sucursal not in imagenes_asignadas:
                                imagenes_asignadas.append(sucursal)
                    except Exception:
                        self.db.rollback()
                continue

            try:
                self.guardar_mantencion_correctiva(
                    {
                        "sucursal": sucursal,
                        "problema": "Mantencion Preventiva",
                        "observacion": "",
                        "observacion_servicio": observacion_servicio_auto,
                        "estado": "Pendiente",
                        "tecnico": "",
                        "acompanante": "",
                        "prioridad": "",
                    }
                )
                odt_creada = (
                    self.db.scalar(
                        select(Registro.odt)
                        .where(
                            Registro.cliente == sucursal,
                            Registro.problema == "Mantencion Preventiva",
                        )
                        .order_by(Registro.id.desc())
                    )
                    or ""
                )
                if odt_creada:
                    try:
                        if self._asignar_imagenes_programadas_a_odt(str(odt_creada), sucursal):
                            if sucursal not in imagenes_asignadas:
                                imagenes_asignadas.append(sucursal)
                    except Exception:
                        self.db.rollback()
                creadas.append(sucursal)
            except Exception as exc:
                self.db.rollback()
                errores.append({"sucursal": sucursal, "error": str(exc)})

        return {
            "status": "ok",
            "comuna": comuna_normalizada,
            "trimestre": trimestre,
            "fecha_referencia": ref.isoformat(),
            "mes_programacion": mes_key,
            "meses_trimestre": meses_trimestre,
            "marca": marca,
            "limite": limite,
            "total_programadas": len(sucursales),
            "creadas": len(creadas),
            "omitidas": len(omitidas),
            "imagenes_asignadas": len(imagenes_asignadas),
            "errores": errores,
            "sucursales_creadas": creadas,
            "sucursales_omitidas": omitidas,
            "sucursales_imagenes_asignadas": imagenes_asignadas,
        }

    def programar_mantenciones_trimestrales_quintero(
        self,
        fecha_referencia: datetime | None = None,
        forzar: bool = False,
        limite: int | None = None,
    ) -> dict[str, Any]:
        return self._programar_mantenciones_trimestrales(
            comuna_key="quintero",
            sucursales_base=MANTENCIONES_TRIMESTRALES_QUINTERO,
            fecha_referencia=fecha_referencia,
            forzar=forzar,
            limite=limite,
        )

    def programar_mantenciones_trimestrales_concon(
        self,
        fecha_referencia: datetime | None = None,
        forzar: bool = False,
        limite: int | None = None,
    ) -> dict[str, Any]:
        return self._programar_mantenciones_trimestrales(
            comuna_key="concon",
            sucursales_base=MANTENCIONES_TRIMESTRALES_CONCON,
            fecha_referencia=fecha_referencia,
            forzar=forzar,
            limite=limite,
        )

    def programar_mantenciones_trimestrales_quintero_y_concon(
        self,
        fecha_referencia: datetime | None = None,
        forzar: bool = False,
        limite: int | None = None,
    ) -> dict[str, Any]:
        resultados = {
            "quintero": self.programar_mantenciones_trimestrales_quintero(
                fecha_referencia=fecha_referencia,
                forzar=forzar,
                limite=limite,
            ),
            "concon": self.programar_mantenciones_trimestrales_concon(
                fecha_referencia=fecha_referencia,
                forzar=forzar,
                limite=limite,
            ),
        }
        total_creadas = sum(int(r.get("creadas") or 0) for r in resultados.values())
        total_omitidas = sum(int(r.get("omitidas") or 0) for r in resultados.values())
        total_errores = sum(len(r.get("errores") or []) for r in resultados.values())
        return {
            "status": "ok" if total_errores == 0 else "partial",
            "fecha_referencia": next((r.get("fecha_referencia") for r in resultados.values() if r.get("fecha_referencia")), None),
            "creadas": total_creadas,
            "omitidas": total_omitidas,
            "errores": total_errores,
            "resultados": resultados,
        }

    def programar_mantenciones_mensuales_llay_llay(
        self,
        fecha_referencia: datetime | None = None,
        forzar: bool = False,
    ) -> dict[str, Any]:
        tz = ZoneInfo(settings.timezone or "America/Santiago")
        if fecha_referencia is None:
            ref = datetime.now(tz)
        elif fecha_referencia.tzinfo is None:
            ref = fecha_referencia.replace(tzinfo=tz)
        else:
            ref = fecha_referencia.astimezone(tz)

        if not forzar and ref.day != 1:
            return {
                "status": "skip",
                "reason": "solo_dia_01",
                "fecha_referencia": ref.isoformat(),
            }
        if not forzar and ref.hour < 6:
            return {
                "status": "skip",
                "reason": "hora_menor_a_06",
                "fecha_referencia": ref.isoformat(),
            }

        mes_key = ref.strftime("%Y-%m")
        marca = f"[AUTO-MANT-LLAY-LLAY {mes_key}]"
        observacion_servicio_auto = "Mantención Preventiva Completa de la totalidad del servicio"
        creadas: list[str] = []
        omitidas: list[str] = []
        imagenes_asignadas: list[str] = []
        errores: list[dict[str, str]] = []

        for sucursal in MANTENCIONES_MENSUALES_LLAY_LLAY:
            ya_existe = (
                self.db.scalar(
                    select(func.count())
                    .select_from(Registro)
                    .where(
                        Registro.cliente == sucursal,
                        Registro.problema == "Mantencion Preventiva",
                        Registro.observacion_servicio == observacion_servicio_auto,
                        func.extract("year", Registro.fecha_registro) == ref.year,
                        func.extract("month", Registro.fecha_registro) == ref.month,
                    )
                )
                or 0
            )
            if ya_existe:
                omitidas.append(sucursal)
                odt_existente = (
                    self.db.scalar(
                        select(Registro.odt)
                        .where(
                            Registro.cliente == sucursal,
                            Registro.problema == "Mantencion Preventiva",
                        )
                        .order_by(Registro.id.desc())
                    )
                    or ""
                )
                if odt_existente:
                    try:
                        if self._asignar_imagenes_programadas_a_odt(str(odt_existente), sucursal):
                            if sucursal not in imagenes_asignadas:
                                imagenes_asignadas.append(sucursal)
                    except Exception:
                        self.db.rollback()
                continue

            try:
                self.guardar_mantencion_correctiva(
                    {
                        "sucursal": sucursal,
                        "problema": "Mantencion Preventiva",
                        "observacion": "",
                        "observacion_servicio": observacion_servicio_auto,
                        "estado": "Pendiente",
                        "tecnico": "",
                        "acompanante": "",
                        "prioridad": "",
                    }
                )
                odt_creada = (
                    self.db.scalar(
                        select(Registro.odt)
                        .where(
                            Registro.cliente == sucursal,
                            Registro.problema == "Mantencion Preventiva",
                        )
                        .order_by(Registro.id.desc())
                    )
                    or ""
                )
                if odt_creada:
                    try:
                        if self._asignar_imagenes_programadas_a_odt(str(odt_creada), sucursal):
                            if sucursal not in imagenes_asignadas:
                                imagenes_asignadas.append(sucursal)
                    except Exception:
                        self.db.rollback()
                creadas.append(sucursal)
            except Exception as exc:
                self.db.rollback()
                errores.append({"sucursal": sucursal, "error": str(exc)})

        return {
            "status": "ok" if not errores else "partial",
            "fecha_referencia": ref.isoformat(),
            "mes_programacion": mes_key,
            "marca": marca,
            "creadas": len(creadas),
            "omitidas": len(omitidas),
            "imagenes_asignadas": len(imagenes_asignadas),
            "errores": errores,
            "sucursales_creadas": creadas,
            "sucursales_omitidas": omitidas,
            "sucursales_imagenes_asignadas": imagenes_asignadas,
        }

    def obtener_clientes_soporte(self) -> list[str]:
        clientes_base = self.obtener_catalogo_clientes()
        if clientes_base:
            clientes = sorted({(r or "").strip() for r in clientes_base if (r or "").strip() and (r or "").strip().lower() != "oficina atc"})
        else:
            try:
                rows_contacto = self.db.scalars(select(ContactoEmergencia.sucursal)).all()
                clientes = sorted({(r or "").strip() for r in rows_contacto if (r or "").strip() and (r or "").strip().lower() != "oficina atc"})
            except Exception:
                clientes = []
        return ["OFICINA ATC", *clientes]
    def obtener_catalogo_clientes(self) -> list[str]:
        # En tu PostgreSQL (captura) existen columnas como:
        # nombre_sucursal / nombre_cliente / rut_cliente.
        # Priorizamos nombre_sucursal para poblar el selector "Cliente" en UI.
        preferidas = ["nombre_sucursal", "nombre_cliente", "sucursal", "cliente"]
        schema_preferido = (getattr(settings, "db_schema", None) or "public").strip()

        def _schemas_catalogo() -> list[str]:
            rows = self.db.execute(
                text(
                    """
                    SELECT DISTINCT table_schema
                    FROM information_schema.columns
                    WHERE table_name = 'catalogo_clientes'
                      AND table_schema NOT IN ('pg_catalog', 'information_schema')
                    """
                )
            ).all()
            schemas = [str(r[0]).strip() for r in rows if r and r[0]]
            if not schemas:
                return [schema_preferido]
            # Priorizar schema configurado/esperado.
            schemas.sort(key=lambda s: (0 if s == schema_preferido else 1, s))
            return schemas

        def _columnas_catalogo(schema_name: str) -> set[str]:
            rows = self.db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = :schema_name
                      AND table_name = 'catalogo_clientes'
                    """
                ),
                {"schema_name": schema_name},
            ).all()
            return {str(r[0]).strip() for r in rows if r and r[0]}

        try:
            for schema_name in _schemas_catalogo():
                cols = _columnas_catalogo(schema_name)
                if not cols:
                    continue

                col_cliente = next((c for c in preferidas if c in cols), None)
                if not col_cliente:
                    continue

                col_activo = "activo" if "activo" in cols else None
                where_activo = f'AND "{col_activo}" = TRUE' if col_activo else ""

                sql = text(
                    f"""
                    SELECT DISTINCT "{col_cliente}" AS cliente
                    FROM "{schema_name}"."catalogo_clientes"
                    WHERE "{col_cliente}" IS NOT NULL
                      AND btrim(CAST("{col_cliente}" AS text)) <> ''
                      {where_activo}
                    ORDER BY 1
                    """
                )
                rows = self.db.execute(sql).all()
                clientes = [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
                if clientes:
                    return clientes
        except Exception:
            pass

        # fallback solo si no hay catalogo usable
        try:
            rows_bbdd = self.db.scalars(select(ClienteBBDD.cliente).order_by(ClienteBBDD.cliente.asc())).all()
            clientes_bbdd = [r for r in rows_bbdd if r]
            if clientes_bbdd:
                return clientes_bbdd
        except Exception:
            pass

        try:
            rows_contacto = self.db.scalars(select(ContactoEmergencia.sucursal).order_by(ContactoEmergencia.sucursal.asc())).all()
            return sorted({r for r in rows_contacto if r})
        except Exception:
            return []

    def guardar_mantencion_correctiva(self, data: dict[str, Any]) -> str:
        sucursal = str(data.get("sucursal") or "").strip()
        problema = str(data.get("problema") or "").strip()
        if not sucursal or not problema:
            raise ValueError("Sucursal y servicio son obligatorios.")

        odt = self._proximo_odt("M")
        ahora = datetime.utcnow()
        observacion = str(data.get("observacion") or "").strip()
        observacion_servicio = str(data.get("observacion_servicio") or "").strip()
        estado = str(data.get("estado") or "Pendiente").strip() or "Pendiente"

        reg = Registro(
            odt=odt,
            fecha_registro=ahora,
            puesto=None,
            cliente=sucursal,
            problema=problema,
            detalle_problema=(observacion or None),
            derivacion="Servicio Tecnico",
            observacion=(observacion or None),
            observacion_servicio=(observacion_servicio or None),
            tecnicos=str(data.get("tecnico") or "").strip() or None,
            acompanante=str(data.get("acompanante") or "").strip() or None,
            estado=estado,
            fecha_derivacion_area=ahora,
            fecha_derivacion_tecnico=ahora,
            direccion=self._direccion_cliente(sucursal),
            prioridad=data.get("prioridad") or None,
        )
        self.db.add(reg)
        self.db.commit()
        return "OK"

    def obtener_contactos_por_sucursal(self) -> dict[str, list[dict[str, str]]]:
        data: dict[str, list[dict[str, str]]] = {}

        def _push(sucursal: str, nombre: str, telefono: str, email: str, prioridad: str) -> None:
            suc = (sucursal or "").strip()
            if not suc:
                return
            # Evitar entradas vacÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â­as inÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Âºtiles en el selector.
            if not ((nombre or "").strip() or (telefono or "").strip() or (email or "").strip()):
                return
            data.setdefault(suc, []).append(
                {
                    "nombre": (nombre or "").strip(),
                    "telefono": (telefono or "").strip(),
                    "email": (email or "").strip(),
                    "prioridad": (prioridad or "").strip(),
                }
            )

        def _tablas_disponibles(table_name: str) -> list[str]:
            rows = self.db.execute(
                text(
                    """
                    SELECT DISTINCT table_schema
                    FROM information_schema.columns
                    WHERE table_name = :table_name
                      AND table_schema NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY table_schema
                    """
                ),
                {"table_name": table_name},
            ).all()
            return [str(r[0]).strip() for r in rows if r and r[0]]

        def _columnas(schema_name: str, table_name: str) -> set[str]:
            rows = self.db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = :schema_name
                      AND table_name = :table_name
                    """
                ),
                {"schema_name": schema_name, "table_name": table_name},
            ).all()
            return {str(r[0]).strip() for r in rows if r and r[0]}

        def _pick(cols: set[str], opciones: list[str]) -> str | None:
            return next((c for c in opciones if c in cols), None)

        def _priority_key(item: dict[str, str]) -> tuple[int, str, str]:
            prio_raw = str(item.get("prioridad") or "").strip()
            match = re.search(r"\d+", prio_raw)
            prio_num = int(match.group()) if match else 999999
            return (prio_num, prio_raw.lower(), str(item.get("nombre") or "").lower())

        def _ordenar_por_prioridad() -> None:
            for sucursal in data:
                data[sucursal].sort(key=_priority_key)

        # 1) Fuente principal: contactos_emergencia (si existe).
        try:
            for schema_name in _tablas_disponibles("contactos_emergencia"):
                cols = _columnas(schema_name, "contactos_emergencia")
                if not cols:
                    continue
                col_sucursal = _pick(cols, ["sucursal", "nombre_sucursal", "cliente", "nombre_cliente"])
                if not col_sucursal:
                    continue
                col_nombre = _pick(cols, ["nombre_empleado", "nombre", "nombre_contacto", "contacto", "contacto_nombre"])
                col_tel = _pick(cols, ["celular", "telefono", "fono", "telefono_contacto"])
                col_email = _pick(cols, ["email", "correo", "mail"])
                col_prio = _pick(cols, ["nro_emergencia", "prioridad", "nivel_prioridad"])

                select_cols = [
                    f'"{col_sucursal}" AS sucursal',
                    f'COALESCE(CAST("{col_nombre}" AS text), \'\') AS nombre' if col_nombre else "'' AS nombre",
                    f'COALESCE(CAST("{col_tel}" AS text), \'\') AS telefono' if col_tel else "'' AS telefono",
                    f'COALESCE(CAST("{col_email}" AS text), \'\') AS email' if col_email else "'' AS email",
                    f'COALESCE(CAST("{col_prio}" AS text), \'\') AS prioridad' if col_prio else "'' AS prioridad",
                ]
                sql = text(
                    f"""
                    SELECT {", ".join(select_cols)}
                    FROM "{schema_name}"."contactos_emergencia"
                    WHERE "{col_sucursal}" IS NOT NULL
                      AND btrim(CAST("{col_sucursal}" AS text)) <> ''
                    ORDER BY 1
                    """
                )
                for row in self.db.execute(sql).all():
                    _push(row[0], row[1], row[2], row[3], row[4])
        except Exception:
            pass

        if data:
            _ordenar_por_prioridad()
            return data

        # 2) Fallback: catalogo_clientes (cuando contactos estÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡n en la misma tabla).
        try:
            for schema_name in _tablas_disponibles("catalogo_clientes"):
                cols = _columnas(schema_name, "catalogo_clientes")
                if not cols:
                    continue
                col_sucursal = _pick(cols, ["nombre_sucursal", "sucursal", "cliente", "nombre_cliente"])
                if not col_sucursal:
                    continue
                col_nombre = _pick(cols, ["nombre_empleado", "nombre_contacto", "contacto", "contacto_emergencia", "nombre_emergencia"])
                col_tel = _pick(cols, ["celular", "telefono_contacto", "telefono", "fono"])
                col_email = _pick(cols, ["correo_contacto", "email", "correo", "mail"])
                col_prio = _pick(cols, ["nro_emergencia", "prioridad", "nivel_prioridad"])
                if not any([col_nombre, col_tel, col_email]):
                    continue

                select_cols = [
                    f'"{col_sucursal}" AS sucursal',
                    f'COALESCE(CAST("{col_nombre}" AS text), \'\') AS nombre' if col_nombre else "'' AS nombre",
                    f'COALESCE(CAST("{col_tel}" AS text), \'\') AS telefono' if col_tel else "'' AS telefono",
                    f'COALESCE(CAST("{col_email}" AS text), \'\') AS email' if col_email else "'' AS email",
                    f'COALESCE(CAST("{col_prio}" AS text), \'\') AS prioridad' if col_prio else "'' AS prioridad",
                ]
                sql = text(
                    f"""
                    SELECT {", ".join(select_cols)}
                    FROM "{schema_name}"."catalogo_clientes"
                    WHERE "{col_sucursal}" IS NOT NULL
                      AND btrim(CAST("{col_sucursal}" AS text)) <> ''
                    ORDER BY 1
                    """
                )
                for row in self.db.execute(sql).all():
                    _push(row[0], row[1], row[2], row[3], row[4])
        except Exception:
            pass

        # 3) ÃƒÆ’Ã†â€™Ãƒâ€¦Ã‚Â¡ltimo fallback (modelo actual)
        if data:
            _ordenar_por_prioridad()
            return data
        try:
            rows = self.db.scalars(select(ContactoEmergencia)).all()
            for r in rows:
                _push(r.sucursal, r.nombre or "", r.celular or "", r.email or "", r.prioridad or "")
        except Exception:
            pass
        _ordenar_por_prioridad()
        return data

    def registrar_envio_correo(self, odt: str, sucursal: str, observacion: str, estado: str) -> None:
        self.db.add(
            RegistroCorreoCliente(
                odt=odt,
                sucursal=sucursal,
                observacion=observacion,
                estado=estado,
            )
        )
        self.db.commit()

    def _enviar_correo_derivacion_automatico(
        self,
        *,
        row: Registro,
        derivacion: str,
        usuario: str,
    ) -> dict[str, Any]:
        odt = str(getattr(row, "odt", "") or "").strip()
        sucursal = str(getattr(row, "cliente", "") or "").strip()
        problema = str(getattr(row, "problema", "") or "").strip()
        derivacion_txt = str(derivacion or "").strip()
        if not odt or not sucursal or not derivacion_txt:
            return {"ok": False, "emails_enviados": 0, "warning": "Datos incompletos para correo automatico."}

        contactos = self.obtener_contactos_por_sucursal()
        sucursal_key = next(
            (k for k in contactos.keys() if self._normalizar_texto(k) == self._normalizar_texto(sucursal)),
            "",
        )
        destinos = contactos.get(sucursal_key) or []
        emails: list[str] = []
        vistos: set[str] = set()
        for contacto in destinos:
            email = str(contacto.get("email") or "").strip()
            email_key = email.lower()
            if email and email_key not in vistos:
                vistos.add(email_key)
                emails.append(email)

        if not emails:
            warning = "No se encontraron contactos con correo para esta sucursal."
            self.db.add(
                RegistroCorreoCliente(
                    odt=odt,
                    sucursal=sucursal,
                    observacion=f"[{usuario}] Correo automatico por derivacion a {derivacion_txt}: {warning}",
                    estado=str(getattr(row, "estado", "") or ""),
                )
            )
            self.db.commit()
            return {"ok": False, "emails_enviados": 0, "warning": warning}

        obs_base = (
            str(getattr(row, "observacion_soporte", "") or "").strip()
            or str(getattr(row, "observacion", "") or "").strip()
            or "Se informa actualizacion de la incidencia."
        )
        observacion = (
            f"ODT {odt} derivada a {derivacion_txt}. "
            f"Detalle: {obs_base}"
        )
        asunto, cuerpo, cuerpo_html = self._build_correo_incidencia_cliente_html(
            sucursal=sucursal,
            problema=problema,
            observacion=observacion,
            con_imagenes=False,
        )
        asunto = f"ATC | Derivacion a {derivacion_txt} - {sucursal}"
        logo_atc = self._logo_atc_bytes()

        enviados: set[str] = set()
        errores: list[str] = []
        for email in emails:
            try:
                self._enviar_correo_automatico(
                    email,
                    asunto,
                    cuerpo,
                    html_body=cuerpo_html,
                    logo_bytes=logo_atc,
                )
                enviados.add(email.lower())
            except Exception as exc:
                errores.append(str(exc))

        for contacto in destinos:
            email = str(contacto.get("email") or "").strip()
            if email and email.lower() in enviados:
                estado_correo = "enviado"
            elif email:
                estado_correo = "fallido"
            else:
                estado_correo = "sin correo"
            self.db.add(
                RegistroCorreoCliente(
                    odt=odt,
                    sucursal=sucursal,
                    observacion=(
                        f"[{usuario}] Correo automatico por derivacion a {derivacion_txt}. "
                        f"Contacto: {contacto.get('nombre') or '-'} | Correo: {email or '-'} | "
                        f"Estado correo: {estado_correo}"
                    ),
                    estado=str(getattr(row, "estado", "") or ""),
                )
            )
        self.db.commit()
        return {
            "ok": bool(enviados),
            "emails_enviados": len(enviados),
            "emails_fallidos": max(0, len(emails) - len(enviados)),
            "warning": " | ".join(errores[:3]) if errores else "",
        }

    def _enviar_correo_derivacion_automatico_silencioso(
        self,
        *,
        row: Registro,
        derivacion: str,
        usuario: str,
    ) -> dict[str, Any]:
        try:
            return self._enviar_correo_derivacion_automatico(
                row=row,
                derivacion=derivacion,
                usuario=usuario,
            )
        except Exception as exc:
            LOGGER.exception("No se pudo enviar correo automatico de derivacion para ODT %s.", getattr(row, "odt", ""))
            return {"ok": False, "emails_enviados": 0, "warning": str(exc)}

    def _enviar_correo_automatico(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: str | None = None,
        logo_bytes: bytes | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        cfg = self._smtp_runtime_config()
        if not cfg["enabled"]:
            raise ValueError("El envio automatico de correo esta deshabilitado (SMTP_ENABLED=false).")

        host = str(cfg["host"] or "").strip()
        port = int(cfg["port"] or 0)
        username = str(cfg["username"] or "").strip()
        password = str(cfg["password"] or "")
        from_email = str(cfg["from_email"] or "").strip()
        from_name = str(cfg["from_name"] or "").strip()
        use_tls = bool(cfg["use_tls"])
        use_ssl = bool(cfg["use_ssl"])
        timeout = int(cfg["timeout"] or 20)

        if not host or not port or not from_email:
            raise ValueError("SMTP incompleto. Configura SMTP_HOST, SMTP_PORT y SMTP_FROM_EMAIL (o SMTP_USERNAME).")

        msg = EmailMessage()
        msg["Subject"] = subject
        if from_name:
            msg["From"] = f"{from_name} <{from_email}>"
        else:
            msg["From"] = from_email
        msg["To"] = to_email
        msg.set_content(body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
            if logo_bytes:
                try:
                    html_part = msg.get_payload()[-1]
                    html_part.add_related(
                        logo_bytes,
                        maintype="image",
                        subtype="jpeg",
                        cid="<logoatc>",
                    )
                except Exception:
                    pass
        for item in attachments or []:
            try:
                nombre = str(item.get("nombre") or "adjunto").strip() or "adjunto"
                tipo = str(item.get("tipo") or "application/octet-stream").strip()
                contenido = item.get("contenido") or b""
                if isinstance(contenido, str):
                    import base64

                    contenido = base64.b64decode(contenido)
                maintype, subtype = (tipo.split("/", 1) + ["octet-stream"])[:2] if "/" in tipo else ("application", "octet-stream")
                msg.add_attachment(contenido, maintype=maintype, subtype=subtype, filename=nombre)
            except Exception:
                continue

        try:
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, timeout=timeout) as smtp:
                    if username:
                        smtp.login(username, password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                    smtp.ehlo()
                    if use_tls:
                        smtp.starttls()
                        smtp.ehlo()
                    if username:
                        smtp.login(username, password)
                    smtp.send_message(msg)
        except Exception as exc:
            raise ValueError(f"No se pudo enviar correo automatico a {to_email}: {exc}") from exc

    def registrar_envio_informacion_contacto(
        self, data: EnviarInformacionContactoRequest
    ) -> dict[str, Any]:
        odt = str(data.odt or "").strip()
        sucursal = str(data.sucursal or "").strip()
        if not odt or not sucursal:
            raise ValueError("Debes indicar ODT y sucursal.")

        destinos_validos: list[ContactoDestinoRequest] = []
        for d in list(data.destinos or []):
            email = str(d.email or "").strip()
            telefono = str(d.telefono or "").strip()
            if email or telefono:
                destinos_validos.append(d)
        if not destinos_validos:
            raise ValueError("Debes seleccionar al menos un contacto con correo o telefono.")

        usuario = self.get_usuario_actual(str(data.token or ""))
        usuario = usuario if usuario and usuario != "Desconocido" else "Sistema"
        registro = self.db.scalar(select(Registro).where(Registro.odt == odt).limit(1))
        problema = str(data.problema or (registro.problema if registro else "") or "").strip()
        estado = str(data.estado or (registro.estado if registro else "") or "").strip() or "En Proceso"
        obs_base = str(data.observacion or (registro.observacion if registro else "") or "").strip()
        tecnico = str(data.tecnico or (registro.tecnico if registro else "") or "").strip()
        acompanante = str(data.acompanante or (registro.acompanante if registro else "") or "").strip()

        fecha_visita = self._parse_fecha_visita(data.fecha_visita or "")
        if not fecha_visita and registro and registro.fecha_cierre:
            fecha_visita = registro.fecha_cierre
        if not fecha_visita:
            fecha_visita = datetime.now(ZoneInfo(settings.timezone))

        emails_unicos: list[str] = []
        seen_emails: set[str] = set()
        total_emails = 0
        total_telefonos = 0
        total_destinos = 0
        for destino in destinos_validos:
            nombre = str(destino.nombre or "").strip()
            telefono = str(destino.telefono or "").strip()
            email = str(destino.email or "").strip()
            prioridad = str(destino.prioridad or "").strip()
            total_emails += 1 if email else 0
            total_telefonos += 1 if telefono else 0
            total_destinos += 1

            email_key = email.lower()
            if email and email_key not in seen_emails:
                seen_emails.add(email_key)
                emails_unicos.append(email)

        if not emails_unicos:
            raise ValueError("Por ahora solo esta habilitado correo. Selecciona al menos un contacto con email.")

        asunto, cuerpo, cuerpo_html = self._build_correo_visita_html(
            odt=odt,
            sucursal=sucursal,
            problema=problema,
            estado=estado,
            tecnico=tecnico,
            acompanante=acompanante,
            fecha_visita=fecha_visita,
            observacion=obs_base,
        )
        logo_atc = self._logo_atc_bytes()
        if not logo_atc:
            cuerpo_html = cuerpo_html.replace(
                '<img src="cid:logoatc" alt="ATC" style="height:58px;width:auto;display:block;margin:0 auto 12px;" />',
                "",
            )

        emails_enviados: set[str] = set()
        errores_email: list[str] = []
        for to_email in emails_unicos:
            try:
                self._enviar_correo_automatico(
                    to_email,
                    asunto,
                    cuerpo,
                    html_body=cuerpo_html,
                    logo_bytes=logo_atc,
                )
                emails_enviados.add(to_email.lower())
            except Exception as exc:
                errores_email.append(str(exc))

        for destino in destinos_validos:
            nombre = str(destino.nombre or "").strip()
            telefono = str(destino.telefono or "").strip()
            email = str(destino.email or "").strip()
            prioridad = str(destino.prioridad or "").strip()
            if email and email.lower() in emails_enviados:
                estado_correo = "enviado"
            elif email:
                estado_correo = "fallido"
            else:
                estado_correo = "sin correo"

            partes = [
                f"[{usuario}] Envio de informacion a contacto de cliente.",
                f"Problema: {problema or '-'}",
                f"Contacto: {nombre or '-'}",
                f"Telefono: {telefono or '-'}",
                f"Correo: {email or '-'}",
                f"Prioridad: {prioridad or '-'}",
                f"Estado correo: {estado_correo}",
                "WhatsApp: pendiente API",
                f"Fecha visita enviada: {fecha_visita.strftime('%d/%m/%Y')}",
                f"Tecnico: {tecnico or '-'}",
                f"Acompanante: {acompanante or '-'}",
            ]
            if obs_base:
                partes.append(f"Detalle: {obs_base}")
            observacion_log = " | ".join(partes)
            self.db.add(
                RegistroCorreoCliente(
                    odt=odt,
                    sucursal=sucursal,
                    observacion=observacion_log,
                    estado=estado,
                )
            )

        self.db.commit()
        if not emails_enviados:
            detalle = errores_email[0] if errores_email else "No se pudo enviar ningun correo."
            raise ValueError(detalle)

        return {
            "ok": True,
            "odt": odt,
            "sucursal": sucursal,
            "destinos": total_destinos,
            "emails": total_emails,
            "emails_enviados": len(emails_enviados),
            "emails_fallidos": max(0, len(emails_unicos) - len(emails_enviados)),
            "telefonos": total_telefonos,
            "usuario": usuario,
            "whatsapp_pendiente": total_telefonos,
            "warning": " | ".join(errores_email[:3]) if errores_email else "",
        }

    def _build_correo_incidencia_cliente_html(
        self,
        *,
        sucursal: str,
        problema: str,
        observacion: str,
        con_imagenes: bool,
    ) -> tuple[str, str, str]:
        titulo = "Incidencia Tecnica"
        mensaje = "Se informa una incidencia tecnica detectada."
        problema_key = self._normalizar_texto(problema)
        if problema_key == "desconexion":
            titulo = "Incidencia por Desconexion del Sistema"
            mensaje = "Se ha detectado una desconexion del sistema de monitoreo."
        elif problema_key == "problema de parlante":
            titulo = "Incidencia en Sistema de Audio"
            mensaje = "Se ha informado un inconveniente en el sistema de parlantes."
        elif problema_key == "problema de alarma":
            titulo = "Incidencia en Sistema de Alarma"
            mensaje = "Se ha detectado un problema en el sistema de alarma."
        elif problema_key == "problema de visual":
            titulo = "Incidencia en Sistema de Visualizacion"
            mensaje = "Se ha informado un inconveniente en el sistema visual."
        elif problema_key == "hora y/o fecha cambiada":
            titulo = "Ajuste de Fecha y/u Hora del Sistema"
            mensaje = "Se ha realizado una modificacion en la configuracion de fecha y/u hora."

        detalle_imagenes = "Imagen/es adjunta/s en mail." if con_imagenes else "Sin imagenes adjuntas."
        subject = f"ATC | Atención requerida: {titulo} - {sucursal}"
        text_body = (
            "Estimados/as,\n\n"
            f"{mensaje}\n\n"
            f"Sucursal: {sucursal}\n"
            f"Observacion: {observacion}\n\n"
            "Accion sugerida: revisar la informacion enviada y confirmar recepcion o indicar cualquier antecedente adicional.\n\n"
            "Saludos cordiales,\nEquipo Tecnico\nAlguien Te Cuida"
        )
        html_body = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#eef3f6;font-family:Segoe UI,Arial,sans-serif;color:#17252f;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef3f6;padding:32px 14px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;background:#ffffff;border:1px solid #d8e3ea;border-radius:14px;overflow:hidden;box-shadow:0 14px 34px rgba(15,48,72,0.12);">
            <tr>
              <td style="background:#0f3048;padding:14px 24px;color:#ffffff;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">
                Atención requerida | Alguien Te Cuida
              </td>
            </tr>
            <tr>
              <td style="padding:24px 28px 14px;text-align:center;">
                <img src="cid:logoatc" alt="ATC" style="height:54px;width:auto;display:block;margin:0 auto 14px;">
                <div style="display:inline-block;margin:0 0 12px;padding:6px 12px;border-radius:999px;background:#fff1e6;color:#b94a00;border:1px solid #ffd2ad;font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;">
                  Información importante para revisión
                </div>
                <div style="font-size:24px;line-height:1.25;font-weight:800;color:#0f3048;">{html_escape(titulo)}</div>
                <div style="margin-top:8px;font-size:14px;color:#607483;">Notificacion operacional a cliente</div>
              </td>
            </tr>
            <tr>
              <td style="padding:0 28px 6px;">
                <div style="background:#fff8f1;border:1px solid #ffd9b5;border-left:6px solid #ff8b33;border-radius:12px;padding:14px 16px;color:#3b2a1c;">
                  <div style="font-size:13px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:#9b4300;margin-bottom:6px;">Acción sugerida</div>
                  <div style="font-size:15px;line-height:1.55;">Favor revisar esta notificación y confirmar recepción, o responder este correo si existe información adicional que debamos considerar.</div>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 28px 28px;">
                <p style="margin:0 0 14px;font-size:16px;line-height:1.65;color:#243746;">Estimados/as,</p>
                <p style="margin:0 0 18px;font-size:16px;line-height:1.65;color:#243746;">{html_escape(mensaje)}</p>

                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 18px;border:1px solid #dce7ee;border-radius:10px;overflow:hidden;">
                  <tr>
                    <td style="padding:12px 14px;background:#f7fafc;color:#607483;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;">Sucursal</td>
                    <td style="padding:12px 14px;background:#ffffff;color:#17252f;font-size:15px;font-weight:700;text-align:right;">{html_escape(sucursal)}</td>
                  </tr>
                  <tr>
                    <td style="padding:12px 14px;background:#f7fafc;color:#607483;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;border-top:1px solid #dce7ee;">Incidencia</td>
                    <td style="padding:12px 14px;background:#ffffff;color:#17252f;font-size:15px;font-weight:700;text-align:right;border-top:1px solid #dce7ee;">{html_escape(problema or titulo)}</td>
                  </tr>
                </table>

                <div style="margin:0 0 18px;background:#f5f8fb;border:1px solid #dbe6ee;border-left:5px solid #ff8b33;border-radius:10px;padding:16px 18px;">
                  <div style="margin:0 0 8px;color:#0f3048;font-size:13px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;">Observación</div>
                  <div style="color:#243746;font-size:16px;line-height:1.65;">{html_escape(observacion).replace(chr(10), "<br>")}</div>
                  <div style="margin-top:12px;color:#667887;font-size:13px;font-style:italic;">{html_escape(detalle_imagenes)}</div>
                </div>

                <p style="margin:0 0 18px;font-size:16px;line-height:1.65;color:#243746;">Quedamos atentos a sus comentarios y ante cualquier solicitud adicional.</p>
                <p style="margin:0;font-size:16px;line-height:1.65;color:#243746;">
                  Saludos cordiales,<br>
                  <strong style="color:#0f3048;">Equipo Tecnico</strong><br>
                  Alguien Te Cuida
                </p>
              </td>
            </tr>
            <tr>
              <td style="border-top:1px solid #e2e8f0;padding:14px 24px;text-align:center;font-size:12px;color:#718293;background:#fafbfd;">
                Este mensaje ha sido generado automaticamente por el sistema de Alguien Te Cuida.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
        return subject, text_body, html_body

    def registrar_envio_correo_coordinacion(
        self, data: EnviarInformacionContactoRequest
    ) -> dict[str, Any]:
        odt = str(data.odt or "").strip()
        sucursal = str(data.sucursal or "").strip()
        if not odt or not sucursal:
            raise ValueError("Debes indicar ODT y sucursal.")

        destinos = [
            d
            for d in list(data.destinos or [])
            if str(d.email or "").strip() or str(d.telefono or "").strip()
        ]
        if not destinos:
            contactos = self.obtener_contactos_por_sucursal()
            sucursal_key = next(
                (k for k in contactos.keys() if self._normalizar_texto(k) == self._normalizar_texto(sucursal)),
                "",
            )
            destinos = [
                ContactoDestinoRequest(**c)
                for c in (contactos.get(sucursal_key) or [])
                if str(c.get("email") or "").strip() or str(c.get("telefono") or "").strip()
            ]
        if not destinos:
            raise ValueError("No se encontraron contactos para esta sucursal.")

        emails_unicos: list[str] = []
        seen_emails: set[str] = set()
        for destino in destinos:
            email = str(destino.email or "").strip()
            email_key = email.lower()
            if email and email_key not in seen_emails:
                seen_emails.add(email_key)
                emails_unicos.append(email)
        if not emails_unicos:
            raise ValueError("Por ahora solo esta habilitado correo. Selecciona al menos un contacto con email.")

        registro = self.db.scalar(select(Registro).where(Registro.odt == odt).limit(1))
        problema = str(data.problema or (registro.problema if registro else "") or "").strip()
        observacion = str(data.observacion or (registro.observacion if registro else "") or "").strip()
        if not observacion:
            raise ValueError("La observacion no puede estar vacia.")

        imagenes = [
            img
            for img in list(data.imagenes or [])
            if str(img.get("contenido") or "").strip()
        ]
        asunto, cuerpo, cuerpo_html = self._build_correo_incidencia_cliente_html(
            sucursal=sucursal,
            problema=problema,
            observacion=observacion,
            con_imagenes=bool(imagenes),
        )
        logo_atc = self._logo_atc_bytes()
        emails_enviados: set[str] = set()
        errores: list[str] = []
        for email in emails_unicos:
            email_key = email.lower()
            if email_key in emails_enviados:
                continue
            try:
                self._enviar_correo_automatico(
                    email,
                    asunto,
                    cuerpo,
                    html_body=cuerpo_html,
                    logo_bytes=logo_atc,
                    attachments=imagenes,
                )
                emails_enviados.add(email_key)
            except Exception as exc:
                errores.append(str(exc))

        usuario = self.get_usuario_actual(str(data.token or ""))
        usuario = usuario if usuario and usuario != "Desconocido" else "Sistema"
        for destino in destinos:
            nombre = str(destino.nombre or "").strip()
            telefono = str(destino.telefono or "").strip()
            email = str(destino.email or "").strip()
            if email and email.lower() in emails_enviados:
                estado_correo = "enviado"
            elif email:
                estado_correo = "fallido"
            else:
                estado_correo = "sin correo"
            self.db.add(
                RegistroCorreoCliente(
                    odt=odt,
                    sucursal=sucursal,
                    observacion=(
                        f"[{usuario}] Envio de incidencia a cliente. Problema: {problema or '-'} | "
                        f"Contacto: {nombre or '-'} | Telefono: {telefono or '-'} | "
                        f"Correo: {email or '-'} | Estado correo: {estado_correo} | Detalle: {observacion}"
                        " | WhatsApp: Proximamente"
                    ),
                    estado=str(data.estado or (registro.estado if registro else "") or ""),
                )
            )
        self.db.commit()
        if not emails_enviados:
            raise ValueError(errores[0] if errores else "No se pudo enviar ningun correo.")
        return {
            "ok": True,
            "odt": odt,
            "sucursal": sucursal,
            "emails_enviados": len(emails_enviados),
            "emails_fallidos": max(0, len(emails_unicos) - len(emails_enviados)),
            "telefonos": sum(1 for d in destinos if str(d.telefono or "").strip()),
            "whatsapp_pendiente": sum(1 for d in destinos if str(d.telefono or "").strip()),
            "warning": " | ".join(errores[:3]) if errores else "",
        }

    def obtener_resumen_correos_por_odt(self) -> dict[str, dict[str, Any]]:
        stmt = (
            select(
                RegistroCorreoCliente.odt,
                func.count(RegistroCorreoCliente.id),
                func.max(RegistroCorreoCliente.fecha_envio),
            )
            .group_by(RegistroCorreoCliente.odt)
            .order_by(RegistroCorreoCliente.odt)
        )
        return {
            odt: {"total": total, "ultimo_envio": ultimo_envio}
            for odt, total, ultimo_envio in self.db.execute(stmt).all()
        }

    def obtener_cantidad_correos_por_odt(self) -> dict[str, int]:
        return {
            odt: int(info.get("total") or 0)
            for odt, info in self.obtener_resumen_correos_por_odt().items()
        }

    def obtener_registros_derivaciones(self) -> list[list[Any]]:
        correos = self.obtener_resumen_correos_por_odt()
        rows = self.db.scalars(select(Registro).order_by(Registro.id.desc())).all()
        out: list[list[Any]] = []
        for r in rows:
            correo_info = correos.get(r.odt, {})
            out.append(
                [
                    r.odt,
                    _to_ddmmyyyy_hhmm(r.fecha_registro),
                    r.cliente,
                    r.problema,
                    r.derivacion,
                    r.observacion,
                    r.estado,
                    r.observacion_final,
                    int(correo_info.get("total") or 0),
                    getattr(r, "observacion_soporte", "") or "",
                    getattr(r, "observacion_servicio", "") or "",
                    _to_ddmmyyyy_hhmm(correo_info.get("ultimo_envio")),
                ]
            )
        return out

    # =========================
    # TAREAS
    # =========================
    def registrar_tarea_manual(self, data: TareaManualRequest) -> str:
        usuario = self.get_usuario_actual(data.token)
        if usuario == "Desconocido":
            raise ValueError("SesiÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n invÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡lida o expirada")

        ultimo = self.db.scalar(select(Tarea).order_by(Tarea.id.desc()))
        siguiente = 1
        if ultimo and ultimo.codigo:
            match = re.match(r"T-(\d+)$", ultimo.codigo)
            if match:
                siguiente = int(match.group(1)) + 1
        codigo = f"T-{siguiente}"

        ahora = datetime.utcnow()
        finalizada = data.estado.strip().lower() == "finalizado"
        tarea = Tarea(
            codigo=codigo,
            usuario_soporte=usuario,
            fecha_creacion=ahora,
            cliente=data.cliente,
            tipo_tarea=data.tipo_tarea,
            especificacion=data.especificacion,
            descripcion=data.descripcion,
            solicitante=data.solicitante,
            estado=data.estado,
            tecnico_cierre=usuario if finalizada else None,
            fecha_cierre=ahora if finalizada else None,
            dias_ejecucion=0 if finalizada else None,
        )
        self.db.add(tarea)
        self.db.commit()
        return codigo

    def obtener_registro_tareas(self) -> list[list[str]]:
        rows = self.db.scalars(select(Tarea).order_by(Tarea.id.desc())).all()
        out: list[list[str]] = []
        for t in rows:
            out.append(
                [
                    t.codigo,
                    t.usuario_soporte,
                    _to_ddmmyyyy_hhmm(t.fecha_creacion),
                    t.cliente,
                    t.tipo_tarea,
                    t.especificacion,
                    t.descripcion,
                    t.solicitante or "",
                    t.estado,
                    t.tecnico_cierre or "",
                    _to_ddmmyyyy_hhmm(t.fecha_cierre),
                    str(t.dias_ejecucion or ""),
                ]
            )
        return out

    def actualizar_celda_tarea(self, fila_id: int, columna: str, valor: str, token: str) -> bool:
        tarea = self.db.get(Tarea, fila_id)
        if not tarea:
            return False

        columnas_validas = {
            "cliente",
            "tipo_tarea",
            "especificacion",
            "descripcion",
            "solicitante",
            "estado",
        }
        if columna not in columnas_validas:
            raise ValueError(f"Columna invÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡lida: {columna}")
        setattr(tarea, columna, valor)

        if columna == "estado" and valor == "Finalizado" and not tarea.fecha_cierre:
            ahora = datetime.utcnow()
            tarea.tecnico_cierre = self.get_usuario_actual(token)
            tarea.fecha_cierre = ahora
            tarea.dias_ejecucion = (ahora.date() - tarea.fecha_creacion.date()).days

        self.db.commit()
        return True

    # =========================
    # RENDICIONES
    # =========================
    def _folio_siguiente(self) -> int:
        ultimo = self.db.scalar(select(func.max(Rendicion.folio)))
        return int(ultimo or 0) + 1

    def existe_nro_documento_duplicado(self, nro_documento: str) -> bool:
        if not nro_documento:
            return False
        stmt = select(Rendicion).where(
            Rendicion.nro_documento == nro_documento.strip(),
            Rendicion.estado_revision != "Rechazado",
        )
        return self.db.scalar(stmt) is not None

    def guardar_boleta_rendicion(
        self,
        *,
        content: bytes,
        filename: str,
        tecnico: str = "",
        odt: str = "",
    ) -> str:
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise ValueError("Archivo de boleta vacio.")

        nombre_original = str(filename or "boleta.jpg").strip() or "boleta.jpg"
        ext = Path(nombre_original).suffix.lower()
        permitidas = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif"}
        if ext not in permitidas:
            raise ValueError("Formato de imagen no permitido para boleta.")

        odt_seguro = re.sub(r"[^A-Za-z0-9_-]+", "", str(odt or "").strip().upper()) or "SIN_ODT"
        tecnico_seguro = re.sub(r"[^A-Za-z0-9_-]+", "", str(tecnico or "").strip().upper()) or "SIN_TECNICO"
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique = uuid.uuid4().hex[:10]
        nombre_final = f"{ts}_{odt_seguro}_{tecnico_seguro}_{unique}{ext}"

        static_dir = Path(__file__).resolve().parent / "static" / "rendiciones"
        static_dir.mkdir(parents=True, exist_ok=True)
        destino = static_dir / nombre_final
        destino.write_bytes(bytes(content))
        return f"/static/rendiciones/{nombre_final}"

    def registrar_gasto(self, data: RendicionRequest, mail_tecnico: str = "") -> dict[str, Any]:
        if self.existe_nro_documento_duplicado(data.nro_documento):
            raise ValueError(f"El Nro de Documento {data.nro_documento} ya fue registrado.")

        folio = self._folio_siguiente()
        dia_excel = int((data.fecha_documento.date() - datetime(1899, 12, 30).date()).days)
        iniciales = "".join([w[:1].upper() for w in data.tecnico.split() if w])
        tipo_id = re.sub(r"\s+", "", data.tipo_gasto).upper()
        codigo_diario = f"{dia_excel}-{iniciales}-{tipo_id}"

        rend = Rendicion(
            folio=folio,
            codigo_diario=codigo_diario,
            tecnico=data.tecnico,
            mail=mail_tecnico,
            odt=data.odt.upper().strip(),
            cliente=data.cliente.strip(),
            tipo_gasto=data.tipo_gasto,
            tipo_documento=data.tipo_documento,
            nro_documento=data.nro_documento.strip(),
            fecha_documento=data.fecha_documento,
            monto_total=data.monto_total,
            descripcion=data.descripcion or "",
            documento=f"{data.tipo_documento} {data.nro_documento}",
            url_boleta=str(data.url_boleta or "").strip() or None,
        )
        self.db.add(rend)
        self.db.commit()
        return {"folio": folio, "codigoDiario": codigo_diario}

    def obtener_rendiciones(
        self,
        tecnico: str = "",
        pendientes_only: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self.db.scalars(select(Rendicion).order_by(Rendicion.id.desc())).all()
        out: list[dict[str, Any]] = []
        tecnico_norm = self._normalizar_texto(tecnico or "")
        for r in rows:
            tecnico_row = str(r.tecnico or "").strip()
            if tecnico_norm and self._normalizar_texto(tecnico_row) != tecnico_norm:
                continue

            estado_revision = str(r.estado_revision or "").strip() or "Pendiente"
            estado_norm = self._normalizar_texto(estado_revision)
            es_pendiente = "acept" not in estado_norm and "rechaz" not in estado_norm
            if pendientes_only and not es_pendiente:
                continue

            out.append(
                {
                    "folio": r.folio,
                    "codigoDiario": r.codigo_diario,
                    "fechaRegistro": _to_ddmmyyyy_hhmm(r.fecha_registro),
                    "tecnico": tecnico_row,
                    "mail": r.mail,
                    "odt": r.odt,
                    "cliente": r.cliente,
                    "comuna": r.comuna,
                    "tipoGasto": r.tipo_gasto,
                    "tipoDocumento": r.tipo_documento,
                    "nroDocumento": r.nro_documento,
                    "fechaDocumento": _to_ddmmyyyy(r.fecha_documento),
                    "montoTotal": float(r.monto_total),
                    "descripcion": r.descripcion,
                    "urlBoleta": r.url_boleta,
                    "urlInforme": r.url_informe,
                    "estadoRevision": estado_revision,
                }
            )
        return out

    def marcar_rendicion(self, folio: int, accion: str) -> bool:
        rend = self.db.scalar(select(Rendicion).where(Rendicion.folio == folio))
        if not rend:
            return False
        if accion == "aceptar":
            rend.estado_revision = "Aceptado"
        elif accion == "rechazar":
            rend.estado_revision = "Rechazado"
        else:
            raise ValueError("AcciÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â³n invÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¡lida. Debe ser 'aceptar' o 'rechazar'.")
        self.db.commit()
        return True

    # =========================
    # PLANIFICACIÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œN
    # =========================
    def obtener_planificacion_total(
        self,
        mes: int,
        anio: int,
        estado: str = "Todos",
        tecnico: str = "Todos",
    ) -> dict[str, list[dict[str, Any]]]:
        resultado: dict[str, list[dict[str, Any]]] = {}

        stmt_inc = select(Registro).where(
            func.extract("month", Registro.fecha_derivacion_area) == mes,
            func.extract("year", Registro.fecha_derivacion_area) == anio,
        )
        if estado != "Todos":
            stmt_inc = stmt_inc.where(Registro.estado == estado)
        if tecnico != "Todos":
            stmt_inc = stmt_inc.where(
                or_(Registro.tecnicos == tecnico, Registro.acompanante == tecnico)
            )

        for row in self.db.scalars(stmt_inc).all():
            fecha = _to_ddmmyyyy(row.fecha_derivacion_area)
            resultado.setdefault(fecha, []).append(
                {
                    "odt": row.odt,
                    "cliente": row.cliente,
                    "direccion": row.direccion,
                    "servicio": row.problema,
                    "tecnico": row.tecnicos,
                    "acompanante": row.acompanante,
                    "estado": row.estado,
                    "origen": "incidencias",
                }
            )

        stmt_ven = select(OdtVenta, AdministracionODT).join(
            AdministracionODT, AdministracionODT.odt == OdtVenta.odt, isouter=True
        )
        for odt_row, adm_row in self.db.execute(stmt_ven).all():
            if not odt_row.fecha:
                continue
            if odt_row.fecha.month != mes or odt_row.fecha.year != anio:
                continue
            tecnico_venta = (adm_row.tecnico if adm_row else None) or odt_row.tecnico
            if tecnico != "Todos" and tecnico_venta != tecnico:
                continue
            fecha = _to_ddmmyyyy(odt_row.fecha)
            resultado.setdefault(fecha, []).append(
                {
                    "odt": odt_row.odt,
                    "cliente": odt_row.cliente,
                    "direccion": odt_row.direccion,
                    "servicio": odt_row.servicio,
                    "tecnico": tecnico_venta,
                    "acompanante": (adm_row.acompanante if adm_row else None),
                    "estado": odt_row.estado,
                    "origen": "ventas",
                }
            )
        return resultado
