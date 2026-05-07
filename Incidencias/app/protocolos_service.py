from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.drive_report_service import (
    DriveReportError,
    create_protocol_individual_report_pdf,
    create_protocol_weekly_report_pdf,
)
from app.models import LoginSession, ProtocoloInforme, ProtocoloRegistro, Registro
from app.schemas import ProtocoloRegistroCreateRequest

LOGGER = logging.getLogger(__name__)


@dataclass
class RangoFechas:
    inicio: datetime
    fin: datetime
    texto_inicio: str
    texto_fin: str
    etiqueta_mes: str = ""
    modo: str = ""


def _normalizar_clave_nombre(valor: str | None) -> str:
    txt = str(valor or "").strip().lower()
    if not txt:
        return ""
    txt = "".join(ch for ch in unicodedata.normalize("NFD", txt) if unicodedata.category(ch) != "Mn")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


ENCARGADO_GRUPO = {
    _normalizar_clave_nombre("Mery Delgado"): "Grupo B",
    _normalizar_clave_nombre("Cristian Olivares"): "Grupo B",
    _normalizar_clave_nombre("HÃ©ctor Rosales"): "Coordinador",
    _normalizar_clave_nombre("AngÃ©lica Guerra"): "Grupo A",
    _normalizar_clave_nombre("Nicolas SantibaÃ±ez"): "Grupo PT",
    _normalizar_clave_nombre("Daisy Vergara"): "Grupo PT",
    _normalizar_clave_nombre("Tahira Riquelme"): "Grupo Diurno",
    _normalizar_clave_nombre("Marian Macho"): "Grupo A",
    _normalizar_clave_nombre("Manuel Mondaca"): "Grupo PT",
}

PROMPT_FORMALIZAR_OBSERVACION = (
    "Necesito que de la observacion, corrijas todo lo que no este bien escrito y las tildes que falten en el texto, "
    "y lo redactes de manera mas formal, sin perder los tecnicismos. "
    "No tienes que corregir las palabras OP, cam, hrs. , GGSS.\n"
    "Reglas adicionales:\n"
    "- No inventes informacion.\n"
    "- No limites ni simplifiques el contenido tecnico.\n"
    "- No cambies nombres propios, codigos, ODT, numeros, fechas ni horas.\n"
    "- Conserva exactamente OP, cam, hrs. y GGSS.\n"
    "- Devuelve solo el texto corregido, sin comillas ni explicaciones."
)


