from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.crm import Advisor, Conversation, Message
from app.security import get_current_user
from app.services.crm_service import save_outbound_message
from app.whatsapp_service import send_text_message

router = APIRouter(
    prefix="/conversations",
    tags=["Conversations"],
)


class SendConversationMessageRequest(BaseModel):
    message: str


class UpdateConversationStatusRequest(BaseModel):
    status: str


class AssignConversationRequest(BaseModel):
    advisor_id: int


def can_access_conversation(
    user: Advisor,
    conversation: Conversation,
) -> bool:
    if user.role == "admin":
        return True

    if user.role == "advisor":
        return conversation.assigned_advisor_id == user.id

    return False


def serialize_message(message: Message):
    has_media = bool(message.media_id)

    return {
        "id": message.id,
        "wa_message_id": message.wa_message_id,
        "direction": message.direction,
        "message_type": message.message_type,
        "body": message.body,
        "media": {
            "id": message.media_id,
            "mime_type": message.media_mime_type,
            "filename": message.media_filename,
            "caption": message.media_caption,
            "sha256": message.media_sha256,
            "available": False,
            "url": None,
        }
        if has_media
        else None,
        "status": message.status,
        "status_updated_at": message.status_updated_at,
        "delivered_at": message.delivered_at,
        "read_at": message.read_at,
        "failed_reason": message.failed_reason,
        "sent_by_advisor_id": message.sent_by_advisor_id,
        "created_at": message.created_at,
    }


def serialize_conversation(
    conversation: Conversation,
    last_message: Message | None,
):
    return {
        "id": conversation.id,
        "status": conversation.status,
        "source_campaign": conversation.source_campaign,
        "last_message_at": conversation.last_message_at,
        "created_at": conversation.created_at,
        "unread_count": conversation.unread_count or 0,
        "has_new_message": bool(conversation.has_new_message),
        "last_inbound_message_at": conversation.last_inbound_message_at,
        "last_read_at": conversation.last_read_at,
        "contact": {
            "id": conversation.contact.id,
            "name": conversation.contact.name,
            "phone": conversation.contact.phone,
            "wa_id": conversation.contact.wa_id,
        },
        "advisor": {
            "id": conversation.advisor.id,
            "name": conversation.advisor.name,
            "email": conversation.advisor.email,
        }
        if conversation.advisor
        else None,
        "last_message": (
            serialize_message(last_message)
            if last_message
            else None
        ),
    }


@router.get("")
def list_conversations(
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    query = (
        db.query(Conversation)
        .options(
            joinedload(Conversation.contact),
            joinedload(Conversation.advisor),
        )
    )

    if current_user.role == "advisor":
        query = query.filter(
            Conversation.assigned_advisor_id == current_user.id
        )

    elif current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Invalid role",
        )

    conversations = (
        query
        .order_by(Conversation.last_message_at.desc())
        .all()
    )

    result = []

    for conversation in conversations:
        last_message = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation.id
            )
            .order_by(Message.created_at.desc())
            .first()
        )

        result.append(
            serialize_conversation(
                conversation,
                last_message,
            )
        )

    return result


@router.get("/{conversation_id}/messages")
def get_conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    conversation = (
        db.query(Conversation)
        .options(
            joinedload(Conversation.contact),
            joinedload(Conversation.advisor),
        )
        .filter(
            Conversation.id == conversation_id
        )
        .first()
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    if not can_access_conversation(
        current_user,
        conversation,
    ):
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this conversation",
        )

    messages = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id
        )
        .order_by(Message.created_at.asc())
        .all()
    )

    return {
        "conversation": {
            "id": conversation.id,
            "status": conversation.status,
            "source_campaign": conversation.source_campaign,
            "unread_count": conversation.unread_count or 0,
            "has_new_message": bool(
                conversation.has_new_message
            ),
            "last_inbound_message_at": (
                conversation.last_inbound_message_at
            ),
            "last_read_at": conversation.last_read_at,
            "assigned_advisor_id": (
                conversation.assigned_advisor_id
            ),
            "contact": {
                "id": conversation.contact.id,
                "name": conversation.contact.name,
                "phone": conversation.contact.phone,
                "wa_id": conversation.contact.wa_id,
            },
            "advisor": {
                "id": conversation.advisor.id,
                "name": conversation.advisor.name,
                "email": conversation.advisor.email,
            }
            if conversation.advisor
            else None,
        },
        "messages": [
            serialize_message(message)
            for message in messages
        ],
    }


