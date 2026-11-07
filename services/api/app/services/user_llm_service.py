"""Per-user AI settings: provider, model and (optionally) the user's own API key.

Resolution (`settings_for`), applied to every API request and every background job of that user:
  1. the user saved a provider, model AND key           -> their own key is used (`llm_key_source = "user"`);
  2. the user saved a provider and model but no key, and the server is configured for that same provider
                                                          -> the server key with the user's chosen model;
  3. otherwise                                           -> the server configuration, unchanged.
The plain-text key exists only in memory for the duration of a request/job. It is never logged, audited or returned.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core import secretbox
from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.db.models import User, UserLLMSetting
from app.providers.llm import LLMError, build_provider, catalog, describe
from app.services import audit_service

log = logging.getLogger(__name__)
TEST_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"ok": {"type": "BOOLEAN"}},
    "required": ["ok"],
}


def allowed_providers(settings: Settings) -> list[str]:
    keys = [k.strip().lower() for k in settings.llm_user_providers.split(",") if k.strip()]
    return [k for k in keys if k in catalog.PROVIDERS]


def get(db: Session, user_id: uuid.UUID) -> UserLLMSetting | None:
    return db.get(UserLLMSetting, user_id)


def settings_for(db: Session, base: Settings, user_id: uuid.UUID | None) -> Settings:
    """The settings to use for this user's AI calls (see module docstring). Never raises."""
    if user_id is None or not base.llm_allow_user_keys:
        return base
    try:
        row = get(db, user_id)
    except Exception:  # noqa: BLE001  a lookup problem must never break the request; fall back to the server
        log.exception("user AI settings lookup failed")
        return base
    if row is None or row.provider not in allowed_providers(base):
        return base
    if row.api_key_ciphertext:
        try:
            key = secretbox.decrypt(base, row.api_key_ciphertext)
        except secretbox.SecretUnavailable:
            log.warning(
                "stored user API key cannot be decrypted; using server AI settings", extra={"user_id": str(user_id)}
            )
            return base
        return base.model_copy(
            update={"llm_provider": row.provider, "llm_model": row.model, "llm_api_key": key, "llm_key_source": "user"}
        )
    if row.provider == base.llm_provider and base.llm_api_key:
        return base.model_copy(update={"llm_model": row.model})
    return base


def view(db: Session, settings: Settings, user: User) -> dict[str, Any]:
    row = get(db, user.id)
    effective = settings_for(db, settings, user.id)
    key_readable = True
    if row is not None and row.api_key_ciphertext:
        try:
            secretbox.decrypt(settings, row.api_key_ciphertext)
        except secretbox.SecretUnavailable:
            key_readable = False
    server = describe(settings)
    return {
        "allow_user_keys": settings.llm_allow_user_keys,
        "saved": None
        if row is None
        else {
            "provider": row.provider,
            "model": row.model,
            "pricing": catalog.static_pricing(row.provider, row.model),
            "has_key": bool(row.api_key_ciphertext),
            "key_hint": row.api_key_hint,
            "key_readable": key_readable,
            "updated_at": row.updated_at,
        },
        "server": {
            "configured": server["configured"],
            "provider": server["provider"],
            "model": server["model"],
        },
        "effective": describe(effective),
        "default": {
            "provider": catalog.DEFAULT_PROVIDER,
            "model": catalog.PROVIDERS[catalog.DEFAULT_PROVIDER].default_model,
        },
        "providers": [catalog.PROVIDERS[k].as_dict() for k in allowed_providers(settings)],
    }


def _validate(settings: Settings, provider: str, model: str) -> tuple[str, str]:
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    if provider not in allowed_providers(settings):
        raise ValidationFailed(
            "Choose one of the supported AI providers.",
            details=[{"field": "provider", "issue": "unsupported", "allowed": allowed_providers(settings)}],
        )
    if not catalog.valid_model_id(model):
        raise ValidationFailed("Enter a valid model id.", details=[{"field": "model", "issue": "invalid"}])
    return provider, model


