from sqlalchemy.orm import Session

from app.models.crm import Advisor, Conversation


def assign_available_advisor(db: Session, conversation: Conversation) -> Conversation:
    if conversation.assigned_advisor_id:
        return conversation

    advisor = (
        db.query(Advisor)
        .filter(
            Advisor.is_active == True,
            Advisor.status == "available",
        )
        .order_by(Advisor.id.asc())
        .first()
    )

    if not advisor:
        print("No hay asesores disponibles. La conversación queda sin asignar.")
        return conversation

    conversation.assigned_advisor_id = advisor.id
    conversation.status = "assigned"

    db.commit()
    db.refresh(conversation)

    print(f"Conversación {conversation.id} asignada a asesor {advisor.id}")

    return conversation