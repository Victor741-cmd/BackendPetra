from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.crm import Advisor, Conversation, Message
from app.security import get_current_user
from app.services.media_storage_service import (
    MediaStorageError,
    get_blob_service_client,
    get_container_name,
)

router = APIRouter(
    prefix="/media",
    tags=["Media"],
)


def can_access_conversation(
    user: Advisor,
    conversation: Conversation,
) -> bool:
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
        raise HTTPException(
            status_code=404,
            detail="Message not found",
        )

    if not can_access_conversation(
        current_user,
        message.conversation,
    ):
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this media",
        )

    if (
        message.media_storage_status != "stored"
        or not message.media_blob_name
    ):
        raise HTTPException(
            status_code=404,
            detail="Media is not available",
        )

    try:
        blob_client = get_blob_service_client().get_blob_client(
            container=get_container_name(),
            blob=message.media_blob_name,
        )

        properties = blob_client.get_blob_properties()
        downloader = blob_client.download_blob(max_concurrency=2)

        content_type = (
            message.media_mime_type
            or properties.content_settings.content_type
            or "application/octet-stream"
        )

        filename = (
            message.media_filename
            or message.media_blob_name.rsplit("/", 1)[-1]
            or f"media-{message.id}"
        )

        disposition = (
            f"inline; filename*=UTF-8''{quote(filename)}"
        )

        def stream_blob():
            for chunk in downloader.chunks():
                yield chunk

        return StreamingResponse(
            stream_blob(),
            media_type=content_type,
            headers={
                "Content-Disposition": disposition,
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except MediaStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to retrieve media: {str(exc)}",
        ) from exc
