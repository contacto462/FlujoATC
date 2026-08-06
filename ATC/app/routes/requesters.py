from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ATC.app.core.db import get_db
from ATC.app.models.requester import Requester   # âœ… FIX
from ATC.app.schemas.requester import RequesterCreate, RequesterOut

router = APIRouter(prefix="/requesters", tags=["requesters"])


def _require_login(request: Request, db: Session = Depends(get_db)):
    """Exige la cookie de sesion (access_token) que ya usan las paginas
    HTML de web.py. Antes ningun endpoint de este router verificaba sesion
    (hallazgo de auditoria de seguridad, ago 2026)."""
    from ATC.app.routes.web import COOKIE_NAME as _COOKIE_NAME, _decode_cookie_token as _decode_cookie_token_web
    from ATC.app.services.user_service import UserService as _UserService

    cookie = request.cookies.get(_COOKIE_NAME, "")
    if cookie:
        try:
            login = _decode_cookie_token_web(cookie)
            user = _UserService.find_by_login(db, login)
            if user and user.is_active:
                return user
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="No autenticado.")


@router.post("/", response_model=RequesterOut)
def create_requester(
    data: RequesterCreate,
    db: Session = Depends(get_db),
    current_user=Depends(_require_login),
):
    requester = Requester(
        name=data.name,
        email=data.email,
        phone=data.phone,
        type=data.type,
    )

    db.add(requester)
    db.commit()
    db.refresh(requester)

    return requester


@router.get("/", response_model=list[RequesterOut])
def list_requesters(db: Session = Depends(get_db), current_user=Depends(_require_login)):
    return db.query(Requester).all()
