import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.services.r2_media import bucket_usage_bytes, r2_configured, safe_r2_failure


R2_FREE_STORAGE_BYTES = 10_000_000_000
R2_CLASS_A_FREE_REQUESTS = 1_000_000
R2_CLASS_B_FREE_REQUESTS = 10_000_000

CLASS_A_ACTIONS = {
    "listbuckets",
    "putbucket",
    "listobjects",
    "listobjectsv2",
    "putobject",
    "copyobject",
    "completemultipartupload",
    "createmultipartupload",
    "lifecyclestoragetiertransition",
    "listmultipartuploads",
    "uploadpart",
    "uploadpartcopy",
    "listparts",
    "putbucketencryption",
    "putbucketcors",
    "putbucketlifecycleconfiguration",
}
CLASS_B_ACTIONS = {
    "headbucket",
    "headobject",
    "getobject",
    "usagesummary",
    "getbucketencryption",
    "getbucketlocation",
    "getbucketcors",
    "getbucketlifecycleconfiguration",
}
FREE_ACTIONS = {"deleteobject", "deletebucket", "abortmultipartupload"}

R2_ANALYTICS_QUERY = """
query R2Usage(
  $accountTag: string!
  $startDate: Time!
  $endDate: Time!
) {
  viewer {
    accounts(filter: { accountTag: $accountTag }) {
      operations: r2OperationsAdaptiveGroups(
        limit: 10000
        filter: { datetime_geq: $startDate, datetime_leq: $endDate }
      ) {
        sum { requests }
        dimensions { actionType }
      }
      storage: r2StorageAdaptiveGroups(
        limit: 1
        filter: { datetime_geq: $startDate, datetime_leq: $endDate }
        orderBy: [datetime_DESC]
      ) {
        max { objectCount payloadSize metadataSize }
        dimensions { datetime }
      }
    }
  }
}
"""


def cloudflare_analytics_configured() -> bool:
    return bool(settings.r2_account_id and settings.cloudflare_analytics_api_token)


def _safe_cloudflare_message(message: str) -> str:
    safe = str(message or "")
    for secret in (
        settings.cloudflare_analytics_api_token,
        settings.r2_secret_access_key,
        settings.r2_access_key_id,
        settings.r2_account_id,
    ):
        if secret:
            safe = safe.replace(secret, "[valeur masquée]")
    safe = re.sub(r"\b(?:cfat_|cfut_)[A-Za-z0-9_-]+\b", "[valeur masquée]", safe)
    return " ".join(safe.split())[:300]


def _operation_class(action: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", action.lower())
    if normalized in CLASS_A_ACTIONS:
        return "class_a"
    if normalized in CLASS_B_ACTIONS:
        return "class_b"
    if normalized in FREE_ACTIONS:
        return "free"
    return "unknown"


def _integer(value: Any) -> int:
    return max(0, int(value)) if isinstance(value, (int, float)) else 0


def _quota(used: int, limit: int) -> dict[str, int | float]:
    return {
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "percent": used / limit * 100 if limit else 0.0,
    }


def _query_cloudflare(client: httpx.Client, start: datetime, end: datetime) -> dict[str, Any]:
    try:
        response = client.post(
            "https://api.cloudflare.com/client/v4/graphql",
            headers={
                "Authorization": f"Bearer {settings.cloudflare_analytics_api_token}",
                "Content-Type": "application/json",
            },
            json={
                "query": R2_ANALYTICS_QUERY,
                "variables": {
                    "accountTag": settings.r2_account_id,
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                },
            },
        )
    except httpx.HTTPError as exc:
        raise RuntimeError("Connexion à l’API Analytics Cloudflare impossible.") from exc
    if response.status_code in {401, 403}:
        raise RuntimeError(
            "Cloudflare refuse le token Analytics. Vérifie la permission Account Analytics — Read."
        )
    if response.status_code >= 400:
        raise RuntimeError(f"API Analytics Cloudflare indisponible (HTTP {response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Cloudflare a renvoyé des statistiques invalides.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Cloudflare a renvoyé des statistiques invalides.")
    errors = payload.get("errors") or []
    if errors:
        detail = "; ".join(
            str(item.get("message") or "") for item in errors if isinstance(item, dict)
        )
        raise RuntimeError(
            "Cloudflare refuse la lecture des statistiques : "
            f"{_safe_cloudflare_message(detail) or 'erreur GraphQL'}."
        )
    accounts = (((payload.get("data") or {}).get("viewer") or {}).get("accounts") or [])
    if not accounts or not isinstance(accounts[0], dict):
        raise RuntimeError("Aucune statistique R2 trouvée pour ce compte Cloudflare.")
    return accounts[0]


def r2_usage_summary(
    *,
    now: datetime | None = None,
    analytics_client: httpx.Client | None = None,
    storage_client=None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    summary: dict[str, Any] = {
        "analytics_configured": cloudflare_analytics_configured(),
        "analytics_ready": False,
        "analytics_error": "",
        "period_start": start.isoformat(),
        "period_end": current.isoformat(),
        "bucket_name": settings.r2_bucket_name,
        "bucket_storage_bytes": 0,
        "bucket_storage_error": "",
        "account_storage_bytes": None,
        "account_object_count": None,
        "storage_sampled_at": None,
        "free_storage_bytes": R2_FREE_STORAGE_BYTES,
        "studio_storage_limit_bytes": int(settings.r2_max_storage_gb * 1_000_000_000),
        "class_a": _quota(0, R2_CLASS_A_FREE_REQUESTS),
        "class_b": _quota(0, R2_CLASS_B_FREE_REQUESTS),
        "free_operations": 0,
        "unknown_operations": 0,
    }
    if r2_configured():
        try:
            summary["bucket_storage_bytes"] = bucket_usage_bytes(storage_client)
        except Exception as exc:
            summary["bucket_storage_error"] = safe_r2_failure(exc, "analyse du stockage")

    if not cloudflare_analytics_configured():
        return summary

    owns_client = analytics_client is None
    client = analytics_client or httpx.Client(timeout=25.0)
    try:
        account = _query_cloudflare(client, start, current)
    except RuntimeError as exc:
        summary["analytics_error"] = _safe_cloudflare_message(str(exc))
        return summary
    finally:
        if owns_client:
            client.close()

    operation_totals = {"class_a": 0, "class_b": 0, "free": 0, "unknown": 0}
    for group in account.get("operations") or []:
        if not isinstance(group, dict):
            continue
        action = str((group.get("dimensions") or {}).get("actionType") or "")
        requests = _integer((group.get("sum") or {}).get("requests"))
        operation_totals[_operation_class(action)] += requests

    storage_groups = account.get("storage") or []
    if storage_groups and isinstance(storage_groups[0], dict):
        storage = storage_groups[0]
        maximums = storage.get("max") or {}
        summary["account_storage_bytes"] = _integer(maximums.get("payloadSize")) + _integer(
            maximums.get("metadataSize")
        )
        summary["account_object_count"] = _integer(maximums.get("objectCount"))
        summary["storage_sampled_at"] = (storage.get("dimensions") or {}).get("datetime")

    summary.update(
        {
            "analytics_ready": True,
            "class_a": _quota(operation_totals["class_a"], R2_CLASS_A_FREE_REQUESTS),
            "class_b": _quota(operation_totals["class_b"], R2_CLASS_B_FREE_REQUESTS),
            "free_operations": operation_totals["free"],
            "unknown_operations": operation_totals["unknown"],
        }
    )
    return summary
