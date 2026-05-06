from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """
    ConfiguraciÃ³n central del sistema.
    Lee variables desde .env usando Pydantic Settings.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",  # ðŸ”¥ IMPORTANTE para evitar UnicodeDecodeError
        env_ignore_empty=True,
        extra="ignore",
    )

    # ==============================
    # DATABASE
    # ==============================
    DATABASE_URL: str
    # AJUSTE SOPORTE REGISTRO SQL #
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
    # EMAIL (IMAP)
    # ==============================
    IMAP_HOST: str
    IMAP_PORT: int = 993
    IMAP_USER: str
    IMAP_PASSWORD: str
    IMAP_FOLDER: str = "INBOX"

    # ==============================
    # EMAIL (SMTP)
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

    # ==============================
    # GOOGLE DRIVE / DOCS (CIERRE ODT)
    # ==============================
    GOOGLE_DRIVE_ENABLED: bool = False
    GOOGLE_DRIVE_AUTH_MODE: str = "service_account"  # service_account | oauth_user
    GOOGLE_SERVICE_ACCOUNT_FILE: Optional[str] = None
    GOOGLE_OAUTH_CLIENT_SECRET_FILE: Optional[str] = None
    GOOGLE_OAUTH_TOKEN_FILE: Optional[str] = None
    GOOGLE_DRIVE_ROOT_FOLDER_ID: Optional[str] = None
    GOOGLE_DOC_TEMPLATE_ID: Optional[str] = None
    GOOGLE_DRIVE_SUPPORT_FOLDER_ID: Optional[str] = "1EO7fPTC6d97BnZfnfUYxRp6e1sFUmJa_"

    # ==============================
    # VENTA CATALOGO EXTERNO
    # ==============================
    VENTA_CATALOGO_BASE_URL: Optional[str] = None
    VENTA_CATALOGO_TIMEOUT_SECONDS: int = 8


# Instancia global
settings = Settings()
