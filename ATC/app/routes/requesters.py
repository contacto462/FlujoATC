from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ATC.app.core.db import get_db
from ATC.app.models.requester import Requester   # âœ… FIX
from ATC.app.schemas.requester import RequesterCreate, RequesterOut

router = APIRouter(prefix="/requesters", tags=["requesters"])


@router.post("/", response_model=RequesterOut)
def create_requester(
    data: RequesterCreate,
    db: Session = Depends(get_db)
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
def list_requesters(db: Session = Depends(get_db)):
    return db.query(Requester).all()