@router.post("/{conversation_id}/send-message")
def send_conversation_message(
    conversation_id: int,
    data: SendConversationMessageRequest,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    conversation = (
        db.query(Conversation)
        .options(
            joinedload(Conversation.contact)
        )
        .filter(
            Conversation.id == conversation_id
        )
        .first()
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    if not can_access_conversation(
        current_user,
        conversation,
    ):
        raise HTTPException(
            status_code=403,
            detail="You cannot send messages in this conversation",
        )

    if conversation.status in [
        "closed",
        "no_contact",
    ]:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot send message to a closed "
                "or no_contact conversation"
            ),
        )

    message_text = data.message.strip()

    if not message_text:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty",
        )

    try:
        result = send_text_message(
            to=conversation.contact.wa_id,
            message=message_text,
        )

        wa_message_id = None

        if result.get("messages"):
            wa_message_id = (
                result["messages"][0].get("id")
            )

        saved_message = save_outbound_message(
            db=db,
            conversation_id=conversation.id,
            wa_message_id=wa_message_id,
            body=message_text,
            status="sent",
            sent_by_advisor_id=current_user.id,
        )

        saved_message.status_updated_at = (
            datetime.utcnow()
        )

        if conversation.status in [
            "new",
            "assigned",
        ]:
            conversation.status = "in_progress"

        db.commit()
        db.refresh(saved_message)

        return {
            "status": "ok",
            "whatsapp_response": result,
            "message": serialize_message(
                saved_message
            ),
        }

    except Exception as error:
        failed_message = save_outbound_message(
            db=db,
            conversation_id=conversation.id,
            wa_message_id=None,
            body=message_text,
            status="failed",
            sent_by_advisor_id=current_user.id,
        )

        failed_message.status_updated_at = (
            datetime.utcnow()
        )

        failed_message.failed_reason = str(error)

        db.commit()

        raise HTTPException(
            status_code=500,
            detail=(
                "Error sending WhatsApp message: "
                f"{str(error)}"
            ),
        )


@router.patch("/{conversation_id}/status")
def update_conversation_status(
    conversation_id: int,
    data: UpdateConversationStatusRequest,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    allowed_statuses = [
        "new",
        "assigned",
        "in_progress",
        "pending",
        "closed",
        "no_contact",
    ]

    if data.status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid status. "
                f"Allowed: {allowed_statuses}"
            ),
        )

    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id
        )
        .first()
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    if not can_access_conversation(
        current_user,
        conversation,
    ):
        raise HTTPException(
            status_code=403,
            detail="You cannot update this conversation",
        )

    conversation.status = data.status

    db.commit()
    db.refresh(conversation)

    return {
        "status": "ok",
        "conversation_id": conversation.id,
        "conversation_status": conversation.status,
    }


@router.patch("/{conversation_id}/assign")
def assign_conversation(
    conversation_id: int,
    data: AssignConversationRequest,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins can assign conversations",
        )

    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id
        )
        .first()
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    advisor = (
        db.query(Advisor)
        .filter(
            Advisor.id == data.advisor_id,
            Advisor.role == "advisor",
            Advisor.is_active == True,
        )
        .first()
    )

    if not advisor:
        raise HTTPException(
            status_code=404,
            detail="Advisor not found or inactive",
        )

    conversation.assigned_advisor_id = advisor.id
    conversation.status = "assigned"

    db.commit()
    db.refresh(conversation)

    return {
        "status": "ok",
        "conversation_id": conversation.id,
        "assigned_advisor_id": advisor.id,
        "advisor_name": advisor.name,
    }


@router.patch("/{conversation_id}/mark-read")
def mark_conversation_as_read(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id
        )
        .first()
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    if not can_access_conversation(
        current_user,
        conversation,
    ):
        raise HTTPException(
            status_code=403,
            detail="You cannot update this conversation",
        )

    conversation.unread_count = 0
    conversation.has_new_message = False
    conversation.last_read_at = datetime.utcnow()

    db.commit()
    db.refresh(conversation)

    return {
        "status": "ok",
        "conversation_id": conversation.id,
        "unread_count": conversation.unread_count,
        "has_new_message": bool(
            conversation.has_new_message
        ),
        "last_read_at": conversation.last_read_at,
    }