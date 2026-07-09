from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.crm import Advisor

router = APIRouter(prefix="/advisors", tags=["Advisors"])


@router.post("/seed")
def seed_advisors(db: Session = Depends(get_db)):
    advisors_data = [
        {"name": "Asesor 1", "email": "asesor1@petra.com"},
        {"name": "Asesor 2", "email": "asesor2@petra.com"},
        {"name": "Asesor 3", "email": "asesor3@petra.com"},
    ]

    created = []

    for item in advisors_data:
        existing = db.query(Advisor).filter(Advisor.email == item["email"]).first()

        if existing:
            existing.status = "available"
            existing.is_active = True
            continue

        advisor = Advisor(
            name=item["name"],
            email=item["email"],
            password_hash="TEMP_DEV_PASSWORD_123456",
            role="advisor",
            status="available",
            is_active=True,
        )

        db.add(advisor)
        created.append(item["email"])

    db.commit()

    return {
        "status": "ok",
        "created": created,
        "default_password": "123456",
        "note": "Password temporal de desarrollo. Luego se reemplaza por hash real."
    }


@router.get("")
def list_advisors(db: Session = Depends(get_db)):
    advisors = db.query(Advisor).order_by(Advisor.id.asc()).all()

    return [
        {
            "id": advisor.id,
            "name": advisor.name,
            "email": advisor.email,
            "role": advisor.role,
            "status": advisor.status,
            "is_active": advisor.is_active,
        }
        for advisor in advisors
    ]