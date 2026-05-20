from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ATC.app.core.db import get_db
from ATC.app.schemas.user import UserCreate, UserLogin
from ATC.app.services.user_service import UserService
from ATC.app.core.security import create_access_token

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/register")
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    user = UserService.create_user(
        db,
        user_data.name,
        user_data.username,
        user_data.password
    )
    return user

@router.post("/login")
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = UserService.authenticate(
        db,
        user_data.username,
        user_data.password
    )

    if not user:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = create_access_token({"sub": user.username})

    return {"access_token": token, "token_type": "bearer"}
