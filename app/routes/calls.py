import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.crm import Advisor, CallLog, CallPermission, Conversation
from app.security import get_current_user
from app.services.crm_service import save_outbound_message
from app.whatsapp_calling_service import (
    connect_business_call,
    enable_calling_settings,
    get_call_permission,
    get_calling_settings,
    send_call_permission_request,
    terminate_call,
)

router = APIRouter(prefix="/calls", tags=["Calls"])


class StartCallRequest(BaseModel):
    conversation_id: int
    call_type: str
    notes: str | None = None


class StartWhatsAppCallRequest(BaseModel):
    conversation_id: int
    call_type: str = "audio"
    sdp_offer: str
    notes: str | None = None


class PermissionRequestBody(BaseModel):
    conversation_id: int
    message: str | None = None


class UpdateCallStatusRequest(BaseModel):
    status: str
    notes: str | None = None
    provider_call_id: str | None = None


def can_access_conversation(user: Advisor, conversation: Conversation) -> bool:
    if user.role == "admin":
        return True

    if user.role == "advisor":
        return conversation.assigned_advisor_id == user.id

    return False


def serialize_call(call: CallLog):
    return {
        "id": call.id,
        "conversation_id": call.conversation_id,
        "contact_id": call.contact_id,
        "advisor_id": call.advisor_id,
        "call_type": call.call_type,
        "direction": call.direction,
        "status": call.status,
        "provider_call_id": call.provider_call_id,
        "wa_call_id": call.wa_call_id,
        "sdp_offer": call.sdp_offer,
        "sdp_answer": call.sdp_answer,
        "notes": call.notes,
        "meta_response": call.meta_response,
        "last_webhook_payload": call.last_webhook_payload,
        "last_error": call.last_error,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "created_at": call.created_at,
    }


def serialize_permission(permission: CallPermission | None):
    if not permission:
        return None

    return {
        "id": permission.id,
        "contact_id": permission.contact_id,
        "conversation_id": permission.conversation_id,
        "permission_status": permission.permission_status,
        "permission_source": permission.permission_source,
        "request_message_id": permission.request_message_id,
        "last_requested_at": permission.last_requested_at,
        "expires_at": permission.expires_at,
        "granted_at": permission.granted_at,
        "created_at": permission.created_at,
        "meta_response": permission.meta_response,
        "last_error": permission.last_error,
    }


