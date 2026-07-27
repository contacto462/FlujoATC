from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode
import re

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ATC.app.core.config import settings
from ATC.app.models.ticket_sla_feedback import TicketSlaFeedback
from ATC.app.models.ticket_sla_feedback_event import TicketSlaFeedbackEvent


def get_public_base_url() -> str:
    base_url = (settings.PUBLIC_BASE_URL or "https://soporteatc.cl").strip()
    return base_url.rstrip("/")


def build_sla_feedback_token(ticket_id: int) -> str:
    payload = {
        "scope": "sla_feedback",
        "ticket_id": ticket_id,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def verify_sla_feedback_token(token: str, ticket_id: int) -> bool:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except JWTError:
        return False

    return (
        payload.get("scope") == "sla_feedback"
        and int(payload.get("ticket_id") or 0) == int(ticket_id)
    )


def build_sla_feedback_link(
    *,
    ticket_id: int,
    token: str,
    rating: int | None = None,
    resolved: str | None = None,
) -> str:
    params: dict[str, str | int] = {"token": token}
    if rating is not None:
        params["rating"] = rating
    if resolved is not None:
        params["resolved"] = resolved

    query = urlencode(params)
    return f"{get_public_base_url()}/encuesta/{ticket_id}?{query}"


def build_static_sla_survey_link(
    *,
    ticket_id: int,
    requester_name: str | None = None,
) -> str:
    params: dict[str, str | int] = {"ticket_id": ticket_id}
    if requester_name:
        params["name"] = requester_name
    query = urlencode(params)
    return f"{get_public_base_url()}/static/encuesta/index.html?{query}"


def build_configured_sla_survey_link(
    *,
    ticket_id: int,
    requester_name: str | None = None,
) -> str | None:
    base_url = (settings.SLA_SURVEY_URL or "").strip()
    if not base_url:
        return None

    params: dict[str, str | int] = {"ticket_id": ticket_id}
    if requester_name:
        params["name"] = requester_name

    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode(params)}"


def get_or_create_ticket_sla_feedback(db: Session, ticket_id: int) -> TicketSlaFeedback:
    feedback = db.get(TicketSlaFeedback, ticket_id)
    if feedback:
        return feedback

    feedback = TicketSlaFeedback(ticket_id=ticket_id)
    db.add(feedback)
    db.flush()
    return feedback


def apply_ticket_sla_feedback(
    db: Session,
    *,
    ticket_id: int,
    rating: int | None = None,
    resolved: bool | None = None,
) -> TicketSlaFeedback:
    feedback = get_or_create_ticket_sla_feedback(db, ticket_id)

    if rating is not None:
        feedback.technician_rating = rating
    if resolved is not None:
        feedback.resolution_satisfied = resolved

    if feedback.technician_rating is not None and feedback.resolution_satisfied is not None:
        feedback.submitted_at = datetime.now(timezone.utc)

    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback


def _flatten_payload(value, prefix: str = "") -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []

    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_flatten_payload(child, child_prefix))
        return items

    if isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            items.extend(_flatten_payload(child, child_prefix))
        return items

    items.append((prefix.lower(), value))
    return items


def _extract_named_answer_candidates(value) -> list[tuple[str, object]]:
    candidates: list[tuple[str, object]] = []

    if isinstance(value, dict):
        label_parts = [
            value.get("name"),
            value.get("label"),
            value.get("question"),
            value.get("field"),
            value.get("fieldName"),
            value.get("title"),
        ]
        answer_value = (
            value.get("value")
            if "value" in value
            else value.get("answer")
            if "answer" in value
            else value.get("text")
        )

        label = " ".join(str(part).strip() for part in label_parts if part).strip().lower()
        if label and answer_value is not None:
            candidates.append((label, answer_value))

        for child in value.values():
            candidates.extend(_extract_named_answer_candidates(child))

    elif isinstance(value, list):
        for child in value:
            candidates.extend(_extract_named_answer_candidates(child))

    return candidates


def parse_rating_value(value: object) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = int(value)
        return number if 1 <= number <= 5 else None

    raw = str(value).strip()
    match = re.search(r"\b([1-5])\b", raw)
    if not match:
        return None

    number = int(match.group(1))
    return number if 1 <= number <= 5 else None


def parse_resolution_value(value: object) -> bool | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    raw = str(value).strip().lower()
    if raw in {"si", "sí", "yes", "true", "1", "satisfactorio"}:
        return True
    if raw in {"no", "false", "0"}:
        return False
    return None


def parse_ticket_id_value(value: object) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return int(value)

    raw = str(value).strip()
    match = re.search(r"\b(\d+)\b", raw)
    return int(match.group(1)) if match else None


def extract_feedback_from_payload(payload: dict) -> tuple[int | None, int | None, bool | None]:
    ticket_id: int | None = None
    rating: int | None = None
    resolved: bool | None = None

    for label, answer in _extract_named_answer_candidates(payload):
        if ticket_id is None and ("ticket" in label or "ticket_id" in label):
            ticket_id = parse_ticket_id_value(answer)
            continue

        if rating is None and any(
            marker in label
            for marker in (
                "atencion del tecnico",
                "atención del técnico",
                "atencion_tecnico",
                "tecnico",
                "técnico",
                "technician",
                "rating",
            )
        ):
            rating = parse_rating_value(answer)
            continue

        if resolved is None and any(
            marker in label
            for marker in (
                "tiempo de resolucion",
                "tiempo de resolución",
                "tiempo_resolucion",
                "satisfactorio",
                "resolution",
                "satisfied",
            )
        ):
            resolved = parse_resolution_value(answer)
            continue

    for key, value in _flatten_payload(payload):
        normalized_key = key.lower()

        if ticket_id is None and (
            "ticket_id" in normalized_key
            or "ticket id" in normalized_key
            or normalized_key.endswith(".ticket")
        ):
            ticket_id = parse_ticket_id_value(value)
            continue

        if rating is None and any(
            marker in normalized_key
            for marker in (
                "atencion_tecnico",
                "atención_técnico",
                "tecnico",
                "técnico",
                "technician_rating",
                "rating",
            )
        ):
            rating = parse_rating_value(value)
            continue

        if resolved is None and any(
            marker in normalized_key
            for marker in (
                "tiempo_resolucion",
                "tiempo_resolución",
                "resolution",
                "satisfactorio",
                "satisfied",
            )
        ):
            resolved = parse_resolution_value(value)
            continue

    return ticket_id, rating, resolved


def store_sla_feedback_event(
    db: Session,
    *,
    payload: dict,
    source: str = "fillout",
    ticket_id: int | None = None,
    rating: int | None = None,
    resolved: bool | None = None,
) -> TicketSlaFeedbackEvent:
    event = TicketSlaFeedbackEvent(
        ticket_id=ticket_id,
        source=source,
        technician_rating=rating,
        resolution_satisfied=resolved,
        payload=payload,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event

