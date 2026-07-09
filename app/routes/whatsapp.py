import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import WHATSAPP_VERIFY_TOKEN
from app.database import get_db
from app.models.crm import CallLog, CallPermission, Contact, Message
from app.services.assignment_service import assign_available_advisor
from app.services.crm_service import (
    get_or_create_contact,
    get_or_create_open_conversation,
    save_inbound_message,
    save_outbound_message,
)
from app.whatsapp_service import send_text_message

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp"])


def extract_failed_reason(status_item: dict) -> str | None:
    errors = status_item.get("errors", [])

    if not errors:
        return None

    parts = []

    for error in errors:
        code = error.get("code")
        title = error.get("title")
        message = error.get("message")
        details = error.get("error_data", {}).get("details")

        clean_parts = []

        if code:
            clean_parts.append(f"code={code}")

        if title:
            clean_parts.append(f"title={title}")

        if message:
            clean_parts.append(f"message={message}")

        if details:
            clean_parts.append(f"details={details}")

        if clean_parts:
            parts.append(" | ".join(clean_parts))

    return " || ".join(parts) if parts else None


def update_whatsapp_message_status(db: Session, status_item: dict):
    wa_message_id = status_item.get("id")
    status_value = status_item.get("status")

    if not wa_message_id or not status_value:
        return

    message = (
        db.query(Message)
        .filter(Message.wa_message_id == wa_message_id)
        .first()
    )

    if not message:
        print(
            f"WhatsApp status received but local message was not found: "
            f"message={wa_message_id}, status={status_value}"
        )
        return

    now = datetime.utcnow()

    message.status = status_value
    message.status_updated_at = now

    if status_value == "sent":
        message.failed_reason = None

    elif status_value == "delivered":
        message.delivered_at = message.delivered_at or now
        message.failed_reason = None

    elif status_value == "read":
        message.read_at = message.read_at or now
        message.delivered_at = message.delivered_at or now
        message.failed_reason = None

    elif status_value == "failed":
        message.failed_reason = extract_failed_reason(status_item)

    db.commit()

    print(
        f"WhatsApp status updated: "
        f"local_message_id={message.id}, "
        f"wa_message_id={wa_message_id}, "
        f"status={status_value}"
    )


def normalize_permission_status(raw_status: str | None) -> str | None:
    if not raw_status:
        return None

    status = str(raw_status).lower()

    mapping = {
        "granted": "granted",
        "approved": "granted",
        "accepted": "granted",
        "allow": "granted",
        "allowed": "granted",
        "denied": "denied",
        "rejected": "denied",
        "declined": "denied",
        "revoked": "revoked",
        "expired": "expired",
        "requested": "requested",
    }

    return mapping.get(status, status)


def update_call_permission_for_contact(
    db: Session,
    wa_id: str,
    status: str,
    payload_text: str,
):
    contact = db.query(Contact).filter(Contact.wa_id == wa_id).first()

    if not contact:
        contact = get_or_create_contact(
            db=db,
            wa_id=wa_id,
            name=None,
            phone=wa_id,
        )

    conversation, _ = get_or_create_open_conversation(
        db=db,
        contact_id=contact.id,
    )

    permission = (
        db.query(CallPermission)
        .filter(CallPermission.contact_id == contact.id)
        .order_by(CallPermission.created_at.desc())
        .first()
    )

    if not permission:
        permission = CallPermission(
            contact_id=contact.id,
            conversation_id=conversation.id,
            permission_status=status,
            permission_source="webhook",
            created_at=datetime.utcnow(),
        )
        db.add(permission)
    else:
        permission.permission_status = status
        permission.permission_source = "webhook"

        if not permission.conversation_id:
            permission.conversation_id = conversation.id

    if status == "granted":
        permission.granted_at = permission.granted_at or datetime.utcnow()

    permission.meta_response = payload_text
    permission.last_error = None

    db.commit()


