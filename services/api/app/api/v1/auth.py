from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import client_key, current_session, current_user, get_rate_limiter
from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter
from app.db.models import AuthSession, User
from app.db.session import get_db
from app.providers.email import Mail, build_mailer
from app.schemas.auth import (
    AuthConfig,
    Credentials,
    EmailOnly,
    LLMModelsRequest,
    LLMSettingsIn,
    LLMTestRequest,
    PasswordResetConfirm,
    RegisterOut,
    TokenOnly,
    UserOut,
)
from app.services import auth_service, user_llm_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _sender(background: BackgroundTasks, settings: Settings) -> auth_service.SendMail:
    """Mail is sent after the response, so timing does not reveal whether an account exists."""
    mailer = build_mailer(settings)

    def send(mail: Mail) -> None:
        background.add_task(mailer.send, mail)

    return send


def _mail_possible(settings: Settings) -> bool:
    return settings.email_backend.strip().lower() != "none"


@router.get("/config", response_model=AuthConfig)
def auth_config(settings: Settings = Depends(get_settings)) -> AuthConfig:
    return AuthConfig(
        password_reset_available=_mail_possible(settings),
        email_verification_required=settings.email_verification_required,
    )


@router.post("/register", response_model=RegisterOut, status_code=201)
def register(
    body: Credentials,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiter: RateLimiter = Depends(get_rate_limiter),
    ckey: str = Depends(client_key),
) -> RegisterOut:
    user = auth_service.register(db, settings, limiter, email=body.email, password=body.password, client_key=ckey)
    sent = False
    if _mail_possible(settings):
        auth_service.send_verification(db, settings, user, _sender(background, settings))
        sent = True
    return RegisterOut(
        **UserOut.model_validate(user).model_dump(),
        verification_required=settings.email_verification_required,
        verification_sent=sent,
    )


@router.post("/verify-email/request", status_code=202, response_class=Response)
def request_verification(
    body: EmailOnly,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiter: RateLimiter = Depends(get_rate_limiter),
    ckey: str = Depends(client_key),
) -> Response:
    auth_service.request_verification(
        db, settings, limiter, _sender(background, settings), email=body.email, client_key=ckey
    )
    return Response(status_code=202)


@router.post("/verify-email/confirm", response_model=UserOut)
def confirm_verification(body: TokenOnly, db: Session = Depends(get_db)) -> User:
    return auth_service.confirm_verification(db, token=body.token)


@router.post("/password-reset/request", status_code=202, response_class=Response)
def request_password_reset(
    body: EmailOnly,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiter: RateLimiter = Depends(get_rate_limiter),
    ckey: str = Depends(client_key),
) -> Response:
    auth_service.request_password_reset(
        db, settings, limiter, _sender(background, settings), email=body.email, client_key=ckey
    )
    return Response(status_code=202)


@router.post("/password-reset/confirm", status_code=204, response_class=Response)
def confirm_password_reset(
    body: PasswordResetConfirm, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> Response:
    auth_service.confirm_password_reset(db, token=body.token, password=body.password)
    resp = Response(status_code=204)
    resp.delete_cookie(settings.session_cookie_name, path="/")
    return resp


@router.post("/login", response_model=UserOut)
def login(
    body: Credentials,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiter: RateLimiter = Depends(get_rate_limiter),
    ckey: str = Depends(client_key),
) -> User:
    user, token, expires = auth_service.login(
        db,
        settings,
        limiter,
        email=body.email,
        password=body.password,
        client_key=ckey,
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return user


@router.post("/logout", status_code=204, response_class=Response)
def logout(
    resolved: tuple[User, AuthSession] = Depends(current_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    auth_service.logout(db, *resolved)
    resp = Response(status_code=204)  # explicit response object: the documented pattern for 204 + cookie changes
    resp.delete_cookie(settings.session_cookie_name, path="/")
    return resp


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


# ---------- personal AI settings (provider, model, own API key) ----------


@router.get("/me/llm")
def get_llm_settings(
    user: User = Depends(current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    return user_llm_service.view(db, settings, user)


@router.put("/me/llm")
def put_llm_settings(
    body: LLMSettingsIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    user_llm_service.save(
        db, settings, user, provider=body.provider, model=body.model, api_key=body.api_key, clear_key=body.clear_key
    )
    return user_llm_service.view(db, settings, user)


@router.delete("/me/llm")
def delete_llm_settings(
    user: User = Depends(current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    user_llm_service.delete(db, user)
    return user_llm_service.view(db, settings, user)


@router.post("/me/llm/models")
def list_llm_models(
    body: LLMModelsRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return user_llm_service.models(db, settings, user, body.provider, body.api_key)


@router.post("/me/llm/test")
def test_llm_settings(
    body: LLMTestRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, Any]:
    allowed, retry = limiter.hit(f"llmtest:{user.id}", 20, 3600)
    if not allowed:
        from app.core.errors import RateLimited

        raise RateLimited(retry)
    return user_llm_service.test(db, settings, user, provider=body.provider, model=body.model, api_key=body.api_key)
