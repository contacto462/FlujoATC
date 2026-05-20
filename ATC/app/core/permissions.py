from fastapi import Depends, HTTPException, status
from ATC.app.core.auth import get_current_user
from ATC.app.models.user import User


def require_admin(current_user: User = Depends(get_current_user)):

    if not current_user.is_admin:   # ðŸ‘ˆ LLAMADA CORRECTA
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permisos de administrador"
        )

    return current_user
