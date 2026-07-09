from datetime import datetime

from sqlalchemy.orm import Session

from app.models.crm import Contact, Conversation, Message


def get_or_create_contact(
    db: Session,
    wa_id: str,
    name: str | None = None,
    phone: str | None = None,
) -> Contact:
    contact = db.query(Contact).filter(Contact.wa_id == wa_id).first()

    if contact:
        if name and not contact.name:
            contact.name = name

        if phone and not contact.phone:
            contact.phone = phone

        db.commit()
        db.refresh(contact)

        return contact

    contact = Contact(
        wa_id=wa_id,
        name=name,
        phone=phone or wa_id,
    )

    db.add(contact)
    db.commit()
    db.refresh(contact)

    return contact


def get_or_create_open_conversation(
    db: Session,
    contact_id: int,
    source_campaign: str | None = None,
) -> tuple[Conversation, bool]:
    open_statuses = ["new", "assigned", "in_progress", "pending"]

    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.contact_id == contact_id,
            Conversation.status.in_(open_statuses),
        )
        .order_by(Conversation.created_at.desc())
        .first()
    )

    if conversation:
        return conversation, False

    now = datetime.utcnow()

    conversation = Conversation(
        contact_id=contact_id,
        status="new",
        source_campaign=source_campaign,
        last_message_at=now,
        unread_count=0,
        has_new_message=False,
    )

    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    return conversation, True


def save_inbound_message(
    db: Session,
    conversation_id: int,
    wa_message_id: str | None,
    body: str | None,
    message_type: str = "text",
) -> Message | None:
    if wa_message_id:
        existing_message = (
            db.query(Message)
            .filter(Message.wa_message_id == wa_message_id)
            .first()
        )

        if existing_message:
            return existing_message

    now = datetime.utcnow()

    message = Message(
        conversation_id=conversation_id,
        wa_message_id=wa_message_id,
        direction="inbound",
        message_type=message_type,
        body=body,
        status="received",
        created_at=now,
    )

    db.add(message)

    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id)
        .first()
    )

    if conversation:
        conversation.unread_count = (conversation.unread_count or 0) + 1
        conversation.has_new_message = True
        conversation.last_inbound_message_at = now
        conversation.last_message_at = now

        if conversation.status == "new":
            conversation.status = "new"

    db.commit()
    db.refresh(message)

    return message


def save_outbound_message(
    db: Session,
    conversation_id: int,
    wa_message_id: str | None,
    body: str | None,
    status: str = "sent",
    sent_by_advisor_id: int | None = None,
) -> Message:
    now = datetime.utcnow()

    message = Message(
        conversation_id=conversation_id,
        wa_message_id=wa_message_id,
        direction="outbound",
        message_type="text",
        body=body,
        status=status,
        sent_by_advisor_id=sent_by_advisor_id,
        created_at=now,
    )

    db.add(message)

    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id)
        .first()
    )

    if conversation:
        conversation.last_message_at = now

    db.commit()
    db.refresh(message)

    return message