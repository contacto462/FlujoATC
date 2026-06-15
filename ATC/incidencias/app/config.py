import os
from pathlib import Path
import ssl

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    app_name: str = "Incidencias API"
    app_env: str = "dev"
    database_url: str = Field(
        default_factory=lambda: (
            os.getenv("DATABASE_URL")
            or os.getenv("POSTGRES_URL")
            or os.getenv("POSTGRESQL_URL")
            or "sqlite:///./incidencias.db"
        ),
        description="Ej: postgresql+psycopg://user:pass@localhost:5432/incidencias",
    )
    timezone: str = "America/Santiago"
    db_schema: str = Field(
        default_factory=lambda: os.getenv("DB_SCHEMA") or "public",
        description="Schema por defecto para tablas PostgreSQL (ej: public).",
    )
    postgres_lock_timeout_ms: int = Field(
        default_factory=lambda: int(os.getenv("POSTGRES_LOCK_TIMEOUT_MS") or "5000"),
        description="Timeout de espera por locks PostgreSQL en milisegundos.",
    )
    postgres_statement_timeout_ms: int = Field(
        default_factory=lambda: int(os.getenv("POSTGRES_STATEMENT_TIMEOUT_MS") or "30000"),
        description="Timeout de ejecucion de sentencias PostgreSQL en milisegundos.",
    )
    support_sync_mode: str = Field(
        default_factory=lambda: (os.getenv("SUPPORT_SYNC_MODE") or "off").lower(),
        description="off | api | db",
    )
    support_sync_api_url: str = Field(
        default_factory=lambda: os.getenv("SUPPORT_SYNC_API_URL") or "",
        description="Endpoint del otro sistema Python para recibir incidencias.",
    )
    support_sync_api_token: str = Field(
        default_factory=lambda: os.getenv("SUPPORT_SYNC_API_TOKEN") or "",
        description="Token Bearer para autenticaciÃ³n con el sistema de soporte.",
    )
    support_sync_timeout_sec: int = Field(
        default_factory=lambda: int(os.getenv("SUPPORT_SYNC_TIMEOUT_SEC") or "10"),
        description="Timeout para sincronizaciÃ³n por API.",
    )
    support_db_url: str = Field(
        default_factory=lambda: os.getenv("SUPPORT_DB_URL") or "",
        description="Cadena de conexiÃ³n al SQL del sistema de soporte (modo db).",
    )
    support_db_schema: str = Field(
        default_factory=lambda: os.getenv("SUPPORT_DB_SCHEMA") or "public",
        description="Schema destino en SQL de soporte.",
    )
    # AJUSTE SOPORTE REGISTRO SQL #
    support_db_table: str = Field(
        default_factory=lambda: os.getenv("SUPPORT_DB_TABLE") or "registro",
        description="Tabla destino en SQL de soporte.",
    )
    helpdesk_base_url: str = Field(
        default_factory=lambda: (os.getenv("HELPDESK_BASE_URL") or "").rstrip("/"),
        description="URL base del Helpdesk para redirigir usuarios de soporte desde el login unico.",
    )
    materiales_excel_path: str = Field(
        default_factory=lambda: os.getenv("MATERIALES_EXCEL_PATH")
        or str(Path.home() / "Desktop" / "Hoja de cálculo sin título.xlsx"),
        description="Ruta al Excel con el catálogo de materiales para el buscador de cierre.",
    )
    venta_catalogo_base_url: str = Field(
        default_factory=lambda: (os.getenv("VENTA_CATALOGO_BASE_URL") or "").rstrip("/"),
        description="Base URL de la API externa de regiones/comunas para Venta.",
    )
    venta_catalogo_timeout_seconds: int = Field(
        default_factory=lambda: int(os.getenv("VENTA_CATALOGO_TIMEOUT_SECONDS") or "8"),
        description="Timeout para consultar la API externa de regiones/comunas de Venta.",
    )
    venta_catalogo_verify_ssl: bool = Field(
        default_factory=lambda: str(os.getenv("VENTA_CATALOGO_VERIFY_SSL") or "true").strip().lower() in {"1", "true", "yes", "on"},
        description="Valida el certificado SSL de la API externa de regiones/comunas.",
    )
    smtp_enabled: bool = Field(
        default_factory=lambda: str(os.getenv("SMTP_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"},
        description="Habilita envio automatico de correos desde backend.",
    )
    smtp_host: str = Field(
        default_factory=lambda: os.getenv("SMTP_HOST") or "",
    )
    smtp_port: int = Field(
        default_factory=lambda: int(os.getenv("SMTP_PORT") or "587"),
    )
    smtp_username: str = Field(
        default_factory=lambda: os.getenv("SMTP_USERNAME") or "",
    )
    smtp_password: str = Field(
        default_factory=lambda: os.getenv("SMTP_PASSWORD") or "",
    )
    smtp_from_email: str = Field(
        default_factory=lambda: os.getenv("SMTP_FROM_EMAIL") or "",
    )
    smtp_from_name: str = Field(
        default_factory=lambda: os.getenv("SMTP_FROM_NAME") or "ATC Incidencias",
    )
    smtp_use_tls: bool = Field(
        default_factory=lambda: str(os.getenv("SMTP_USE_TLS") or "true").strip().lower() in {"1", "true", "yes", "on"},
    )
    smtp_use_ssl: bool = Field(
        default_factory=lambda: str(os.getenv("SMTP_USE_SSL") or "false").strip().lower() in {"1", "true", "yes", "on"},
    )
    smtp_timeout_sec: int = Field(
        default_factory=lambda: int(os.getenv("SMTP_TIMEOUT_SEC") or "20"),
    )
    smtp_bcc_emails: str = Field(
        default_factory=lambda: os.getenv("SMTP_BCC_EMAILS") or "",
        description="Correos separados por coma que reciben BCC en cada envio automatico.",
    )

    # Segunda cuenta SMTP (soporte@alguientecuida.cl)
    smtp2_enabled: bool = Field(
        default_factory=lambda: str(os.getenv("SMTP2_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"},
        description="Habilita segundo emisor SMTP.",
    )
    smtp2_host: str = Field(
        default_factory=lambda: os.getenv("SMTP2_HOST") or "",
    )
    smtp2_port: int = Field(
        default_factory=lambda: int(os.getenv("SMTP2_PORT") or "587"),
    )
    smtp2_username: str = Field(
        default_factory=lambda: os.getenv("SMTP2_USERNAME") or "",
    )
    smtp2_password: str = Field(
        default_factory=lambda: os.getenv("SMTP2_PASSWORD") or "",
    )
    smtp2_from_email: str = Field(
        default_factory=lambda: os.getenv("SMTP2_FROM_EMAIL") or "",
    )
    smtp2_from_name: str = Field(
        default_factory=lambda: os.getenv("SMTP2_FROM_NAME") or "ATC Incidencias",
    )
    smtp2_use_tls: bool = Field(
        default_factory=lambda: str(os.getenv("SMTP2_USE_TLS") or "true").strip().lower() in {"1", "true", "yes", "on"},
    )
    smtp2_use_ssl: bool = Field(
        default_factory=lambda: str(os.getenv("SMTP2_USE_SSL") or "false").strip().lower() in {"1", "true", "yes", "on"},
    )
    smtp2_timeout_sec: int = Field(
        default_factory=lambda: int(os.getenv("SMTP2_TIMEOUT_SEC") or "20"),
    )

    # IA para formalizacion de observaciones
    ia_formalizador_enabled: bool = Field(
        default_factory=lambda: str(os.getenv("IA_FORMALIZADOR_ENABLED") or "true").strip().lower() in {"1", "true", "yes", "on"},
        description="Habilita formalizacion con IA real para observaciones de protocolos.",
    )
    ia_formalizador_strict: bool = Field(
        default_factory=lambda: str(os.getenv("IA_FORMALIZADOR_STRICT") or "true").strip().lower() in {"1", "true", "yes", "on"},
        description="Si esta activo, falla el guardado cuando la IA no responde correctamente.",
    )
    openai_api_key: str = Field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY") or "",
    )
    openai_base_url: str = Field(
        default_factory=lambda: (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
    )
    openai_model_formalizador: str = Field(
        default_factory=lambda: os.getenv("OPENAI_MODEL_FORMALIZADOR") or "gpt-4.1-mini",
    )
    openai_timeout_sec: int = Field(
        default_factory=lambda: int(os.getenv("OPENAI_TIMEOUT_SEC") or "25"),
    )

    # Claude (Anthropic) para formalizacion de observaciones
    anthropic_api_key: str = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or "",
        description="API key de Anthropic para correccion de texto con Claude.",
    )
    anthropic_model_formalizador: str = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL_FORMALIZADOR") or "claude-haiku-4-5",
        description="Modelo Claude para formalizar observaciones (ej: claude-haiku-4-5, claude-opus-4-7).",
    )
    anthropic_timeout_sec: int = Field(
        default_factory=lambda: int(os.getenv("ANTHROPIC_TIMEOUT_SEC") or "25"),
        description="Timeout en segundos para llamadas a la API de Anthropic.",
    )

    google_drive_enabled: bool = Field(
        default_factory=lambda: str(os.getenv("GOOGLE_DRIVE_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"},
        description="Habilita generacion de PDF e imagenes en Google Drive al cerrar ODT.",
    )
    google_drive_auth_mode: str = Field(
        default_factory=lambda: (os.getenv("GOOGLE_DRIVE_AUTH_MODE") or "oauth_user").strip().lower(),
        description="oauth_user | service_account",
    )
    google_service_account_file: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "secrets/gdrive_service_account.json",
    )
    google_oauth_client_secret_file: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE") or "secrets/google_oauth_client_secret.json",
    )
    google_oauth_token_file: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_OAUTH_TOKEN_FILE") or "secrets/google_oauth_token.json",
    )
    google_drive_root_folder_id: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID") or "",
    )
    google_drive_ods_root_folder_id: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_DRIVE_ODS_ROOT_FOLDER_ID") or "",
    )
    google_drive_support_folder_id: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_DRIVE_SUPPORT_FOLDER_ID") or "1EO7fPTC6d97BnZfnfUYxRp6e1sFUmJa_",
    )
    google_doc_template_id: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_DOC_TEMPLATE_ID") or "",
    )
    google_doc_template_protocolos_id: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_DOC_TEMPLATE_PROTOCOLOS_ID") or "1FWm1_UUK1zm_ouK0hT75P_sb_Xw1s5TvmF7ZN5-SV3k",
    )
    google_doc_template_protocolos_diario_id: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_DOC_TEMPLATE_PROTOCOLOS_DIARIO_ID") or "1IazJgh23qh5qHu_gSrmivcStLg4NW-qnt8nxdrHf0VE",
    )
    google_doc_template_protocolos_semanal_id: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_DOC_TEMPLATE_PROTOCOLOS_SEMANAL_ID") or "1RgaKKrsgacVEFhbfjhOn8qpqtuOQEe-cUiiE7vuZ0xQ",
    )
    google_drive_protocolos_folder_id: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_DRIVE_PROTOCOLOS_FOLDER_ID") or "1beVaXbf23FTHlBa2FfO1mnz55RcKf_iW",
    )

    # Dashboard Servicio Tecnico (indicadores parametrizables)
    servicio_sla_dias: int = Field(
        default_factory=lambda: int(os.getenv("SERVICIO_SLA_DIAS") or "3"),
        description="Dias maximos para cerrar una ODT dentro de SLA.",
    )
    servicio_odt_antigua_dias: int = Field(
        default_factory=lambda: int(os.getenv("SERVICIO_ODT_ANTIGUA_DIAS") or "14"),
        description="Dias abiertos a partir de los cuales una ODT pendiente se considera antigua.",
    )
    servicio_reincidencia_ventana_dias: int = Field(
        default_factory=lambda: int(os.getenv("SERVICIO_REINCIDENCIA_VENTANA_DIAS") or "7"),
        description="Ventana (dias) para detectar reincidencias por sucursal.",
    )
    servicio_instalacion_mala_dias: int = Field(
        default_factory=lambda: int(os.getenv("SERVICIO_INSTALACION_MALA_DIAS") or "15"),
        description="Caida antes de estos dias => calidad de instalacion Mala.",
    )
    servicio_instalacion_regular_dias: int = Field(
        default_factory=lambda: int(os.getenv("SERVICIO_INSTALACION_REGULAR_DIAS") or "30"),
        description="Caida antes de estos dias (y despues del umbral malo) => calidad Regular.",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_db_url(cls, value: str) -> str:
        if not value:
            return "sqlite:///./incidencias.db"
        v = str(value).strip()
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://") and "+psycopg" not in v:
            v = v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @field_validator("support_db_url", mode="before")
    @classmethod
    def normalize_support_db_url(cls, value: str) -> str:
        if not value:
            return ""
        v = str(value).strip()
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://") and "+psycopg" not in v:
            v = v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @model_validator(mode="after")
    def apply_unified_defaults(self):
        if not self.support_db_url:
            self.support_db_url = self.database_url
        if not self.support_db_schema:
            self.support_db_schema = self.db_schema or "public"
        return self

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
