from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Advisor(Base):
    __tablename__ = "advisors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    email = Column(String(150), nullable=False, unique=True, index=True)
    password_hash = Column(String(500), nullable=False)
    role = Column(String(50), nullable=False, default="advisor")
    status = Column(String(50), nullable=False, default="available")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    conversations = relationship(
        "Conversation",
        back_populates="advisor",
        foreign_keys="Conversation.assigned_advisor_id",
    )

    sent_messages = relationship(
        "Message",
        back_populates="sent_by_advisor",
        foreign_keys="Message.sent_by_advisor_id",
    )


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    wa_id = Column(String(50), nullable=False, unique=True, index=True)
    phone = Column(String(50), nullable=True)
    name = Column(String(150), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    conversations = relationship("Conversation", back_populates="contact")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)

    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    assigned_advisor_id = Column(Integer, ForeignKey("advisors.id"), nullable=True)

    status = Column(String(50), nullable=False, default="new")
    source_campaign = Column(String(250), nullable=True)

    last_message_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    unread_count = Column(Integer, nullable=False, default=0)
    has_new_message = Column(Boolean, nullable=False, default=False)
    last_inbound_message_at = Column(DateTime, nullable=True)
    last_read_at = Column(DateTime, nullable=True)

    contact = relationship("Contact", back_populates="conversations")

    advisor = relationship(
        "Advisor",
        back_populates="conversations",
        foreign_keys=[assigned_advisor_id],
    )

    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)

    wa_message_id = Column(String(250), nullable=True, unique=True, index=True)
    direction = Column(String(50), nullable=False)
    message_type = Column(String(50), nullable=False, default="text")
    body = Column(Text, nullable=True)

    # Metadata from WhatsApp/Meta. No binary content is stored in SQL.
    media_id = Column(String(500), nullable=True)
    media_mime_type = Column(String(200), nullable=True)
    media_filename = Column(String(500), nullable=True)
    media_caption = Column(Text, nullable=True)
    media_sha256 = Column(String(500), nullable=True)

    # Private Azure Blob Storage metadata.
    media_blob_name = Column(String(1000), nullable=True)
    media_size = Column(BigInteger, nullable=True)
    media_stored_at = Column(DateTime, nullable=True)
    media_storage_status = Column(String(50), nullable=True)
    media_storage_error = Column(Text, nullable=True)

    status = Column(String(50), nullable=True, default="received")
    status_updated_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    failed_reason = Column(Text, nullable=True)

    sent_by_advisor_id = Column(Integer, ForeignKey("advisors.id"), nullable=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")

    sent_by_advisor = relationship(
        "Advisor",
        back_populates="sent_messages",
        foreign_keys=[sent_by_advisor_id],
    )


class ConversationEvent(Base):
    __tablename__ = "conversation_events"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    advisor_id = Column(Integer, ForeignKey("advisors.id"), nullable=True)

    event_type = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())


class CallPermission(Base):
    __tablename__ = "call_permissions"

    id = Column(Integer, primary_key=True, index=True)

    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)

    permission_status = Column(String(50), nullable=False, default="unknown")
    permission_source = Column(String(100), nullable=True)

    request_message_id = Column(String(250), nullable=True)
    last_requested_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    granted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    meta_response = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)
    advisor_id = Column(Integer, ForeignKey("advisors.id"), nullable=True)

    call_type = Column(String(50), nullable=False)
    direction = Column(String(50), nullable=False, default="outbound")
    status = Column(String(50), nullable=False, default="created")

    provider_call_id = Column(String(250), nullable=True)
    wa_call_id = Column(String(250), nullable=True)

    sdp_offer = Column(Text, nullable=True)
    sdp_answer = Column(Text, nullable=True)

    notes = Column(Text, nullable=True)
    meta_response = Column(Text, nullable=True)
    last_webhook_payload = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)

    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
