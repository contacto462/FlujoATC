import os
from pathlib import Path
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # ==============================
    # DATABASE
    # ==============================
    DATABASE_URL: str
    # Obsoleto — las dos BBDD son ahora la misma. Se mantiene para no romper .env existentes.
    INCIDENCIAS_DATABASE_URL: Optional[str] = None

    # ==============================
    # AUTH / JWT
    # ==============================
    JWT_SECRET: str = "change_me"
    JWT_ALG: str = "HS256"
    JWT_EXPIRES_MIN: int = 60 * 24

    # ==============================
    # REDIS / CELERY
    # ==============================
    REDIS_URL: Optional[str] = None

    # ==============================
    # EMAIL (IMAP) — cuenta principal
    # ==============================
    IMAP_HOST: str = ""
    IMAP_PORT: int = 993
    IMAP_USER: str = ""
    IMAP_PASSWORD: str = ""
    IMAP_FOLDER: str = "INBOX"

    # Segunda cuenta IMAP (opcional)
    IMAP2_HOST: Optional[str] = None
    IMAP2_PORT: int = 993
    IMAP2_USER: Optional[str] = None
    IMAP2_PASSWORD: Optional[str] = None
    IMAP2_FOLDER: str = "INBOX"

    # ==============================
    # EMAIL (SMTP) — cuenta Helpdesk
    # ==============================
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: Optional[str] = None

    # ==============================
    # PUBLIC URLS
    # ==============================
    PUBLIC_BASE_URL: Optional[str] = None
    INCIDENCIAS_PUBLIC_BASE_URL: Optional[str] = None
    INICIO_TURNO_PUBLIC_BASE_URL: Optional[str] = None
    SLA_SURVEY_URL: Optional[str] = None
    SLA_WEBHOOK_TOKEN: Optional[str] = None
    AUTOMATION_PENDING_CLOSE_DAYS: int = 3
    AUTOMATION_POLL_SECONDS: int = 300
    AUTOMATION_EMAIL_AUTO_REPLY_ENABLED: bool = True

    # ==============================
    # WHATSAPP CLOUD API
    # ==============================
    WA_VERIFY_TOKEN: Optional[str] = None
    WA_ACCESS_TOKEN: Optional[str] = None
    WA_PHONE_NUMBER_ID: Optional[str] = None
    WA_APP_SECRET: Optional[str] = None

    # ==============================
    # GOOGLE DRIVE / DOCS (Helpdesk)
    # ==============================
    GOOGLE_DRIVE_ENABLED: bool = False
    GOOGLE_DRIVE_AUTH_MODE: str = "service_account"
    GOOGLE_SERVICE_ACCOUNT_FILE: Optional[str] = None
    GOOGLE_OAUTH_CLIENT_SECRET_FILE: Optional[str] = None
    GOOGLE_OAUTH_TOKEN_FILE: Optional[str] = None
    GOOGLE_DRIVE_ROOT_FOLDER_ID: Optional[str] = None
    GOOGLE_DOC_TEMPLATE_ID: Optional[str] = None
    GOOGLE_DRIVE_SUPPORT_FOLDER_ID: Optional[str] = "1EO7fPTC6d97BnZfnfUYxRp6e1sFUmJa_"

    # ==============================
    # DB / SQL Server
    # ==============================
    # postgres_lock_timeout_ms y postgres_statement_timeout_ms eliminados (no aplican en SQL Server)
    timezone: str = "America/Santiago"
    db_schema: str = "dbo"

    # ==============================
    # SMTP Incidencias (cuenta 1)
    # ==============================
    smtp_enabled: bool = False
    # smtp_host/smtp_port/smtp_password → properties → SMTP_HOST/SMTP_PORT/SMTP_PASSWORD
    smtp_username: str = ""          # env SMTP_USERNAME (distinto de SMTP_USER del Helpdesk)
    smtp_from_email: str = ""        # env SMTP_FROM_EMAIL
    smtp_from_name: str = "ATC Incidencias"
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_sec: int = 20
    smtp_bcc_emails: str = ""

    # SMTP cuenta 2
    smtp2_enabled: bool = False
    smtp2_host: str = ""
    smtp2_port: int = 587
    smtp2_username: str = ""
    smtp2_password: str = ""
    smtp2_from_email: str = ""
    smtp2_from_name: str = "ATC Incidencias"
    smtp2_use_tls: bool = True
    smtp2_use_ssl: bool = False
    smtp2_timeout_sec: int = 20

    # SMTP informes/protocolos y visualizacion de camaras
    smtp_informe_host: str = ""
    smtp_informe_port: int = 587
    smtp_informe_username: str = ""
    smtp_informe_password: str = ""
    smtp_informe_from_email: str = ""
    smtp_informe_from_name: str = "Alguien Te Cuida"
    smtp_informe_use_tls: bool = True
    smtp_informe_use_ssl: bool = False
    smtp_informe_timeout_sec: int = 20

    # ==============================
    # IA (OpenAI / Anthropic)
    # ==============================
    ia_formalizador_enabled: bool = True
    ia_formalizador_strict: bool = True
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model_formalizador: str = "gpt-4.1-mini"
    openai_timeout_sec: int = 25
    anthropic_api_key: str = ""
    anthropic_model_formalizador: str = "claude-haiku-4-5"
    anthropic_timeout_sec: int = 25

    # ==============================
    # GOOGLE DRIVE (Incidencias) — extras
    # ==============================
    google_drive_ods_root_folder_id: str = ""
    google_doc_template_protocolos_id: str = "1FWm1_UUK1zm_ouK0hT75P_sb_Xw1s5TvmF7ZN5-SV3k"
    google_doc_template_protocolos_diario_id: str = "1IazJgh23qh5qHu_gSrmivcStLg4NW-qnt8nxdrHf0VE"
    google_doc_template_protocolos_semanal_id: str = "1RgaKKrsgacVEFhbfjhOn8qpqtuOQEe-cUiiE7vuZ0xQ"
    google_drive_protocolos_folder_id: str = "1beVaXbf23FTHlBa2FfO1mnz55RcKf_iW"
    google_drive_rendiciones_folder_id: str = ""
    google_drive_cierre_apertura_folder_id: str = ""
    google_maps_api_key: str = ""

    # ==============================
    # LAVADOS / Servicios vehiculos
    # ==============================
    lavados_sheet_id: str = "1O3wRowhOZLcB8F7hG4vmIdGS9euEWLJO1qmpA0UevUQ"
    lavados_drive_folder_id: str = "1Fgf3Q7W8f63tanqKmIFO5JDlEQTva8x3"
    lavados_doc_template_id: str = "1620EPOEPbq-5f-VHbvopstsGGMSaC2xwpyBeGuHarKI"

    # ==============================
    # VENTA / Catálogo externo
    # ==============================
    venta_catalogo_base_url: str = ""
    venta_catalogo_timeout_seconds: int = 8
    venta_catalogo_verify_ssl: bool = True

    # ==============================
    # SYNC Soporte → BD externa
    # ==============================
    support_sync_mode: str = "off"
    support_sync_api_url: str = ""
    support_sync_api_token: str = ""
    support_sync_timeout_sec: int = 10
    support_db_url: str = ""
    support_db_schema: str = "public"
    support_db_table: str = "registro"

    # ==============================
    # HELPDESK base URL (para redirigir desde Incidencias)
    # ==============================
    helpdesk_base_url: str = ""

    # ==============================
    # MATERIALES (cierre ODT)
    # ==============================
    materiales_excel_path: str = str(Path.home() / "Desktop" / "Hoja de cálculo sin título.xlsx")

    # ==============================
    # DASHBOARD Servicio Técnico
    # ==============================
    servicio_sla_dias: int = 3
    servicio_odt_antigua_dias: int = 14
    servicio_reincidencia_ventana_dias: int = 7
    servicio_instalacion_mala_dias: int = 15
    servicio_instalacion_regular_dias: int = 30

    # ==============================
    # PIRIOD (creacion automatica de clientes)
    # ==============================
    PIRIOD_API_KEY: str = ""
    PIRIOD_ORGANIZATION_ID: str = ""
    piriod_api_base_url: str = "https://api.piriod.com"
    piriod_timeout_seconds: int = 15

    # ==============================
    # MISC Incidencias
    # ==============================
    app_name: str = "ATC"
    app_env: str = "dev"

    @model_validator(mode="after")
    def apply_unified_defaults(self):
        if not self.support_db_url:
            self.support_db_url = self.DATABASE_URL
        return self

    # ------------------------------------------------------------------
    # Propiedades de compatibilidad — código de incidencias usa lowercase
    # ------------------------------------------------------------------

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL

    @property
    def smtp_host(self) -> str:
        return self.SMTP_HOST or ""

    @property
    def smtp_port(self) -> int:
        return self.SMTP_PORT

    @property
    def smtp_password(self) -> str:
        return self.SMTP_PASSWORD or ""

    @property
    def google_drive_enabled(self) -> bool:
        return bool(self.GOOGLE_DRIVE_ENABLED)

    @property
    def google_drive_auth_mode(self) -> str:
        return self.GOOGLE_DRIVE_AUTH_MODE

    @property
    def google_service_account_file(self) -> str:
        return self.GOOGLE_SERVICE_ACCOUNT_FILE or "secrets/gdrive_service_account.json"

    @property
    def google_oauth_client_secret_file(self) -> str:
        return self.GOOGLE_OAUTH_CLIENT_SECRET_FILE or "secrets/google_oauth_client_secret.json"

    @property
    def google_oauth_token_file(self) -> str:
        return self.GOOGLE_OAUTH_TOKEN_FILE or "secrets/google_oauth_token.json"

    @property
    def google_drive_root_folder_id(self) -> str:
        return self.GOOGLE_DRIVE_ROOT_FOLDER_ID or ""

    @property
    def google_doc_template_id(self) -> str:
        return self.GOOGLE_DOC_TEMPLATE_ID or ""

    @property
    def google_drive_support_folder_id(self) -> str:
        return self.GOOGLE_DRIVE_SUPPORT_FOLDER_ID or ""


settings = Settings()