def get_accessible_conversation(
    db: Session,
    conversation_id: int,
    current_user: Advisor,
) -> Conversation:
    conversation = (
        db.query(Conversation)
        .options(joinedload(Conversation.contact))
        .filter(Conversation.id == conversation_id)
        .first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not can_access_conversation(current_user, conversation):
        raise HTTPException(
            status_code=403,
            detail="You cannot access this conversation",
        )

    return conversation


def get_or_create_call_permission(
    db: Session,
    conversation: Conversation,
) -> CallPermission:
    permission = (
        db.query(CallPermission)
        .filter(CallPermission.contact_id == conversation.contact_id)
        .order_by(CallPermission.created_at.desc())
        .first()
    )

    if permission:
        if not permission.conversation_id:
            permission.conversation_id = conversation.id

        return permission

    permission = CallPermission(
        contact_id=conversation.contact_id,
        conversation_id=conversation.id,
        permission_status="unknown",
        permission_source="whatsapp_calling_api",
        created_at=datetime.utcnow(),
    )

    db.add(permission)
    db.commit()
    db.refresh(permission)

    return permission


def extract_permission_status_from_meta(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None

    if result.get("status"):
        return str(result.get("status"))

    if result.get("permission_status"):
        return str(result.get("permission_status"))

    data = result.get("data")

    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        if data[0].get("status"):
            return str(data[0].get("status"))

        if data[0].get("permission_status"):
            return str(data[0].get("permission_status"))

    return None


@router.post("/settings/enable")
def enable_calling(
    current_user: Advisor = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can enable calling")

    try:
        result = enable_calling_settings()

        return {
            "status": "ok",
            "meta_response": result,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings")
def read_calling_settings(
    current_user: Advisor = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can read settings")

    try:
        result = get_calling_settings()

        return {
            "status": "ok",
            "meta_response": result,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/permission-request")
def request_call_permission(
    data: PermissionRequestBody,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    conversation = get_accessible_conversation(
        db=db,
        conversation_id=data.conversation_id,
        current_user=current_user,
    )

    permission = get_or_create_call_permission(db=db, conversation=conversation)

    body_text = data.message or (
        "Hola, nuestro equipo quiere llamarte por WhatsApp para ayudarte mejor. "
        "¿Nos autorizas a llamarte?"
    )

    try:
        result = send_call_permission_request(
            to=conversation.contact.wa_id,
            body_text=body_text,
        )

        message_id = None

        if result.get("messages"):
            message_id = result["messages"][0].get("id")

        if message_id:
            save_outbound_message(
                db=db,
                conversation_id=conversation.id,
                wa_message_id=message_id,
                body=body_text,
                status="sent",
                sent_by_advisor_id=current_user.id,
            )

        now = datetime.utcnow()

        permission.permission_status = "requested"
        permission.permission_source = "whatsapp_calling_api"
        permission.request_message_id = message_id
        permission.last_requested_at = now
        permission.expires_at = now + timedelta(days=7)
        permission.meta_response = json.dumps(result, ensure_ascii=False)
        permission.last_error = None

        db.commit()
        db.refresh(permission)

        return {
            "status": "ok",
            "permission": serialize_permission(permission),
            "meta_response": result,
        }

    except Exception as e:
        error_text = str(e)

        if "138017" in error_text or "already call this consumer" in error_text:
            now = datetime.utcnow()

            permission.permission_status = "granted"
            permission.permission_source = "whatsapp_calling_api"
            permission.granted_at = permission.granted_at or now
            permission.last_requested_at = now
            permission.expires_at = None
            permission.last_error = None
            permission.meta_response = json.dumps(
                {
                    "handled_error": True,
                    "meta_code": 138017,
                    "message": "A permanent permission has already been approved by this consumer.",
                    "raw_error": error_text,
                },
                ensure_ascii=False,
            )

            db.commit()
            db.refresh(permission)

            return {
                "status": "ok",
                "message": "Consumer already granted permanent call permission",
                "permission": serialize_permission(permission),
                "meta_response": {
                    "code": 138017,
                    "details": "A permanent permission has already been approved by this consumer.",
                },
            }

        permission.permission_status = "request_failed"
        permission.permission_source = "whatsapp_calling_api"
        permission.last_error = error_text
        db.commit()

        raise HTTPException(status_code=500, detail=error_text)


@router.get("/permission/{conversation_id}")
def read_call_permission(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    conversation = get_accessible_conversation(
        db=db,
        conversation_id=conversation_id,
        current_user=current_user,
    )

    permission = get_or_create_call_permission(db=db, conversation=conversation)

    try:
        result = get_call_permission(user_wa_id=conversation.contact.wa_id)

        permission.meta_response = json.dumps(result, ensure_ascii=False)
        permission.last_error = None

        possible_status = extract_permission_status_from_meta(result)

        if possible_status:
            permission.permission_status = possible_status

            if possible_status.lower() in ["granted", "approved", "accepted"]:
                permission.granted_at = permission.granted_at or datetime.utcnow()

        db.commit()
        db.refresh(permission)

        return {
            "status": "ok",
            "permission": serialize_permission(permission),
            "meta_response": result,
        }

    except Exception as e:
        permission.last_error = str(e)
        db.commit()
        db.refresh(permission)

        return {
            "status": "error",
            "permission": serialize_permission(permission),
            "error": str(e),
        }


@router.post("/start")
def start_call(
    data: StartCallRequest,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    allowed_call_types = ["audio", "video"]

    if data.call_type not in allowed_call_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid call_type. Allowed: {allowed_call_types}",
        )

    conversation = get_accessible_conversation(
        db=db,
        conversation_id=data.conversation_id,
        current_user=current_user,
    )

    if conversation.status in ["closed", "no_contact"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot start call for a closed or no_contact conversation",
        )

    now = datetime.utcnow()

    call = CallLog(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        advisor_id=current_user.id,
        call_type=data.call_type,
        direction="outbound",
        status="initiated",
        notes=data.notes,
        started_at=now,
        created_at=now,
    )

    db.add(call)
    db.commit()
    db.refresh(call)

    return {
        "status": "ok",
        "message": "Call registered internally",
        "call": serialize_call(call),
    }


@router.post("/start-whatsapp")
def start_whatsapp_call(
    data: StartWhatsAppCallRequest,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    allowed_call_types = ["audio", "video"]

    if data.call_type not in allowed_call_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid call_type. Allowed: {allowed_call_types}",
        )

    conversation = get_accessible_conversation(
        db=db,
        conversation_id=data.conversation_id,
        current_user=current_user,
    )

    if conversation.status in ["closed", "no_contact"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot start call for a closed or no_contact conversation",
        )

    now = datetime.utcnow()

    call = CallLog(
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        advisor_id=current_user.id,
        call_type=data.call_type,
        direction="outbound",
        status="connecting",
        notes=data.notes or "WhatsApp Calling API call attempt",
        sdp_offer=data.sdp_offer,
        started_at=now,
        created_at=now,
    )

    db.add(call)
    db.commit()
    db.refresh(call)

    try:
        result = connect_business_call(
            to=conversation.contact.wa_id,
            sdp_offer=data.sdp_offer,
            call_type=data.call_type,
            opaque_callback_data=f"petra_call_log_id:{call.id}",
        )

        call.meta_response = json.dumps(result, ensure_ascii=False)
        call.last_error = None

        wa_call_id = None

        if result.get("call_id"):
            wa_call_id = result.get("call_id")
        elif result.get("id"):
            wa_call_id = result.get("id")
        elif isinstance(result.get("calls"), list) and result.get("calls"):
            wa_call_id = result["calls"][0].get("id")

        if wa_call_id:
            call.wa_call_id = wa_call_id
            call.provider_call_id = wa_call_id

        call.status = "requested"

        db.commit()
        db.refresh(call)

        return {
            "status": "ok",
            "message": "WhatsApp call requested",
            "call": serialize_call(call),
            "meta_response": result,
        }

    except Exception as e:
        call.status = "failed"
        call.last_error = str(e)
        call.ended_at = datetime.utcnow()

        db.commit()
        db.refresh(call)

        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.post("/{call_id}/terminate-whatsapp")
def terminate_whatsapp_call(
    call_id: int,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    call = db.query(CallLog).filter(CallLog.id == call_id).first()

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    get_accessible_conversation(
        db=db,
        conversation_id=call.conversation_id,
        current_user=current_user,
    )

    wa_call_id = call.wa_call_id or call.provider_call_id

    if not wa_call_id:
        raise HTTPException(
            status_code=400,
            detail="This call does not have a WhatsApp call id",
        )

    try:
        result = terminate_call(call_id=wa_call_id)

        call.status = "terminated"
        call.ended_at = datetime.utcnow()
        call.meta_response = json.dumps(result, ensure_ascii=False)
        call.last_error = None

        db.commit()
        db.refresh(call)

        return {
            "status": "ok",
            "call": serialize_call(call),
            "meta_response": result,
        }

    except Exception as e:
        call.last_error = str(e)
        db.commit()

        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_calls(
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    query = db.query(CallLog)

    if current_user.role == "advisor":
        query = query.filter(CallLog.advisor_id == current_user.id)

    elif current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Invalid role")

    calls = query.order_by(CallLog.created_at.desc()).limit(100).all()

    return [serialize_call(call) for call in calls]


@router.get("/conversation/{conversation_id}")
def list_conversation_calls(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    conversation = get_accessible_conversation(
        db=db,
        conversation_id=conversation_id,
        current_user=current_user,
    )

    calls = (
        db.query(CallLog)
        .filter(CallLog.conversation_id == conversation.id)
        .order_by(CallLog.created_at.desc())
        .all()
    )

    return [serialize_call(call) for call in calls]


@router.get("/{call_id}")
def get_call(
    call_id: int,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    call = db.query(CallLog).filter(CallLog.id == call_id).first()

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    get_accessible_conversation(
        db=db,
        conversation_id=call.conversation_id,
        current_user=current_user,
    )

    return serialize_call(call)


@router.patch("/{call_id}/status")
def update_call_status(
    call_id: int,
    data: UpdateCallStatusRequest,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    allowed_statuses = [
        "initiated",
        "requested",
        "connecting",
        "ringing",
        "in_progress",
        "completed",
        "canceled",
        "failed",
        "missed",
        "rejected",
        "terminated",
    ]

    if data.status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed: {allowed_statuses}",
        )

    call = db.query(CallLog).filter(CallLog.id == call_id).first()

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    get_accessible_conversation(
        db=db,
        conversation_id=call.conversation_id,
        current_user=current_user,
    )

    call.status = data.status

    if data.notes is not None:
        call.notes = data.notes

    if data.provider_call_id is not None:
        call.provider_call_id = data.provider_call_id
        call.wa_call_id = data.provider_call_id

    if data.status in [
        "completed",
        "canceled",
        "failed",
        "missed",
        "rejected",
        "terminated",
    ]:
        call.ended_at = datetime.utcnow()

    db.commit()
    db.refresh(call)

    return {
        "status": "ok",
        "call": serialize_call(call),
    }