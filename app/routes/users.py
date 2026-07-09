import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.crm import Advisor
from app.security import hash_password, require_admin

router = APIRouter(prefix="/users", tags=["Users"])


class CreateAdvisorRequest(BaseModel):
    name: str
    password: str
    username: str | None = None


class UpdateAdvisorRequest(BaseModel):
    name: str | None = None
    password: str | None = None
    status: str | None = None
    is_active: bool | None = None


def normalize_username(value: str) -> str:
    value = value.strip().lower()

    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))

    value = re.sub(r"[^a-z0-9]+", ".", value)
    value = value.strip(".")

    if not value:
        value = "asesor"

    return value


def user_to_dict(user: Advisor):
    return {
        "id": user.id,
        "name": user.name,
        "username": user.email,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


def generate_unique_username(db: Session, base_name: str) -> str:
    base_username = normalize_username(base_name)

    username = base_username
    counter = 1

    while db.query(Advisor).filter(Advisor.email == username).first():
        counter += 1
        username = f"{base_username}{counter}"

    return username


@router.post("/advisors")
def create_advisor(
    data: CreateAdvisorRequest,
    db: Session = Depends(get_db),
    admin: Advisor = Depends(require_admin),
):
    name = data.name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    if not data.password or len(data.password) < 4:
        raise HTTPException(
            status_code=400,
            detail="Password must have at least 4 characters",
        )

    if data.username:
        username = normalize_username(data.username)

        existing = db.query(Advisor).filter(Advisor.email == username).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Username already exists",
            )
    else:
        username = generate_unique_username(db, name)

    advisor = Advisor(
        name=name,
        email=username,
        password_hash=hash_password(data.password),
        role="advisor",
        status="available",
        is_active=True,
    )

    db.add(advisor)
    db.commit()
    db.refresh(advisor)

    return {
        "status": "ok",
        "message": "Advisor created",
        "advisor": user_to_dict(advisor),
    }


@router.get("")
def list_users(
    db: Session = Depends(get_db),
    admin: Advisor = Depends(require_admin),
):
    users = db.query(Advisor).order_by(Advisor.id.asc()).all()

    return [user_to_dict(user) for user in users]


@router.get("/advisors")
def list_advisors(
    db: Session = Depends(get_db),
    admin: Advisor = Depends(require_admin),
):
    advisors = (
        db.query(Advisor)
        .filter(Advisor.role == "advisor")
        .order_by(Advisor.id.asc())
        .all()
    )

    return [user_to_dict(advisor) for advisor in advisors]


@router.patch("/advisors/{advisor_id}")
def update_advisor(
    advisor_id: int,
    data: UpdateAdvisorRequest,
    db: Session = Depends(get_db),
    admin: Advisor = Depends(require_admin),
):
    advisor = (
        db.query(Advisor)
        .filter(Advisor.id == advisor_id, Advisor.role == "advisor")
        .first()
    )

    if not advisor:
        raise HTTPException(status_code=404, detail="Advisor not found")

    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        advisor.name = name

    if data.password is not None:
        if len(data.password) < 4:
            raise HTTPException(
                status_code=400,
                detail="Password must have at least 4 characters",
            )
        advisor.password_hash = hash_password(data.password)

    if data.status is not None:
        allowed_statuses = ["available", "busy", "offline"]
        if data.status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Allowed: {allowed_statuses}",
            )
        advisor.status = data.status

    if data.is_active is not None:
        advisor.is_active = data.is_active

    db.commit()
    db.refresh(advisor)

    return {
        "status": "ok",
        "message": "Advisor updated",
        "advisor": user_to_dict(advisor),
    }


@router.patch("/me/status")
def update_my_status(
    status_value: str,
    db: Session = Depends(get_db),
    current_user: Advisor = Depends(require_admin),
):
    """
    Este endpoint queda para admin por ahora.
    Luego creamos uno separado para asesores cuando hagamos la UI de estado.
    """
    allowed_statuses = ["available", "busy", "offline"]

    if status_value not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed: {allowed_statuses}",
        )

    current_user.status = status_value
    db.commit()
    db.refresh(current_user)

    return {
        "status": "ok",
        "user": user_to_dict(current_user),
    }