def _clean_key(api_key: str) -> str:
    key = api_key.strip()
    if not 8 <= len(key) <= 500 or any(c.isspace() for c in key):
        raise ValidationFailed(
            "That does not look like an API key.", details=[{"field": "api_key", "issue": "invalid"}]
        )
    return key


def save(
    db: Session,
    settings: Settings,
    user: User,
    *,
    provider: str,
    model: str,
    api_key: str | None,
    clear_key: bool = False,
) -> UserLLMSetting:
    if not settings.llm_allow_user_keys:
        raise ValidationFailed("Personal AI settings are disabled on this server.")
    provider, model = _validate(settings, provider, model)
    row = get(db, user.id)
    before = (
        None if row is None else {"provider": row.provider, "model": row.model, "has_key": bool(row.api_key_ciphertext)}
    )
    if row is None:
        row = UserLLMSetting(user_id=user.id, provider=provider, model=model)
        db.add(row)
    elif row.provider != provider and not api_key:
        clear_key = True  # a key belongs to one provider; never send it to another
    row.provider, row.model = provider, model
    if api_key:
        key = _clean_key(api_key)
        try:
            row.api_key_ciphertext = secretbox.encrypt(settings, key)
        except secretbox.SecretUnavailable as e:
            raise ValidationFailed(str(e)) from None
        row.api_key_hint = secretbox.hint(key)
    elif clear_key:
        row.api_key_ciphertext, row.api_key_hint = None, None
    row.updated_at = datetime.now(UTC)
    audit_service.record(
        db,
        action="user.llm_settings_updated",
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        before=before,
        after={"provider": provider, "model": model, "has_key": bool(row.api_key_ciphertext)},
    )
    db.commit()
    return row


def delete(db: Session, user: User) -> None:
    row = get(db, user.id)
    if row is None:
        return
    db.delete(row)
    audit_service.record(
        db, action="user.llm_settings_removed", entity_type="user", entity_id=user.id, actor_id=user.id
    )
    db.commit()


def _key_for(db: Session, settings: Settings, user: User, provider: str, api_key: str | None) -> str | None:
    if api_key:
        return _clean_key(api_key)
    row = get(db, user.id)
    if row is not None and row.provider == provider and row.api_key_ciphertext:
        try:
            return secretbox.decrypt(settings, row.api_key_ciphertext)
        except secretbox.SecretUnavailable:
            return None
    if settings.llm_provider == provider and settings.llm_api_key:
        return settings.llm_api_key
    return None


def models(
    db: Session,
    settings: Settings,
    user: User,
    provider: str,
    api_key: str | None,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    provider = (provider or "").strip().lower()
    if provider not in allowed_providers(settings):
        raise ValidationFailed("Unsupported AI provider.", details=[{"field": "provider", "issue": "unsupported"}])
    key = _key_for(db, settings, user, provider, api_key)
    http = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0))
    items, source = catalog.list_models(provider, key, http)
    return {"provider": provider, "source": source, "models": items}


def test(
    db: Session,
    settings: Settings,
    user: User,
    *,
    provider: str,
    model: str,
    api_key: str | None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """One tiny request with the given (or stored) key. Sends no project data."""
    provider, model = _validate(settings, provider, model)
    key = _key_for(db, settings, user, provider, api_key)
    if not key:
        return {"ok": False, "message": "Enter an API key for this provider first."}
    probe = settings.model_copy(
        update={
            "llm_provider": provider,
            "llm_model": model,
            "llm_api_key": key,
            "llm_key_source": "user",
            "llm_max_retries": 0,
        }
    )
    try:
        prov = build_provider(probe, client=client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)))
        prov.generate_json(
            system="You are a connectivity check.",
            prompt='Reply with {"ok": true}.',
            schema=TEST_SCHEMA,
            temperature=0.0,
            max_output_tokens=256,
        )
    except LLMError as e:
        return {"ok": False, "message": e.message}
    return {"ok": True, "message": f"Connected to {catalog.PROVIDERS[provider].label} ({model})."}
