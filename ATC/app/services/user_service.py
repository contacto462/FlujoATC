from sqlalchemy.orm import Session

from ATC.app.core.security import hash_password, verify_password
from ATC.app.models.user import User


class UserService:
    @staticmethod
    def create_user(db: Session, name: str, username: str, password: str):
        hashed = hash_password(password)
        user = User(name=name, username=username, hashed_password=hashed)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def authenticate(db: Session, username: str, password: str):
        user = UserService.find_by_login(db, username)
        if not user:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user

    @staticmethod
    def find_by_login(db: Session, login: str):
        value = str(login or "").strip()
        if not value:
            return None
        return db.query(User).filter(User.username == value).first()