def update_call_permission_from_payload(db: Session, payload: dict):
    payload_text = json.dumps(payload, ensure_ascii=False)

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            permissions = (
                value.get("call_permissions")
                or value.get("calling_permissions")
                or value.get("permissions")
                or []
            )

            if isinstance(permissions, dict):
                permissions = [permissions]

            for permission_item in permissions:
                wa_id = (
                    permission_item.get("wa_id")
                    or permission_item.get("from")
                    or permission_item.get("user_wa_id")
                )

                raw_status = (
                    permission_item.get("status")
                    or permission_item.get("permission_status")
                    or permission_item.get("event")
                )

                status = normalize_permission_status(raw_status)

                if wa_id and status:
                    update_call_permission_for_contact(
                        db=db,
                        wa_id=wa_id,
                        status=status,
                        payload_text=payload_text,
                    )

            messages = value.get("messages", [])

            for message_item in messages:
                if message_item.get("type") != "interactive":
                    continue

                wa_id = message_item.get("from")
                interactive = message_item.get("interactive", {})

                possible_type = (
                    interactive.get("type")
                    or interactive.get("button_reply", {}).get("id")
                    or interactive.get("button_reply", {}).get("title")
                )

                if not possible_type:
                    continue

                possible_type_text = str(possible_type).lower()

                if "call" not in possible_type_text and "permission" not in possible_type_text:
                    continue

                raw_status = (
                    interactive.get("status")
                    or interactive.get("button_reply", {}).get("id")
                    or interactive.get("button_reply", {}).get("title")
                )

                status = normalize_permission_status(raw_status)

                if wa_id and status:
                    update_call_permission_for_contact(
                        db=db,
                        wa_id=wa_id,
                        status=status,
                        payload_text=payload_text,
                    )


def get_or_create_call_conversation(
    db: Session,
    wa_id: str | None,
) -> tuple[int | None, int | None]:
    if not wa_id:
        return None, None

    contact = get_or_create_contact(
        db=db,
        wa_id=wa_id,
        name=None,
        phone=wa_id,
    )

    conversation, _ = get_or_create_open_conversation(
        db=db,
        contact_id=contact.id,
    )

    return contact.id, conversation.id


def update_call_log_from_payload(db: Session, payload: dict):
    payload_text = json.dumps(payload, ensure_ascii=False)

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            calls = value.get("calls") or value.get("calling") or []

            if isinstance(calls, dict):
                calls = [calls]

            for call_item in calls:
                wa_call_id = (
                    call_item.get("id")
                    or call_item.get("call_id")
                    or call_item.get("wa_call_id")
                    or call_item.get("wacid")
                )

                raw_status = (
                    call_item.get("status")
                    or call_item.get("event")
                    or call_item.get("action")
                    or "webhook_received"
                )

                status = str(raw_status)

                direction = call_item.get("direction") or "inbound"
                call_type = call_item.get("call_type") or call_item.get("type") or "audio"

                session = call_item.get("session") or {}

                sdp_answer = None

                if isinstance(session, dict):
                    sdp_answer = session.get("sdp")

                if not sdp_answer:
                    sdp_answer = call_item.get("sdp") or call_item.get("sdp_answer")

                opaque_data = (
                    call_item.get("biz_opaque_callback_data")
                    or call_item.get("opaque_callback_data")
                    or ""
                )

                call = None

                if "petra_call_log_id:" in opaque_data:
                    try:
                        call_id = int(
                            opaque_data.split("petra_call_log_id:")[1]
                            .split()[0]
                            .strip()
                        )
                        call = db.query(CallLog).filter(CallLog.id == call_id).first()
                    except Exception:
                        call = None

                if not call and wa_call_id:
                    call = (
                        db.query(CallLog)
                        .filter(
                            or_(
                                CallLog.wa_call_id == wa_call_id,
                                CallLog.provider_call_id == wa_call_id,
                            )
                        )
                        .first()
                    )

                from_wa_id = call_item.get("from") or call_item.get("user_wa_id")
                contact_id, conversation_id = get_or_create_call_conversation(
                    db=db,
                    wa_id=from_wa_id,
                )

                if not call:
                    if not conversation_id:
                        print("Call webhook received without conversation_id. Payload saved in logs only.")
                        continue

                    call = CallLog(
                        conversation_id=conversation_id,
                        contact_id=contact_id,
                        advisor_id=None,
                        call_type=call_type,
                        direction=direction,
                        status=status,
                        wa_call_id=wa_call_id,
                        provider_call_id=wa_call_id,
                        started_at=datetime.utcnow(),
                        created_at=datetime.utcnow(),
                    )

                    db.add(call)

                call.status = status
                call.last_webhook_payload = payload_text

                if wa_call_id:
                    call.wa_call_id = wa_call_id
                    call.provider_call_id = wa_call_id

                if sdp_answer:
                    call.sdp_answer = sdp_answer

                normalized_status = status.lower()

                if normalized_status in [
                    "completed",
                    "canceled",
                    "cancelled",
                    "failed",
                    "missed",
                    "rejected",
                    "terminated",
                    "terminate",
                ]:
                    call.ended_at = datetime.utcnow()

                db.commit()


