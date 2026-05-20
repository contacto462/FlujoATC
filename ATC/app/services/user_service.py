from sqlalchemy.orm import Session
from ATC.app.models.user import User
from ATC.app.core.security import hash_password, verify_password

class UserService:

    @staticmethod
    def create_user(db: Session, name: str, username: str, password: str):
        hashed = hash_password(password)
        user = User(
            name=name,
            username=username,
            hashed_password=hashed
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def authenticate(db: Session, username: str, password: str):
        user = db.query(User).filter(User.username == username).first()
        if not user:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user
