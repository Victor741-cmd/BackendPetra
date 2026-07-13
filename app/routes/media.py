from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.crm import Advisor, Conversation, Message
from app.security import get_current_user
from app.services.media_storage_service import (
    MediaStorageError,
    generate_private_blob_url,
)

router = APIRouter(prefix="/media", tags=["Media"])


def can_access_conversation(user: Advisor, conversation: Conversation) -> bool:
    if user.role == "admin":
        return True

    if user.role == "advisor":
        return conversation.assigned_advisor_id == user.id

    return False


@router.get("/messages/{message_id}")
def get_message_media(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(get_current_user),
):
    message = (
        db.query(Message)
        .options(joinedload(Message.conversation))
        .filter(Message.id == message_id)
        .first()
    )

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    if not can_access_conversation(current_user, message.conversation):
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this media",
        )

    if (
        message.media_storage_status != "stored"
        or not message.media_blob_name
    ):
        raise HTTPException(status_code=404, detail="Media is not available")

    try:
        temporary_url = generate_private_blob_url(message.media_blob_name)
    except MediaStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return RedirectResponse(url=temporary_url, status_code=307)