class ProtocolosService:
    """MigraciÃ³n base de Control de Protocolos (Apps Script -> Python).

    Objetivo inmediato:
    - dejar registro SQL centralizado
    - automatizar rangos de fechas y resÃºmenes
    - normalizar/redactar observaciones de forma automÃ¡tica (sin hardcode manual en frontend)
    """

    def __init__(self, db: Session):
        self.db = db
        self.tz = ZoneInfo(settings.timezone or "America/Santiago")

    # =========================
    # Utilidades de fecha
    # =========================
    def _fmt(self, dt: datetime, pattern: str = "%d/%m/%Y") -> str:
        return dt.astimezone(self.tz).strftime(pattern)

    def _dt_bounds(self, d: date) -> tuple[datetime, datetime]:
        start = datetime.combine(d, time.min, tzinfo=self.tz)
        end = datetime.combine(d, time.max, tzinfo=self.tz)
        return start, end

    def obtener_rango_hoy(self) -> RangoFechas:
        now = datetime.now(self.tz)
        inicio, fin = self._dt_bounds(now.date())
        return RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin))

    def obtener_rango_semana_actual(self) -> RangoFechas:
        now = datetime.now(self.tz)
        weekday = now.weekday()  # lunes=0
        lunes = (now - timedelta(days=weekday)).date()
        domingo = lunes + timedelta(days=6)
        inicio, _ = self._dt_bounds(lunes)
        _, fin = self._dt_bounds(domingo)
        return RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin))

    def obtener_rango_semana_anterior(self) -> RangoFechas:
        actual = self.obtener_rango_semana_actual()
        lunes_anterior = (actual.inicio - timedelta(days=7)).date()
        domingo_anterior = lunes_anterior + timedelta(days=6)
        inicio, _ = self._dt_bounds(lunes_anterior)
        _, fin = self._dt_bounds(domingo_anterior)
        return RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin))

    def obtener_rango_mes_actual(self) -> RangoFechas:
        now = datetime.now(self.tz)
        first_day = date(now.year, now.month, 1)
        if now.month == 12:
            next_month = date(now.year + 1, 1, 1)
        else:
            next_month = date(now.year, now.month + 1, 1)
        last_day = next_month - timedelta(days=1)
        inicio, _ = self._dt_bounds(first_day)
        _, fin = self._dt_bounds(last_day)
        return RangoFechas(
            inicio,
            fin,
            self._fmt(inicio),
            self._fmt(fin),
            etiqueta_mes=now.strftime("%m/%Y"),
        )

    def obtener_rango_para_diarios(self) -> RangoFechas:
        now = datetime.now(self.tz)
        # Mantiene lÃ³gica Apps Script: lunes -> semana anterior cerrada; resto -> hoy.
        if now.weekday() == 0:
            r = self.obtener_rango_semana_anterior()
            r.modo = "SEMANA_ANTERIOR"
            return r
        r = self.obtener_rango_hoy()
        r.modo = "HOY"
        return r

    def parsear_fecha(self, valor: str | datetime | None) -> datetime | None:
        if valor is None:
            return None
        if isinstance(valor, datetime):
            return valor

        txt = str(valor).strip()
        if not txt:
            return None

        # dd/MM/yyyy [HH:mm[:ss]]
        m = re.match(r"^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?$", txt)
        if m:
            dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh = int(m.group(4) or 0)
            mi = int(m.group(5) or 0)
            ss = int(m.group(6) or 0)
            try:
                return datetime(yyyy, mm, dd, hh, mi, ss, tzinfo=self.tz)
            except Exception:
                return None

        # fallback ISO
        try:
            dt = datetime.fromisoformat(txt)
            return dt if dt.tzinfo else dt.replace(tzinfo=self.tz)
        except Exception:
            return None

    # =========================
    # IA/normalizaciÃ³n de texto (versiÃ³n backend)
    # =========================
    def _normalizar_si_no(self, value: str | None) -> str:
        txt = str(value or "").strip().lower()
        if txt in {"si", "sÃ­", "s", "yes", "y", "1", "true"}:
            return "SI"
        if txt in {"no", "n", "0", "false"}:
            return "NO"
        return str(value or "").strip().upper()

    def _capitalizar_oraciones(self, text: str) -> str:
        out: list[str] = []
        for part in re.split(r"([.!?]\s+)", text):
            if not part:
                continue
            if re.match(r"[.!?]\s+", part):
                out.append(part)
                continue
            stripped = part.strip()
            if not stripped:
                out.append(part)
                continue
            out.append(stripped[0].upper() + stripped[1:])
        return "".join(out).strip()

    def formalizar_observacion(self, observacion: str | None) -> str:
        txt = str(observacion or "").strip()
        if not txt:
            return ""

        if settings.ia_formalizador_enabled:
            try:
                return self._formalizar_observacion_con_ia(txt)
            except Exception as exc:
                LOGGER.warning("Falla IA formalizacion: %s", exc)
                if settings.ia_formalizador_strict:
                    raise ValueError(f"Error en formalizacion con IA: {exc}") from exc

        return self.formalizar_observacion_mejorada(txt)

    def _preservar_tokens_operativos(self, text: str) -> tuple[str, dict[str, str]]:
        placeholders: dict[str, str] = {}
        reglas = [r"\bOP\b", r"\bcam\b", r"\bhrs\.?(?=\s|,|$)", r"\bGGSS\b"]
        idx = 0
        txt = text

        for patron in reglas:
            def _repl(match: re.Match[str]) -> str:
                nonlocal idx
                key = f"__TOK_OP_{idx}__"
                idx += 1
                placeholders[key] = match.group(0)
                return key

            txt = re.sub(patron, _repl, txt, flags=re.IGNORECASE)
        return txt, placeholders

    def _restaurar_tokens_operativos(self, text: str, placeholders: dict[str, str]) -> str:
        txt = text
        for key, value in placeholders.items():
            txt = txt.replace(key, value)
        return txt

    def formalizar_observacion_mejorada(self, observacion: str | None) -> str:
        txt = str(observacion or "").strip()
        if not txt:
            return ""

        txt = re.sub(r"\s+", " ", txt).strip()
        txt, placeholders = self._preservar_tokens_operativos(txt)

        reemplazos = [
            ("revicion", "revisiÃ³n"),
            ("revison", "revisiÃ³n"),
            ("corecto", "correcto"),
            ("conjelada", "congelada"),
            ("imgen", "imagen"),
            ("conecion", "conexiÃ³n"),
            ("conexion", "conexiÃ³n"),
            ("monitoro", "monitoreo"),
            ("camara", "cÃ¡mara"),
            ("camaras", "cÃ¡maras"),
            ("tecnico", "tÃ©cnico"),
            ("tecnicos", "tÃ©cnicos"),
            ("observacion", "observaciÃ³n"),
            ("mas", "mÃ¡s"),
            ("nvr", "NVR"),
            ("dvr", "DVR"),
        ]
        for origen, destino in reemplazos:
            txt = re.sub(rf"\b{origen}\b", destino, txt, flags=re.IGNORECASE)

        txt = re.sub(r"\s+([,.;:!?])", r"\1", txt)
        txt = re.sub(r"([,;.!?])(?!\s|$)", r"\1 ", txt)
        txt = re.sub(r"(?<!\d):(?!\s|$)", r": ", txt)
        txt = re.sub(r"\s{2,}", " ", txt).strip()

        txt = self._capitalizar_oraciones(txt)
        if txt and txt[-1] not in ".!?":
            txt += "."
        txt = self._restaurar_tokens_operativos(txt, placeholders)
        txt = re.sub(r"\bhrs\.\s+MÃ¡s\b", "hrs. mÃ¡s", txt, flags=re.IGNORECASE)
        return txt

    def construir_prompt_formalizacion_observacion(self, observacion: str | None) -> str:
        """Prompt base para integrar un modelo IA de correccion/formalizacion."""
        return (
            f"{PROMPT_FORMALIZAR_OBSERVACION}\n\n"
            f"Observacion original:\n{str(observacion or '').strip()}"
        )

    def _extraer_texto_chat_completion(self, payload: dict[str, object]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            partes: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    partes.append(text.strip())
            return "\n".join(partes).strip()
        return ""

    def _formalizar_observacion_con_ia(self, observacion: str) -> str:
        api_key = str(settings.openai_api_key or "").strip()
        if not api_key:
            raise ValueError("Falta OPENAI_API_KEY para formalizacion con IA.")

        base_url = str(settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        model = str(settings.openai_model_formalizador or "gpt-4.1-mini").strip() or "gpt-4.1-mini"
        timeout_sec = max(5, int(settings.openai_timeout_sec or 25))

        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": PROMPT_FORMALIZAR_OBSERVACION},
                {"role": "user", "content": str(observacion).strip()},
            ],
        }

        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout_sec)
        except requests.RequestException as exc:
            raise ValueError(f"Error de red llamando a IA: {exc}") from exc

        if resp.status_code >= 400:
            detail = ""
            try:
                data = resp.json()
                detail = str(data.get("error", {}).get("message") or data)
            except Exception:
                detail = (resp.text or "").strip()
            raise ValueError(f"IA respondio {resp.status_code}: {detail or 'sin detalle'}")

        data = resp.json()
        out = self._extraer_texto_chat_completion(data)
        if not out:
            raise ValueError("La IA no devolvio contenido de texto util.")
        return out

    # =========================
    # Listas para formulario
    # =========================
    def obtener_listas(self) -> dict[str, object]:
        encargados_set: set[str] = set()
        grupos: dict[str, str] = {}
        clientes_set: set[str] = set()
        sucursales_set: set[str] = set()
        cliente_sucursales: dict[str, set[str]] = defaultdict(set)
        operadores_set: set[str] = set()

        # Desde histÃ³ricos de protocolos
        rows = self.db.scalars(select(ProtocoloRegistro).order_by(ProtocoloRegistro.id.desc()).limit(5000)).all()
        for r in rows:
            if r.encargado:
                encargados_set.add(r.encargado.strip())
                if r.grupo:
                    grupos.setdefault(r.encargado.strip(), r.grupo.strip())
            if r.operador:
                operadores_set.add(r.operador.strip())

        # Desde base actual del sistema (solo operadores/usuarios).
        for value in self.db.scalars(select(Registro.tecnicos)).all():
            if value:
                operadores_set.add(str(value).strip())
        for value in self.db.scalars(select(Registro.acompanante)).all():
            if value:
                operadores_set.add(str(value).strip())

        # Usuarios con sesiÃ³n
        for value in self.db.scalars(select(LoginSession.usuario)).all():
            if value:
                encargados_set.add(str(value).strip())
                operadores_set.add(str(value).strip())

        # Fuente principal solicitada: catalogo_clientes(nombre_cliente, nombre_sucursal).
        try:
            bind = self.db.get_bind()
            dialect = (bind.dialect.name if bind is not None else "").lower()
            if dialect == "sqlite":
                stmt_cat = text(
                    """
                    SELECT nombre_cliente, nombre_sucursal
                    FROM catalogo_clientes
                    WHERE nombre_cliente IS NOT NULL
                      AND TRIM(nombre_cliente) <> ''
                      AND nombre_sucursal IS NOT NULL
                      AND TRIM(nombre_sucursal) <> ''
                    """
                )
            else:
                schema_name = (settings.db_schema or "public").strip() or "public"
                stmt_cat = text(
                    f"""
                    SELECT nombre_cliente, nombre_sucursal
                    FROM "{schema_name}"."catalogo_clientes"
                    WHERE nombre_cliente IS NOT NULL
                      AND btrim(CAST(nombre_cliente AS text)) <> ''
                      AND nombre_sucursal IS NOT NULL
                      AND btrim(CAST(nombre_sucursal AS text)) <> ''
                    """
                )
            rows_cat = self.db.execute(stmt_cat).all()
            clientes_set.clear()
            sucursales_set.clear()
            cliente_sucursales.clear()
            for nombre_cliente, nombre_sucursal in rows_cat:
                c = str(nombre_cliente or "").strip()
                s = str(nombre_sucursal or "").strip()
                if not c or not s:
                    continue
                clientes_set.add(c)
                sucursales_set.add(s)
                cliente_sucursales[c].add(s)
        except Exception as exc:
            LOGGER.warning(
                "No fue posible cargar cliente/sucursal desde catalogo_clientes(nombre_cliente,nombre_sucursal): %s",
                exc,
            )

        def _sort(vals: set[str]) -> list[str]:
            return sorted((v for v in vals if v), key=lambda x: x.lower())

        encargados = _sort(encargados_set)
        clientes = _sort(clientes_set)
        sucursales = _sort(sucursales_set)
        operadores = _sort(operadores_set)
        grupos_final = {k: v for k, v in grupos.items() if k and v}

        return {
            "encargados": encargados,
            "grupos": grupos_final,
            "clientes": clientes,
            "sucursales": sucursales,
            "cliente_sucursales": {
                cliente: sorted(vals, key=lambda x: x.lower())
                for cliente, vals in sorted(cliente_sucursales.items(), key=lambda x: x[0].lower())
                if cliente and vals
            },
            "operadores": operadores,
        }

    # =========================
    # Registro
    # =========================
    def _usuario_por_token(self, token: str | None) -> str:
        tk = str(token or "").strip()
        if not tk:
            return ""
        now = datetime.utcnow()
        sesion = self.db.scalar(
            select(LoginSession).where(LoginSession.token == tk, LoginSession.expires_at > now).limit(1)
        )
        return str(sesion.usuario or "").strip() if sesion else ""

    def _grupo_por_encargado(self, encargado: str) -> str:
        key = _normalizar_clave_nombre(encargado)
        grupo = ENCARGADO_GRUPO.get(key, "").strip()
        if grupo:
            return grupo

        # Fallback: ultimo grupo historico usado por ese encargado.
        ultimo_grupo = self.db.scalar(
            select(ProtocoloRegistro.grupo)
            .where(
                ProtocoloRegistro.encargado.is_not(None),
                func.lower(ProtocoloRegistro.encargado) == str(encargado or "").strip().lower(),
                ProtocoloRegistro.grupo.is_not(None),
                ProtocoloRegistro.grupo != "",
            )
            .order_by(ProtocoloRegistro.id.desc())
            .limit(1)
        )
        return str(ultimo_grupo or "").strip()

    def _dt_db(self, dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(self.tz).replace(tzinfo=None)

    def _valor_si_no(self, valor: str | None) -> str:
        txt = str(valor or "").strip().upper()
        return txt if txt in {"SI", "NO"} else "-"

    def _resumen_ejecutivo_individual(self, row: ProtocoloRegistro) -> str:
        checks = [
            row.detectado,
            row.efectivo,
            row.sirena,
            row.voz,
            row.carabineros,
            row.alpha3,
            row.informado,
            row.bitacora,
            row.protocolo_exitoso,
        ]
        total_si = sum(1 for c in checks if str(c or "").strip().upper() == "SI")
        return (
            f"Se registro un protocolo {row.tipo_protocolo or '-'} para la sucursal {row.sucursal}. "
            f"El checklist operativo obtuvo {total_si} respuestas afirmativas de {len(checks)} variables. "
            f"Este informe consolida antecedentes tecnicos, observacion original y su redaccion formalizada."
        )

    def _guardar_informe_error(self, informe: ProtocoloInforme, error_text: str) -> None:
        informe.estado = "ERROR"
        informe.error_detalle = str(error_text or "Error no especificado")
        self.db.add(informe)
        self.db.commit()

    def _lanzar_generacion_informes_async(
        self,
        *,
        registro_id: int,
        cliente: str,
        sucursal: str,
        lanzar_semanal: bool,
    ) -> None:
        def _worker() -> None:
            db = SessionLocal()
            try:
                service = ProtocolosService(db)
                row = db.scalar(
                    select(ProtocoloRegistro).where(ProtocoloRegistro.id == int(registro_id)).limit(1)
                )
                if row:
                    service._generar_informe_individual(row)
                if lanzar_semanal:
                    service._generar_resumen_semanal_si_corresponde(
                        cliente=str(cliente or "").strip(),
                        sucursal=str(sucursal or "").strip(),
                        forzar=True,
                    )
            except Exception:
                LOGGER.exception(
                    "Fallo la generacion asincronica de informes de protocolo (registro_id=%s).",
                    registro_id,
                )
            finally:
                db.close()

        threading.Thread(
            target=_worker,
            name=f"protocolo-report-{registro_id}",
            daemon=True,
        ).start()

    def _generar_informe_individual(self, row: ProtocoloRegistro) -> dict[str, object]:
        informe = self.db.scalar(
            select(ProtocoloInforme)
            .where(
                ProtocoloInforme.tipo_informe == "INDIVIDUAL",
                ProtocoloInforme.registro_id == row.id,
            )
            .limit(1)
        )
        if informe and str(informe.estado or "").upper() == "OK" and str(informe.pdf_url or "").strip():
            return {
                "status": "ok",
                "url": str(informe.pdf_url or ""),
                "informe_id": informe.id,
            }

        if not informe:
            informe = ProtocoloInforme(
                tipo_informe="INDIVIDUAL",
                estado="PENDIENTE",
                registro_id=row.id,
                cliente=row.cliente,
                sucursal=row.sucursal,
                titulo=f"Informe Protocolo #{row.id}",
            )
            self.db.add(informe)
            self.db.commit()
            self.db.refresh(informe)

        try:
            payload = {
                "codigo_informe": f"PR-IND-{row.id}",
                "fecha_emision": self._fmt(datetime.now(self.tz), "%d/%m/%Y %H:%M"),
                "registro_id": row.id,
                "cliente": row.cliente,
                "sucursal": row.sucursal,
                "fecha_registro": self._fmt(row.fecha_registro, "%d/%m/%Y %H:%M"),
                "encargado": row.encargado or "-",
                "grupo": row.grupo or "-",
                "operador": row.operador or "-",
                "puesto": row.puesto or "-",
                "tipo_protocolo": row.tipo_protocolo or "-",
                "detectado": self._valor_si_no(row.detectado),
                "efectivo": self._valor_si_no(row.efectivo),
                "sirena": self._valor_si_no(row.sirena),
                "voz": self._valor_si_no(row.voz),
                "carabineros": self._valor_si_no(row.carabineros),
                "alpha3": self._valor_si_no(row.alpha3),
                "informado": self._valor_si_no(row.informado),
                "bitacora": self._valor_si_no(row.bitacora),
                "protocolo_exitoso": self._valor_si_no(row.protocolo_exitoso),
                "resumen_ejecutivo": self._resumen_ejecutivo_individual(row),
                "observacion_original": row.observaciones_raw or "-",
                "observacion_formalizada": row.observaciones_formal or row.observaciones_raw or "-",
            }
            result = create_protocol_individual_report_pdf(context=payload)
            informe.estado = "OK"
            informe.pdf_url = str(result.get("pdf_web_view_link") or "")
            informe.drive_file_id = str(result.get("pdf_file_id") or "")
            informe.drive_folder_id = str(result.get("folder_id") or "")
            informe.drive_folder_name = str(result.get("folder_name") or "")
            informe.metadata_json = json.dumps(result, ensure_ascii=False)
            informe.error_detalle = None
            self.db.add(informe)
            self.db.commit()
            return {
                "status": "ok",
                "url": str(informe.pdf_url or ""),
                "informe_id": informe.id,
            }
        except DriveReportError as exc:
            self._guardar_informe_error(informe, str(exc))
            return {"status": "error", "error": str(exc), "informe_id": informe.id}
        except Exception as exc:
            self._guardar_informe_error(informe, f"Error generando informe individual: {exc}")
            return {"status": "error", "error": str(exc), "informe_id": informe.id}

    def _filas_semana_anterior_cliente_sucursal(
        self,
        *,
        cliente: str,
        sucursal: str,
    ) -> tuple[RangoFechas, list[ProtocoloRegistro]]:
        rango = self.obtener_rango_semana_anterior()
        inicio_db = self._dt_db(rango.inicio)
        fin_db = self._dt_db(rango.fin)
        rows = self.db.scalars(
            select(ProtocoloRegistro)
            .where(
                ProtocoloRegistro.fecha_registro >= inicio_db,
                ProtocoloRegistro.fecha_registro <= fin_db,
                func.lower(ProtocoloRegistro.cliente) == str(cliente or "").strip().lower(),
                func.lower(ProtocoloRegistro.sucursal) == str(sucursal or "").strip().lower(),
            )
            .order_by(ProtocoloRegistro.fecha_registro.asc())
        ).all()
        return rango, rows

    def _generar_resumen_semanal_si_corresponde(
        self,
        *,
        cliente: str,
        sucursal: str,
        forzar: bool = False,
    ) -> dict[str, object]:
        now = datetime.now(self.tz)
        if (not forzar) and now.weekday() != 0:
            return {"status": "skip", "reason": "solo_lunes"}

        rango, rows = self._filas_semana_anterior_cliente_sucursal(cliente=cliente, sucursal=sucursal)
        if not rows:
            return {"status": "skip", "reason": "sin_registros_semana_anterior"}

        inicio_db = self._dt_db(rango.inicio)
        fin_db = self._dt_db(rango.fin)
        informe = self.db.scalar(
            select(ProtocoloInforme)
            .where(
                ProtocoloInforme.tipo_informe == "SEMANAL",
                func.lower(ProtocoloInforme.cliente) == str(cliente or "").strip().lower(),
                func.lower(ProtocoloInforme.sucursal) == str(sucursal or "").strip().lower(),
                ProtocoloInforme.periodo_inicio == inicio_db,
                ProtocoloInforme.periodo_fin == fin_db,
            )
            .limit(1)
        )
        if informe and str(informe.estado or "").upper() == "OK" and str(informe.pdf_url or "").strip():
            return {
                "status": "ok",
                "url": str(informe.pdf_url or ""),
                "informe_id": informe.id,
            }

        if not informe:
            informe = ProtocoloInforme(
                tipo_informe="SEMANAL",
                estado="PENDIENTE",
                registro_id=None,
                cliente=cliente,
                sucursal=sucursal,
                periodo_inicio=inicio_db,
                periodo_fin=fin_db,
                titulo=f"Resumen semanal {rango.texto_inicio} - {rango.texto_fin}",
            )
            self.db.add(informe)
            self.db.commit()
            self.db.refresh(informe)

        total_preventivo = sum(1 for r in rows if str(r.tipo_protocolo or "").strip().lower() == "preventivo")
        total_intrusivo = sum(1 for r in rows if str(r.tipo_protocolo or "").strip().lower() == "intrusivo")
        total_exitosos = sum(1 for r in rows if str(r.protocolo_exitoso or "").strip().upper() == "SI")
        detalle_lineas = []
        detalle_filas: list[dict[str, str]] = []
        for r in rows:
            fecha_item = self._fmt(r.fecha_registro, "%d/%m/%Y %H:%M")
            tipo_item = r.tipo_protocolo or "-"
            # En el reporte semanal la columna "Observación" debe mostrar solo la observación.
            observacion_item = (r.observaciones_raw or r.observaciones_formal or "-").strip()
            detalle_lineas.append(
                (
                    f"{fecha_item} | "
                    f"{tipo_item} | "
                    f"{observacion_item}"
                )
            )
            detalle_filas.append(
                {
                    "fecha": fecha_item,
                    "sucursal": r.sucursal or sucursal,
                    "tipo_protocolo": tipo_item,
                    "observacion": observacion_item,
                }
            )

        resumen_ejecutivo = (
            f"Durante la semana evaluada se registraron {len(rows)} protocolos en la sucursal {sucursal}. "
            f"Se identificaron {total_preventivo} protocolos preventivos y {total_intrusivo} protocolos intrusivos, "
            f"con {total_exitosos} protocolos exitosos."
        )
        conclusiones = (
            "Se recomienda mantener seguimiento sobre hallazgos recurrentes y validar continuidad operativa "
            "segun observaciones formalizadas registradas en este periodo."
        )

        try:
            payload = {
                "codigo_informe": (
                    f"PR-SEM-{cliente[:12].upper().replace(' ', '')}-{sucursal[:12].upper().replace(' ', '')}"
                ),
                "fecha_emision": self._fmt(datetime.now(self.tz), "%d/%m/%Y %H:%M"),
                "cliente": cliente,
                "sucursal": sucursal,
                "periodo_inicio": rango.texto_inicio,
                "periodo_fin": rango.texto_fin,
                "total_registros": len(rows),
                "total_preventivo": total_preventivo,
                "total_intrusivo": total_intrusivo,
                "total_exitosos": total_exitosos,
                "resumen_ejecutivo": resumen_ejecutivo,
                "detalle_lineas": detalle_lineas,
                "detalle_filas": detalle_filas,
                "conclusiones": conclusiones,
            }
            result = create_protocol_weekly_report_pdf(context=payload)
            informe.estado = "OK"
            informe.pdf_url = str(result.get("pdf_web_view_link") or "")
            informe.drive_file_id = str(result.get("pdf_file_id") or "")
            informe.drive_folder_id = str(result.get("folder_id") or "")
            informe.drive_folder_name = str(result.get("folder_name") or "")
            informe.metadata_json = json.dumps(result, ensure_ascii=False)
            informe.error_detalle = None
            self.db.add(informe)
            self.db.commit()
            return {
                "status": "ok",
                "url": str(informe.pdf_url or ""),
                "informe_id": informe.id,
            }
        except DriveReportError as exc:
            self._guardar_informe_error(informe, str(exc))
            return {"status": "error", "error": str(exc), "informe_id": informe.id}
        except Exception as exc:
            self._guardar_informe_error(informe, f"Error generando informe semanal: {exc}")
            return {"status": "error", "error": str(exc), "informe_id": informe.id}

    def generar_resumenes_semanales_pendientes(self, *, forzar: bool = False) -> dict[str, object]:
        now = datetime.now(self.tz)
        if (not forzar) and now.weekday() != 0:
            return {"ok": True, "status": "skip", "reason": "solo_lunes", "procesados": 0}

        rango = self.obtener_rango_semana_anterior()
        inicio_db = self._dt_db(rango.inicio)
        fin_db = self._dt_db(rango.fin)
        rows = self.db.execute(
            select(ProtocoloRegistro.cliente, ProtocoloRegistro.sucursal)
            .where(
                ProtocoloRegistro.fecha_registro >= inicio_db,
                ProtocoloRegistro.fecha_registro <= fin_db,
            )
            .distinct()
        ).all()
        total = 0
        ok = 0
        errores = 0
        detalle: list[dict[str, object]] = []
        for cliente, sucursal in rows:
            c = str(cliente or "").strip()
            s = str(sucursal or "").strip()
            if not c or not s:
                continue
            total += 1
            result = self._generar_resumen_semanal_si_corresponde(cliente=c, sucursal=s, forzar=True)
            if str(result.get("status") or "").lower() == "ok":
                ok += 1
            elif str(result.get("status") or "").lower() == "error":
                errores += 1
            detalle.append({"cliente": c, "sucursal": s, **result})
        return {
            "ok": True,
            "status": "done",
            "periodo": {"inicio": rango.texto_inicio, "fin": rango.texto_fin},
            "procesados": total,
            "generados_ok": ok,
            "errores": errores,
            "detalle": detalle,
        }

    def guardar_registro(self, data: ProtocoloRegistroCreateRequest) -> dict[str, object]:
        cliente = str(data.cliente or "").strip()
        sucursal = str(data.sucursal or "").strip()
        if not cliente or not sucursal:
            raise ValueError("Cliente y sucursal son obligatorios.")

        encargado = self._usuario_por_token(data.token)
        if not encargado:
            raise ValueError("Sesion invalida o expirada. Vuelve a iniciar sesion.")

        tipo_raw = str(data.tipo_protocolo or "").strip()
        tipo_norm = _normalizar_clave_nombre(tipo_raw)
        if tipo_norm not in {"preventivo", "intrusivo"}:
            raise ValueError("Tipo de protocolo invalido. Usa Preventivo o Intrusivo.")
        tipo_protocolo = "Preventivo" if tipo_norm == "preventivo" else "Intrusivo"

        grupo = self._grupo_por_encargado(encargado)
        observ_raw = str(data.observaciones or "").strip()
        observ_formal = self.formalizar_observacion(observ_raw)

        row = ProtocoloRegistro(
            encargado=encargado or None,
            grupo=grupo or None,
            cliente=cliente,
            sucursal=sucursal,
            tipo_protocolo=tipo_protocolo,
            detectado=self._normalizar_si_no(data.detectado),
            efectivo=self._normalizar_si_no(data.efectivo),
            sirena=self._normalizar_si_no(data.sirena),
            voz=self._normalizar_si_no(data.voz),
            carabineros=self._normalizar_si_no(data.carabineros),
            alpha3=self._normalizar_si_no(data.alpha3),
            informado=self._normalizar_si_no(data.informado),
            bitacora=self._normalizar_si_no(data.bitacora),
            protocolo_exitoso=self._normalizar_si_no(data.protocolo_exitoso),
            puesto=str(data.puesto or "").strip() or None,
            operador=str(data.operador or "").strip() or None,
            observaciones_raw=observ_raw or None,
            observaciones_formal=observ_formal or None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        es_lunes = datetime.now(self.tz).weekday() == 0
        self._lanzar_generacion_informes_async(
            registro_id=int(row.id),
            cliente=row.cliente,
            sucursal=row.sucursal,
            lanzar_semanal=es_lunes,
        )

        return {
            "ok": True,
            "id": row.id,
            "fecha": self._fmt(row.fecha_registro, "%d/%m/%Y %H:%M:%S"),
            "observacion_formal": row.observaciones_formal or "",
            "informes_async": True,
            "informe_individual": {
                "status": "queued",
                "message": "Generacion de informe individual en segundo plano.",
            },
            "informe_semanal": (
                {
                    "status": "queued",
                    "message": "Generacion de resumen semanal en segundo plano.",
                }
                if es_lunes
                else {
                    "status": "skip",
                    "reason": "solo_lunes",
                }
            ),
        }

    # =========================
    # Consultas / reportes
    # =========================
    def listar_registros(
        self,
        *,
        cliente: str = "",
        sucursal: str = "",
        tipo_protocolo: str = "",
        fecha_desde: str = "",
        fecha_hasta: str = "",
        limit: int = 300,
    ) -> list[dict[str, object]]:
        limit_value = int(limit or 0)
        if limit_value < 0:
            limit_value = 0
        if limit_value > 0:
            limit_value = min(limit_value, 50000)
        stmt = select(ProtocoloRegistro)

        where = []
        if cliente.strip():
            where.append(func.lower(ProtocoloRegistro.cliente) == cliente.strip().lower())
        if sucursal.strip():
            where.append(func.lower(ProtocoloRegistro.sucursal) == sucursal.strip().lower())
        if tipo_protocolo.strip():
            where.append(func.lower(ProtocoloRegistro.tipo_protocolo) == tipo_protocolo.strip().lower())

        dt_desde = self.parsear_fecha(fecha_desde)
        dt_hasta = self.parsear_fecha(fecha_hasta)
        if dt_desde:
            where.append(ProtocoloRegistro.fecha_registro >= dt_desde)
        if dt_hasta:
            where.append(ProtocoloRegistro.fecha_registro <= dt_hasta)

        if where:
            stmt = stmt.where(and_(*where))

        stmt = stmt.order_by(ProtocoloRegistro.id.desc())
        if limit_value > 0:
            stmt = stmt.limit(limit_value)
        rows = self.db.scalars(stmt).all()
        out: list[dict[str, object]] = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "fecha": self._fmt(r.fecha_registro, "%d/%m/%Y %H:%M"),
                    "encargado": r.encargado or "",
                    "grupo": r.grupo or "",
                    "cliente": r.cliente,
                    "sucursal": r.sucursal,
                    "tipo_protocolo": r.tipo_protocolo or "",
                    "detectado": r.detectado or "",
                    "efectivo": r.efectivo or "",
                    "sirena": r.sirena or "",
                    "voz": r.voz or "",
                    "carabineros": r.carabineros or "",
                    "alpha3": r.alpha3 or "",
                    "informado": r.informado or "",
                    "bitacora": r.bitacora or "",
                    "protocolo_exitoso": r.protocolo_exitoso or "",
                    "observaciones": r.observaciones_formal or r.observaciones_raw or "",
                    "observaciones_raw": r.observaciones_raw or "",
                    "observaciones_formal": r.observaciones_formal or "",
                    "operador": r.operador or "",
                    "puesto": r.puesto or "",
                    "created_at": self._fmt(r.created_at, "%d/%m/%Y %H:%M:%S"),
                    "updated_at": self._fmt(r.updated_at, "%d/%m/%Y %H:%M:%S"),
                }
            )
        return out

    def _filas_en_rango(self, inicio: datetime, fin: datetime) -> list[ProtocoloRegistro]:
        return self.db.scalars(
            select(ProtocoloRegistro)
            .where(ProtocoloRegistro.fecha_registro >= inicio, ProtocoloRegistro.fecha_registro <= fin)
            .order_by(ProtocoloRegistro.fecha_registro.asc())
        ).all()

    def generar_resumen(self, *, periodo: str = "diario", fecha_referencia: str = "") -> dict[str, object]:
        periodo_norm = str(periodo or "diario").strip().lower()
        if periodo_norm not in {"diario", "semanal", "mensual"}:
            raise ValueError("Periodo invÃ¡lido. Usa: diario, semanal o mensual.")

        if periodo_norm == "diario":
            rango = self.obtener_rango_para_diarios()
        elif periodo_norm == "semanal":
            rango = self.obtener_rango_semana_anterior()
        else:
            rango = self.obtener_rango_mes_actual()

        # Permite forzar fecha de referencia si viene.
        if fecha_referencia.strip():
            dt_ref = self.parsear_fecha(fecha_referencia.strip())
            if dt_ref:
                if periodo_norm == "diario":
                    inicio, fin = self._dt_bounds(dt_ref.date())
                    rango = RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin), modo="FECHA_MANUAL")
                elif periodo_norm == "semanal":
                    ref = dt_ref.astimezone(self.tz)
                    monday = (ref - timedelta(days=ref.weekday())).date()
                    sunday = monday + timedelta(days=6)
                    inicio, _ = self._dt_bounds(monday)
                    _, fin = self._dt_bounds(sunday)
                    rango = RangoFechas(inicio, fin, self._fmt(inicio), self._fmt(fin))
                else:
                    first_day = date(dt_ref.year, dt_ref.month, 1)
                    if dt_ref.month == 12:
                        next_month = date(dt_ref.year + 1, 1, 1)
                    else:
                        next_month = date(dt_ref.year, dt_ref.month + 1, 1)
                    last_day = next_month - timedelta(days=1)
                    inicio, _ = self._dt_bounds(first_day)
                    _, fin = self._dt_bounds(last_day)
                    rango = RangoFechas(
                        inicio,
                        fin,
                        self._fmt(inicio),
                        self._fmt(fin),
                        etiqueta_mes=dt_ref.strftime("%m/%Y"),
                    )

        rows = self._filas_en_rango(rango.inicio, rango.fin)

        # Apps Script semanal: solo "preventivo".
        if periodo_norm == "semanal":
            rows = [r for r in rows if str(r.tipo_protocolo or "").strip().lower() == "preventivo"]

        agrupado: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            agrupado[r.cliente][r.sucursal].append(
                {
                    "fecha": self._fmt(r.fecha_registro, "%d/%m/%Y %H:%M"),
                    "tipo": r.tipo_protocolo or "",
                    "observacion": r.observaciones_formal or r.observaciones_raw or "",
                }
            )

        reportes: list[dict[str, object]] = []
        for cliente, sucursales in sorted(agrupado.items(), key=lambda x: x[0].lower()):
            for sucursal, regs in sorted(sucursales.items(), key=lambda x: x[0].lower()):
                reportes.append(
                    {
                        "cliente": cliente,
                        "sucursal": sucursal,
                        "total_registros": len(regs),
                        "detalle": regs,
                    }
                )

        return {
            "periodo": periodo_norm,
            "rango": {
                "inicio": rango.texto_inicio,
                "fin": rango.texto_fin,
                "etiqueta_mes": rango.etiqueta_mes,
                "modo": rango.modo,
            },
            "total_registros": len(rows),
            "total_reportes": len(reportes),
            "reportes": reportes,
        }

    def conteo_por_puesto_mes(self, *, anio: int, mes: int) -> list[dict[str, int]]:
        if mes < 1 or mes > 12:
            raise ValueError("Mes invÃ¡lido.")
        inicio = datetime(anio, mes, 1, 0, 0, 0, tzinfo=self.tz)
        if mes == 12:
            next_month = datetime(anio + 1, 1, 1, 0, 0, 0, tzinfo=self.tz)
        else:
            next_month = datetime(anio, mes + 1, 1, 0, 0, 0, tzinfo=self.tz)
        fin = next_month - timedelta(microseconds=1)

        rows = self._filas_en_rango(inicio, fin)
        buckets: dict[int, dict[str, int]] = {i: {"puesto": i, "intrusivo": 0, "preventivo": 0, "total": 0} for i in range(1, 31)}
        for r in rows:
            try:
                p = int(str(r.puesto or "").strip())
            except Exception:
                continue
            if p < 1 or p > 30:
                continue
            tipo = str(r.tipo_protocolo or "").strip().lower()
            if tipo == "intrusivo":
                buckets[p]["intrusivo"] += 1
            elif tipo == "preventivo":
                buckets[p]["preventivo"] += 1
            buckets[p]["total"] += 1
        return [buckets[i] for i in range(1, 31)]

    def listar_informes(
        self,
        *,
        cliente: str = "",
        sucursal: str = "",
        tipo_informe: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        limit_value = max(1, min(int(limit or 200), 5000))
        stmt = select(ProtocoloInforme)
        where = []
        if cliente.strip():
            where.append(func.lower(ProtocoloInforme.cliente) == cliente.strip().lower())
        if sucursal.strip():
            where.append(func.lower(ProtocoloInforme.sucursal) == sucursal.strip().lower())
        if tipo_informe.strip():
            where.append(func.lower(ProtocoloInforme.tipo_informe) == tipo_informe.strip().lower())
        if where:
            stmt = stmt.where(and_(*where))
        stmt = stmt.order_by(ProtocoloInforme.id.desc()).limit(limit_value)
        rows = self.db.scalars(stmt).all()
        out: list[dict[str, object]] = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "tipo_informe": r.tipo_informe,
                    "estado": r.estado,
                    "registro_id": r.registro_id,
                    "cliente": r.cliente,
                    "sucursal": r.sucursal,
                    "periodo_inicio": self._fmt(r.periodo_inicio, "%d/%m/%Y %H:%M") if r.periodo_inicio else "",
                    "periodo_fin": self._fmt(r.periodo_fin, "%d/%m/%Y %H:%M") if r.periodo_fin else "",
                    "titulo": r.titulo or "",
                    "pdf_url": r.pdf_url or "",
                    "drive_file_id": r.drive_file_id or "",
                    "drive_folder_id": r.drive_folder_id or "",
                    "drive_folder_name": r.drive_folder_name or "",
                    "error_detalle": r.error_detalle or "",
                    "created_at": self._fmt(r.created_at, "%d/%m/%Y %H:%M:%S"),
                    "updated_at": self._fmt(r.updated_at, "%d/%m/%Y %H:%M:%S"),
                }
            )
        return out



