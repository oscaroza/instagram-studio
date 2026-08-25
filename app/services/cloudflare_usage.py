import re
from datetime import datetime, timedelta, timezone
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
    # Read-only configuration lookups emitted by the Cloudflare dashboard/API.
    # Counting undocumented reads as Class B is conservative: it can only make
    # the displayed remaining quota lower, never hide billable usage.
    "getbucketnotificationconfiguration",
    "getbucketsippyconfiguration",
}
FREE_ACTIONS = {"deleteobject", "deletebucket", "abortmultipartupload"}
ACTION_ALIASES = {
    # Cloudflare Analytics can expose the S3 API name while the pricing table
    # uses the canonical R2 billing name.
    "listobjectsv1": "listobjects",
    "createbucket": "putbucket",
    "deleteobjects": "deleteobject",
}

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


def cloudflare_billing_configured() -> bool:
    return bool(settings.r2_account_id and settings.cloudflare_billing_api_token)


def _safe_cloudflare_message(message: str) -> str:
    safe = str(message or "")
    for secret in (
        settings.cloudflare_analytics_api_token,
        settings.cloudflare_billing_api_token,
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
    normalized = ACTION_ALIASES.get(normalized, normalized)
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


def _cloudflare_json(response: httpx.Response, service: str) -> dict[str, Any]:
    if response.status_code in {401, 403}:
        raise RuntimeError(
            f"Cloudflare refuse l’accès {service}. Vérifie la permission Account Billing — Read."
        )
    if response.status_code >= 400:
        raise RuntimeError(f"API {service} Cloudflare indisponible (HTTP {response.status_code}).")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Cloudflare a renvoyé des données {service} invalides.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Cloudflare a renvoyé des données {service} invalides.")
    if payload.get("success") is False or payload.get("errors"):
        detail = "; ".join(
            str(item.get("message") or "")
            for item in payload.get("errors") or []
            if isinstance(item, dict)
        )
        raise RuntimeError(
            f"Cloudflare refuse la lecture {service} : "
            f"{_safe_cloudflare_message(detail) or 'erreur API'}."
        )
    return payload


def _parse_cloudflare_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _billing_metric_class(record: dict[str, Any]) -> str | None:
    label = " ".join(
        str(record.get(key) or "")
        for key in ("x_BillableMetricId", "x_BillableMetricName", "ChargeDescription")
    ).lower()
    normalized = re.sub(r"[^a-z0-9]", "", label)
    if "classa" in normalized:
        return "class_a"
    if "classb" in normalized:
        return "class_b"
    return None


def _query_billable_usage(
    client: httpx.Client,
    current: datetime,
    expected_period: tuple[datetime, datetime] | None = None,
) -> dict[str, Any]:
    # The endpoint accepts at most 31 days and returns the billing-period boundaries
    # on every usage row. Querying the trailing 30 days lets us locate the active cycle.
    query_start = max(
        current - timedelta(days=30), expected_period[0] if expected_period else current - timedelta(days=30)
    )
    start_date = query_start.date().isoformat()
    try:
        response = client.get(
            f"https://api.cloudflare.com/client/v4/accounts/{settings.r2_account_id}/billable/usage",
            headers={"Authorization": f"Bearer {settings.cloudflare_billing_api_token}"},
            params={"from": start_date, "to": current.date().isoformat()},
        )
    except httpx.HTTPError as exc:
        raise RuntimeError("Connexion à l’API Billing Cloudflare impossible.") from exc
    if response.status_code in {401, 403}:
        raise RuntimeError(
            "Cloudflare refuse l’API Billable Usage, encore en accès alpha restreint. "
            "Vérifie Billing — Read ; si cette permission est déjà active, ce compte "
            "n’est probablement pas encore autorisé à utiliser cet endpoint."
        )
    payload = _cloudflare_json(response, "Billing")
    records = [item for item in payload.get("result") or [] if isinstance(item, dict)]
    r2_records = [
        item
        for item in records
        if str(item.get("x_ProductFamilyName") or "").strip().lower() == "r2"
        or str(item.get("x_ProductFamilyId") or "").strip().lower() == "r2"
    ]
    if expected_period:
        period_start, period_end = expected_period
        current_records = []
        for record in r2_records:
            record_start = _parse_cloudflare_datetime(record.get("BillingPeriodStart"))
            record_end = _parse_cloudflare_datetime(record.get("BillingPeriodEnd"))
            charge_start = _parse_cloudflare_datetime(record.get("ChargePeriodStart"))
            if record_start and record_end:
                if record_start == period_start and record_end == period_end:
                    current_records.append(record)
            elif charge_start and period_start <= charge_start < period_end:
                current_records.append(record)
    else:
        active: list[tuple[dict[str, Any], datetime, datetime]] = []
        for record in r2_records:
            record_start = _parse_cloudflare_datetime(record.get("BillingPeriodStart"))
            record_end = _parse_cloudflare_datetime(record.get("BillingPeriodEnd"))
            if record_start and record_end and record_start <= current < record_end:
                active.append((record, record_start, record_end))
        if active:
            period_start, period_end = max(
                ((start, end) for _, start, end in active), key=lambda period: period[0]
            )
            current_records = [
                record for record, start, end in active if start == period_start and end == period_end
            ]
        else:
            current_records = []
    if not current_records:
        raise RuntimeError("Cloudflare Billing n’a renvoyé aucune ligne R2 pour le cycle actif.")
    totals = {"class_a": 0.0, "class_b": 0.0}
    detected = {"class_a": False, "class_b": False}
    billed_cost = 0.0
    cost_available = False
    currency = ""
    for record in current_records:
        operation_class = _billing_metric_class(record)
        quantity = record.get("ConsumedQuantity")
        if operation_class and isinstance(quantity, (int, float)):
            # Corrections can be negative; keep them so our total follows Billing.
            totals[operation_class] += float(quantity)
            detected[operation_class] = True
        cost = record.get("BilledCost")
        if isinstance(cost, (int, float)):
            billed_cost += float(cost)
            cost_available = True
        if not currency:
            currency = str(record.get("BillingCurrency") or "")
    if not any(detected.values()):
        raise RuntimeError(
            "Cloudflare Billing a répondu, mais aucun compteur R2 classe A/B n’a été identifié."
        )
    return {
        "period_start": period_start,
        "period_end": period_end,
        "class_a": max(0, round(totals["class_a"])),
        "class_b": max(0, round(totals["class_b"])),
        "cost": billed_cost if cost_available else None,
        "currency": currency,
    }


def _subscription_text(subscription: dict[str, Any]) -> str:
    rate_plan = subscription.get("rate_plan") or {}
    values = [subscription.get("id"), rate_plan.get("id"), rate_plan.get("public_name")]
    values.extend(rate_plan.get("sets") or [])
    return " ".join(str(value or "") for value in values).lower()


def _query_billing_period(client: httpx.Client, current: datetime) -> tuple[datetime, datetime]:
    try:
        response = client.get(
            f"https://api.cloudflare.com/client/v4/accounts/{settings.r2_account_id}/subscriptions",
            headers={"Authorization": f"Bearer {settings.cloudflare_billing_api_token}"},
            params={"page": 1, "per_page": 50},
        )
    except httpx.HTTPError as exc:
        raise RuntimeError("Connexion aux abonnements Cloudflare impossible.") from exc
    payload = _cloudflare_json(response, "Billing")
    candidates: list[tuple[int, datetime, datetime]] = []
    for subscription in payload.get("result") or []:
        if not isinstance(subscription, dict):
            continue
        start = _parse_cloudflare_datetime(subscription.get("current_period_start"))
        end = _parse_cloudflare_datetime(subscription.get("current_period_end"))
        if not start or not end or not start <= current < end:
            continue
        state = str(subscription.get("state") or "").lower()
        if state in {"cancelled", "failed", "expired"}:
            continue
        text = _subscription_text(subscription)
        score = 100 if "r2" in text or "object storage" in text else 0
        if str(subscription.get("frequency") or "").lower() == "monthly":
            score += 10
        candidates.append((score, start, end))
    if not candidates:
        raise RuntimeError("Aucun cycle de facturation Cloudflare actif n’a été trouvé.")
    r2_candidates = [candidate for candidate in candidates if candidate[0] >= 100]
    if r2_candidates:
        _, start, end = max(r2_candidates, key=lambda candidate: candidate[0])
        return start, end
    periods = {(start, end) for _, start, end in candidates}
    if len(periods) == 1:
        return next(iter(periods))
    raise RuntimeError("Le cycle R2 ne peut pas être identifié parmi les abonnements Cloudflare.")


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
    billing_client: httpx.Client | None = None,
    storage_client=None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    summary: dict[str, Any] = {
        "analytics_configured": cloudflare_analytics_configured(),
        "analytics_ready": False,
        "analytics_error": "",
        "billing_configured": cloudflare_billing_configured(),
        "billing_ready": False,
        "billing_period_ready": False,
        "billing_authoritative": False,
        "billing_error": "",
        "usage_source": "unavailable",
        "period_start": None,
        "period_end": None,
        "billed_cost": None,
        "billing_currency": "",
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
        "unknown_operation_types": [],
    }
    if r2_configured():
        try:
            summary["bucket_storage_bytes"] = bucket_usage_bytes(storage_client)
        except Exception as exc:
            summary["bucket_storage_error"] = safe_r2_failure(exc, "analyse du stockage")

    period_start: datetime | None = None
    period_end: datetime | None = None
    if cloudflare_billing_configured():
        owns_billing_client = billing_client is None
        current_billing_client = billing_client or httpx.Client(timeout=25.0)
        try:
            try:
                period_start, period_end = _query_billing_period(current_billing_client, current)
                summary["billing_period_ready"] = True
                summary["usage_source"] = "analytics_billing_period"
            except RuntimeError as period_exc:
                summary["billing_error"] = _safe_cloudflare_message(str(period_exc))
            try:
                billing = _query_billable_usage(
                    current_billing_client,
                    current,
                    (period_start, period_end) if period_start and period_end else None,
                )
            except RuntimeError as usage_exc:
                summary["billing_error"] = _safe_cloudflare_message(str(usage_exc))
            else:
                period_start = billing["period_start"]
                period_end = billing["period_end"]
                summary.update(
                    {
                        "billing_ready": True,
                        "billing_period_ready": True,
                        "billing_authoritative": True,
                        "usage_source": "cloudflare_billing",
                        "class_a": _quota(billing["class_a"], R2_CLASS_A_FREE_REQUESTS),
                        "class_b": _quota(billing["class_b"], R2_CLASS_B_FREE_REQUESTS),
                        "billed_cost": billing["cost"],
                        "billing_currency": billing["currency"],
                        "billing_error": "",
                    }
                )
        except RuntimeError as exc:
            summary["billing_error"] = _safe_cloudflare_message(str(exc))
        finally:
            if owns_billing_client:
                current_billing_client.close()
    else:
        summary["billing_error"] = (
            "Ajoute CLOUDFLARE_BILLING_API_TOKEN avec Account Billing — Read."
        )

    if period_start and period_end:
        summary["period_start"] = period_start.isoformat()
        summary["period_end"] = period_end.isoformat()

    if not period_start or not period_end:
        return summary
    if not cloudflare_analytics_configured():
        summary["analytics_error"] = (
            "Ajoute CLOUDFLARE_ANALYTICS_API_TOKEN avec Account Analytics — Read "
            "pour vérifier le stockage de tout le compte R2."
        )
        return summary

    owns_client = analytics_client is None
    client = analytics_client or httpx.Client(timeout=25.0)
    try:
        account = _query_cloudflare(client, period_start, min(current, period_end))
    except RuntimeError as exc:
        summary["analytics_error"] = _safe_cloudflare_message(str(exc))
        return summary
    finally:
        if owns_client:
            client.close()

    operation_totals = {"class_a": 0, "class_b": 0, "free": 0, "unknown": 0}
    unknown_action_totals: dict[str, int] = {}
    for group in account.get("operations") or []:
        if not isinstance(group, dict):
            continue
        action = str((group.get("dimensions") or {}).get("actionType") or "")
        requests = _integer((group.get("sum") or {}).get("requests"))
        operation_class = _operation_class(action)
        operation_totals[operation_class] += requests
        if operation_class == "unknown" and requests:
            safe_action = re.sub(r"[^A-Za-z0-9_.:-]", "", action)[:80] or "sans_nom"
            unknown_action_totals[safe_action] = (
                unknown_action_totals.get(safe_action, 0) + requests
            )

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
            "analytics_class_a": _quota(
                operation_totals["class_a"], R2_CLASS_A_FREE_REQUESTS
            ),
            "analytics_class_b": _quota(
                operation_totals["class_b"], R2_CLASS_B_FREE_REQUESTS
            ),
            "free_operations": operation_totals["free"],
            "unknown_operations": operation_totals["unknown"],
            "unknown_operation_types": [
                {"action": action, "requests": requests}
                for action, requests in sorted(unknown_action_totals.items())
            ],
        }
    )
    if not summary["billing_ready"]:
        summary.update(
            {
                "class_a": summary["analytics_class_a"],
                "class_b": summary["analytics_class_b"],
            }
        )
    return summary
