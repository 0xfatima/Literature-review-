from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import client_key, current_session, current_user, get_rate_limiter
from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter
from app.db.models import AuthSession, User
from app.db.session import get_db
from app.schemas.auth import Credentials, UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: Credentials, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
             limiter: RateLimiter = Depends(get_rate_limiter), ckey: str = Depends(client_key)) -> User:
    return auth_service.register(db, settings, limiter, email=body.email, password=body.password, client_key=ckey)


@router.post("/login", response_model=UserOut)
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db),
          settings: Settings = Depends(get_settings), limiter: RateLimiter = Depends(get_rate_limiter),
          ckey: str = Depends(client_key)) -> User:
    user, token, expires = auth_service.login(db, settings, limiter, email=body.email, password=body.password,
                                              client_key=ckey, user_agent=request.headers.get("user-agent"))
    response.set_cookie(settings.session_cookie_name, token, max_age=settings.session_ttl_hours * 3600,
                        httponly=True, samesite="lax", secure=settings.cookie_secure, path="/")
    return user


@router.post("/logout", status_code=204, response_class=Response)
def logout(resolved: tuple[User, AuthSession] = Depends(current_session),
           db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Response:
    auth_service.logout(db, *resolved)
    resp = Response(status_code=204)  # explicit response object: the documented pattern for 204 + cookie changes
    resp.delete_cookie(settings.session_cookie_name, path="/")
    return resp


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user