@router.get("")
async def verify_webhook(request: Request):
    params = dict(request.query_params)

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return int(challenge)

    return {"status": "error", "message": "Invalid verification token"}


@router.post("")
async def receive_whatsapp_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        payload = await request.json()

        update_call_permission_from_payload(db=db, payload=payload)
        update_call_log_from_payload(db=db, payload=payload)

        entries = payload.get("entry", [])

        for entry in entries:
            changes = entry.get("changes", [])

            for change in changes:
                value = change.get("value", {})

                messages = value.get("messages", [])
                contacts = value.get("contacts", [])
                statuses = value.get("statuses", [])

                for status_item in statuses:
                    update_whatsapp_message_status(db=db, status_item=status_item)

                for message_item in messages:
                    wa_message_id = message_item.get("id")
                    wa_id = message_item.get("from")
                    message_type = message_item.get("type", "text")

                    if wa_id == "16315551181":
                        print("Ignoring Meta test webhook")
                        continue

                    contact_name = None

                    if contacts:
                        profile = contacts[0].get("profile", {})
                        contact_name = profile.get("name")

                    body = ""

                    if message_type == "text":
                        body = message_item.get("text", {}).get("body", "")

                    elif message_type == "interactive":
                        interactive = message_item.get("interactive", {})
                        body = (
                            interactive.get("button_reply", {}).get("title")
                            or interactive.get("list_reply", {}).get("title")
                            or interactive.get("type")
                            or "[interactive]"
                        )

                    else:
                        body = f"[{message_type}]"

                    print(f"Incoming WhatsApp message from {wa_id}: {body}")

                    contact = get_or_create_contact(
                        db=db,
                        wa_id=wa_id,
                        name=contact_name,
                        phone=wa_id,
                    )

                    conversation, is_new_conversation = get_or_create_open_conversation(
                        db=db,
                        contact_id=contact.id,
                    )

                    save_inbound_message(
                        db=db,
                        conversation_id=conversation.id,
                        wa_message_id=wa_message_id,
                        body=body,
                        message_type=message_type,
                    )

                    if is_new_conversation:
                        conversation = assign_available_advisor(
                            db=db,
                            conversation=conversation,
                        )

                        auto_reply = (
                            "Hola, recibimos tu mensaje. "
                            "¿Me puedes indicar tu nombre completo y cuál es tu situación?"
                        )

                        try:
                            whatsapp_response = send_text_message(
                                to=wa_id,
                                message=auto_reply,
                            )

                            outbound_wa_message_id = None

                            if whatsapp_response.get("messages"):
                                outbound_wa_message_id = whatsapp_response["messages"][
                                    0
                                ].get("id")

                            save_outbound_message(
                                db=db,
                                conversation_id=conversation.id,
                                wa_message_id=outbound_wa_message_id,
                                body=auto_reply,
                                status="sent",
                            )

                        except Exception as send_error:
                            print(f"Error sending auto reply: {send_error}")

        return {"status": "ok"}

    except Exception as e:
        print(f"Webhook error: {str(e)}")
        return {"status": "ok", "error": str(e)}


@router.post("/send-test")
def send_test_message(to: str, message: str):
    result = send_text_message(to=to, message=message)

    return {
        "status": "ok",
        "result": result,
    }