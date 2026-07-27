from __future__ import annotations

from datetime import datetime
from typing import List
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, DateTime, Index, func

from ATC.app.core.db import Base

class User(Base):
    __tablename__ = "users"

    # =========================
    # ðŸ†” IDENTIFICACIÃ“N
    # =========================
    id: Mapped[int] = mapped_column(primary_key=True)

    # =========================
    # ðŸ‘¤ INFORMACIÃ“N BÃSICA
    # =========================
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    username: Mapped[str] = mapped_column(
        "user",
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )

    hashed_password: Mapped[str] = mapped_column(
        "password",
        String(255),
        nullable=False,
    )

    # =========================
    # ðŸ›¡ ROLES Y ESTADO
    # =========================
    role: Mapped[str] = mapped_column(
        String(20),
        default="agent",
        nullable=False,
    )
    # Valores permitidos: "admin" | "agent"

    is_active: Mapped[bool] = mapped_column(
        "is_activate",
        Boolean,
        default=True,
        nullable=False,
    )

    department: Mapped[str | None] = mapped_column(
        "departament",
        String(80),
        nullable=True,
        index=True,
    )

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    password_changed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # =========================
    # ðŸ”— RELACIONES
    # =========================
    assigned_tickets: Mapped[List["Ticket"]] = relationship(
        "Ticket",
        back_populates="assigned_to",
    )

    # =========================
    # ðŸ›¡ PROPIEDADES DE PERMISOS
    # =========================
    @property
    def is_admin(self) -> bool:
        """True para admin de bitácora Y superadmin. Usar is_super_admin para acceso total al sistema."""
        return self.role in ("admin", "superadmin")

    @property
    def is_super_admin(self) -> bool:
        """Acceso total al sistema (solo Gianpiero y Fernando)."""
        return self.role == "superadmin"

    @property
    def is_agent(self) -> bool:
        return self.role == "agent"

    # =========================
    # ðŸ§  MÃ‰TODOS ÃšTILES
    # =========================
    def deactivate(self) -> None:
        self.is_active = False

    def activate(self) -> None:
        self.is_active = True

    def promote_to_admin(self) -> None:
        self.role = "admin"

    def demote_to_agent(self) -> None:
        self.role = "agent"

    # =========================
    # ðŸ§¾ DEBUG PROFESIONAL
    # =========================
    def __repr__(self) -> str:
        return (
            f"<User id={self.id} "
            f"username={self.username} "
            f"role={self.role}>"
        )


# =========================
# ðŸ“Œ ÃNDICES OPCIONALES EXTRA
# =========================
Index("ix_users_role", User.role)
