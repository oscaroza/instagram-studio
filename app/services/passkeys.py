import json
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import settings
from app.services.database import database, database_configured, utc_now


class PasskeyError(RuntimeError):
    pass


CHALLENGE_LIFETIME_SECONDS = 300


def _rp_config() -> tuple[str, str]:
    parsed = urlparse(settings.app_base_url)
    hostname = (parsed.hostname or "").lower()
    scheme = parsed.scheme.lower()
    if not hostname or scheme not in {"http", "https"}:
        raise PasskeyError("APP_BASE_URL doit contenir l’adresse complète du Studio.")
    if scheme != "https" and hostname not in {"localhost", "127.0.0.1"}:
        raise PasskeyError("Face ID/passkey nécessite une adresse HTTPS.")
    origin = f"{scheme}://{parsed.netloc}"
    return hostname, origin


def _require_database() -> None:
    if not database_configured():
        raise PasskeyError("MongoDB est nécessaire pour enregistrer une passkey.")


def _account_user_id() -> bytes:
    db = database()
    account = db.passkey_accounts.find_one({"_id": "primary"})
    if not account:
        encoded = bytes_to_base64url(secrets.token_bytes(32))
        db.passkey_accounts.update_one(
            {"_id": "primary"},
            {"$setOnInsert": {"user_id": encoded, "created_at": utc_now()}},
            upsert=True,
        )
        account = db.passkey_accounts.find_one({"_id": "primary"}) or {"user_id": encoded}
    return base64url_to_bytes(str(account["user_id"]))


def _credential_documents() -> list[dict[str, Any]]:
    return list(database().passkeys.find({}).sort("created_at", 1).limit(25))


def passkey_available() -> bool:
    if not database_configured():
        return False
    try:
        return database().passkeys.find_one({}, {"_id": 1}) is not None
    except Exception:
        return False


def _save_challenge(ceremony: str, challenge: bytes, client_hash: str) -> str:
    ceremony_id = secrets.token_urlsafe(24)
    database().passkey_challenges.insert_one(
        {
            "_id": ceremony_id,
            "ceremony": ceremony,
            "challenge": bytes_to_base64url(challenge),
            "client_hash": client_hash,
            "created_at": utc_now(),
            "expires_at": utc_now() + timedelta(seconds=CHALLENGE_LIFETIME_SECONDS),
        }
    )
    return ceremony_id


def _consume_challenge(ceremony_id: str, ceremony: str, client_hash: str) -> bytes:
    document = database().passkey_challenges.find_one_and_delete(
        {
            "_id": ceremony_id,
            "ceremony": ceremony,
            "client_hash": client_hash,
            "expires_at": {"$gt": utc_now()},
        }
    )
    if not document:
        raise PasskeyError("La demande Face ID a expiré. Recommence.")
    return base64url_to_bytes(str(document["challenge"]))


def registration_options(client_hash: str) -> dict[str, Any]:
    _require_database()
    rp_id, _ = _rp_config()
    existing = _credential_documents()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Instagram Studio",
        user_id=_account_user_id(),
        user_name="studio-owner",
        user_display_name="Instagram Studio",
        timeout=CHALLENGE_LIFETIME_SECONDS * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(str(item["_id"])))
            for item in existing
        ],
    )
    return {
        "ceremony_id": _save_challenge("registration", options.challenge, client_hash),
        "public_key": json.loads(options_to_json(options)),
    }


def verify_registration(
    ceremony_id: str,
    credential: dict[str, Any],
    client_hash: str,
    label: str,
) -> dict[str, Any]:
    _require_database()
    rp_id, origin = _rp_config()
    challenge = _consume_challenge(ceremony_id, "registration", client_hash)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError("La passkey n’a pas pu être vérifiée.") from exc

    credential_id = bytes_to_base64url(verified.credential_id)
    transports = ((credential.get("response") or {}).get("transports") or [])
    now = utc_now()
    database().passkeys.update_one(
        {"_id": credential_id},
        {
            "$set": {
                "label": (label.strip() or "Face ID / passkey")[:80],
                "public_key": bytes_to_base64url(verified.credential_public_key),
                "sign_count": int(verified.sign_count),
                "device_type": str(getattr(verified.credential_device_type, "value", verified.credential_device_type)),
                "backed_up": bool(verified.credential_backed_up),
                "transports": [str(value)[:30] for value in transports[:8]],
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return {"id": credential_id, "label": (label.strip() or "Face ID / passkey")[:80]}


def authentication_options(client_hash: str) -> dict[str, Any]:
    _require_database()
    rp_id, _ = _rp_config()
    credentials = _credential_documents()
    if not credentials:
        raise PasskeyError("Aucune passkey n’est encore configurée.")
    options = generate_authentication_options(
        rp_id=rp_id,
        timeout=CHALLENGE_LIFETIME_SECONDS * 1000,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(str(item["_id"])))
            for item in credentials
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return {
        "ceremony_id": _save_challenge("authentication", options.challenge, client_hash),
        "public_key": json.loads(options_to_json(options)),
    }


def verify_authentication(
    ceremony_id: str,
    credential: dict[str, Any],
    client_hash: str,
) -> dict[str, Any]:
    _require_database()
    rp_id, origin = _rp_config()
    challenge = _consume_challenge(ceremony_id, "authentication", client_hash)
    credential_id = str(credential.get("id") or "")
    stored = database().passkeys.find_one({"_id": credential_id})
    if not stored:
        raise PasskeyError("Cette passkey n’est pas reconnue par le Studio.")
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(str(stored["public_key"])),
            credential_current_sign_count=int(stored.get("sign_count", 0)),
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError("Face ID/passkey n’a pas pu confirmer la connexion.") from exc
    database().passkeys.update_one(
        {"_id": credential_id},
        {
            "$set": {
                "sign_count": int(verified.new_sign_count),
                "device_type": str(getattr(verified.credential_device_type, "value", verified.credential_device_type)),
                "backed_up": bool(verified.credential_backed_up),
                "last_used_at": utc_now(),
                "updated_at": utc_now(),
            }
        },
    )
    return stored


def list_passkeys() -> list[dict[str, Any]]:
    _require_database()
    return [
        {
            "id": str(item["_id"]),
            "label": str(item.get("label") or "Face ID / passkey"),
            "created_at": item.get("created_at").isoformat() if item.get("created_at") else None,
            "last_used_at": item.get("last_used_at").isoformat() if item.get("last_used_at") else None,
            "backed_up": bool(item.get("backed_up", False)),
        }
        for item in _credential_documents()
    ]


def delete_passkey(credential_id: str) -> bool:
    _require_database()
    result = database().passkeys.delete_one({"_id": credential_id})
    return bool(result.deleted_count)
